"""Executable contracts for actual, planned and unknown aggregates.

The rule the whole feature exists to protect:

```
Recorded  10 km
Planned  100 km
Unknown   50 km

actual total = 10 km        never 160 km, never 110 km
```

Actual and planned are separate sets selected by the **effective** kind, so a
user correction moves a track between them without anything being recalculated.
`UNKNOWN` belongs to neither and is never silently assigned to one.

The second rule is about time: a month is a local month. Which month a late
evening activity falls into depends on the configured aggregation timezone, and
a track's month comes from its own positions -- never from when the file was
imported and never from when it was exported.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpx_view.application.analysis import InstalledAnalysis
from gpx_view.application.analyze import AnalyzeTrack
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
from gpx_view.infrastructure.database import SqliteTrackStore

pytestmark = [pytest.mark.contract, pytest.mark.statistics]

NOW = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)
METRES_PER_LATITUDE_DEGREE = 111_195.0
BERLIN = "Europe/Berlin"


class _FixedClock:
    """A clock that answers one instant."""

    def now(self) -> datetime:
        """Return the configured instant."""
        return NOW


@pytest.fixture
def store(tmp_path: Path) -> SqliteTrackStore:
    """Return a migrated store in a throwaway directory."""
    store = SqliteTrackStore(tmp_path / "gpx-view.sqlite3")
    store.migrate()
    return store


def _segments(metres: float, start: datetime | None) -> tuple[TrackSegment, ...]:
    """Build a straight north-south segment of a known length.

    The length is what the aggregate arithmetic is checked against, so it is
    stated rather than measured: ten points a fixed distance apart.
    """
    points = 10
    step = metres / (points - 1) / METRES_PER_LATITUDE_DEGREE
    return (
        TrackSegment(
            points=tuple(
                TrackPoint(
                    latitude=index * step,
                    longitude=8.0,
                    elevation=100.0 + index * 10.0,
                    time=None if start is None else start + timedelta(seconds=index * 60),
                )
                for index in range(points)
            )
        ),
    )


def _evidence(start: datetime | None, measured: bool) -> tuple[str, ...]:
    """Return the evidence codes a track of this shape would have been seen with.

    A timed track states that its positions carry instants, and a measured one
    states that a receiver said how well it was fixing them. The second is what
    makes the first usable as an activity date, so a fixture that wants a track
    to land in a month has to say both.
    """
    codes: list[str] = []
    if start is not None:
        codes.append(EvidenceCode.TIMESTAMPS_PRESENT.value)
        if measured:
            codes.append(EvidenceCode.GPS_ACCURACY_PRESENT.value)
    elif measured:
        codes.append(EvidenceCode.GPS_ACCURACY_PRESENT.value)
    return tuple(codes)


def _add(
    store: SqliteTrackStore,
    *,
    key: str,
    metres: float,
    start: datetime | None,
    kind: TrackKind = TrackKind.RECORDED,
    activity: Activity = Activity.WALKING,
    measured: bool = True,
) -> int:
    """Store one analysed track of a known length, kind and activity."""
    sha = f"{abs(hash(key)) % (16**63):063x}"[:64].rjust(64, "0")
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
                evidence=_evidence(start, measured),
            )
        ),
        title=key,
        activity=activity,
    )
    (track_id,) = store.record_import(raw, run, [track])
    AnalyzeTrack(repository=store, clock=_FixedClock(), analysis=InstalledAnalysis())(track_id)
    return track_id


def _year(
    store: SqliteTrackStore,
    year: int = 2026,
    *,
    scope: AggregationScope = AggregationScope.RECORDED,
    activity: Activity | None = None,
    timezone: str = "UTC",
) -> YearStatistics:
    """Ask for one year's totals."""
    return GetYearStatistics(repository=store, timezone=timezone, analysis=InstalledAnalysis())(
        year, scope=scope, activity=activity
    )


# --- Actual, planned and unknown stay separate ------------------------------


def test_planned_and_unknown_never_reach_the_actual_total(store: SqliteTrackStore) -> None:
    """Ten recorded kilometres beside a hundred planned and fifty unknown."""
    january = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    _add(store, key="recorded", metres=10_000, start=january)
    _add(store, key="planned", metres=100_000, start=january, kind=TrackKind.PLANNED)
    _add(store, key="unknown", metres=50_000, start=january, kind=TrackKind.UNKNOWN)

    actual = _year(store)

    assert actual.totals.track_count == 1
    assert actual.totals.distance_m == pytest.approx(10_000, rel=0.01)


def test_planned_tracks_are_aggregated_in_their_own_scope(store: SqliteTrackStore) -> None:
    """Planned figures exist; they are simply not actual ones."""
    january = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    _add(store, key="recorded", metres=10_000, start=january)
    _add(store, key="planned", metres=100_000, start=january, kind=TrackKind.PLANNED)

    planned = _year(store, scope=AggregationScope.PLANNED)

    assert planned.totals.track_count == 1
    assert planned.totals.distance_m == pytest.approx(100_000, rel=0.01)


