"""Observed time, movement and speed, derived without trusting a single fix.

## Four different statements about time

```
elapsed        first temporal observation -> last temporal observation
moving         observed, and the track was going somewhere
stopped        observed, and it was not
unobserved     nothing was recorded at all
unattributed   observed, and the data does not support any of the three
```

Keeping `stopped` and `unobserved` apart is the point of this module. A
twenty-minute silence in a recording is not proof of a rest: the recording was
paused, or the battery saver stopped it, or the receiver lost its fix, or the
phone went into a pocket that blocked the sky. All four produce identical data,
so the honest name for that time is *unobserved*. A stop is only claimed where
positions kept arriving and showed no movement.

The fifth name exists so that the four add up:

```
moving + stopped + unobserved + unattributed == elapsed
```

An equality, not an inequality. Time that no rule can classify -- either side of
a clock that jumped backwards, an interval too fast to be believed, a hole
between two positions that carry no instant -- is *named* rather than quietly
dropped. A decomposition that does not add up is how lost time hides.

## Why a point-to-point speed is not enough

A receiver standing still on a table reports positions three to five metres
apart every second. Point to point, that is three to five metres per second --
faster than a walk. Any threshold applied to single intervals therefore counts
a coffee break as a hike.

What separates noise from travel is *how far the receiver got* over a stretch of
time, rather than how far it wobbled between two samples. Over thirty seconds a
stationary receiver's positions stay inside a cloud a few metres across; a
walker's spread out over forty metres.

## Why the spread, and not the start-to-end displacement

Measuring where the window *ended up* relative to where it started is the
obvious version of that idea, and it is wrong for every path that bends back on
itself:

```
park circuit      walks a lap, ends where it started  -> displacement 0
out and back      fifteen seconds each way            -> displacement 0
switchback trail  climbs by reversing every leg       -> displacement small
```

None of those is a pause, and a rule built on start-to-end displacement calls
all three of them standing still. This module therefore measures the **extent**
of the window: the greatest straight-line spread the positions in it cover. A
lap spreads across its own diameter, an out-and-back across its turning point,
and a straight walk across exactly the distance it travelled -- so the ordinary
case keeps the ordinary answer while the bending ones stop being wrong.

The extent is measured as the widest spread along four evenly spaced compass
axes. That is a lower bound on the true spread and never an overestimate, and it
is within 8 % of it, so a maximum speed built on it can be a little conservative
but never inflated. For a straight stretch it is exact.

The cost is stated rather than hidden: movement confined to a circle smaller
than the window's reach is reported at the speed its *extent* implies, not the
speed its path length implies. Somebody running tight laps of a twenty-metre
circle is moving, and this says so, but it will not say how fast.

The same window is what makes the maximum speed survivable: a bad fix that jumps
a kilometre is excluded outright before any window sees it.

## Why one window is not enough

A single threshold on a single window makes an unstated claim about the world:

```
anything below 0.5 m/s is standing still
```

That is an algorithmic decision presented as a fact, and it is wrong for a good
deal of what an archive holds -- a scramble, a loaded ascent, a pushed bicycle,
a dog walk. It also turns a hundredth of a metre per second into the difference
between ten minutes of walking and ten minutes of standing still, which nothing
in the world does.

The cut existed for a real reason: over thirty seconds, slow progress and
receiver noise cover a similar spread. What separates them is what the spread
does when you watch for longer:

```
still receiver    the cloud is bounded; three minutes are no wider than thirty seconds
slow progression  the spread keeps growing, at the rate the walker advances
```

So the spread is measured at two scales, and the wander a still receiver
produces anyway -- `POSITION_NOISE_METRES` -- is taken off before a rate is
computed, which turns the remainder into a statement about *progress*:

```
thirty seconds already show 0.5 m/s              -> moving
three minutes show 0.1 m/s of net progress       -> moving
three minutes stay inside the receiver envelope  -> stopped
neither                                          -> unattributed
```

The long window is measured only across consecutive intervals the short one
could not settle, and clipped to them. That is what keeps a rest inside a walk a
rest: the slow stretch a rest forms is bounded by the moving intervals either
side, so the longer window never reaches them and never smears their movement
over it.

The fourth answer is used rather than avoided. A ten-metre circle walked slowly
produces a bounded cloud exactly as a receiver on a table does, and no amount of
arithmetic separates them; calling it a stop would be a claim the positions do
not support. What this module claims to detect is bounded, deliberately: net
progress below `SLOW_MOVEMENT_SPEED_MPS`, or any path that stays inside the
receiver's own envelope, is admitted as undecidable.

## Why implausible intervals are excluded rather than capped

An outlier ceiling scaled to the track itself -- a multiple of the median
interval speed -- is activity-agnostic by construction. A walk at 1.4 m/s
rejects a 400 m/s teleport; a fast descent on a bicycle rejects the same
teleport without rejecting the descent. No table of per-activity speed limits is
needed, and none is written, because such a table would be a hidden second
authority on what a track "is".

An excluded interval is an analysis decision and nothing more. It never removes
a position: the normalized geometry is the source of truth and stays intact, so
a better algorithm can revisit exactly the same data later.
"""

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import cos, pi, radians, sin
from statistics import median

