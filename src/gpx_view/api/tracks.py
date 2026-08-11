"""HTTP projection of the stored tracks.

Routes validate, call a use case and shape the result. They hold no
classification and no analysis logic, and they never decide a track kind: the
override endpoint records what the user said and reads the effective kind back
from the classification, which stays the single authority.

Geometry lives behind its own endpoint. A long recording holds tens of thousands
of positions, so a listing must not pay for them, and a client can read
``point_count`` first and decide whether to ask.

**There is no authentication.** These endpoints are meant for a trusted network:
a self-hosted deployment reachable only from the owner's own machines or behind a
reverse proxy that authenticates. That is also why importing is not exposed here
-- see ``docs/technical/architecture.md``.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, Field

from gpx_view.application import ImportErrorCode
from gpx_view.application.analysis import GetTrackAnalysis, TrackAnalysisReport
from gpx_view.application.calendar import MAX_QUERY_YEAR, MIN_QUERY_YEAR, MONTHS_IN_YEAR
from gpx_view.application.ports import (
    MAX_PAGE_SIZE,
    AnalysisAvailability,
    TrackOrder,
    TrackSummary,
)
from gpx_view.application.track_queries import DEFAULT_PAGE_SIZE, TrackQueries
from gpx_view.domain import (
    Activity,
    TemporalEvidence,
    TrackKind,
    TrackSegment,
    supports_actual_calendar_placement,
    supports_actual_timing,
)
from gpx_view.domain.analysis import MetricName, MetricValue

router = APIRouter(prefix="/api/v1", tags=["tracks"])

TrackId = Annotated[int, Path(ge=1, description="Identity of a stored track")]

# The maximum is enforced by the query rather than clamped silently. A caller
# asking for ten million rows has misunderstood something, and answering with
# two hundred would hide that until they wrote a pager around it.
PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="How many tracks to return")]
PageOffset = Annotated[int, Query(ge=0, description="How many tracks to skip")]
KindFilter = Annotated[
    TrackKind | None, Query(description="Narrow to one effective kind, honouring a correction")
]
ActivityFilter = Annotated[Activity | None, Query(description="Narrow to one activity")]
YearFilter = Annotated[
    int | None,
    Query(ge=MIN_QUERY_YEAR, le=MAX_QUERY_YEAR, description="Year in the configured zone"),
]
MonthFilter = Annotated[
    int | None, Query(ge=1, le=MONTHS_IN_YEAR, description="Month of that year; needs a year")
]
AnalysisStatusFilter = Annotated[
    AnalysisAvailability | None,
    Query(description="Narrow to tracks whose stored metrics are in one availability state"),
]
SortOrder = Annotated[TrackOrder, Query(description="How to order the selection")]


class ErrorBody(BaseModel):
    """The stable shape of an error, without internals."""

    code: ImportErrorCode
    message: str


class ErrorResponse(BaseModel):
    """An error response. Never carries a stack trace, a path or a coordinate."""

    error: ErrorBody


class ClassificationResponse(BaseModel):
    """What was detected, what the user said, and what therefore applies."""

    effective_kind: TrackKind
    detected_kind: TrackKind
    confidence: float
    method: str
    method_version: str
    evidence: list[str]
    override: TrackKind | None
    is_overridden: bool


class SourceResponse(BaseModel):
    """Where a track came from. Evidence, never authority."""

    exchange_format: str
    format_version: str | None
    creator: str | None
    links: list[str]
    extension_namespaces: list[str]


class AnalysisSummaryResponse(BaseModel):
    """The headline metrics of one track, for a listing.

    Enough to render a row without a second request per track, and no more. The
    full metric set, the algorithms that produced it and its quality flags live
    behind the track's own analysis resource.

    ``status`` replaced an `analysed: bool`, which was the wrong question with a
    reassuring answer: it was true for a track whose numbers came from
    algorithms this build no longer runs and true for one whose stored analysis
    cannot be read at all. A metric here is present only while ``status`` is
    ``current``; in the other three states it is ``null``, because a headline
    number is a claim about what the track *is* rather than about what was once
    derived from it. The last thing that was derived is still readable -- from
    the track's own analysis resource, which says what it is.

    The two durations travel with the track's ``timeline``, which is where the
    statement that qualifies them lives: a duration derived from a planner's
    instants and one derived from a receiver's are the same number and mean
    different things, and a list view is exactly where that difference gets
    lost.
    """

    status: AnalysisAvailability = Field(
        description="current, outdated, missing or invalid -- the same word the detail view uses"
    )
    distance_m: float | None
    elevation_gain_m: float | None
    elapsed_duration_s: float | None
    moving_duration_s: float | None


class TimelineResponse(BaseModel):
    """When the track's own positions say it happened, and what that is worth.

    Two questions that look like one until a planner writes plausible instants
    onto a route nobody travelled:

    ```
    timeline time            the instants the positions carry
    activity calendar time   the claim that this happened then
    ```

    The instants are never withheld -- they are real data about the file -- but
    they never travel without ``basis`` either. ``is_actual_calendar_time`` is
    what a period total asks and depends on the basis alone;
    ``is_actual_activity_timing`` additionally needs the track to be a
    recording, because a route's duration is not somebody's afternoon whatever
    its clock was.
    """

    started_at: datetime | None
    ended_at: datetime | None
    basis: TemporalEvidence = Field(
        description="What the instants behind this timeline were shown to be"
    )
    is_actual_calendar_time: bool = Field(
        description="Whether this timeline may place the activity in a calendar period"
    )
    is_actual_activity_timing: bool = Field(
        description="Whether durations from it may be presented as time somebody spent"
    )


class TrackResponse(BaseModel):
    """One stored track without its geometry."""

    id: int
    raw_import_sha256: str
    source_index: int
    title: str | None
    activity: Activity
    classification: ClassificationResponse
    timeline: TimelineResponse
    analysis: AnalysisSummaryResponse
    point_count: int
    segment_count: int
    source: SourceResponse


class TrackListResponse(BaseModel):
    """One page of stored tracks.

    ``total`` counts what the filter selected, not what this page holds. Without
    it a client cannot tell a last page from a full one, and every pager built
    on the response would be guessing.
    """

    total: int = Field(description="Tracks the filter selected, across all pages")
    limit: int = Field(description="Page size actually applied, which may be smaller than asked")
    offset: int
    tracks: list[TrackResponse]


class PointResponse(BaseModel):
    """One normalized position."""

    latitude: float
    longitude: float
    elevation: float | None
    time: datetime | None


class SegmentResponse(BaseModel):
    """One uninterrupted run of positions."""

    points: list[PointResponse]


class GeometryResponse(BaseModel):
    """The geometry of one track, with its segment boundaries preserved."""

    track_id: int
    segment_count: int
    point_count: int
    segments: list[SegmentResponse]


class AnalysisProfileResponse(BaseModel):
    """The algorithms that produced a set of metrics.

    Reported so that "why did my elevation gain change?" is answerable from the
    data rather than from a changelog.
    """

    distance_algorithm: str
    distance_algorithm_version: int
    movement_algorithm: str
    movement_algorithm_version: int
    elevation_algorithm: str
    elevation_algorithm_version: int
    metric_schema_version: int


class GeometryMetricsResponse(BaseModel):
    """What the shape of the track supports, with no reference to a clock.

    These are worth the same whatever a track's instants turn out to be: a route
    somebody drew has a length and a profile exactly as a walk somebody took
    does. Each field carries its unit in its name, and a metric that could not be
    derived is ``null`` -- never ``0``.
    """

    distance_m: float | None
    elevation_min_m: float | None
    elevation_max_m: float | None
    elevation_gain_m: float | None
    elevation_loss_m: float | None


class TimedPathMetricsResponse(BaseModel):
    """What the track's instants support, and what those instants are worth.

    Deliberately not called "activity": these are properties of a path through
    time, and whether that path is anybody's afternoon is what ``basis`` and
    ``is_actual_activity_timing`` answer. The numbers are never withheld -- a
    planned route really does have a derived route speed -- but they are never
    handed over without the statement that qualifies them either.
    """

    basis: TemporalEvidence = Field(
        description="What the instants behind these numbers were shown to be"
    )
    is_actual_activity_timing: bool = Field(
        description="Whether these may be presented as time somebody actually spent"
    )
    elapsed_duration_s: float | None
    moving_duration_s: float | None
    stopped_duration_s: float | None
    unobserved_gap_duration_s: float | None
    unattributed_duration_s: float | None
    average_speed_mps: float | None
    moving_average_speed_mps: float | None
    maximum_sustained_speed_mps: float | None = Field(
        description="Highest speed held across the analysis window, not an instantaneous peak"
    )


class TrackAnalysisResponse(BaseModel):
    """The derived metrics of one track, and what produced them."""

    track_id: int
    status: AnalysisAvailability
    analyzed_at: datetime | None
    profile: AnalysisProfileResponse | None
    geometry: GeometryMetricsResponse
    timed_path: TimedPathMetricsResponse
    quality: list[str] = Field(
        description="What was wrong with the data the metrics were derived from"
    )
    error_code: str | None = Field(
        description="Why the newest analysis attempt failed, when it did"
    )


class ClassificationOverrideRequest(BaseModel):
    """An explicit user correction of a track's kind."""

    kind: TrackKind = Field(description="recorded, planned or unknown")


