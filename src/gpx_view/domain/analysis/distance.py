"""How far a track went, defined precisely enough to be reproducible.

> Distance is the sum of the horizontal distances between consecutive positions
> **within** each segment.

Three words in that sentence carry the whole definition.

*Horizontal*: elevation never enters it. A path length through three dimensions
is a different, later metric, and quietly folding altitude into "distance" would
make an alpine walk incomparable with a flat one for no stated reason.

*Consecutive*: positions are compared in source order. The order is data.

*Within*: a segment boundary is a recording interruption, so joining the end of
one segment to the start of the next would invent travel nobody made. Two
segments a kilometre apart are two segments, not a kilometre of walking.

Distance deliberately needs no timestamps. A planned route has geometry, so it
has a length -- whether it was ever travelled is the classification's business.

## The formula

The haversine formula on a sphere of the IUGG mean earth radius. It is chosen
for what it does not do: it needs no dependency, performs no I/O, and stays
numerically stable for the short distances between consecutive GPS positions --
which is exactly where the spherical law of cosines loses its significant
digits.

The sphere costs accuracy against the WGS-84 ellipsoid the coordinates are
expressed on: up to roughly 0.3 % depending on latitude and bearing, or about
30 m on a 10 km walk, and systematic rather than random. That is acceptable for
activity statistics and it is not silently acceptable -- it is why the algorithm
is versioned. An ellipsoidal formula would be version 2, and every stored
distance would say which of the two produced it.
"""

from collections.abc import Iterable
from math import asin, cos, radians, sin, sqrt

from gpx_view.domain.geometry import TrackPoint, TrackSegment

EARTH_RADIUS_METRES = 6_371_008.8
"""IUGG mean earth radius, the conventional sphere for this approximation."""


def geodesic_distance(start: TrackPoint, end: TrackPoint) -> float:
    """Return the horizontal distance between two positions, in metres.

    Elevation is ignored on purpose: this is a distance over the ground, not a
    path length through the air.

    The antimeridian needs no special case. The formula uses the *sine* of half
    the longitude difference, and a step from 179.99 to -179.99 degrees produces
    the same tiny sine as the short way round.
    """
    start_latitude = radians(start.latitude)
    end_latitude = radians(end.latitude)
    half_latitude = sin((end_latitude - start_latitude) / 2.0)
    half_longitude = sin(radians(end.longitude - start.longitude) / 2.0)

    chord = half_latitude * half_latitude + (
        cos(start_latitude) * cos(end_latitude) * half_longitude * half_longitude
    )
    # Clamped because floating point can push a value for two identical
    # positions a hair above 1, and `asin` refuses that rather than returning
    # the zero it obviously means.
    return 2.0 * EARTH_RADIUS_METRES * asin(sqrt(min(1.0, chord)))


def segment_distance(segment: TrackSegment) -> float:
    """Return the distance travelled within one segment, in metres.

    A segment of one position has no distance: one position is a place, not a
    movement.
    """
    points = segment.points
    return sum(
        geodesic_distance(points[index], points[index + 1]) for index in range(len(points) - 1)
    )


def track_distance(segments: Iterable[TrackSegment]) -> float:
    """Return the distance of a whole track, in metres.

    Segments are summed, never joined: the distance between the end of one and
    the start of the next is not travel, it is a gap in the recording.
    """
    return sum(segment_distance(segment) for segment in segments)
