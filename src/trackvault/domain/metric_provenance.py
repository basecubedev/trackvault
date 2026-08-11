"""Origin of a numeric metric, kept separate from the metric value itself."""

from enum import StrEnum


class MetricProvenance(StrEnum):
    """Where the value of a metric comes from.

    Provenance is part of every metric's business meaning, not an annotation, and
    it must never be silently discarded or conflated: values of different
    provenance are not summed, averaged or presented as a single number.

    Comparing them *as a comparison*, with both provenances visible, is explicitly
    allowed. Holding a source-reported distance next to one derived from the
    coordinates is how the application checks quality, analyses deviation and
    decides which value to present as canonical.

    Attributes:
        MEASURED: Read directly from a sensor or the source system, for example
            heart rate, power, temperature or reported GPS accuracy.
        DERIVED: Computed from measured or normalized data, for example distance
            from coordinates, speed from coordinates and timestamps, moving time,
            or elevation gain after filtering.
        ESTIMATED: Produced by a model or planner, for example a planned duration,
            a predicted walking speed or a routing-engine ETA.
    """

    MEASURED = "measured"
    DERIVED = "derived"
    ESTIMATED = "estimated"
