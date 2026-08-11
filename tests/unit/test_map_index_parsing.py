"""What the archive reads out of a provider's index, and what it refuses.

The index is remote, untrusted input, and this is the boundary it crosses. The
region hierarchy came through here before; the outlines are new, and they are
the fact that lets the archive answer "which map does this track need".

An outline is read for one purpose and then thrown away: the rectangle around
it. Everything below is about that reduction being safe -- a coordinate array is
somebody else's document, and this build walks it recursively.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gpx_view.application.maps import MapErrorCode, MapOperationError
from gpx_view.infrastructure.maps.geofabrik import parse_index

pytestmark = [pytest.mark.unit, pytest.mark.maps]

DOWNLOAD = "https://download.geofabrik.de"


def feature(path: str, name: str, geometry: object | None = None) -> dict[str, Any]:
    """Return one index entry, shaped as the provider publishes it."""
    entry: dict[str, Any] = {
        "type": "Feature",
        "properties": {"name": name, "urls": {"pbf": f"{DOWNLOAD}/{path}-latest.osm.pbf"}},
    }
    if geometry is not None:
        entry["geometry"] = geometry
    return entry


def polygon(*rings: list[list[float]]) -> dict[str, Any]:
    """Return a `MultiPolygon` around the given rings, as the index nests them."""
    return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}


def index(*features: dict[str, Any]) -> bytes:
    """Return an index document holding these features."""
    return json.dumps({"type": "FeatureCollection", "features": list(features)}).encode()


def test_a_regions_extent_is_the_rectangle_around_its_outline() -> None:
    """The one new fact, and the whole feature rests on it."""
    body = index(
        feature(
            "europe/monaco",
            "Monaco",
            polygon([[7.41, 43.72], [7.44, 43.75], [7.39, 43.74], [7.41, 43.72]]),
        )
    )

    bounds = parse_index(body)[0].bounds

    assert bounds is not None
    assert (bounds.min_longitude, bounds.min_latitude) == (7.39, 43.72)
    assert (bounds.max_longitude, bounds.max_latitude) == (7.44, 43.75)


def test_every_part_of_a_multipart_region_is_inside_its_extent() -> None:
    """A country with islands is one region, and its rectangle holds all of it."""
    body = index(
        feature(
            "europe/spain",
            "Spain",
            polygon(
                [[-9.3, 36.0], [3.3, 43.8], [-9.3, 43.8], [-9.3, 36.0]],
                [[1.1, 38.6], [4.4, 40.1], [1.1, 40.1], [1.1, 38.6]],
            ),
        )
    )

    bounds = parse_index(body)[0].bounds

    assert bounds is not None
    assert bounds.max_longitude == 4.4


def test_an_index_without_outlines_still_yields_its_regions() -> None:
    """A region without an extent is still browsable and installable.

    The provider publishes a smaller index too, and a build reading it loses
    nothing except the ability to suggest that region for a track.
    """
    regions = parse_index(index(feature("europe/monaco", "Monaco")))

    assert regions[0].name == "Monaco"
    assert regions[0].bounds is None


def test_an_outline_off_the_globe_costs_a_region_its_extent_and_nothing_else() -> None:
    """Repairing a nonsense rectangle into a plausible one is how a boundary stops being one."""
    body = index(
        feature(
            "europe/monaco",
            "Monaco",
            polygon([[7.41, 43.72], [7.44, 999.0], [7.39, 43.74], [7.41, 43.72]]),
        )
    )

    regions = parse_index(body)

    assert regions[0].name == "Monaco"
    assert regions[0].bounds is None


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "MultiPolygon", "coordinates": "not coordinates"},
        {"type": "MultiPolygon", "coordinates": [[[["a", "b"]]]]},
        {"type": "MultiPolygon", "coordinates": [[[[True, False]]]]},
        {"type": "MultiPolygon", "coordinates": [[[[7.41]]]]},
        {"type": "MultiPolygon"},
        "not a geometry",
    ],
)
def test_an_outline_that_is_not_coordinates_is_refused_rather_than_guessed_at(
    geometry: object,
) -> None:
    """Including `true`, which is an `int` in Python and is not a longitude."""
    regions = parse_index(index(feature("europe/monaco", "Monaco", geometry)))

    assert regions[0].bounds is None


def test_an_absurdly_nested_coordinate_array_is_refused_rather_than_walked() -> None:
    """A recursive walk over somebody else's document needs a floor under it."""
    nested: Any = [[7.41, 43.72]]
    for _ in range(200):
        nested = [nested]

    regions = parse_index(index(feature("europe/monaco", "Monaco", {"coordinates": nested})))

    assert regions[0].bounds is None


def test_an_index_that_is_not_an_index_is_refused_by_name() -> None:
    """The catalog is replaced only by something that parsed."""
    with pytest.raises(MapOperationError) as raised:
        parse_index(b"{}")

    assert raised.value.code is MapErrorCode.MAP_CATALOG_INVALID


def test_an_index_holding_no_usable_region_is_refused() -> None:
    """An empty catalog would silently replace a good one with nothing."""
    with pytest.raises(MapOperationError) as raised:
        parse_index(index({"type": "Feature", "properties": {"name": "Nowhere"}}))

    assert raised.value.code is MapErrorCode.MAP_CATALOG_INVALID
