"""Executable contracts for the SQLite store and the managed raw storage.

These are the invariants persistence has to keep no matter how the schema is
refactored: raw imports never change, exact duplicates stay one artifact, the
detected classification may be replaced while a user correction survives, and a
failed import never leaves half a track behind.
"""

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpx_view.application import ImportErrorCode, TrackImportError
from gpx_view.application.ports import RawArtifactState, TrackQuery
from gpx_view.domain import (
    NORMALIZATION_SCHEMA_VERSION,
    Activity,
    ClassificationResult,
    EvidenceCode,
    InputChannel,
    NormalizedTrack,
    ProcessingRun,
    ProcessingStatus,
    RawImport,
    SourceMetadata,
    TrackClassification,
    TrackKind,
    TrackPoint,
    TrackSegment,
)
from gpx_view.infrastructure.database import SCHEMA_VERSION, SqliteTrackStore, migrations
from gpx_view.infrastructure.database.migrations import LEGACY_SOURCE_KEY_PREFIX, MIGRATIONS
from gpx_view.infrastructure.filesystem import FilesystemRawImportStore

pytestmark = [pytest.mark.contract, pytest.mark.persistence]

SHA = "a" * 64
OTHER_SHA = "b" * 64
NOW = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
START = datetime(2026, 5, 4, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SqliteTrackStore:
    """Return a migrated store in a throwaway directory."""
    store = SqliteTrackStore(tmp_path / "gpx-view.sqlite3")
    store.migrate()
    return store


def _raw(sha256: str = SHA, **overrides: object) -> RawImport:
    """Build a raw import for a scenario."""
    values: dict[str, object] = {
        "sha256": sha256,
        "size_bytes": 512,
        "original_filename": "track.gpx",
        "received_at": NOW,
        "media_type": "application/gpx+xml",
        "input_channel": InputChannel.LOCAL_FILE,
    }
    values.update(overrides)
    return RawImport(**values)  # type: ignore[arg-type]


def _run(
    sha256: str = SHA,
    version: str = "1",
    status: ProcessingStatus = ProcessingStatus.SUCCEEDED,
    error_code: str | None = None,
) -> ProcessingRun:
    """Build a processing run for a scenario."""
    return ProcessingRun(
        raw_import_sha256=sha256,
        importer="gpx",
        importer_version=version,
        normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
        processed_at=NOW,
        status=status,
        error_code=error_code,
    )


def _track(
    kind: TrackKind = TrackKind.RECORDED,
    *,
    title: str = "Synthetic track",
    segments: int = 2,
    source_key: str = "trk:0",
) -> NormalizedTrack:
    """Build a normalized track for a scenario."""
    return NormalizedTrack(
        source_key=source_key,
        segments=tuple(
            TrackSegment(
                points=(
                    TrackPoint(
                        latitude=51.0 + index,
                        longitude=7.0 + index,
                        elevation=40.0 + index,
                        time=START + timedelta(minutes=index),
                    ),
                    TrackPoint(latitude=51.5 + index, longitude=7.5 + index),
                )
            )
            for index in range(segments)
        ),
        source=SourceMetadata(
            exchange_format="gpx",
            format_version="1.1",
            creator="SyntheticRecorder",
            external_links=("https://example.test/one", "https://example.test/two"),
            extension_namespaces=("http://example.test/ns",),
        ),
        classification=TrackClassification(
            detected=ClassificationResult(
                kind=kind,
                confidence=0.9,
                method="evidence-weights",
                method_version="1",
                evidence=(EvidenceCode.GPS_ACCURACY_PRESENT.value,),
            )
        ),
        title=title,
        activity=Activity.WALKING,
    )


# --- Migration --------------------------------------------------------------


def test_an_empty_database_migrates_to_the_latest_schema(tmp_path: Path) -> None:
    """A fresh deployment reaches the current schema in one step."""
    store = SqliteTrackStore(tmp_path / "fresh.sqlite3")

    store.migrate()

    assert store.schema_version() == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 1


def test_migrating_twice_changes_nothing(store: SqliteTrackStore) -> None:
    """Start-up runs the migration every time and must stay idempotent."""
    store.migrate()

    assert store.schema_version() == SCHEMA_VERSION


def test_a_database_from_the_future_is_refused(tmp_path: Path) -> None:
    """Downgrading silently would corrupt data; the application refuses to run."""
    path = tmp_path / "future.sqlite3"
    store = SqliteTrackStore(path)
    store.migrate()
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

    with pytest.raises(RuntimeError, match="newer schema"):
        SqliteTrackStore(path).migrate()


def test_foreign_keys_are_enforced(store: SqliteTrackStore) -> None:
    """Orphaned rows are prevented by the database, not by hope."""
    store.record_import(_raw(), _run(), [_track()])

    with pytest.raises(sqlite3.IntegrityError), store.connection() as connection:
        connection.execute(
            "INSERT INTO track_segments (track_id, position) VALUES (?, ?)", (9999, 0)
        )


def test_the_schema_stores_no_second_effective_kind(store: SqliteTrackStore) -> None:
    """A stored effective kind would be an authority that can go stale."""
    with store.connection() as connection:
        columns = [
            row["name"]
            for table in ("tracks", "track_classifications")
            for row in connection.execute(f"PRAGMA table_info({table})")
        ]

    assert "effective_kind" not in columns
    assert "kind" not in columns


# --- Raw imports are immutable ----------------------------------------------


def test_a_raw_import_is_stored_once_and_read_back_unchanged(store: SqliteTrackStore) -> None:
    """What arrived is recorded exactly as it arrived."""
    raw = _raw()

    store.record_import(raw, _run(), [_track()])

    assert store.find_raw_import(SHA) == raw


def test_reprocessing_does_not_modify_the_raw_import(store: SqliteTrackStore) -> None:
    """Importer v2 rewrites the normalized data, never the source evidence."""
    original = _raw()
    store.record_import(original, _run(version="1"), [_track()])

    store.record_import(
        _raw(original_filename="renamed-by-a-later-run.gpx", size_bytes=999_999),
        _run(version="2"),
        [_track(TrackKind.PLANNED)],
    )

    assert store.find_raw_import(SHA) == original


def test_every_processing_run_is_kept(store: SqliteTrackStore) -> None:
    """Runs are history: the latest one is current, the earlier ones stay."""
    store.record_import(_raw(), _run(version="1"), [_track()])
    store.record_import(_raw(), _run(version="2"), [_track()])

    latest = store.latest_run(SHA)

    assert latest is not None
    assert latest.importer_version == "2"
    assert store.run_count(SHA) == 2


# --- Exact duplicates -------------------------------------------------------


def test_the_same_bytes_never_become_two_track_sets(store: SqliteTrackStore) -> None:
    """Importing the same file twice is idempotent, not duplicating."""
    first = store.record_import(_raw(), _run(version="1"), [_track()])
    second = store.record_import(_raw(), _run(version="2"), [_track()])

    assert first == second
    assert len(store.list_tracks(TrackQuery()).tracks) == 1


def test_different_bytes_are_different_imports(store: SqliteTrackStore) -> None:
    """Content identity is what separates imports, not the filename."""
    store.record_import(_raw(SHA), _run(SHA), [_track(title="First")])
    store.record_import(_raw(OTHER_SHA), _run(OTHER_SHA), [_track(title="Second")])

    assert {summary.raw_import_sha256 for summary in store.list_tracks(TrackQuery()).tracks} == {
        SHA,
        OTHER_SHA,
    }


# --- Normalized rows --------------------------------------------------------


def test_segments_and_points_keep_their_order(store: SqliteTrackStore) -> None:
    """Order is source data and must survive a round trip through storage."""
    track = _track(segments=3)
    (track_id,) = store.record_import(_raw(), _run(), [track])

    assert store.get_geometry(track_id) == track.segments


def test_optional_point_values_survive_as_missing(store: SqliteTrackStore) -> None:
    """A missing elevation must not come back as zero."""
    (track_id,) = store.record_import(_raw(), _run(), [_track()])

    geometry = store.get_geometry(track_id)

    assert geometry is not None
    assert geometry[0].points[1].elevation is None
    assert geometry[0].points[1].time is None


def test_a_summary_reports_counts_and_extent_without_loading_geometry(
    store: SqliteTrackStore,
) -> None:
    """Listing thousands of tracks must not mean reading millions of positions."""
    (track_id,) = store.record_import(_raw(), _run(), [_track(segments=3)])

    summary = store.get_track(track_id)

    assert summary is not None
    assert summary.segment_count == 3
    assert summary.point_count == 6
    assert summary.started_at == START
    assert summary.ended_at == START + timedelta(minutes=2)


def test_provenance_and_evidence_survive_the_round_trip(store: SqliteTrackStore) -> None:
    """A stored verdict stays explainable after a restart."""
    (track_id,) = store.record_import(_raw(), _run(), [_track()])

    summary = store.get_track(track_id)

    assert summary is not None
    assert summary.source.creator == "SyntheticRecorder"
    assert summary.source.external_links == ("https://example.test/one", "https://example.test/two")
    assert summary.source.extension_namespaces == ("http://example.test/ns",)
    assert summary.classification.detected.evidence == (EvidenceCode.GPS_ACCURACY_PRESENT.value,)
    assert summary.classification.detected.method == "evidence-weights"


def test_several_tracks_from_one_file_keep_their_source_order(store: SqliteTrackStore) -> None:
    """One import may yield several tracks, and their order is stable.

    Three candidates are three identities. Giving them one source key would make
    them one track, which is what the key is for.
    """
    ids = store.record_import(
        _raw(),
        _run(),
        [
            _track(title="First", source_key="trk:0"),
            _track(title="Second", source_key="trk:1"),
            _track(title="Third", source_key="rte:0"),
        ],
    )

    assert len(ids) == 3
    titles = [store.get_track(track_id).title for track_id in ids]  # type: ignore[union-attr]
    assert titles == ["First", "Second", "Third"]


# --- Failed processing stays recoverable ------------------------------------


def test_a_failed_run_keeps_the_raw_import_and_names_the_reason(
    store: SqliteTrackStore,
) -> None:
    """A file we cannot read today is kept, so it can be read tomorrow."""
    store.record_import(
        _raw(),
        _run(status=ProcessingStatus.FAILED, error_code=ImportErrorCode.INVALID_GPX.value),
        [],
    )

    run = store.latest_run(SHA)

    assert store.find_raw_import(SHA) is not None
    assert run is not None
    assert run.status is ProcessingStatus.FAILED
    assert run.error_code == ImportErrorCode.INVALID_GPX.value
    assert store.list_tracks(TrackQuery()).tracks == ()


def test_a_failing_transaction_leaves_nothing_behind(store: SqliteTrackStore) -> None:
    """Half a track is worse than no track."""
    broken = _track()
    object.__setattr__(broken, "activity", None)

    with pytest.raises(TrackImportError) as raised:
        store.record_import(_raw(), _run(), [_track(title="Good"), broken])

    assert raised.value.code is ImportErrorCode.PERSISTENCE_FAILED
    assert store.find_raw_import(SHA) is None
    assert store.list_tracks(TrackQuery()).tracks == ()


# --- Reprocessing and the user override -------------------------------------


def test_reprocessing_replaces_the_detected_classification(store: SqliteTrackStore) -> None:
    """A better classifier is allowed to change the detected result."""
    (track_id,) = store.record_import(_raw(), _run(version="1"), [_track(TrackKind.UNKNOWN)])

    store.record_import(_raw(), _run(version="2"), [_track(TrackKind.RECORDED)])

    summary = store.get_track(track_id)
    assert summary is not None
    assert summary.detected_kind is TrackKind.RECORDED


def test_a_user_override_survives_reprocessing(store: SqliteTrackStore) -> None:
    """A parser or classifier upgrade must never overrule a manual correction."""
    (track_id,) = store.record_import(_raw(), _run(version="1"), [_track(TrackKind.PLANNED)])
    store.set_override(track_id, TrackKind.RECORDED, NOW)

    store.record_import(_raw(), _run(version="2"), [_track(TrackKind.PLANNED)])

    summary = store.get_track(track_id)
    assert summary is not None
    assert summary.detected_kind is TrackKind.PLANNED
    assert summary.effective_kind is TrackKind.RECORDED
    assert summary.classification.is_overridden


def test_reprocessing_keeps_the_track_identity(store: SqliteTrackStore) -> None:
    """The identity a user corrected must not be replaced by a new row."""
    first = store.record_import(_raw(), _run(version="1"), [_track()])

    second = store.record_import(_raw(), _run(version="2"), [_track()])

    assert first == second


def test_an_override_can_be_withdrawn(store: SqliteTrackStore) -> None:
    """Removing a correction hands authority back to the classifier."""
    (track_id,) = store.record_import(_raw(), _run(), [_track(TrackKind.PLANNED)])
    store.set_override(track_id, TrackKind.RECORDED, NOW)

    assert store.clear_override(track_id)

    summary = store.get_track(track_id)
    assert summary is not None
    assert summary.effective_kind is TrackKind.PLANNED
    assert not summary.classification.is_overridden


def test_overriding_an_unknown_track_reports_that_it_is_unknown(
    store: SqliteTrackStore,
) -> None:
    """A correction for a track that does not exist is not silently accepted."""
    assert not store.set_override(4711, TrackKind.RECORDED, NOW)
    assert not store.clear_override(4711)


# --- Managed raw storage ----------------------------------------------------


def _digest(content: bytes) -> str:
    """Return the real content hash, so a scenario stays a content-addressed one."""
    return hashlib.sha256(content).hexdigest()


def test_the_canonical_path_comes_from_the_content_hash(tmp_path: Path) -> None:
    """A filename never decides where bytes are written."""
    raw_store = FilesystemRawImportStore(tmp_path / "raw")
    content = b"synthetic gpx bytes"

    raw_store.store(content, _digest(content))

    stored = list((tmp_path / "raw").rglob("*.raw"))
    assert len(stored) == 1
    assert _digest(content) in stored[0].name
    assert "track.gpx" not in str(stored[0])


def test_stored_bytes_are_byte_identical(tmp_path: Path) -> None:
    """The original is source evidence and is never rewritten."""
    raw_store = FilesystemRawImportStore(tmp_path / "raw")
    content = b"<gpx>\r\n  synthetic \x00 bytes\r\n</gpx>"

    raw_store.store(content, _digest(content))

    assert raw_store.read(_digest(content)) == content


def test_storing_the_same_content_twice_creates_one_artifact(tmp_path: Path) -> None:
    """Exact duplicates share one managed artifact."""
    raw_store = FilesystemRawImportStore(tmp_path / "raw")

    raw_store.store(b"same bytes", _digest(b"same bytes"))
    raw_store.store(b"same bytes", _digest(b"same bytes"))

    assert len(list((tmp_path / "raw").rglob("*.raw"))) == 1


def test_no_temporary_files_are_left_behind(tmp_path: Path) -> None:
    """Temporary artifacts have a lifecycle, not a graveyard."""
    raw_store = FilesystemRawImportStore(tmp_path / "raw")

    raw_store.store(b"bytes", _digest(b"bytes"))

    assert not list((tmp_path / "raw").rglob("*.part"))


@pytest.mark.parametrize(
    "digest",
    ["../../escape", "a" * 63, "A" * 64, "", "a/b", "..", f"{'a' * 62}/.."],
)
def test_a_content_hash_that_is_not_a_digest_is_refused(tmp_path: Path, digest: str) -> None:
    """The hash is the only path input, so its shape is checked before use."""
    raw_store = FilesystemRawImportStore(tmp_path / "raw")

    with pytest.raises(TrackImportError) as raised:
        raw_store.store(b"bytes", digest)

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_FAILED
    assert not (tmp_path / "raw").exists() or not list((tmp_path / "raw").rglob("*"))


def test_a_symlinked_target_is_refused(tmp_path: Path) -> None:
    """A planted symlink must not redirect where the archive writes."""
    root = tmp_path / "raw"
    raw_store = FilesystemRawImportStore(root)
    digest = _digest(b"bytes")
    target = raw_store.path_for(digest)
    target.parent.mkdir(parents=True)
    target.symlink_to(tmp_path / "elsewhere")

    with pytest.raises(TrackImportError) as raised:
        raw_store.store(b"bytes", digest)

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_FAILED
    assert not (tmp_path / "elsewhere").exists()


def test_an_unstored_artifact_reports_itself_missing(tmp_path: Path) -> None:
    """Asking about content we never stored is answered, not guessed."""
    raw_store = FilesystemRawImportStore(tmp_path / "raw")

    assert raw_store.integrity(SHA) is RawArtifactState.MISSING


def test_reading_an_artifact_that_is_not_there_is_a_named_failure(tmp_path: Path) -> None:
    """A missing artifact is reported, never returned as empty content.

    Absence and damage are different problems with different recoveries, so they
    do not share a code: missing bytes come back if the same source is offered
    again, wrong bytes never do.
    """
    raw_store = FilesystemRawImportStore(tmp_path / "raw")

    with pytest.raises(TrackImportError) as raised:
        raw_store.read(SHA)

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_MISSING


def test_a_storage_root_that_cannot_hold_files_is_a_named_failure(tmp_path: Path) -> None:
    """An unusable data directory fails the import instead of losing the bytes."""
    blocked = tmp_path / "raw"
    blocked.write_bytes(b"this is a file, not a directory")
    raw_store = FilesystemRawImportStore(blocked)

    with pytest.raises(TrackImportError) as raised:
        raw_store.store(b"bytes", _digest(b"bytes"))

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_FAILED


@pytest.mark.storage
def test_a_symlinked_parent_directory_cannot_redirect_the_write(tmp_path: Path) -> None:
    """No component of the managed path may lead out of the storage root.

    Checking only the final target misses the interesting case: the fan-out
    directory is created by the store itself, so anything that can plant a
    symlink there redirects every artifact whose hash starts with those two
    characters -- without the final path ever being a link.
    """
    root = tmp_path / "raw"
    outside = tmp_path / "outside"
    outside.mkdir()
    digest = _digest(b"bytes")
    fanout = root / "sha256"
    fanout.mkdir(parents=True)
    (fanout / digest[:2]).symlink_to(outside, target_is_directory=True)
    raw_store = FilesystemRawImportStore(root)

    with pytest.raises(TrackImportError) as raised:
        raw_store.store(b"bytes", digest)

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_FAILED
    assert not list(outside.iterdir()), "the artifact was written outside the managed root"


@pytest.mark.storage
def test_an_existing_artifact_whose_content_does_not_match_its_hash_is_refused(
    tmp_path: Path,
) -> None:
    """A path named after a hash is only valid while its bytes have that hash.

    Treating "the file is there" as "the content is right" makes the store
    content-addressed in name only.
    """
    raw_store = FilesystemRawImportStore(tmp_path / "raw")
    digest = _digest(b"bytes")
    target = raw_store.path_for(digest)
    target.parent.mkdir(parents=True)
    corrupted = b"these bytes do not hash to the name of this file"
    target.write_bytes(corrupted)

    with pytest.raises(TrackImportError) as raised:
        raw_store.store(b"bytes", digest)

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_CORRUPT
    assert target.read_bytes() == corrupted, "a corrupt artifact was silently repaired"


@pytest.mark.storage
def test_reading_an_artifact_whose_content_does_not_match_its_hash_is_refused(
    tmp_path: Path,
) -> None:
    """Corrupted source evidence must never become the input of a reprocessing."""
    raw_store = FilesystemRawImportStore(tmp_path / "raw")
    target = raw_store.path_for(SHA)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"these bytes do not hash to the name of this file")

    with pytest.raises(TrackImportError) as raised:
        raw_store.read(SHA)

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_CORRUPT


