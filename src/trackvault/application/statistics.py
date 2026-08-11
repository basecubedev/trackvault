"""Year and month totals, built from per-track metrics.

Two rules shape every number here.

**Actual and planned are separate sets.** They are selected by the *effective*
track kind, so a user correction moves a track from one to the other with
nothing recalculated -- distance came from geometry the correction did not
touch. `UNKNOWN` is a third set and is never silently folded into either:

```
Recorded  10 km
Planned  100 km
Unknown   50 km

actual total = 10 km
```

**A month is a local month.** Which one a late evening falls into depends on the
configured zone -- 23:30 UTC on 31 January is already February in Berlin -- so
the boundaries are computed in that zone and the archive is queried in UTC. The
conversion happens here rather than in SQL because a zone's offset is not a
constant: doing the arithmetic with a fixed offset is exactly how a
daylight-saving transition moves a track into the wrong month.

Nothing here reads a position. A total is a sum over one row per track, which is
what keeps a yearly query the same cost whether the archive holds a thousand
tracks or a thousand tracks of a million points each.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from trackvault.application.analysis import InstalledAnalysis
from trackvault.application.calendar import MIN_QUERY_YEAR, MONTHS_IN_YEAR, period_window
from trackvault.application.ports import (
    AnalysisAvailability,
    DatedTrackRow,
    TrackAggregationRow,
    TrackRepository,
)
from trackvault.domain import Activity, TemporalEvidence, TrackKind
from trackvault.domain.analysis import MetricName


class AggregationScope(StrEnum):
    """Which set of tracks a total covers.

    There is deliberately no "everything" scope. A number that adds routes
    somebody planned to distances somebody travelled is not a statistic about
    either, and making it the default would make it the one people quote.

    Attributes:
        RECORDED: Tracks whose effective kind is ``RECORDED`` -- what actually
            happened.
        PLANNED: Tracks whose effective kind is ``PLANNED``.
        UNKNOWN: Tracks whose kind could not be decided. Their own category, so
            that a data-quality view is possible without them contaminating the
            other two.
    """

    RECORDED = "recorded"
    PLANNED = "planned"
    UNKNOWN = "unknown"

    @property
    def kind(self) -> TrackKind:
        """Return the effective track kind this scope selects."""
        return _SCOPE_KINDS[self]


_SCOPE_KINDS = {
    AggregationScope.RECORDED: TrackKind.RECORDED,
    AggregationScope.PLANNED: TrackKind.PLANNED,
    AggregationScope.UNKNOWN: TrackKind.UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class PeriodTotals:
    """What a set of tracks adds up to.

    A total is ``None`` when the period holds tracks but none of them carried
    that metric -- a planned route has no moving time, and reporting zero would
    claim it was travelled and nobody moved. An **empty** period is different:
    it totals zero because there was nothing to total, and ``track_count`` says
    so unambiguously. That distinction is what lets a caller render twelve
    months without inventing the empty ones.

    Attributes:
        track_count: Tracks in the period and scope, whatever state their
            analysis is in.
        analysed_track_count: How many of them actually contributed. Together
            with ``track_count`` this is what stops a partial total from reading
            as a complete one: eight of ten is a different statement from ten.
        tracks_without_analysis: No analysis has ever been published for them.
            Nothing to contribute yet, and nothing wrong either.
        tracks_with_outdated_analysis: Analysed, by algorithms or from geometry
            that have since moved on. Their numbers exist and are deliberately
            not added: a total that mixed two algorithm versions would be a
            measurement of neither. ``analyze --outdated`` is the fix.
        tracks_with_invalid_analysis: Holding a stored analysis the archive
            cannot interpret. Kept apart from ``tracks_without_analysis``
            because the two call for different reactions: one is a track waiting
            to be analysed, the other is damage. The first three counters plus
            ``analysed_track_count`` add up to ``track_count`` exactly -- they
            are the four availability states, and every track is in one.
        tracks_with_failed_analysis: Whose newest attempt failed. Counted
            separately because it overlaps the other two rather than replacing
            them -- a track can hold perfectly current metrics from an earlier
            run and a failure from the newest one.
        tracks_without_observed_timing: Whose instants nothing showed to be
            measured, so they contributed a distance but no time. Reported so
            that a moving total smaller than the track count explains itself.
        distance_m: Total distance in metres.
        elapsed_duration_s: Total elapsed duration in seconds.
        moving_duration_s: Total moving duration in seconds.
        elevation_gain_m: Total ascent in metres.
    """

    track_count: int
    analysed_track_count: int
    tracks_without_analysis: int
    tracks_with_outdated_analysis: int
    tracks_with_invalid_analysis: int
    tracks_with_failed_analysis: int
    tracks_without_observed_timing: int
    distance_m: float | None
    elapsed_duration_s: float | None
    moving_duration_s: float | None
    elevation_gain_m: float | None


@dataclass(frozen=True, slots=True)
class UnplacedTotals:
    """What a scope holds that belongs to no calendar period.

    Split rather than pooled, because the two halves call for different
    reactions and only one of them is a data-quality problem:

    Attributes:
        without_date: Tracks carrying no temporal observation at all. A planned
            route usually. It has a length and no date, and both are true at
            once.
        with_unverified_date: Tracks that carry instants nothing showed to be
            measured. They *look* dated -- a file says October -- and that claim
            is exactly what cannot be checked, so they are kept out of the
            period rather than quietly summed into it.
    """

    without_date: "PeriodTotals"
    with_unverified_date: "PeriodTotals"


@dataclass(frozen=True, slots=True)
class ActivityTotals:
    """What one activity did inside one period.

    Attributes:
        activity: Which activity, from the domain's own flat taxonomy.
        totals: What its tracks add up to, under exactly the rules the period's
            own total follows -- only current analyses contribute, and a metric
            nothing could derive stays absent.
    """

    activity: Activity
    totals: PeriodTotals


@dataclass(frozen=True, slots=True)
class MonthTotals:
    """One month of a year, and what it holds.

    Attributes:
        month: The calendar month, 1-12.
        totals: What the month adds up to. The authority for the month.
        by_activity: The same tracks grouped a second way, in the taxonomy's
            order. A track has exactly one activity, so this is a partition:
            nothing is counted twice and nothing is left out, and the parts add
            up to ``totals``.

            An activity with no track in this month is **absent** rather than
            reported as zero. It did not do nothing here; it was not here.
    """

    month: int
    totals: PeriodTotals
    by_activity: tuple[ActivityTotals, ...] = ()


@dataclass(frozen=True, slots=True)
class YearStatistics:
    """One year's totals, in one scope.

    Attributes:
        year: The year, in the aggregation timezone.
        scope: Which set of tracks the totals cover.
        activity: The activity the totals were narrowed to, if any.
        timezone: The zone the year's boundaries were drawn in. Reported so that
            a number is explainable to whoever reads it.
        totals: What the year adds up to -- over the tracks whose activity date
            the archive can vouch for.
        unplaced: Tracks in scope that belong to no year at all, reported beside
            it rather than inside it, and split by *why* they have no period.
    """

    year: int
    scope: AggregationScope
    activity: Activity | None
    timezone: str
    totals: PeriodTotals
    unplaced: UnplacedTotals


@dataclass(frozen=True, slots=True)
class MonthlyStatistics:
    """A year broken into its twelve months.

    Attributes:
        year: The year, in the aggregation timezone.
        scope: Which set of tracks the totals cover.
        activity: The activity the totals were narrowed to, if any.
        timezone: The zone the month boundaries were drawn in.
        months: Twelve buckets, always, in calendar order. A month with no
            activity is present and empty.
        activities: The activities this year actually holds, in the taxonomy's
            own order. Not the whole vocabulary: naming eight for an archive
            that holds two is a claim about the archive. Not by size either --
            an order that depended on the totals would rearrange a chart, and
            repaint it, whenever somebody imported a file.
    """

    year: int
    scope: AggregationScope
    activity: Activity | None
    timezone: str
    months: tuple[MonthTotals, ...]
    activities: tuple[Activity, ...] = ()


@dataclass(frozen=True, slots=True)
class YearTotals:
    """One year of the whole archive, and what it holds.

    Attributes:
        year: The year, in the aggregation timezone.
        totals: What it adds up to.
        by_activity: The same tracks grouped by activity, exactly as a month
            reports it -- one implementation, so the two levels cannot disagree
            about what the grouping means.
    """

    year: int
    totals: PeriodTotals
    by_activity: tuple[ActivityTotals, ...] = ()


@dataclass(frozen=True, slots=True)
class OverallStatistics:
    """Everything one scope holds, and each year inside it.

    A year is a period; "everything" is not one, and the shape says so. A year
    is reported as twelve months because a calendar has twelve and an empty one
    is a fact about that year. Everything is reported as the years the archive
    actually has something for: padding the axis out to the supported calendar
    would draw a century of empty bars to say nothing.

    Attributes:
        scope: Which set of tracks the totals cover.
        activity: The activity the totals were narrowed to, if any.
        timezone: The zone every boundary here was drawn in.
        totals: What the whole scope adds up to, over the tracks whose activity
            date the archive can vouch for. The years add up to exactly this.
        unplaced: Tracks in scope that belong to no year at all. Beside the
            total rather than inside it -- widening the period does not make a
            clock nobody measured into a date.
        years: One bucket per year the archive holds something for, **oldest
            first**, because that is the order a time axis reads in.
        activities: The activities this scope holds, in the taxonomy's order.
    """

    scope: AggregationScope
    activity: Activity | None
    timezone: str
    totals: PeriodTotals
    unplaced: UnplacedTotals
    years: tuple[YearTotals, ...]
    activities: tuple[Activity, ...]


class _Aggregator:
    """Shared machinery for the year and month queries.

    Both answer the same question over the same rows and differ only in how they
    group them, so the window arithmetic and the summation live once. Two copies
    would be two places for a month boundary to be drawn differently.
    """

    def __init__(
        self, repository: TrackRepository, timezone: str, analysis: InstalledAnalysis
    ) -> None:
        """Wire the query to its repository, its aggregation zone and currency."""
        self._repository = repository
        self._timezone = timezone
        self._zone = ZoneInfo(timezone)
        self._analysis = analysis

    @property
    def timezone(self) -> str:
        """Return the zone boundaries are drawn in."""
        return self._timezone

    def rows_of(
        self, year: int, scope: AggregationScope, activity: Activity | None
    ) -> tuple[TrackAggregationRow, ...]:
        """Return the tracks of one local year that a scope and filter select.

        Only the ones the year can vouch for: a period selection is a claim
        about when something happened, and a clock nothing measured cannot
        support it.

        The window comes from the one calendar authority, so the year a total
        covers and the year a listing filters to are the same instants -- and
        the last supported year is a window rather than an arithmetic overflow.
        """
        start, end = period_window(year, None, self._zone)
        return _selected(self._repository.placed_aggregation_rows(start, end), scope, activity)

    def every_row(
        self, scope: AggregationScope, activity: Activity | None
    ) -> tuple[TrackAggregationRow, ...]:
        """Return every placed track a scope and filter select, in any year.

        The window is the supported calendar itself, drawn by the same authority
        one year's is. "Everything" is therefore the same question with a wider
        window rather than a second way of selecting rows, and a track it counts
        is a track some year's total counts too.
        """
        start, _ = period_window(MIN_QUERY_YEAR, None, self._zone)
        return _selected(self._repository.placed_aggregation_rows(start, None), scope, activity)

    def year_of(self, row: TrackAggregationRow) -> int:
        """Return the local year a track's activity started in."""
        started = row.started_at
        if started is None:  # pragma: no cover - dated rows always carry one
            raise ValueError("a dated aggregation row must carry a start instant")
        return started.astimezone(self._zone).year

    def unplaced(self, scope: AggregationScope, activity: Activity | None) -> UnplacedTotals:
        """Return what a scope holds that belongs to no period, split by why.

        The split is read from whether a row carries an instant at all. Both
        halves came back from one query, because "has no calendar period" is one
        condition and asking it twice would be two chances to disagree.
        """
        rows = _selected(self._repository.unplaced_aggregation_rows(), scope, activity)
        return UnplacedTotals(
            without_date=_totals([row for row in rows if row.started_at is None], self._analysis),
            with_unverified_date=_totals(
                [row for row in rows if row.started_at is not None], self._analysis
            ),
        )

    def month_of(self, row: TrackAggregationRow) -> int:
        """Return the local month a track's activity started in."""
        started = row.started_at
        if started is None:  # pragma: no cover - dated rows always carry one
            raise ValueError("a dated aggregation row must carry a start instant")
        return started.astimezone(self._zone).month


