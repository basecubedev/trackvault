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

from gpx_view.application.calendar import MAX_QUERY_YEAR, MIN_QUERY_YEAR
from gpx_view.application.statistics import (
    AggregationScope,
    GetMonthlyStatistics,
    GetYearStatistics,
    MonthlyStatistics,
    PeriodTotals,
    YearStatistics,
)
from gpx_view.domain import Activity

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


class MonthResponse(BaseModel):
    """One month of a year. Present even when nothing happened in it."""

    month: int
    totals: TotalsResponse


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


def _year_query(request: Request) -> GetYearStatistics:
    """Return the year query the composition root wired into the app."""
    return cast(GetYearStatistics, request.app.state.year_statistics)


def _monthly_query(request: Request) -> GetMonthlyStatistics:
    """Return the monthly query the composition root wired into the app."""
    return cast(GetMonthlyStatistics, request.app.state.monthly_statistics)


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
            MonthResponse(month=bucket.month, totals=_totals(bucket.totals))
            for bucket in statistics.months
        ],
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
