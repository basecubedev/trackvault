"""The metric vocabulary: what is calculated, in which unit, from what.

Three rules shape this module.

**A number needs a unit.** ``22.4`` is metres, seconds or kilometres per hour
depending on who reads it. Values are held in stable SI units, and turning them
into something friendlier is a presentation decision made later and elsewhere.

**A number needs a provenance.** Measured, derived and estimated values must not
be conflated, so provenance travels with the value rather than beside it.

**Missing is not zero.** A planned route has no moving time; reporting it as
``0`` would claim the route was travelled and nobody moved. An unavailable
metric is therefore absent from the set rather than present with a filler value.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from gpx_view.domain.metric_provenance import MetricProvenance


class MetricUnit(StrEnum):
    """The unit a metric value is expressed in.

    Deliberately few. A unit is added when a metric needs it, not in advance.
    """

    METRES = "m"
    SECONDS = "s"
    METRES_PER_SECOND = "m/s"


class MetricName(StrEnum):
    """The metrics a track analysis can produce.

    The names are stored keys and part of the data contract: renaming one
    invalidates every stored metric, so they are not renamed. Each carries its
    unit as a suffix, so a stored row is readable without a lookup table.

    Attributes:
        DISTANCE: Horizontal travelled distance, summed within segments.
        ELEVATION_MINIMUM: Lowest raw elevation observed.
        ELEVATION_MAXIMUM: Highest raw elevation observed.
        ELEVATION_GAIN: Ascent accumulated from the filtered elevation profile.
        ELEVATION_LOSS: Descent from the same filtered profile.
        ELAPSED_DURATION: From the first to the last temporal observation.
        MOVING_DURATION: Observed time the track was moving.
        STOPPED_DURATION: Observed time the track was stationary. Positions kept
            arriving and showed no movement -- which is a different statement
            from silence.
        UNOBSERVED_GAP_DURATION: Time no position was recorded for. A paused
            recording, a flat battery and a lost fix all look like this, so it is
            never called a pause.
        UNATTRIBUTED_DURATION: Elapsed time none of the other three could claim,
            because the data underneath it was unusable -- a clock that jumped
            backwards, an interval too fast to be believed, a hole between
            positions that carry no instant. Present so that the four durations
            add up to the elapsed one exactly; time that does not add up is time
            that went missing without saying so.
        AVERAGE_SPEED: Distance over the elapsed duration.
        MOVING_AVERAGE_SPEED: Distance travelled while moving, over the moving
            duration.
        MAXIMUM_SPEED: Highest speed sustained across the analysis window.
    """

    DISTANCE = "distance_m"
    ELEVATION_MINIMUM = "elevation_min_m"
    ELEVATION_MAXIMUM = "elevation_max_m"
    ELEVATION_GAIN = "elevation_gain_m"
    ELEVATION_LOSS = "elevation_loss_m"
    ELAPSED_DURATION = "elapsed_duration_s"
    MOVING_DURATION = "moving_duration_s"
    STOPPED_DURATION = "stopped_duration_s"
    UNOBSERVED_GAP_DURATION = "unobserved_gap_duration_s"
    UNATTRIBUTED_DURATION = "unattributed_duration_s"
    AVERAGE_SPEED = "average_speed_mps"
    MOVING_AVERAGE_SPEED = "moving_average_speed_mps"
    MAXIMUM_SPEED = "maximum_speed_mps"

    @property
    def needs_a_clock(self) -> bool:
        """Report whether this metric could only be derived from instants.

        The split a reader has to see. Distance and the elevation figures are
        properties of the geometry: they are worth the same whether the track's
        instants came from a receiver, from a planner or from nowhere at all.
        Everything else inherits whatever those instants were worth, and must
        therefore be presented with it.
        """
        return self in _CLOCK_METRICS

    @property
    def may_be_negative(self) -> bool:
        """Report whether this metric can legitimately hold a negative value.

        Only the altitudes can. A distance, a duration, an ascent and a speed
        below zero are not values this analysis could ever have produced, so a
        stored one is evidence of damage rather than of an unusual track --
        while an altitude below sea level is an ordinary Tuesday by the Dead Sea.
        """
        return self in _SIGNED_METRICS


_CLOCK_METRICS = frozenset(
    {
        MetricName.ELAPSED_DURATION,
        MetricName.MOVING_DURATION,
        MetricName.STOPPED_DURATION,
        MetricName.UNOBSERVED_GAP_DURATION,
        MetricName.UNATTRIBUTED_DURATION,
        MetricName.AVERAGE_SPEED,
        MetricName.MOVING_AVERAGE_SPEED,
        MetricName.MAXIMUM_SPEED,
    }
)
"""The metrics that could not exist without instants.

