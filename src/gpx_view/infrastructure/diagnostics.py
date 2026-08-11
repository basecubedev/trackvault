"""Looking at a deployment without touching it.

Everything here reads. Nothing migrates, imports, repairs, downloads or writes,
and the one place that is easy to get wrong -- asking a database for its schema
version -- deliberately opens the file directly rather than going through the
store, because the store's own connection would create a missing database and
turn a diagnostic into a change.

What is observed is facts. Whether a fact is a problem belongs to
:mod:`gpx_view.application.diagnostics`, which is why this module contains no
thresholds and no advice.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from gpx_view.application.diagnostics import DeploymentObservation
from gpx_view.application.maps import MapStorageCensus
from gpx_view.application.ports import RawArtifactState
from gpx_view.config import Settings
from gpx_view.infrastructure.archive.container import ARCHIVE_SUFFIX
from gpx_view.infrastructure.archive.publication import restore_is_pending
from gpx_view.infrastructure.assembly import TrackServices
from gpx_view.infrastructure.database.inspection import is_intact, read_schema_version
from gpx_view.release import VERSION

_CONTAINER_MARKER = Path("/.dockerenv")
_CGROUP = Path("/proc/1/cgroup")
_CONTAINER_HINTS = ("docker", "containerd", "kubepods", "libpod")

_SECONDS_PER_DAY = 86_400


def observe(services: TrackServices, installed_schema_version: int) -> DeploymentObservation:
    """Read everything a diagnosis needs, changing nothing.

    Args:
        services: The wired deployment, used for its configuration and its
            read-only stores.
        installed_schema_version: What this build's migrations amount to.

    Returns:
        The observation, ready to be judged.
    """
    settings = services.settings
    database = settings.database_path
    schema_version = read_schema_version(database)
    missing, corrupt, expected = _raw_artifact_health(services)
    maps = _map_census(services)
    return DeploymentObservation(
        release=VERSION,
        installed_schema_version=installed_schema_version,
        data_directory_present=settings.data_dir.is_dir(),
        data_directory_writable=_is_writable(settings.data_dir),
        data_directory_creatable=_is_writable(settings.data_dir.parent),
        database_present=database.is_file(),
        database_schema_version=schema_version,
        database_intact=is_intact(database),
        raw_artifacts_expected=expected,
        raw_artifacts_missing=missing,
        raw_artifacts_corrupt=corrupt,
        import_directory_configured=settings.import_dir is not None,
        import_directory_readable=_is_readable(settings.import_dir),
        backup_directory_present=settings.backup_storage_dir.is_dir(),
        backup_count=len(_backups(settings)),
        latest_backup_age_days=_newest_backup_age_days(settings),
        web_assets_present=(settings.web_dir / "index.html").is_file(),
        processing_profiles=services.processing.profiles,
        analysis_profile=services.analyze.installed.profile,
        containerized=_is_containerized(),
        restore_in_progress=restore_is_pending(settings.data_dir),
        maps_enabled=settings.maps_enabled,
        installed_map_count=maps.installed,
        invalid_map_count=maps.invalid,
        unclaimed_map_file_count=maps.unclaimed,
    )


def _raw_artifact_health(services: TrackServices) -> tuple[int, int, int]:
    """Return how many managed originals are missing, corrupt, and known at all.

    Every artifact is read and hashed. There is no cheaper honest answer -- a
    file of the right name proves only that a file of that name exists -- and
    this is the one check whose whole purpose is to catch the case where the
    database and the disk disagree.
    """
    if not services.settings.database_path.is_file():
        return 0, 0, 0
    snapshots = services.store.processing_snapshots()
    missing = corrupt = 0
    for snapshot in snapshots:
        state = services.raw_store.integrity(snapshot.raw_import_sha256)
        missing += state in (RawArtifactState.MISSING, RawArtifactState.UNREADABLE)
        corrupt += state is RawArtifactState.CORRUPT
    return missing, corrupt, len(snapshots)


def _is_writable(directory: Path) -> bool:
    """Report whether this process may write into a directory.

    Asked of the operating system rather than answered by creating a file: a
    diagnostic that proves writability by writing has changed the thing it was
    asked to describe.
    """
    return directory.is_dir() and os.access(directory, os.W_OK | os.X_OK)


def _is_readable(directory: Path | None) -> bool | None:
    """Report whether a configured directory can be listed, or ``None`` if unset."""
    if directory is None:
        return None
    return directory.is_dir() and os.access(directory, os.R_OK | os.X_OK)


def _backups(settings: Settings) -> list[Path]:
    """Return the archives in the backup directory."""
    directory = settings.backup_storage_dir
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"*{ARCHIVE_SUFFIX}"))


def _newest_backup_age_days(settings: Settings) -> float | None:
    """Return how old the newest backup is, in days, or ``None`` if there is none."""
    archives = _backups(settings)
    if not archives:
        return None
    newest = max(archive.stat().st_mtime for archive in archives)
    return (datetime.now(UTC).timestamp() - newest) / _SECONDS_PER_DAY


def _map_census(services: TrackServices) -> MapStorageCensus:
    """Return what the map area amounts to, asked of the map authority itself.

    Deliberately not recomputed here. Whether a package counts as installed is a
    business rule with one owner, and a diagnostic that re-derived it from the
    same two ingredients would be a second opinion that agrees until the day it
    does not.

    The expensive answer, not the one a page load takes. Every managed file is
    re-read and the directory is compared against the rows, because a file that
    is no longer what it claims to be is precisely what `doctor` exists to find
    and precisely what a cheap check cannot see.
    """
    if not services.settings.database_path.is_file():
        return MapStorageCensus(installed=0, unprovable=0, unclaimed=0)
    return services.maps.installed.census()


def _is_containerized() -> bool:
    """Report whether this looks like a container, so advice can match reality.

    A best-effort observation, and it is treated as one: nothing behaves
    differently because of it, and the only consumer is a sentence telling an
    operator whether their paths are host paths or container paths.
    """
    if _CONTAINER_MARKER.exists():
        return True
    try:
        content = _CGROUP.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(hint in content for hint in _CONTAINER_HINTS)
