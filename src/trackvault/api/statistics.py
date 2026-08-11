"""HTTP projection of the aggregated statistics.

Routes validate, call a query and shape the result. Nothing here sums a metric,
decides a track kind or draws a month boundary -- doing any of that in a route
would be a second answer to a question that already has an authority, and it
would be the answer nobody updates when the rule changes.

Two things every response states out loud:

**Units live in the field names.** ``distance_m``, ``moving_duration_s``. A bare
``22.4`` means metres, kilometres or minutes depending on who reads it, and the
one thing an API must not do is leave that to the reader.

**The timezone is named.** A month is a local month, so a monthly figure whose
zone is unstated cannot be checked by the person looking at it.

Actual and planned are asked for separately, through ``scope``. There is no
combined default: adding routes somebody planned to distances somebody travelled
produces a number that is about neither.
"""

from typing import Annotated, cast

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from trackvault.application.calendar import MAX_QUERY_YEAR, MIN_QUERY_YEAR
from trackvault.application.statistics import (
    AggregationScope,
    GetAvailableYears,
    GetMonthlyStatistics,
    GetOverallStatistics,
    GetYearStatistics,
    MonthlyStatistics,
    OverallStatistics,
    PeriodTotals,
    YearStatistics,
)
from trackvault.domain import Activity

router = APIRouter(prefix="/api/v1/statistics", tags=["statistics"])

# The supported range comes from the one calendar authority. A route that
# validated a year the use case behind it could not then process would be a
# crash with a green validator in front of it, which is exactly what two copies
# of this constant produced.
Year = Annotated[
    int,
    Path(ge=MIN_QUERY_YEAR, le=MAX_QUERY_YEAR, description="Year in the configured zone"),
]
Scope = Annotated[
    AggregationScope,
    Query(description="Which set of tracks to total: recorded, planned or unknown"),
]
ActivityFilter = Annotated[Activity | None, Query(description="Narrow the totals to one activity")]


class TotalsResponse(BaseModel):
    """What one period adds up to, and how much of it the total covers.

    A total is ``null`` when the period holds tracks but none of them carried a
    usable value for that metric. An empty period totals ``0`` instead, and
    ``track_count`` says which of the two it is.

    Only tracks whose analysis is current contribute. ``analysed_track_count``
    beside ``track_count`` is what makes a partial total legible as one: eight
    of ten is a different statement from ten, and the three counters underneath
    say why the other two are missing and what to do about them.
    """

    track_count: int
    analysed_track_count: int = Field(
        description="Tracks that actually contributed, having a current analysis"
    )
    tracks_without_analysis: int = Field(description="Tracks whose metrics have never been derived")
    tracks_with_outdated_analysis: int = Field(
        description="Tracks analysed by algorithms or from geometry that have since moved on"
    )
    tracks_with_invalid_analysis: int = Field(
        description="Tracks holding a stored analysis the archive cannot interpret"
    )
    tracks_with_failed_analysis: int = Field(
        description="Tracks whose newest analysis attempt failed, whatever it left behind"
    )
    tracks_without_observed_timing: int = Field(
        description="Contributing tracks whose instants were not shown to be measured"
    )
    distance_m: float | None
    elapsed_duration_s: float | None = Field(
        description="Summed over tracks with observed instants only"
    )
    moving_duration_s: float | None = Field(
        description="Summed over tracks with observed instants only"
    )
    elevation_gain_m: float | None


class UnplacedResponse(BaseModel):
    """What a scope holds that belongs to no calendar period.

    Reported beside a year rather than inside it, and split by *why* a track has
    no period. A route with no instants and a route whose instants nothing
    vouches for are different facts about the archive, and only the second one
    looks like a date until somebody checks.
    """

    without_date: TotalsResponse = Field(description="Tracks carrying no instants at all")
    with_unverified_date: TotalsResponse = Field(
        description="Tracks whose instants were not shown to have been measured"
    )


class ActivityTotalsResponse(BaseModel):
    """What one activity did inside one period."""

    activity: Activity
    totals: TotalsResponse


class MonthResponse(BaseModel):
    """One month of a year. Present even when nothing happened in it."""

    month: int
    totals: TotalsResponse = Field(description="The authority for this month")
    by_activity: list[ActivityTotalsResponse] = Field(
        description="The same tracks grouped by activity, in the taxonomy's order. "
        "A partition: the parts add up to `totals`. An activity with no track in "
        "this month is absent rather than reported as zero."
    )


