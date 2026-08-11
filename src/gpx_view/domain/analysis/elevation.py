"""Elevation, and why the obvious calculation is wrong.

The naive ascent is one line:

```python
sum(max(0.0, later - earlier) for earlier, later in consecutive_pairs)
```

and it is unusable. A phone's barometric or satellite altitude wanders by a
metre or two between samples, so a flat hour of walking accumulates hundreds of
metres of imaginary climbing. The number grows with the sampling rate rather
than with the terrain, which is the giveaway: the same walk logged twice as
often "climbs" twice as much.

Two filters, in order, for two different defects:

**A rolling median** removes isolated impossible samples. One position at 900 m
in the middle of a 100 m plateau is a defect, not a peak, and a median of its
neighbourhood never sees it.

**A deadband** removes the drift the median leaves behind. The profile is
reduced to the turning points it actually reached: a climb accumulates when the
profile has fallen back out of a band below the highest point it reached, and
that peak -- not the sample that happened to trigger the test -- is what the
ascent is measured to. Noise inside the band never turns anything around, while
a genuine climb passes straight through it.

Measuring to the turning point rather than to the triggering sample is what
makes the result independent of the direction of travel:

```
gain(track reversed) == loss(track)
```

exactly, rather than approximately. A rule that instead moved its reference to
whichever sample first left the band would put the reference in a different
place walking uphill than walking downhill, and would report a different ascent
for the same path taken the other way.

Two different questions therefore get two different answers:

| Metric | Read from |
| --- | --- |
| minimum, maximum | the raw observations |
| ascent, descent | the filtered profile |

The extremes are single observations, and a filter would only move them away
from what the source recorded. Ascent is a sum over the whole track, so every
sample's error is added to it -- that is exactly the quantity a filter has to
protect.

Nothing here corrects elevation against a terrain model. Downloading a digital
elevation model is a network dependency and a separate feature; this analyses
the data the track carries.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from gpx_view.domain.geometry import TrackSegment

MEDIAN_WINDOW_POINTS = 5
"""How many samples the spike filter looks at, centred on each one.

Odd, so there is a middle. Five is enough to outvote a single bad sample and
short enough that it cannot flatten a real slope.
"""

ELEVATION_DEADBAND_METRES = 5.0
"""How far the profile must turn back before a climb or a descent is confirmed.