NOT_FOUND: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No such track"}
}


def _queries(request: Request) -> TrackQueries:
    """Return the query use cases the composition root wired into the app."""
    return cast(TrackQueries, request.app.state.track_queries)


def _analysis(request: Request) -> GetTrackAnalysis:
    """Return the analysis query the composition root wired into the app."""
    return cast(GetTrackAnalysis, request.app.state.track_analysis)


def _not_found(response: Response) -> ErrorResponse:
    """Project an absent track onto the stable error contract."""
    response.status_code = status.HTTP_404_NOT_FOUND
    return ErrorResponse(
        error=ErrorBody(code=ImportErrorCode.TRACK_NOT_FOUND, message="no such track")
    )


def _project(summary: TrackSummary) -> TrackResponse:
    """Shape one stored track for HTTP."""
    classification = summary.classification
    detected = classification.detected
    return TrackResponse(
        id=summary.track_id,
        raw_import_sha256=summary.raw_import_sha256,
        source_index=summary.source_index,
        title=summary.title,
        activity=summary.activity,
        classification=ClassificationResponse(
            effective_kind=classification.effective_kind,
            detected_kind=detected.kind,
            confidence=detected.confidence,
            method=detected.method,
            method_version=detected.method_version,
            evidence=list(detected.evidence),
            override=classification.override,
            is_overridden=classification.is_overridden,
        ),
        timeline=TimelineResponse(
            started_at=summary.started_at,
            ended_at=summary.ended_at,
            basis=summary.temporal_evidence,
            is_actual_calendar_time=supports_actual_calendar_placement(summary.temporal_evidence),
            is_actual_activity_timing=supports_actual_timing(
                summary.effective_kind, summary.temporal_evidence
            ),
        ),
        analysis=AnalysisSummaryResponse(
            status=summary.analysis,
            distance_m=_metric(summary.metrics, MetricName.DISTANCE),
            elevation_gain_m=_metric(summary.metrics, MetricName.ELEVATION_GAIN),
            elapsed_duration_s=_metric(summary.metrics, MetricName.ELAPSED_DURATION),
            moving_duration_s=_metric(summary.metrics, MetricName.MOVING_DURATION),
        ),
        point_count=summary.point_count,
        segment_count=summary.segment_count,
        source=SourceResponse(
            exchange_format=summary.source.exchange_format,
            format_version=summary.source.format_version,
            creator=summary.source.creator,
            links=list(summary.source.external_links),
            extension_namespaces=list(summary.source.extension_namespaces),
        ),
    )


