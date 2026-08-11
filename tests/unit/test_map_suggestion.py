"""Which regions could hold a track the archive has no map for.

The rule is deliberately weaker than the one that decides what to *draw*, and
the difference is the point. Coverage selection answers "which installed package
belongs behind this rectangle" and is allowed to decide, because an installed
package's extent is the extent of data this archive holds. A catalog extent is
not that. It is a bounding box around a country's outline, and a bounding box
around the Netherlands contains Aachen.

So this answers "which regions *could* hold it", names several, and orders them
by how specific they are. Deciding is left to the person who knows where they
walked.
"""

from __future__ import annotations

import pytest

from trackvault.domain.maps import MapBounds, MapRegionId, RegionExtent, suggest_regions

pytestmark = [pytest.mark.unit, pytest.mark.maps]


def extent(identity: str, box: tuple[float, float, float, float]) -> RegionExtent:
    """Return one catalog region's extent, for building a fixture catalog readably."""
    west, south, east, north = box
    return RegionExtent(
        region_id=MapRegionId.parse(identity),
        bounds=MapBounds(
            min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north
        ),
    )


def track(box: tuple[float, float, float, float]) -> MapBounds:
    """Return the rectangle a track occupies."""
    west, south, east, north = box
    return MapBounds(min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north)


EUROPE = extent("geofabrik:europe", (-31.0, 27.0, 50.0, 72.0))
SPAIN = extent("geofabrik:europe/spain", (-18.2, 27.4, 4.6, 44.1))
BALEARES = extent("geofabrik:europe/spain/islas-baleares", (1.1, 38.5, 4.4, 40.2))
NETHERLANDS = extent("geofabrik:europe/netherlands", (3.0, 50.6, 7.3, 53.7))
LIMBURG = extent("geofabrik:europe/netherlands/limburg", (5.5, 50.7, 6.3, 51.8))
GERMANY = extent("geofabrik:europe/germany", (5.8, 47.2, 15.1, 55.1))
NRW = extent("geofabrik:europe/germany/nordrhein-westfalen", (5.8, 50.3, 9.5, 52.6))

CATALOG = (EUROPE, SPAIN, BALEARES, NETHERLANDS, LIMBURG, GERMANY, NRW)

WALK_ON_MALLORCA = track((2.80, 39.60, 2.90, 39.70))
WALK_NEAR_AACHEN = track((6.05, 50.75, 6.10, 50.79))


def test_the_most_specific_region_that_contains_the_track_comes_first() -> None:
    """A reader offered Spain for a walk on Mallorca would download 1.5 GB for nothing."""
    suggested = suggest_regions(WALK_ON_MALLORCA, CATALOG)

    assert suggested[0] == BALEARES.region_id


def test_the_wider_regions_stay_on_the_list_behind_it() -> None:
    """The smallest is a suggestion, not a verdict; the fallbacks stay reachable."""
    suggested = suggest_regions(WALK_ON_MALLORCA, CATALOG)

    assert suggested[1:3] == (SPAIN.region_id, EUROPE.region_id)


def test_a_region_that_does_not_reach_the_track_is_never_suggested() -> None:
    """Offering Germany for a walk on Mallorca is not a smaller kind of wrong."""
    suggested = suggest_regions(WALK_ON_MALLORCA, CATALOG)

    assert GERMANY.region_id not in suggested
    assert NRW.region_id not in suggested


def test_an_extent_is_not_a_border_so_alternatives_are_named() -> None:
    """The one case that decides the whole shape of this feature.

    A walk in Aachen is in Germany. The smallest catalog extent containing it
    belongs to Limburg, which is in the Netherlands, because a bounding box
    around a province is a rectangle and a border is not. Answering "Limburg"
    alone would be a confident wrong answer, and a confident wrong answer here
    costs somebody a gigabyte and a map their track is not on.
    """
    suggested = suggest_regions(WALK_NEAR_AACHEN, CATALOG)

    assert LIMBURG.region_id in suggested
    assert NRW.region_id in suggested
    assert len(suggested) > 1


def test_a_track_no_single_region_contains_is_answered_with_what_it_crosses() -> None:
    """A walk over a border needs both sides, and neither one alone."""
    crossing = track((6.20, 50.70, 6.40, 50.80))
    over_the_border = (
        extent("geofabrik:a", (5.0, 50.0, 6.3, 51.0)),
        extent("geofabrik:b", (6.25, 50.0, 7.5, 51.0)),
    )

    suggested = suggest_regions(crossing, over_the_border)

    assert len(suggested) == 2


def test_a_region_that_contains_the_track_outranks_one_that_merely_touches_it() -> None:
    """Half a map is worse than a bigger whole one."""
    containing = extent("geofabrik:whole", (5.0, 50.0, 8.0, 52.0))
    touching = extent("geofabrik:half", (6.07, 50.0, 6.2, 52.0))

    suggested = suggest_regions(WALK_NEAR_AACHEN, (touching, containing))

    assert suggested == (containing.region_id,)


def test_regions_of_equal_extent_come_out_in_a_stable_order() -> None:
    """Two suggestions that swap places between page loads are a bug report nobody can reproduce."""
    same = (
        extent("geofabrik:second", (5.0, 50.0, 8.0, 52.0)),
        extent("geofabrik:first", (5.0, 50.0, 8.0, 52.0)),
    )

    assert suggest_regions(WALK_NEAR_AACHEN, same) == suggest_regions(
        WALK_NEAR_AACHEN, tuple(reversed(same))
    )


def test_a_catalog_with_no_extents_suggests_nothing() -> None:
    """Nothing to say is a normal answer, and it is not an empty screen's fault."""
    assert suggest_regions(WALK_ON_MALLORCA, ()) == ()


def test_a_region_whose_outline_wraps_the_globe_is_never_offered() -> None:
    """An extent spanning the globe is dropped rather than offered everywhere.

    Fourteen real regions cross the antimeridian, and every one of them reduces
    to a rectangle containing every track on Earth. Offering Kiribati for a walk
    on Mallorca is not a small mistake -- it is a suggestion that cannot be
    wrong about anything, and so cannot be right about anything.
    """
    wrapping = extent("geofabrik:oceania/kiribati", (-180.0, -11.5, 180.0, 4.8))

    assert suggest_regions(WALK_ON_MALLORCA, (*CATALOG, wrapping))[0] == BALEARES.region_id
    assert wrapping.region_id not in suggest_regions(WALK_ON_MALLORCA, (*CATALOG, wrapping))


def test_a_track_inside_such_a_region_is_offered_nothing_rather_than_everything() -> None:
    """The cost of the rule above, stated rather than hidden.

    A walk in New Zealand gets no suggestion, which is exactly what it got
    before this feature existed. What it does not get is a confident offer of
    the wrong hemisphere.
    """
    wrapping = extent("geofabrik:oceania/new-zealand", (-180.0, -47.5, 180.0, -34.0))
    walk_in_wellington = track((174.7, -41.4, 174.8, -41.2))

    assert suggest_regions(walk_in_wellington, (wrapping,)) == ()
