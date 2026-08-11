"""Executable contract: a total never mixes analysis algorithm versions.

The situation this file exists for is ordinary and silent. An algorithm
improves, the profile is bumped, and the archive now holds two kinds of number:
some produced by the rules this build applies and some by the rules the last one
did. Adding them produces a figure that is not a measurement of anything, and
nothing about it looks wrong.

```
10 tracks
 8 analysed by the installed algorithms
 2 left over from the previous ones

total distance = the 8            never the 10
```

Refusing to answer would be worse than mixing -- the eight are perfectly good --
so the total is of the eight, and the response says so plainly enough that a
reader cannot mistake it for all ten. `analyze --outdated` closes the gap, and
it decides what is outdated by asking the same authority this does.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpx_view.application.analysis import InstalledAnalysis
from gpx_view.application.analyze import AnalyzeTrack
from gpx_view.application.ports import AnalysisRun, AnalysisStatus
from gpx_view.application.statistics import (
    AggregationScope,
    GetMonthlyStatistics,
    GetYearStatistics,
    YearStatistics,
)
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
from gpx_view.domain.analysis import ANALYSIS_PROFILE, AnalysisProfile
from gpx_view.infrastructure.database import SqliteTrackStore

pytestmark = [pytest.mark.contract, pytest.mark.statistics, pytest.mark.analysis]

NOW = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)
JANUARY = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
METRES_PER_LATITUDE_DEGREE = 111_195.0


class _FixedClock:
    """A clock that answers one instant."""

    def now(self) -> datetime:
        """Return the configured instant."""
        return NOW


def _next_profile() -> AnalysisProfile:
    """Return a profile one movement version ahead of the installed one.

    Exactly the shape a released algorithm change takes, without this test
    having to guess which component will move next.
    """
    return AnalysisProfile(
        distance_algorithm=ANALYSIS_PROFILE.distance_algorithm,
        distance_algorithm_version=ANALYSIS_PROFILE.distance_algorithm_version,
        movement_algorithm=ANALYSIS_PROFILE.movement_algorithm,
        movement_algorithm_version=ANALYSIS_PROFILE.movement_algorithm_version + 1,
        elevation_algorithm=ANALYSIS_PROFILE.elevation_algorithm,
        elevation_algorithm_version=ANALYSIS_PROFILE.elevation_algorithm_version,
        metric_schema_version=ANALYSIS_PROFILE.metric_schema_version,
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteTrackStore:
    """Return a migrated store in a throwaway directory."""
    store = SqliteTrackStore(tmp_path / "gpx-view.sqlite3")
    store.migrate()
    return store


def _segments(metres: float, start: datetime | None) -> tuple[TrackSegment, ...]:
    """Build a straight segment of a known length."""
    points = 40
    step = metres / (points - 1) / METRES_PER_LATITUDE_DEGREE
    return (
        TrackSegment(
            points=tuple(
                TrackPoint(
                    latitude=index * step,
                    longitude=8.0,
                    elevation=100.0 + index * 10.0,
                    time=None if start is None else start + timedelta(seconds=index * 10),
                )
                for index in range(points)
            )
        ),
    )


def _add(
    store: SqliteTrackStore,
    *,
    key: str,
    metres: float = 10_000.0,
    start: datetime | None = JANUARY,
    kind: TrackKind = TrackKind.RECORDED,
    measured: bool = True,
    analysis: AnalysisProfile | None = ANALYSIS_PROFILE,
) -> int:
    """Store one track, analysed under a stated profile or not analysed at all."""
    sha = hashlib.sha256(key.encode()).hexdigest()
    raw = RawImport(
        sha256=sha,
        size_bytes=1024,
        original_filename=None,
        received_at=NOW,
        input_channel=InputChannel.LOCAL_FILE,
    )
    run = ProcessingRun(
        raw_import_sha256=sha,
        importer="gpx",
        importer_version="1",
        normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
        processed_at=NOW,
        status=ProcessingStatus.SUCCEEDED,
        classifier="evidence-weights",
        classifier_version="2",
    )
    evidence = [EvidenceCode.TIMESTAMPS_PRESENT.value] if start else []
    if measured:
        evidence.append(EvidenceCode.GPS_ACCURACY_PRESENT.value)
    else:
        evidence.append(EvidenceCode.MEASUREMENT_METADATA_ABSENT.value)
    track = NormalizedTrack(
        segments=_segments(metres, start),
        source=SourceMetadata(exchange_format="gpx"),
        source_key="trk:0",
        classification=TrackClassification(
            detected=ClassificationResult(
                kind=kind,
                confidence=0.9 if kind is not TrackKind.UNKNOWN else 0.0,
                method="evidence-weights",
                method_version="2",
                evidence=tuple(evidence) if kind is not TrackKind.UNKNOWN else (),
            )
        ),
        title=key,
        activity=Activity.WALKING,
    )
    (track_id,) = store.record_import(raw, run, [track])
    if analysis is not None:
        AnalyzeTrack(repository=store, clock=_FixedClock(), analysis=InstalledAnalysis(analysis))(
            track_id
        )
    return track_id


def _year(
    store: SqliteTrackStore,
    *,
    installed: AnalysisProfile = ANALYSIS_PROFILE,
    scope: AggregationScope = AggregationScope.RECORDED,
) -> YearStatistics:
    """Ask for 2026's totals under a stated installed analysis."""
    return GetYearStatistics(
        repository=store, timezone="UTC", analysis=InstalledAnalysis(installed)
    )(2026, scope=scope)


