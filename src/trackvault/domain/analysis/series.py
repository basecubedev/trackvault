"""The per-position series a map and a chart are drawn from.

The metrics beside this module are aggregates: one distance, one ascent, one
maximum speed. A chart needs the other shape of the same analysis -- a value at
every position -- and the one thing that must never happen is for the two to
come from different arithmetic:

```
elevation_gain_m   accumulated from the filtered profile
elevation series   the same filtered profile
maximum_speed_mps  the highest sustained window speed
speed series       the same sustained window speeds
```

A chart drawn from raw altitude under a headline computed from a filtered one
is two pictures of two different tracks sharing an axis, and the reader is left
to work out why they disagree. So nothing here computes anything: it asks the
elevation filter and the movement window for their own series and lays them out
against the positions.

Like every other module in this package, the entry point takes segments and
nothing else. No exchange format, source application or importer is reachable
from a tuple of segments, which is what makes the profile source-agnostic by
construction rather than by intention.

## Identity

A sample is addressed by `(segment_index, point_index)`, which is the position's
own address in the normalized geometry. That is what lets a map marker and a
chart cursor mean the same sample without either of them searching for a
latitude: nearest-coordinate matching in a browser is a second, worse authority
on identity, and it picks the wrong point exactly where a track crosses itself.

## Distance

Cumulative distance is measured **within** segments and carried across them.
A segment boundary is a recording interruption, so the ground between two
segments was not travelled and never enters the total -- but the next segment
continues from the distance so far rather than restarting at zero, because the
axis a chart draws is one axis for one track. The result is monotonic by
construction, with no jump at a boundary.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from trackvault.domain.analysis.distance import geodesic_distance
from trackvault.domain.analysis.elevation import filtered_elevation_profile
from trackvault.domain.analysis.movement import sustained_speeds
from trackvault.domain.geometry import TrackSegment


@dataclass(frozen=True, slots=True)
class ProfileSample:
    """One position, with everything a map or a chart reads from it.

    Attributes:
        segment_index: Which segment the position belongs to.
        point_index: The position's index within that segment. Together with
            ``segment_index`` this is the sample's identity, stable across
            requests and shared by every view of the track.
        cumulative_distance_m: Distance from the track's start, summed within
            segments and carried across them. Monotonic.
        latitude: The position's latitude.
        longitude: The position's longitude.
        instant: What the position's own clock said, if anything. What that is
            worth is a property of the track, reported once beside the series.
        raw_elevation_m: The altitude as recorded, or ``None``.
        filtered_elevation_m: The same altitude after the filter the ascent is
            accumulated from, or ``None``. The series a chart shows by default,
            because it is the one the headline figure was derived from.
        sustained_speed_mps: The speed held across the analysis window at this
            position, or ``None`` where none could be derived. Never ``0.0`` as
            a filler: a gap, an implausible interval and a missing instant are
            absences, and a zero would draw them as a stop.
        heart_rate_bpm: What a monitor reported here, or ``None``. Carried
            through untouched: it is a *measurement*, and the archive has
            nothing to add to it. The elevation beside it exists twice --
            recorded and filtered -- because the ascent figure is accumulated
            from one of them; nothing is accumulated from this.
        cadence_rpm: What a cadence sensor reported here, or ``None``. Zero is a
            reading and stays one.
    """

    segment_index: int
    point_index: int
    cumulative_distance_m: float
    latitude: float
    longitude: float
    instant: datetime | None
    raw_elevation_m: float | None
    filtered_elevation_m: float | None
    sustained_speed_mps: float | None
    heart_rate_bpm: int | None = None
    cadence_rpm: int | None = None


@dataclass(frozen=True, slots=True)
class ProfileSegment:
    """One uninterrupted run of samples.

    Segments stay apart all the way to the client. Joining two of them draws a
    line across ground nobody travelled, and on a map that line is the most
    visible thing on the screen.

    Attributes:
        index: The segment's index in the track.
        samples: Its samples, in traversal order.
    """

    index: int
    samples: tuple[ProfileSample, ...]


@dataclass(frozen=True, slots=True)
class TrackProfile:
    """Every position of one track, with its distance, altitude and speed.

    Attributes:
        segments: The track's segments, in source order.
    """

    segments: tuple[ProfileSegment, ...]

    @property
    def sample_count(self) -> int:
        """Return how many samples the profile holds in total."""
        return sum(len(segment.samples) for segment in self.segments)

    @property
    def total_distance_m(self) -> float:
        """Return the distance axis's upper end, or ``0.0`` for an empty track."""
        for segment in reversed(self.segments):
            if segment.samples:
                return segment.samples[-1].cumulative_distance_m
        return 0.0


def derive_profile(segments: Sequence[TrackSegment]) -> TrackProfile:
    """Lay one track's positions out against distance, altitude and speed.

    Args:
        segments: The normalized geometry, in source order. Nothing else is
            read, which is what keeps the profile format-independent.

    Returns:
        One sample per position, in traversal order, addressed by its own place
        in the geometry.
    """
    speeds = sustained_speeds(segments)
    profile: list[ProfileSegment] = []
    travelled = 0.0

    for segment_index, segment in enumerate(segments):
        filtered = filtered_elevation_profile(segment)
        samples: list[ProfileSample] = []
        for point_index, point in enumerate(segment.points):
            if point_index > 0:
                travelled += geodesic_distance(segment.points[point_index - 1], point)
            samples.append(
                ProfileSample(
                    segment_index=segment_index,
                    point_index=point_index,
                    cumulative_distance_m=travelled,
                    latitude=point.latitude,
                    longitude=point.longitude,
                    instant=point.time,
                    raw_elevation_m=point.elevation,
                    filtered_elevation_m=filtered[point_index],
                    sustained_speed_mps=speeds.get((segment_index, point_index)),
                    heart_rate_bpm=point.heart_rate_bpm,
                    cadence_rpm=point.cadence_rpm,
                )
            )
        profile.append(ProfileSegment(index=segment_index, samples=tuple(samples)))

    return TrackProfile(segments=tuple(profile))


__all__ = [
    "ProfileSample",
    "ProfileSegment",
    "TrackProfile",
    "derive_profile",
]
