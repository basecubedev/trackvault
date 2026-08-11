"""The Geofabrik catalog adapter.

Everything this provider knows about the world lives here: the index URL, the
package URL convention, the hosts it is allowed to talk to and its display
name. The application above knows a `region_id` and nothing else, which is what
makes "an API caller cannot choose the host this server contacts" structural
rather than a promise.

**Where the region hierarchy comes from.** Geofabrik publishes a machine-readable
index, `index-v1.json`, holding every region with its display name, its parent,
its download URLs and its outline. That index is the source of the tree -- an
HTML page is a document for people, and parsing one as an API means a redesign
breaks this application.

**Why the index with geometry.** There is a smaller `-nogeom` variant, and this
adapter used it until the archive needed to answer "which map does this track
need?". Without knowing where a region *is*, that question has no answer at all
and a reader is left browsing 555 regions to find their own. The outlines are
read, reduced to one rectangle per region, and thrown away: four floats are
what a suggestion needs, and keeping polygons would mean carrying a spatial
index around for a question a bounding box answers well enough to *offer*. It
costs about 3 MB more on a catalog refresh, which is an action somebody pressed.

**Where the package URL comes from, honestly.** The index lists `.osm.pbf`,
`.shp.zip` and history URLs. It does **not** list the vector tile package, which
Geofabrik offers on its per-region HTML pages, marked `experimental`, at

```
<region path>-shortbread-1.0.mbtiles
```

So the URL is composed from the region path the index gave us plus that suffix,
and whether it exists is settled with a `HEAD` rather than assumed. That is a
convention this adapter depends on, not a contract Geofabrik published, and it
is the main reason this whole capability is behind a port.

**What is validated.** The index is remote, untrusted input. Region paths are
derived from URLs that must sit under the download host and end in the expected
suffix; names are bounded; a region whose path does not survive `MapRegionId`
validation is dropped rather than repaired. Nothing from the index is ever used
as a path, only as an identity that is then hashed.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from gpx_view.application.maps import (
    CatalogRegion,
    MapErrorCode,
    MapOperationError,
    RemotePackage,
    TransferOutcome,
)
from gpx_view.domain.maps import MapBounds, MapRegionId
from gpx_view.infrastructure.maps.transfer import HttpTransfer, user_agent

logger = logging.getLogger(__name__)

PROVIDER_SLUG = "geofabrik"
PROVIDER_DISPLAY_NAME = "Geofabrik GmbH"

DOWNLOAD_HOST = "download.geofabrik.de"
DOWNLOAD_BASE = f"https://{DOWNLOAD_HOST}/"
INDEX_URL = f"{DOWNLOAD_BASE}index-v1.json"
ALLOWED_HOSTS = frozenset({DOWNLOAD_HOST})

PBF_SUFFIX = "-latest.osm.pbf"
PACKAGE_SUFFIX = "-shortbread-1.0.mbtiles"

MAX_INDEX_BYTES = 24 * 1024 * 1024
"""The most an index may be, whatever it declares.

The index with outlines is around 4 MB and grows as the world is mapped. The
ceiling is generous rather than snug because the failure it prevents is a
provider serving something unbounded, not a provider serving a bigger world.
"""

MAX_REGIONS = 5000
MAX_NAME_LENGTH = 120
MAX_COORDINATE_DEPTH = 8
"""How deep a nested coordinate array may be before it is refused.