@dataclass(frozen=True, slots=True)
class AvailableYears:
    """Which periods an archive has something to show for, and what it misses.

    Attributes:
        scope: Which set of tracks the years were read from.
        activity: The activity the selection was narrowed to, if any.
        timezone: The zone the year boundaries were drawn in -- the same one
            every total and the listing's period filter use, so a year offered
            here is a year those two agree exists.
        years: The local years the scope holds tracks in, **newest first**. A
            caller picking a default picks the first element, and the useful
            default is the most recent year there is something to show for
            rather than whatever year the reader's own clock says.
        unplaced_without_date: Tracks in scope carrying no instants at all.
        unplaced_with_unverified_date: Tracks in scope carrying instants nothing
            showed to be measured. Kept apart from the previous count for the
            same reason the yearly totals keep them apart: one has no date and
            the other has one nobody can check, and only the second one looks
            like a date until somebody looks.
        archive_track_count: Tracks the archive holds in *any* scope. The one
            number that tells "nothing has been imported yet" apart from
            "nothing matches this selection", which are opposite instructions
            to whoever is reading.
    """

    scope: AggregationScope
    activity: Activity | None
    timezone: str
    years: tuple[int, ...]
    unplaced_without_date: int
    unplaced_with_unverified_date: int
    archive_track_count: int

    @property
    def unplaced_track_count(self) -> int:
        """Return how many tracks in scope belong to no calendar period."""
        return self.unplaced_without_date + self.unplaced_with_unverified_date


