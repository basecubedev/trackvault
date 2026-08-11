"""Reading a downloaded MBTiles package, and refusing one that is not what it claims.

A package that arrived over the internet is a **foreign SQLite database**, and
that is a sentence worth pausing on. SQLite is a programmable engine: a file can
carry views, triggers and expressions, and opening one the way the archive opens
its own would be handing a stranger's file the same trust as the archive's own
data. So:

```
opened read-only, through a URI with mode=ro and immutable=1
extension loading refused explicitly
only fixed, parameterised statements -- nothing built from the file's content
no ATTACH, no writes, no journal, never the archive's own connection
```

`immutable=1` is not an optimisation here. It tells SQLite the file will not
change underneath it, which is true by construction -- the name *is* the content
hash -- and it stops the driver from trying to create journal files beside a
package it must never write to.

Validation is the other half. An extension proves nothing, a `Content-Type`
proves nothing, and a file that opens as SQLite proves only that. What is
checked is the container, the metadata a reader will be shown, and the tile
vocabulary the style is written against -- because a package whose layers are
named differently renders as a blank map, which is indistinguishable from a
broken installation.
"""

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path

from gpx_view.application.maps import (
    InspectedPackage,
    MapErrorCode,
    MapOperationError,
)
from gpx_view.domain.maps import (
    AttributionLink,
    MapAttribution,
    MapBounds,
    MapPackageFormat,
    MapRegionId,
    MapTileSchema,
)
from gpx_view.infrastructure.maps.storage import FilesystemMapPackageStorage

logger = logging.getLogger(__name__)

SQLITE_MAGIC = b"SQLite format 3\x00"

SHORTBREAD_SCHEMA = "shortbread"
SHORTBREAD_SIGNATURE = frozenset(
    {"boundaries", "land", "ocean", "place_labels", "street_labels", "streets", "water_polygons"}
)
"""Layers a package must carry to be servable by this build's styles.

Not the whole Shortbread vocabulary -- a package legitimately omits `pois` for a
region that has none. These seven are the ones both themes draw, so their
absence is the difference between a map and an empty rectangle.
"""

DEFAULT_SHORTBREAD_VERSION = "1.0"

MAX_METADATA_VALUE_BYTES = 256 * 1024
"""How much of one metadata value is read before it is refused.

The `json` value legitimately runs to a few kilobytes. A megabyte of it is a
package doing something this build does not need to accommodate.
"""

MAX_ZOOM = 24

OSM_ATTRIBUTION_TEXT = "Map data © OpenStreetMap contributors"

LICENCE_IDENTIFIERS: Mapping[str, str] = {
    "open database license 1.0": "ODbL-1.0",
    "open database license 1.0 (odbl)": "ODbL-1.0",
    "odbl 1.0": "ODbL-1.0",
    "odbl-1.0": "ODbL-1.0",
    "odc-odbl": "ODbL-1.0",
}
"""Licence names this build recognises, mapped onto a stable identifier.

Deliberately small. A name that is not in here is kept verbatim as both the name
and the identifier: the package still installs and still shows what it says,
because refusing a licence merely because it is unfamiliar would be this build
overruling the data's own statement. What is refused is a package that states
*nothing*.
"""

LICENCE_LINKS: Mapping[str, str] = {
    "ODbL-1.0": "https://opendatacommons.org/licenses/odbl/1-0/",
}

OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"


def _connect(path: Path) -> sqlite3.Connection:
    """Open a foreign package read-only, with nothing else enabled."""
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    # Off by default in a stock CPython, and stated anyway: a default is a
    # property of the build somebody may change, and this is a property of the
    # decision. A build compiled without extension support has no method at all.
    with suppress(AttributeError):
        connection.enable_load_extension(False)
    connection.execute("PRAGMA query_only = 1")
    connection.execute("PRAGMA trusted_schema = 0")
    return connection


