"""Executable contract for the canonical track analysis.

Analysis turns the current normalized track into derived metrics. The rules
checked here are the ones the rest of the application relies on:

* analysis reads normalized geometry and nothing else, so a future FIT adapter
  reaches the same numbers as the GPX one,
* a segment boundary is never bridged,
* elapsed, moving, stopped and unobserved time are four different statements,
* a metric that could not be derived is absent, never zero,
* every metric states its unit and its provenance.

The synthetic fixtures are built from plain coordinates. No private recording is
copied into this file.
"""

from datetime import UTC, datetime, timedelta

import pytest

from gpx_view.domain import MetricProvenance, TrackPoint, TrackSegment
from gpx_view.domain.analysis import (
    ANALYSIS_PROFILE,
    AnalysisProfile,
    AnalysisQuality,
    MetricName,
    MetricUnit,
    analyze_track,
)

START = datetime(2026, 3, 14, 9, 0, tzinfo=UTC)

# One degree of latitude is close enough to 111 km that a straight north-south
# fixture has a distance an author can predict without running the code.
METRES_PER_LATITUDE_DEGREE = 111_195.0


def _point(
    latitude: float,
    longitude: float = 0.0,
    *,
    elevation: float | None = None,
    at: datetime | None = None,
) -> TrackPoint:
    """Build one position for a fixture."""
    return TrackPoint(latitude=latitude, longitude=longitude, elevation=elevation, time=at)


def _walk(
    seconds: int,
    *,
    metres_per_second: float = 1.4,
    start: datetime = START,
    elevation: float | None = None,
) -> TrackSegment:
    """Build a segment moving steadily north, one position per second."""
    step = metres_per_second / METRES_PER_LATITUDE_DEGREE
    return TrackSegment(
        points=tuple(
            _point(
                index * step,
                elevation=elevation,
                at=start + timedelta(seconds=index),
            )
            for index in range(seconds + 1)
        )
    )


def _stationary(seconds: int, *, start: datetime = START, jitter: float = 3.0) -> TrackSegment:
    """Build a segment that stays put while the receiver wanders.

    The wander is deterministic and alternating, which is what stationary GPS
    noise looks like: every sample is metres away from the last one, and the net
    displacement over any window stays roughly zero.
    """
    step = jitter / METRES_PER_LATITUDE_DEGREE
    return TrackSegment(
        points=tuple(
            _point(step * (index % 2), at=start + timedelta(seconds=index))
            for index in range(seconds + 1)
        )
    )


# --- Distance ---------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.analysis
def test_distance_is_the_sum_of_horizontal_distances_between_consecutive_points() -> None:
    """The definition the whole statistics layer rests on."""
    analysis = analyze_track((_walk(100),))

    distance = analysis.metric(MetricName.DISTANCE)
    assert distance is not None
    assert distance.value == pytest.approx(140.0, rel=0.01)
    assert distance.unit is MetricUnit.METRES
    assert distance.provenance is MetricProvenance.DERIVED


@pytest.mark.contract
@pytest.mark.analysis
def test_a_segment_boundary_is_never_bridged() -> None:
    """Two segments a kilometre apart are not one kilometre of travel.

    A recording that was paused and resumed arrives as separate segments, and
    joining their ends would invent movement nobody made.
    """
    near = TrackSegment(points=(_point(0.0), _point(0.001)))
    far = TrackSegment(points=(_point(1.0), _point(1.001)))

    together = analyze_track((near, far))
    apart = analyze_track((near,)).value_of(MetricName.DISTANCE)

    assert apart is not None
    assert together.value_of(MetricName.DISTANCE) == pytest.approx(2 * apart, rel=1e-9)


@pytest.mark.contract
@pytest.mark.analysis
def test_a_single_point_segment_contributes_no_distance() -> None:
    """One position is a place, not a movement."""
    analysis = analyze_track((TrackSegment(points=(_point(51.2, 6.8),)),))

    assert analysis.value_of(MetricName.DISTANCE) == pytest.approx(0.0)


