"""The rectangle a map package covers, and the rectangle a track occupies.

Both are the same shape, which is the whole reason coverage selection is four
float comparisons rather than a spatial database. A track's extent and a
region's extent meet here and nowhere else, so nothing about maps reaches into
the track model and nothing about tracks reaches into the map model.

Longitudes that wrap the antimeridian are deliberately **not** modelled. No
region in any catalog this project reads crosses it, and a pair of ranges that
sometimes means "inside" and sometimes "outside" is the kind of subtlety that
silently picks the wrong basemap. A track that wraps reports the wide bounds it
literally has, matches nothing, and draws on a neutral background.
"""

from dataclasses import dataclass
from typing import Self

MIN_LATITUDE = -90.0
MAX_LATITUDE = 90.0
MIN_LONGITUDE = -180.0
MAX_LONGITUDE = 180.0


@dataclass(frozen=True, slots=True)
class MapBounds:
    """A west/south/east/north rectangle in WGS 84 degrees.

    Attributes:
        min_longitude: Western edge.
        min_latitude: Southern edge.
        max_longitude: Eastern edge.
        max_latitude: Northern edge.
    """

    min_longitude: float
    min_latitude: float
    max_longitude: float
    max_latitude: float

    def __post_init__(self) -> None:
        """Refuse a rectangle that is off the globe or inside out."""
        for value in (
            self.min_longitude,
            self.min_latitude,
            self.max_longitude,
            self.max_latitude,
        ):
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError("map bounds must be finite")
        if not MIN_LATITUDE <= self.min_latitude <= self.max_latitude <= MAX_LATITUDE:
            raise ValueError("map bounds latitudes must be ordered and within -90..90")
        if not MIN_LONGITUDE <= self.min_longitude <= self.max_longitude <= MAX_LONGITUDE:
            raise ValueError("map bounds longitudes must be ordered and within -180..180")

    @classmethod
    def around(cls, positions: tuple[tuple[float, float], ...]) -> Self | None:
        """Return the rectangle enclosing ``(longitude, latitude)`` pairs.

        Returns:
            The enclosing rectangle, or ``None`` for an empty input -- a track
            with no positions occupies nowhere, which is a different answer from
            occupying a point at the origin.
        """
        if not positions:
            return None
        longitudes = [longitude for longitude, _ in positions]
        latitudes = [latitude for _, latitude in positions]
        return cls(
            min_longitude=min(longitudes),
            min_latitude=min(latitudes),
            max_longitude=max(longitudes),
            max_latitude=max(latitudes),
        )

    def covers(self, other: "MapBounds") -> bool:
        """Report whether this rectangle fully contains another."""
        return (
            self.min_longitude <= other.min_longitude
            and self.min_latitude <= other.min_latitude
            and self.max_longitude >= other.max_longitude
            and self.max_latitude >= other.max_latitude
        )

    def intersects(self, other: "MapBounds") -> bool:
        """Report whether the two rectangles share any area, edges included."""
        return (
            self.min_longitude <= other.max_longitude
            and other.min_longitude <= self.max_longitude
            and self.min_latitude <= other.max_latitude
            and other.min_latitude <= self.max_latitude
        )

    @property
    def area(self) -> float:
        """Return the rectangle's extent in square degrees.

        Square degrees, not square metres: this exists to order candidate
        packages by how specific they are, and for two packages that both cover
        the same track the comparison is the same either way. Calling it an area
        in metres would invite somebody to report it.
        """
        return (self.max_longitude - self.min_longitude) * (self.max_latitude - self.min_latitude)

    def padded(self, degrees: float) -> "MapBounds":
        """Return this rectangle grown by ``degrees`` on every side, clamped.

        A track that runs along a region's edge is inside it, and a viewport
        around that track is not. Padding the query is how "the map has to keep
        working while the reader pans a little" stays a property of the
        selection rather than a surprise.
        """
        if degrees < 0:
            raise ValueError("padding must not be negative")
        return MapBounds(
            min_longitude=max(MIN_LONGITUDE, self.min_longitude - degrees),
            min_latitude=max(MIN_LATITUDE, self.min_latitude - degrees),
            max_longitude=min(MAX_LONGITUDE, self.max_longitude + degrees),
            max_latitude=min(MAX_LATITUDE, self.max_latitude + degrees),
        )
