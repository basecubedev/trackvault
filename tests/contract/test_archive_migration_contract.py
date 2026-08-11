"""Restoring a backup that was taken by an older build.

A backup is only useful if it outlives the release that wrote it, so the
interesting case is not "restore what I made this morning" -- it is "restore what
I made two upgrades ago, onto the machine I bought last week".

Three things have to hold, and they are tested separately because they fail
separately:

```
an older archive restores, and says a migration is coming
the migration then runs, and the data that was in it is still there
a newer archive is refused, and nothing in the destination is touched
```

The old databases here are built with the project's **own** migration functions
rather than committed as fixtures. A committed binary would be a database nobody
can review in a diff, and it would stop being a real schema-1 database the first
time somebody edited migration 1.
"""

import sqlite3
from pathlib import Path

import pytest

from gpx_view.application.archive import (
    ArchiveCompatibility,
    ArchiveError,
    ArchiveErrorCode,
)
from gpx_view.config import Settings
from gpx_view.domain import TrackKind
from gpx_view.infrastructure.archive import FilesystemArchiveBuilder, FilesystemArchiveExtractor
from gpx_view.infrastructure.assembly import TrackServices, build_services
from gpx_view.infrastructure.database.inspection import read_schema_version
from gpx_view.infrastructure.database.migrations import MIGRATIONS, SCHEMA_VERSION

pytestmark = [pytest.mark.contract, pytest.mark.persistence]

SOURCE_HASH = "a" * 64