@pytest.mark.storage
def test_content_that_does_not_match_the_stated_hash_is_refused(tmp_path: Path) -> None:
    """The port protects its own invariant instead of trusting today's caller.

    The import use case computes the hash itself, so this cannot happen through
    it. That is exactly why the store has to check: an authority that holds only
    because of who happens to call it is not an authority.
    """
    raw_store = FilesystemRawImportStore(tmp_path / "raw")

    with pytest.raises(TrackImportError) as raised:
        raw_store.store(b"bytes", _digest(b"different bytes"))

    assert raised.value.code is ImportErrorCode.RAW_STORAGE_FAILED
    assert not list((tmp_path / "raw").rglob("*.raw"))


@pytest.mark.storage
def test_storing_the_same_content_twice_leaves_it_intact(tmp_path: Path) -> None:
    """A repeated store of matching content stays a no-op, not a rewrite."""
    raw_store = FilesystemRawImportStore(tmp_path / "raw")
    content = b"<gpx>synthetic</gpx>"
    digest = _digest(content)

    raw_store.store(content, digest)
    raw_store.store(content, digest)

    assert raw_store.read(digest) == content
    assert len(list((tmp_path / "raw").rglob("*.raw"))) == 1
    assert not list((tmp_path / "raw").rglob("*.part"))