from trackvault.domain.analysis.distance import EARTH_RADIUS_METRES, geodesic_distance
from trackvault.domain.geometry import TrackPoint, TrackSegment

MOVEMENT_WINDOW_SECONDS = 30.0
"""How much time the positional spread is measured over.

Long enough that stationary receiver noise averages out, short enough that a
genuine stop of a minute is still visible as one.
"""

SUSTAINED_WINDOW_SECONDS = 180.0
"""The second scale the positional spread is measured over.

Six times the short window. A stationary receiver's cloud is the same width at
either scale; a walker's spread is six times wider at this one. That difference
*is* the discrimination, and it is why one scale cannot do this job.
"""

POSITION_NOISE_METRES = 12.0
"""How far a stationary receiver's fix may wander before the wander is movement.

A property of satellite positioning, not of any activity: consumer receivers put
a still device inside a cloud a few metres across, and a cloud several times that
is not something a receiver produces by standing still. Subtracting it before a
rate is computed is what lets the thresholds below be about *progress* rather
than about noise -- without it, a wider cloud simply looks like a faster walk.

Everything beyond it is admitted as unexplained rather than claimed. A spread
this cannot account for is not thereby movement.
"""

MOVEMENT_SPEED_THRESHOLD_MPS = 0.5
"""Windowed speed at or above which thirty seconds already settle the question.

Fifteen metres of spread in half a minute: more than a still receiver produces,
so no noise argument is needed to believe it. This rule exists beside the slower
one below because a long window saturates on anything that circles -- somebody
running laps of a small track has a bounded spread at every scale, and only the
short window shows how quickly they cover it.

It is deliberately compared against the raw spread rather than the noise-adjusted
one. Fifteen metres already exceeds ``POSITION_NOISE_METRES``, so the two rules
agree about what a receiver can produce, and subtracting the envelope twice would
turn a walked circuit back into a pause.

One value, in one place, for every activity -- a per-activity table would decide
what a track is, which is the classification's job and not this module's.
"""

SLOW_MOVEMENT_SPEED_MPS = 0.1
"""Progress rate, net of the noise envelope, that a long window has to show.

Deliberately low, because over three minutes it is a *provable* statement rather
than a hopeful one: net progress of eighteen metres beyond the noise envelope is
not something a still receiver produces, while it is a tenth of what a slow walk
covers. The threshold bounds what this build claims to detect, not what counts
as walking.
"""

UNOBSERVED_GAP_FLOOR_SECONDS = 60.0
UNOBSERVED_GAP_SAMPLING_FACTOR = 10.0
"""When silence stops being a sample interval and becomes a gap.

A fixed threshold alone would be wrong in both directions: it would call a track
deliberately sampled every five minutes one long gap, and it would miss a
thirty-second dropout in one-second data. The threshold is therefore scaled to
what this track's own sampling looks like, with a floor so that dense data does
not turn every hesitation into a gap.
"""