def _project_geometry(track_id: int, segments: tuple[TrackSegment, ...]) -> GeometryResponse:
    """Shape the geometry of one track for HTTP."""
    return GeometryResponse(
        track_id=track_id,
        segment_count=len(segments),
        point_count=sum(segment.point_count for segment in segments),
        segments=[
            SegmentResponse(
                points=[
                    PointResponse(
                        latitude=point.latitude,
                        longitude=point.longitude,
                        elevation=point.elevation,
                        time=point.time,
                    )
                    for point in segment.points
                ]
            )
            for segment in segments
        ],
    )


def _metric(metrics: Mapping[MetricName, MetricValue], name: MetricName) -> float | None:
    """Return one metric's magnitude, or ``None`` when it was not derived.

    Absent stays absent. Substituting a zero here would turn "we could not tell"
    into "it was nothing", which is the one translation this API must not make.
    """
    metric = metrics.get(name)
    return None if metric is None else metric.value


def _project_analysis(report: TrackAnalysisReport) -> TrackAnalysisResponse:
    """Shape one track's full analysis for HTTP."""
    profile = report.profile
    metrics = report.metrics
    return TrackAnalysisResponse(
        track_id=report.track_id,
        status=report.status,
        analyzed_at=report.analyzed_at,
        profile=None
        if profile is None
        else AnalysisProfileResponse(
            distance_algorithm=profile.distance_algorithm,
            distance_algorithm_version=profile.distance_algorithm_version,
            movement_algorithm=profile.movement_algorithm,
            movement_algorithm_version=profile.movement_algorithm_version,
            elevation_algorithm=profile.elevation_algorithm,
            elevation_algorithm_version=profile.elevation_algorithm_version,
            metric_schema_version=profile.metric_schema_version,
        ),
        geometry=GeometryMetricsResponse(
            **{name.value: _metric(metrics, name) for name in MetricName if not name.needs_a_clock}
        ),
        timed_path=TimedPathMetricsResponse(
            basis=report.temporal_evidence,
            is_actual_activity_timing=report.is_actual_activity_timing,
            maximum_sustained_speed_mps=_metric(metrics, MetricName.MAXIMUM_SPEED),
            **{
                name.value: _metric(metrics, name)
                for name in MetricName
                if name.needs_a_clock and name is not MetricName.MAXIMUM_SPEED
            },
        ),
        quality=[flag.value for flag in report.quality],
        error_code=report.latest_error_code,
    )


