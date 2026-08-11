"""Building a real, tiny map package the tests can install and read.

A fixture that is a stub proves nothing about a format. So this builds an actual
Shortbread-shaped MBTiles archive: gzipped Mapbox vector tiles with real
geometry, in a SQLite container with the metadata the inspector reads. It is a
few kilobytes, it is generated at test time, and no downloaded map ever enters
the repository.

The tile encoder is written out by hand rather than pulled in as a dependency.
The specification is small, stable and public, and a test suite that can produce
its own tiles can also produce a *deliberately broken* one -- which is most of
what the validation tests need.

Everything here is synthetic. The geometry is a handful of straight lines and
rectangles at coordinates chosen for the test, so no real map data, and
certainly no private track, is copied into this repository.
"""

from __future__ import annotations

import gzip
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

EXTENT = 4096
"""The coordinate space one tile spans, as every Shortbread package uses."""

MOVE_TO = 1
LINE_TO = 2
CLOSE_PATH = 7

POINT = 1
LINESTRING = 2
POLYGON = 3

SHORTBREAD_LAYERS = (
    "boundaries",
    "land",
    "ocean",
    "place_labels",
    "street_labels",
    "streets",
    "water_polygons",
)
"""The layers the inspector requires. Every fixture package carries all of them."""

DEFAULT_LICENCE = "Open Database License 1.0"
DEFAULT_AUTHOR = "OpenStreetMap contributors, GPX-View test fixtures"


@dataclass(slots=True)
class Feature:
    """One feature: its geometry type, its points and its attributes."""

    kind: int
    points: Sequence[tuple[int, int]]
    attributes: dict[str, str] = field(default_factory=dict)


def build_package(
    path: Path,
    *,
    bounds: tuple[float, float, float, float],
    min_zoom: int = 0,
    max_zoom: int = 6,
    place_name: str = "Fixtureberg",
    author: str = DEFAULT_AUTHOR,
    licence: str | None = DEFAULT_LICENCE,
    layers: Sequence[str] = SHORTBREAD_LAYERS,
    with_tiles: bool = True,
) -> Path:
    """Write a valid Shortbread-shaped MBTiles package covering ``bounds``.

    Args:
        path: Where to write it.
        bounds: `(west, south, east, north)` in degrees. Tiles are generated for
            every zoom in range that touches the rectangle.
        min_zoom: Lowest zoom level to generate.
        max_zoom: Highest zoom level to generate.
        place_name: The label the `place_labels` layer carries, so a test can
            assert that a *specific* name reached the browser.
        author: What the `author` metadata says.
        licence: What the `license` metadata says. ``None`` omits it, which is
            how the "a package without a licence does not install" test builds
            its input.
        layers: Which layers each tile carries. Shortened by the tests that
            check an unsupported schema is refused by name.
        with_tiles: ``False`` writes the container and its metadata and no
            tiles, which is what an empty package looks like.

    Returns:
        The path that was written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Rebuilt from scratch every time. A test that publishes a second version of
    # a region wants different bytes, not a container with two of everything.
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE tiles ("
            "zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)"
        )
        connection.execute("CREATE TABLE metadata (name TEXT, value TEXT)")

        if with_tiles:
            for zoom, column, row in _tiles_covering(bounds, min_zoom, max_zoom):
                payload = gzip.compress(_tile(layers, place_name), mtime=0)
                connection.execute(
                    "INSERT INTO tiles VALUES (?, ?, ?, ?)", (zoom, column, row, payload)
                )

        entries = {
            "name": "Shortbread",
            "type": "baselayer",
            "version": "1.0",
            "description": "Synthetic Shortbread package for the GPX-View test suite",
            "format": "pbf",
            "minzoom": str(min_zoom),
            "maxzoom": str(max_zoom),
            "json": _layer_description(layers),
            "author": author,
            "bounds": ",".join(f"{value:g}" for value in bounds),
        }
        if licence is not None:
            entries["license"] = licence
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(entries.items()))
        connection.commit()
    finally:
        connection.close()
    return path


def _layer_description(layers: Sequence[str]) -> str:
    """Return the `json` metadata value describing the vector layers."""
    described = ",".join(
        f'{{"id":"{name}","fields":{{"kind":"String","name":"String"}},"minzoom":0,"maxzoom":14}}'
        for name in layers
    )
    return f'{{"vector_layers":[{described}]}}'


def _tiles_covering(
    bounds: tuple[float, float, float, float], min_zoom: int, max_zoom: int
) -> list[tuple[int, int, int]]:
    """Return every `(zoom, column, tms_row)` a rectangle touches.

    The row is written in the TMS orientation MBTiles stores, which is the one
    detail a tile endpoint has to convert and the one this fixture exists to
    let a test catch.
    """
    west, south, east, north = bounds
    covering = []
    for zoom in range(min_zoom, max_zoom + 1):
        span = 1 << zoom
        for column in range(_column(west, zoom), _column(east, zoom) + 1):
            for row in range(_row(north, zoom), _row(south, zoom) + 1):
                covering.append((zoom, column, span - 1 - row))
    return covering


def _column(longitude: float, zoom: int) -> int:
    """Return the tile column a longitude falls in."""
    span = 1 << zoom
    return max(0, min(span - 1, int((longitude + 180.0) / 360.0 * span)))


def _row(latitude: float, zoom: int) -> int:
    """Return the XYZ tile row a latitude falls in."""
    span = 1 << zoom
    radians = math.radians(max(-85.05, min(85.05, latitude)))
    fraction = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0
    return max(0, min(span - 1, int(fraction * span)))


def _tile(layers: Sequence[str], place_name: str) -> bytes:
    """Return one vector tile carrying a recognisable feature in every layer."""
    encoded = b""
    for name in layers:
        encoded += _bytes(3, _layer(name, _features_for(name, place_name)))
    return encoded


def _features_for(name: str, place_name: str) -> list[Feature]:
    """Return the geometry one layer holds, shaped like what it means."""
    if name in {"land", "ocean", "water_polygons"}:
        return [Feature(POLYGON, _rectangle(256, 256, 3840, 3840), {"kind": name})]
    if name in {"streets", "boundaries"}:
        return [
            Feature(
                LINESTRING,
                ((256, 2048), (2048, 1024), (3840, 2048)),
                {"kind": "residential" if name == "streets" else "2"},
            )
        ]
    if name == "street_labels":
        return [
            Feature(
                LINESTRING,
                ((512, 2048), (3584, 2048)),
                {"kind": "residential", "name": "Fixtureweg"},
            )
        ]
    return [Feature(POINT, ((2048, 2048),), {"kind": "town", "name": place_name})]


def _rectangle(left: int, top: int, right: int, bottom: int) -> tuple[tuple[int, int], ...]:
    """Return a closed rectangle, wound the way the specification requires."""
    return ((left, top), (right, top), (right, bottom), (left, bottom))


def _layer(name: str, features: Sequence[Feature]) -> bytes:
    """Encode one vector tile layer."""
    keys: list[str] = []
    values: list[str] = []
    encoded_features = b""
    for feature in features:
        tags: list[int] = []
        for key, value in feature.attributes.items():
            if key not in keys:
                keys.append(key)
            if value not in values:
                values.append(value)
            tags.extend((keys.index(key), values.index(value)))
        body = _packed(2, tags) if tags else b""
        body += _varint_field(3, feature.kind)
        body += _packed(4, _geometry(feature.kind, feature.points))
        encoded_features += _bytes(2, body)

    payload = _string(1, name) + encoded_features
    for key in keys:
        payload += _string(3, key)
    for value in values:
        payload += _bytes(4, _string(1, value))
    payload += _varint_field(5, EXTENT) + _varint_field(15, 2)
    return payload


def _geometry(kind: int, points: Sequence[tuple[int, int]]) -> list[int]:
    """Encode a geometry as the command and parameter integers of the format."""
    commands = [(MOVE_TO & 0x7) | (1 << 3), _zigzag(points[0][0]), _zigzag(points[0][1])]
    if kind == POINT:
        return commands
    rest = points[1:]
    commands.append((LINE_TO & 0x7) | (len(rest) << 3))
    cursor = points[0]
    for point in rest:
        commands.extend((_zigzag(point[0] - cursor[0]), _zigzag(point[1] - cursor[1])))
        cursor = point
    if kind == POLYGON:
        commands.append((CLOSE_PATH & 0x7) | (1 << 3))
    return commands


def _zigzag(value: int) -> int:
    """Return a signed integer in the zigzag encoding the format uses."""
    return (value << 1) ^ (value >> 31)


def _varint(value: int) -> bytes:
    """Return one unsigned varint."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _key(field_number: int, wire: int) -> bytes:
    """Return a field tag."""
    return _varint((field_number << 3) | wire)