OUTLIER_SPEED_FACTOR = 10.0
OUTLIER_SPEED_FLOOR_MPS = 10.0
MAX_PLAUSIBLE_SPEED_MPS = 150.0
"""How far above its own track an interval may be before it is not believed.

The factor scales the ceiling to the activity without naming one. The floor
keeps a mostly-stationary track from rejecting the little real movement it has,
and the absolute ceiling means no median can make 540 km/h believable.
"""

_DEGREE_METRES = EARTH_RADIUS_METRES * pi / 180.0
"""Metres per degree of latitude on the sphere the distance formula uses."""

_HALF_TURN_DEGREES = 180.0
_FULL_TURN_DEGREES = 360.0

_AXES = 4
"""How many directions the positional spread is measured along.

Four evenly spaced axes -- north, north-east, east, south-east -- bound the true
spread from below by ``cos(22.5 degrees)``, so the estimate is at most 8 % short
and never long. Two axes would be a bounding box, which is exact for a straight
stretch and up to 41 % *over* for a curved one, and overstating a maximum speed
is the one direction this must not err in.
"""

_AXIS_UNITS = tuple((cos(pi * index / _AXES), sin(pi * index / _AXES)) for index in range(_AXES))
"""Unit vectors of the measuring axes, half a turn spread evenly.

Only half a turn: an axis and its opposite measure the same spread.
"""


class _Interval(Enum):
    """What one step between two consecutive timed positions turned out to be."""

    OBSERVED = "observed"
    GAP = "gap"
    INVALID = "invalid"
    OUTLIER = "outlier"


class _Attribution(Enum):
    """Which of the four durations a stretch of the timeline belongs to."""

    MOVING = "moving"
    STOPPED = "stopped"
    UNOBSERVED = "unobserved"
    UNATTRIBUTED = "unattributed"


@dataclass(frozen=True, slots=True)
class MovementAnalysis:
    """The temporal picture of one track.

    Durations are ``None`` when the data could not support them, never ``0.0``:
    "nobody moved" and "we cannot tell" are different answers.

    Attributes:
        first_observed_at: First instant any position carried, in traversal order.
        last_observed_at: Last instant any position carried.
        elapsed_seconds: From the first observation to the last.
        moving_seconds: Observed time spent moving.
        stopped_seconds: Observed time spent stationary.
        unobserved_seconds: Time no position was recorded for.
        unattributed_seconds: Elapsed time that none of the other three could
            claim, because the data underneath it was unusable. Reported so that
            the four add up to the elapsed duration exactly.
        moving_distance_metres: Distance covered during the moving intervals,
            which is what a moving average may be built from.
        maximum_speed_mps: Highest speed sustained across the analysis window.
        missing_timestamps: Some positions carried no instant.
        non_monotonic_timestamps: Time ran backwards somewhere.
        large_unobserved_gaps: At least one gap exceeded this track's sampling.
        outliers_excluded: At least one interval was too fast to be believed.
    """

    first_observed_at: datetime | None
    last_observed_at: datetime | None
    elapsed_seconds: float | None
    moving_seconds: float | None
    stopped_seconds: float | None
    unobserved_seconds: float | None
    unattributed_seconds: float | None
    moving_distance_metres: float | None
    maximum_speed_mps: float | None
    missing_timestamps: bool = False
    non_monotonic_timestamps: bool = False
    large_unobserved_gaps: bool = False
    outliers_excluded: bool = False


@dataclass(slots=True)
class _Step:
    """One step between two consecutive timed positions of the same run."""

    seconds: float
    distance: float
    kind: _Interval


@dataclass(frozen=True, slots=True)
class _Stretch:
    """One labelled piece of the timeline, in seconds from the track's start."""

    start: float
    end: float
    attribution: _Attribution


