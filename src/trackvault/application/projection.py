"""Making a large track small enough to send, without making it a different track.

A recording of a long day holds tens of thousands of positions. Serialising all
of them is a response nobody can render and, at the top of the range, a browser
tab that runs out of memory. So a client may ask for a bounded number of them.

Two rules make this safe rather than merely smaller.

**This is presentation, never normalization.** Nothing here writes anything. The
canonical geometry keeps every position it was imported with, so the reduction
can be redone differently tomorrow, and asking for fewer points is not a way to
lose data.

**A map and a chart need different points.** They are different projections of
one set of samples and they are wrong in different ways when reduced by the same
rule:

```
map     wants the shape        -- a corner matters, a straight kilometre does not
chart   wants the extremes     -- a summit matters even if the map would drop it
```

A line simplifier keeps the positions that carry the *shape*, which is exactly
what a map needs and exactly what destroys a profile: the highest point of a
climb sits on a nearly straight piece of ground and is the first thing such an
algorithm discards. So the map gets Ramer--Douglas--Peucker and the chart gets a
decimation that keeps its turning points, and both are computed from the same
samples so a marker and a cursor still address the same position.

**Segments are never joined.** Every reduction runs per segment and keeps that
segment's first and last sample. A line drawn across a recording interruption is
ground nobody travelled, and on a map it is the most visible thing on screen.
"""

from collections.abc import Callable, Sequence
from heapq import heappop, heappush
from math import cos, radians

from trackvault.domain.analysis.series import ProfileSample, ProfileSegment, TrackProfile
from trackvault.domain.geometry import TrackPoint, TrackSegment

MINIMUM_KEPT_POINTS = 2
"""The fewest positions a reduction may leave a segment with.

Its ends. A segment reduced past them is no longer that segment, and a caller
asking for one point per segment has asked for something that is not a track.
"""


def simplify_geometry(
    segments: Sequence[TrackSegment], max_points: int
) -> tuple[TrackSegment, ...]:
    """Return the same shape with at most ``max_points`` positions.

    The budget is shared out in proportion to how many positions each segment
    holds, so a long segment is not reduced to its ends to make room for a short
    one, and every segment keeps at least its two.

    Args:
        segments: The canonical geometry. Never modified.
        max_points: The most positions the result may hold in total. A budget
            smaller than two positions per segment cannot be honoured -- the
            ends are what a segment *is* -- and the result then holds those.

    Returns:
        The reduced geometry, segment boundaries intact.
    """
    budgets = _budgets([segment.point_count for segment in segments], max_points)
    return tuple(
        TrackSegment(points=_douglas_peucker(segment.points, budget))
        for segment, budget in zip(segments, budgets, strict=True)
    )


def decimate_profile(profile: TrackProfile, max_samples: int) -> TrackProfile:
    """Return the same profile with at most ``max_samples`` samples.

    Unlike the map reduction this keeps what a chart is read for: the first and
    last sample of every segment, and the local extremes of the elevation and
    speed series in between. A summit dropped from a profile is a summit the
    reader never sees, and it is precisely the sample a shape-preserving
    simplifier considers redundant.

    Args:
        profile: The full profile. Never modified.
        max_samples: The most samples the result may hold in total.

    Returns:
        The reduced profile, in traversal order, with every sample keeping the
        identity it had -- so a decimated chart and a full map still address the
        same positions.
    """
    budgets = _budgets([len(segment.samples) for segment in profile.segments], max_samples)
    return TrackProfile(
        segments=tuple(
            ProfileSegment(index=segment.index, samples=_thin(segment.samples, budget))
            for segment, budget in zip(profile.segments, budgets, strict=True)
        )
    )


def _budgets(sizes: Sequence[int], total: int) -> list[int]:
    """Share a sample budget between segments, in proportion to their size.

    Every segment keeps at least its ends, so the result can exceed ``total``
    when a track holds more segments than the budget has room for. That is the
    right direction to be wrong in: a track of a thousand two-point segments is
    a thousand two-point segments, and returning half of them would be returning
    a different track.
    """
    if not sizes:
        return []
    population = sum(sizes)
    if population <= total:
        return list(sizes)
    spare = max(0, total - MINIMUM_KEPT_POINTS * len(sizes))
    return [min(size, MINIMUM_KEPT_POINTS + int(spare * size / population)) for size in sizes]


def _thin(samples: Sequence[ProfileSample], budget: int) -> tuple[ProfileSample, ...]:
    """Reduce one segment's samples to a budget, keeping its turning points.

    Three tiers, and the first is the one that matters.

    The **ends and the global extremes** are kept unconditionally: the highest
    and lowest point of the filtered profile and the fastest sustained speed are
    the numbers a reader takes off the chart, and they are the same numbers the
    headline figures beside it quote. A chart whose peak is 22 centimetres lower
    than the summit next to it is a chart that quietly contradicts the page.

    Then the **local** turning points, because the shape between the extremes is
    what makes a profile readable. Whatever budget is left is filled by sampling
    the rest evenly, so a long flat stretch gets a line rather than a straight
    jump between two distant points.
    """
    count = len(samples)
    if count <= budget:
        return tuple(samples)
    if budget <= MINIMUM_KEPT_POINTS:
        return (samples[0], samples[-1]) if count > 1 else tuple(samples)

    required = {0, count - 1}
    required |= _global_extremes(samples, lambda sample: sample.filtered_elevation_m)
    required |= _global_extremes(samples, lambda sample: sample.sustained_speed_mps)

    if len(required) > budget:
        # A budget too small for the extremes themselves. The ends stay, and as
        # many extremes as fit: the alternative is exceeding a bound the caller
        # asked for.
        interior = sorted(required - {0, count - 1})
        return tuple(
            samples[index] for index in sorted({0, count - 1} | set(interior[: max(0, budget - 2)]))
        )

    kept = set(required)
    kept |= _extrema(samples, lambda sample: sample.filtered_elevation_m)
    kept |= _extrema(samples, lambda sample: sample.sustained_speed_mps)

    if len(kept) > budget:
        # More turning points than the budget allows. Thin the optional ones
        # evenly rather than dropping the tail, so the shape stays recognisable
        # instead of ending abruptly -- and never at the cost of an extreme.
        optional = sorted(kept - required)
        room = budget - len(required)
        step = len(optional) / room if room > 0 else 0.0
        kept = set(required)
        if room > 0:
            kept |= {optional[min(len(optional) - 1, int(index * step))] for index in range(room)}
    else:
        spare = budget - len(kept)
        if spare > 0:
            step = count / (spare + 1)
            kept |= {min(count - 1, int((index + 1) * step)) for index in range(spare)}

    return tuple(samples[index] for index in sorted(kept))


