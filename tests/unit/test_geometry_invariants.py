"""Invariants the distance and elevation algorithms must hold for any track.

Numeric algorithms are badly served by snapshot tests. A pinned "this fixture
produces 483.4 m" breaks on every legitimate improvement and says nothing about
the cases nobody thought to pin. The relations here survive that: they state how
an analysis must respond when its *input* is transformed in a way that should
not change the answer -- reversed, resampled, shifted, offset -- and they hold
for any correct implementation of the contract.

Every fixture is synthetic. Nothing here is derived from a real recording.
"""

import math

import pytest

from trackvault.domain import TrackPoint, TrackSegment
from trackvault.domain.analysis import analyse_elevation, track_distance
from trackvault.domain.analysis.elevation import ELEVATION_DEADBAND_METRES

pytestmark = [pytest.mark.unit, pytest.mark.analysis]

LATITUDE = 47.0
METRES_PER_LATITUDE_DEGREE = 111_195.0


def _at(
    north_metres: float, east_metres: float = 0.0, *, elevation: float | None = None
) -> TrackPoint:
    """Build one position from a local offset in metres."""
    return TrackPoint(
        latitude=LATITUDE + north_metres / METRES_PER_LATITUDE_DEGREE,
        longitude=east_metres / (METRES_PER_LATITUDE_DEGREE * math.cos(math.radians(LATITUDE))),
        elevation=elevation,
    )


def _line(count: int, *, metres: float) -> list[TrackPoint]:
    """Build a straight line of a stated total length."""
    return [_at(index * metres / (count - 1)) for index in range(count)]


def _profile(elevations: list[float], *, spacing: float = 10.0) -> TrackSegment:
    """Build a segment that climbs a stated profile at a fixed ground spacing."""
    return TrackSegment(
        points=tuple(
            _at(index * spacing, elevation=elevation) for index, elevation in enumerate(elevations)
        )
    )


def _reversed(segment: TrackSegment) -> TrackSegment:
    """Return the same segment walked the other way."""
    return TrackSegment(points=tuple(reversed(segment.points)))


def _shifted(segment: TrackSegment, metres: float) -> TrackSegment:
    """Return the same segment with every elevation moved by a constant."""
    return TrackSegment(
        points=tuple(
            TrackPoint(
                latitude=point.latitude,
                longitude=point.longitude,
                elevation=None if point.elevation is None else point.elevation + metres,
                time=point.time,
            )
            for point in segment.points
        )
    )


# --- Distance ---------------------------------------------------------------


def test_a_distance_is_never_negative() -> None:
    """There is no shape of track that travels a negative way."""
    for count in (2, 3, 10, 100):
        assert track_distance([TrackSegment(points=tuple(_line(count, metres=1000.0)))]) >= 0.0


def test_walking_a_path_backwards_covers_the_same_ground() -> None:
    """Distance is a property of the path, not of the direction taken along it."""
    segment = TrackSegment(points=tuple(_line(50, metres=5000.0)))

    assert track_distance([_reversed(segment)]) == pytest.approx(track_distance([segment]))


def test_standing_still_for_a_sample_adds_nothing() -> None:
    """A repeated position is a position recorded twice, not a step taken."""
    points = _line(20, metres=2000.0)
    doubled = [*points[:10], points[9], *points[10:]]

    assert track_distance([TrackSegment(points=tuple(doubled))]) == pytest.approx(
        track_distance([TrackSegment(points=tuple(points))])
    )


def test_splitting_a_segment_at_a_repeated_position_preserves_the_total() -> None:
    """Cutting a recording in two where it already was changes nothing.

    The cut point belongs to both halves, so no ground is lost between them.
    """
    points = _line(40, metres=4000.0)
    whole = TrackSegment(points=tuple(points))
    first = TrackSegment(points=tuple(points[:21]))
    second = TrackSegment(points=tuple(points[20:]))

    assert track_distance([first, second]) == pytest.approx(track_distance([whole]))


def test_a_gap_between_two_segments_is_never_travelled() -> None:
    """Two segments a kilometre apart are two segments, not a kilometre walked."""
    near = TrackSegment(points=(_at(0.0), _at(100.0)))
    far = TrackSegment(points=(_at(10_000.0), _at(10_100.0)))

    assert track_distance([near, far]) == pytest.approx(200.0, rel=0.01)


