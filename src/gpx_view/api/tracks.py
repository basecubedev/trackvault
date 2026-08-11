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
reverse proxy that authenticates.

``POST /tracks/imports`` is the one endpoint here that makes the server *write*,
and it is the reason that sentence matters more than it used to. It exists
because the archive's owner asked for it, it goes through the single canonical
import use case like every other input path, it bounds its read before consuming
a body, and a deployment that cannot assume a trusted network refuses it with
``GPX_VIEW_UPLOAD_ENABLED=false``. See ``docs/adr/0011-web-upload.md``.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, Field

from gpx_view.application import ImportErrorCode
from gpx_view.application.analysis import GetTrackAnalysis, TrackAnalysisReport
from gpx_view.application.calendar import MAX_QUERY_YEAR, MIN_QUERY_YEAR, MONTHS_IN_YEAR
from gpx_view.application.import_tracks import ImportRequest, ImportStatus, ImportTracks
from gpx_view.application.maps import ApproximateLocation, LocateTracks
from gpx_view.application.ports import (
    MAX_PAGE_SIZE,
    AnalysisAvailability,
    TrackOrder,
    TrackSummary,
)
from gpx_view.application.track_profile import (
    DEFAULT_PROFILE_SAMPLES,
    MAX_GEOMETRY_POINTS,
    MAX_PROFILE_SAMPLES,
    GetTrackGeometry,
    GetTrackProfile,
    TrackGeometryReport,
    TrackProfileReport,
)
from gpx_view.application.track_queries import (
    DEFAULT_PAGE_SIZE,
    UNCHANGED,
    TrackQueries,
    Unchanged,
)
from gpx_view.domain import (
    MAX_NOTE_LENGTH,
    MAX_TITLE_LENGTH,
    Activity,
    TemporalEvidence,
    TrackKind,
    supports_actual_calendar_placement,
    supports_actual_timing,
)
from gpx_view.domain.analysis import AnalysisProfile, MetricName, MetricValue
from gpx_view.domain.raw_import import InputChannel

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
GeometryPoints = Annotated[
    int | None,
    Query(
        ge=2,
        le=MAX_GEOMETRY_POINTS,
        description="Reduce the shape to at most this many positions; omit for the canonical set",
    ),
]
ProfileSamples = Annotated[
    int,
    Query(ge=2, le=MAX_PROFILE_SAMPLES, description="How many samples the series may hold"),
]


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


class UserMetadataResponse(BaseModel):
    """What the archive's owner said about a track, beside what its source said.

    ``title`` here is the *correction*, not the title to display: that is the
    track's own ``title``, which projects this over the source one exactly as
    ``effective_kind`` projects an override over a detected kind. Both halves
    are reported so a reader can tell a correction from a document.
    """

    title: str | None = Field(description="The user's title, or null to let the source stand")
    note: str | None
    source_title: str | None = Field(description="What the document called it. Never overwritten")
    is_overridden: bool


class LocatedCountryResponse(BaseModel):
    """One country a track was approximately in."""

    name: str
    code: str | None = Field(description="ISO 3166-1 alpha-2, where the provider states one")


class ApproximateLocationResponse(BaseModel):
    """Roughly where a track was, and the word "roughly" is in the name.

    What is compared is rectangles: the box around the track against the box
    around a region's outline. Inside a country that is right; near a border it
    is not, and it is confidently not -- a walk in Aachen falls inside the
    rectangle around the Dutch province of Limburg, and this says so. An
    interface showing it has to say it is approximate; the field name is the
    reminder that it cannot be anything else.

    ``null`` where nothing can answer: a track with no positions, or an archive
    that has never read a region catalog. Locating reaches no provider.
    """

    regions: list[str] = Field(
        description="Named regions, most specific first. More than one only when no "
        "single region's rectangle holds the whole track."
    )
    countries: list[LocatedCountryResponse] = Field(
        description="The countries those regions belong to. The country is the named "
        "region's own ancestor, so the two can be wrong together but never contradict."
    )


