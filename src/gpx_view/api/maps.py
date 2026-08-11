"""HTTP projection of the offline map capability.

```
GET    /api/v1/maps                      what is installed
GET    /api/v1/maps/catalog              what can be installed, one level at a time
POST   /api/v1/maps/catalog/refresh      re-read the provider's index
POST   /api/v1/maps/install              queue an install or an update
DELETE /api/v1/maps/{region_id}          remove one
GET    /api/v1/maps/jobs                 the newest installations
GET    /api/v1/maps/jobs/{job_id}        one of them
POST   /api/v1/maps/jobs/{job_id}/cancel ask one to stop
GET    /api/v1/maps/coverage             which maps belong behind a rectangle
GET    /api/v1/maps/credits              who to thank, from installed metadata
GET    /api/v1/maps/tiles/{id}/{z}/{x}/{y}.mvt   one tile
```

Three design decisions are worth stating, because each of them is a place the
obvious shape would be wrong.

**Installing takes a region, never a URL.** A `url` parameter would make this
endpoint a general-purpose fetcher operated by anybody who can reach the archive
-- the classic server-side request forgery primitive. The provider adapter
resolves the address, and nothing in a request body can influence which host is
contacted.

**Coverage is answered, styling is not.** The response says which packages
apply, where their tiles are and what has to be credited. It does not contain a
MapLibre style document: selection is a business decision this layer projects,
and colour is a presentation decision the page owns. A style assembled here
would put a design system in the API.

**A tile identity is a content hash, never a path.** `{delivery_id}` is matched
against the database, and the file it resolves to is derived from the region
identity. A caller cannot express a path, and the URL is immutable, which is
what makes a one-year cache lifetime safe.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, Field

from gpx_view.application.maps import (
    CatalogPage,
    GetMapCatalog,
    GetMapInstallJob,
    ListInstalledMaps,
    MapCoverage,
    MapErrorCode,
    MapInstallationService,
    MapOperationError,
    MapPackageRepository,
    MapSource,
    MapTileSource,
    RemoveMapPackage,
    SelectMapCoverage,
)
from gpx_view.application.maps.ports import InstalledMap, MapInstallJob
from gpx_view.domain.maps import (
    MapAttribution,
    MapBounds,
    MapInstallState,
    MapPackage,
    MapRegionId,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/maps", tags=["maps"])

MAX_TILE_ZOOM = 24
TILE_MEDIA_TYPE = "application/vnd.mapbox-vector-tile"
TILE_CACHE_CONTROL = "public, max-age=31536000, immutable"
"""How long a tile may be cached.

A year, because the package's content hash is in the URL: a different map is a
different address rather than the same address with different bytes behind it.
This is the same reasoning the hashed frontend assets use, and it is what stops
an update from needing a cache purge.
"""

STATE_CACHE_CONTROL = "no-store"
"""How long map *state* may be cached: not at all.

Coverage, the installed list and a job's progress all change while a reader is
looking at them. A cached job status is a progress bar that never moves.
"""

_STATUS_FOR: dict[MapErrorCode, int] = {
    MapErrorCode.MAP_REGION_UNKNOWN: status.HTTP_404_NOT_FOUND,
    MapErrorCode.MAP_JOB_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    MapErrorCode.MAP_PACKAGE_NOT_INSTALLED: status.HTTP_404_NOT_FOUND,
    MapErrorCode.MAP_PACKAGE_UNAVAILABLE: status.HTTP_404_NOT_FOUND,
    MapErrorCode.MAP_MUTATION_IN_PROGRESS: status.HTTP_409_CONFLICT,
    MapErrorCode.MAP_PROVIDER_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    MapErrorCode.MAP_INSUFFICIENT_DISK_SPACE: status.HTTP_507_INSUFFICIENT_STORAGE,
}
"""Which HTTP status each named failure projects to.

