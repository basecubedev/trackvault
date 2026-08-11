"""Diagnostic contracts: say what is wrong, and change nothing saying it.

Two halves, tested apart because they fail apart. The *rules* -- is a schema
behind the build a warning or an error -- are pure and are exercised against
constructed observations, so every branch is reachable without arranging a
broken disk. The *observation* is exercised against real directories, and what
is asserted about it is mostly that it left them alone.
"""

import hashlib
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support.map_packages import build_package
from trackvault.application.diagnostics import (
    CheckStatus,
    DeploymentObservation,
    Diagnose,
    DiagnosticReport,
)
from trackvault.application.import_tracks import ImportRequest, ImportStatus
from trackvault.config import Settings
from trackvault.domain import InputChannel, ProcessingProfile
from trackvault.domain.analysis import AnalysisProfile
from trackvault.domain.maps import (
    MapAttribution,
    MapBounds,
    MapPackage,
    MapPackageFormat,
    MapRegionId,
    MapTileSchema,
)
from trackvault.infrastructure.assembly import TrackServices, build_services
from trackvault.infrastructure.database.migrations import SCHEMA_VERSION
from trackvault.infrastructure.diagnostics import observe
from trackvault.infrastructure.private_data import create_private_directory

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

HEALTHY = DeploymentObservation(
    release="1.2.3",
    installed_schema_version=10,
    data_directory_present=True,
    data_directory_writable=True,
    data_directory_creatable=True,
    database_present=True,
    database_schema_version=10,
    database_intact=True,
    raw_artifacts_expected=3,
    raw_artifacts_missing=0,
    raw_artifacts_corrupt=0,
    import_directory_configured=True,
    import_directory_readable=True,
    backup_directory_present=True,
    backup_count=2,
    latest_backup_age_days=1.5,
    web_assets_present=True,
    processing_profiles=(
        ProcessingProfile(
            importer="gpx",
            importer_version="2",
            normalization_schema_version=3,
            classifier="evidence-weights",
            classifier_version="2",
        ),
    ),
    analysis_profile=AnalysisProfile(
        distance_algorithm="haversine",
        distance_algorithm_version=1,
        movement_algorithm="two-scale-spread",
        movement_algorithm_version=2,
        elevation_algorithm="median-deadband",
        elevation_algorithm_version=1,
        metric_schema_version=2,
    ),
    containerized=False,
    maps_enabled=True,
    installed_map_count=1,
    invalid_map_count=0,
)


def _check(report: DiagnosticReport, name: str) -> CheckStatus:
    """Return the status of one named check."""
    return next(check.status for check in report.checks if check.name == name)


def _report(**changes: object) -> DiagnosticReport:
    """Diagnose a deployment that differs from the healthy one in the given ways."""
    return Diagnose()(replace(HEALTHY, **changes))  # type: ignore[arg-type]


# --- the rules ------------------------------------------------------------


def test_a_healthy_deployment_reports_nothing_to_do() -> None:
    """The baseline. Without it, every other assertion here proves nothing."""
    report = _report()

    assert report.healthy
    assert report.status is CheckStatus.OK


def test_the_report_is_as_bad_as_its_worst_check() -> None:
    """One broken thing makes a deployment broken, however much else is fine."""
    assert _report(database_intact=False).status is CheckStatus.ERROR
    assert _report(backup_count=0).status is CheckStatus.WARNING


@pytest.mark.parametrize(
    ("changes", "check", "expected"),
    [
        (
            {"data_directory_present": False, "data_directory_creatable": False},
            "data_directory",
            CheckStatus.ERROR,
        ),
        ({"data_directory_present": False}, "data_directory", CheckStatus.WARNING),
        ({"data_directory_writable": False}, "data_directory", CheckStatus.ERROR),
        ({"database_intact": False}, "database_integrity", CheckStatus.ERROR),
        ({"raw_artifacts_corrupt": 1}, "raw_storage", CheckStatus.ERROR),
        ({"raw_artifacts_missing": 1}, "raw_storage", CheckStatus.WARNING),
        ({"import_directory_configured": False}, "import_directory", CheckStatus.WARNING),
        ({"import_directory_readable": False}, "import_directory", CheckStatus.ERROR),
        ({"backup_count": 0}, "backups", CheckStatus.WARNING),
        ({"backup_directory_present": False}, "backups", CheckStatus.WARNING),
        ({"web_assets_present": False}, "web_assets", CheckStatus.WARNING),
        ({"invalid_map_count": 1}, "maps", CheckStatus.WARNING),
    ],
)
def test_each_fault_is_judged_at_the_severity_it_deserves(
    changes: dict[str, object], check: str, expected: CheckStatus
) -> None:
    """Degraded is not broken, and collapsing the two trains people to ignore both."""
    assert _check(_report(**changes), check) is expected


