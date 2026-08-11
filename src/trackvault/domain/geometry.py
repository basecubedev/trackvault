"""Normalized track geometry: positions and the segments they form.

These types are the output contract every importer normalizes onto. They express
positions and instants, nothing about the exchange format the data arrived in.
"""

import hashlib
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

MIN_LATITUDE = -90.0
MAX_LATITUDE = 90.0
MIN_LONGITUDE = -180.0
MAX_LONGITUDE = 180.0


def _require_finite(name: str, value: float) -> None:
    """Reject NaN and infinity, which poison every later calculation silently."""
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _as_utc(name: str, value: datetime) -> datetime:
    """Return an unambiguous instant in UTC, rejecting naive timestamps."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TrackPoint:
    """One measured or planned position.

    Elevation and time are optional because not every source records them, and a
    missing value is data, not a defect. Timestamps are stored as unambiguous
    instants in UTC; which timezone they are *displayed* or bucketed in is a
    separate, later decision.

    **Sensor readings are measurements, and they are optional in the same way.**
    A phone in a pocket records neither a heart rate nor a cadence; a chest strap
    records one; a bike computer records both. A position without them is a
    normal position, and a reading of zero is a reading -- a cadence sensor on a
    coasting bike reports zero, and turning that into "no sensor" would be the
    same conflation this model refuses everywhere else.

    Attributes:
        latitude: Degrees north, within [-90, 90].
        longitude: Degrees east, within [-180, 180].
        elevation: Metres above the source's reference, if the source states one.
        time: The instant this position belongs to, if the source states one.
        heart_rate_bpm: Beats per minute a monitor reported here, if one did.
        cadence_rpm: Revolutions or steps per minute a sensor reported, if one did.

    Raises:
        ValueError: If a coordinate is out of range or not finite, the elevation
            is not finite, the timestamp is naive, or a sensor reading is
            negative -- which is a parsing accident rather than a measurement.
    """

    latitude: float
    longitude: float
    elevation: float | None = None
    time: datetime | None = None
    heart_rate_bpm: int | None = None
    cadence_rpm: int | None = None

    def __post_init__(self) -> None:
        """Reject positions that cannot exist and instants that are ambiguous."""
        _require_finite("latitude", self.latitude)
        _require_finite("longitude", self.longitude)
        if not MIN_LATITUDE <= self.latitude <= MAX_LATITUDE:
            raise ValueError(f"latitude must be within [{MIN_LATITUDE}, {MAX_LATITUDE}]")
        if not MIN_LONGITUDE <= self.longitude <= MAX_LONGITUDE:
            raise ValueError(f"longitude must be within [{MIN_LONGITUDE}, {MAX_LONGITUDE}]")
        if self.elevation is not None:
            _require_finite("elevation", self.elevation)
        if self.heart_rate_bpm is not None and self.heart_rate_bpm < 0:
            raise ValueError("heart rate must not be negative")
        if self.cadence_rpm is not None and self.cadence_rpm < 0:
            raise ValueError("cadence must not be negative")
        if self.time is not None:
            object.__setattr__(self, "time", _as_utc("time", self.time))


@dataclass(frozen=True, slots=True)
class TrackSegment:
    """An ordered run of positions without an interruption in the source.

    Segment boundaries are source data: a recording that was paused and resumed
    arrives as several segments, and flattening them into one point list would
    invent a continuity that was never recorded.

    Attributes:
        points: The positions in the order the source listed them.

    Raises:
        ValueError: If the segment holds no point.
    """

    points: tuple[TrackPoint, ...]

    def __post_init__(self) -> None:
        """Reject a segment that carries no geometry."""
        if not self.points:
            raise ValueError("a segment must hold at least one point")

    @property
    def point_count(self) -> int:
        """Return how many positions this segment holds."""
        return len(self.points)

    @property
    def earliest_time(self) -> datetime | None:
        """Return the earliest instant observed, or ``None`` without time data.

        This reports the extent of the instants that were recorded. It is not a
        claim about point order: the source order is preserved untouched.
        """
        return earliest_time([self])

    @property
    def latest_time(self) -> datetime | None:
        """Return the latest instant observed, or ``None`` without time data."""
        return latest_time([self])


def total_point_count(segments: Iterable[TrackSegment]) -> int:
    """Return the number of positions across all given segments."""
    return sum(segment.point_count for segment in segments)


def earliest_time(segments: Iterable[TrackSegment]) -> datetime | None:
    """Return the earliest instant across all given segments, if any."""
    instants = _instants(segments)
    return min(instants) if instants else None


def latest_time(segments: Iterable[TrackSegment]) -> datetime | None:
    """Return the latest instant across all given segments, if any."""
    instants = _instants(segments)
    return max(instants) if instants else None


def _instants(segments: Iterable[TrackSegment]) -> list[datetime]:
    """Return every instant the given segments recorded."""
    return [point.time for segment in segments for point in segment.points if point.time]


def recording_fingerprint(segments: Sequence["TrackSegment"]) -> str:
    """Return the identity of the *recording* a set of segments describes.

    > Two imports whose normalized positions and instants match exactly are the
    > same recording.

    An equality, not a similarity, and that distinction is the whole design.
    One ride exported as GPX 1.1, as GPX 1.0 and as a route produces three
    different files, three different content hashes and three raw imports --
    correctly, because the bytes really are different evidence and one of them
    carries a heart rate the others lost. What they describe is one afternoon,
    and this is what says so.

    Two receivers cannot produce the same positions at the same instants to full
    precision without being the same receiver on the same ride, so there is no
    threshold here, no window and nothing to tune. It is therefore **not** the
    semantic-duplicate problem this project defers: the same loop ridden twice, a
    trimmed export, a file rounded on the way out are all different recordings by
    this rule, and finding them needs a heuristic that can be wrong.

    What is deliberately left out:

    * **Segment boundaries.** A route export flattens a paused recording into one
      run. Where the source put its breaks is data about the document.
    * **Elevation and sensor readings.** A poorer export of one ride is that
      ride. Letting a heart rate in would make the richer file a different
      recording, which is the opposite of the point.
    """
    digest = hashlib.sha256()
    for segment in segments:
        for point in segment.points:
            instant = point.time.isoformat() if point.time is not None else ""
            digest.update(f"{point.latitude!r},{point.longitude!r},{instant}\n".encode())
    return digest.hexdigest()