class GetAvailableYears:
    """Answers which calendar years an archive actually holds tracks in.

    The alternative -- a browser generating "this year and the eleven before
    it" -- is a second authority on the calendar, and it is wrong in both
    directions at once: it offers years the archive has nothing for, and it
    hides the years of an archive nobody has added to since 2019.
    """

    def __init__(self, *, repository: TrackRepository, timezone: str) -> None:
        """Wire the query to its repository and its aggregation zone."""
        self._repository = repository
        self._timezone = timezone
        self._zone = ZoneInfo(timezone)

    def __call__(
        self,
        *,
        scope: AggregationScope = AggregationScope.RECORDED,
        activity: Activity | None = None,
    ) -> AvailableYears:
        """Return the years one scope holds, newest first.

        Bucketing happens here rather than in SQL for the same reason every
        other period decision does: a zone's offset is not a constant, and
        23:30 UTC on 31 December is already the next year in Berlin.
        """
        rows = [
            row
            for row in self._repository.dated_track_rows()
            if row.effective_kind is scope.kind and (activity is None or row.activity is activity)
        ]
        unplaced = _selected(self._repository.unplaced_aggregation_rows(), scope, activity)
        return AvailableYears(
            scope=scope,
            activity=activity,
            timezone=self._timezone,
            years=tuple(sorted({self._year_of(row) for row in rows}, reverse=True)),
            unplaced_without_date=sum(1 for row in unplaced if row.started_at is None),
            unplaced_with_unverified_date=sum(1 for row in unplaced if row.started_at is not None),
            archive_track_count=self._repository.current_track_count(),
        )

    def _year_of(self, row: DatedTrackRow) -> int:
        """Return the local year a track's activity started in."""
        return row.started_at.astimezone(self._zone).year