Consumer GPS altitude is good to something like five to ten metres, so a
smaller band would still be accumulating the receiver's own uncertainty. The
cost is honest and worth stating: rolling terrain whose undulations stay inside
the band is not counted, because at that amplitude it cannot be told apart from
noise. Terrain that leaves the band is counted in full, measured to the turning
point it actually reached.
"""


@dataclass(frozen=True, slots=True)
class ElevationAnalysis:
    """What a track's elevation data supports.

    Every value is ``None`` when the data could not support it. A track without
    elevation has no ascent -- which is not the same as an ascent of zero.

    Attributes:
        minimum_metres: Lowest raw observation.
        maximum_metres: Highest raw observation.
        gain_metres: Ascent accumulated from the filtered profile.
        loss_metres: Descent from the same profile.
    """

    minimum_metres: float | None
    maximum_metres: float | None
    gain_metres: float | None
    loss_metres: float | None


def analyse_elevation(segments: Sequence[TrackSegment]) -> ElevationAnalysis:
    """Derive the elevation picture of one track.

    Args:
        segments: The normalized geometry, in source order.

    Returns:
        The extremes from the raw observations and the ascent and descent from
        the filtered profile, each absent where the data does not support it.
    """
    observed = [
        point.elevation
        for segment in segments
        for point in segment.points
        if point.elevation is not None
    ]
    if not observed:
        return ElevationAnalysis(None, None, None, None)

    profiles = [_elevations_of(segment) for segment in segments]
    climbable = [profile for profile in profiles if len(profile) >= 2]
    if not climbable:
        return ElevationAnalysis(min(observed), max(observed), None, None)

    gain = loss = 0.0
    for profile in climbable:
        # Each segment accumulates on its own. A recording that resumed eight
        # hundred metres higher up did not climb between the two segments; it
        # was switched off for the ascent, and inventing it here would put a
        # mountain into the statistics of a track that never recorded one.
        segment_gain, segment_loss = _accumulate(_rolling_median(profile))
        gain += segment_gain
        loss += segment_loss

    return ElevationAnalysis(min(observed), max(observed), gain, loss)


def filtered_elevation_profile(segment: TrackSegment) -> tuple[float | None, ...]:
    """Return the filtered elevation of every position of one segment.

    The series a chart draws and the ascent a total quotes come from **one**
    filter: this runs the same rolling median over the same observations that
    :func:`analyse_elevation` accumulates from. Two implementations would put a
    raw, noisy line under a filtered number and leave a reader to work out why
    the picture and the figure disagree.

    A position that carried no elevation has no filtered elevation either. The
    filter is computed over the observations only -- interpolating across a hole
    would invent ground -- and the result is mapped back onto the positions, so
    a sample index in the series is the same sample index as in the geometry.
    """
    observed = _elevations_of(segment)
    if not observed:
        return tuple(None for _ in segment.points)
    smoothed = iter(_rolling_median(observed))
    return tuple(
        next(smoothed) if point.elevation is not None else None for point in segment.points
    )


def _elevations_of(segment: TrackSegment) -> list[float]:
    """Return one segment's elevations, skipping the positions that carry none.

    Skipping rather than interpolating: a missing altitude is missing data, and
    a straight line drawn through it would be an invention that the ascent then
    counts.
    """
    return [point.elevation for point in segment.points if point.elevation is not None]


def _rolling_median(profile: Sequence[float]) -> list[float]:
    """Return the profile with isolated spikes voted out.

    The window shrinks at the ends rather than padding them, so the first and
    last samples stay close to what was recorded instead of being pulled towards
    an invented neighbour.
    """
    reach = MEDIAN_WINDOW_POINTS // 2
    count = len(profile)
    return [
        median(profile[max(0, index - reach) : min(count, index + reach + 1)])
        for index in range(count)
    ]


def _accumulate(profile: Sequence[float]) -> tuple[float, float]:
    """Return the ascent and descent of a filtered profile.

    The profile is walked while two things are remembered: the last turning
    point that was confirmed, and the most extreme altitude reached since. A
    climb is confirmed only once the profile has dropped more than the deadband
    below its running peak, and what it then adds is the whole rise from the
    previous turning point *to that peak*.

    Everything the accuracy depends on follows from measuring to the peak rather
    than to the sample that triggered the confirmation:

    - a steady climb adds its full height, however small its individual steps,
      because nothing turns it around;
    - a wander inside the band confirms nothing and moves nothing;
    - and the same path walked the other way turns around at exactly the same
      altitudes, so its ascent and descent are exactly swapped.
    """
    gain = loss = 0.0
    if not profile:
        return gain, loss

    # Until the profile has left the band once, there is no direction to be in,
    # so both extremes are tracked and whichever is left behind first becomes
    # the first turning point.
    lowest = highest = anchor = extreme = profile[0]
    rising: bool | None = None

    for elevation in profile:
        if rising is None:
            lowest = min(lowest, elevation)
            highest = max(highest, elevation)
            if elevation - lowest > ELEVATION_DEADBAND_METRES:
                rising, anchor, extreme = True, lowest, elevation
            elif highest - elevation > ELEVATION_DEADBAND_METRES:
                rising, anchor, extreme = False, highest, elevation
        elif rising:
            if elevation > extreme:
                extreme = elevation
            elif extreme - elevation > ELEVATION_DEADBAND_METRES:
                gain += extreme - anchor
                anchor, extreme, rising = extreme, elevation, False
        else:
            if elevation < extreme:
                extreme = elevation
            elif elevation - extreme > ELEVATION_DEADBAND_METRES:
                loss += anchor - extreme
                anchor, extreme, rising = extreme, elevation, True

    # The last stretch never turns around, so nothing confirms it. It is real
    # ground all the same, and dropping it would lose the whole final climb of
    # any track that ends at the top of one.
    if rising is True:
        gain += max(0.0, extreme - anchor)
    elif rising is False:
        loss += max(0.0, anchor - extreme)
    return gain, loss