def analyse_movement(segments: Sequence[TrackSegment]) -> MovementAnalysis:
    """Decompose a track's time and derive its movement and speed.

    Args:
        segments: The normalized geometry, in source order. Nothing else is
            read, which is what keeps the analysis format-independent.

    Returns:
        The temporal picture, with every quantity that could not be derived
        reported as absent rather than as zero.
    """
    runs = _timed_runs(segments)
    first, last = _observed_extent(segments)
    elapsed = _elapsed(first, last)
    missing = any(point.time is None for segment in segments for point in segment.points)

    steps = [_steps_of(run.points) for run in runs]
    _classify(steps)
    flat = [step for run_steps in steps for step in run_steps]

    # The quality of the input is reported whether or not the durations could be
    # derived from it. "We could not tell you how long this took" and "nothing
    # was wrong with the data" are separate statements, and a track whose clock
    # runs backwards has to be able to make the first without making the second.
    durations: dict[_Attribution, float] | None = None
    moving_distance: float | None = None
    maximum: float | None = None
    if runs and first is not None and elapsed is not None:
        stretches, moving_distance, maximum = _attribute(runs, steps, first)
        durations = _durations(stretches, elapsed)

    return MovementAnalysis(
        first_observed_at=first,
        last_observed_at=last,
        elapsed_seconds=elapsed,
        moving_seconds=None if durations is None else durations[_Attribution.MOVING],
        stopped_seconds=None if durations is None else durations[_Attribution.STOPPED],
        unobserved_seconds=None if durations is None else durations[_Attribution.UNOBSERVED],
        unattributed_seconds=None if durations is None else durations[_Attribution.UNATTRIBUTED],
        moving_distance_metres=moving_distance,
        maximum_speed_mps=maximum,
        missing_timestamps=missing,
        non_monotonic_timestamps=any(step.seconds < 0.0 for step in flat),
        large_unobserved_gaps=any(step.kind is _Interval.GAP for step in flat),
        outliers_excluded=any(step.kind is _Interval.OUTLIER for step in flat),
    )


def sustained_speeds(segments: Sequence[TrackSegment]) -> dict[tuple[int, int], float]:
    """Return the sustained speed at each position that has one.

    The series a chart draws and the maximum a headline quotes come from **one**
    rule: this runs the same window over the same believable intervals that
    :func:`analyse_movement` reads its maximum from. A chart plotting
    point-to-point speed under a headline labelled "maximum sustained speed"
    would be two different quantities sharing an axis, and the peak of the first
    is a bad fix while the peak of the second is a descent.

    Args:
        segments: The normalized geometry, in source order.

    Returns:
        Speeds in metres per second, keyed by ``(segment index, position index)``
        so a caller can address the same position the geometry does. A position
        whose interval was a gap, an outlier, a backwards clock or an untimed
        neighbour is **absent** rather than zero -- a speed that could not be
        derived is not a speed of nothing.
    """
    runs = _timed_runs(segments)
    steps = [_steps_of(run.points) for run in runs]
    _classify(steps)

    speeds: dict[tuple[int, int], float] = {}
    for run, run_steps in zip(runs, steps, strict=True):
        for group in _observed_groups(run.points, run_steps):
            base = run.start + group.offset
            for window in _windowed(group.points, MOVEMENT_WINDOW_SECONDS):
                position = base + window.index
                speeds[(run.segment, position)] = window.speed
                # The position an interval ends on inherits it, so the last
                # sample of a believable stretch carries a value too. The next
                # interval overwrites it with its own.
                speeds.setdefault((run.segment, position + 1), window.speed)
    return speeds


@dataclass(frozen=True, slots=True)
class _Group:
    """A run of consecutive believable intervals, and where it starts."""

    points: tuple[TrackPoint, ...]
    offset: int


@dataclass(frozen=True, slots=True)
class _Run:
    """A maximal run of adjacent timed positions, and where it sits.

    Attributes:
        segment: Which segment the run belongs to.
        start: Index of the run's first position within that segment, so a
            result can be reported against the geometry a reader sees.
        points: The positions themselves, all of them timed.
    """

    segment: int
    start: int
    points: tuple[TrackPoint, ...]


