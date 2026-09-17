"""The invented place the documentation screenshots are taken in.

Screenshots of an activity archive are pictures of where somebody goes, which is
exactly the data `docs/developer/agent-rules.md` forbids this repository to
carry. So the documentation gets an archive of its own: a fictional island in
empty ocean, a road and path network across it, and journeys along that network.
Nothing here was recorded by anybody.

This module is the one description of that place. The basemap generator draws
the island from it and the archive generator walks the same network, so a
screenshot shows a track on a trail rather than a line beside one.

Everything is deterministic: same inputs, same island, same tracks, same
pictures. There is no random seed to drift.

The geometry is built in a local metric frame -- metres east and north of the
island's centre -- because a route is easier to describe in kilometres than in
fractions of a degree. `to_degrees` converts at the end.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CENTRE_LONGITUDE = -25.00
CENTRE_LATITUDE = 43.60
"""Open North Atlantic. No real coastline, no real settlement, nobody's home."""

_METRES_PER_DEGREE_LATITUDE = 111_320.0
_METRES_PER_DEGREE_LONGITUDE = _METRES_PER_DEGREE_LATITUDE * math.cos(math.radians(CENTRE_LATITUDE))

Point = tuple[float, float]
"""One position as `(longitude, latitude)` in degrees."""

Metres = tuple[float, float]
"""One position as `(east, north)` in metres from the island's centre."""


def to_degrees(position: Metres) -> Point:
    """Return a local metric position as `(longitude, latitude)`."""
    east, north = position
    return (
        CENTRE_LONGITUDE + east / _METRES_PER_DEGREE_LONGITUDE,
        CENTRE_LATITUDE + north / _METRES_PER_DEGREE_LATITUDE,
    )


def to_metres(position: Point) -> Metres:
    """Return a `(longitude, latitude)` position in the local metric frame."""
    longitude, latitude = position
    return (
        (longitude - CENTRE_LONGITUDE) * _METRES_PER_DEGREE_LONGITUDE,
        (latitude - CENTRE_LATITUDE) * _METRES_PER_DEGREE_LATITUDE,
    )


def coast_radius(bearing: float) -> float:
    """Return how far the shore is from the centre at one compass angle.

    The island is star-shaped around its centre: one radius per angle. That is
    what makes "how far inland is this point" a subtraction rather than a
    polygon test, and every slope, every shore-following road and every forest
    edge is derived from it.
    """
    radius = (
        19_000.0
        + 4_200.0 * math.sin(3.0 * bearing + 0.7)
        + 2_600.0 * math.sin(5.0 * bearing + 2.1)
        + 1_500.0 * math.sin(8.0 * bearing + 0.3)
        + 800.0 * math.sin(13.0 * bearing + 1.7)
    )
    # Storvika, the deep bay the main town sits at the head of.
    radius -= 7_600.0 * math.exp(-(_angle_gap(bearing, 2.35) ** 2) / (2.0 * 0.30**2))
    # A second, shallower bite out of the south coast.
    radius -= 3_100.0 * math.exp(-(_angle_gap(bearing, -1.55) ** 2) / (2.0 * 0.22**2))
    return radius


def _angle_gap(first: float, second: float) -> float:
    """Return the smallest signed angle between two bearings."""
    return (first - second + math.pi) % (2.0 * math.pi) - math.pi


def coastline(points: int = 512) -> list[Point]:
    """Return the shore as a closed ring."""
    return [to_degrees(at_bearing(2.0 * math.pi * index / points, 0.0)) for index in range(points)]


def at_bearing(bearing: float, inset: float) -> Metres:
    """Return the position at one bearing, a given distance inland of the shore.

    A negative inset is a position out to sea, which is how the basemap draws
    water that meets the coastline exactly.
    """
    radius = coast_radius(bearing) - inset
    return (radius * math.cos(bearing), radius * math.sin(bearing))


def inland_distance(position: Metres) -> float:
    """Return how far a point is inside the shore, negative when it is at sea."""
    east, north = position
    return coast_radius(math.atan2(north, east)) - math.hypot(east, north)


