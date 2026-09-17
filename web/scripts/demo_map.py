"""Draw the demo island as an offline map package and install it.

The documentation needs a map behind its screenshots, and it must not be a real
one: shipping a downloaded Geofabrik extract would put a hundred megabytes of
somebody else's data in this repository to illustrate a feature. So the island
`demo_region` describes is encoded as vector tiles here, through the same
Shortbread-shaped writer the test suite uses, and installed through the real
pipeline -- storage, inspector, repository, atomic publish. What the screenshots
show is therefore the actual map feature, over a place that does not exist.

The package states what it is. Its attribution says the data is synthetic, so a
screenshot of the credits page does not claim OpenStreetMap for an invented
coastline.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_region as region_model  # noqa: E402

from support.map_packages import EXTENT, LINESTRING, POINT, POLYGON, Feature  # noqa: E402

REGION_ID = "demo:nordvik"
REGION_NAME = "Nordvik (demo)"
PROVIDER_SLUG = "demo"
PROVIDER_NAME = "TrackVault demo data"
AUTHOR = "TrackVault demo data (synthetic), TrackVault documentation"
LICENCE = "CC0 1.0 Universal (public domain dedication)"

MIN_ZOOM = 4
MAX_ZOOM = 14
"""Zoom 14 is where the map style starts drawing street names.

Below 4 the island is smaller than a pixel and above 14 MapLibre magnifies the
last level it has, which is what every vector map does.
"""

_MARGIN = 0.20
"""Degrees of sea kept around the island.

Generous on purpose. A package covers a rectangle and MapLibre asks for no tile
outside it, so a viewport wider than the package shows the style's background
colour where the sea should be -- which is correct behaviour and looks like a
defect in a screenshot. The island is 0.6 degrees across and the widest picture
the documentation takes is the whole of it, so the sea reaches a third of the
island's width past every edge.
"""


def bounds() -> tuple[float, float, float, float]:
    """Return `(west, south, east, north)` for the package, in degrees."""
    ring = region_model.coastline()
    longitudes = [longitude for longitude, _ in ring]
    latitudes = [latitude for _, latitude in ring]
    return (
        min(longitudes) - _MARGIN,
        min(latitudes) - _MARGIN,
        max(longitudes) + _MARGIN,
        max(latitudes) + _MARGIN,
    )


def _project(point: region_model.Point, zoom: int, column: int, row: int) -> tuple[int, int]:
    """Return one position in the tile-local coordinates the format uses.

    A coordinate outside `0..EXTENT` is normal and deliberate: a feature that
    leaves the tile is written whole and clipped by the renderer, which is what
    keeps a coastline continuous across a tile boundary without this script
    having to implement polygon clipping.
    """
    longitude, latitude = point
    span = 1 << zoom
    x = (longitude + 180.0) / 360.0 * span
    y = (1.0 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2.0 * span
    return round((x - column) * EXTENT), round((y - row) * EXTENT)


def _tile_bounds(zoom: int, column: int, row: int) -> tuple[float, float, float, float]:
    """Return one tile's `(west, south, east, north)` in degrees."""
    span = 1 << zoom
    west = column / span * 360.0 - 180.0
    east = (column + 1) / span * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * row / span))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (row + 1) / span))))
    return west, south, east, north


def _overlaps(
    shape: list[region_model.Point], tile: tuple[float, float, float, float], pad: float
) -> bool:
    """Return whether a shape's own rectangle reaches into a padded tile.

    A rectangle test rather than a real intersection: it is allowed to say yes
    too often -- the cost is a feature written into a tile it does not visibly
    cross -- and must never say no when the shape is there.
    """
    west, south, east, north = tile
    longitudes = [longitude for longitude, _ in shape]
    latitudes = [latitude for _, latitude in shape]
    return (
        min(longitudes) <= east + pad
        and max(longitudes) >= west - pad
        and min(latitudes) <= north + pad
        and max(latitudes) >= south - pad
    )


def _degrees(shape: list[region_model.Metres]) -> list[region_model.Point]:
    """Return a shape from the local metric frame in degrees."""
    return [region_model.to_degrees(position) for position in shape]


_LAND_AREAS = [(kind, _degrees(shape)) for kind, shape in region_model.LAND_AREAS]
_WATER_AREAS = [_degrees(shape) for shape in region_model.WATER_AREAS]
_WATER_LINES = [(name, _degrees(shape)) for name, shape in region_model.WATER_LINES]
_STREETS = [
    (name, kind, _degrees(shape))
    for name, kind, shape in (*region_model.STREETS, *region_model.PATHS)
]


