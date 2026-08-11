"""The Geofabrik catalog adapter.

Everything this provider knows about the world lives here: the index URL, the
package URL convention, the hosts it is allowed to talk to and its display
name. The application above knows a `region_id` and nothing else, which is what
makes "an API caller cannot choose the host this server contacts" structural
rather than a promise.

**Where the region hierarchy comes from.** Geofabrik publishes a machine-readable
index, `index-v1-nogeom.json`, holding every region with its display name, its
parent and its download URLs. That index is the source of the tree -- an HTML
page is a document for people, and parsing one as an API means a redesign breaks
this application.

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
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from gpx_view.application.maps import (
    CatalogRegion,
    MapErrorCode,
    MapOperationError,
    RemotePackage,
    TransferOutcome,
)
from gpx_view.domain.maps import MapRegionId
from gpx_view.infrastructure.maps.transfer import HttpTransfer, user_agent

logger = logging.getLogger(__name__)

PROVIDER_SLUG = "geofabrik"
PROVIDER_DISPLAY_NAME = "Geofabrik GmbH"

DOWNLOAD_HOST = "download.geofabrik.de"
DOWNLOAD_BASE = f"https://{DOWNLOAD_HOST}/"
INDEX_URL = f"{DOWNLOAD_BASE}index-v1-nogeom.json"
ALLOWED_HOSTS = frozenset({DOWNLOAD_HOST})

PBF_SUFFIX = "-latest.osm.pbf"
PACKAGE_SUFFIX = "-shortbread-1.0.mbtiles"

MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_REGIONS = 5000
MAX_NAME_LENGTH = 120


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
        body = self._transfer.fetch(self._index_url, limit_bytes=MAX_INDEX_BYTES)
        return self._parse_index(body)

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

    def _parse_index(self, body: bytes) -> tuple[CatalogRegion, ...]:
        """Turn the provider's index into validated catalog regions."""
        try:
            document: Any = json.loads(body)
        except ValueError as error:
            raise MapOperationError(MapErrorCode.MAP_CATALOG_INVALID, "unreadable index") from error
        if not isinstance(document, dict) or not isinstance(document.get("features"), list):
            raise MapOperationError(MapErrorCode.MAP_CATALOG_INVALID, "unexpected index shape")

        features = document["features"][:MAX_REGIONS]
        found: dict[str, tuple[MapRegionId, str, str | None]] = {}
        for feature in features:
            parsed = self._region_of(feature)
            if parsed is not None:
                found[parsed[0].path] = parsed
        if not found:
            raise MapOperationError(MapErrorCode.MAP_CATALOG_INVALID, "index holds no regions")

        return tuple(
            CatalogRegion(
                region_id=region_id,
                name=name,
                parent_id=_parent_within(region_id, found),
                country_code=country,
            )
            for region_id, name, country in found.values()
        )

    def _region_of(self, feature: object) -> tuple[MapRegionId, str, str | None] | None:
        """Return one validated region, or ``None`` for an entry to skip."""
        if not isinstance(feature, dict):
            return None
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            return None
        urls = properties.get("urls")
        name = properties.get("name")
        if not isinstance(urls, dict) or not isinstance(name, str) or not name.strip():
            return None
        path = self._path_of(urls.get("pbf"))
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
        return region_id, name.strip()[:MAX_NAME_LENGTH], _country_code(properties)

    def _path_of(self, url: object) -> str | None:
        """Return the region path a download URL names, if it names one safely."""
        if not isinstance(url, str) or not url.endswith(PBF_SUFFIX):
            return None
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() != DOWNLOAD_HOST:
            return None
        path = parts.path.lstrip("/").removesuffix(PBF_SUFFIX)
        return path or None


def _parent_within(
    region_id: MapRegionId, known: dict[str, tuple[MapRegionId, str, str | None]]
) -> MapRegionId | None:
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