# --- Mixing --------------------------------------------------------------


def test_an_outdated_analysis_never_reaches_a_total(store: SqliteTrackStore) -> None:
    """Ten kilometres analysed by yesterday's rules is not today's ten kilometres."""
    _add(store, key="current", metres=10_000.0)
    _add(store, key="stale", metres=20_000.0, analysis=_next_profile())

    totals = _year(store, installed=_next_profile()).totals

    assert totals.track_count == 2
    assert totals.distance_m == pytest.approx(20_000.0, rel=0.01), (
        "the total mixed a result produced by different algorithms"
    )
    assert totals.analysed_track_count == 1
    assert totals.tracks_with_outdated_analysis == 1


def test_bumping_the_installed_profile_outdates_every_stored_total(
    store: SqliteTrackStore,
) -> None:
    """The day an algorithm changes, nothing is current until it is redone."""
    _add(store, key="a", metres=10_000.0)
    _add(store, key="b", metres=20_000.0)

    after = _year(store, installed=_next_profile()).totals

    assert after.track_count == 2
    assert after.tracks_with_outdated_analysis == 2
    assert after.analysed_track_count == 0
    assert after.distance_m is None, "a total of nothing current is not a total of zero"


def test_reanalysing_closes_the_gap(store: SqliteTrackStore) -> None:
    """`analyze --outdated` is the migration, and it works through this authority."""
    _add(store, key="a", metres=10_000.0, analysis=None)
    _add(store, key="b", metres=20_000.0, analysis=_next_profile())
    installed = InstalledAnalysis(ANALYSIS_PROFILE)
    analyze = AnalyzeTrack(repository=store, clock=_FixedClock(), analysis=installed)

    before = _year(store).totals
    assert before.analysed_track_count == 0

    for track_id in analyze.outdated_tracks():
        analyze(track_id)

    after = _year(store).totals
    assert after.tracks_without_analysis == 0
    assert after.tracks_with_outdated_analysis == 0
    assert after.analysed_track_count == 2
    assert after.distance_m == pytest.approx(30_000.0, rel=0.01)


def test_the_command_line_and_the_statistics_agree_on_what_is_outdated(
    store: SqliteTrackStore,
) -> None:
    """One authority, asked twice. Two would drift and nobody would notice."""
    _add(store, key="a")
    _add(store, key="b", analysis=_next_profile())
    _add(store, key="c", analysis=None)
    installed = InstalledAnalysis(ANALYSIS_PROFILE)

    selected = AnalyzeTrack(
        repository=store, clock=_FixedClock(), analysis=installed
    ).outdated_tracks()
    totals = _year(store).totals

    assert len(selected) == totals.tracks_without_analysis + totals.tracks_with_outdated_analysis


# --- The three ways of not being current stay distinguishable ----------------


def test_missing_and_outdated_are_counted_apart(store: SqliteTrackStore) -> None:
    """One has never been analysed; the other was, by rules that moved on.

    They need different actions and they are different numbers.
    """
    _add(store, key="never", analysis=None)
    _add(store, key="stale", analysis=_next_profile())
    _add(store, key="fine")

    totals = _year(store).totals

    assert totals.tracks_without_analysis == 1
    assert totals.tracks_with_outdated_analysis == 1
    assert totals.analysed_track_count == 1