def _varint_field(field_number: int, value: int) -> bytes:
    """Return a varint field."""
    return _key(field_number, 0) + _varint(value)


def _bytes(field_number: int, value: bytes) -> bytes:
    """Return a length-delimited field."""
    return _key(field_number, 2) + _varint(len(value)) + value


def _string(field_number: int, value: str) -> bytes:
    """Return a length-delimited string field."""
    return _bytes(field_number, value.encode("utf-8"))


def _packed(field_number: int, values: Sequence[int]) -> bytes:
    """Return a packed repeated varint field."""
    return _bytes(field_number, b"".join(_varint(value) for value in values))


def layer_names(tile: bytes) -> tuple[str, ...]:
    """Return the layer names a vector tile carries. Used to check a delivery."""
    raw = gzip.decompress(tile) if tile[:2] == b"\x1f\x8b" else tile
    names = []
    index = 0
    while index < len(raw):
        key, index = _read_varint(raw, index)
        number, wire = key >> 3, key & 7
        if wire == 2:
            length, index = _read_varint(raw, index)
            chunk = raw[index : index + length]
            index += length
            if number == 3:
                names.append(_layer_name(chunk))
        elif wire == 0:
            _, index = _read_varint(raw, index)
        else:  # pragma: no cover - the encoder above writes no other wire type
            break
    return tuple(names)


def _layer_name(chunk: bytes) -> str:
    """Return one encoded layer's name."""
    index = 0
    while index < len(chunk):
        key, index = _read_varint(chunk, index)
        number, wire = key >> 3, key & 7
        if wire == 2:
            length, index = _read_varint(chunk, index)
            if number == 1:
                return chunk[index : index + length].decode("utf-8")
            index += length
        elif wire == 0:
            _, index = _read_varint(chunk, index)
        else:  # pragma: no cover
            break
    return ""


def _read_varint(buffer: bytes, index: int) -> tuple[int, int]:
    """Return the varint at ``index`` and the position after it."""
    result = shift = 0
    while True:
        byte = buffer[index]
        index += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, index