@dataclass(frozen=True, slots=True)
class Peak:
    """One hill: where it is, how high, and how far its slopes reach."""

    name: str
    at: Metres
    height: float
    spread: float


PEAKS = (
    Peak("Raudfjell", (2_400.0, 6_200.0), 784.0, 5_200.0),
    Peak("Storknuten", (-8_800.0, -2_900.0), 543.0, 4_600.0),
    Peak("Lyngfjell", (7_600.0, -7_400.0), 412.0, 3_900.0),
    Peak("Kvithorn", (-4_200.0, 8_100.0), 361.0, 3_100.0),
)
"""The high ground. Named so an elevation profile has somewhere to be about."""


def elevation(position: Metres) -> float:
    """Return the ground height in metres at one position.

    A sum of hills over a gentle interior plateau, faded to sea level across the
    last kilometre and a half of shore -- so a coastal route is flat, a ridge
    route climbs, and the two profiles look like what their names say.
    """
    inland = inland_distance(position)
    if inland <= 0.0:
        return 0.0
    east, north = position
    height = 34.0 * math.exp(-(math.hypot(east, north) ** 2) / (2.0 * 17_000.0**2))
    for peak in PEAKS:
        gap = math.hypot(east - peak.at[0], north - peak.at[1])
        height += peak.height * math.exp(-(gap**2) / (2.0 * peak.spread**2))
    # Small, smooth undulation, so a profile is not a set of clean bell curves.
    height += 22.0 * math.sin(east / 1_900.0) * math.cos(north / 2_300.0)
    height += 9.0 * math.sin(east / 700.0 + 1.3) * math.sin(north / 640.0)
    return max(0.0, height * min(1.0, inland / 1_500.0))


def _ring(centre: Metres, radius: float, *, wobble: float = 0.0, points: int = 48) -> list[Metres]:
    """Return a closed blob around a centre, roughly circular."""
    east, north = centre
    return [
        (
            east
            + (radius + wobble * math.sin(8.0 * math.pi * index / points + radius))
            * math.cos(2.0 * math.pi * index / points),
            north
            + (radius + wobble * math.cos(6.0 * math.pi * index / points + radius))
            * math.sin(2.0 * math.pi * index / points),
        )
        for index in range(points)
    ]


def _flow(start: Metres, end: Metres, points: int = 40, meander: float = 900.0) -> list[Metres]:
    """Return a line from one place to another that does not run straight."""
    (east_from, north_from), (east_to, north_to) = start, end
    length = math.hypot(east_to - east_from, north_to - north_from)
    across = ((north_from - north_to) / length, (east_to - east_from) / length)
    return [
        (
            east_from
            + (east_to - east_from) * index / points
            + across[0]
            * meander
            * math.sin(3.4 * math.pi * index / points)
            * math.sin(math.pi * index / points),
            north_from
            + (north_to - north_from) * index / points
            + across[1]
            * meander
            * math.sin(3.4 * math.pi * index / points)
            * math.sin(math.pi * index / points),
        )
        for index in range(points + 1)
    ]


COAST_ROAD = [at_bearing(math.radians(degree), 2_600.0) for degree in range(360)]
"""Kystvegen, the road right round the island: one point per degree of bearing.

One point per degree is what makes a journey along it a slice: `coast_road(14,
125)` is the stretch between Storvika and Nordvik. Every settlement below is
placed *on* this line rather than near it, so a ride along the coast passes
through the towns instead of past them.
"""


def coast_road(start: int, end: int) -> list[Metres]:
    """Return the stretch of the coast road from one bearing to another.

    Always the direction the bearing increases, wrapping past north when the end
    is behind the start, and right round the island when the two are the same.
    Travelling the other way is `_back` of the same stretch, which keeps one
    rule here instead of a shorter-way-round guess.
    """
    span = (end - start) % 360 or 360
    return [COAST_ROAD[(start + step) % 360] for step in range(span + 1)]


NORDVIK = COAST_ROAD[125]
STORVIKA = COAST_ROAD[14]
LYNGHOLM = COAST_ROAD[292]
SANDDAL = COAST_ROAD[205]
"""The four settlements, each one a point on the coast road."""