@router.get("/tracks", summary="List stored tracks")
def list_tracks(
    request: Request,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    offset: PageOffset = 0,
    kind: KindFilter = None,
    activity: ActivityFilter = None,
    year: YearFilter = None,
    month: MonthFilter = None,
    analysis_status: AnalysisStatusFilter = None,
    sort: SortOrder = TrackOrder.IMPORTED_NEWEST_FIRST,
) -> TrackListResponse:
    """Return one page of stored tracks, without geometry.

    The page size is bounded by the server whatever is asked for: an archive
    grows without anybody deciding to, and an unbounded listing is a page that
    works until it does not.
    """
    try:
        page = _queries(request).list_tracks(
            limit=limit,
            offset=offset,
            kind=kind,
            activity=activity,
            year=year,
            month=month,
            analysis_status=analysis_status,
            order=sort,
        )
    except ValueError as error:
        # A month without its year is a combination no single parameter is wrong
        # about, so the framework's per-parameter validation cannot see it. The
        # answer is still the one a caller expects for an unusable query.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return TrackListResponse(
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        tracks=[_project(summary) for summary in page.tracks],
    )


@router.get("/tracks/{track_id}", summary="Read one track", responses=NOT_FOUND)
def read_track(
    request: Request, response: Response, track_id: TrackId
) -> TrackResponse | ErrorResponse:
    """Return one stored track, without geometry."""
    summary = _queries(request).get_track(track_id)
    return _not_found(response) if summary is None else _project(summary)


@router.get(
    "/tracks/{track_id}/geometry", summary="Read the geometry of one track", responses=NOT_FOUND
)
def read_geometry(
    request: Request, response: Response, track_id: TrackId
) -> GeometryResponse | ErrorResponse:
    """Return the segments and positions of one stored track."""
    segments = _queries(request).get_geometry(track_id)
    return _not_found(response) if segments is None else _project_geometry(track_id, segments)


@router.get(
    "/tracks/{track_id}/analysis",
    summary="Read the derived metrics of one track",
    responses=NOT_FOUND,
)
def read_analysis(
    request: Request, response: Response, track_id: TrackId
) -> TrackAnalysisResponse | ErrorResponse:
    """Return the metrics of one track, and whether they are still current.

    A separate resource rather than another field on the track. Metrics have
    their own lifecycle -- they can be missing, outdated or freshly derived
    while the track itself never changed -- and folding a status of their own
    into the track would make "this track exists" mean two things again.
    """
    report = _analysis(request)(track_id)
    return _not_found(response) if report is None else _project_analysis(report)


@router.put(
    "/tracks/{track_id}/classification",
    summary="Correct the track kind explicitly",
    responses=NOT_FOUND,
)
def override_classification(
    request: Request,
    response: Response,
    track_id: TrackId,
    correction: ClassificationOverrideRequest,
) -> TrackResponse | ErrorResponse:
    """Record a user correction, which outranks the classifier from now on."""
    summary = _queries(request).override_classification(track_id, correction.kind)
    return _not_found(response) if summary is None else _project(summary)


@router.delete(
    "/tracks/{track_id}/classification",
    summary="Withdraw the explicit correction",
    responses=NOT_FOUND,
)
def reset_classification(
    request: Request, response: Response, track_id: TrackId
) -> TrackResponse | ErrorResponse:
    """Remove a user correction, handing authority back to the classifier."""
    summary = _queries(request).reset_classification(track_id)
    return _not_found(response) if summary is None else _project(summary)