class YearResponse(BaseModel):
    """One year's totals, in one scope."""

    year: int
    scope: AggregationScope
    activity: Activity | None
    timezone: str = Field(description="IANA zone the year's boundaries were drawn in")
    totals: TotalsResponse = Field(
        description="Summed over the tracks whose activity date the archive can vouch for"
    )
    unplaced: UnplacedResponse = Field(
        description="Tracks in scope that belong to no calendar period, and why"
    )


class MonthlyResponse(BaseModel):
    """A year broken into its twelve months."""

    year: int
    scope: AggregationScope
    activity: Activity | None
    timezone: str
    months: list[MonthResponse]
    activities: list[Activity] = Field(
        description="The activities this year holds, in the taxonomy's order -- not the "
        "whole vocabulary, and never ordered by size. A client may rely on an "
        "activity keeping its place, and therefore its colour, across requests."
    )


class YearBucketResponse(BaseModel):
    """One year of the whole archive."""

    year: int
    totals: TotalsResponse
    by_activity: list[ActivityTotalsResponse] = Field(
        description="The same partition a month reports, one level up"
    )


class OverallResponse(BaseModel):
    """Everything one scope holds, and each year inside it.

    A year is reported as twelve months because a calendar has twelve. This is
    reported as the years the archive has something for: padding out to the
    supported calendar would draw a century of empty buckets to say nothing.
    """

    scope: AggregationScope
    activity: Activity | None
    timezone: str
    totals: TotalsResponse = Field(
        description="What the whole scope adds up to. The years add up to exactly this."
    )
    unplaced: UnplacedResponse = Field(
        description="Tracks in scope that belong to no year -- beside the total, never inside"
    )
    years: list[YearBucketResponse] = Field(
        description="One bucket per year the archive holds something for, oldest first"
    )
    activities: list[Activity] = Field(
        description="The activities this scope holds, in the taxonomy's order"
    )


class UnplacedCountsResponse(BaseModel):
    """How many tracks belong to no calendar period, split by why.

    The two halves are different facts and the second is the one that looks
    answered: no instants at all, against instants nothing showed to be
    measured.
    """

    without_date: int
    with_unverified_date: int


class AvailableYearsResponse(BaseModel):
    """Which years this archive has something to show for.

    Deliberately not a range: an interface that offered "this year and the
    eleven before it" opened an archive of 2019 recordings on an empty 2026,
    and it computed a calendar the archive already owns.
    """

    scope: AggregationScope
    activity: Activity | None
    timezone: str
    years: list[int] = Field(
        description="Local years the scope holds tracks in, newest first",
    )
    unplaced: UnplacedCountsResponse = Field(
        description="Tracks in scope that belong to no year -- reported beside it, never inside"
    )
    archive_track_count: int = Field(
        description="Tracks held in any scope: what tells an empty archive from an empty selection"
    )


def _years_query(request: Request) -> GetAvailableYears:
    """Return the available-years query the composition root wired into the app."""
    return cast(GetAvailableYears, request.app.state.available_years)


def _year_query(request: Request) -> GetYearStatistics:
    """Return the year query the composition root wired into the app."""
    return cast(GetYearStatistics, request.app.state.year_statistics)


def _monthly_query(request: Request) -> GetMonthlyStatistics:
    """Return the monthly query the composition root wired into the app."""
    return cast(GetMonthlyStatistics, request.app.state.monthly_statistics)


def _overall_query(request: Request) -> GetOverallStatistics:
    """Return the whole-archive query the composition root wired into the app."""
    return cast(GetOverallStatistics, request.app.state.overall_statistics)


def _totals(totals: PeriodTotals) -> TotalsResponse:
    """Shape one period's totals for HTTP.

    Values are passed through at the precision they were summed at. Rounding is
    a presentation decision and belongs to whatever renders them, not to the
    boundary that would make the rounding permanent.
    """
    return TotalsResponse(
        track_count=totals.track_count,
        analysed_track_count=totals.analysed_track_count,
        tracks_without_analysis=totals.tracks_without_analysis,
        tracks_with_outdated_analysis=totals.tracks_with_outdated_analysis,
        tracks_with_invalid_analysis=totals.tracks_with_invalid_analysis,
        tracks_with_failed_analysis=totals.tracks_with_failed_analysis,
        tracks_without_observed_timing=totals.tracks_without_observed_timing,
        distance_m=totals.distance_m,
        elapsed_duration_s=totals.elapsed_duration_s,
        moving_duration_s=totals.moving_duration_s,
        elevation_gain_m=totals.elevation_gain_m,
    )