@pytest.mark.contract
@pytest.mark.analysis
def test_distance_needs_no_timestamps() -> None:
    """A planned route has geometry, so it has a length.

    Distance is geometry. Making it depend on timing would leave every planned
    route without the one metric it can legitimately have.
    """
    planned = TrackSegment(points=(_point(0.0), _point(0.01), _point(0.02)))

    analysis = analyze_track((planned,))

    distance = analysis.value_of(MetricName.DISTANCE)
    assert distance is not None
    assert distance > 0.0


@pytest.mark.contract
@pytest.mark.analysis
def test_elevation_never_enters_the_standard_distance() -> None:
    """Standard activity distance is horizontal. A 3D length is a separate metric."""
    flat = TrackSegment(points=(_point(0.0, elevation=0.0), _point(0.01, elevation=0.0)))
    steep = TrackSegment(points=(_point(0.0, elevation=0.0), _point(0.01, elevation=800.0)))

    assert analyze_track((steep,)).value_of(MetricName.DISTANCE) == pytest.approx(
        analyze_track((flat,)).value_of(MetricName.DISTANCE)
    )


# --- Time -------------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.analysis
def test_the_activity_start_comes_from_the_positions() -> None:
    """A document's export time is not an activity start."""
    analysis = analyze_track((_walk(60),))

    assert analysis.first_observed_at == START
    assert analysis.last_observed_at == START + timedelta(seconds=60)


@pytest.mark.contract
@pytest.mark.analysis
def test_a_track_without_timestamps_reports_no_temporal_metrics() -> None:
    """Missing is not zero.

    Reporting ``moving_duration = 0`` for a planned route would mean "this route
    was travelled, and nobody moved", which is a different and false claim.
    """
    planned = TrackSegment(points=(_point(0.0), _point(0.01), _point(0.02)))

    analysis = analyze_track((planned,))

    assert analysis.first_observed_at is None
    for name in (
        MetricName.ELAPSED_DURATION,
        MetricName.MOVING_DURATION,
        MetricName.STOPPED_DURATION,
        MetricName.AVERAGE_SPEED,
        MetricName.MOVING_AVERAGE_SPEED,
        MetricName.MAXIMUM_SPEED,
    ):
        assert analysis.metric(name) is None
    assert AnalysisQuality.INSUFFICIENT_TEMPORAL_DATA in analysis.quality


@pytest.mark.contract
@pytest.mark.analysis
def test_a_non_positive_interval_never_produces_a_speed() -> None:
    """Two positions at the same instant did not happen at infinite speed."""
    duplicated = TrackSegment(
        points=(
            _point(0.0, at=START),
            _point(0.01, at=START),
            _point(0.02, at=START + timedelta(seconds=30)),
        )
    )

    analysis = analyze_track((duplicated,))

    maximum = analysis.value_of(MetricName.MAXIMUM_SPEED)
    assert maximum is not None
    assert maximum < 1000.0


@pytest.mark.contract
@pytest.mark.analysis
def test_a_backwards_timestamp_is_reported_rather_than_absorbed() -> None:
    """Time going backwards is a data defect that stays visible."""
    backwards = TrackSegment(
        points=(
            _point(0.0, at=START),
            _point(0.001, at=START + timedelta(seconds=30)),
            _point(0.002, at=START + timedelta(seconds=10)),
            _point(0.003, at=START + timedelta(seconds=60)),
        )
    )

    analysis = analyze_track((backwards,))

    assert AnalysisQuality.NON_MONOTONIC_TIMESTAMPS in analysis.quality