Listed rather than inferred from the unit: a distance in metres and an ascent in
metres are both clock-free, and a future clock-free metric measured in seconds
would be miscategorised by any rule that guessed.
"""

_SIGNED_METRICS = frozenset({MetricName.ELEVATION_MINIMUM, MetricName.ELEVATION_MAXIMUM})
"""The metrics whose value may be below zero.

An altitude is a position on an axis with an arbitrary origin. Every other
metric here is a magnitude, and a magnitude below zero is not a small value --
it is a value this analysis could not have produced.
"""


class AnalysisQuality(StrEnum):
    """What was wrong with the data an analysis had to work from.

    A small, deliberately finite set: these are the things an operator or a user
    has to be able to understand about a number. It is not a diagnostics
    catalogue, and a flag is added only when an unexplained result would
    otherwise look like a defect.

    Attributes:
        MISSING_TIMESTAMPS: Some positions carry no instant, so part of the
            track could not be placed in time.
        NON_MONOTONIC_TIMESTAMPS: Time runs backwards somewhere. Those intervals
            are excluded rather than absorbed.
        LARGE_UNOBSERVED_GAPS: The recording stopped producing positions for
            longer than its own sampling explains.
        SPEED_OUTLIERS_EXCLUDED: Intervals whose implied speed was implausible
            for this track were excluded from the movement and speed metrics.
            The positions themselves are untouched.
        INSUFFICIENT_TEMPORAL_DATA: There was not enough timing to derive any
            duration or speed.
        INSUFFICIENT_ELEVATION_DATA: There was not enough elevation to derive a
            profile.
    """

    MISSING_TIMESTAMPS = "missing_timestamps"
    NON_MONOTONIC_TIMESTAMPS = "non_monotonic_timestamps"
    LARGE_UNOBSERVED_GAPS = "large_unobserved_gaps"
    SPEED_OUTLIERS_EXCLUDED = "speed_outliers_excluded"
    INSUFFICIENT_TEMPORAL_DATA = "insufficient_temporal_data"
    INSUFFICIENT_ELEVATION_DATA = "insufficient_elevation_data"


@dataclass(frozen=True, slots=True)
class MetricValue:
    """One derived number, with everything needed to understand it.

    Attributes:
        value: The magnitude, in ``unit``.
        unit: The unit the magnitude is expressed in.
        provenance: Where the value came from. Analysis produces ``DERIVED``
            values; a source-reported figure would be ``MEASURED`` and is a
            different thing even at the same magnitude.

    Raises:
        ValueError: If the value is not a finite number. NaN and infinity poison
            every later aggregation silently.
    """

    value: float
    unit: MetricUnit
    provenance: MetricProvenance = MetricProvenance.DERIVED

    def __post_init__(self) -> None:
        """Reject a value no aggregate could safely sum."""
        if self.value != self.value or self.value in (float("inf"), float("-inf")):
            raise ValueError("a metric value must be a finite number")


def quality_in_canonical_order(
    flags: Iterable[AnalysisQuality],
) -> tuple[AnalysisQuality, ...]:
    """Return the given flags deduplicated and in declaration order.

    Quality flags are stored and compared, so their order must not depend on the
    order in which an algorithm happened to notice things.
    """
    observed = set(flags)
    return tuple(flag for flag in AnalysisQuality if flag in observed)


@dataclass(frozen=True, slots=True)
class TrackAnalysis:
    """Everything one analysis run derived from one normalized track.

    Only metrics that could actually be derived are present. A caller asking for
    an absent one gets ``None``, which is the honest answer and is not the same
    answer as ``0.0``.

    Attributes:
        metrics: The derived values, keyed by metric name.
        quality: What was wrong with the input, in declaration order.
        first_observed_at: The first instant any position carried, in source
            traversal order. This is the activity's start -- a document's export
            time never reaches it.
        last_observed_at: The last instant any position carried.
    """

    metrics: Mapping[MetricName, MetricValue]
    quality: tuple[AnalysisQuality, ...] = ()
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None

    def metric(self, name: MetricName) -> MetricValue | None:
        """Return one metric with its unit and provenance, or ``None``."""
        return self.metrics.get(name)

    def value_of(self, name: MetricName) -> float | None:
        """Return the bare magnitude of one metric, or ``None`` if it has none.

        Convenience for callers that already know the unit from the name. The
        unit is never dropped from storage or from the API by using this.
        """
        metric = self.metrics.get(name)
        return None if metric is None else metric.value


__all__ = [
    "AnalysisQuality",
    "MetricName",
    "MetricUnit",
    "MetricValue",
    "TrackAnalysis",
    "quality_in_canonical_order",
]