class TrackResponse(BaseModel):
    """One stored track without its geometry."""

    id: int
    raw_import_sha256: str
    source_index: int
    title: str | None = Field(
        description="The title to display: the user's correction, else the source's"
    )
    metadata: UserMetadataResponse
    activity: Activity
    classification: ClassificationResponse
    timeline: TimelineResponse
    analysis: AnalysisSummaryResponse
    point_count: int
    segment_count: int
    source: SourceResponse
    approximate_location: ApproximateLocationResponse | None = Field(
        description="Roughly where the track was, from region rectangles. Never exact."
    )
    same_recording_ids: list[int] = Field(
        description="Other tracks whose normalized positions and instants are identical to "
        "this one's -- one ride exported more than once. An equality, not a "
        "similarity: it never claims two rides are one because they look alike, "
        "and it does not find the same loop ridden on two days. Empty also means "
        "'nothing matched', never 'nothing was compared'."
    )


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
    """The geometry of one track, with its segment boundaries preserved.

    Without ``max_points`` this is the canonical normalized geometry: every
    position, exactly as stored. With one it is a *projection* of that shape --
    the positions that carry it, chosen so a map draws the same line with fewer
    of them -- and ``simplified`` says which of the two a client is holding.
    Truncating silently would make the canonical projection a lossy one, and
    nothing downstream could tell.
    """

    track_id: int
    segment_count: int
    point_count: int = Field(description="Positions in this response")
    total_point_count: int = Field(description="Positions the track holds")
    simplified: bool = Field(description="Whether the shape was reduced for presentation")
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


class ProfileSampleResponse(BaseModel):
    """One position, with what a map and a chart each read from it.

    ``segment_index`` and ``point_index`` are the sample's identity. A chart
    cursor and a map marker address a sample by them rather than by looking for
    a nearby coordinate, which is a second authority on identity and picks the
    wrong position exactly where a track crosses itself.
    """

    segment_index: int
    point_index: int
    distance_m: float = Field(description="Cumulative distance, summed within segments")
    latitude: float
    longitude: float
    time: datetime | None
    elevation_m: float | None = Field(description="The altitude as recorded")
    filtered_elevation_m: float | None = Field(
        description="The altitude after the filter the ascent figure is accumulated from"
    )
    speed_mps: float | None = Field(
        description="Speed held across the analysis window; null where none could be derived"
    )
    heart_rate_bpm: int | None = Field(
        description="What a monitor measured here. A measurement, passed through untouched."
    )
    cadence_rpm: int | None = Field(
        description="What a cadence sensor measured here. Zero is a reading, not an absence."
    )


class ProfileSegmentResponse(BaseModel):
    """One uninterrupted run of samples. Never joined to its neighbour."""

    index: int
    samples: list[ProfileSampleResponse]


class TrackProfileResponse(BaseModel):
    """The series of one track, bounded to what a client can render.

    The series are always derived by the algorithms this build installs, from
    the geometry a reader currently sees. ``analysis_status`` describes the
    track's *stored* aggregates, which may have been derived by older ones: when
    it is not ``current``, the chart and the headline figures beside it were not
    produced by the same rules, and ``analyze --outdated`` is what makes them
    agree again.
    """

    track_id: int
    sample_count: int = Field(description="Samples in this response")
    total_sample_count: int = Field(description="Samples the track holds")
    total_distance_m: float = Field(description="Upper end of the distance axis, in metres")
    analysis_status: AnalysisAvailability = Field(
        description="What the track's stored aggregate metrics amount to"
    )
    derived_with: AnalysisProfileResponse = Field(
        description="The algorithms these series were derived by, always the installed ones"
    )
    timing_basis: TemporalEvidence
    is_actual_activity_timing: bool
    segments: list[ProfileSegmentResponse]


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


class TrackMetadataRequest(BaseModel):
    """A partial update of what the user says about a track.

    A field that is **absent** keeps its stored value; a field that is present
    and ``null`` -- or blank -- is cleared. Without that distinction a request
    that only renames a track would delete its note, which is how a partial
    update quietly loses data.

    Both fields are plain text. Nothing here is markup, nothing is interpreted,
    and the length bounds exist because an unauthenticated endpoint that accepts
    text is a storage-growth decision if it accepts unbounded text.
    """

    title: str | None = Field(
        default=None,
        max_length=MAX_TITLE_LENGTH,
        description="A new title, or null to fall back to the source title",
    )
    note: str | None = Field(
        default=None, max_length=MAX_NOTE_LENGTH, description="A new note, or null to remove it"
    )