class MbtilesPackageInspector:
    """Validates a published package and extracts the metadata it will be shown by."""

    def __init__(self, storage: FilesystemMapPackageStorage) -> None:
        """Wire the inspector to the managed storage it reads from."""
        self._storage = storage

    def inspect(self, region_id: MapRegionId, content_sha256: str) -> InspectedPackage:
        """Return what a published package is, or refuse it by name."""
        path = self._storage.package_path(region_id, content_sha256)
        self._require_container(path)
        connection = _connect(path)
        try:
            metadata = self._metadata(connection)
            self._require_tables(connection)
            bounds = self._bounds(metadata)
            layers = self._layers(metadata)
            min_zoom, max_zoom = self._zoom_range(connection, metadata)
            tile_count = self._tile_count(connection)
        except sqlite3.DatabaseError as error:
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_INVALID, "unreadable container"
            ) from error
        finally:
            connection.close()

        if tile_count == 0:
            raise MapOperationError(MapErrorCode.MAP_PACKAGE_EMPTY)
        if metadata.get("format", "").strip().lower() != "pbf":
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_SCHEMA_UNSUPPORTED, "tiles are not vector tiles"
            )
        if not layers >= SHORTBREAD_SIGNATURE:
            missing = sorted(SHORTBREAD_SIGNATURE - layers)
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_SCHEMA_UNSUPPORTED,
                f"missing layers: {', '.join(missing)}",
            )

        return InspectedPackage(
            format=MapPackageFormat.MBTILES,
            tile_schema=MapTileSchema(
                name=SHORTBREAD_SCHEMA,
                version=_schema_version(metadata),
            ),
            bounds=bounds,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            tile_count=tile_count,
            attribution=_attribution_of(metadata),
            dataset_version=metadata.get("version") or None,
        )

    def _require_container(self, path: Path) -> None:
        """Refuse anything whose first bytes are not a SQLite database."""
        try:
            with path.open("rb") as handle:
                header = handle.read(len(SQLITE_MAGIC))
        except OSError as error:
            raise MapOperationError(MapErrorCode.MAP_STORAGE_FAILED) from error
        if header != SQLITE_MAGIC:
            raise MapOperationError(MapErrorCode.MAP_PACKAGE_INVALID, "not a database container")

    def _require_tables(self, connection: sqlite3.Connection) -> None:
        """Refuse a database that is not an MBTiles archive."""
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        missing = {"tiles", "metadata"} - names
        if missing:
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_INVALID, f"missing: {', '.join(sorted(missing))}"
            )

    def _metadata(self, connection: sqlite3.Connection) -> dict[str, str]:
        """Return the package's metadata table, bounded."""
        rows = connection.execute(
            "SELECT name, value FROM metadata WHERE typeof(name) = 'text' LIMIT 200"
        )
        metadata: dict[str, str] = {}
        for name, value in rows:
            if not isinstance(value, str) or len(value) > MAX_METADATA_VALUE_BYTES:
                continue
            metadata[str(name).strip().lower()] = value
        return metadata

    def _bounds(self, metadata: Mapping[str, str]) -> MapBounds:
        """Return the rectangle the package declares, or refuse it."""
        raw = metadata.get("bounds", "")
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 4:
            raise MapOperationError(MapErrorCode.MAP_PACKAGE_INVALID, "bounds are not four numbers")
        try:
            west, south, east, north = (float(part) for part in parts)
        except ValueError as error:
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_INVALID, "bounds are not four numbers"
            ) from error
        try:
            return MapBounds(
                min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north
            )
        except ValueError as error:
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_INVALID, "bounds are off the globe"
            ) from error

    def _layers(self, metadata: Mapping[str, str]) -> frozenset[str]:
        """Return the vector layer names the package declares."""
        raw = metadata.get("json")
        if not raw:
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_SCHEMA_UNSUPPORTED, "no layer description"
            )
        try:
            document: object = json.loads(raw)
        except ValueError as error:
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_SCHEMA_UNSUPPORTED, "unreadable layer description"
            ) from error
        if not isinstance(document, dict):
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_SCHEMA_UNSUPPORTED, "unreadable layer description"
            )
        layers = document.get("vector_layers")
        if not isinstance(layers, list):
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_SCHEMA_UNSUPPORTED, "unreadable layer description"
            )
        return frozenset(
            str(layer["id"])
            for layer in layers
            if isinstance(layer, dict) and isinstance(layer.get("id"), str)
        )

    def _zoom_range(
        self, connection: sqlite3.Connection, metadata: Mapping[str, str]
    ) -> tuple[int, int]:
        """Return the zoom levels the package actually holds tiles for.

        Read from the tiles rather than trusted from the metadata. A declared
        `maxzoom` a package does not deliver would make MapLibre request tiles
        that are never there, and the map would stop drawing above some zoom
        level for no visible reason.
        """
        row = connection.execute("SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles").fetchone()
        low, high = row if row else (None, None)
        if low is None or high is None:
            return _declared_zoom(metadata)
        try:
            min_zoom, max_zoom = int(low), int(high)
        except (TypeError, ValueError) as error:
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_INVALID, "unreadable zoom levels"
            ) from error
        if not 0 <= min_zoom <= max_zoom <= MAX_ZOOM:
            raise MapOperationError(MapErrorCode.MAP_PACKAGE_INVALID, "impossible zoom levels")
        return min_zoom, max_zoom

    def _tile_count(self, connection: sqlite3.Connection) -> int:
        """Return whether the package holds tiles at all, cheaply.

        `EXISTS` rather than `COUNT(*)`: counting the rows of a multi-gigabyte
        package to find out whether it has any is a full scan to answer a
        yes-or-no question.
        """
        row = connection.execute("SELECT EXISTS(SELECT 1 FROM tiles)").fetchone()
        return 1 if row and row[0] else 0