@pytest.mark.contract
@pytest.mark.analysis
def test_an_unobserved_gap_is_not_an_observed_stop() -> None:
    """A device that stopped reporting did not prove that its owner stopped.

    Recording paused, battery saving, a lost fix and a genuine rest all produce
    the same silence, so the silence is reported as its own quantity.
    """
    first = _walk(60)
    resumed = _walk(60, start=START + timedelta(minutes=40))
    joined = TrackSegment(points=first.points + resumed.points)

    analysis = analyze_track((joined,))

    gap = analysis.value_of(MetricName.UNOBSERVED_GAP_DURATION)
    stopped = analysis.value_of(MetricName.STOPPED_DURATION)
    assert gap is not None
    assert gap == pytest.approx(timedelta(minutes=40).total_seconds() - 60, abs=1.0)
    assert stopped == pytest.approx(0.0, abs=1.0)
    assert AnalysisQuality.LARGE_UNOBSERVED_GAPS in analysis.quality


@pytest.mark.contract
@pytest.mark.analysis
def test_time_between_segments_is_unobserved_rather_than_stationary() -> None:
    """The recorder was off between two segments, so nothing was observed."""
    analysis = analyze_track((_walk(60), _walk(60, start=START + timedelta(minutes=30))))

    assert analysis.value_of(MetricName.UNOBSERVED_GAP_DURATION) == pytest.approx(
        timedelta(minutes=29).total_seconds(), abs=1.0
    )


@pytest.mark.contract
@pytest.mark.analysis
def test_the_time_decomposition_never_exceeds_the_elapsed_duration() -> None:
    """Moving, stopped and unobserved time are parts of one whole."""
    analysis = analyze_track(
        (
            _walk(120),
            _stationary(300, start=START + timedelta(seconds=200)),
            _walk(120, start=START + timedelta(minutes=45)),
        )
    )

    elapsed = analysis.value_of(MetricName.ELAPSED_DURATION)
    parts = [
        analysis.value_of(MetricName.MOVING_DURATION),
        analysis.value_of(MetricName.STOPPED_DURATION),
        analysis.value_of(MetricName.UNOBSERVED_GAP_DURATION),
    ]
    assert elapsed is not None
    assert all(part is not None and part >= 0.0 for part in parts)
    assert sum(part for part in parts if part is not None) <= elapsed + 1e-6


# --- Movement ---------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.analysis
def test_stationary_receiver_noise_is_not_movement() -> None:
    """The defect a naive per-point speed threshold produces.

    A receiver standing still reports positions metres apart every second, which
    is several metres per second of apparent speed. Counting that as movement
    would make every coffee break look like a walk.
    """
    analysis = analyze_track((_stationary(600),))

    moving = analysis.value_of(MetricName.MOVING_DURATION)
    stopped = analysis.value_of(MetricName.STOPPED_DURATION)
    assert moving is not None
    assert stopped is not None
    assert moving < 60.0
    assert stopped > 500.0


@pytest.mark.contract
@pytest.mark.analysis
def test_steady_walking_is_movement() -> None:
    """The other half of the same rule: real travel has to survive the filter."""
    analysis = analyze_track((_walk(600),))

    moving = analysis.value_of(MetricName.MOVING_DURATION)
    stopped = analysis.value_of(MetricName.STOPPED_DURATION)
    assert moving is not None
    assert stopped is not None
    assert moving > 540.0
    assert stopped < 60.0


@pytest.mark.contract
@pytest.mark.analysis
def test_a_single_position_jump_does_not_become_the_maximum_speed() -> None:
    """One bad fix must not decide a headline number.

    A receiver that briefly reports a position a kilometre away and then returns
    did not travel two kilometres at speed.
    """
    walked = _walk(600)
    points = list(walked.points)
    jumped = points[300]
    points[300] = _point(jumped.latitude + 0.01, jumped.longitude, at=jumped.time)

    analysis = analyze_track((TrackSegment(points=tuple(points)),))

    maximum = analysis.value_of(MetricName.MAXIMUM_SPEED)
    assert maximum is not None
    assert maximum < 5.0


