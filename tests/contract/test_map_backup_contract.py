"""What a backup says about offline maps, and what a restore then makes true.

A map package is the one thing a deployment holds that the archive deliberately
does not carry. That decision is sound -- the bytes are a public dataset that can
be fetched again, and they are by far the largest thing on the disk -- but it
creates the failure this file exists to rule out:

```
archive:   database says "Germany installed"
target:    no Germany package anywhere
restored:  a map manager offering a map that answers every tile with nothing
```

The row is restored because the whole database is. The file is not, because the
archive never held it. So the *state* has to come out right without either half
being changed, and it does, for one reason: an installation is a row **and** a
file that hashes to it, decided in one place. A restored row without its file is
`INVALID` -- a word with its own instruction -- rather than a lie.

The matrix below is deliberately about the target rather than the archive: the
same backup restored onto three different machines must produce three honest
answers, and the interesting one is the machine that already has the map.
"""

import hashlib
import shutil
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support.map_packages import build_package
from trackvault.application.archive import UNREFERENCED_RAW_OBJECTS
from trackvault.application.diagnostics import CheckStatus, Diagnose
from trackvault.application.import_tracks import ImportRequest, ImportStatus
from trackvault.config import Settings
from trackvault.domain import InputChannel
from trackvault.domain.maps import (
    MapAttribution,
    MapBounds,
    MapInstallState,
    MapPackage,
    MapPackageFormat,
    MapRegionId,
    MapTileSchema,
)
from trackvault.infrastructure.archive import FilesystemArchiveBuilder, FilesystemArchiveExtractor
from trackvault.infrastructure.assembly import TrackServices, build_services
from trackvault.infrastructure.database.migrations import SCHEMA_VERSION
from trackvault.infrastructure.diagnostics import observe
from trackvault.infrastructure.private_data import create_private_directory