def _global_extremes(
    samples: Sequence[ProfileSample], read: Callable[[ProfileSample], float | None]
) -> set[int]:
    """Return where one series reaches its highest and lowest value.

    The first occurrence of each, so the answer is deterministic when a plateau
    holds the maximum. A series with no values at all contributes nothing --
    absence is not an extreme.
    """
    present = [
        (index, value)
        for index, sample in enumerate(samples)
        if (value := read(sample)) is not None
    ]
    if not present:
        return set()
    highest = max(present, key=lambda entry: (entry[1], -entry[0]))
    lowest = min(present, key=lambda entry: (entry[1], entry[0]))
    return {highest[0], lowest[0]}


def _extrema(
    samples: Sequence[ProfileSample], read: Callable[[ProfileSample], float | None]
) -> set[int]:
    """Return the indices where one series turns around.

    A sample is a turning point when the series rises to it and falls after it,
    or the other way round. Samples the series has no value for are skipped:
    they are holes, and a hole is not a peak.
    """
    present: list[tuple[int, float]] = []
    for index, sample in enumerate(samples):
        value = read(sample)
        if value is not None:
            present.append((index, value))
    turning: set[int] = set()
    for position in range(1, len(present) - 1):
        before = present[position - 1][1]
        index, here = present[position]
        after = present[position + 1][1]
        if (here > before and here >= after) or (here < before and here <= after):
            turning.add(index)
    return turning


def _douglas_peucker(points: Sequence[TrackPoint], budget: int) -> tuple[TrackPoint, ...]:
    """Return the positions that carry the shape, up to a budget.

    Ramer--Douglas--Peucker, run as a priority queue rather than to a tolerance:
    the position furthest from the line between its kept neighbours is added
    first, then the next furthest, until the budget is spent. That gives the
    best shape a given number of points can carry, and it needs no tolerance
    constant -- which would otherwise be a distance in metres chosen for one
    zoom level and wrong at every other.

    A heap keyed on that distance, not a rescan: each accepted position splits
    one interval into two and only those two are measured again. Rescanning
    every interval on every step is the obvious version and it is quadratic,
    which is fine at a thousand positions and is a timeout at a hundred
    thousand -- which is the size this exists for.

    Forty lines, no dependency. Adding one for this would be adding a supply
    chain to avoid a well-known recursion.
    """
    count = len(points)
    if count <= budget or count <= MINIMUM_KEPT_POINTS:
        return tuple(points)

    kept = [0, count - 1]
    # Negated distance, because `heapq` is a min-heap and this wants the widest
    # deviation first. The interval bounds break ties deterministically, so the
    # same track always simplifies to the same positions.
    pending: list[tuple[float, int, int, int]] = []
    _offer(pending, points, 0, count - 1)
    while len(kept) < budget and pending:
        _, start, end, index = heappop(pending)
        kept.append(index)
        _offer(pending, points, start, index)
        _offer(pending, points, index, end)
    kept.sort()
    return tuple(points[index] for index in kept)


def _offer(
    pending: list[tuple[float, int, int, int]],
    points: Sequence[TrackPoint],
    start: int,
    end: int,
) -> None:
    """Queue the widest deviation inside one interval, if it holds anything."""
    index, distance = _furthest(points, start, end)
    if index >= 0:
        heappush(pending, (-distance, start, end, index))


def _furthest(points: Sequence[TrackPoint], start: int, end: int) -> tuple[int, float]:
    """Return the position furthest from the chord between two kept ones.

    The distance is measured in a local planar frame, in degrees scaled so that
    latitude and longitude are comparable. Over the span between two kept
    positions that agrees with a geodesic far more closely than the difference
    between two candidate positions, and unlike a geodesic it is a perpendicular
    offset, which is what a chord distance needs.
    """
    if end - start < 2:
        return -1, 0.0
    first, last = points[start], points[end]
    scale = max(0.01, _cosine(first.latitude))
    ax, ay = first.longitude * scale, first.latitude
    bx, by = last.longitude * scale, last.latitude
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5

    best_index = -1
    best_distance = 0.0
    for index in range(start + 1, end):
        point = points[index]
        px, py = point.longitude * scale, point.latitude
        if length == 0.0:
            distance = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        else:
            distance = abs(dx * (ay - py) - (ax - px) * dy) / length
        if distance > best_distance:
            best_index, best_distance = index, distance
    return best_index, best_distance


def _cosine(latitude: float) -> float:
    """Return the east-west scale factor at one latitude."""
    return cos(radians(latitude))


__all__ = [
    "MINIMUM_KEPT_POINTS",
    "decimate_profile",
    "simplify_geometry",
]