Anything not listed is a 400: it is a request that cannot be carried out, and
inventing a five-hundred for a provider serving a broken package would blame
this deployment for somebody else's file.
"""


class ErrorBody(BaseModel):
    """The one error envelope this API answers with."""

    code: str = Field(description="Stable error code")
    message: str = Field(description="Short, structural detail. Never a path or a URL.")


class ErrorResponse(BaseModel):
    """The error envelope, wrapped as every other endpoint wraps it."""

    error: ErrorBody


class AttributionLinkResponse(BaseModel):
    """One credit link, structured so nothing renders provider markup."""

    label: str
    url: str


class AttributionResponse(BaseModel):
    """What has to be shown while a package's data is drawn."""

    data_owner: str = Field(description="Who the underlying data belongs to")
    provider: str = Field(description="Who assembled and published the package")
    license_identifier: str = Field(description="Short, stable licence code")
    license_name: str = Field(description="The licence's own name")
    required_text: str = Field(description="The line that must stay visible")
    links: list[AttributionLinkResponse]


class BoundsResponse(BaseModel):
    """A west/south/east/north rectangle in WGS 84 degrees."""

    min_longitude: float
    min_latitude: float
    max_longitude: float
    max_latitude: float


class InstalledMapResponse(BaseModel):
    """One installed package, with everything a manager row needs."""

    region_id: str
    region_name: str
    provider: str
    state: MapInstallState = Field(
        description="`installed` only while a managed file backs the row"
    )
    format: str
    tile_schema: str
    tile_schema_version: str
    delivery_id: str = Field(description="Content hash; the identity tiles are served under")
    size_bytes: int
    bounds: BoundsResponse
    min_zoom: int
    max_zoom: int
    dataset_version: str | None
    dataset_timestamp: str | None = Field(description="When the provider last built it")
    downloaded_at: str = Field(description="When this deployment fetched it")
    attribution: AttributionResponse


class InstalledMapsResponse(BaseModel):
    """Everything installed, plus whether this deployment may install more."""

    maps: list[InstalledMapResponse]
    installs_enabled: bool
    total_size_bytes: int


class RemotePackageResponse(BaseModel):
    """What the provider said about a package, before anything is fetched."""

    size_bytes: int | None = Field(
        description="Declared size, or null when the provider declared none"
    )
    updated_at: str | None = Field(description="When the provider last built it")


class CatalogEntryResponse(BaseModel):
    """One catalog row."""

    region_id: str
    name: str
    parent_id: str | None
    has_children: bool
    installable: bool = Field(description="Whether a package exists for this region")
    availability_known: bool = Field(
        description="False means the provider could not be asked, not that there is none"
    )
    package: RemotePackageResponse | None
    installed: bool


class CatalogResponse(BaseModel):
    """One level of the provider's region tree."""

    parent_id: str | None
    provider: str
    provider_name: str
    provider_available: bool = Field(
        description="False with entries present means these came from the cache"
    )
    fetched_at: str | None
    entries: list[CatalogEntryResponse]


class InstallRequest(BaseModel):
    """What an install asks for: a region, and nothing that could name a host."""

    region_id: str = Field(
        min_length=3, max_length=256, description="A `provider:path` region identity"
    )


class JobResponse(BaseModel):
    """Where one installation has got to."""

    job_id: str
    region_id: str
    region_name: str
    state: str
    bytes_downloaded: int
    bytes_total: int | None = Field(description="Null when the provider declared no size")
    percentage: int | None = Field(description="Null when the total is unknown; never invented")
    is_update: bool
    error_code: str | None
    started_at: str
    updated_at: str


class JobsResponse(BaseModel):
    """The newest installations, finished or not."""

    jobs: list[JobResponse]


class MapSourceResponse(BaseModel):
    """One package as the page needs it to draw a basemap."""

    region_id: str
    region_name: str
    source_id: str = Field(description="Stable identifier for this source in a style")
    tiles_url: str = Field(description="Same-origin tile template")
    tile_schema: str
    tile_schema_version: str
    bounds: BoundsResponse
    min_zoom: int
    max_zoom: int
    attribution: AttributionResponse


