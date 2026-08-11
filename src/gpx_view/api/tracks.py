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

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Path, Request, Response, status
from pydantic import BaseModel, Field

from gpx_view.application import ImportErrorCode
from gpx_view.application.ports import TrackSummary
from gpx_view.application.track_queries import TrackQueries
from gpx_view.domain import Activity, TrackKind, TrackSegment

router = APIRouter(prefix="/api/v1", tags=["tracks"])

TrackId = Annotated[int, Path(ge=1, description="Identity of a stored track")]


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


class TrackResponse(BaseModel):
    """One stored track without its geometry."""

    id: int
    raw_import_sha256: str
    source_index: int
    title: str | None
    activity: Activity
    classification: ClassificationResponse
    point_count: int
    segment_count: int
    started_at: datetime | None
    ended_at: datetime | None
    source: SourceResponse


class TrackListResponse(BaseModel):
    """Every stored track."""

    count: int
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


class ClassificationOverrideRequest(BaseModel):
    """An explicit user correction of a track's kind."""

    kind: TrackKind = Field(description="recorded, planned or unknown")


NOT_FOUND: dict[int | str, dict[str, object]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "No such track"}
}


def _queries(request: Request) -> TrackQueries:
    """Return the query use cases the composition root wired into the app."""
    return cast(TrackQueries, request.app.state.track_queries)


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
        point_count=summary.point_count,
        segment_count=summary.segment_count,
        started_at=summary.started_at,
        ended_at=summary.ended_at,
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


@router.get("/tracks", summary="List stored tracks")
def list_tracks(request: Request) -> TrackListResponse:
    """Return every stored track, without geometry."""
    summaries = _queries(request).list_tracks()
    return TrackListResponse(
        count=len(summaries), tracks=[_project(summary) for summary in summaries]
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
