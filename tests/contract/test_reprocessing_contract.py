"""Executable contracts for reprocessing and the current normalized generation.

Raw imports and processing runs exist so that a parser or classifier upgrade can
regenerate normalized data from the original bytes. That only works if two things
stay apart:

```
processing history      every attempt, append-only
current generation      exactly one successful run's candidates
```

The contracts below are about what a *reader* sees after a reprocess. They are
deliberately written against observable behaviour rather than against a column
name: which candidate a user corrected is a business fact, and it has to survive
however the schema stores it.

Candidates are told apart by their title here, because that is the one thing a
scenario can state about "the same logical track" without borrowing the identity
mechanism the tests are meant to check.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpx_view.application.ports import TrackQuery, TrackSummary
from gpx_view.domain import (
    NORMALIZATION_SCHEMA_VERSION,
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
from gpx_view.infrastructure.database import SqliteTrackStore

pytestmark = [pytest.mark.contract, pytest.mark.reprocessing]

SHA = "c" * 64
NOW = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
START = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SqliteTrackStore:
    """Return a migrated store in a throwaway directory."""
    store = SqliteTrackStore(tmp_path / "gpx-view.sqlite3")
    store.migrate()
    return store


def _raw() -> RawImport:
    """Build the one raw import every scenario here reprocesses."""
    return RawImport(
        sha256=SHA,
        size_bytes=1024,
        original_filename="synthetic.gpx",
        received_at=NOW,
        media_type="application/gpx+xml",
        input_channel=InputChannel.LOCAL_FILE,
    )


def _run(
    version: str = "1",
    status: ProcessingStatus = ProcessingStatus.SUCCEEDED,
    error_code: str | None = None,
) -> ProcessingRun:
    """Build one processing attempt."""
    return ProcessingRun(
        raw_import_sha256=SHA,
        importer="gpx",
        importer_version=version,
        normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
        processed_at=NOW,
        status=status,
        error_code=error_code,
    )


def _track(title: str, kind: TrackKind = TrackKind.UNKNOWN) -> NormalizedTrack:
    """Build one candidate, identifiable by its title.

    The title doubles as the candidate's source key, which is what "the same
    logical candidate" means in these scenarios: the same key is the same
    candidate however the run orders it.
    """
    offset = len(title)
    return NormalizedTrack(
        source_key=f"trk:{title}",
        segments=(
            TrackSegment(
                points=(
                    TrackPoint(
                        latitude=51.0 + offset / 100,
                        longitude=7.0 + offset / 100,
                        elevation=100.0,
                        time=START + timedelta(minutes=offset),
                    ),
                    TrackPoint(latitude=51.1 + offset / 100, longitude=7.1 + offset / 100),
                )
            ),
        ),
        source=SourceMetadata(exchange_format="gpx", format_version="1.1", creator="Synthetic"),
        classification=TrackClassification(
            detected=ClassificationResult(
                kind=kind,
                confidence=0.0 if kind is TrackKind.UNKNOWN else 0.8,
                method="evidence-weights",
                method_version="1",
                evidence=() if kind is TrackKind.UNKNOWN else (EvidenceCode.TRACK_ELEMENT_PRESENT,),
            )
        ),
        title=title,
    )


def _titles(store: SqliteTrackStore) -> tuple[str | None, ...]:
    """Return the titles a reader currently sees, in listing order."""
    return tuple(summary.title for summary in store.list_tracks(TrackQuery()).tracks)


def _by_title(store: SqliteTrackStore, title: str) -> TrackSummary | None:
    """Return the currently visible track with that title, or ``None``."""
    return next((s for s in store.list_tracks(TrackQuery()).tracks if s.title == title), None)


# --- The current generation is exactly one successful run's candidates -------


def test_a_reprocess_with_fewer_candidates_drops_the_ones_it_did_not_produce(
    store: SqliteTrackStore,
) -> None:
    """A successful run replaces the whole candidate set, not just the overlap.

    A parser that learns a document actually holds one track, not two, must not
    leave the second one standing as a current track forever.
    """
    store.record_import(_raw(), _run("1"), [_track("A"), _track("B")])

    store.record_import(_raw(), _run("2"), [_track("A")])

    assert _titles(store) == ("A",)
    assert len(store.track_ids_for(SHA)) == 1


def test_a_reprocess_that_produces_no_candidate_empties_the_current_view(
    store: SqliteTrackStore,
) -> None:
    """Zero candidates is a successful outcome, and it is a visible one.

    This is not the same as a failed run: the document was read, and it holds
    nothing importable. The previous tracks must not survive as current.
    """
    store.record_import(_raw(), _run("1"), [_track("A"), _track("B")])

    store.record_import(_raw(), _run("2"), [])

    assert _titles(store) == ()


def test_a_reprocess_with_more_candidates_shows_all_of_them(store: SqliteTrackStore) -> None:
    """A parser that learns to read more of a document may add candidates."""
    store.record_import(_raw(), _run("1"), [_track("A")])

    store.record_import(_raw(), _run("2"), [_track("A"), _track("B")])

    assert set(_titles(store)) == {"A", "B"}


# --- Candidate identity is independent of ordering --------------------------


def test_a_user_correction_stays_with_the_candidate_it_was_made_on(
    store: SqliteTrackStore,
) -> None:
    """An override belongs to a logical candidate, never to a position.

    A reprocess that yields an extra candidate in front shifts every later
    position by one. If identity is the position, the correction the user made on
    one track silently becomes a correction on a different one -- the worst kind
    of data error, because nothing looks broken.
    """
    identities = store.record_import(_raw(), _run("1"), [_track("A"), _track("B")])
    store.set_override(identities[1], TrackKind.RECORDED, NOW)

    store.record_import(_raw(), _run("2"), [_track("X"), _track("A"), _track("B")])

    corrected = [
        s.title
        for s in store.list_tracks(TrackQuery()).tracks
        if s.classification.override is not None
    ]
    assert corrected == ["B"], "the correction moved to a different logical candidate"


def test_reordered_candidates_keep_their_own_classification(store: SqliteTrackStore) -> None:
    """Reordering the source candidates does not swap their detected results."""
    store.record_import(
        _raw(), _run("1"), [_track("A", TrackKind.RECORDED), _track("B", TrackKind.PLANNED)]
    )

    store.record_import(
        _raw(), _run("2"), [_track("B", TrackKind.PLANNED), _track("A", TrackKind.RECORDED)]
    )

    kinds = {s.title: s.detected_kind for s in store.list_tracks(TrackQuery()).tracks}
    assert kinds == {"A": TrackKind.RECORDED, "B": TrackKind.PLANNED}


def test_a_candidate_that_disappears_and_returns_gets_its_correction_back(
    store: SqliteTrackStore,
) -> None:
    """A parser regression must not quietly discard what the user decided.

    A candidate that a newer importer temporarily fails to see stops being
    current. That is not a reason to forget the correction: when the same logical
    candidate reappears, the user's decision applies to it again.
    """
    identities = store.record_import(_raw(), _run("1"), [_track("A"), _track("B")])
    store.set_override(identities[1], TrackKind.RECORDED, NOW)

    store.record_import(_raw(), _run("2"), [_track("A")])
    store.record_import(_raw(), _run("3"), [_track("A"), _track("B")])

    restored = _by_title(store, "B")
    assert restored is not None
    assert restored.effective_kind is TrackKind.RECORDED


# --- History and the current generation are separate authorities ------------


def test_a_failed_reprocess_leaves_the_last_good_generation_current(
    store: SqliteTrackStore,
) -> None:
    """A broken parser version must not destroy data that was already good.

    Both facts have to be expressible at once: the newest attempt failed, and the
    tracks a reader sees are still the ones the last successful run produced.
    """
    store.record_import(_raw(), _run("1"), [_track("A"), _track("B")])

    store.record_import(_raw(), _run("2", ProcessingStatus.FAILED, "invalid_gpx"), [])

    assert set(_titles(store)) == {"A", "B"}
    latest = store.latest_run(SHA)
    assert latest is not None
    assert latest.status is ProcessingStatus.FAILED
    assert latest.error_code == "invalid_gpx"


def test_a_successful_reprocess_after_a_failure_becomes_current(
    store: SqliteTrackStore,
) -> None:
    """A fixed importer takes over again, as the whole point of keeping the source."""
    store.record_import(_raw(), _run("1", ProcessingStatus.FAILED, "invalid_gpx"), [])

    store.record_import(_raw(), _run("2"), [_track("A")])

    assert _titles(store) == ("A",)
    latest = store.latest_run(SHA)
    assert latest is not None
    assert latest.status is ProcessingStatus.SUCCEEDED


def test_every_attempt_stays_in_the_history(store: SqliteTrackStore) -> None:
    """Processing history is append-only: an attempt is never overwritten."""
    store.record_import(_raw(), _run("1"), [_track("A")])
    store.record_import(_raw(), _run("2", ProcessingStatus.FAILED, "invalid_gpx"), [])
    store.record_import(_raw(), _run("3"), [_track("A")])

    assert store.run_count(SHA) == 3


def test_reprocessing_never_touches_the_raw_import(store: SqliteTrackStore) -> None:
    """The source is immutable evidence, whatever a later importer decides."""
    store.record_import(_raw(), _run("1"), [_track("A")])
    before = store.find_raw_import(SHA)

    store.record_import(_raw(), _run("2"), [_track("A"), _track("B")])

    assert store.find_raw_import(SHA) == before


# --- Listing order is the order the port declares ----------------------------


def test_tracks_are_listed_newest_imported_source_first(tmp_path: Path) -> None:
    """The declared order is implemented, not approximated.

    A port that promises one order while the query delivers another is worse than
    an undocumented order: a caller trusts the promise and the bug only shows up
    once there is enough data for the two to disagree.
    """
    store = SqliteTrackStore(tmp_path / "ordered.sqlite3")
    store.migrate()
    older = RawImport(
        sha256="d" * 64,
        size_bytes=1,
        original_filename="older.gpx",
        received_at=NOW - timedelta(days=1),
        media_type="application/gpx+xml",
        input_channel=InputChannel.LOCAL_FILE,
    )
    store.record_import(older, replace(_run(), raw_import_sha256=older.sha256), [_track("Older")])
    store.record_import(_raw(), _run(), [_track("Newer first"), _track("Newer second")])

    assert _titles(store) == ("Newer first", "Newer second", "Older")


def test_the_listing_order_is_stable_across_reads(store: SqliteTrackStore) -> None:
    """Two identical questions get identical answers, whatever the storage does."""
    store.record_import(_raw(), _run(), [_track("A"), _track("B"), _track("C")])

    assert _titles(store) == _titles(store)
    assert _titles(store) == ("A", "B", "C")