def test_a_corrupt_original_outranks_a_missing_one() -> None:
    """Missing repairs itself when the same bytes come back. Corrupt does not.

    A missing artifact is restored by importing the file again -- the bytes hash
    to the digest it is filed under, which is the same proof the first import
    needed. A corrupt one is evidence of a problem and is never overwritten.
    """
    assert _check(_report(raw_artifacts_missing=5), "raw_storage") is CheckStatus.WARNING
    assert (
        _check(_report(raw_artifacts_missing=5, raw_artifacts_corrupt=1), "raw_storage")
        is CheckStatus.ERROR
    )


def test_a_schema_behind_the_build_is_a_warning_and_ahead_of_it_is_an_error() -> None:
    """The same asymmetry the database itself applies, reported rather than hidden.

    Behind migrates on the next start. Ahead means this build does not know what
    the columns mean, and opening it would be a downgrade nobody asked for.
    """
    assert _check(_report(database_schema_version=9), "schema") is CheckStatus.WARNING
    assert _check(_report(database_schema_version=11), "schema") is CheckStatus.ERROR
    assert _check(_report(database_schema_version=10), "schema") is CheckStatus.OK


def test_an_archive_that_has_never_started_is_not_broken() -> None:
    """A fresh installation has no database, and that is the normal first state."""
    report = _report(database_present=False, database_schema_version=None, database_intact=None)

    assert _check(report, "database") is CheckStatus.WARNING
    assert report.status is CheckStatus.WARNING


def test_no_check_reports_a_path_a_title_or_a_coordinate() -> None:
    """A diagnostic that is safe to paste into an issue is one people paste.

    The details are deliberately structural. Nothing here interpolates a
    configured path, a track name or anything a user wrote.
    """
    report = _report(
        database_intact=False, raw_artifacts_corrupt=2, import_directory_readable=False
    )

    for check in report.checks:
        assert "/" not in check.detail.replace("`trackvault backup create`", ""), check


# --- the observation ------------------------------------------------------


@pytest.fixture
def services(settings: Settings) -> TrackServices:
    """Return a wired archive over a throwaway data directory."""
    return build_services(settings)


def test_observing_a_fresh_directory_creates_no_database(
    services: TrackServices, settings: Settings
) -> None:
    """The contract that makes `doctor` safe to run when you are unsure.

    Reading a schema version through the ordinary store would create the file it
    was asked about, and the diagnostic would report a deployment it had just
    made.
    """
    observation = observe(services, SCHEMA_VERSION)

    assert not observation.database_present
    assert observation.database_schema_version is None
    assert not settings.database_path.exists()


def test_observing_a_live_archive_reads_what_is_there(services: TrackServices) -> None:
    """The ordinary case: a real archive, described accurately."""
    services.prepare_storage()
    services.import_tracks(
        ImportRequest(
            content=(FIXTURES / "recorded-measurements.gpx").read_bytes(),
            original_filename="recorded-measurements.gpx",
            input_channel=InputChannel.LOCAL_FILE,
        )
    )

    observation = observe(services, SCHEMA_VERSION)

    assert observation.database_present
    assert observation.database_schema_version == SCHEMA_VERSION
    assert observation.database_intact
    assert observation.raw_artifacts_expected == 1
    assert observation.raw_artifacts_missing == 0
    assert observation.raw_artifacts_corrupt == 0
    assert Diagnose()(observation).status is not CheckStatus.ERROR


def test_a_lost_managed_artifact_is_seen(services: TrackServices, settings: Settings) -> None:
    """The failure a database row cannot report on its own."""
    services.prepare_storage()
    outcome = services.import_tracks(
        ImportRequest(
            content=(FIXTURES / "recorded-measurements.gpx").read_bytes(),
            original_filename="recorded-measurements.gpx",
            input_channel=InputChannel.LOCAL_FILE,
        )
    )
    assert outcome.status is ImportStatus.IMPORTED
    next(settings.raw_storage_dir.rglob("*.raw")).unlink()

    observation = observe(services, SCHEMA_VERSION)

    assert observation.raw_artifacts_missing == 1
    assert Diagnose()(observation).status is CheckStatus.WARNING