class ImportOutcomeResponse(BaseModel):
    """What one offered file produced.

    Every completed attempt answers ``200`` and says in ``status`` which of the
    four outcomes it was -- including ``failed``. A file the archive could not
    read is not a failed *request*: the server did exactly what was asked, read
    the bytes, and concluded something about them. Encoding that conclusion in
    an HTTP status as well would be a second vocabulary for one answer, and the
    two would drift the first time a fifth outcome existed.
    """

    status: ImportStatus = Field(description="imported, duplicate, repaired or failed")
    sha256: str = Field(description="Content hash of the offered bytes, and their identity")
    track_ids: list[int] = Field(
        description="The tracks this produced, or the tracks a duplicate already had"
    )
    error_code: str | None = Field(description="Why a failed attempt failed")


NOT_FOUND: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No such track"}
}


def _locate(request: Request, summary: TrackSummary) -> ApproximateLocation | None:
    """Return roughly where one track was, from the catalog this deployment cached.

    Read per row rather than batched: the whole answer is a few hundred
    rectangle comparisons against a document already in memory, and a cache read
    per page would be the thing to add if that ever stopped being true.
    """
    locate = cast(LocateTracks, request.app.state.locate_tracks)
    return locate.for_bounds(summary.bounds)


def _queries(request: Request) -> TrackQueries:
    """Return the query use cases the composition root wired into the app."""
    return cast(TrackQueries, request.app.state.track_queries)


def _analysis(request: Request) -> GetTrackAnalysis:
    """Return the analysis query the composition root wired into the app."""
    return cast(GetTrackAnalysis, request.app.state.track_analysis)


def _profile(request: Request) -> GetTrackProfile:
    """Return the profile query the composition root wired into the app."""
    return cast(GetTrackProfile, request.app.state.track_profile)


def _geometry(request: Request) -> GetTrackGeometry:
    """Return the geometry query the composition root wired into the app."""
    return cast(GetTrackGeometry, request.app.state.track_geometry)


def _not_found(response: Response) -> ErrorResponse:
    """Project an absent track onto the stable error contract."""
    response.status_code = status.HTTP_404_NOT_FOUND
    return ErrorResponse(
        error=ErrorBody(code=ImportErrorCode.TRACK_NOT_FOUND, message="no such track")
    )


def _project(summary: TrackSummary, location: ApproximateLocation | None = None) -> TrackResponse:
    """Shape one stored track for HTTP."""
    classification = summary.classification
    detected = classification.detected
    return TrackResponse(
        id=summary.track_id,
        raw_import_sha256=summary.raw_import_sha256,
        source_index=summary.source_index,
        title=summary.display_title,
        metadata=UserMetadataResponse(
            title=summary.user_metadata.title,
            note=summary.user_metadata.note,
            source_title=summary.title,
            is_overridden=summary.user_metadata.title is not None,
        ),
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
        same_recording_ids=list(summary.same_recording_ids),
        approximate_location=(
            None
            if location is None
            else ApproximateLocationResponse(
                regions=list(location.regions),
                countries=[
                    LocatedCountryResponse(name=country.name, code=country.code)
                    for country in location.countries
                ],
            )
        ),
        source=SourceResponse(
            exchange_format=summary.source.exchange_format,
            format_version=summary.source.format_version,
            creator=summary.source.creator,
            links=list(summary.source.external_links),
            extension_namespaces=list(summary.source.extension_namespaces),
        ),
    )


