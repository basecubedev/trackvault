"""A large track must stay a bounded response, and keep what matters in it.

The measurements live in ``scripts/benchmark_projection.py`` -- a developer
tool, because a wall-clock threshold on a shared runner is noise with a red
cross attached. What belongs in the suite is the property those measurements
exist to protect: however many positions a recording holds, what leaves the
archive is bounded, monotonic, segment-preserving, and still carries the
summit.

The last one is the trap. A shape simplifier drops a peak that sits on nearly
straight ground, which is exactly where summits sit, so the profile is decimated
by its own rule -- turning points -- rather than by the map's.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest

from trackvault.application.projection import decimate_profile, simplify_geometry
from trackvault.application.track_profile import (
    DEFAULT_PROFILE_SAMPLES,
    MAX_GEOMETRY_POINTS,
    MAX_PROFILE_SAMPLES,
)
from trackvault.domain.analysis.series import derive_profile
from trackvault.domain.geometry import TrackPoint, TrackSegment

pytestmark = [pytest.mark.integration, pytest.mark.analysis]

LARGE = 100_000
"""Positions in the synthetic recording. Well past any real day out."""

SUMMIT_INDEX = 61_803
"""Where the one unmistakable peak sits: nowhere near a segment boundary."""

SUMMIT_WIDTH = 6_000.0
"""How many positions the climb and the descent take.

Terrain, not a spike. A one-sample jump of two kilometres is what the elevation
filter exists to remove -- and removing it is correct -- so a test that demanded
it back would be demanding the archive stop filtering. What has to survive
decimation is a summit the *filtered* profile still has.
"""


def _large_track(points: int = LARGE, *, segments: int = 4) -> tuple[TrackSegment, ...]:
    """Return a long synthetic recording over a hill, in four segments.

    Deterministic and synthetic: no private data, and the same shape on every
    machine. The path advances steadily so distance is strictly increasing, and
    the terrain carries small ripples under one broad peak so that the global
    maximum is a real turning point among many local ones.
    """
    start = datetime(2026, 4, 1, 6, 0, tzinfo=UTC)
    per_segment = points // segments
    built: list[TrackSegment] = []
    index = 0
    for segment in range(segments):
        positions: list[TrackPoint] = []
        for _ in range(per_segment):
            hill = 1_200.0 * math.exp(-(((index - SUMMIT_INDEX) / SUMMIT_WIDTH) ** 2))
            ripple = 8.0 * math.sin(index / 220.0)
            positions.append(
                TrackPoint(
                    latitude=48.0 + index * 1e-5,
                    longitude=11.0 + index * 5e-6,
                    elevation=180.0 + hill + ripple,
                    time=start + timedelta(seconds=index * 2 + segment * 900),
                )
            )
            index += 1
        built.append(TrackSegment(points=tuple(positions)))
    return tuple(built)


@pytest.fixture(scope="module")
def large() -> tuple[TrackSegment, ...]:
    """One large synthetic recording, built once for the whole module."""
    return _large_track()


def test_a_profile_response_is_bounded_however_long_the_track_is(
    large: tuple[TrackSegment, ...],
) -> None:
    """A hundred thousand positions leave as a few thousand samples."""
    reduced = decimate_profile(derive_profile(large), DEFAULT_PROFILE_SAMPLES)

    assert reduced.sample_count <= DEFAULT_PROFILE_SAMPLES
    assert reduced.sample_count > 0
    assert DEFAULT_PROFILE_SAMPLES <= MAX_PROFILE_SAMPLES


def test_a_simplified_geometry_response_is_bounded_too(
    large: tuple[TrackSegment, ...],
) -> None:
    """The map's reduction answers the same promise, by its own rule."""
    simplified = simplify_geometry(large, 5_000)

    assert sum(len(segment.points) for segment in simplified) <= 5_000
    assert MAX_GEOMETRY_POINTS >= 5_000


def test_the_reduction_never_joins_two_segments(large: tuple[TrackSegment, ...]) -> None:
    """A boundary is ground nobody travelled. Reducing must not draw across it."""
    reduced = decimate_profile(derive_profile(large), DEFAULT_PROFILE_SAMPLES)
    simplified = simplify_geometry(large, 5_000)

    assert len(reduced.segments) == len(large)
    assert len(simplified) == len(large)
    assert all(segment.samples for segment in reduced.segments)


def test_distance_stays_monotonic_across_the_whole_reduced_profile(
    large: tuple[TrackSegment, ...],
) -> None:
    """One axis for one track, with no jump at a boundary and no step back."""
    reduced = decimate_profile(derive_profile(large), DEFAULT_PROFILE_SAMPLES)

    distances = [
        sample.cumulative_distance_m for segment in reduced.segments for sample in segment.samples
    ]

    assert distances == sorted(distances)


def _extremes(profile: object) -> tuple[float, float, float]:
    """Return the highest and lowest filtered altitude and the fastest speed."""
    samples = [
        sample
        for segment in profile.segments  # type: ignore[attr-defined]
        for sample in segment.samples
    ]
    elevations = [
        sample.filtered_elevation_m for sample in samples if sample.filtered_elevation_m is not None
    ]
    speeds = [
        sample.sustained_speed_mps for sample in samples if sample.sustained_speed_mps is not None
    ]
    return max(elevations), min(elevations), max(speeds)


def test_the_reduced_profile_still_reaches_the_summit(
    large: tuple[TrackSegment, ...],
) -> None:
    """The failure a shape simplifier would cause, and the reason for two rules.

    A summit sits on ground that is nearly straight in plan view, which is the
    first thing Ramer--Douglas--Peucker discards. The chart is therefore reduced
    on its *own* turning points, so the highest and lowest sample of the
    filtered series -- the series the ascent figure is accumulated from -- and
    the fastest sustained speed all survive.
    """
    full = derive_profile(large)
    reduced = decimate_profile(full, DEFAULT_PROFILE_SAMPLES)

    assert _extremes(reduced) == _extremes(full)


def test_the_reduced_profile_keeps_the_track_as_long_as_it_was(
    large: tuple[TrackSegment, ...],
) -> None:
    """Sending fewer samples must not shorten the track."""
    full = derive_profile(large)
    reduced = decimate_profile(full, DEFAULT_PROFILE_SAMPLES)

    assert reduced.total_distance_m == pytest.approx(full.total_distance_m)


def test_every_reduced_sample_keeps_the_identity_it_had(
    large: tuple[TrackSegment, ...],
) -> None:
    """A reduction is a subset, never a resampling.

    A map marker and a chart cursor address a position by
    ``(segment_index, point_index)``. Averaging two positions into one would
    produce a sample that identifies nothing, and the two views would stop
    meaning the same thing.
    """
    full = derive_profile(large)
    reduced = decimate_profile(full, DEFAULT_PROFILE_SAMPLES)

    original = {
        (sample.segment_index, sample.point_index): sample
        for segment in full.segments
        for sample in segment.samples
    }
    for segment in reduced.segments:
        for sample in segment.samples:
            assert original[(sample.segment_index, sample.point_index)] == sample