class CoverageResponse(BaseModel):
    """Which installed maps belong behind a rectangle."""

    sources: list[MapSourceResponse] = Field(
        description="Most specific first. Empty means: draw on a neutral background."
    )
    glyphs_url: str = Field(description="Same-origin glyph template the styles use")
    any_installed: bool = Field(
        description="Whether the archive holds any usable map at all, so the page "
        "can tell 'nothing installed' from 'nothing here'"
    )


class CreditResponse(BaseModel):
    """One distinct data source behind the installed maps."""

    data_owner: str
    provider: str
    license_identifier: str
    license_name: str
    links: list[AttributionLinkResponse]
    regions: list[str]


class CreditsResponse(BaseModel):
    """Who to thank, derived from what is installed rather than hard-coded."""

    map_data: list[CreditResponse]


@router.get(
    "",
    response_model=InstalledMapsResponse,
    summary="Every installed map package",
)
def list_installed_maps(request: Request, response: Response) -> InstalledMapsResponse:
    """Return the installed packages and what the archive currently thinks of them."""
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    installed = _installed(request).all()
    return InstalledMapsResponse(
        maps=[_project_installed(entry) for entry in installed],
        installs_enabled=_enabled(request),
        total_size_bytes=sum(entry.package.size_bytes for entry in installed),
    )


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    responses={503: {"model": ErrorResponse}},
    summary="One level of the provider's region tree",
)
def read_catalog(
    request: Request,
    response: Response,
    parent: Annotated[
        str | None, Query(description="Region to open; omit for the top level")
    ] = None,
) -> CatalogResponse:
    """Return one node's children, with what each would cost to install."""
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    _require_maps_enabled(request)
    parent_id = _region_id(parent) if parent else None
    try:
        page = _catalog(request).children_of(parent_id)
    except MapOperationError as error:
        raise _http_error(error) from error
    installed = {str(entry.package.region_id) for entry in _installed(request).all()}
    return _project_catalog(page, installed)


@router.post(
    "/catalog/refresh",
    response_model=CatalogResponse,
    responses={503: {"model": ErrorResponse}},
    summary="Re-read the provider's index",
)
def refresh_catalog(request: Request, response: Response) -> CatalogResponse:
    """Fetch the provider's index again and return the top level.

    The one endpoint that always reaches the provider. Everything else answers
    from the cache, which is what makes "installed maps keep working while the
    provider is down" true rather than aspirational.
    """
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    _require_maps_enabled(request)
    catalog = _catalog(request)
    try:
        catalog.refresh()
        page = catalog.children_of(None)
    except MapOperationError as error:
        raise _http_error(error) from error
    installed = {str(entry.package.region_id) for entry in _installed(request).all()}
    return _project_catalog(page, installed)


@router.post(
    "/install",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Queue an install or an update for one region",
)
def install_map(request: Request, response: Response, payload: InstallRequest) -> JobResponse:
    """Queue an installation and answer immediately with its job.

    `202`, because nothing has been installed yet. A package is hundreds of
    megabytes and the transfer outlives any sensible request timeout, so the
    honest answer is "accepted, here is where to watch it".
    """
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    _require_maps_enabled(request)
    region_id = _region_id(payload.region_id)
    try:
        job = _installer(request).start(region_id)
    except MapOperationError as error:
        raise _http_error(error) from error
    return _project_job(job)


@router.delete(
    "/regions/{region_id:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Remove one installed map package",
)
def remove_map(request: Request, region_id: Annotated[str, Path()]) -> Response:
    """Delete a region's package. Track data is untouched."""
    try:
        _remove(request).run(_region_id(region_id))
    except MapOperationError as error:
        raise _http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/jobs", response_model=JobsResponse, summary="The newest installations")