def _timed_runs(segments: Sequence[TrackSegment]) -> list[_Run]:
    """Return each maximal run of adjacent timed positions, with its segment.

    A position without an instant breaks the chain rather than being bridged
    over. The time on either side of it is real, but which part of it belongs to
    which interval is not knowable, and a bridge would quietly assert that it is.
    """
    runs: list[_Run] = []
    for index, segment in enumerate(segments):
        current: list[TrackPoint] = []
        start = 0
        for position, point in enumerate(segment.points):
            if point.time is None:
                if len(current) >= 2:
                    runs.append(_Run(segment=index, start=start, points=tuple(current)))
                current = []
                start = position + 1
            else:
                current.append(point)
        if len(current) >= 2:
            runs.append(_Run(segment=index, start=start, points=tuple(current)))
    return runs


def _observed_extent(
    segments: Iterable[TrackSegment],
) -> tuple[datetime | None, datetime | None]:
    """Return the first and last instant observed, in source traversal order.

    Traversal order rather than ``min`` and ``max`` on purpose: a track whose
    timestamps jump around has a defect, and taking the extremes would hide it
    behind a plausible-looking span.
    """
    instants = [point.time for segment in segments for point in segment.points if point.time]
    return (instants[0], instants[-1]) if instants else (None, None)


def _elapsed(first: datetime | None, last: datetime | None) -> float | None:
    """Return the elapsed duration, or ``None`` when it would not be one.

    A negative span means the track ends before it starts. That is a defect, and
    a negative duration is not a duration, so nothing is reported for it.
    """
    if first is None or last is None:
        return None
    seconds = (last - first).total_seconds()
    return seconds if seconds > 0.0 else None


def _steps_of(points: Sequence[TrackPoint]) -> list[_Step]:
    """Return the raw steps of one run, before any of them is judged."""
    steps: list[_Step] = []
    for index in range(len(points) - 1):
        start, end = points[index], points[index + 1]
        if start.time is None or end.time is None:
            # A run holds only timed positions, so this is unreachable. It is
            # handled rather than asserted because the alternative -- dropping
            # the step -- would misalign every later step with its interval.
            steps.append(_Step(seconds=0.0, distance=0.0, kind=_Interval.INVALID))
            continue
        steps.append(
            _Step(
                seconds=(end.time - start.time).total_seconds(),
                distance=geodesic_distance(start, end),
                kind=_Interval.OBSERVED,
            )
        )
    return steps


def _classify(steps: Sequence[Sequence[_Step]]) -> None:
    """Decide for every step whether it is observed, a gap, invalid or too fast.

    The thresholds are derived from the track itself, so both passes happen here
    rather than while the steps are built: nothing can be judged before the whole
    track has said what its normal sampling and its normal speed look like.
    """
    flat = [step for run in steps for step in run]
    positive = [step for step in flat if step.seconds > 0.0]

    for step in flat:
        if step.seconds <= 0.0:
            step.kind = _Interval.INVALID

    if not positive:
        return

    gap_threshold = max(
        UNOBSERVED_GAP_FLOOR_SECONDS,
        UNOBSERVED_GAP_SAMPLING_FACTOR * median(step.seconds for step in positive),
    )
    for step in positive:
        if step.seconds > gap_threshold:
            step.kind = _Interval.GAP

    observed = [step for step in positive if step.kind is _Interval.OBSERVED]
    if not observed:
        return

    ceiling = min(
        MAX_PLAUSIBLE_SPEED_MPS,
        max(
            OUTLIER_SPEED_FLOOR_MPS,
            OUTLIER_SPEED_FACTOR * median(step.distance / step.seconds for step in observed),
        ),
    )
    for step in observed:
        if step.distance / step.seconds > ceiling:
            step.kind = _Interval.OUTLIER