def _database_at_schema(path: Path, version: int) -> None:
    """Build a real database at an earlier schema version.

    Uses the shipped migration functions, stopping partway. That is what makes
    this an actual schema-`version` database rather than a today database with a
    number written on it -- and the difference is the whole test.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        for index, migration in enumerate(MIGRATIONS[:version]):
            migration(connection)
            connection.execute(f"PRAGMA user_version = {index + 1:d}")
        connection.commit()
    finally:
        connection.close()


def _insert_schema_1_track(path: Path) -> None:
    """Put one source, one run and one track into a schema-1 database.

    Written against the columns schema 1 actually had. History is never
    rewritten by a migration, so these statements stay valid however many
    migrations are added after them.
    """
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO raw_imports (sha256, size_bytes, original_filename, received_at,"
            " media_type, input_channel) VALUES (?, ?, ?, ?, ?, ?)",
            (
                SOURCE_HASH,
                42,
                "old.gpx",
                "2019-06-11T09:14:00+00:00",
                "application/gpx+xml",
                "local_file",
            ),
        )
        connection.execute(
            "INSERT INTO processing_runs (id, raw_import_sha256, importer, importer_version,"
            " normalization_schema_version, processed_at, status, error_code)"
            " VALUES (1, ?, 'gpx', '1', 1, '2019-06-11T09:15:00+00:00', 'succeeded', NULL)",
            (SOURCE_HASH,),
        )
        connection.execute(
            "INSERT INTO tracks (id, raw_import_sha256, source_index, processing_run_id, title,"
            " activity, exchange_format, format_version, creator, started_at, ended_at,"
            " point_count, segment_count)"
            " VALUES (1, ?, 0, 1, 'An old ride', 'cycling', 'gpx', '1.1', 'OldRecorder',"
            " '2019-06-11T09:14:00+00:00', '2019-06-11T10:14:00+00:00', 2, 1)",
            (SOURCE_HASH,),
        )
        connection.execute(
            "INSERT INTO track_classifications (track_id, detected_kind, confidence, method,"
            " method_version) VALUES (1, 'recorded', 0.9, 'evidence-weights', '1')"
        )
        # A `recorded` verdict must state the evidence it was reached from --
        # a rule schema 1 already had, and one the domain still enforces when
        # the migrated row is read back.
        connection.executemany(
            "INSERT INTO track_evidence (track_id, position, code) VALUES (1, ?, ?)",
            [(0, "track_element_present"), (1, "timestamps_present"), (2, "gps_accuracy_present")],
        )
        connection.execute("INSERT INTO track_segments (id, track_id, position) VALUES (1, 1, 0)")
        connection.executemany(
            "INSERT INTO track_points (segment_id, position, latitude, longitude, elevation,"
            " recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, 0, 52.5, 13.4, 34.0, "2019-06-11T09:14:00+00:00"),
                (1, 1, 52.6, 13.5, 36.0, "2019-06-11T10:14:00+00:00"),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _archive_of(data_dir: Path, destination: Path, services: TrackServices) -> Path:
    """Write an archive of a data directory that is not the wired one."""
    builder = FilesystemArchiveBuilder(
        destination=destination,
        database_path=data_dir / "gpx-view.sqlite3",
        raw_root=data_dir / "raw",
    )
    services.create_archive(builder)
    return destination


def _extractor(archive: Path, data_dir: Path) -> FilesystemArchiveExtractor:
    """Return an extractor that would publish into ``data_dir``."""
    return FilesystemArchiveExtractor(
        source=archive,
        data_dir=data_dir,
        database_path=data_dir / "gpx-view.sqlite3",
        raw_root=data_dir / "raw",
    )


@pytest.fixture
def services(settings: Settings) -> TrackServices:
    """Return a wired archive over a throwaway data directory."""
    built = build_services(settings)
    built.prepare_storage()
    return built


def test_the_schema_version_is_the_number_of_migrations() -> None:
    """The premise everything below rests on."""
    assert len(MIGRATIONS) == SCHEMA_VERSION
    assert SCHEMA_VERSION > 1, "there is no older schema to test against"


def test_an_archive_from_an_older_build_reports_that_a_migration_is_coming(
    services: TrackServices, tmp_path: Path
) -> None:
    """Restorable and restorable-as-it-is are different answers.

    An operator deciding whether to restore needs to know a migration will run,
    because that is the step a backup is taken before.
    """
    old = tmp_path / "old-deployment"
    _database_at_schema(old / "gpx-view.sqlite3", SCHEMA_VERSION - 1)
    archive = _archive_of(old, tmp_path / "old.tar.gz", services)

    inspection = services.restore_archive.inspect(_extractor(archive, tmp_path / "fresh"))

    assert inspection.manifest.schema_version == SCHEMA_VERSION - 1
    assert inspection.compatibility is ArchiveCompatibility.MIGRATION_REQUIRED
    assert inspection.restorable


def test_an_archive_from_an_older_build_restores_and_then_migrates(
    services: TrackServices, tmp_path: Path
) -> None:
    """The upgrade path a restore hands over to, driven end to end.

    Restoring does not migrate. Starting the archive does, which is the same
    path an in-place upgrade takes -- one migration authority, not two.
    """
    old = tmp_path / "old-deployment"
    _database_at_schema(old / "gpx-view.sqlite3", SCHEMA_VERSION - 1)
    archive = _archive_of(old, tmp_path / "old.tar.gz", services)
    fresh = tmp_path / "fresh"

    outcome = services.restore_archive(_extractor(archive, fresh))
    assert outcome.compatibility is ArchiveCompatibility.MIGRATION_REQUIRED
    assert read_schema_version(fresh / "gpx-view.sqlite3") == SCHEMA_VERSION - 1

    restored = build_services(Settings(data_dir=fresh))
    restored.prepare_storage()

    assert read_schema_version(fresh / "gpx-view.sqlite3") == SCHEMA_VERSION


def test_a_track_from_the_first_schema_survives_every_migration(
    services: TrackServices, tmp_path: Path
) -> None:
    """The claim that matters: no silent data loss across six years of upgrades.

    A track written by schema 1 is restored, migrated the whole way, and then
    read back through the ordinary query path -- not by inspecting tables. If a
    migration dropped it, rewrote its identity, or left it unreadable to the
    current model, this is where that shows.
    """
    old = tmp_path / "old-deployment"
    _database_at_schema(old / "gpx-view.sqlite3", 1)
    _insert_schema_1_track(old / "gpx-view.sqlite3")
    archive = _archive_of(old, tmp_path / "ancient.tar.gz", services)
    fresh = tmp_path / "fresh"

    services.restore_archive(_extractor(archive, fresh))
    restored = build_services(Settings(data_dir=fresh))
    restored.prepare_storage()

    track = restored.store.get_track(1)
    assert track is not None, "a track written by schema 1 did not survive the migrations"
    assert track.title == "An old ride"
    assert track.effective_kind is TrackKind.RECORDED
    assert track.raw_import_sha256 == SOURCE_HASH
    geometry = restored.store.get_geometry(1)
    assert geometry is not None
    assert [(point.latitude, point.longitude) for point in geometry[0].points] == [
        (52.5, 13.4),
        (52.6, 13.5),
    ]


def test_the_manifest_of_an_old_archive_states_the_old_schema(
    services: TrackServices, tmp_path: Path
) -> None:
    """The manifest describes what is inside it, not what wrote the manifest.

    Both facts are recorded -- `schema_version` and `gpx_view_version` -- because
    they answer different questions and a restore reads only the first.
    """
    old = tmp_path / "old-deployment"
    _database_at_schema(old / "gpx-view.sqlite3", 1)
    archive = _archive_of(old, tmp_path / "ancient.tar.gz", services)

    manifest = _extractor(archive, tmp_path / "fresh").manifest()

    assert manifest.schema_version == 1
    assert manifest.gpx_view_version


def test_an_archive_from_a_newer_build_is_refused_and_changes_nothing(
    services: TrackServices, tmp_path: Path
) -> None:
    """Downgrading is not supported, and refusing is how that is enforced.

    The same rule the database itself applies: this build does not know what the
    newer columns mean, and opening them anyway would be a silent downgrade.
    """
    future = tmp_path / "future-deployment"
    _database_at_schema(future / "gpx-view.sqlite3", SCHEMA_VERSION)
    connection = sqlite3.connect(future / "gpx-view.sqlite3")
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5:d}")
    connection.commit()
    connection.close()
    archive = _archive_of(future, tmp_path / "future.tar.gz", services)
    fresh = tmp_path / "fresh"

    with pytest.raises(ArchiveError) as raised:
        services.restore_archive(_extractor(archive, fresh))

    assert raised.value.code is ArchiveErrorCode.SCHEMA_UNSUPPORTED
    assert not (fresh / "gpx-view.sqlite3").exists()


def test_a_dry_run_of_a_newer_archive_says_so_without_raising(
    services: TrackServices, tmp_path: Path
) -> None:
    """Finding out costs nothing, which is what a dry run is for."""
    future = tmp_path / "future-deployment"
    _database_at_schema(future / "gpx-view.sqlite3", SCHEMA_VERSION)
    connection = sqlite3.connect(future / "gpx-view.sqlite3")
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5:d}")
    connection.commit()
    connection.close()
    archive = _archive_of(future, tmp_path / "future.tar.gz", services)

    inspection = services.restore_archive.inspect(_extractor(archive, tmp_path / "fresh"))

    assert inspection.compatibility is ArchiveCompatibility.UNSUPPORTED
    assert not inspection.restorable


def test_the_archived_database_carries_its_own_schema_rather_than_the_builds(
    services: TrackServices, tmp_path: Path
) -> None:
    """An archive of somebody else's data directory describes *that* directory.

    The builder reads the database it was pointed at. If it reported the wired
    deployment's schema instead, every archive would claim to be current and the
    compatibility rule would never fire.
    """
    old = tmp_path / "old-deployment"
    _database_at_schema(old / "gpx-view.sqlite3", 1)
    archive = _archive_of(old, tmp_path / "ancient.tar.gz", services)

    assert services.store.schema_version() == SCHEMA_VERSION
    assert _extractor(archive, tmp_path / "fresh").manifest().schema_version == 1