def test_unknown_tracks_keep_a_category_of_their_own(store: SqliteTrackStore) -> None:
    """`UNKNOWN` is permanent and first class, so it is countable without being assigned."""
    january = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    _add(store, key="unknown", metres=50_000, start=january, kind=TrackKind.UNKNOWN)

    unknown = _year(store, scope=AggregationScope.UNKNOWN)
    actual = _year(store)
    planned = _year(store, scope=AggregationScope.PLANNED)

    assert unknown.totals.track_count == 1
    assert actual.totals.track_count == 0
    assert planned.totals.track_count == 0


# --- User overrides move a track between the sets ---------------------------


def test_an_override_moves_a_track_into_the_actual_total(store: SqliteTrackStore) -> None:
    """Fifty unknown kilometres become actual the moment the user says they are.

    Nothing is recalculated: the distance came from geometry the correction did
    not touch, and only which set it belongs to changed.
    """
    january = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    _add(store, key="recorded", metres=10_000, start=january)
    unknown_id = _add(store, key="unknown", metres=50_000, start=january, kind=TrackKind.UNKNOWN)

    before = _year(store).totals.distance_m
    store.set_override(unknown_id, TrackKind.RECORDED, NOW)
    after = _year(store).totals.distance_m

    assert before == pytest.approx(10_000, rel=0.01)
    assert after == pytest.approx(60_000, rel=0.01)


def test_withdrawing_an_override_takes_the_track_back_out(store: SqliteTrackStore) -> None:
    """The correction is the authority, and withdrawing it hands authority back."""
    january = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    _add(store, key="recorded", metres=10_000, start=january)
    unknown_id = _add(store, key="unknown", metres=50_000, start=january, kind=TrackKind.UNKNOWN)
    store.set_override(unknown_id, TrackKind.RECORDED, NOW)

    store.clear_override(unknown_id)

    assert _year(store).totals.distance_m == pytest.approx(10_000, rel=0.01)


def test_an_override_does_not_make_the_analysis_stale(store: SqliteTrackStore) -> None:
    """Aggregation reads the effective kind live, so nothing needs re-deriving."""
    january = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    track_id = _add(store, key="unknown", metres=50_000, start=january, kind=TrackKind.UNKNOWN)
    store.set_override(track_id, TrackKind.RECORDED, NOW)

    analyze = AnalyzeTrack(repository=store, clock=_FixedClock(), analysis=InstalledAnalysis())

    assert analyze.outdated_tracks() == ()


# --- Buckets ----------------------------------------------------------------


def test_a_year_holds_twelve_months_even_when_most_are_empty(store: SqliteTrackStore) -> None:
    """Twelve buckets always, so a caller never has to invent the missing ones."""
    _add(store, key="one", metres=5_000, start=datetime(2026, 3, 4, 9, 0, tzinfo=UTC))

    monthly = GetMonthlyStatistics(repository=store, timezone="UTC", analysis=InstalledAnalysis())(
        2026, scope=AggregationScope.RECORDED
    )

    assert [bucket.month for bucket in monthly.months] == list(range(1, 13))
    assert monthly.months[2].totals.track_count == 1
    assert monthly.months[0].totals.track_count == 0
    assert monthly.months[0].totals.distance_m == pytest.approx(0.0)


def test_the_monthly_totals_add_up_to_the_year(store: SqliteTrackStore) -> None:
    """The consistency invariant a dashboard will silently rely on."""
    _add(store, key="jan", metres=10_000, start=datetime(2026, 1, 10, 9, 0, tzinfo=UTC))
    _add(store, key="jan2", metres=4_000, start=datetime(2026, 1, 20, 9, 0, tzinfo=UTC))
    _add(store, key="feb", metres=7_000, start=datetime(2026, 2, 2, 9, 0, tzinfo=UTC))
    _add(store, key="nov", metres=3_000, start=datetime(2026, 11, 30, 9, 0, tzinfo=UTC))

    year = _year(store)
    monthly = GetMonthlyStatistics(repository=store, timezone="UTC", analysis=InstalledAnalysis())(
        2026, scope=AggregationScope.RECORDED
    )

    assert sum(bucket.totals.distance_m or 0.0 for bucket in monthly.months) == pytest.approx(
        year.totals.distance_m
    )
    assert sum(bucket.totals.track_count for bucket in monthly.months) == (year.totals.track_count)


def test_a_track_belongs_to_the_year_it_happened_in(store: SqliteTrackStore) -> None:
    """A 2025 recording imported in 2026 is a 2025 activity.

    The import instant is when the archive learned about it, which is not a fact
    about the activity at all.
    """
    _add(store, key="old", metres=8_000, start=datetime(2025, 6, 1, 9, 0, tzinfo=UTC))

    assert _year(store, 2025).totals.track_count == 1
    assert _year(store, 2026).totals.track_count == 0