def _declared_zoom(metadata: Mapping[str, str]) -> tuple[int, int]:
    """Return the zoom range the metadata declares, refusing nonsense."""
    try:
        min_zoom = int(metadata.get("minzoom", "0"))
        max_zoom = int(metadata.get("maxzoom", "14"))
    except ValueError as error:
        raise MapOperationError(
            MapErrorCode.MAP_PACKAGE_INVALID, "unreadable zoom levels"
        ) from error
    if not 0 <= min_zoom <= max_zoom <= MAX_ZOOM:
        raise MapOperationError(MapErrorCode.MAP_PACKAGE_INVALID, "impossible zoom levels")
    return min_zoom, max_zoom


def _schema_version(metadata: Mapping[str, str]) -> str:
    """Return the schema version the package declares.

    The layer signature is the authority for *which* schema this is; the version
    is what the package says about it. They are recorded separately so a future
    Shortbread 1.1 that adds a layer is a version this build can notice rather
    than a silent difference.
    """
    name = metadata.get("name", "").strip().lower()
    version = metadata.get("version", "").strip()
    if name == SHORTBREAD_SCHEMA and version:
        return version
    return DEFAULT_SHORTBREAD_VERSION


def _attribution_of(metadata: Mapping[str, str]) -> MapAttribution:
    """Return what the package says about its data, or refuse to install it.

    > A package that states no licence and no author does not install.

    There is no default. A hard-coded "© OpenStreetMap contributors" would be
    this build asserting something about a file it did not read, and the whole
    point of storing attribution as data is that it comes from the package.
    """
    author = metadata.get("author", "").strip()
    licence = metadata.get("license", "").strip()
    if not author or not licence:
        raise MapOperationError(MapErrorCode.MAP_PACKAGE_LICENCE_MISSING)

    identifier = LICENCE_IDENTIFIERS.get(licence.lower(), licence)
    data_owner, _, provider = (part.strip() for part in author.partition(","))
    links = [AttributionLink(label="OpenStreetMap", url=OSM_COPYRIGHT_URL)]
    if licence_url := LICENCE_LINKS.get(identifier):
        links.append(AttributionLink(label=identifier, url=licence_url))

    try:
        return MapAttribution(
            data_owner=data_owner or author,
            provider=provider or author,
            license_identifier=identifier,
            license_name=licence,
            required_text=_required_text(data_owner or author),
            links=tuple(links),
        )
    except ValueError as error:
        raise MapOperationError(
            MapErrorCode.MAP_PACKAGE_LICENCE_MISSING, "unusable metadata"
        ) from error


def _required_text(data_owner: str) -> str:
    """Return the one line that must be visible while the package renders."""
    if "openstreetmap" in data_owner.lower():
        return OSM_ATTRIBUTION_TEXT
    return f"Map data © {data_owner}"


class MbtilesTileReader:
    """Serves single tiles out of installed packages.

    One connection per thread per package, held open. Opening a database for
    every tile would pay the file-open, header-read and page-cache cost sixty
    times for one screenful; keeping one connection per package amortises it and
    stays correct because SQLite connections are not shared across threads.

    Nothing here touches the archive's own database. A tile read cannot block a
    track query, because the two are different files with different connections.
    """

    def __init__(self, storage: FilesystemMapPackageStorage) -> None:
        """Wire the reader to the managed storage."""
        self._storage = storage
        self._local = threading.local()

    def read(
        self, region_id: MapRegionId, content_sha256: str, zoom: int, column: int, row: int
    ) -> bytes | None:
        """Return one tile's stored bytes, or ``None`` where there is no tile.

        The row is converted from the XYZ addressing MapLibre uses to the TMS
        addressing MBTiles stores, which is the one arithmetic detail this whole
        endpoint turns on: getting it wrong produces a map that renders
        perfectly and is upside down.
        """
        connection = self._connection_for(region_id, content_sha256)
        if connection is None:
            return None
        flipped = (1 << zoom) - 1 - row
        try:
            found = connection.execute(
                "SELECT tile_data FROM tiles "
                "WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?",
                (zoom, column, flipped),
            ).fetchone()
        except sqlite3.DatabaseError as error:
            raise MapOperationError(MapErrorCode.MAP_TILE_READ_FAILED) from error
        if found is None or not isinstance(found[0], bytes):
            return None
        return found[0]

    def close(self) -> None:
        """Close this thread's connections. Used by tests and shutdown."""
        for connection in getattr(self._local, "connections", {}).values():
            connection.close()
        self._local.connections = {}

    def _connection_for(
        self, region_id: MapRegionId, content_sha256: str
    ) -> sqlite3.Connection | None:
        """Return this thread's connection to one package, opening it if needed."""
        connections: dict[str, sqlite3.Connection] = getattr(self._local, "connections", {})
        if not connections:
            self._local.connections = connections
        if existing := connections.get(content_sha256):
            return existing
        path = self._storage.package_path(region_id, content_sha256)
        if not path.is_file():
            return None
        try:
            connection = _connect(path)
        except sqlite3.DatabaseError:
            logger.warning("map_tiles.open_failed")
            return None
        connections[content_sha256] = connection
        return connection


def digest_of(path: Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    """Return the SHA-256 of a file. Used by tests and by integrity repair."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()