def _attribute(
    runs: Sequence[_Run],
    steps: Sequence[Sequence[_Step]],
    first: datetime,
) -> tuple[list[_Stretch], float, float | None]:
    """Label every step and every hole between runs, in traversal order.

    Returns the labelled stretches, the distance covered while moving, and the
    highest windowed speed observed.
    """
    stretches: list[_Stretch] = []
    moving_distance = 0.0
    maximum: float | None = None

    for run_index, (run, run_steps) in enumerate(zip(runs, steps, strict=True)):
        points = run.points
        speeds: dict[int, float] = {}
        verdicts: dict[int, _Attribution] = {}
        for group in _observed_groups(points, run_steps):
            windowed = _windowed(group.points, MOVEMENT_WINDOW_SECONDS)
            for window in windowed:
                speeds[group.offset + window.index] = window.speed
            for offset, verdict in _verdicts(group.points, windowed).items():
                verdicts[group.offset + offset] = verdict

        for index, step in enumerate(run_steps):
            start, end = points[index].time, points[index + 1].time
            if start is None or end is None or step.seconds <= 0.0:
                continue
            attribution = _Attribution.UNATTRIBUTED
            if step.kind is _Interval.GAP:
                attribution = _Attribution.UNOBSERVED
            elif step.kind is _Interval.OBSERVED:
                attribution = verdicts.get(index, _Attribution.STOPPED)
                if attribution is _Attribution.MOVING:
                    speed = speeds.get(index, 0.0)
                    moving_distance += step.distance
                    maximum = speed if maximum is None else max(maximum, speed)
            stretches.append(
                _Stretch(
                    start=(start - first).total_seconds(),
                    end=(end - first).total_seconds(),
                    attribution=attribution,
                )
            )

        stretches.extend(_hole_after(runs, run_index, first))

    return stretches, moving_distance, maximum


def _hole_after(runs: Sequence[_Run], index: int, first: datetime) -> list[_Stretch]:
    """Return the labelled hole between one run and the next, if there is one.

    A segment boundary *is* a recording interruption -- that is what makes it a
    boundary -- so the silence across it is unobserved time. Two runs inside one
    segment are separated by positions that carry no instant instead, and that
    time is unattributable rather than provably silent.
    """
    if index + 1 >= len(runs):
        return []
    segment, previous = runs[index].segment, runs[index].points
    following_segment, following = runs[index + 1].segment, runs[index + 1].points
    end, start = previous[-1].time, following[0].time
    if end is None or start is None or start <= end:
        return []
    attribution = (
        _Attribution.UNOBSERVED if following_segment != segment else _Attribution.UNATTRIBUTED
    )
    return [
        _Stretch(
            start=(end - first).total_seconds(),
            end=(start - first).total_seconds(),
            attribution=attribution,
        )
    ]


def _durations(stretches: Sequence[_Stretch], elapsed: float) -> dict[_Attribution, float]:
    """Fold labelled stretches into four durations that add up to the elapsed one.

    Stretches are consumed in traversal order behind a frontier, so a clock that
    jumped backwards cannot make one instant count twice, and whatever the
    stretches never covered ends up named rather than lost.
    """
    totals = dict.fromkeys(_Attribution, 0.0)
    frontier = 0.0
    for stretch in stretches:
        start = max(stretch.start, frontier)
        if stretch.end <= start:
            continue
        if start > frontier:
            totals[_Attribution.UNATTRIBUTED] += start - frontier
        totals[stretch.attribution] += min(stretch.end, elapsed) - start
        frontier = min(stretch.end, elapsed)
        if frontier >= elapsed:
            break
    totals[_Attribution.UNATTRIBUTED] += max(0.0, elapsed - frontier)
    return totals


def _observed_groups(points: Sequence[TrackPoint], steps: Sequence[_Step]) -> list[_Group]:
    """Split a run into stretches of consecutive believable intervals.

    A gap, a backwards step or an outlier ends a stretch. That is what keeps a
    single bad fix out of its neighbours' windows: the discontinuity is a
    boundary, so the spread across it is never measured.
    """
    groups: list[_Group] = []
    start: int | None = None
    for index, step in enumerate(steps):
        if step.kind is _Interval.OBSERVED:
            start = index if start is None else start
            continue
        if start is not None:
            groups.append(_Group(points=tuple(points[start : index + 1]), offset=start))
            start = None
    if start is not None:
        groups.append(_Group(points=tuple(points[start:]), offset=start))
    return groups