def list_jobs(request: Request, response: Response) -> JobsResponse:
    """Return recent installation jobs, finished or not."""
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    return JobsResponse(jobs=[_project_job(job) for job in _repository(request).recent_jobs()])


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Where one installation has got to",
)
def read_job(request: Request, response: Response, job_id: Annotated[str, Path()]) -> JobResponse:
    """Return one job."""
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    try:
        return _project_job(_jobs(request).by_id(job_id))
    except MapOperationError as error:
        raise _http_error(error) from error


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Ask a running installation to stop",
)
def cancel_job(request: Request, response: Response, job_id: Annotated[str, Path()]) -> JobResponse:
    """Ask a job to stop at the next chunk boundary.

    Cooperative, and the response says so by returning the job's *current*
    state rather than claiming it is cancelled. A job already publishing runs to
    completion, because interrupting an atomic switch to save a moment would
    trade the guarantee that matters for the one that does not.
    """
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    try:
        job = _jobs(request).by_id(job_id)
    except MapOperationError as error:
        raise _http_error(error) from error
    _installer(request).cancel(job_id)
    return _project_job(job)


@router.get(
    "/coverage",
    response_model=CoverageResponse,
    summary="Which installed maps belong behind a rectangle",
)
def read_coverage(
    request: Request,
    response: Response,
    bbox: Annotated[
        str,
        Query(
            min_length=7,
            max_length=100,
            description="`min_lon,min_lat,max_lon,max_lat` in WGS 84 degrees",
        ),
    ],
) -> CoverageResponse:
    """Return the packages to draw behind a rectangle, most specific first.

    Reaches no provider and reads no track. It is the only map endpoint a track
    page calls, which is what makes viewing a track send nothing anywhere.
    """
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    coverage = _coverage(request).for_bounds(_bounds(bbox))
    return _project_coverage(coverage, any_installed=bool(_installed(request).usable()))


@router.get("/credits", response_model=CreditsResponse, summary="Who to thank")
def read_credits(request: Request, response: Response) -> CreditsResponse:
    """Return the distinct data sources behind the installed maps.

    Derived from package metadata, deduplicated by owner and licence, so a
    second provider appears here the day somebody installs one of its packages
    and no code changes.
    """
    response.headers["cache-control"] = STATE_CACHE_CONTROL
    grouped: dict[tuple[str, str, str], list[MapPackage]] = {}
    for entry in _installed(request).all():
        attribution = entry.package.attribution
        key = (attribution.data_owner, attribution.provider, attribution.license_identifier)
        grouped.setdefault(key, []).append(entry.package)
    return CreditsResponse(
        map_data=[
            CreditResponse(
                data_owner=owner,
                provider=provider,
                license_identifier=identifier,
                license_name=packages[0].attribution.license_name,
                links=[
                    AttributionLinkResponse(label=link.label, url=link.url)
                    for link in packages[0].attribution.links
                ],
                regions=sorted(package.region_name for package in packages),
            )
            for (owner, provider, identifier), packages in sorted(grouped.items())
        ]
    )