BLAVATNET = (-2_600.0, 1_100.0)
RAUDFJELL = PEAKS[0].at
STORKNUTEN = PEAKS[1].at
LYNGFJELL = PEAKS[2].at
KVITHORN = PEAKS[3].at
"""The inland junctions. Every trail below starts and ends at one of these."""


@dataclass(frozen=True, slots=True)
class Place:
    """One label the basemap carries."""

    name: str
    at: Metres
    kind: str


PLACES = (
    Place("Nordvik", NORDVIK, "town"),
    Place("Storvika", STORVIKA, "village"),
    Place("Lyngholm", LYNGHOLM, "village"),
    Place("Sanddal", SANDDAL, "village"),
    Place("Raudfjell", RAUDFJELL, "peak"),
    Place("Storknuten", STORKNUTEN, "peak"),
    Place("Lyngfjell", LYNGFJELL, "peak"),
    Place("Kvithorn", KVITHORN, "peak"),
)

LAND_AREAS: tuple[tuple[str, list[Metres]], ...] = (
    ("forest", _ring((1_200.0, 3_400.0), 4_200.0, wobble=900.0)),
    ("forest", _ring((-9_800.0, 900.0), 3_600.0, wobble=800.0)),
    ("forest", _ring((6_900.0, -5_100.0), 3_100.0, wobble=700.0)),
    ("forest", _ring((-3_600.0, -9_400.0), 2_700.0, wobble=600.0)),
    ("scrub", _ring((-2_000.0, 11_400.0), 2_400.0, wobble=500.0)),
    ("farmland", _ring((-9_400.0, 7_900.0), 2_600.0, wobble=400.0)),
    ("farmland", _ring((11_200.0, -1_300.0), 2_200.0, wobble=400.0)),
    ("meadow", _ring((-11_900.0, -9_600.0), 2_000.0, wobble=350.0)),
    ("residential", _ring(NORDVIK, 1_500.0, wobble=150.0)),
    ("residential", _ring(STORVIKA, 950.0, wobble=120.0)),
    ("residential", _ring(LYNGHOLM, 820.0, wobble=100.0)),
    ("residential", _ring(SANDDAL, 760.0, wobble=100.0)),
)
"""Ground cover. `kind` is the Shortbread vocabulary the map style filters on."""

WATER_AREAS: tuple[list[Metres], ...] = (
    _ring(BLAVATNET, 1_450.0, wobble=260.0),
    _ring((5_200.0, 7_300.0), 780.0, wobble=140.0),
)
"""Blavatnet and the tarn under Raudfjell."""

WATER_LINES: tuple[tuple[str, list[Metres]], ...] = (
    ("Storelva", _flow((1_900.0, 4_800.0), BLAVATNET)),
    ("Storelva", _flow(BLAVATNET, (8_900.0, 1_400.0), points=50, meander=1_400.0)),
    ("Kaldelva", _flow((-8_200.0, -1_400.0), (-13_600.0, -7_300.0), meander=700.0)),
)

_FJELLVEGEN = _flow(NORDVIK, RAUDFJELL, points=40, meander=1_500.0)
_SANDDALSVEGEN = _flow(SANDDAL, BLAVATNET, points=40, meander=1_200.0)
_STORVIKVEGEN = _flow(STORVIKA, LYNGHOLM, points=44, meander=1_700.0)
_LYNGVEGEN = _flow(LYNGHOLM, LYNGFJELL, points=26, meander=900.0)

STREETS: tuple[tuple[str, str, list[Metres]], ...] = (
    ("Kystvegen", "secondary", [*COAST_ROAD, COAST_ROAD[0]]),
    ("Fjellvegen", "tertiary", _FJELLVEGEN),
    ("Sanddalsvegen", "tertiary", _SANDDALSVEGEN),
    ("Storvikvegen", "tertiary", _STORVIKVEGEN),
    ("Lyngvegen", "unclassified", _LYNGVEGEN),
    ("Havnegata", "residential", _flow(COAST_ROAD[122], COAST_ROAD[128], points=14, meander=700.0)),
    (
        "Strandgata",
        "residential",
        _flow(NORDVIK, (NORDVIK[0] + 900.0, NORDVIK[1] - 1_100.0), points=10, meander=200.0),
    ),
)