def test_a_failed_attempt_is_visible_beside_the_metrics_it_did_not_replace(
    store: SqliteTrackStore,
) -> None:
    """A track can hold good current metrics and a failed newest attempt at once.

    Collapsing the two would make a healthy archive look broken, or a broken one
    look healthy, depending on which won.
    """
    track_id = _add(store, key="a", metres=10_000.0)
    store.record_analysis(
        AnalysisRun(
            track_id=track_id,
            processing_run_id=1,
            profile=ANALYSIS_PROFILE,
            analyzed_at=NOW,
            status=AnalysisStatus.FAILED,
            error_code="analysis_failed",
        ),
        None,
    )

    totals = _year(store).totals

    assert totals.tracks_with_failed_analysis == 1
    assert totals.analysed_track_count == 1, "the earlier good metrics are still current"
    assert totals.distance_m == pytest.approx(10_000.0, rel=0.01)


def test_a_partial_total_says_how_much_of_the_period_it_covers(
    store: SqliteTrackStore,
) -> None:
    """The number that stops a partial total from reading as a complete one."""
    for index in range(8):
        _add(store, key=f"ok{index}", metres=1_000.0)
    for index in range(2):
        _add(store, key=f"stale{index}", metres=1_000.0, analysis=_next_profile())

    totals = _year(store).totals

    assert (totals.analysed_track_count, totals.track_count) == (8, 10)


# --- Timing eligibility ------------------------------------------------------


def test_a_period_covers_only_the_tracks_it_can_date(store: SqliteTrackStore) -> None:
    """Instants nothing vouches for cannot place a track in a year.

    A recording whose timestamps nothing vouches for still went a distance --
    that came from geometry. What it cannot support is the claim that the
    distance happened *in this year*, so it is reported beside the period rather
    than inside it, with its length intact.
    """
    _add(store, key="measured", metres=10_000.0, measured=True)
    _add(store, key="unmeasured", metres=10_000.0, measured=False)

    year = _year(store)

    assert year.totals.track_count == 1
    assert year.totals.distance_m == pytest.approx(10_000.0, rel=0.01)
    assert year.totals.moving_duration_s is not None
    assert year.unplaced.with_unverified_date.track_count == 1
    assert year.unplaced.with_unverified_date.distance_m == pytest.approx(10_000.0, rel=0.01)


def test_an_unmeasured_recording_contributes_no_moving_time(store: SqliteTrackStore) -> None:
    """The 391-position case, in a total."""
    _add(store, key="unmeasured", metres=10_000.0, measured=False)

    year = _year(store)

    assert year.totals.track_count == 0, "an unverifiable date placed a track in a year"
    assert year.totals.moving_duration_s == 0.0, "an empty period totals zero, and says so"
    unplaced = year.unplaced.with_unverified_date
    assert unplaced.track_count == 1
    assert unplaced.distance_m == pytest.approx(10_000.0, rel=0.01)
    assert unplaced.moving_duration_s is None
    assert unplaced.tracks_without_observed_timing == 1


# --- The relations between the periods hold ---------------------------------


def test_the_months_add_up_to_the_year(store: SqliteTrackStore) -> None:
    """A dashboard shows both, and they have to agree."""
    _add(store, key="jan", metres=10_000.0, start=datetime(2026, 1, 5, 9, tzinfo=UTC))
    _add(store, key="feb", metres=20_000.0, start=datetime(2026, 2, 5, 9, tzinfo=UTC))
    _add(
        store,
        key="stale",
        metres=99_000.0,
        start=datetime(2026, 3, 5, 9, tzinfo=UTC),
        analysis=_next_profile(),
    )
    installed = InstalledAnalysis(ANALYSIS_PROFILE)

    year = GetYearStatistics(repository=store, timezone="UTC", analysis=installed)(2026)
    months = GetMonthlyStatistics(repository=store, timezone="UTC", analysis=installed)(2026)

    assert year.totals.distance_m is not None
    assert sum(month.totals.distance_m or 0.0 for month in months.months) == pytest.approx(
        year.totals.distance_m
    )
    assert sum(month.totals.track_count for month in months.months) == year.totals.track_count
    assert (
        sum(month.totals.tracks_with_outdated_analysis for month in months.months)
        == year.totals.tracks_with_outdated_analysis
    )