def test_a_recorded_track_without_a_date_is_reported_apart(store: SqliteTrackStore) -> None:
    """It has a length, and it belongs to no month. Both are true at once.

    Assigning it to the import date, or to 1970, would put a real distance into
    a period it has nothing to do with.
    """
    _add(store, key="dated", metres=10_000, start=datetime(2026, 5, 1, 9, 0, tzinfo=UTC))
    _add(store, key="undated", metres=25_000, start=None)

    year = _year(store)

    assert year.totals.track_count == 1
    assert year.unplaced.without_date.track_count == 1
    assert year.unplaced.without_date.distance_m == pytest.approx(25_000, rel=0.01)


# --- Timezone ---------------------------------------------------------------


def test_the_month_follows_the_configured_timezone(store: SqliteTrackStore) -> None:
    """A late evening on the last of the month is local, not universal.

    23:30 UTC on 31 January is 00:30 on 1 February in Berlin, so the same track
    is a January activity in one configuration and a February one in the other.
    """
    _add(store, key="boundary", metres=6_000, start=datetime(2026, 1, 31, 23, 30, tzinfo=UTC))

    universal = GetMonthlyStatistics(
        repository=store, timezone="UTC", analysis=InstalledAnalysis()
    )(2026, scope=AggregationScope.RECORDED)
    local = GetMonthlyStatistics(repository=store, timezone=BERLIN, analysis=InstalledAnalysis())(
        2026, scope=AggregationScope.RECORDED
    )

    assert universal.months[0].totals.track_count == 1
    assert local.months[1].totals.track_count == 1


def test_a_new_year_boundary_follows_the_configured_timezone(store: SqliteTrackStore) -> None:
    """The same rule decides a year, which is what makes a yearly total local too."""
    _add(store, key="newyear", metres=6_000, start=datetime(2025, 12, 31, 23, 30, tzinfo=UTC))

    assert _year(store, 2025).totals.track_count == 1
    assert _year(store, 2026, timezone=BERLIN).totals.track_count == 1


def test_the_spring_transition_does_not_lose_a_track(store: SqliteTrackStore) -> None:
    """The night Berlin skips an hour is still an ordinary night for a bucket."""
    _add(store, key="spring", metres=6_000, start=datetime(2026, 3, 29, 1, 30, tzinfo=UTC))

    monthly = GetMonthlyStatistics(repository=store, timezone=BERLIN, analysis=InstalledAnalysis())(
        2026, scope=AggregationScope.RECORDED
    )

    assert monthly.months[2].totals.track_count == 1


def test_the_autumn_transition_does_not_duplicate_a_track(store: SqliteTrackStore) -> None:
    """The night Berlin repeats an hour must count a track once, not twice."""
    _add(store, key="autumn", metres=6_000, start=datetime(2026, 10, 25, 0, 30, tzinfo=UTC))

    monthly = GetMonthlyStatistics(repository=store, timezone=BERLIN, analysis=InstalledAnalysis())(
        2026, scope=AggregationScope.RECORDED
    )
    year = _year(store, 2026, timezone=BERLIN)

    assert sum(bucket.totals.track_count for bucket in monthly.months) == 1
    assert year.totals.track_count == 1


def test_the_response_states_the_timezone_it_used(store: SqliteTrackStore) -> None:
    """Otherwise a monthly number is unexplainable to whoever reads it."""
    assert _year(store, timezone=BERLIN).timezone == BERLIN


# --- Activity filtering -----------------------------------------------------


def test_totals_can_be_narrowed_to_one_activity(store: SqliteTrackStore) -> None:
    """Walking kilometres and cycling kilometres are not the same statistic."""
    january = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    _add(store, key="walk", metres=10_000, start=january, activity=Activity.WALKING)
    _add(store, key="ride", metres=40_000, start=january, activity=Activity.CYCLING)

    walking = _year(store, activity=Activity.WALKING)
    everything = _year(store)

    assert walking.totals.distance_m == pytest.approx(10_000, rel=0.01)
    assert everything.totals.distance_m == pytest.approx(50_000, rel=0.01)


# --- Missing is not zero ----------------------------------------------------


def test_a_track_without_analysis_is_counted_as_missing_rather_than_as_zero(
    store: SqliteTrackStore,
) -> None:
    """An unanalysed track must not silently reduce a total to look complete."""
    january = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
    _add(store, key="analysed", metres=10_000, start=january)
    unanalysed = _add(store, key="pending", metres=10_000, start=january)
    with store.connection() as connection:
        connection.execute(
            "UPDATE tracks SET current_analysis_run_id = NULL WHERE id = ?", (unanalysed,)
        )

    year = _year(store)

    assert year.totals.track_count == 2
    assert year.totals.tracks_without_analysis == 1
    assert year.totals.distance_m == pytest.approx(10_000, rel=0.01)


def test_planned_routes_report_no_actual_moving_time(store: SqliteTrackStore) -> None:
    """A planned route was never travelled, so it has no moving duration.

    Reporting zero would claim it was travelled and nobody moved.
    """
    _add(
        store,
        key="planned",
        metres=100_000,
        start=None,
        kind=TrackKind.PLANNED,
    )

    planned = _year(store, scope=AggregationScope.PLANNED)

    assert planned.unplaced.without_date.distance_m == pytest.approx(100_000, rel=0.01)
    assert planned.unplaced.without_date.moving_duration_s is None