_RAUDFJELLSTIEN = _flow(BLAVATNET, RAUDFJELL, points=34, meander=900.0)
_RYGGSTIEN = _flow(RAUDFJELL, KVITHORN, points=32, meander=1_100.0)
_KVITHORNSTIEN = _flow(KVITHORN, NORDVIK, points=26, meander=800.0)
_STORKNUTSTIEN = _flow(BLAVATNET, STORKNUTEN, points=30, meander=700.0)
_LYNGSTIEN = _flow(LYNGFJELL, STORVIKA, points=32, meander=1_300.0)
_VASSTIEN = _flow(BLAVATNET, NORDVIK, points=34, meander=1_000.0)
_HAVNESTIEN = [*_ring(NORDVIK, 760.0, wobble=180.0, points=40), NORDVIK]
_KYSTSTIEN = [at_bearing(math.radians(degree), 900.0) for degree in range(248, 269)]

PATHS: tuple[tuple[str, str, list[Metres]], ...] = (
    ("Raudfjellstien", "path", _RAUDFJELLSTIEN),
    ("Ryggstien", "path", _RYGGSTIEN),
    ("Kvithornstien", "path", _KVITHORNSTIEN),
    ("Storknutstien", "path", _STORKNUTSTIEN),
    ("Lyngstien", "path", _LYNGSTIEN),
    ("Vasstien", "track", _VASSTIEN),
    ("Havnestien", "footway", _HAVNESTIEN),
    ("Kyststien", "track", _KYSTSTIEN),
)
"""Trails. Every one of them runs between two of the junctions above."""


@dataclass(frozen=True, slots=True)
class Journey:
    """One route through the region, as the waypoints a traveller passes."""

    name: str
    activity: str
    waypoints: list[Metres]


def _back(leg: list[Metres]) -> list[Metres]:
    """Return a leg travelled the other way round."""
    return list(reversed(leg))


def _join(*legs: list[Metres]) -> list[Metres]:
    """Return several legs as one continuous line.

    Every leg has to start where the previous one ended -- that is what the
    junctions above are for -- so the shared point is dropped rather than
    bridged. A gap here would draw as a straight line across country, which is
    the one thing a screenshot of a track on a trail must not show.
    """
    joined: list[Metres] = []
    for leg in legs:
        joined.extend(leg[1:] if joined else leg)
    return joined


JOURNEYS = (
    Journey("Raudfjell from Nordvik", "hiking", _join(_FJELLVEGEN, _back(_FJELLVEGEN))),
    Journey(
        "Raudfjell and the ridge",
        "hiking",
        _join(_FJELLVEGEN, _RYGGSTIEN, _KVITHORNSTIEN),
    ),
    Journey(
        "Storknuten circuit",
        "hiking",
        _join(_STORKNUTSTIEN, _back(_STORKNUTSTIEN)),
    ),
    Journey("Harbour loop", "walking", _HAVNESTIEN),
    Journey(
        "Blavatnet and back",
        "walking",
        _join(_SANDDALSVEGEN[:22], _back(_SANDDALSVEGEN[:22])),
    ),
    Journey("South shore track", "walking", _join(_KYSTSTIEN, _back(_KYSTSTIEN))),
    Journey("Coast road to Storvika", "cycling", _back(coast_road(14, 125))),
    Journey("Around the island", "cycling", coast_road(125, 125)),
    Journey(
        "Storvika to Lyngholm",
        "cycling",
        _join(_STORVIKVEGEN, coast_road(292, 14)),
    ),
    Journey("Lyngfjell out and back", "running", _join(_LYNGVEGEN, _back(_LYNGVEGEN))),
    Journey(
        "Sanddal river run",
        "running",
        _join(_SANDDALSVEGEN[:15], _back(_SANDDALSVEGEN[:15])),
    ),
)
"""The journeys the demo archive is built from. Each is a line on the network."""