def _projections(points: Sequence[TrackPoint]) -> list[tuple[float, ...]]:
    """Return each position's offset along the measuring axes, in metres.

    A local planar frame anchored on the stretch's first position. Over the
    seconds a window spans it agrees with the geodesic distance to far better
    than the receiver noise this filters, and unlike a geodesic it can be
    compared along an axis, which is what a spread needs.
    """
    origin = points[0]
    projected: list[tuple[float, ...]] = []
    for point in points:
        north = (point.latitude - origin.latitude) * _DEGREE_METRES
        offset = point.longitude - origin.longitude
        wrapped = (offset + _HALF_TURN_DEGREES) % _FULL_TURN_DEGREES - _HALF_TURN_DEGREES
        east = wrapped * _DEGREE_METRES * cos(radians(point.latitude))
        projected.append(tuple(north * unit[0] + east * unit[1] for unit in _AXIS_UNITS))
    return projected


class _Spread:
    """The widest spread of a sliding window, along each measuring axis.

    Both window edges only ever advance, so a monotonic queue per axis keeps the
    running extremes in amortised constant time per position. Recomputing the
    spread from scratch for every interval would make a dense recording
    quadratic in its own sampling rate.
    """

    __slots__ = ("_high", "_left", "_low", "_projections", "_right")

    def __init__(self, projections: Sequence[tuple[float, ...]]) -> None:
        """Start with an empty window over the given projected positions."""
        self._projections = projections
        self._left = 0
        self._right = -1
        self._high: list[deque[int]] = [deque() for _ in range(_AXES)]
        self._low: list[deque[int]] = [deque() for _ in range(_AXES)]

    def cover(self, left: int, right: int) -> None:
        """Advance the window to ``[left, right]``. Both bounds only grow."""
        while self._right < right:
            self._right += 1
            values = self._projections[self._right]
            for axis in range(_AXES):
                high, low = self._high[axis], self._low[axis]
                while high and self._projections[high[-1]][axis] <= values[axis]:
                    high.pop()
                high.append(self._right)
                while low and self._projections[low[-1]][axis] >= values[axis]:
                    low.pop()
                low.append(self._right)
        while self._left < left:
            for axis in range(_AXES):
                if self._high[axis][0] == self._left:
                    self._high[axis].popleft()
                if self._low[axis][0] == self._left:
                    self._low[axis].popleft()
            self._left += 1

    def extent(self) -> float:
        """Return the widest spread the current window covers, in metres."""
        return max(
            self._projections[self._high[axis][0]][axis]
            - self._projections[self._low[axis][0]][axis]
            for axis in range(_AXES)
        )


@dataclass(frozen=True, slots=True)
class _Window:
    """What one window says about the interval at its centre.

    Attributes:
        index: The interval the window is centred on.
        extent: The widest positional spread the window covers, in metres.
        speed: ``extent / span`` -- the speed the window implies, which is what
            a *sustained* maximum speed is read from.
        progress: ``(extent - noise) / span`` -- what the window can *prove*
            about progress, once the wander a still receiver produces anyway has
            been taken off. Never negative: a spread inside the envelope proves
            nothing rather than negative progress.
    """

    index: int
    extent: float
    speed: float
    progress: float


