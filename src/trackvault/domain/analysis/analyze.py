"""The one entry point that turns normalized geometry into metrics.

```
tuple[TrackSegment, ...]  ->  TrackAnalysis
```

That signature is the source-agnostic guarantee, made structural rather than
promised. Nothing here can read a GPX document, a Locus extension or a FIT
message, because none of them is reachable from a tuple of segments. A FIT
adapter that produces the same segments reaches the same numbers, and it does so
without this module being told it exists.

Analysis is never source authority. It is derived state, rebuildable from the
current normalized track at any time, which is why every result carries the
profile that produced it and why nothing here writes anything back into the
geometry it read.

The three calculations are independent on purpose. A track whose elevation is
unusable still has a distance, and a planned route with no timestamps still has
both. Only what genuinely could not be derived goes missing.
"""

from collections.abc import Sequence

from trackvault.domain.analysis.distance import track_distance
from trackvault.domain.analysis.elevation import analyse_elevation
from trackvault.domain.analysis.metrics import (
    AnalysisQuality,
    MetricName,
    MetricUnit,
    MetricValue,
    TrackAnalysis,
    quality_in_canonical_order,
)
from trackvault.domain.analysis.movement import MovementAnalysis, analyse_movement
from trackvault.domain.geometry import TrackSegment


def analyze_track(segments: Sequence[TrackSegment]) -> TrackAnalysis:
    """Derive every metric one normalized track supports.

    Args:
        segments: The track's geometry, in source order. This is the whole
            input: no classification, no source metadata and no format.

    Returns:
        The metrics that could be derived, the quality of the data they came
        from, and the track's temporal extent. A metric that could not be
        derived is absent -- callers must not read that as zero.
    """
    metrics: dict[MetricName, MetricValue] = {
        MetricName.DISTANCE: _metres(track_distance(segments))
    }
    quality: set[AnalysisQuality] = set()

    movement = analyse_movement(segments)
    metrics.update(_temporal_metrics(movement, metrics[MetricName.DISTANCE].value))
    quality.update(_temporal_quality(movement))

    elevation = analyse_elevation(segments)
    for name, value in (
        (MetricName.ELEVATION_MINIMUM, elevation.minimum_metres),
        (MetricName.ELEVATION_MAXIMUM, elevation.maximum_metres),
        (MetricName.ELEVATION_GAIN, elevation.gain_metres),
        (MetricName.ELEVATION_LOSS, elevation.loss_metres),
    ):
        if value is not None:
            metrics[name] = _metres(value)
    if elevation.gain_metres is None:
        quality.add(AnalysisQuality.INSUFFICIENT_ELEVATION_DATA)

    return TrackAnalysis(
        metrics=metrics,
        quality=quality_in_canonical_order(quality),
        first_observed_at=movement.first_observed_at,
        last_observed_at=movement.last_observed_at,
    )


def _temporal_metrics(movement: MovementAnalysis, distance: float) -> dict[MetricName, MetricValue]:
    """Return the durations and speeds the temporal data supports.

    A speed is only formed where both of its parts exist and the divisor is a
    real duration. Dividing by a zero that stood for "we do not know" is how an
    unavailable metric turns into an infinite one.
    """
    metrics: dict[MetricName, MetricValue] = {}
    for name, value in (
        (MetricName.ELAPSED_DURATION, movement.elapsed_seconds),
        (MetricName.MOVING_DURATION, movement.moving_seconds),
        (MetricName.STOPPED_DURATION, movement.stopped_seconds),
        (MetricName.UNOBSERVED_GAP_DURATION, movement.unobserved_seconds),
        (MetricName.UNATTRIBUTED_DURATION, movement.unattributed_seconds),
    ):
        if value is not None:
            metrics[name] = MetricValue(value=value, unit=MetricUnit.SECONDS)

    elapsed = movement.elapsed_seconds
    if elapsed is not None and elapsed > 0.0 and movement.moving_seconds is not None:
        metrics[MetricName.AVERAGE_SPEED] = _speed(distance / elapsed)

    moving = movement.moving_seconds
    moving_distance = movement.moving_distance_metres
    if moving is not None and moving > 0.0 and moving_distance is not None:
        metrics[MetricName.MOVING_AVERAGE_SPEED] = _speed(moving_distance / moving)

    if movement.maximum_speed_mps is not None:
        metrics[MetricName.MAXIMUM_SPEED] = _speed(movement.maximum_speed_mps)
    return metrics


def _temporal_quality(movement: MovementAnalysis) -> set[AnalysisQuality]:
    """Return what was wrong with the timing the movement analysis had."""
    quality: set[AnalysisQuality] = set()
    if movement.moving_seconds is None:
        quality.add(AnalysisQuality.INSUFFICIENT_TEMPORAL_DATA)
    if movement.missing_timestamps:
        quality.add(AnalysisQuality.MISSING_TIMESTAMPS)
    if movement.non_monotonic_timestamps:
        quality.add(AnalysisQuality.NON_MONOTONIC_TIMESTAMPS)
    if movement.large_unobserved_gaps:
        quality.add(AnalysisQuality.LARGE_UNOBSERVED_GAPS)
    if movement.outliers_excluded:
        quality.add(AnalysisQuality.SPEED_OUTLIERS_EXCLUDED)
    return quality


def _metres(value: float) -> MetricValue:
    """Return a length metric."""
    return MetricValue(value=value, unit=MetricUnit.METRES)


def _speed(value: float) -> MetricValue:
    """Return a speed metric."""
    return MetricValue(value=value, unit=MetricUnit.METRES_PER_SECOND)
