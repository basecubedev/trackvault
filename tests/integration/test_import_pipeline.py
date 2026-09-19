"""The whole import pipeline, from bytes to stored, classified tracks.

This is where the adapter, the classifier and persistence meet. It is also where
the classification rules meet real documents: every rule has a positive, a
negative and an undecidable fixture here.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trackvault.application import ImportErrorCode, ImportLimits
from trackvault.application.import_tracks import (
    ImportOutcome,
    ImportRequest,
    ImportStatus,
    ImportTracks,
)
from trackvault.application.normalization import NormalizeRawImport
from trackvault.application.ports import TrackQuery
from trackvault.domain import Activity, EvidenceCode, InputChannel, ProcessingStatus, TrackKind
from trackvault.infrastructure.database import SqliteTrackStore
from trackvault.infrastructure.filesystem import FilesystemRawImportStore
from trackvault.infrastructure.gpx import GpxImporter

pytestmark = [pytest.mark.integration, pytest.mark.gpx, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"
NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


class FrozenClock:
    """A clock that never moves, so stored instants stay predictable."""

    def now(self) -> datetime:
        """Return the fixed instant this test suite runs at."""
        return NOW


def _pipeline(
    tmp_path: Path, store: SqliteTrackStore, limits: ImportLimits | None = None
) -> ImportTracks:
    """Wire the import use case to a throwaway data directory."""
    return ImportTracks(
        raw_store=FilesystemRawImportStore(tmp_path / "raw"),
        repository=store,
        clock=FrozenClock(),
        normalize=NormalizeRawImport(
            importers=(GpxImporter(),),
            repository=store,
            clock=FrozenClock(),
            limits=limits or ImportLimits(),
        ),
    )


@pytest.fixture
def pipeline(tmp_path: Path) -> ImportTracks:
    """Return the import use case wired to a throwaway data directory."""
    store = SqliteTrackStore(tmp_path / "trackvault.sqlite3")
    store.migrate()
    return _pipeline(tmp_path, store)


@pytest.fixture
def store(pipeline: ImportTracks, tmp_path: Path) -> SqliteTrackStore:  # noqa: ARG001
    """Return the store the pipeline writes to."""
    return SqliteTrackStore(tmp_path / "trackvault.sqlite3")


@pytest.fixture
def raw_store(tmp_path: Path) -> FilesystemRawImportStore:
    """Return the managed raw storage the pipeline writes to."""
    return FilesystemRawImportStore(tmp_path / "raw")


def content(name: str) -> bytes:
    """Return the bytes of a synthetic fixture."""
    return (FIXTURES / name).read_bytes()


def run(
    pipeline: ImportTracks,
    name: str,
    original_filename: str | None = None,
    channel: InputChannel = InputChannel.LOCAL_FILE,
) -> ImportOutcome:
    """Import a fixture through the pipeline."""
    return pipeline(
        ImportRequest(
            content=content(name),
            original_filename=name if original_filename is None else original_filename,
            input_channel=channel,
        )
    )


# --- The pipeline stores what it imported -----------------------------------


def test_a_recording_is_imported_classified_and_stored(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """The whole chain runs: parse, classify, persist, read back."""
    outcome = run(pipeline, "recorded-measurements.gpx")

    assert outcome.status is ImportStatus.IMPORTED
    (track_id,) = outcome.track_ids

    summary = store.get_track(track_id)
    assert summary is not None
    assert summary.title == "Synthetic morning walk"
    assert summary.activity is Activity.WALKING
    assert summary.point_count == 4
    assert summary.segment_count == 1
    assert summary.started_at == datetime(2026, 5, 4, 8, 0, tzinfo=UTC)
    assert summary.source.exchange_format == "gpx"


def test_the_original_bytes_are_kept_byte_identically(
    pipeline: ImportTracks, raw_store: FilesystemRawImportStore
) -> None:
    """The source evidence must survive normalization untouched."""
    outcome = run(pipeline, "recorded-measurements.gpx")

    assert raw_store.read(outcome.sha256) == content("recorded-measurements.gpx")


def test_one_file_may_produce_several_tracks(pipeline: ImportTracks) -> None:
    """`RawImport -> exactly one NormalizedTrack` is not wired in anywhere."""
    outcome = run(pipeline, "multiple-tracks.gpx")

    assert len(outcome.track_ids) == 2


def test_segment_boundaries_survive_the_whole_pipeline(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """A paused recording stays one track of several segments, end to end."""
    outcome = run(pipeline, "multiple-segments.gpx")
    (track_id,) = outcome.track_ids

    geometry = store.get_geometry(track_id)

    assert geometry is not None
    assert [segment.point_count for segment in geometry] == [2, 1, 2]


# --- Classification of real documents ---------------------------------------


def test_a_document_with_measurement_metadata_is_classified_recorded(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """Positive case: receiver quality and heading support a recording."""
    outcome = run(pipeline, "recorded-measurements.gpx")
    summary = store.get_track(outcome.track_ids[0])

    assert summary is not None
    assert summary.detected_kind is TrackKind.RECORDED
    assert EvidenceCode.GPS_ACCURACY_PRESENT.value in summary.classification.detected.evidence


def test_a_generic_external_link_does_not_make_a_track_planned(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """Negative case: a link is where to read more, not how the geometry was made.

    An ordinary recording exported by an application that puts its own website
    into ``<metadata><link>`` must not be filed as a planned route because of it.
    """
    outcome = run(pipeline, "generic-external-link.gpx")
    summary = store.get_track(outcome.track_ids[0])

    assert summary is not None
    assert summary.detected_kind is TrackKind.UNKNOWN


def test_a_route_element_is_classified_planned(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """Positive case: a route structure with nothing measured about it."""
    outcome = run(pipeline, "route-only.gpx")
    summary = store.get_track(outcome.track_ids[0])

    assert summary is not None
    assert summary.detected_kind is TrackKind.PLANNED


def test_a_computed_route_with_turn_instructions_is_classified_planned(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """Positive case: turn-by-turn instructions exist only for a computed route."""
    outcome = run(pipeline, "planned-route-instructions.gpx")
    summary = store.get_track(outcome.track_ids[0])

    assert summary is not None
    assert summary.detected_kind is TrackKind.PLANNED
    assert EvidenceCode.ROUTE_INSTRUCTIONS_PRESENT.value in (
        summary.classification.detected.evidence
    )


@pytest.mark.parametrize(
    "name",
    [
        "ambiguous-minimal.gpx",
        "missing-timestamps.gpx",
        "missing-elevation.gpx",
        "ambiguous-measured-with-instructions.gpx",
    ],
)
def test_thin_evidence_stays_unknown_end_to_end(
    pipeline: ImportTracks, store: SqliteTrackStore, name: str
) -> None:
    """Negative case: positions, times and elevation decide nothing on their own."""
    outcome = run(pipeline, name)
    summary = store.get_track(outcome.track_ids[0])

    assert summary is not None
    assert summary.detected_kind is TrackKind.UNKNOWN
    assert summary.classification.detected.confidence == pytest.approx(0.0)


def test_a_legacy_document_with_measurements_is_classified_recorded(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """The rules are format-version independent: GPX 1.0 gets the same treatment."""
    outcome = run(pipeline, "gpx-1.0.gpx")
    summary = store.get_track(outcome.track_ids[0])

    assert summary is not None
    assert summary.detected_kind is TrackKind.RECORDED


def test_the_creator_never_decides_the_kind(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """Two documents by the same writer get different verdicts from their evidence."""
    recorded = run(pipeline, "recorded-measurements.gpx")
    ambiguous = run(pipeline, "activity-extension.gpx")

    first = store.get_track(recorded.track_ids[0])
    second = store.get_track(ambiguous.track_ids[0])

    assert first is not None
    assert second is not None
    assert first.source.creator == second.source.creator == "SyntheticRecorder 1.0"
    assert first.detected_kind is TrackKind.RECORDED
    assert second.detected_kind is TrackKind.UNKNOWN


# --- Exact duplicates -------------------------------------------------------


def test_importing_the_same_bytes_twice_is_idempotent(
    pipeline: ImportTracks, store: SqliteTrackStore, raw_store: FilesystemRawImportStore
) -> None:
    """Exact duplicates never become a second archive entry."""
    first = run(pipeline, "recorded-measurements.gpx")
    second = run(pipeline, "recorded-measurements.gpx", original_filename="copy-of-a-track.gpx")

    assert second.status is ImportStatus.DUPLICATE
    assert second.track_ids == first.track_ids
    assert second.error_code is None
    assert len(store.list_tracks(TrackQuery()).tracks) == 1
    assert len(list(raw_store.root.rglob("*.raw"))) == 1


def test_offering_bytes_that_never_became_tracks_again_says_why(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """A duplicate of a source that could not be read is still not in the archive.

    The bytes are held -- that is what makes the offer a duplicate -- but
    whoever offers the file again needs to hear why it is still not a track, not
    "nothing to do". The reason is the one the last attempt recorded: nothing is
    parsed and nothing is recorded to say so.
    """
    first = run(pipeline, "malformed.gpx")
    again = run(pipeline, "malformed.gpx")

    assert first.error_code is ImportErrorCode.INVALID_GPX
    assert again.status is ImportStatus.DUPLICATE
    assert again.track_ids == ()
    assert again.error_code is ImportErrorCode.INVALID_GPX
    assert store.run_count(again.sha256) == 1


def test_a_document_that_holds_no_track_is_a_plain_duplicate(pipeline: ImportTracks) -> None:
    """Holding nothing importable is a successful import, and saying so again is not a failure."""
    first = run(pipeline, "empty-track.gpx")
    again = run(pipeline, "empty-track.gpx")

    assert first.track_ids == again.track_ids == ()
    assert again.status is ImportStatus.DUPLICATE
    assert again.error_code is None


def test_a_stored_reason_this_build_does_not_know_is_not_repeated(
    pipeline: ImportTracks, tmp_path: Path
) -> None:
    """What comes back out of the database is validated before it is believed.

    A reason written by another build, or damaged since, is not an error code
    this build can state. The duplicate is still a duplicate; it just has no
    reason to give.
    """
    first = run(pipeline, "malformed.gpx")
    with sqlite3.connect(tmp_path / "trackvault.sqlite3") as connection:
        connection.execute(
            "UPDATE processing_runs SET error_code = 'from_a_future_build' "
            "WHERE raw_import_sha256 = ?",
            (first.sha256,),
        )

    again = run(pipeline, "malformed.gpx")

    assert again.status is ImportStatus.DUPLICATE
    assert again.error_code is None


def test_a_different_filename_is_still_the_same_content(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """The filename is display metadata and proves nothing about identity."""
    run(pipeline, "recorded-measurements.gpx", original_filename="one.gpx")
    run(pipeline, "recorded-measurements.gpx", original_filename="two.gpx")

    (summary,) = store.list_tracks(TrackQuery()).tracks
    assert summary.raw_import_sha256


def test_different_content_produces_separate_archive_entries(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """Two genuinely different files are two imports."""
    run(pipeline, "recorded-measurements.gpx")
    run(pipeline, "generic-external-link.gpx")

    assert len(store.list_tracks(TrackQuery()).tracks) == 2


# --- Failures stay recoverable ----------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("not-gpx.xml", ImportErrorCode.UNSUPPORTED_FORMAT),
        ("not-xml.txt", ImportErrorCode.UNSUPPORTED_FORMAT),
        ("malformed.gpx", ImportErrorCode.INVALID_GPX),
        ("unsafe-entity.gpx", ImportErrorCode.UNSAFE_XML),
        ("invalid-coordinate.gpx", ImportErrorCode.INVALID_COORDINATE),
        ("invalid-timestamp.gpx", ImportErrorCode.INVALID_TIMESTAMP),
    ],
)
def test_a_file_we_cannot_read_fails_with_a_stable_code(
    pipeline: ImportTracks, store: SqliteTrackStore, name: str, expected: ImportErrorCode
) -> None:
    """Every refusal is named, and no track is stored."""
    outcome = run(pipeline, name)

    assert outcome.status is ImportStatus.FAILED
    assert outcome.error_code is expected
    assert store.list_tracks(TrackQuery()).tracks == ()


def test_a_failed_import_keeps_its_source_evidence(
    pipeline: ImportTracks, store: SqliteTrackStore, raw_store: FilesystemRawImportStore
) -> None:
    """A file we cannot read today is kept so it can be read tomorrow."""
    outcome = run(pipeline, "not-gpx.xml")

    assert store.find_raw_import(outcome.sha256) is not None
    assert raw_store.read(outcome.sha256) == content("not-gpx.xml")
    run_record = store.latest_run(outcome.sha256)
    assert run_record is not None
    assert run_record.status is ProcessingStatus.FAILED
    assert run_record.error_code == ImportErrorCode.UNSUPPORTED_FORMAT.value


def test_a_failed_import_is_not_retried_endlessly(pipeline: ImportTracks) -> None:
    """Rescanning a directory must not reprocess the same broken file forever."""
    run(pipeline, "malformed.gpx")

    second = run(pipeline, "malformed.gpx")

    assert second.status is ImportStatus.DUPLICATE


def test_an_oversized_file_stores_nothing_at_all(
    tmp_path: Path, store: SqliteTrackStore, raw_store: FilesystemRawImportStore
) -> None:
    """A file refused on size must not cost storage either."""
    tiny = SqliteTrackStore(tmp_path / "trackvault.sqlite3")
    tiny.migrate()
    pipeline = _pipeline(tmp_path, tiny, ImportLimits(max_bytes=64))

    outcome = pipeline(ImportRequest(content=content("recorded-measurements.gpx")))

    assert outcome.error_code is ImportErrorCode.IMPORT_TOO_LARGE
    assert store.find_raw_import(outcome.sha256) is None
    assert not list(raw_store.root.rglob("*.raw"))


def test_a_track_without_geometry_imports_successfully_with_no_tracks(
    pipeline: ImportTracks,
) -> None:
    """Zero tracks is a successful import, not a failure."""
    outcome = run(pipeline, "empty-track.gpx")

    assert outcome.status is ImportStatus.IMPORTED
    assert outcome.track_ids == ()


# --- Filenames never authorise anything -------------------------------------


def test_a_path_like_filename_is_discarded_rather_than_trusted(
    pipeline: ImportTracks, store: SqliteTrackStore
) -> None:
    """A filename is display metadata and never reaches the file system."""
    outcome = run(pipeline, "recorded-measurements.gpx", original_filename="../../etc/passwd")

    raw = store.find_raw_import(outcome.sha256)
    assert raw is not None
    assert raw.original_filename is None