def _windowed(points: Sequence[TrackPoint], window_seconds: float) -> list[_Window]:
    """Return what a window of one length says about every interval in a stretch.

    The window of interval *i* is centred on it and spans ``window_seconds``.
    Both edges advance monotonically, so the whole stretch costs one pass
    whatever the window length -- which is what makes measuring at two scales
    affordable.
    """
    base = points[0].time
    if base is None:
        return []
    seconds = [
        0.0 if point.time is None else (point.time - base).total_seconds() for point in points
    ]

    half = window_seconds / 2.0
    count = len(points)
    spread = _Spread(_projections(points))
    windows: list[_Window] = []
    left = 0
    right = 0
    for index in range(count - 1):
        while seconds[index] - seconds[left] > half:
            left += 1
        right = max(right, index + 1)
        while right + 1 < count and seconds[right + 1] - seconds[index + 1] <= half:
            right += 1

        spread.cover(left, right)
        span = seconds[right] - seconds[left]
        if span > 0.0:
            extent = spread.extent()
        else:
            # A window that spans no time at all still has to say something, and
            # the only thing left to read is the interval itself.
            extent = geodesic_distance(points[index], points[index + 1])
            span = seconds[index + 1] - seconds[index]
        if span <= 0.0:
            windows.append(_Window(index=index, extent=extent, speed=0.0, progress=0.0))
            continue
        windows.append(
            _Window(
                index=index,
                extent=extent,
                speed=extent / span,
                progress=max(0.0, extent - POSITION_NOISE_METRES) / span,
            )
        )
    return windows


def _verdicts(points: Sequence[TrackPoint], short: Sequence[_Window]) -> dict[int, _Attribution]:
    """Decide, for every interval of one stretch, what its time was.

    Two scales, asked in order of what they can settle:

    ```
    thirty seconds already show 0.5 m/s of progress   -> moving
    three minutes show 0.1 m/s of progress            -> moving
    three minutes stay inside the receiver's envelope -> stopped
    neither                                           -> unattributed
    ```

    The short scale comes first because a long window saturates on anything that
    circles: laps of a small track have a bounded spread however fast they are
    run, and only the short window shows how quickly that spread is covered.

    The long scale is measured **only across consecutive intervals the short one
    could not settle**, and clipped to them. That is what keeps a rest inside a
    walk a rest: the slow stretch a rest forms is bounded by the fast intervals
    either side of it, so the longer window never reaches them and never smears
    their movement over it.

    The fourth answer is the honest one and it is used. A ten-metre circle
    walked slowly produces a bounded cloud exactly as a still receiver does, and
    a spread wider than any receiver explains is not thereby a stop.
    """
    verdicts: dict[int, _Attribution] = {}
    undecided: list[int] = []
    for window in short:
        if window.speed >= MOVEMENT_SPEED_THRESHOLD_MPS:
            verdicts[window.index] = _Attribution.MOVING
        else:
            undecided.append(window.index)

    for first, last in _consecutive(undecided):
        for window in _windowed(points[first : last + 2], SUSTAINED_WINDOW_SECONDS):
            verdicts[first + window.index] = _sustained_verdict(window)
    return verdicts


def _sustained_verdict(window: _Window) -> _Attribution:
    """Return what a long window proves about one interval it could reach."""
    if window.progress >= SLOW_MOVEMENT_SPEED_MPS:
        return _Attribution.MOVING
    if window.extent <= POSITION_NOISE_METRES:
        return _Attribution.STOPPED
    return _Attribution.UNATTRIBUTED


def _consecutive(indices: Sequence[int]) -> list[tuple[int, int]]:
    """Return the maximal runs of consecutive integers, as inclusive bounds."""
    runs: list[tuple[int, int]] = []
    for index in indices:
        if runs and index == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], index)
        else:
            runs.append((index, index))
    return runs


__all__ = [
    "MAX_PLAUSIBLE_SPEED_MPS",
    "MOVEMENT_SPEED_THRESHOLD_MPS",
    "MOVEMENT_WINDOW_SECONDS",
    "OUTLIER_SPEED_FACTOR",
    "OUTLIER_SPEED_FLOOR_MPS",
    "POSITION_NOISE_METRES",
    "SLOW_MOVEMENT_SPEED_MPS",
    "SUSTAINED_WINDOW_SECONDS",
    "UNOBSERVED_GAP_FLOOR_SECONDS",
    "UNOBSERVED_GAP_SAMPLING_FACTOR",
    "MovementAnalysis",
    "analyse_movement",
]