class GetYearStatistics:
    """Answers what one year adds up to, in one scope."""

    def __init__(
        self, *, repository: TrackRepository, timezone: str, analysis: InstalledAnalysis
    ) -> None:
        """Wire the query to its repository, its zone and the currency authority.

        The same ``InstalledAnalysis`` that ``analyze --outdated`` selects with,
        so what a total leaves out and what a batch run picks up are one answer
        rather than two that drift.
        """
        self._aggregator = _Aggregator(repository, timezone, analysis)
        self._analysis = analysis

    def __call__(
        self,
        year: int,
        *,
        scope: AggregationScope = AggregationScope.RECORDED,
        activity: Activity | None = None,
    ) -> YearStatistics:
        """Return one year's totals.

        Args:
            year: The year, read in the configured aggregation timezone.
            scope: Which set of tracks to total. Recorded by default, because
                "what did I actually do" is the question being asked.
            activity: Narrow the totals to one activity, or ``None`` for all.
        """
        return YearStatistics(
            year=year,
            scope=scope,
            activity=activity,
            timezone=self._aggregator.timezone,
            totals=_totals(self._aggregator.rows_of(year, scope, activity), self._analysis),
            unplaced=self._aggregator.unplaced(scope, activity),
        )


class GetOverallStatistics:
    """Answers what a whole archive adds up to, and what each of its years did.

    The one query that is not about a period. Everything else here answers "what
    happened in this window"; this answers "what is there", which is the
    question somebody with five years of tracks actually starts from.

    It is still a scope question. There is no total that adds planned routes to
    travelled distance, and widening the period does not create one.
    """

    def __init__(
        self, *, repository: TrackRepository, timezone: str, analysis: InstalledAnalysis
    ) -> None:
        """Wire the query to its repository, its zone and the currency authority."""
        self._aggregator = _Aggregator(repository, timezone, analysis)
        self._analysis = analysis

    def __call__(
        self,
        *,
        scope: AggregationScope = AggregationScope.RECORDED,
        activity: Activity | None = None,
    ) -> OverallStatistics:
        """Return the whole scope's totals and one bucket per year it holds."""
        rows = self._aggregator.every_row(scope, activity)
        buckets: dict[int, list[TrackAggregationRow]] = {}
        for row in rows:
            buckets.setdefault(self._aggregator.year_of(row), []).append(row)

        return OverallStatistics(
            scope=scope,
            activity=activity,
            timezone=self._aggregator.timezone,
            totals=_totals(rows, self._analysis),
            unplaced=self._aggregator.unplaced(scope, activity),
            years=tuple(
                YearTotals(
                    year=year,
                    totals=_totals(buckets[year], self._analysis),
                    by_activity=_by_activity(buckets[year], self._analysis),
                )
                for year in sorted(buckets)
            ),
            activities=_present_activities(rows),
        )