def _project_year(statistics: YearStatistics) -> YearResponse:
    """Shape one year for HTTP."""
    return YearResponse(
        year=statistics.year,
        scope=statistics.scope,
        activity=statistics.activity,
        timezone=statistics.timezone,
        totals=_totals(statistics.totals),
        unplaced=UnplacedResponse(
            without_date=_totals(statistics.unplaced.without_date),
            with_unverified_date=_totals(statistics.unplaced.with_unverified_date),
        ),
    )


def _project_monthly(statistics: MonthlyStatistics) -> MonthlyResponse:
    """Shape twelve months for HTTP."""
    return MonthlyResponse(
        year=statistics.year,
        scope=statistics.scope,
        activity=statistics.activity,
        timezone=statistics.timezone,
        months=[
            MonthResponse(
                month=bucket.month,
                totals=_totals(bucket.totals),
                by_activity=[
                    ActivityTotalsResponse(activity=entry.activity, totals=_totals(entry.totals))
                    for entry in bucket.by_activity
                ],
            )
            for bucket in statistics.months
        ],
        activities=list(statistics.activities),
    )


def _project_overall(statistics: OverallStatistics) -> OverallResponse:
    """Shape the whole archive for HTTP."""
    return OverallResponse(
        scope=statistics.scope,
        activity=statistics.activity,
        timezone=statistics.timezone,
        totals=_totals(statistics.totals),
        unplaced=UnplacedResponse(
            without_date=_totals(statistics.unplaced.without_date),
            with_unverified_date=_totals(statistics.unplaced.with_unverified_date),
        ),
        years=[
            YearBucketResponse(
                year=bucket.year,
                totals=_totals(bucket.totals),
                by_activity=[
                    ActivityTotalsResponse(activity=entry.activity, totals=_totals(entry.totals))
                    for entry in bucket.by_activity
                ],
            )
            for bucket in statistics.years
        ],
        activities=list(statistics.activities),
    )


@router.get("/overall", summary="Total every year at once")
def read_overall(
    request: Request,
    scope: Scope = AggregationScope.RECORDED,
    activity: ActivityFilter = None,
) -> OverallResponse:
    """Return what a whole scope adds up to, and what each of its years did.

    Registered before ``/year/{year}`` for the same reason ``/years`` is: a
    literal path must win over a parameterised one whatever the router's
    matching order turns out to be.
    """
    return _project_overall(_overall_query(request)(scope=scope, activity=activity))


@router.get("/years", summary="List the years this archive holds tracks in")
def read_available_years(
    request: Request,
    scope: Scope = AggregationScope.RECORDED,
    activity: ActivityFilter = None,
) -> AvailableYearsResponse:
    """Return the years one scope holds, newest first.

    Registered before ``/year/{year}`` so that the literal path wins over the
    parameterised one whatever the router's matching order turns out to be.
    """
    available = _years_query(request)(scope=scope, activity=activity)
    return AvailableYearsResponse(
        scope=available.scope,
        activity=available.activity,
        timezone=available.timezone,
        years=list(available.years),
        unplaced=UnplacedCountsResponse(
            without_date=available.unplaced_without_date,
            with_unverified_date=available.unplaced_with_unverified_date,
        ),
        archive_track_count=available.archive_track_count,
    )


@router.get("/year/{year}", summary="Total one year")
def read_year(
    request: Request,
    year: Year,
    scope: Scope = AggregationScope.RECORDED,
    activity: ActivityFilter = None,
) -> YearResponse:
    """Return one year's totals.

    The default scope is ``recorded``, because the unqualified question is what
    actually happened rather than what everything adds up to.
    """
    return _project_year(_year_query(request)(year, scope=scope, activity=activity))


@router.get("/year/{year}/monthly", summary="Total each month of one year")
def read_monthly(
    request: Request,
    year: Year,
    scope: Scope = AggregationScope.RECORDED,
    activity: ActivityFilter = None,
) -> MonthlyResponse:
    """Return twelve monthly totals, including the months with no activity."""
    return _project_monthly(_monthly_query(request)(year, scope=scope, activity=activity))