def test_recording_into_an_unmigrated_database_is_a_named_failure(tmp_path: Path) -> None:
    """A misconfigured deployment fails loudly rather than half-writing."""
    unmigrated = SqliteTrackStore(tmp_path / "empty.sqlite3")

    with pytest.raises(TrackImportError) as raised:
        unmigrated.record_import(_raw(), _run(), [_track()])

    assert raised.value.code is ImportErrorCode.PERSISTENCE_FAILED


# --- Migrating an existing database -----------------------------------------


def _schema_1_database(path: Path) -> None:
    """Create a database in the shape the first productive build wrote."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        MIGRATIONS[0](connection)
        connection.execute("PRAGMA user_version = 1")
        connection.execute("COMMIT")
    finally:
        connection.close()


def _fill_schema_1(path: Path) -> None:
    """Put one imported, classified and corrected track into a schema-1 database."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO raw_imports "
            "(sha256, size_bytes, original_filename, received_at, media_type, input_channel) "
            "VALUES (?, 512, 'old.gpx', ?, 'application/gpx+xml', 'local_file')",
            (SHA, _as_stored(NOW)),
        )
        connection.execute(
            "INSERT INTO processing_runs (id, raw_import_sha256, importer, importer_version, "
            "normalization_schema_version, processed_at, status, error_code) "
            "VALUES (1, ?, 'gpx', '1', 1, ?, 'succeeded', NULL)",
            (SHA, _as_stored(NOW)),
        )
        connection.execute(
            "INSERT INTO tracks (id, raw_import_sha256, source_index, processing_run_id, title, "
            "activity, exchange_format, format_version, creator, started_at, ended_at, "
            "point_count, segment_count) "
            "VALUES (1, ?, 0, 1, 'Old track', 'walking', 'gpx', '1.1', 'Old', ?, ?, 1, 1)",
            (SHA, _as_stored(START), _as_stored(START)),
        )
        connection.execute(
            "INSERT INTO track_classifications "
            "(track_id, detected_kind, confidence, method, method_version) "
            "VALUES (1, 'unknown', 0.0, 'evidence-weights', '1')"
        )
        connection.execute(
            "INSERT INTO track_classification_overrides (track_id, override_kind, set_at) "
            "VALUES (1, 'recorded', ?)",
            (_as_stored(NOW),),
        )
        connection.execute("INSERT INTO track_segments (id, track_id, position) VALUES (1, 1, 0)")
        connection.execute(
            "INSERT INTO track_points (segment_id, position, latitude, longitude, elevation, "
            "recorded_at) VALUES (1, 0, 51.0, 7.0, 40.0, ?)",
            (_as_stored(START),),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()


def _as_stored(moment: datetime) -> str:
    """Return an instant the way the store writes it."""
    return moment.astimezone(UTC).isoformat()


def test_a_schema_1_database_migrates_to_the_latest_schema(tmp_path: Path) -> None:
    """An existing deployment keeps its data across the upgrade.

    The interesting part is not the version number: it is that the rebuild of the
    tracks table did not take the classification, the correction or the geometry
    with it.
    """
    path = tmp_path / "old.sqlite3"
    _schema_1_database(path)
    _fill_schema_1(path)
    store = SqliteTrackStore(path)

    assert store.migrate() == SCHEMA_VERSION

    (summary,) = store.list_tracks(TrackQuery()).tracks
    assert summary.title == "Old track"
    assert summary.point_count == 1
    assert summary.effective_kind is TrackKind.RECORDED
    assert summary.classification.override is TrackKind.RECORDED
    geometry = store.get_geometry(summary.track_id)
    assert geometry is not None
    assert geometry[0].point_count == 1


def _database_at(path: Path, version: int) -> None:
    """Create a database in the shape the build of that schema version wrote."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        for index in range(version):
            MIGRATIONS[index](connection)
        connection.execute(f"PRAGMA user_version = {version:d}")
        connection.execute("COMMIT")
    finally:
        connection.close()


def _fill_schema_2(path: Path) -> None:
    """Put one track with two stored links into a schema-2 database."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO raw_imports "
            "(sha256, size_bytes, original_filename, received_at, media_type, input_channel, "
            "active_processing_run_id) "
            "VALUES (?, 512, 'old.gpx', ?, 'application/gpx+xml', 'local_file', 1)",
            (SHA, _as_stored(NOW)),
        )
        connection.execute(
            "INSERT INTO processing_runs (id, raw_import_sha256, importer, importer_version, "
            "normalization_schema_version, processed_at, status, error_code) "
            "VALUES (1, ?, 'gpx', '1', 1, ?, 'succeeded', NULL)",
            (SHA, _as_stored(NOW)),
        )
        connection.execute(
            "INSERT INTO tracks (id, raw_import_sha256, source_key, source_index, "
            "processing_run_id, title, activity, exchange_format, format_version, creator, "
            "started_at, ended_at, point_count, segment_count) "
            "VALUES (1, ?, 'trk:0', 0, 1, 'Old track', 'walking', 'gpx', '1.1', 'Old', ?, ?, 1, 1)",
            (SHA, _as_stored(START), _as_stored(START)),
        )
        connection.execute(
            "INSERT INTO track_classifications "
            "(track_id, detected_kind, confidence, method, method_version) "
            "VALUES (1, 'unknown', 0.0, 'evidence-weights', '2')"
        )
        connection.executemany(
            "INSERT INTO track_source_links (track_id, position, url) VALUES (1, ?, ?)",
            [(0, "https://example.test/one"), (1, "https://example.test/two")],
        )
        connection.execute("INSERT INTO track_segments (id, track_id, position) VALUES (1, 1, 0)")
        connection.execute(
            "INSERT INTO track_points (segment_id, position, latitude, longitude, elevation, "
            "recorded_at) VALUES (1, 0, 51.0, 7.0, 40.0, ?)",
            (_as_stored(START),),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()


def test_a_schema_2_database_migrates_to_the_latest_schema(tmp_path: Path) -> None:
    """An upgrade from the previous schema keeps every track and every link.

    Renaming a table is where a migration is most likely to look successful and
    leave the rows behind, so the check is that the data comes back through the
    ordinary read path rather than that a table of the new name exists.
    """
    path = tmp_path / "schema-2.sqlite3"
    _database_at(path, 2)
    _fill_schema_2(path)
    store = SqliteTrackStore(path)

    assert store.migrate() == SCHEMA_VERSION

    (summary,) = store.list_tracks(TrackQuery()).tracks
    assert summary.title == "Old track"
    assert summary.source.external_links == (
        "https://example.test/one",
        "https://example.test/two",
    )


def test_a_migrated_run_keeps_the_versions_it_recorded(tmp_path: Path) -> None:
    """A migration must not make an old run look like one this build wrote.

    The classifier columns arrive empty on existing rows, and stay empty. Filling
    them with today's version would make every stored generation claim the
    installed processing, and `--outdated` would then find nothing -- the exact
    question the columns were added to answer.
    """
    path = tmp_path / "schema-2.sqlite3"
    _database_at(path, 2)
    _fill_schema_2(path)
    store = SqliteTrackStore(path)

    store.migrate()

    snapshot = store.processing_snapshot(SHA)
    assert snapshot is not None
    assert snapshot.current_run is not None
    assert snapshot.current_run.importer_version == "1"
    assert snapshot.current_run.classifier is None
    assert snapshot.current_run.profile is None, "a migrated run claims a profile it cannot prove"


def test_a_migrated_track_says_its_identity_was_reconstructed(tmp_path: Path) -> None:
    """Schema 1 recorded a position, so a position is all that can be recovered.

    The key is marked as legacy rather than presented as one an importer produced.
    Reprocessing is what turns it into the real thing.
    """
    path = tmp_path / "old.sqlite3"
    _schema_1_database(path)
    _fill_schema_1(path)
    store = SqliteTrackStore(path)

    store.migrate()

    (summary,) = store.list_tracks(TrackQuery()).tracks
    assert summary.source_key == f"{LEGACY_SOURCE_KEY_PREFIX}0"


def test_reprocessing_gives_a_migrated_track_its_real_identity(tmp_path: Path) -> None:
    """The first reprocess adopts the legacy row rather than duplicating it.

    Adopting it is what keeps the user's correction attached to the track it was
    made on. A second row would leave the correction on a track nobody sees.
    """
    path = tmp_path / "old.sqlite3"
    _schema_1_database(path)
    _fill_schema_1(path)
    store = SqliteTrackStore(path)
    store.migrate()

    store.record_import(_raw(), _run(version="2"), [_track(title="Old track", source_key="trk:0")])

    (summary,) = store.list_tracks(TrackQuery()).tracks
    assert summary.source_key == "trk:0"
    assert summary.effective_kind is TrackKind.RECORDED


def test_the_migration_and_the_legacy_prefix_agree(tmp_path: Path) -> None:
    """The migration spells its prefix out; this is what keeps the two in step.

    A migration is a historical record and must not change when a constant is
    renamed, so the literal is frozen in the SQL. That only works if something
    notices when they drift apart.
    """
    path = tmp_path / "old.sqlite3"
    _schema_1_database(path)
    _fill_schema_1(path)
    store = SqliteTrackStore(path)

    store.migrate()

    with store.connection() as connection:
        stored = str(connection.execute("SELECT source_key FROM tracks").fetchone()["source_key"])
    assert stored.startswith(LEGACY_SOURCE_KEY_PREFIX)


def test_a_failed_migration_leaves_the_database_at_its_old_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration is one transaction: it happens completely or not at all."""
    path = tmp_path / "old.sqlite3"
    _schema_1_database(path)
    _fill_schema_1(path)

    def explode(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE half_done (id INTEGER PRIMARY KEY)")
        raise sqlite3.OperationalError("migration failed halfway")

    monkeypatch.setattr(migrations, "MIGRATIONS", (MIGRATIONS[0], explode))
    store = SqliteTrackStore(path)

    with pytest.raises(sqlite3.OperationalError):
        store.migrate()

    assert store.schema_version() == 1
    with store.connection() as connection:
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "half_done" not in tables


def _fill_schema_4(path: Path) -> None:
    """Put one track into a schema-4 database, under the names that schema used."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO raw_imports "
            "(sha256, size_bytes, original_filename, received_at, media_type, input_channel, "
            "active_processing_run_id) "
            "VALUES (?, 512, 'old.gpx', ?, 'application/gpx+xml', 'local_file', 1)",
            (SHA, _as_stored(NOW)),
        )
        connection.execute(
            "INSERT INTO processing_runs (id, raw_import_sha256, importer, importer_version, "
            "normalization_schema_version, processed_at, status, error_code, classifier, "
            "classifier_version) "
            "VALUES (1, ?, 'gpx', '1', 2, ?, 'succeeded', NULL, 'evidence-weights', '2')",
            (SHA, _as_stored(NOW)),
        )
        connection.execute(
            "INSERT INTO tracks (id, raw_import_sha256, source_key, source_index, "
            "processing_run_id, title, activity, exchange_format, format_version, creator, "
            "started_at, ended_at, point_count, segment_count) "
            "VALUES (1, ?, 'trk:0', 0, 1, 'Old track', 'walking', 'gpx', '1.1', 'Old', ?, ?, 1, 1)",
            (SHA, _as_stored(START), _as_stored(START)),
        )
        connection.execute(
            "INSERT INTO track_classifications "
            "(track_id, detected_kind, confidence, method, method_version) "
            "VALUES (1, 'unknown', 0.0, 'evidence-weights', '2')"
        )
        connection.execute("INSERT INTO track_segments (id, track_id, position) VALUES (1, 1, 0)")
        connection.execute(
            "INSERT INTO track_points (segment_id, position, latitude, longitude, elevation, "
            "recorded_at) VALUES (1, 0, 51.0, 7.0, 40.0, ?)",
            (_as_stored(START),),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()


@pytest.mark.parametrize("version", range(1, SCHEMA_VERSION))
def test_every_intermediate_schema_migrates_to_the_latest(tmp_path: Path, version: int) -> None:
    """No deployment is stranded on a version somebody stopped upgrading from.

    The tail of migrations is run from wherever a database happens to be, so a
    build that skipped several releases arrives at the same schema as one that
    upgraded every time.
    """
    path = tmp_path / f"schema-{version}.sqlite3"
    _database_at(path, version)
    store = SqliteTrackStore(path)

    assert store.migrate() == SCHEMA_VERSION
    assert store.schema_version() == SCHEMA_VERSION


def test_a_database_without_analysis_gains_it_without_losing_a_track(tmp_path: Path) -> None:
    """Adding derived metrics leaves the data they will be derived from alone.

    Nothing is back-filled either: no track was analysed before the migration,
    and a metric invented here would be a number nobody derived.
    """
    path = tmp_path / "schema-4.sqlite3"
    _database_at(path, 4)
    _fill_schema_4(path)
    store = SqliteTrackStore(path)

    assert store.migrate() == SCHEMA_VERSION

    (summary,) = store.list_tracks(TrackQuery()).tracks
    assert summary.title == "Old track"
    assert store.current_analysis(summary.track_id) is None
    assert store.analysis_run_count(summary.track_id) == 0