class GetMonthlyStatistics:
    """Answers what each month of one year adds up to, in one scope."""

    def __init__(
        self, *, repository: TrackRepository, timezone: str, analysis: InstalledAnalysis
    ) -> None:
        """Wire the query to its repository, its zone and the currency authority."""
        self._aggregator = _Aggregator(repository, timezone, analysis)
        self._analysis = analysis

    def __call__(
        self,
        year: int,
        *,
        scope: AggregationScope = AggregationScope.RECORDED,
        activity: Activity | None = None,
    ) -> MonthlyStatistics:
        """Return twelve monthly totals, including the empty months.

        Every month is present whether or not anything happened in it. A caller
        that had to fill the gaps would be a second place deciding what an empty
        month means, and it would decide it differently.
        """
        buckets: dict[int, list[TrackAggregationRow]] = {
            month: [] for month in range(1, MONTHS_IN_YEAR + 1)
        }
        for row in self._aggregator.rows_of(year, scope, activity):
            buckets[self._aggregator.month_of(row)].append(row)

        return MonthlyStatistics(
            year=year,
            scope=scope,
            activity=activity,
            timezone=self._aggregator.timezone,
            months=tuple(
                MonthTotals(
                    month=month,
                    totals=_totals(rows, self._analysis),
                    by_activity=_by_activity(rows, self._analysis),
                )
                for month, rows in buckets.items()
            ),
            activities=_present_activities(row for rows in buckets.values() for row in rows),
        )


