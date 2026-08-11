"""Adversarial matrix: very slow movement against stationary receiver noise.

A single windowed speed threshold makes one statement about the world:

```
anything below 0.5 m/s is standing still
```

That is an algorithmic claim, not a fact, and it is wrong for a large part of
what an activity archive holds -- a scrambling section, a heavily loaded ascent,
a dog walk, a scooter being pushed uphill. Steady progression at 0.3 m/s is
movement, and a hard cut reports ten minutes of it as ten minutes of standing
still.

The reason a cut existed is real: a receiver lying still reports positions
several metres apart, so no single interval can be believed. What separates the
two cases is not speed at one scale, it is **what the positional cloud does as
the window grows**:

```
stationary receiver   the cloud is bounded; watching longer does not widen it
slow progression      the spread grows with the time it is measured over
```

So the question this suite asks of every fixture is whether the archive can tell
a bounded cloud from a growing one without being told what activity it is
looking at -- and, where it genuinely cannot, whether it says so instead of
guessing. `unattributed` is a real answer here: a small circle walked slowly is
not distinguishable from noise by any amount of arithmetic, and calling it
`stopped` would be a claim the data does not support.

Every fixture is synthetic. Nothing here is derived from a real recording.
"""

import math
from datetime import UTC, datetime, timedelta

import pytest

from gpx_view.domain import TrackPoint, TrackSegment
from gpx_view.domain.analysis import analyse_movement
from gpx_view.domain.analysis.movement import (
    MOVEMENT_SPEED_THRESHOLD_MPS,
    MOVEMENT_WINDOW_SECONDS,
    POSITION_NOISE_METRES,
)

pytestmark = [pytest.mark.unit, pytest.mark.analysis, pytest.mark.regression]

START = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)
LATITUDE = 52.5
METRES_PER_LATITUDE_DEGREE = 111_195.0

TEN_MINUTES = 600


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


def _straight(seconds: int, *, mps: float, step: float = 1.0) -> list[TrackPoint]:
    """Walk steadily north at a stated speed."""
    count = int(seconds / step) + 1
    return [_at(mps * index * step, 0.0, index * step) for index in range(count)]


def _wander(seconds: int, *, extent: float) -> list[TrackPoint]:
    """A receiver lying still while its fix wanders inside a bounded cloud.

    Deterministic rather than random -- this suite may not depend on a seed. Two
    incommensurable rotations produce a cloud that never repeats and never
    drifts, which is the defining property: watching it for an hour does not
    make it wider than watching it for a minute.
    """
    # The wander fills a square, and the widest spread of a square is its
    # diagonal -- which is what the analysis measures. The fixture is stated in
    # terms of that spread, so "an eight-metre cloud" means eight metres.
    radius = extent / (2.0 * math.sqrt(2.0))
    return [
        _at(radius * math.sin(second * 2.399), radius * math.cos(second * 1.021), second)
        for second in range(seconds + 1)
    ]


