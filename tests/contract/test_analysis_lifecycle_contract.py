"""Executable contracts for the analysis lifecycle.

Analysis is derived state, and derived state is only safe while it can say what
it was derived *from*. Two independent things can outdate it:

```
the algorithms changed        -> a new AnalysisProfile
the geometry changed          -> a new processing generation
```

and one thing deliberately does not:

```
the user corrected the kind   -> the geometry is untouched, the metrics stand
```

The contracts below are written against observable behaviour -- what a reader
sees, what a batch selection covers -- rather than against column names.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trackvault.application.analysis import InstalledAnalysis
from trackvault.application.analyze import AnalyzeStatus, AnalyzeTrack
from trackvault.domain import (
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
from trackvault.domain.analysis import ANALYSIS_PROFILE, MetricName
from trackvault.infrastructure.database import SqliteTrackStore

pytestmark = [pytest.mark.contract, pytest.mark.analysis]

SHA = "d" * 64
NOW = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
START = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)


class _FixedClock:
    """A clock that answers one instant, so a stored run is predictable."""

    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def now(self) -> datetime:
        """Return the configured instant."""
        return self.moment


@pytest.fixture
def store(tmp_path: Path) -> SqliteTrackStore:
    """Return a migrated store in a throwaway directory."""
    store = SqliteTrackStore(tmp_path / "trackvault.sqlite3")
    store.migrate()
    return store


def _segments(*, points: int = 120, elevation: float | None = 100.0) -> tuple[TrackSegment, ...]:
    """Build a short, steadily moving synthetic recording."""
    return (
        TrackSegment(
            points=tuple(
                TrackPoint(
                    latitude=index * 1.4 / 111_195.0,
                    longitude=8.0,
                    elevation=None if elevation is None else elevation + index,
                    time=START + timedelta(seconds=index),
                )
                for index in range(points)
            )
        ),
    )


def _raw() -> RawImport:
    """Build the raw import every scenario here analyses."""
    return RawImport(
        sha256=SHA,
        size_bytes=2048,
        original_filename="synthetic.gpx",
        received_at=NOW,
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
        classifier="evidence-weights",
        classifier_version="2",
    )


def _track(**overrides: object) -> NormalizedTrack:
    """Build one normalized candidate."""
    values: dict[str, object] = {
        "segments": _segments(),
        "source": SourceMetadata(exchange_format="gpx", format_version="1.1"),
        "source_key": "trk:0",
        "classification": TrackClassification(
            detected=ClassificationResult(
                kind=TrackKind.RECORDED,
                confidence=0.9,
                method="evidence-weights",
                method_version="2",
                evidence=(EvidenceCode.GPS_ACCURACY_PRESENT.value,),
            )
        ),
        "title": "Reference walk",
    }
    values.update(overrides)
    return NormalizedTrack(**values)  # type: ignore[arg-type]


def _imported(store: SqliteTrackStore, **overrides: object) -> int:
    """Import one track and return its identity."""
    (track_id,) = store.record_import(_raw(), _run(), [_track(**overrides)])
    return track_id


def _analyzer(store: SqliteTrackStore, installed: InstalledAnalysis | None = None) -> AnalyzeTrack:
    """Wire the use case against a store and a fixed clock."""
    return AnalyzeTrack(
        repository=store,
        clock=_FixedClock(),
        analysis=installed or InstalledAnalysis(),
    )


# --- Publication ------------------------------------------------------------


def test_a_successful_analysis_becomes_the_current_one(store: SqliteTrackStore) -> None:
    """The metrics a reader sees are the ones the last good run produced."""
    track_id = _imported(store)

    outcome = _analyzer(store)(track_id)

    assert outcome.status is AnalyzeStatus.ANALYZED
    stored = store.current_analysis(track_id)
    assert stored is not None
    assert stored.run.profile == ANALYSIS_PROFILE
    assert stored.metrics[MetricName.DISTANCE].value > 0.0


def test_a_track_with_no_analysis_yet_reports_none(store: SqliteTrackStore) -> None:
    """Absence is a normal state, not an error and not an empty metric set."""
    track_id = _imported(store)

    assert store.current_analysis(track_id) is None


def test_analysing_an_unknown_track_changes_nothing(store: SqliteTrackStore) -> None:
    """A track identity that does not exist is answered, not raised about."""
    outcome = _analyzer(store)(9999)

    assert outcome.status is AnalyzeStatus.UNKNOWN_TRACK
    assert store.current_analysis(9999) is None


def test_a_published_analysis_is_complete_or_absent(store: SqliteTrackStore) -> None:
    """No reader ever sees a new distance beside an old duration.

    The metrics, the quality flags and the switch that makes them current are one
    transaction, so a half-written run cannot become the answer to anything.
    """
    track_id = _imported(store)
    _analyzer(store)(track_id)

    stored = store.current_analysis(track_id)
    assert stored is not None
    assert MetricName.DISTANCE in stored.metrics
    assert MetricName.ELAPSED_DURATION in stored.metrics
    assert MetricName.MOVING_DURATION in stored.metrics


def test_analysing_twice_replaces_what_is_current_and_keeps_the_history(
    store: SqliteTrackStore,
) -> None:
    """Old runs are kept for debugging; only one of them is current."""
    track_id = _imported(store)
    analyze = _analyzer(store)

    analyze(track_id)
    analyze(track_id)

    snapshot = store.analysis_snapshot(track_id)
    assert snapshot is not None
    assert snapshot.current_run_id == snapshot.latest_run_id
    assert store.analysis_run_count(track_id) == 2


# --- Currency ---------------------------------------------------------------


def test_a_fresh_analysis_is_current(store: SqliteTrackStore) -> None:
    """Nothing has moved on, so nothing needs redoing."""
    track_id = _imported(store)
    analyze = _analyzer(store)
    analyze(track_id)

    assert InstalledAnalysis().is_current(store.analysis_snapshot(track_id))
    assert analyze.outdated_tracks() == ()


def test_a_track_that_was_never_analysed_is_outdated(store: SqliteTrackStore) -> None:
    """Missing analysis is the most outdated state there is."""
    track_id = _imported(store)

    assert not InstalledAnalysis().is_current(store.analysis_snapshot(track_id))
    assert _analyzer(store).outdated_tracks() == (track_id,)


def test_a_newer_analysis_profile_outdates_a_stored_result(store: SqliteTrackStore) -> None:
    """Changing an algorithm must not leave old numbers looking freshly produced."""
    track_id = _imported(store)
    _analyzer(store)(track_id)

    upgraded = InstalledAnalysis(
        replace(
            ANALYSIS_PROFILE,
            elevation_algorithm_version=ANALYSIS_PROFILE.elevation_algorithm_version + 1,
        )
    )

    assert not upgraded.is_current(store.analysis_snapshot(track_id))
    assert _analyzer(store, upgraded).outdated_tracks() == (track_id,)


def test_analysing_under_a_new_profile_makes_the_result_current_again(
    store: SqliteTrackStore,
) -> None:
    """The upgrade path an algorithm change relies on."""
    track_id = _imported(store)
    _analyzer(store)(track_id)
    upgraded = InstalledAnalysis(
        replace(
            ANALYSIS_PROFILE,
            movement_algorithm_version=ANALYSIS_PROFILE.movement_algorithm_version + 1,
        )
    )

    assert _analyzer(store, upgraded)(track_id).status is AnalyzeStatus.ANALYZED

    assert upgraded.is_current(store.analysis_snapshot(track_id))
    assert _analyzer(store, upgraded).outdated_tracks() == ()


def test_reprocessing_outdates_the_analysis_of_the_old_generation(
    store: SqliteTrackStore,
) -> None:
    """New geometry means the old numbers describe something that is gone."""
    track_id = _imported(store)
    _analyzer(store)(track_id)

    store.record_import(_raw(), _run(version="2"), [_track(segments=_segments(points=200))])

    assert not InstalledAnalysis().is_current(store.analysis_snapshot(track_id))
    assert _analyzer(store).outdated_tracks() == (track_id,)


def test_a_failed_reprocess_leaves_the_analysis_current(store: SqliteTrackStore) -> None:
    """The generation a reader sees did not change, so its metrics did not either.

    Invalidating here would make a broken importer version cost the archive its
    statistics as well as its parse.
    """
    track_id = _imported(store)
    _analyzer(store)(track_id)

    store.record_import(
        _raw(),
        _run(version="2", status=ProcessingStatus.FAILED, error_code="invalid_gpx"),
        [],
    )

    assert InstalledAnalysis().is_current(store.analysis_snapshot(track_id))
    assert _analyzer(store).outdated_tracks() == ()


def test_a_classification_override_does_not_outdate_the_analysis(
    store: SqliteTrackStore,
) -> None:
    """A correction changes what a track counts towards, not how long it is.

    Distance and elevation come from geometry the user did not touch, so
    recomputing them would burn work to reach the same numbers.
    """
    track_id = _imported(store)
    _analyzer(store)(track_id)
    before = store.current_analysis(track_id)

    assert store.set_override(track_id, TrackKind.PLANNED, NOW)

    assert InstalledAnalysis().is_current(store.analysis_snapshot(track_id))
    after = store.current_analysis(track_id)
    assert after is not None
    assert before is not None
    assert after.metrics == before.metrics


# --- Failure isolation ------------------------------------------------------


def test_a_track_stays_readable_when_its_analysis_is_missing(store: SqliteTrackStore) -> None:
    """A track being available while its analysis is absent is a normal state."""
    track_id = _imported(store)

    summary = store.get_track(track_id)
    assert summary is not None
    assert summary.point_count == 120
    assert store.current_analysis(track_id) is None


def test_analysis_never_alters_the_normalized_geometry(store: SqliteTrackStore) -> None:
    """Outlier handling is an analysis decision, never a deletion.

    The raw import and the normalized track are the authority; analysis is a
    reading of them, and a reading that edited its source would destroy the
    ability to read it differently later.
    """
    track_id = _imported(store)
    before = store.get_geometry(track_id)

    _analyzer(store)(track_id)

    assert store.get_geometry(track_id) == before


def test_analysis_of_a_track_that_left_the_current_generation_is_refused(
    store: SqliteTrackStore,
) -> None:
    """History is not analysed. Only what a reader sees has metrics."""
    track_id = _imported(store)
    store.record_import(_raw(), _run(version="2"), [_track(source_key="trk:9")])

    assert _analyzer(store)(track_id).status is AnalyzeStatus.UNKNOWN_TRACK