def test_a_corrupted_managed_artifact_is_seen(services: TrackServices, settings: Settings) -> None:
    """Bytes that are not the ones their name claims, reported as data damage."""
    services.prepare_storage()
    services.import_tracks(
        ImportRequest(
            content=(FIXTURES / "recorded-measurements.gpx").read_bytes(),
            original_filename="recorded-measurements.gpx",
            input_channel=InputChannel.LOCAL_FILE,
        )
    )
    next(settings.raw_storage_dir.rglob("*.raw")).write_bytes(b"something else")

    observation = observe(services, SCHEMA_VERSION)

    assert observation.raw_artifacts_corrupt == 1
    assert Diagnose()(observation).status is CheckStatus.ERROR


# --- map integrity --------------------------------------------------------
#
# `installed` and `invalid` answer two different questions, and a doctor whose
# two counters describe the same set is a doctor that cannot report the one
# failure it exists for. The contract, in full:
#
#     row + file that hashes to it   installed = 1   invalid = 0
#     row, file gone                 installed = 0   invalid = 1
#     row, file with other bytes     installed = 0   invalid = 1
#     file nothing claims            installed = 0   invalid = 1
#     nothing at all                 installed = 0   invalid = 0


MONACO = "fake:europe/monaco"
MONACO_BOUNDS = (7.40, 43.48, 7.60, 43.76)


def _package_of(region_id: MapRegionId, digest: str, size: int) -> MapPackage:
    """Return the row an installation of the fixture package would have written."""
    return MapPackage(
        region_id=region_id,
        region_name="Monaco",
        provider=region_id.provider,
        format=MapPackageFormat.MBTILES,
        tile_schema=MapTileSchema(name="shortbread", version="1.0"),
        content_sha256=digest,
        size_bytes=size,
        bounds=MapBounds(*MONACO_BOUNDS),
        min_zoom=0,
        max_zoom=6,
        attribution=MapAttribution(
            data_owner="OpenStreetMap contributors",
            provider="TrackVault test fixtures",
            license_identifier="ODbL-1.0",
            license_name="Open Database License 1.0",
            required_text="© OpenStreetMap contributors",
        ),
        downloaded_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        source_url="https://example.invalid/monaco.mbtiles",
    )


def _install_map(services: TrackServices, tmp_path: Path) -> tuple[MapRegionId, Path]:
    """Put a real package on disk and the row that claims it into the database.

    The two halves are written separately on purpose: every case below is one of
    them being wrong, and a helper that could only produce healthy installations
    could not express them.
    """
    source = build_package(tmp_path / "monaco.mbtiles", bounds=MONACO_BOUNDS)
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    region_id = MapRegionId.parse(MONACO)
    managed = services.maps.storage.package_path(region_id, digest)
    create_private_directory(managed.parent)
    shutil.copyfile(source, managed)
    services.maps.repository.save_package(_package_of(region_id, digest, len(payload)))
    return region_id, managed


def test_a_deployment_with_no_maps_counts_none_of_either(services: TrackServices) -> None:
    """The baseline. Neither counter invents a package out of an empty archive."""
    services.prepare_storage()

    observation = observe(services, SCHEMA_VERSION)

    assert observation.installed_map_count == 0
    assert observation.invalid_map_count == 0
    assert _check(Diagnose()(observation), "maps") is CheckStatus.OK


def test_a_healthy_package_counts_as_installed_and_not_as_invalid(
    services: TrackServices, tmp_path: Path
) -> None:
    """A row and a file that hashes to it: the one state that is an installation."""
    services.prepare_storage()
    _install_map(services, tmp_path)

    observation = observe(services, SCHEMA_VERSION)

    assert observation.installed_map_count == 1
    assert observation.invalid_map_count == 0
    assert _check(Diagnose()(observation), "maps") is CheckStatus.OK


def test_a_row_whose_file_is_gone_counts_as_invalid_and_not_as_installed(
    services: TrackServices, tmp_path: Path
) -> None:
    """The lost-volume case: a map that answers every tile with nothing."""
    services.prepare_storage()
    _, managed = _install_map(services, tmp_path)
    managed.unlink()

    observation = observe(services, SCHEMA_VERSION)

    assert observation.installed_map_count == 0
    assert observation.invalid_map_count == 1
    assert _check(Diagnose()(observation), "maps") is CheckStatus.WARNING


