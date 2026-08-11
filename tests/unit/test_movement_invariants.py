"""Invariants the movement and speed algorithms must hold for any track.

The example-by-example tests beside this one pin what a handful of fixtures
produce. They cannot catch the failure this file exists for: an algorithm that
is right on a straight line and systematically wrong on every path that bends.

The tests here are therefore *metamorphic* and *adversarial* rather than
snapshot-based. A metamorphic test states a relation between two analyses of
related inputs -- the same walk resampled, the same clock shifted -- so it stays
meaningful when the numbers themselves legitimately change. An adversarial
fixture is a shape a real recording produces and a naive implementation gets
wrong: a park loop, a switchback, a receiver lying on a table.

Every fixture is synthetic. Nothing here is derived from a real recording.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest

from trackvault.domain import TrackPoint, TrackSegment
from trackvault.domain.analysis import analyse_movement

pytestmark = [pytest.mark.unit, pytest.mark.analysis]

START = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)
LATITUDE = 52.5
METRES_PER_LATITUDE_DEGREE = 111_195.0
WALKING_MPS = 1.4

# The classifier's threshold is 0.5 m/s. Fixtures state what they are, and the
# assertions ask for a verdict, so a future threshold change does not silently
# turn one of these into a test of nothing.
SLOW_WALK_MPS = 0.9


def _at(north_metres: float, east_metres: float, second: float) -> TrackPoint:
    """Build one position from a local offset in metres, north and east."""
    latitude = LATITUDE + north_metres / METRES_PER_LATITUDE_DEGREE
    longitude = east_metres / (METRES_PER_LATITUDE_DEGREE * math.cos(math.radians(LATITUDE)))
    return TrackPoint(
        latitude=latitude,
        longitude=longitude,
        elevation=100.0,
        time=START + timedelta(seconds=second),
    )


def _segment(points: list[TrackPoint]) -> TrackSegment:
    """Wrap positions into the one segment a fixture usually needs."""
    return TrackSegment(points=tuple(points))


def _straight(seconds: int, *, mps: float = WALKING_MPS, step: int = 1) -> list[TrackPoint]:
    """Walk steadily north."""
    return [_at(mps * second, 0.0, second) for second in range(0, seconds + 1, step)]


def _circle(seconds: int, *, radius: float, lap_seconds: float) -> list[TrackPoint]:
    """Walk a closed loop of a stated radius, over and over.

    A park circuit, a running track, a dog being walked round a green. The net
    displacement across one lap is zero while the walker never stops.
    """
    points = []
    for second in range(seconds + 1):
        angle = 2.0 * math.pi * (second % lap_seconds) / lap_seconds
        points.append(_at(radius * math.sin(angle), radius * math.cos(angle), second))
    return points


def _out_and_back(seconds: int, *, leg_seconds: float, mps: float) -> list[TrackPoint]:
    """Walk out and back again, repeatedly."""
    points = []
    for second in range(seconds + 1):
        phase = second % (2 * leg_seconds)
        north = mps * (phase if phase <= leg_seconds else 2 * leg_seconds - phase)
        points.append(_at(north, 0.0, second))
    return points


def _switchbacks(seconds: int, *, leg_seconds: float, mps: float) -> list[TrackPoint]:
    """Climb a zig-zag: each leg reverses the east-west direction.

    A hairpin trail gains height steadily while the net displacement across a
    window is a fraction of the distance actually walked.
    """
    points = []
    north = 0.0
    east = 0.0
    for second in range(seconds + 1):
        heading = 1.0 if (second // leg_seconds) % 2 == 0 else -1.0
        points.append(_at(north, east, second))
        north += mps * 0.25
        east += mps * heading * 0.97
    return points


def _jitter(seconds: int, *, metres: float) -> list[TrackPoint]:
    """A receiver lying still while its fix wanders.

    Deterministic rather than random: a seeded generator would still be a
    generator, and this suite may not depend on a random seed. The wander is a
    pair of incommensurable rotations, which produces a bounded cloud that never
    repeats and never drifts.
    """
    return [
        _at(
            metres * math.sin(second * 2.399),
            metres * math.cos(second * 1.021),
            second,
        )
        for second in range(seconds + 1)
    ]


def _decomposition(points: list[TrackPoint]) -> tuple[float, float, float, float]:
    """Return the four durations one segment of positions decomposes into."""
    analysis = analyse_movement([_segment(points)])
    assert analysis.moving_seconds is not None
    assert analysis.stopped_seconds is not None
    assert analysis.unobserved_seconds is not None
    assert analysis.elapsed_seconds is not None
    return (
        analysis.moving_seconds,
        analysis.stopped_seconds,
        analysis.unobserved_seconds,
        analysis.elapsed_seconds,
    )


# --- Real movement must survive a bending path ------------------------------


def test_a_steady_walk_in_a_straight_line_is_moving() -> None:
    """The baseline every other case is measured against."""
    moving, stopped, _, elapsed = _decomposition(_straight(600))

    assert moving == pytest.approx(elapsed), "a continuous walk lost time to standing still"
    assert stopped == pytest.approx(0.0)


def test_walking_a_small_loop_is_movement_not_a_pause() -> None:
    """A park circuit ends where it started. The walker did not stand there.

    Net displacement across a window is close to zero for any loop whose lap
    fits inside the window, so an algorithm that only measures start-to-end
    displacement reports a ten-minute walk as ten minutes of standing.
    """
    moving, stopped, _, elapsed = _decomposition(
        _circle(600, radius=8.0, lap_seconds=30.0),
    )

    assert moving > 0.9 * elapsed, (
        f"walking a closed loop was classified as {stopped:.0f}s stopped "
        f"out of {elapsed:.0f}s -- low net displacement is not a stop"
    )


def test_walking_out_and_back_within_one_window_is_movement() -> None:
    """Out for fifteen seconds, back for fifteen, over and over.

    The window sees the walker return to where they were. They were still
    walking the whole time.
    """
    moving, _, _, elapsed = _decomposition(
        _out_and_back(600, leg_seconds=15.0, mps=WALKING_MPS),
    )

    assert moving > 0.9 * elapsed


def test_climbing_switchbacks_is_movement() -> None:
    """A hairpin trail: much walking, little net progress in any direction."""
    moving, _, _, elapsed = _decomposition(
        _switchbacks(600, leg_seconds=12.0, mps=WALKING_MPS),
    )

    assert moving > 0.9 * elapsed


def test_a_slow_but_genuine_walk_is_still_movement() -> None:
    """Well above the threshold, well below a brisk pace."""
    moving, _, _, elapsed = _decomposition(_straight(600, mps=SLOW_WALK_MPS))

    assert moving > 0.9 * elapsed


# --- Standing still must stay standing still --------------------------------


def test_a_stationary_receiver_is_not_walking() -> None:
    """The reason the window exists, and it has to keep working.

    Consecutive fixes several metres apart imply a walking pace point to point.
    The receiver is on a table.
    """
    moving, stopped, _, elapsed = _decomposition(_jitter(600, metres=4.0))

    assert stopped > 0.9 * elapsed, (
        f"receiver noise was read as {moving:.0f}s of movement out of {elapsed:.0f}s"
    )


def test_a_stop_between_two_walks_is_found() -> None:
    """Walk, stand still, walk again. The middle is a stop, not a gap."""
    walk_out = _straight(120)
    pause = [_at(WALKING_MPS * 120.0, 0.0, second) for second in range(121, 301)]
    walk_on = [_at(WALKING_MPS * (120.0 + second - 300), 0.0, second) for second in range(301, 421)]

    moving, stopped, unobserved, _ = _decomposition(walk_out + pause + walk_on)

    assert stopped == pytest.approx(180.0, abs=35.0), "the standing still was not found"
    assert moving == pytest.approx(240.0, abs=35.0)
    assert unobserved == 0.0, "positions kept arriving, so nothing was unobserved"


# --- Metamorphic relations --------------------------------------------------


@pytest.mark.parametrize("step", [1, 5, 10])
def test_resampling_the_same_walk_leaves_movement_alone(step: int) -> None:
    """The same walk logged more or less often is the same walk."""
    reference_moving, _, _, reference_elapsed = _decomposition(_straight(600, step=1))
    moving, _, _, elapsed = _decomposition(_straight(600, step=step))

    assert elapsed == pytest.approx(reference_elapsed)
    assert moving == pytest.approx(reference_moving, rel=0.1)


def test_shifting_every_timestamp_changes_nothing_but_the_clock() -> None:
    """A track analysed a hundred days later is the same track."""
    original = analyse_movement([_segment(_straight(600))])
    shifted = analyse_movement(
        [
            _segment(
                [
                    TrackPoint(
                        latitude=point.latitude,
                        longitude=point.longitude,
                        elevation=point.elevation,
                        time=None if point.time is None else point.time + timedelta(days=100),
                    )
                    for point in _straight(600)
                ]
            )
        ]
    )

    assert shifted.moving_seconds == pytest.approx(original.moving_seconds)
    assert shifted.stopped_seconds == pytest.approx(original.stopped_seconds)
    assert shifted.elapsed_seconds == pytest.approx(original.elapsed_seconds)
    assert shifted.maximum_speed_mps == pytest.approx(original.maximum_speed_mps)
    assert original.first_observed_at is not None
    assert shifted.first_observed_at == original.first_observed_at + timedelta(days=100)


def test_reversing_a_walk_preserves_its_movement() -> None:
    """Walking the same path the other way took the same time and effort."""
    points = _straight(600)
    forward = analyse_movement([_segment(points)])
    backward_points = [
        TrackPoint(
            latitude=point.latitude,
            longitude=point.longitude,
            elevation=point.elevation,
            time=START + timedelta(seconds=600) - (point.time - START)  # type: ignore[operator]
            if point.time is not None
            else None,
        )
        for point in reversed(points)
    ]
    backward = analyse_movement([_segment(backward_points)])

    assert backward.moving_seconds == pytest.approx(forward.moving_seconds, rel=0.05)
    assert backward.elapsed_seconds == pytest.approx(forward.elapsed_seconds)


# --- Speed --------------------------------------------------------------


def test_a_sustained_fast_section_decides_the_maximum() -> None:
    """Walk, run for three minutes, walk again.

    The run is real, sustained and far above the track's median speed. An
    outlier rule that rejects it because it is unlike the rest of the track has
    thrown away the answer to the question being asked.
    """
    points = _straight(300)
    north = WALKING_MPS * 300.0
    for second in range(301, 481):
        north += 4.0
        points.append(_at(north, 0.0, second))
    for second in range(481, 781):
        north += WALKING_MPS
        points.append(_at(north, 0.0, second))

    analysis = analyse_movement([_segment(points)])

    assert analysis.maximum_speed_mps is not None
    assert analysis.maximum_speed_mps == pytest.approx(4.0, rel=0.15), (
        "a genuine three-minute run was filtered away as an outlier"
    )
    assert not analysis.outliers_excluded


def test_a_bimodal_track_keeps_its_faster_half() -> None:
    """Half an hour of walking, then half an hour of cycling.

    The fast half is more than half the track, so a rule that rejects whatever
    is far from the median has to decide *which* mode is the outlier. Neither
    is: both are real.
    """
    points = _straight(1800)
    north = WALKING_MPS * 1800.0
    for second in range(1801, 3601):
        north += 6.0
        points.append(_at(north, 0.0, second))

    analysis = analyse_movement([_segment(points)])

    assert analysis.maximum_speed_mps is not None
    assert analysis.maximum_speed_mps == pytest.approx(6.0, rel=0.15)


def test_one_bad_fix_does_not_become_the_maximum_speed() -> None:
    """A fix that jumps a kilometre and returns is a defect, not a sprint."""
    points = _straight(300)
    points.append(_at(WALKING_MPS * 300.0 + 1000.0, 0.0, 301))
    points += [_at(WALKING_MPS * second, 0.0, second) for second in range(302, 601)]

    analysis = analyse_movement([_segment(points)])

    assert analysis.maximum_speed_mps is not None
    assert analysis.maximum_speed_mps < 3.0, "a single teleport decided the headline speed"
    assert analysis.outliers_excluded


def test_positions_sharing_one_instant_produce_no_infinite_speed() -> None:
    """Two places, one timestamp. There is no speed there to report."""
    points = [
        _at(0.0, 0.0, 0),
        _at(10.0, 0.0, 0),
        _at(20.0, 0.0, 1),
        _at(21.4, 0.0, 2),
    ]

    analysis = analyse_movement([_segment(points)])

    assert analysis.maximum_speed_mps is not None
    assert math.isfinite(analysis.maximum_speed_mps)


# --- The decomposition adds up ----------------------------------------------


def test_observed_time_is_fully_attributed() -> None:
    """Every second between the first and last observation has a name.

    Moving, stopped, unobserved or explicitly unattributed -- but never quietly
    missing. A total that does not add up is how lost time hides.
    """
    points = _straight(300)
    points.append(_at(WALKING_MPS * 300.0 + 1000.0, 0.0, 301))
    points += [_at(WALKING_MPS * second, 0.0, second) for second in range(302, 601)]
    analysis = analyse_movement([_segment(points)])

    assert analysis.elapsed_seconds is not None
    assert analysis.unattributed_seconds is not None
    total = (
        (analysis.moving_seconds or 0.0)
        + (analysis.stopped_seconds or 0.0)
        + (analysis.unobserved_seconds or 0.0)
        + analysis.unattributed_seconds
    )
    assert total == pytest.approx(analysis.elapsed_seconds), (
        "time went missing between the categories"
    )


def test_a_long_silence_is_unobserved_rather_than_a_pause() -> None:
    """Half an hour with no position is not proof anybody rested."""
    points = _straight(120)
    points += [
        _at(WALKING_MPS * 120.0 + WALKING_MPS * (second - 1920), 0.0, second)
        for second in range(1920, 2041)
    ]

    moving, stopped, unobserved, elapsed = _decomposition(points)

    assert unobserved == pytest.approx(1800.0, abs=5.0)
    assert stopped < 60.0
    assert moving + stopped + unobserved == pytest.approx(elapsed)


def test_time_never_runs_backwards_into_the_decomposition() -> None:
    """A clock that jumps back must not inflate any duration.

    The intervals around the jump are unusable, and the honest answer is to
    exclude them. Taking an absolute value would turn a defect into data.
    """
    points = [
        _at(0.0, 0.0, 0),
        _at(WALKING_MPS * 5, 0.0, 5),
        _at(WALKING_MPS * 6, 0.0, -1),
        _at(WALKING_MPS * 20, 0.0, 10),
    ]

    analysis = analyse_movement([_segment(points)])

    assert analysis.non_monotonic_timestamps
    assert analysis.elapsed_seconds is not None
    for duration in (
        analysis.moving_seconds,
        analysis.stopped_seconds,
        analysis.unobserved_seconds,
    ):
        assert duration is not None
        assert duration >= 0.0
        assert duration <= analysis.elapsed_seconds, (
            "a duration outgrew the span it has to fit inside"
        )
