"""Where the last good provider catalog is kept.

One JSON document per provider under the managed map area. It exists so that a
provider being unreachable is a message rather than an empty screen, and it is
written the same way everything else here is: to a temporary file that is
renamed into place, so an interrupted write cannot leave a half-document that
then fails to parse forever.

Reading it applies the same validation as reading the provider, because a file
in the data directory is not more trustworthy than the network -- it may have
been written by an older build, or by a disk that is failing.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from gpx_view.application.maps import CachedCatalog, CatalogRegion, RemotePackage
from gpx_view.domain.maps import MapRegionId
from gpx_view.infrastructure.private_data import (
    PRIVATE_FILE_MODE,
    create_private_directory,
)

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
MAX_CACHE_BYTES = 16 * 1024 * 1024


class FilesystemMapCatalogCache:
    """A provider catalog cached as one JSON document per provider."""

    def __init__(self, directory: Path) -> None:
        """Bind the cache to its directory. Nothing is created until it is used."""
        self._directory = directory

    def read(self, provider: str) -> CachedCatalog | None:
        """Return the cached catalog, or ``None`` if there is nothing usable."""
        path = self._path_for(provider)
        try:
            if path.stat().st_size > MAX_CACHE_BYTES:
                return None
            document: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return _catalog_of(provider, document)

    def write(self, catalog: CachedCatalog) -> None:
        """Replace the cached catalog atomically."""
        create_private_directory(self._directory)
        payload = json.dumps(_document_of(catalog), separators=(",", ":"))
        temporary = self._directory / f"{uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                PRIVATE_FILE_MODE,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._path_for(catalog.provider))
        except OSError:
            temporary.unlink(missing_ok=True)
            logger.warning("map_catalog.cache_write_failed")

    def _path_for(self, provider: str) -> Path:
        """Return the document one provider's catalog lives in.

        The provider slug is validated by `MapRegionId` before any identity
        exists, and it is a slug of `[a-z0-9-]`. The check is repeated here
        anyway: this is the one function in the cache that turns a string into a
        file name, and a boundary that relies on a caller having checked is not
        a boundary.
        """
        if not provider.replace("-", "").isalnum():
            raise ValueError("provider slug is not a name")
        return self._directory / f"{provider}.json"


def _document_of(catalog: CachedCatalog) -> dict[str, Any]:
    """Render a catalog as the document that is stored."""
    return {
        "version": CACHE_VERSION,
        "provider": catalog.provider,
        "fetched_at": catalog.fetched_at.astimezone(UTC).isoformat(),
        "regions": [
            {
                "id": str(region.region_id),
                "name": region.name,
                "parent": str(region.parent_id) if region.parent_id else None,
                "country": region.country_code,
            }
            for region in catalog.regions
        ],
        "availability": {
            key: None
            if package is None
            else {
                "url": package.url,
                "size": package.size_bytes,
                "updated": package.updated_at.astimezone(UTC).isoformat()
                if package.updated_at
                else None,
            }
            for key, package in catalog.availability.items()
        },
    }


def _catalog_of(provider: str, document: object) -> CachedCatalog | None:
    """Return a validated catalog from a stored document, or ``None``."""
    if not isinstance(document, dict) or document.get("version") != CACHE_VERSION:
        return None
    if document.get("provider") != provider:
        return None
    fetched_at = _instant(document.get("fetched_at"))
    regions = _regions_of(document.get("regions"))
    if fetched_at is None or not regions:
        return None
    return CachedCatalog(
        provider=provider,
        fetched_at=fetched_at,
        regions=regions,
        availability=_availability_of(document.get("availability")),
    )


def _regions_of(raw: object) -> tuple[CatalogRegion, ...]:
    """Return the validated regions a stored document holds."""
    if not isinstance(raw, list):
        return ()
    regions = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        try:
            region_id = MapRegionId.parse(str(entry.get("id")))
            parent = MapRegionId.parse(str(entry["parent"])) if entry.get("parent") else None
        except ValueError:
            continue
        country = entry.get("country")
        regions.append(
            CatalogRegion(
                region_id=region_id,
                name=entry["name"],
                parent_id=parent,
                country_code=country if isinstance(country, str) else None,
            )
        )
    return tuple(regions)


def _availability_of(raw: object) -> dict[str, RemotePackage | None]:
    """Return the validated availability answers a stored document holds."""
    if not isinstance(raw, dict):
        return {}
    availability: dict[str, RemotePackage | None] = {}
    for key, value in raw.items():
        try:
            region_id = MapRegionId.parse(str(key))
        except ValueError:
            continue
        if value is None:
            availability[str(region_id)] = None
            continue
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            continue
        size = value.get("size")
        availability[str(region_id)] = RemotePackage(
            region_id=region_id,
            url=value["url"],
            size_bytes=size if isinstance(size, int) and size >= 0 else None,
            updated_at=_instant(value.get("updated")),
        )
    return availability


def _instant(value: object) -> datetime | None:
    """Return a stored instant, or ``None`` when it is unreadable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