def test_resampling_a_straight_line_leaves_its_length_alone() -> None:
    """The same straight kilometre logged more often is the same kilometre."""
    lengths = {
        count: track_distance([TrackSegment(points=tuple(_line(count, metres=1000.0)))])
        for count in (2, 5, 50, 500)
    }

    for count, length in lengths.items():
        assert length == pytest.approx(1000.0, rel=0.001), f"{count} points"


# --- Elevation --------------------------------------------------------------


def test_reversing_a_climb_turns_its_ascent_into_descent() -> None:
    """Walking down what was walked up loses what the climb gained."""
    segment = _profile([100.0 + index * 2.0 for index in range(200)])
    forward = analyse_elevation([segment])
    backward = analyse_elevation([_reversed(segment)])

    assert forward.gain_metres is not None
    assert forward.loss_metres is not None
    assert backward.gain_metres == pytest.approx(forward.loss_metres)
    assert backward.loss_metres == pytest.approx(forward.gain_metres)


@pytest.mark.parametrize(
    "elevations",
    [
        pytest.param([100.0 + 30.0 * math.sin(index / 7.0) for index in range(300)], id="rolling"),
        pytest.param([100.0 + 15.0 * math.sin(index / 20.0) for index in range(400)], id="gentle"),
        pytest.param(
            [100.0 + float(index) for index in range(101)]
            + [200.0 - float(index) for index in range(101)],
            id="out-and-back",
        ),
        pytest.param(
            [100.0 + 8.0 * math.sin(index / 3.0) + index * 0.4 for index in range(250)],
            id="climb-with-undulation",
        ),
    ],
)
def test_reversing_terrain_swaps_its_ascent_and_descent_exactly(elevations: list[float]) -> None:
    """Direction of travel is not a property of the ground.

    Exactly, not approximately: the filter confirms a swing at the turning point
    the profile reached, and reversing a profile reaches the same turning points
    in the opposite order.
    """
    segment = _profile(elevations)
    forward = analyse_elevation([segment])
    backward = analyse_elevation([_reversed(segment)])

    assert forward.gain_metres is not None
    assert forward.loss_metres is not None
    assert backward.gain_metres == pytest.approx(forward.loss_metres)
    assert backward.loss_metres == pytest.approx(forward.gain_metres)


def test_a_route_that_returns_to_its_start_gains_what_it_loses() -> None:
    """Up a hill and back down again ends where it began, at both ends."""
    elevations = [100.0 + float(index) for index in range(101)] + [
        200.0 - float(index) for index in range(101)
    ]

    elevation = analyse_elevation([_profile(elevations)])

    assert elevation.gain_metres == pytest.approx(elevation.loss_metres)


def test_moving_a_whole_track_uphill_changes_no_ascent() -> None:
    """Ascent is a sum of differences, so a constant offset cancels out.

    The extremes are single observations and do move -- they are the altitudes
    that were recorded, not differences between them.
    """
    segment = _profile([100.0 + 40.0 * math.sin(index / 11.0) for index in range(200)])
    original = analyse_elevation([segment])
    raised = analyse_elevation([_shifted(segment, 1000.0)])

    assert raised.gain_metres == pytest.approx(original.gain_metres)
    assert raised.loss_metres == pytest.approx(original.loss_metres)
    assert original.minimum_metres is not None
    assert raised.minimum_metres == pytest.approx(original.minimum_metres + 1000.0)
    assert original.maximum_metres is not None
    assert raised.maximum_metres == pytest.approx(original.maximum_metres + 1000.0)


@pytest.mark.parametrize("noise", [0.5, 1.0, 2.0, 4.0])
def test_a_flat_walk_climbs_nothing_however_noisy_the_receiver(noise: float) -> None:
    """The defect the deadband exists for: flat ground accumulating altitude.

    The wander is deterministic rather than random -- this suite may not depend
    on a seed -- and incommensurable, so it never repeats and never drifts.
    """
    elevations = [100.0 + noise * math.sin(index * 2.399) for index in range(500)]

    elevation = analyse_elevation([_profile(elevations)])

    assert elevation.gain_metres == pytest.approx(0.0, abs=noise), (
        f"a flat walk with +-{noise} m of receiver noise climbed {elevation.gain_metres} m"
    )