def test_a_file_that_does_not_hash_to_its_row_counts_as_invalid(
    services: TrackServices, tmp_path: Path
) -> None:
    """A file of the right name proves only that a file of that name exists.

    The listing a page draws checks existence, deliberately: re-hashing a
    gigabyte to render a table is a cost with no reader. The doctor is the
    surface that pays it, which is the whole reason somebody runs it.
    """
    services.prepare_storage()
    _, managed = _install_map(services, tmp_path)
    managed.write_bytes(b"not the bytes this file is named after")

    observation = observe(services, SCHEMA_VERSION)

    assert observation.installed_map_count == 0
    assert observation.invalid_map_count == 1
    assert _check(Diagnose()(observation), "maps") is CheckStatus.WARNING


def test_a_managed_file_no_row_claims_counts_as_invalid(
    services: TrackServices, tmp_path: Path
) -> None:
    """Published, never committed. Storage nobody can account for is not nothing.

    Start-up recovery deletes these, so one exists only between starts -- which
    is exactly when somebody runs `doctor`, and exactly the disk usage they
    cannot otherwise explain.
    """
    services.prepare_storage()
    region_id, managed = _install_map(services, tmp_path)
    services.maps.repository.delete_package(region_id)

    observation = observe(services, SCHEMA_VERSION)

    assert managed.is_file()
    assert observation.installed_map_count == 0
    assert observation.invalid_map_count == 1
    assert _check(Diagnose()(observation), "maps") is CheckStatus.WARNING


def test_installed_and_invalid_never_describe_the_same_package(
    services: TrackServices, tmp_path: Path
) -> None:
    """The defect this contract exists for: two counters over one set.

    A healthy package and a broken one are installed side by side, so a counter
    that answered "how many rows are there" would report two of each and look
    entirely plausible.
    """
    services.prepare_storage()
    _install_map(services, tmp_path)
    broken = MapRegionId.parse("fake:europe/andorra")
    source = build_package(tmp_path / "andorra.mbtiles", bounds=(1.40, 42.42, 1.79, 42.66))
    payload = source.read_bytes()
    services.maps.repository.save_package(
        replace(
            _package_of(broken, hashlib.sha256(payload).hexdigest(), len(payload)),
            region_name="Andorra",
        )
    )

    observation = observe(services, SCHEMA_VERSION)

    assert observation.installed_map_count == 1
    assert observation.invalid_map_count == 1


def test_observing_maps_removes_no_debris(services: TrackServices, tmp_path: Path) -> None:
    """`doctor` reports an orphan; it does not take the decision to delete it.

    Recovery does that, at start-up, deliberately. A diagnostic that repaired
    what it found would leave an operator unable to see the state they ran it to
    understand.
    """
    services.prepare_storage()
    region_id, managed = _install_map(services, tmp_path)
    services.maps.repository.delete_package(region_id)

    observe(services, SCHEMA_VERSION)

    assert managed.is_file()


JOURNAL_SUFFIXES = ("-wal", "-shm")
"""What SQLite puts beside a database in order to read it.

Not archive content, and not evidence of a write. Opening a write-ahead-logging
database read-only creates both, whoever opens it and however carefully; the
only way for a diagnostic to leave them absent is to refuse to look at the
database at all. They are excluded from the "changed nothing" comparison and
checked separately for the one property that does matter -- that they carry the
same private mode as everything else holding movement data.
"""


def _archive_content(data_dir: Path) -> dict[Path, tuple[int, int]]:
    """Return every stored file with its size and modification time.

    Journal files aside, nothing under the data directory may differ across a
    diagnosis: not the database, not a managed original, not a map package.
    """
    return {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(data_dir.rglob("*"))
        if path.is_file() and not path.name.endswith(JOURNAL_SUFFIXES)
    }


def test_observing_an_archive_changes_nothing_about_it(
    services: TrackServices, settings: Settings, tmp_path: Path
) -> None:
    """Read-only is a contract, so it is asserted rather than intended.

    Every kind of thing the archive stores is present before the diagnosis --
    a database, a managed original, an installed map package -- because "nothing
    changed" is only worth asserting over material that could have changed.
    """
    services.prepare_storage()
    services.import_tracks(
        ImportRequest(
            content=(FIXTURES / "recorded-measurements.gpx").read_bytes(),
            original_filename="recorded-measurements.gpx",
            input_channel=InputChannel.LOCAL_FILE,
        )
    )
    _install_map(services, tmp_path)
    before = _archive_content(settings.data_dir)
    assert len(before) >= 3

    observe(services, SCHEMA_VERSION)

    assert _archive_content(settings.data_dir) == before
    for path in settings.data_dir.rglob("*"):
        if path.is_file() and path.name.endswith(JOURNAL_SUFFIXES):
            assert path.stat().st_mode & 0o777 == 0o600, path