A `MultiPolygon` nests four levels. The index is untrusted input and walking it
is recursive, so the depth is bounded rather than trusted -- an array nested ten
thousand deep is a stack overflow, not a region.
"""


class GeofabrikMapProvider:
    """Regions and downloadable vector tile packages, from Geofabrik.

    Args:
        version: This build's version, so the User-Agent identifies who is
            asking. A provider that has to rate-limit somebody deserves to know
            which application it is.
        transfer: Injected so tests can point the whole adapter at a local
            server without a network, and so the host allowlist is visible at
            the wiring rather than hidden in a constructor.
    """

    def __init__(self, *, version: str, transfer: HttpTransfer | None = None) -> None:
        """Build the adapter, with its own bounded transfer by default."""
        self._transfer = transfer or HttpTransfer(
            allowed_hosts=ALLOWED_HOSTS, agent=user_agent(version)
        )
        self._base = DOWNLOAD_BASE
        self._index_url = INDEX_URL

    @property
    def transfer(self) -> HttpTransfer:
        """Return the transfer this adapter talks through, for inspection."""
        return self._transfer

    @property
    def slug(self) -> str:
        """Return the catalog slug that scopes this provider's region ids."""
        return PROVIDER_SLUG

    @property
    def display_name(self) -> str:
        """Return the provider's name as a reader should see it."""
        return PROVIDER_DISPLAY_NAME

    def regions(self) -> tuple[CatalogRegion, ...]:
        """Fetch and validate the provider's region hierarchy."""
        return parse_index(self._transfer.fetch(self._index_url, limit_bytes=MAX_INDEX_BYTES))

    def resolve(self, region_id: MapRegionId) -> RemotePackage | None:
        """Ask whether a package exists for a region, and how big it is."""
        url = self._package_url(region_id)
        resource = self._transfer.head(url)
        if resource is None:
            return None
        return RemotePackage(
            region_id=region_id,
            url=url,
            size_bytes=resource.size_bytes,
            updated_at=resource.modified_at,
        )

    def download(
        self,
        package: RemotePackage,
        sink: BinaryIO,
        *,
        max_bytes: int,
        on_progress: Callable[[int], None],
        is_cancelled: Callable[[], bool],
    ) -> TransferOutcome:
        """Stream a package into ``sink``.

        The URL is re-derived from the region identity rather than taken from
        the argument. A `RemotePackage` travels through the application layer,
        and re-deriving here means even a defect up there cannot turn into a
        request to somewhere else.
        """
        return self._transfer.stream(
            self._package_url(package.region_id),
            sink,
            max_bytes=max_bytes,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
        )

    def _package_url(self, region_id: MapRegionId) -> str:
        """Return the package address for a region."""
        if region_id.provider != PROVIDER_SLUG:
            raise MapOperationError(MapErrorCode.MAP_REGION_UNKNOWN, "wrong provider")
        return f"{self._base}{region_id.path}{PACKAGE_SUFFIX}"


def parse_index(body: bytes) -> tuple[CatalogRegion, ...]:
    """Turn the provider's index into validated catalog regions.

    A module-level function rather than a method, because it depends on nothing
    the adapter holds. Parsing is a boundary against untrusted bytes and can be
    checked as one, without a socket and without a way to point this build at a
    host somebody chose -- which stays impossible.

    Raises:
        MapOperationError: ``map_catalog_invalid`` when the body is not an index
            this build can read.
    """
    try:
        document: Any = json.loads(body)
    except ValueError as error:
        raise MapOperationError(MapErrorCode.MAP_CATALOG_INVALID, "unreadable index") from error
    if not isinstance(document, dict) or not isinstance(document.get("features"), list):
        raise MapOperationError(MapErrorCode.MAP_CATALOG_INVALID, "unexpected index shape")

    features = document["features"][:MAX_REGIONS]
    found: dict[str, CatalogRegion] = {}
    for feature in features:
        parsed = _region_of(feature)
        if parsed is not None:
            found[parsed.region_id.path] = parsed
    if not found:
        raise MapOperationError(MapErrorCode.MAP_CATALOG_INVALID, "index holds no regions")

    return tuple(
        replace(region, parent_id=_parent_within(region.region_id, found))
        for region in found.values()
    )


def _region_of(feature: object) -> CatalogRegion | None:
    """Return one validated region, or ``None`` for an entry to skip.

    The parent is filled in afterwards, from the paths the whole index
    turned out to hold.
    """
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return None
    urls = properties.get("urls")
    name = properties.get("name")
    if not isinstance(urls, dict) or not isinstance(name, str) or not name.strip():
        return None
    path = _path_of(urls.get("pbf"))
    if path is None:
        return None
    try:
        region_id = MapRegionId(provider=PROVIDER_SLUG, segments=tuple(path.split("/")))
    except ValueError:
        # A region whose path this build cannot express as an identity is
        # dropped rather than sanitised. Repairing remote input into
        # something that "looks fine" is how a validation boundary stops
        # being one.
        logger.debug("map_catalog.region_skipped")
        return None
    return CatalogRegion(
        region_id=region_id,
        name=name.strip()[:MAX_NAME_LENGTH],
        country_code=_country_code(properties),
        # An outline this build cannot read costs the region its extent and
        # nothing else: it stays browsable and installable, it just cannot
        # be suggested for a track.
        bounds=_bounds_of(feature.get("geometry")),
    )