def test_the_same_hill_climbs_the_same_however_often_it_is_sampled() -> None:
    """A hundred metres of hill is a hundred metres however dense the log.

    An unfiltered sum of positive differences fails this outright: it grows with
    the sampling rate, so the same walk logged twice as often "climbs" twice as
    much. The relation between the densities is the invariant; the residual
    against the true height is a separate, bounded claim, because the median
    filter shortens a ramp slightly at both of its ends.
    """
    gains = {}
    for count in (20, 200, 2000):
        elevations = [100.0 + 100.0 * index / (count - 1) for index in range(count)]
        gain = analyse_elevation([_profile(elevations, spacing=1000.0 / count)]).gain_metres
        assert gain is not None
        gains[count] = gain

    assert max(gains.values()) == pytest.approx(min(gains.values()), rel=0.15), (
        f"the ascent moved with the sampling rate: {gains}"
    )
    for count, gain in gains.items():
        assert gain <= 100.0 + ELEVATION_DEADBAND_METRES, f"{count} points over-reported the hill"
        assert gain >= 100.0 - 3 * ELEVATION_DEADBAND_METRES, f"{count} points lost the hill"


def test_a_long_gentle_climb_is_not_swallowed_by_the_deadband() -> None:
    """A hundred metres climbed one metre at a time is still a hundred metres.

    Every single step is far inside the deadband. A filter that compared each
    step against the band rather than accumulating towards it would report this
    real climb as flat ground.
    """
    elevations = [100.0 + float(index) for index in range(101)]

    elevation = analyse_elevation([_profile(elevations)])

    assert elevation.gain_metres is not None
    assert elevation.gain_metres == pytest.approx(100.0, abs=2 * ELEVATION_DEADBAND_METRES)


def test_one_impossible_sample_is_not_a_mountain() -> None:
    """A single position at 500 m in the middle of a plateau is a defect."""
    elevations = [100.0, 101.0, 100.0, 500.0, 100.0, 101.0, 100.0]

    elevation = analyse_elevation([_profile(elevations)])

    assert elevation.gain_metres == pytest.approx(0.0)
    assert elevation.loss_metres == pytest.approx(0.0)
    assert elevation.maximum_metres == pytest.approx(500.0), (
        "the extremes report what was recorded, filtered or not"
    )


def test_terrain_that_rolls_further_than_the_deadband_is_counted() -> None:
    """The other side of the trade the deadband makes.

    Undulation inside the band cannot be told from receiver noise and is not
    counted. Undulation that leaves the band is terrain, and it is.
    """
    amplitude = 3.0 * ELEVATION_DEADBAND_METRES
    elevations = [100.0 + amplitude * math.sin(index / 20.0) for index in range(400)]

    elevation = analyse_elevation([_profile(elevations)])

    assert elevation.gain_metres is not None
    assert elevation.gain_metres > 4 * amplitude


def test_a_missing_altitude_never_bridges_into_a_climb() -> None:
    """Skipping a hole is not the same as drawing a line through it."""
    segment = TrackSegment(
        points=(
            _at(0.0, elevation=100.0),
            _at(10.0, elevation=None),
            _at(20.0, elevation=None),
            _at(30.0, elevation=104.0),
        )
    )

    elevation = analyse_elevation([segment])

    assert elevation.gain_metres == pytest.approx(0.0)
    assert elevation.minimum_metres == pytest.approx(100.0)
    assert elevation.maximum_metres == pytest.approx(104.0)


def test_segments_never_climb_the_step_between_them() -> None:
    """A recording resumed eight hundred metres higher did not climb there."""
    low = _profile([100.0, 101.0, 100.0])
    high = _profile([900.0, 901.0, 900.0])

    elevation = analyse_elevation([low, high])

    assert elevation.gain_metres == pytest.approx(0.0)
    assert elevation.minimum_metres == pytest.approx(100.0)
    assert elevation.maximum_metres == pytest.approx(901.0)