def _by_activity(
    rows: Sequence[TrackAggregationRow], analysis: InstalledAnalysis
) -> tuple[ActivityTotals, ...]:
    """Group one period's rows by activity, in the taxonomy's order.

    The same summation the period's own total uses, over subsets of the same
    rows. A second rule here -- a different currency check, a different
    treatment of an absent metric -- would make the parts and the whole two
    answers to one question. One implementation, so a month and a year cannot
    come to disagree about what "grouped by activity" means either.
    """
    grouped: dict[Activity, list[TrackAggregationRow]] = {}
    for row in rows:
        grouped.setdefault(row.activity, []).append(row)
    return tuple(
        ActivityTotals(activity=activity, totals=_totals(grouped[activity], analysis))
        for activity in _present_activities(rows)
    )


def _selected(
    rows: Iterable[TrackAggregationRow],
    scope: AggregationScope,
    activity: Activity | None,
) -> tuple[TrackAggregationRow, ...]:
    """Return the rows one scope and activity filter select.

    The kind is compared against the *effective* one the repository projected,
    which is what makes an override take effect at query time.
    """
    return tuple(
        row
        for row in rows
        if row.effective_kind is scope.kind and (activity is None or row.activity is activity)
    )


_CURRENT = AnalysisAvailability.CURRENT


def _present_activities(rows: Iterable[TrackAggregationRow]) -> tuple[Activity, ...]:
    """Return the activities a set of rows holds, in the taxonomy's own order.

    The order is `Activity`'s declaration order and never the data's. A chart
    drawn from this gives an activity the same place -- and so the same colour
    -- whatever the archive currently holds, which is what stops a filter or an
    import from repainting the series that survive it.
    """
    present = {row.activity for row in rows}
    return tuple(activity for activity in Activity if activity in present)


def _totals(rows: Sequence[TrackAggregationRow], analysis: InstalledAnalysis) -> PeriodTotals:
    """Sum one set of tracks into a period total.

    Only tracks whose analysis is *current* contribute a number. A stored result
    produced by algorithms this build no longer applies is not wrong, it is
    simply not comparable with one that is, and adding the two would produce a
    figure that measures neither. What the others are is counted instead, so the
    shortfall is visible rather than silent -- and counted through the same
    availability authority a listing row and a detail view project, so the three
    cannot reach three different words for one track.
    """
    states = [analysis.availability(row.analysis) for row in rows]
    current = [row for row, state in zip(rows, states, strict=True) if state is _CURRENT]
    timed = [row for row in current if row.temporal_evidence is TemporalEvidence.OBSERVED]
    return PeriodTotals(
        track_count=len(rows),
        analysed_track_count=len(current),
        tracks_without_analysis=states.count(AnalysisAvailability.MISSING),
        tracks_with_outdated_analysis=states.count(AnalysisAvailability.OUTDATED),
        tracks_with_invalid_analysis=states.count(AnalysisAvailability.INVALID),
        tracks_with_failed_analysis=sum(
            1
            for row in rows
            if row.analysis.latest_run is not None and not row.analysis.latest_run.succeeded
        ),
        tracks_without_observed_timing=len(current) - len(timed),
        distance_m=_sum(rows, current, MetricName.DISTANCE),
        elapsed_duration_s=_sum(rows, timed, MetricName.ELAPSED_DURATION),
        moving_duration_s=_sum(rows, timed, MetricName.MOVING_DURATION),
        elevation_gain_m=_sum(rows, current, MetricName.ELEVATION_GAIN),
    )


def _sum(
    rows: Sequence[TrackAggregationRow],
    contributing: Sequence[TrackAggregationRow],
    name: MetricName,
) -> float | None:
    """Return the total of one metric, or ``None`` when nothing carried it.

    An **empty period** totals zero: there was nothing to add, and
    ``track_count`` already says so. A period that holds tracks but no usable
    value for this metric is a different statement, and zero would be the wrong
    one -- it would claim the tracks were measured and came out at nothing.

    ``contributing`` is the subset allowed to answer: the tracks whose analysis
    is current, narrowed further to those with observed instants where the
    metric needs a clock. ``rows`` is still consulted, because whether the
    period was empty is a question about all of it.

    The raw values are summed at full precision. Rounding is a presentation
    decision, and rounding before a sum is how a thousand small errors become
    one visible one.
    """
    if not rows:
        return 0.0
    values = [row.metrics[name].value for row in contributing if name in row.metrics]
    return sum(values) if values else None