@router.get(
    "/tiles/{delivery_id}/{zoom}/{column}/{row}.mvt",
    response_class=Response,
    responses={
        200: {"content": {TILE_MEDIA_TYPE: {}}},
        204: {"description": "No tile here. A normal answer at the edge of coverage."},
        404: {"model": ErrorResponse},
    },
    summary="One vector tile from an installed package",
)
def read_tile(
    request: Request,
    delivery_id: Annotated[str, Path(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")],
    zoom: Annotated[int, Path(ge=0, le=MAX_TILE_ZOOM)],
    column: Annotated[int, Path(ge=0)],
    row: Annotated[int, Path(ge=0)],
) -> Response:
    """Return one tile's stored bytes.

    Stored bytes, not re-encoded ones. Shortbread packages hold gzipped vector
    tiles, so the blob is handed over with `Content-Encoding: gzip` and the
    browser does the one decompression there is. Unpacking and repacking on the
    way through would spend CPU per tile to produce the same bytes.

    A missing tile answers `204`. A hole in coverage is not an error, and a 404
    body would make every edge of every region print a stack of red lines in a
    browser console.
    """
    package = _repository(request).package_by_delivery_id(delivery_id)
    if package is None:
        raise _http_error(MapOperationError(MapErrorCode.MAP_REGION_UNKNOWN, "no such package"))
    limit = 1 << zoom
    if column >= limit or row >= limit:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        tile = _tiles(request).read(package.region_id, package.content_sha256, zoom, column, row)
    except MapOperationError:
        logger.warning("map_tiles.read_failed")
        raise _http_error(MapOperationError(MapErrorCode.MAP_TILE_READ_FAILED)) from None
    if tile is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    headers = {"cache-control": TILE_CACHE_CONTROL}
    if tile[:2] == b"\x1f\x8b":
        headers["content-encoding"] = "gzip"
    return Response(content=tile, media_type=TILE_MEDIA_TYPE, headers=headers)


# --- wiring ------------------------------------------------------------

# Each service is read out of the application state on its own rather than
# through one bag of them. The API layer may not name an infrastructure type,
# and a container object holding several would be exactly that.


def _enabled(request: Request) -> bool:
    """Report whether this deployment may install packages."""
    return bool(request.app.state.maps_enabled)


def _require_maps_enabled(request: Request) -> None:
    """Refuse a management action on a deployment that turned the feature off."""
    if not _enabled(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": MapErrorCode.MAP_PROVIDER_UNAVAILABLE.value,
                    "message": "offline maps are disabled for this deployment",
                }
            },
        )


def _catalog(request: Request) -> GetMapCatalog:
    """Return the catalog query."""
    catalog: GetMapCatalog = request.app.state.map_catalog
    return catalog


def _installed(request: Request) -> ListInstalledMaps:
    """Return the installed-package listing."""
    installed: ListInstalledMaps = request.app.state.map_installed
    return installed


def _coverage(request: Request) -> SelectMapCoverage:
    """Return the coverage authority."""
    coverage: SelectMapCoverage = request.app.state.map_coverage
    return coverage


def _jobs(request: Request) -> GetMapInstallJob:
    """Return the job query."""
    jobs: GetMapInstallJob = request.app.state.map_job_query
    return jobs


def _installer(request: Request) -> MapInstallationService:
    """Return the service that queues installations."""
    installer: MapInstallationService = request.app.state.map_installer
    return installer


def _repository(request: Request) -> MapPackageRepository:
    """Return the package and job repository."""
    repository: MapPackageRepository = request.app.state.map_repository
    return repository


def _tiles(request: Request) -> MapTileSource:
    """Return the tile source."""
    tiles: MapTileSource = request.app.state.map_tiles
    return tiles


def _remove(request: Request) -> RemoveMapPackage:
    """Return the removal use case."""
    remove: RemoveMapPackage = request.app.state.map_remove
    return remove


# --- projection --------------------------------------------------------


def _region_id(value: str) -> MapRegionId:
    """Return a validated region identity, or refuse the request.

    The one place a caller's string becomes an identity. It never becomes a
    path: the storage derives its directory from a digest of this value.
    """
    try:
        return MapRegionId.parse(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": MapErrorCode.MAP_REGION_UNKNOWN.value,
                    "message": "not a region identity",
                }
            },
        ) from error


def _bounds(value: str) -> MapBounds:
    """Return the rectangle a `bbox` query names, or refuse the request."""
    parts = value.split(",")
    try:
        if len(parts) != 4:
            raise ValueError("a bbox is four numbers")
        west, south, east, north = (float(part) for part in parts)
        return MapBounds(
            min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "map_bbox_invalid",
                    "message": "bbox is min_lon,min_lat,max_lon,max_lat",
                }
            },
        ) from error


def _http_error(error: MapOperationError) -> HTTPException:
    """Return the HTTP projection of a named map failure."""
    return HTTPException(
        status_code=_STATUS_FOR.get(error.code, status.HTTP_400_BAD_REQUEST),
        detail={"error": {"code": error.code.value, "message": error.detail or error.code.value}},
    )


def _project_bounds(bounds: MapBounds) -> BoundsResponse:
    """Project a rectangle."""
    return BoundsResponse(
        min_longitude=bounds.min_longitude,
        min_latitude=bounds.min_latitude,
        max_longitude=bounds.max_longitude,
        max_latitude=bounds.max_latitude,
    )