def _project_geometry(report: TrackGeometryReport) -> GeometryResponse:
    """Shape the geometry of one track for HTTP."""
    segments = report.segments
    return GeometryResponse(
        track_id=report.track_id,
        segment_count=len(segments),
        point_count=report.point_count,
        total_point_count=report.total_point_count,
        simplified=report.simplified,
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


def _project_profile(report: TrackProfileReport) -> TrackProfileResponse:
    """Shape one track's series for HTTP."""
    return TrackProfileResponse(
        track_id=report.track_id,
        sample_count=report.sample_count,
        total_sample_count=report.total_sample_count,
        total_distance_m=report.total_distance_m,
        analysis_status=report.analysis_status,
        derived_with=_project_analysis_profile(report.derived_with),
        timing_basis=report.timing_basis,
        is_actual_activity_timing=report.is_actual_activity_timing,
        segments=[
            ProfileSegmentResponse(
                index=segment.index,
                samples=[
                    ProfileSampleResponse(
                        segment_index=sample.segment_index,
                        point_index=sample.point_index,
                        distance_m=sample.cumulative_distance_m,
                        latitude=sample.latitude,
                        longitude=sample.longitude,
                        time=sample.instant,
                        elevation_m=sample.raw_elevation_m,
                        filtered_elevation_m=sample.filtered_elevation_m,
                        speed_mps=sample.sustained_speed_mps,
                        heart_rate_bpm=sample.heart_rate_bpm,
                        cadence_rpm=sample.cadence_rpm,
                    )
                    for sample in segment.samples
                ],
            )
            for segment in report.profile.segments
        ],
    )


def _project_analysis_profile(profile: AnalysisProfile) -> AnalysisProfileResponse:
    """Shape one set of algorithm names and versions for HTTP."""
    return AnalysisProfileResponse(
        distance_algorithm=profile.distance_algorithm,
        distance_algorithm_version=profile.distance_algorithm_version,
        movement_algorithm=profile.movement_algorithm,
        movement_algorithm_version=profile.movement_algorithm_version,
        elevation_algorithm=profile.elevation_algorithm,
        elevation_algorithm_version=profile.elevation_algorithm_version,
        metric_schema_version=profile.metric_schema_version,
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
        profile=None if profile is None else _project_analysis_profile(profile),
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


MAX_FILENAME_LENGTH = 255
UploadFilename = Annotated[
    str | None,
    Query(
        max_length=MAX_FILENAME_LENGTH,
        description="What to remember the file as. Display metadata; never a location.",
    ),
]


def _importer(request: Request) -> ImportTracks:
    """Return the one canonical import use case."""
    return cast(ImportTracks, request.app.state.import_tracks)


def _refuse(code: str, message: str, http_status: int) -> HTTPException:
    """Return the archive's own error envelope, wrapped as every route wraps it."""
    return HTTPException(
        status_code=http_status, detail={"error": {"code": code, "message": message}}
    )


def _too_large() -> HTTPException:
    """Return the one refusal a body's size earns, named as the pipeline names it."""
    return _refuse(
        ImportErrorCode.IMPORT_TOO_LARGE.value,
        "the file is larger than this archive accepts",
        status.HTTP_413_CONTENT_TOO_LARGE,
    )


async def _bounded_body(request: Request, limit: int) -> bytes:
    """Read the request body, stopping as soon as it exceeds ``limit``.

    The bound is on the *read*. Loading a body and then measuring it has already
    paid the cost the limit exists to prevent, and this is an endpoint anybody
    who can reach the port may call. A declared length over the ceiling is
    refused before a single chunk is pulled; an undeclared one is stopped mid
    stream.

    Raises:
        HTTPException: ``413`` when the body is larger than ``limit``.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/tracks/imports",
    summary="Offer one file to the archive",
    responses={
        400: {"model": ErrorResponse, "description": "Nothing was offered"},
        403: {"model": ErrorResponse, "description": "This deployment refuses uploads"},
        413: {"model": ErrorResponse, "description": "Larger than this archive accepts"},
    },
    # Declared rather than inferred. The body is read as a stream so that the
    # size limit bounds the read, which means FastAPI never sees a body
    # parameter to document -- and an endpoint whose schema does not mention
    # the bytes it takes is lying by omission to every generated client.
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The file itself. One per request.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def import_file(request: Request, filename: UploadFilename = None) -> ImportOutcomeResponse:
    """Import one offered file and report what it produced.

    The body is the file. One file per request, deliberately: a reader watching
    twenty files arrive wants to know which of them the archive could not read,
    and a single response for a batch either hides that or reinvents this
    response inside a list.

    Nothing about the request decides anything. The bytes go to the same
    ``ImportTracks`` use case the command line and the scanned directory use, so
    the duplicate rule, the storage layout, the classification and the analysis
    are the archive's, not this route's.
    """
    if not request.app.state.upload_enabled:
        raise _refuse(
            "upload_disabled",
            "this deployment does not accept uploads",
            status.HTTP_403_FORBIDDEN,
        )
    settings = request.app.state.services.settings
    content = await _bounded_body(request, settings.import_max_bytes)
    if not content:
        # A request-level complaint rather than an import outcome: there is no
        # file here to have concluded anything about. `ImportErrorCode` is the
        # vocabulary for what the archive made of some bytes.
        raise _refuse("upload_empty", "no bytes were offered", status.HTTP_400_BAD_REQUEST)
    outcome = _importer(request)(
        ImportRequest(
            content=content,
            original_filename=filename,
            input_channel=InputChannel.WEB_UPLOAD,
        )
    )
    return ImportOutcomeResponse(
        status=outcome.status,
        sha256=outcome.sha256,
        track_ids=list(outcome.track_ids),
        error_code=None if outcome.error_code is None else outcome.error_code.value,
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
        tracks=[_project(summary, _locate(request, summary)) for summary in page.tracks],
    )


@router.get("/tracks/{track_id}", summary="Read one track", responses=NOT_FOUND)
def read_track(
    request: Request, response: Response, track_id: TrackId
) -> TrackResponse | ErrorResponse:
    """Return one stored track, without geometry."""
    summary = _queries(request).get_track(track_id)
    return _not_found(response) if summary is None else _project(summary, _locate(request, summary))


@router.get(
    "/tracks/{track_id}/geometry", summary="Read the geometry of one track", responses=NOT_FOUND
)
def read_geometry(
    request: Request, response: Response, track_id: TrackId, max_points: GeometryPoints = None
) -> GeometryResponse | ErrorResponse:
    """Return the segments and positions of one stored track.

    Without ``max_points`` this is the canonical normalized geometry. With one
    it is a presentation projection of the same shape, and ``simplified`` says
    so. Nothing is written either way: the reduction happens on the way out.
    """
    report = _geometry(request)(track_id, max_points=max_points)
    return _not_found(response) if report is None else _project_geometry(report)


@router.get(
    "/tracks/{track_id}/profile",
    summary="Read the elevation and speed series of one track",
    responses=NOT_FOUND,
)
def read_profile(
    request: Request,
    response: Response,
    track_id: TrackId,
    max_samples: ProfileSamples = DEFAULT_PROFILE_SAMPLES,
) -> TrackProfileResponse | ErrorResponse:
    """Return one track's series against its own distance axis.

    Derived on demand from the current normalized geometry, by the same
    elevation filter the ascent figure is accumulated from and the same window
    the maximum sustained speed is read from. A chart drawn from this and a
    headline computed from those are two views of one analysis rather than two
    analyses.
    """
    report = _profile(request)(track_id, max_samples=max_samples)
    return _not_found(response) if report is None else _project_profile(report)


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
    return _not_found(response) if summary is None else _project(summary, _locate(request, summary))


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
    return _not_found(response) if summary is None else _project(summary, _locate(request, summary))


@router.patch(
    "/tracks/{track_id}/metadata",
    summary="Correct the title, or keep a note",
    responses=NOT_FOUND,
)
def update_metadata(
    request: Request, response: Response, track_id: TrackId, update: TrackMetadataRequest
) -> TrackResponse | ErrorResponse:
    """Record what the user says about a track, and return it as it now reads.

    Source data is untouched. The correction is stored beside the raw import and
    the normalized track, so reprocessing replaces both of those and leaves this
    standing -- the same promise the classification override already makes.

    Which fields were *sent* is the difference between a partial update and a
    replacement, so the request's own field set decides what to leave alone.
    """
    try:
        summary = _queries(request).set_metadata(
            track_id,
            title=_stated(update, "title"),
            note=_stated(update, "note"),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _not_found(response) if summary is None else _project(summary, _locate(request, summary))


def _stated(update: TrackMetadataRequest, field: str) -> str | Unchanged | None:
    """Return what a request said about one field, or that it said nothing.

    ``None`` is a value here -- "clear this" -- so absence needs its own token,
    and the only place that distinction is visible is the request's field set.
    """
    if field not in update.model_fields_set:
        return UNCHANGED
    value: str | None = getattr(update, field)
    return value