@pytest.mark.contract
@pytest.mark.analysis
def test_the_moving_average_speed_uses_the_distance_of_moving_intervals() -> None:
    """Total distance over moving time would mix in what happened while stopped."""
    analysis = analyze_track(
        (
            TrackSegment(
                points=_walk(300).points
                + _stationary(300, start=START + timedelta(seconds=301)).points
            ),
        )
    )

    moving_average = analysis.value_of(MetricName.MOVING_AVERAGE_SPEED)
    assert moving_average is not None
    assert moving_average == pytest.approx(1.4, rel=0.3)


# --- Elevation --------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.analysis
def test_receiver_noise_on_a_flat_track_is_not_elevation_gain() -> None:
    """The defect a naive positive-difference sum produces.

    Summing every upward step over a flat walk turns a metre of receiver noise
    per sample into hundreds of metres of climbing.
    """
    flat = TrackSegment(
        points=tuple(
            _point(
                index / METRES_PER_LATITUDE_DEGREE,
                elevation=100.0 + (1.0 if index % 2 else -1.0),
                at=START + timedelta(seconds=index),
            )
            for index in range(600)
        )
    )

    analysis = analyze_track((flat,))

    gain = analysis.value_of(MetricName.ELEVATION_GAIN)
    assert gain is not None
    assert gain < 10.0


@pytest.mark.contract
@pytest.mark.analysis
def test_a_steady_climb_is_reported_in_full() -> None:
    """The filter removes noise, not terrain."""
    climb = TrackSegment(
        points=tuple(
            _point(
                index / METRES_PER_LATITUDE_DEGREE,
                elevation=100.0 + index,
                at=START + timedelta(seconds=index),
            )
            for index in range(301)
        )
    )

    analysis = analyze_track((climb,))

    gain = analysis.value_of(MetricName.ELEVATION_GAIN)
    loss = analysis.value_of(MetricName.ELEVATION_LOSS)
    assert gain == pytest.approx(300.0, rel=0.05)
    assert loss == pytest.approx(0.0, abs=5.0)


@pytest.mark.contract
@pytest.mark.analysis
def test_a_single_elevation_spike_is_not_a_climb() -> None:
    """One impossible sample is a defect, not a mountain."""
    points = [
        _point(
            index / METRES_PER_LATITUDE_DEGREE,
            elevation=100.0,
            at=START + timedelta(seconds=index),
        )
        for index in range(200)
    ]
    spiked = points[100]
    points[100] = _point(spiked.latitude, elevation=900.0, at=spiked.time)

    analysis = analyze_track((TrackSegment(points=tuple(points)),))

    gain = analysis.value_of(MetricName.ELEVATION_GAIN)
    assert gain is not None
    assert gain < 50.0


@pytest.mark.contract
@pytest.mark.analysis
def test_elevation_extremes_come_from_the_valid_observations() -> None:
    """Minimum and maximum report what the source actually recorded."""
    profile = TrackSegment(
        points=(
            _point(0.0, elevation=100.0),
            _point(0.001, elevation=None),
            _point(0.002, elevation=340.0),
            _point(0.003, elevation=220.0),
        )
    )

    analysis = analyze_track((profile,))

    assert analysis.value_of(MetricName.ELEVATION_MINIMUM) == pytest.approx(100.0)
    assert analysis.value_of(MetricName.ELEVATION_MAXIMUM) == pytest.approx(340.0)


@pytest.mark.contract
@pytest.mark.analysis
def test_a_track_without_elevation_reports_no_elevation_metrics() -> None:
    """Missing is not zero, here as everywhere."""
    analysis = analyze_track((TrackSegment(points=(_point(0.0), _point(0.01))),))

    for name in (
        MetricName.ELEVATION_MINIMUM,
        MetricName.ELEVATION_MAXIMUM,
        MetricName.ELEVATION_GAIN,
        MetricName.ELEVATION_LOSS,
    ):
        assert analysis.metric(name) is None
    assert AnalysisQuality.INSUFFICIENT_ELEVATION_DATA in analysis.quality