def _path_of(url: object) -> str | None:
    """Return the region path a download URL names, if it names one safely."""
    if not isinstance(url, str) or not url.endswith(PBF_SUFFIX):
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() != DOWNLOAD_HOST:
        return None
    path = parts.path.lstrip("/").removesuffix(PBF_SUFFIX)
    return path or None


def _bounds_of(geometry: object) -> MapBounds | None:
    """Return the rectangle around a region's outline, or ``None``.

    The outline itself is discarded. A polygon would let this build say whether
    a track is *inside* a region rather than inside its bounding box, which is a
    better answer -- and a spatial index, a point-in-polygon rule and a much
    larger cache to hold it. A rectangle is enough to offer a region, which is
    all this feature does; see `gpx_view.domain.maps.suggestion`.
    """
    if not isinstance(geometry, dict):
        return None
    corners = _extremes(geometry.get("coordinates"), MAX_COORDINATE_DEPTH)
    if corners is None:
        return None
    west, south, east, north = corners
    try:
        return MapBounds(
            min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north
        )
    except ValueError:
        # An outline that leaves the globe is a defect in somebody's index, not
        # a rectangle this build repairs into looking plausible.
        logger.debug("map_catalog.outline_skipped")
        return None


def _extremes(coordinates: object, depth: int) -> tuple[float, float, float, float] | None:
    """Return west, south, east, north over a nested coordinate array.

    Walked rather than collected: the index holds a few million coordinates and
    every one of them is needed exactly once, to widen four numbers.
    """
    if depth < 0 or not isinstance(coordinates, list) or not coordinates:
        return None
    if _is_number(coordinates[0]):
        if len(coordinates) < 2 or not _is_number(coordinates[1]):
            return None
        longitude = float(coordinates[0])
        latitude = float(coordinates[1])
        return (longitude, latitude, longitude, latitude)
    widest: tuple[float, float, float, float] | None = None
    for entry in coordinates:
        nested = _extremes(entry, depth - 1)
        if nested is None:
            continue
        widest = nested if widest is None else _widened(widest, nested)
    return widest


def _widened(
    one: tuple[float, float, float, float], other: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Return the rectangle containing both."""
    return (
        min(one[0], other[0]),
        min(one[1], other[1]),
        max(one[2], other[2]),
        max(one[3], other[3]),
    )


def _is_number(value: object) -> bool:
    """Report whether a JSON value is a coordinate rather than something else.

    ``bool`` is excluded deliberately: it is an ``int`` in Python, and a `true`
    in a coordinate array is malformed input rather than a longitude of one.
    """
    return isinstance(value, int | float) and not isinstance(value, bool)


def _parent_within(region_id: MapRegionId, known: dict[str, CatalogRegion]) -> MapRegionId | None:
    """Return the nearest ancestor the index also holds.

    Walked upwards rather than read from the index's own `parent` property,
    which names a region *id* while the hierarchy this application browses is
    built from paths. Deriving both from one source keeps them from disagreeing
    about who a region's parent is.
    """
    candidate = region_id.parent
    while candidate is not None:
        if candidate.path in known:
            return candidate
        candidate = candidate.parent
    return None


def _country_code(properties: dict[str, Any]) -> str | None:
    """Return the ISO 3166-1 alpha-2 code a region declares, if exactly one."""
    codes = properties.get("iso3166-1:alpha2")
    if isinstance(codes, list) and len(codes) == 1 and isinstance(codes[0], str):
        code = codes[0].strip().upper()
        return code if len(code) == 2 and code.isalpha() else None
    return None