_SEA_REACH = 60_000.0
"""How far out to sea the water is drawn, in metres from the island's centre.

Comfortably past the far corner of the package's own rectangle, so the sea never
runs out before the tiles do.
"""


def _offshore(degree: int) -> region_model.Metres:
    """Return the far end of one sea wedge, well outside the package."""
    return (
        _SEA_REACH * math.cos(math.radians(degree)),
        _SEA_REACH * math.sin(math.radians(degree)),
    )


_SEA_WEDGES = [
    _degrees(
        [
            region_model.at_bearing(math.radians(degree), 0.0),
            region_model.at_bearing(math.radians(degree + 1), 0.0),
            _offshore(degree + 1),
            _offshore(degree),
        ]
    )
    for degree in range(360)
]
"""The sea, as 360 quadrilaterals standing on the shore and reaching outwards.

The obvious shape for "everything except the island" is one rectangle with the
island cut out of it as a hole -- and it does not survive being cut into tiles.
A renderer clips each ring to the tile it is drawing, and an island-sized hole
around a tile that sits well inside it is clipped away entirely, leaving the
rectangle to flood the land with water.

Every wedge here is instead a small, simple quadrilateral that clips to a
quadrilateral. Neighbours share their edges exactly, because both are computed
from the same `coast_radius` at the same bearing, so the water meets the shore
along the coastline itself and meets itself invisibly.
"""


def _ring(
    shape: list[region_model.Point], zoom: int, column: int, row: int, *, clockwise: bool
) -> list[tuple[int, int]]:
    """Return a closed ring in tile coordinates, wound as the format requires.

    The specification decides what is a hole from the sign of a ring's area, so
    the outer ring is wound one way and every hole the other. This is the only
    place that has an opinion about which.
    """
    projected = [_project(point, zoom, column, row) for point in shape]
    area = sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(projected, projected[1:] + projected[:1], strict=True)
    )
    if (area > 0) != clockwise:
        projected.reverse()
    return projected


def features(layer: str, zoom: int, column: int, row: int) -> list[Feature]:
    """Return what one layer draws in one tile."""
    tile = _tile_bounds(zoom, column, row)
    pad = (tile[2] - tile[0]) * 0.5
    if layer == "ocean":
        return [
            Feature(POLYGON, _ring(wedge, zoom, column, row, clockwise=True), {"kind": "ocean"})
            for wedge in _SEA_WEDGES
            if _overlaps(wedge, tile, pad)
        ]
    if layer == "land":
        return [
            Feature(POLYGON, _ring(shape, zoom, column, row, clockwise=True), {"kind": kind})
            for kind, shape in _LAND_AREAS
            if _overlaps(shape, tile, pad)
        ]
    if layer == "water_polygons":
        return [
            Feature(POLYGON, _ring(shape, zoom, column, row, clockwise=True), {"kind": "water"})
            for shape in _WATER_AREAS
            if _overlaps(shape, tile, pad)
        ]
    if layer == "water_lines":
        return [
            Feature(
                LINESTRING,
                [_project(point, zoom, column, row) for point in shape],
                {"kind": "river", "name": name},
            )
            for name, shape in _WATER_LINES
            if _overlaps(shape, tile, pad)
        ]
    if layer == "streets":
        return [
            Feature(
                LINESTRING,
                [_project(point, zoom, column, row) for point in shape],
                {"kind": kind, "name": name},
            )
            for name, kind, shape in _STREETS
            if _overlaps(shape, tile, pad)
        ]
    if layer == "street_labels":
        return [
            Feature(
                LINESTRING,
                [_project(point, zoom, column, row) for point in shape],
                {"kind": kind, "name": name},
            )
            for name, kind, shape in _STREETS
            if _overlaps(shape, tile, 0.0)
        ]
    if layer == "place_labels":
        return _places(tile, zoom, column, row)
    return []


def _places(
    tile: tuple[float, float, float, float], zoom: int, column: int, row: int
) -> list[Feature]:
    """Return the labels one tile carries, thinned out as the map zooms out."""
    drawn = []
    for place in region_model.PLACES:
        if place.kind != "town" and zoom < 9:
            continue
        if place.kind == "peak" and zoom < 11:
            continue
        point = region_model.to_degrees(place.at)
        west, south, east, north = tile
        if not (west <= point[0] <= east and south <= point[1] <= north):
            continue
        drawn.append(
            Feature(
                POINT,
                [_project(point, zoom, column, row)],
                {"kind": place.kind, "name": place.name},
            )
        )
    return drawn