@pytest.mark.contract
@pytest.mark.analysis
def test_elevation_gain_is_never_accumulated_across_a_segment_boundary() -> None:
    """Two segments at different altitudes did not climb between each other."""
    low = TrackSegment(points=(_point(0.0, elevation=100.0), _point(0.001, elevation=100.0)))
    high = TrackSegment(points=(_point(1.0, elevation=900.0), _point(1.001, elevation=900.0)))

    analysis = analyze_track((low, high))

    assert analysis.value_of(MetricName.ELEVATION_GAIN) == pytest.approx(0.0, abs=1.0)


# --- Provenance and units ---------------------------------------------------


@pytest.mark.contract
@pytest.mark.analysis
def test_every_metric_states_a_unit_and_a_provenance() -> None:
    """A bare number is not a metric.

    Provenance is part of a metric's meaning, and a value without a unit is what
    makes ``22.4`` mean four different things to four readers.
    """
    analysis = analyze_track((_walk(600, elevation=120.0),))

    assert analysis.metrics
    for name, metric in analysis.metrics.items():
        assert isinstance(metric.unit, MetricUnit), name
        assert isinstance(metric.provenance, MetricProvenance), name


@pytest.mark.contract
@pytest.mark.analysis
@pytest.mark.parametrize(
    ("name", "unit"),
    [
        (MetricName.DISTANCE, MetricUnit.METRES),
        (MetricName.ELEVATION_GAIN, MetricUnit.METRES),
        (MetricName.ELAPSED_DURATION, MetricUnit.SECONDS),
        (MetricName.MOVING_DURATION, MetricUnit.SECONDS),
        (MetricName.AVERAGE_SPEED, MetricUnit.METRES_PER_SECOND),
        (MetricName.MAXIMUM_SPEED, MetricUnit.METRES_PER_SECOND),
    ],
)
def test_metrics_are_stored_in_stable_si_units(name: MetricName, unit: MetricUnit) -> None:
    """Internal values are SI. Kilometres and hours are a presentation decision."""
    analysis = analyze_track((_walk(600, elevation=120.0),))

    metric = analysis.metric(name)
    assert metric is not None
    assert metric.unit is unit


@pytest.mark.contract
@pytest.mark.analysis
def test_derived_metrics_are_never_measured() -> None:
    """Nothing here was read from a sensor; every value was computed."""
    analysis = analyze_track((_walk(600, elevation=120.0),))

    assert {metric.provenance for metric in analysis.metrics.values()} == {MetricProvenance.DERIVED}


# --- Profile ----------------------------------------------------------------


@pytest.mark.contract
@pytest.mark.analysis
def test_the_installed_profile_names_every_algorithm_it_applies() -> None:
    """Asking why an elevation gain changed has to be answerable from stored data."""
    assert isinstance(ANALYSIS_PROFILE, AnalysisProfile)
    assert ANALYSIS_PROFILE.distance_algorithm
    assert ANALYSIS_PROFILE.movement_algorithm
    assert ANALYSIS_PROFILE.elevation_algorithm
    assert ANALYSIS_PROFILE.metric_schema_version >= 1


@pytest.mark.contract
@pytest.mark.analysis
def test_a_profile_that_cannot_name_an_algorithm_is_rejected() -> None:
    """A profile that proves nothing about its output is not a profile."""
    with pytest.raises(ValueError, match="algorithm"):
        AnalysisProfile(
            distance_algorithm=" ",
            distance_algorithm_version=1,
            movement_algorithm="windowed-speed",
            movement_algorithm_version=1,
            elevation_algorithm="median-deadband",
            elevation_algorithm_version=1,
            metric_schema_version=1,
        )


@pytest.mark.contract
@pytest.mark.analysis
def test_analysis_is_reproducible() -> None:
    """The same geometry analysed twice produces the same numbers.

    Derived state is rebuildable, and "rebuildable" is worth nothing if a rebuild
    can disagree with what it replaces.
    """
    segments = (_walk(300, elevation=200.0), _stationary(120, start=START + timedelta(minutes=10)))

    assert analyze_track(segments).metrics == analyze_track(segments).metrics