def _switchbacks(
    seconds: int, *, leg_seconds: int, mps: float, climb: float = 0.6
) -> list[TrackPoint]:
    """Climb a zig-zag slowly: each leg reverses the east-west direction.

    ``climb`` is how much of the walking is spent gaining ground rather than
    traversing -- the fraction of the path speed that becomes net progress. A
    hairpin trail is around 0.6; a near-horizontal traverse is much less, and
    that difference is the point of the two fixtures that use this.
    """
    across = math.sqrt(max(0.0, 1.0 - climb * climb))
    points = []
    north = 0.0
    east = 0.0
    for second in range(seconds + 1):
        heading = 1.0 if (second // leg_seconds) % 2 == 0 else -1.0
        points.append(_at(north, east, second))
        north += mps * climb
        east += mps * heading * across
    return points


def _out_and_back(seconds: int, *, leg_seconds: int, mps: float) -> list[TrackPoint]:
    """Walk out and back again slowly, repeatedly."""
    points = []
    for second in range(seconds + 1):
        phase = second % (2 * leg_seconds)
        north = mps * (phase if phase <= leg_seconds else 2 * leg_seconds - phase)
        points.append(_at(north, 0.0, second))
    return points


def _circle(seconds: int, *, radius: float, mps: float) -> list[TrackPoint]:
    """Walk slowly round a circle small enough to fit inside the window's reach."""
    lap_seconds = 2.0 * math.pi * radius / mps
    return [
        _at(
            radius * math.sin(2.0 * math.pi * second / lap_seconds),
            radius * math.cos(2.0 * math.pi * second / lap_seconds),
            second,
        )
        for second in range(seconds + 1)
    ]


def _shares(points: list[TrackPoint]) -> tuple[float, float, float]:
    """Return the moving, stopped and unattributed shares of one fixture's span."""
    analysis = analyse_movement([TrackSegment(points=tuple(points))])
    elapsed = analysis.elapsed_seconds
    assert elapsed is not None and elapsed > 0.0
    assert analysis.moving_seconds is not None
    assert analysis.stopped_seconds is not None
    assert analysis.unattributed_seconds is not None
    return (
        analysis.moving_seconds / elapsed,
        analysis.stopped_seconds / elapsed,
        analysis.unattributed_seconds / elapsed,
    )


# --- Steady slow progression is movement -------------------------------------


@pytest.mark.parametrize("mps", [0.25, 0.30, 0.40, 0.49, 0.50, 0.90])
def test_steady_progression_is_not_reported_as_standing_still(mps: float) -> None:
    """Ten minutes of steady progress, at six speeds either side of the old cut.

    The old rule answered "0 seconds moving" for the first four of these and
    "600 seconds moving" for the last two, on a difference of one centimetre per
    second. Nothing in the world changes that sharply.
    """
    moving, stopped, _ = _shares(_straight(TEN_MINUTES, mps=mps))

    assert moving > 0.8, f"{mps} m/s of steady progress was not movement"
    assert stopped < 0.2, f"{mps} m/s of steady progress was called a stop"


def test_the_old_threshold_is_no_longer_a_cliff() -> None:
    """One centimetre per second must not decide ten minutes of a track."""
    below, _, _ = _shares(_straight(TEN_MINUTES, mps=0.49))
    above, _, _ = _shares(_straight(TEN_MINUTES, mps=0.50))

    assert abs(above - below) < 0.1, "a hundredth of a metre per second flipped the verdict"


# --- Bounded noise is still not movement -------------------------------------


@pytest.mark.parametrize("extent", [2.0, 4.0, 8.0])
def test_a_stationary_receiver_stays_stationary(extent: float) -> None:
    """The case the hard cut existed for. It must survive the cut's removal."""
    moving, stopped, _ = _shares(_wander(TEN_MINUTES, extent=extent))

    assert stopped > 0.8, f"a {extent} m cloud was read as a walk"
    assert moving < 0.1


def test_a_cloud_wider_than_the_receiver_envelope_is_not_claimed_either_way() -> None:
    """Beyond what a receiver explains, the honest answer is that we cannot tell.

    The bound this build claims is where the two rules meet: a cloud narrower
    than thirty seconds at the short threshold -- fifteen metres -- is never
    movement, and one the envelope explains is a stop. Between the two the
    answer is that nothing here decides it.
    """
    unexplained = POSITION_NOISE_METRES + 2.0
    assert unexplained < MOVEMENT_SPEED_THRESHOLD_MPS * MOVEMENT_WINDOW_SECONDS

    moving, stopped, unattributed = _shares(_wander(TEN_MINUTES, extent=unexplained))

    assert moving < 0.2, "an unexplained cloud was claimed as movement"
    assert unattributed > 0.5, "an unexplained cloud was claimed as a stop"
    assert stopped < 0.3


# --- Slow paths that bend ----------------------------------------------------


def test_a_slow_switchback_climb_survives() -> None:
    """A hairpin ascent progresses. Each leg on its own barely does.

    Ninety-second legs at a third of a metre per second: about thirty metres of
    trail between hairpins, which is what a hairpin is. A zig-zag whose legs are
    shorter than the receiver's own envelope is a different fixture and a
    different answer -- see the limitation this suite states below.
    """
    moving, stopped, _ = _shares(_switchbacks(TEN_MINUTES, leg_seconds=90, mps=0.35))

    assert moving > 0.6
    assert stopped < 0.2


def test_a_path_that_advances_more_slowly_than_it_walks_is_not_claimed() -> None:
    """The stated limitation, pinned so it stays a limitation and not a bug.

    What the two scales measure is *spread*, so a path that stays inside the
    receiver's own envelope is below what this build claims to detect: legs of
    fourteen metres, advancing at a twentieth of a metre per second. Nothing in
    the positions separates that from a drifting fix, and the archive says so
    rather than choosing.
    """
    moving, _, _ = _shares(_switchbacks(TEN_MINUTES, leg_seconds=40, mps=0.35, climb=0.15))

    assert moving < 0.2


def test_a_slow_out_and_back_survives() -> None:
    """Out and back has no net displacement and is not a pause."""
    moving, stopped, _ = _shares(_out_and_back(TEN_MINUTES, leg_seconds=120, mps=0.35))

    assert moving > 0.6
    assert stopped < 0.2


def test_a_slow_small_circle_is_admitted_to_be_undecidable() -> None:
    """The case arithmetic cannot solve, answered honestly rather than wrongly.

    A ten-metre circle walked at a third of a metre per second produces a
    bounded cloud, exactly as a receiver on a table does. The spread is wider
    than any receiver's noise, so calling it a stop would be a claim the data
    does not support -- and it is not growing, so calling it movement would be
    another. `unattributed` is the fourth answer, and it exists for this.
    """
    moving, stopped, unattributed = _shares(_circle(TEN_MINUTES, radius=10.0, mps=0.33))

    assert unattributed > 0.5, "an undecidable stretch was decided anyway"
    assert stopped < 0.3
    assert moving < 0.3


# --- Sampling rate is not the subject ----------------------------------------


@pytest.mark.parametrize("step", [0.2, 1.0, 5.0, 30.0])
def test_slow_progression_is_found_at_every_sampling_rate(step: float) -> None:
    """The same walk, sampled from five times a second to twice a minute."""
    moving, _, _ = _shares(_straight(TEN_MINUTES, mps=0.35, step=step))

    assert moving > 0.7, f"a {step} s sampling interval hid the movement"


@pytest.mark.parametrize("step", [1.0, 5.0])
def test_stationary_noise_stays_stationary_at_every_sampling_rate(step: float) -> None:
    """Sampling a still receiver more often must not turn it into a walker."""
    points = _wander(TEN_MINUTES, extent=6.0)
    sampled = [point for index, point in enumerate(points) if index % int(step) == 0]

    moving, stopped, _ = _shares(sampled)

    assert stopped > 0.8
    assert moving < 0.1


# --- A stop inside slow movement is still a stop -----------------------------


def test_a_rest_inside_a_slow_walk_is_still_a_rest() -> None:
    """Reading a longer window must not smear a real stop into movement."""
    first = _straight(300, mps=0.35)
    resting = [
        _at(
            0.35 * 300 + 2.0 * math.sin(second * 2.399),
            2.0 * math.cos(second * 1.021),
            300 + second,
        )
        for second in range(1, 301)
    ]

    moving, stopped, _ = _shares(first + resting)

    assert stopped > 0.3, "a five-minute rest was absorbed into the walk"
    assert moving > 0.3, "the five minutes of walking were absorbed into the rest"
