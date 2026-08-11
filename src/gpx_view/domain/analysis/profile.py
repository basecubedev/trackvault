"""Which algorithms produced a stored set of metrics.

Analysis output is derived state: it can always be rebuilt from the current
normalized track. That is exactly why it has to say what produced it. Elevation
smoothing, a movement threshold or an outlier rule will change, and the day they
do, the old numbers and the new ones must not look like the same measurement
taken twice.

```
same profile      -> the numbers are comparable
different profile -> they were produced by different rules
```

The shape mirrors :class:`~gpx_view.domain.processing.ProcessingProfile`
deliberately: one value that answers "was this produced by what is installed
today", compared in exactly one place.
"""

from dataclasses import dataclass

DISTANCE_ALGORITHM = "haversine"
DISTANCE_ALGORITHM_VERSION = 1
"""Segment-aware sum of horizontal great-circle distances.

Version 1: consecutive positions within a segment, on a sphere of the IUGG mean
earth radius. Segment boundaries are never bridged and elevation never enters
the value.
"""

MOVEMENT_ALGORITHM = "windowed-extent"
MOVEMENT_ALGORITHM_VERSION = 2
"""Movement decided from the positional spread over a time window.

Version 1: intervals are classified against the widest straight-line spread the
window covers rather than against its start-to-end displacement, so a path that
bends back on itself stays movement. Gaps are separated from observed stops by a
sampling-aware threshold, implausible intervals are excluded against a
median-scaled ceiling, and every second of the elapsed span is attributed to
exactly one of moving, stopped, unobserved and unattributed.

It replaced ``windowed-displacement`` version 1, which measured where a window
ended up relative to where it started and therefore reported a walked loop, an
out-and-back and a switchback climb as standing still.

Version 2: the same spread, measured at a second, longer scale, with the wander
a still receiver produces subtracted before a rate is taken. Version 1 had a
single threshold, which made one unstated claim -- that nothing below 0.5 m/s is
movement -- and turned a hundredth of a metre per second into the difference
between ten minutes of walking and ten minutes of standing still. Steady
progression well below that cut is now movement, bounded receiver noise is still
a stop, and a stretch that neither scale can separate from noise is reported as
unattributed rather than decided.
"""

ELEVATION_ALGORITHM = "median-hysteresis"
ELEVATION_ALGORITHM_VERSION = 1
"""Ascent and descent from a filtered profile.

Version 1: a rolling median removes isolated spikes, then a deadband confirms a
climb or a descent once the profile has turned back out of the band, and
accumulates the whole swing to the turning point it reached. Minimum and maximum
stay on the raw observations.

It replaces ``median-deadband`` version 1, which accumulated to whichever sample
first left the band rather than to the turning point, and therefore reported a
different ascent for the same path walked the other way.
"""

METRIC_SCHEMA_VERSION = 2
"""Version of the metric set itself.

Bumped when a metric changes meaning or is added in a way that makes an older
stored set incomplete. Renaming a metric is not an option: the name is the
stored key.

Version 2 adds ``unattributed_duration_s``. A version 1 set cannot say how much
of its elapsed span went unclassified, so its four durations cannot be checked
against each other.
"""


@dataclass(frozen=True, slots=True)
class AnalysisProfile:
    """The analysis a stored set of metrics was produced by.

    Every component changes numbers a user compares over time. A different
    elevation filter reports different ascent for an unchanged track, and a
    different movement rule reports different moving time, so "is this still
    current" is one question about the whole set.

    Attributes:
        distance_algorithm: Name of the distance rule, for example ``haversine``.
        distance_algorithm_version: Version of that rule.
        movement_algorithm: Name of the time and movement rule.
        movement_algorithm_version: Version of that rule.
        elevation_algorithm: Name of the elevation filter.
        elevation_algorithm_version: Version of that filter.
        metric_schema_version: Version of the metric set produced.

    Raises:
        ValueError: If an algorithm is unnamed or a version is not positive. A
            profile that cannot say what produced a number proves nothing about
            it.
    """

    distance_algorithm: str
    distance_algorithm_version: int
    movement_algorithm: str
    movement_algorithm_version: int
    elevation_algorithm: str
    elevation_algorithm_version: int
    metric_schema_version: int

    def __post_init__(self) -> None:
        """Reject a profile that could not identify the analysis it names."""
        for name, version in (
            (self.distance_algorithm, self.distance_algorithm_version),
            (self.movement_algorithm, self.movement_algorithm_version),
            (self.elevation_algorithm, self.elevation_algorithm_version),
        ):
            if not name.strip():
                raise ValueError("an analysis profile must name every algorithm it applies")
            if version < 1:
                raise ValueError("an algorithm version must be positive")
        if self.metric_schema_version < 1:
            raise ValueError("the metric schema version must be positive")


def is_analysis_profile_current(
    stored: AnalysisProfile | None, installed: AnalysisProfile | None
) -> bool:
    """Report whether stored metrics were produced by the installed algorithms.

    Args:
        stored: The profile a stored result proves it used, or ``None`` when it
            cannot prove one.
        installed: The profile this build applies, or ``None`` when it has none.

    Returns:
        ``True`` only when both are known and identical. A result that cannot
        say which algorithms produced it has not shown it is current, and a
        profile *newer* than the installed one is not current either -- after a
        downgrade, reporting newer numbers as up to date would mean never
        regenerating them.

    Note:
        This compares algorithms only. Whether the result also describes the
        geometry a reader currently sees is the other half of the question, and
        it is answered by ``InstalledAnalysis`` where the generation is known.
    """
    if stored is None or installed is None:
        return False
    return stored == installed


ANALYSIS_PROFILE = AnalysisProfile(
    distance_algorithm=DISTANCE_ALGORITHM,
    distance_algorithm_version=DISTANCE_ALGORITHM_VERSION,
    movement_algorithm=MOVEMENT_ALGORITHM,
    movement_algorithm_version=MOVEMENT_ALGORITHM_VERSION,
    elevation_algorithm=ELEVATION_ALGORITHM,
    elevation_algorithm_version=ELEVATION_ALGORITHM_VERSION,
    metric_schema_version=METRIC_SCHEMA_VERSION,
)
"""The analysis this build applies. The one statement of "current"."""