def _project_attribution(attribution: MapAttribution) -> AttributionResponse:
    """Project a package's attribution, structurally and never as markup."""
    return AttributionResponse(
        data_owner=attribution.data_owner,
        provider=attribution.provider,
        license_identifier=attribution.license_identifier,
        license_name=attribution.license_name,
        required_text=attribution.required_text,
        links=[
            AttributionLinkResponse(label=link.label, url=link.url) for link in attribution.links
        ],
    )


def _project_installed(entry: InstalledMap) -> InstalledMapResponse:
    """Project one installed package."""
    package = entry.package
    return InstalledMapResponse(
        region_id=str(package.region_id),
        region_name=package.region_name,
        provider=package.provider,
        state=entry.state,
        format=package.format.value,
        tile_schema=package.tile_schema.name,
        tile_schema_version=package.tile_schema.version,
        delivery_id=package.delivery_id,
        size_bytes=package.size_bytes,
        bounds=_project_bounds(package.bounds),
        min_zoom=package.min_zoom,
        max_zoom=package.max_zoom,
        dataset_version=package.dataset_version,
        dataset_timestamp=(
            None if package.dataset_timestamp is None else package.dataset_timestamp.isoformat()
        ),
        downloaded_at=package.downloaded_at.isoformat(),
        attribution=_project_attribution(package.attribution),
    )


def _project_catalog(page: CatalogPage, installed: set[str]) -> CatalogResponse:
    """Project one level of the catalog tree."""
    return CatalogResponse(
        parent_id=None if page.parent_id is None else str(page.parent_id),
        provider=page.provider,
        provider_name=page.provider_name,
        provider_available=page.provider_available,
        fetched_at=page.fetched_at_iso,
        entries=[
            CatalogEntryResponse(
                region_id=str(entry.region_id),
                name=entry.name,
                parent_id=None if entry.parent_id is None else str(entry.parent_id),
                has_children=entry.has_children,
                installable=entry.package is not None,
                availability_known=entry.availability_known,
                package=(
                    None
                    if entry.package is None
                    else RemotePackageResponse(
                        size_bytes=entry.package.size_bytes,
                        updated_at=(
                            None
                            if entry.package.updated_at is None
                            else entry.package.updated_at.isoformat()
                        ),
                    )
                ),
                installed=str(entry.region_id) in installed,
            )
            for entry in page.entries
        ],
    )


def _project_job(job: MapInstallJob) -> JobResponse:
    """Project one installation job."""
    return JobResponse(
        job_id=job.job_id,
        region_id=str(job.region_id),
        region_name=job.region_name,
        state=job.state.value,
        bytes_downloaded=job.bytes_downloaded,
        bytes_total=job.bytes_total,
        percentage=job.percentage,
        is_update=job.is_update,
        error_code=job.error_code,
        started_at=job.started_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


def _project_source(source: MapSource) -> MapSourceResponse:
    """Project one package as a drawable source."""
    return MapSourceResponse(
        region_id=str(source.region_id),
        region_name=source.region_name,
        source_id=f"map-{source.delivery_id[:12]}",
        tiles_url=f"/api/v1/maps/tiles/{source.delivery_id}/{{z}}/{{x}}/{{y}}.mvt",
        tile_schema=source.tile_schema,
        tile_schema_version=source.tile_schema_version,
        bounds=_project_bounds(source.bounds),
        min_zoom=source.min_zoom,
        max_zoom=source.max_zoom,
        attribution=_project_attribution(source.package.attribution),
    )


def _project_coverage(coverage: MapCoverage, *, any_installed: bool) -> CoverageResponse:
    """Project the coverage answer."""
    return CoverageResponse(
        sources=[_project_source(source) for source in coverage.sources],
        glyphs_url="/fonts/{fontstack}/{range}.pbf",
        any_installed=any_installed,
    )


__all__ = ["router"]