pytestmark = [pytest.mark.contract, pytest.mark.maps, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

MONACO = "fake:europe/monaco"
MONACO_BOUNDS = (7.40, 43.48, 7.60, 43.76)
LICENCE = "Open Database License 1.0"
REQUIRED_TEXT = "© OpenStreetMap contributors"


def _deployment(root: Path) -> TrackServices:
    """Return a wired archive over its own data directory."""
    services = build_services(Settings(data_dir=root))
    services.prepare_storage()
    return services


def _import(services: TrackServices, name: str) -> None:
    """Import one synthetic fixture."""
    outcome = services.import_tracks(
        ImportRequest(
            content=(FIXTURES / name).read_bytes(),
            original_filename=name,
            input_channel=InputChannel.LOCAL_FILE,
        )
    )
    assert outcome.status is ImportStatus.IMPORTED, outcome.error_code


def _install_map(services: TrackServices, source_root: Path) -> tuple[MapPackage, Path]:
    """Install one real package: the managed file and the row that claims it."""
    source = build_package(source_root / "monaco.mbtiles", bounds=MONACO_BOUNDS)
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    region_id = MapRegionId.parse(MONACO)
    package = MapPackage(
        region_id=region_id,
        region_name="Monaco",
        provider=region_id.provider,
        format=MapPackageFormat.MBTILES,
        tile_schema=MapTileSchema(name="shortbread", version="1.0"),
        content_sha256=digest,
        size_bytes=len(payload),
        bounds=MapBounds(*MONACO_BOUNDS),
        min_zoom=0,
        max_zoom=6,
        attribution=MapAttribution(
            data_owner="OpenStreetMap contributors",
            provider="TrackVault test fixtures",
            license_identifier="ODbL-1.0",
            license_name=LICENCE,
            required_text=REQUIRED_TEXT,
        ),
        downloaded_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        source_url="https://example.invalid/monaco.mbtiles",
    )
    managed = services.maps.storage.package_path(region_id, digest)
    create_private_directory(managed.parent)
    shutil.copyfile(source, managed)
    services.maps.repository.save_package(package)
    return package, managed


def _backup(services: TrackServices, destination: Path) -> Path:
    """Write one archive of a deployment."""
    services.create_archive(
        FilesystemArchiveBuilder(
            destination=destination,
            database_path=services.settings.database_path,
            raw_root=services.settings.raw_storage_dir,
        )
    )
    return destination


def _restore_into(services: TrackServices, archive: Path, target: Path) -> None:
    """Restore one archive into a data directory."""
    settings = Settings(data_dir=target)
    services.restore_archive(
        FilesystemArchiveExtractor(
            source=archive,
            data_dir=target,
            database_path=settings.database_path,
            raw_root=settings.raw_storage_dir,
        )
    )


# --- what the archive carries ---------------------------------------------


def test_an_archive_carries_the_map_metadata_and_not_the_package(tmp_path: Path) -> None:
    """The decision, asserted from both sides.

    The row travels because the whole database does. The package bytes do not,
    because they are a public dataset and the largest thing here. Both halves
    are checked: no container member is a package, and the metadata is still
    readable out of the restored database afterwards.
    """
    origin = _deployment(tmp_path / "origin")
    _import(origin, "recorded-measurements.gpx")
    package, managed = _install_map(origin, tmp_path / "remote")

    archive = _backup(origin, tmp_path / "backup.tar.gz")

    with tarfile.open(archive, "r:gz") as container:
        members = container.getnames()
    assert not [name for name in members if name.endswith(".mbtiles")]
    assert archive.stat().st_size < managed.stat().st_size

    target = tmp_path / "target"
    _restore_into(origin, archive, target)
    restored = _deployment(target)
    assert restored.maps.installed.all()[0].package.content_sha256 == package.content_sha256


def test_a_backup_with_no_maps_is_an_ordinary_backup(tmp_path: Path) -> None:
    """The baseline: nothing about maps is invented for a deployment without one."""
    origin = _deployment(tmp_path / "origin")
    _import(origin, "recorded-measurements.gpx")

    archive = _backup(origin, tmp_path / "backup.tar.gz")
    target = tmp_path / "target"
    _restore_into(origin, archive, target)
    restored = _deployment(target)

    assert restored.maps.installed.all() == ()
    census = restored.maps.installed.census()
    assert (census.installed, census.invalid) == (0, 0)


# --- the same archive, three different targets ----------------------------


def test_restoring_onto_a_machine_without_the_package_never_says_installed(
    tmp_path: Path,
) -> None:
    """The failure this file exists for.

    The row arrives, the file does not, and the state has to be the honest one
    without anybody having written a rule about restores: an installation is a
    row and a file, decided in one place.
    """
    origin = _deployment(tmp_path / "origin")
    _import(origin, "recorded-measurements.gpx")
    _install_map(origin, tmp_path / "remote")
    archive = _backup(origin, tmp_path / "backup.tar.gz")

    target = tmp_path / "target"
    _restore_into(origin, archive, target)
    restored = _deployment(target)

    entry = restored.maps.installed.all()[0]
    assert entry.state is MapInstallState.INVALID
    assert entry.state is not MapInstallState.INSTALLED
    assert restored.maps.installed.usable() == ()


def test_restoring_onto_a_machine_that_already_has_the_package_installs_it(
    tmp_path: Path,
) -> None:
    """The case worth designing for: recovery without downloading a gigabyte.

    The package is content-addressed, so the row the archive carries names
    exactly the file the target already has. Nothing is fetched, nothing is
    copied, and the map is usable the moment the database is back.
    """
    origin = _deployment(tmp_path / "origin")
    _import(origin, "recorded-measurements.gpx")
    _install_map(origin, tmp_path / "remote")
    archive = _backup(origin, tmp_path / "backup.tar.gz")
    target = tmp_path / "target"
    already = _deployment(target)
    _install_map(already, tmp_path / "remote-target")

    _restore_into(origin, archive, target)
    restored = _deployment(target)

    entry = restored.maps.installed.all()[0]
    assert entry.state is MapInstallState.INSTALLED
    assert len(restored.maps.installed.usable()) == 1


def test_a_restored_map_that_is_missing_reports_itself_to_the_doctor(
    tmp_path: Path,
) -> None:
    """An operator finds out from the deployment, not from an empty map page."""
    origin = _deployment(tmp_path / "origin")
    _import(origin, "recorded-measurements.gpx")
    _install_map(origin, tmp_path / "remote")
    archive = _backup(origin, tmp_path / "backup.tar.gz")
    target = tmp_path / "target"
    _restore_into(origin, archive, target)
    restored = _deployment(target)

    report = Diagnose()(observe(restored, SCHEMA_VERSION))

    maps = next(check for check in report.checks if check.name == "maps")
    assert maps.status is CheckStatus.WARNING
    assert "reinstall" in maps.detail


def test_attribution_survives_a_restore_that_left_the_package_behind(
    tmp_path: Path,
) -> None:
    """Credit is package metadata, and metadata is what the archive *does* carry.

    It has to survive even for a package whose bytes are gone: the manager still
    has to say what that entry is and where it came from, and a licence that only
    existed inside the file nobody restored would be a credit this build could no
    longer make.
    """
    origin = _deployment(tmp_path / "origin")
    _install_map(origin, tmp_path / "remote")
    archive = _backup(origin, tmp_path / "backup.tar.gz")
    target = tmp_path / "target"
    _restore_into(origin, archive, target)
    restored = _deployment(target)

    entry = restored.maps.installed.all()[0]

    assert entry.state is MapInstallState.INVALID
    assert entry.package.attribution.license_name == LICENCE
    assert entry.package.attribution.required_text == REQUIRED_TEXT
    assert entry.package.region_name == "Monaco"


def test_a_package_the_restored_database_does_not_name_is_reported_before_it_is_cleared(
    tmp_path: Path,
) -> None:
    """A restore replaces the deployment's identity, and its map area with it.

    The target's own installation belonged to the database that was replaced.
    Its bytes are re-downloadable public data, so clearing them is the documented
    behaviour rather than data loss -- but it happens at the next start, through
    the ordinary recovery, and `doctor` can say so first.
    """
    origin = _deployment(tmp_path / "origin")
    _import(origin, "recorded-measurements.gpx")
    archive = _backup(origin, tmp_path / "backup.tar.gz")
    target = tmp_path / "target"
    other = _deployment(target)
    _, managed = _install_map(other, tmp_path / "remote-target")

    _restore_into(origin, archive, target)

    stale = build_services(Settings(data_dir=target))
    census = stale.maps.installed.census()
    assert (census.installed, census.unprovable, census.unclaimed) == (0, 0, 1)
    assert managed.is_file(), "doctor must be able to report it before anything removes it"

    stale.prepare_storage()

    assert not managed.exists()
    assert stale.maps.installed.census().invalid == 0


def test_a_restore_declares_the_packages_it_did_not_carry(tmp_path: Path) -> None:
    """Every manifest states the omission, whether or not a map was installed.

    Categorical rather than counted: the promise is about what this format
    carries, and a reader must be able to tell "no maps were omitted" from "this
    archive does not mention maps at all".
    """
    origin = _deployment(tmp_path / "origin")
    _install_map(origin, tmp_path / "remote")

    manifest = origin.create_archive(
        FilesystemArchiveBuilder(
            destination=tmp_path / "backup.tar.gz",
            database_path=origin.settings.database_path,
            raw_root=origin.settings.raw_storage_dir,
        )
    )

    kinds = [omission.kind for omission in manifest.omissions]
    assert "map_packages" in kinds
    assert UNREFERENCED_RAW_OBJECTS not in kinds
