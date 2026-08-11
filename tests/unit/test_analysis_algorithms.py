"""The algorithmic edge cases each analysis step has to survive.

The contract test states what analysis *means*. This one exercises the shapes of
data that break naive implementations of it: coordinates at the seams of the
coordinate system, timing that a recorder can genuinely produce, and elevation
profiles that separate terrain from receiver noise.

Every fixture is synthetic. Nothing here is derived from a real recording.
"""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from gpx_view.domain import TrackPoint, TrackSegment
from gpx_view.domain.analysis import (
    ANALYSIS_PROFILE,
    AnalysisQuality,
    MetricName,
    MetricUnit,
    MetricValue,
    analyse_elevation,
    analyse_movement,
    analyze_track,
    geodesic_distance,
    quality_in_canonical_order,
)
from gpx_view.domain.analysis.profile import is_analysis_profile_current

START = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)
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


def _moving(
    seconds: int,
    *,
    metres_per_second: float,
    start: datetime = START,
    from_latitude: float = 0.0,
) -> list[TrackPoint]:
    """Build positions travelling steadily north at a given pace."""
    step = metres_per_second / METRES_PER_LATITUDE_DEGREE
    return [
        _point(from_latitude + index * step, at=start + timedelta(seconds=index))
        for index in range(seconds + 1)
    ]


def _still(
    seconds: int, *, start: datetime = START, at_latitude: float = 0.0, jitter: float = 4.0
) -> list[TrackPoint]:
    """Build positions that stay put while the receiver wanders."""
    step = jitter / METRES_PER_LATITUDE_DEGREE
    return [
        _point(at_latitude + step * (index % 2), at=start + timedelta(seconds=index))
        for index in range(seconds + 1)
    ]


def _profile(elevations: list[float | None]) -> TrackSegment:
    """Build a segment whose positions carry the given elevations."""
    return TrackSegment(
        points=tuple(
            _point(
                index / METRES_PER_LATITUDE_DEGREE,
                elevation=elevation,
                at=START + timedelta(seconds=index),
            )
            for index, elevation in enumerate(elevations)
        )
    )


# --- Distance edge cases ----------------------------------------------------


@pytest.mark.unit
@pytest.mark.analysis
def test_the_same_coordinate_is_no_distance_at_all() -> None:
    """A duplicate position must not produce a residue from rounding."""
    here = _point(47.123456, 11.987654)

    assert geodesic_distance(here, here) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.unit
@pytest.mark.analysis
def test_a_metre_scale_distance_stays_accurate() -> None:
    """Short steps are the normal case, and the formula is chosen for them."""
    start = _point(48.0, 11.0)
    one_metre_north = _point(48.0 + 1.0 / METRES_PER_LATITUDE_DEGREE, 11.0)

    assert geodesic_distance(start, one_metre_north) == pytest.approx(1.0, rel=0.01)


@pytest.mark.unit
@pytest.mark.analysis
def test_crossing_the_equator_is_an_ordinary_step() -> None:
    """The sign change of the latitude must not become a discontinuity."""
    step = 100.0 / METRES_PER_LATITUDE_DEGREE

    across = geodesic_distance(_point(-step / 2, 9.0), _point(step / 2, 9.0))

    assert across == pytest.approx(100.0, rel=0.01)


@pytest.mark.unit
@pytest.mark.analysis
def test_crossing_the_antimeridian_takes_the_short_way() -> None:
    """A step from 179.999 to -179.999 is metres, not most of the planet."""
    across = geodesic_distance(_point(0.0, 179.9995), _point(0.0, -179.9995))

    assert across < 200.0


@pytest.mark.unit
@pytest.mark.analysis
def test_a_degree_of_longitude_shrinks_towards_the_pole() -> None:
    """Longitude is not a fixed distance, and the formula has to know it."""
    at_equator = geodesic_distance(_point(0.0, 0.0), _point(0.0, 1.0))
    far_north = geodesic_distance(_point(80.0, 0.0), _point(80.0, 1.0))

    assert far_north < at_equator / 4.0


@pytest.mark.unit
@pytest.mark.analysis
def test_a_repeated_position_adds_nothing_to_a_track() -> None:
    """A stalled recorder writing the same fix twice did not travel."""
    once = TrackSegment(points=(_point(0.0), _point(0.01)))
    repeated = TrackSegment(points=(_point(0.0), _point(0.0), _point(0.01), _point(0.01)))

    assert analyze_track((repeated,)).value_of(MetricName.DISTANCE) == pytest.approx(
        analyze_track((once,)).value_of(MetricName.DISTANCE)
    )


# --- Movement ---------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.analysis
def test_bicycle_speeds_are_movement_under_the_same_generic_rule() -> None:
    """No per-activity threshold exists, so a faster activity needs no new rule."""
    movement = analyse_movement((TrackSegment(points=tuple(_moving(600, metres_per_second=6.0))),))

    assert movement.moving_seconds is not None
    assert movement.moving_seconds > 570.0
    assert movement.stopped_seconds == pytest.approx(0.0, abs=30.0)


@pytest.mark.unit
@pytest.mark.analysis
def test_a_stop_inside_a_recording_is_observed_rather_than_unobserved() -> None:
    """Positions kept arriving, so the rest is provable and is not a gap."""
    walked = _moving(300, metres_per_second=1.4)
    rested = _still(600, start=START + timedelta(seconds=301), at_latitude=walked[-1].latitude)
    onwards = _moving(
        300,
        metres_per_second=1.4,
        start=START + timedelta(seconds=902),
        from_latitude=walked[-1].latitude,
    )

    movement = analyse_movement(
        (TrackSegment(points=tuple(walked + rested + onwards)),),
    )

    assert movement.stopped_seconds is not None
    assert movement.moving_seconds is not None
    assert movement.stopped_seconds > 500.0
    assert movement.moving_seconds > 500.0
    assert movement.unobserved_seconds == pytest.approx(0.0, abs=5.0)


@pytest.mark.unit
@pytest.mark.analysis
def test_a_one_way_position_jump_is_excluded_rather_than_believed() -> None:
    """A teleport is a discontinuity in the data, not a sprint."""
    first = _moving(300, metres_per_second=1.4)
    # The recorder resumes ten kilometres away in the very next second.
    second = _moving(
        300, metres_per_second=1.4, start=START + timedelta(seconds=301), from_latitude=0.1
    )

    movement = analyse_movement((TrackSegment(points=tuple(first + second)),))

    assert movement.outliers_excluded
    assert movement.maximum_speed_mps is not None
    assert movement.maximum_speed_mps < 5.0


@pytest.mark.unit
@pytest.mark.analysis
def test_a_jump_and_return_leaves_the_surrounding_speeds_alone() -> None:
    """The bad fix is removed from its neighbours' windows, not averaged into them."""
    points = _moving(600, metres_per_second=1.4)
    spoiled = points[300]
    points[300] = _point(spoiled.latitude + 0.02, at=spoiled.time)

    movement = analyse_movement((TrackSegment(points=tuple(points)),))

    assert movement.outliers_excluded
    assert movement.maximum_speed_mps is not None
    assert movement.maximum_speed_mps == pytest.approx(1.4, rel=0.2)


@pytest.mark.unit
@pytest.mark.analysis
def test_duplicate_timestamps_do_not_stop_the_analysis() -> None:
    """A recorder writing two positions in the same second is ordinary."""
    points = _moving(120, metres_per_second=1.4)
    doubled = [point for pair in ((point, point) for point in points) for point in pair]

    movement = analyse_movement((TrackSegment(points=tuple(doubled)),))

    assert movement.moving_seconds is not None
    assert movement.moving_seconds > 100.0
    assert movement.maximum_speed_mps is not None
    assert movement.maximum_speed_mps < 10.0


@pytest.mark.unit
@pytest.mark.analysis
def test_partly_missing_timestamps_are_reported_and_survived() -> None:
    """Half a track's timing is still worth something, and the gap is named."""
    timed = _moving(300, metres_per_second=1.4)
    untimed = [_point(point.latitude) for point in _moving(300, metres_per_second=1.4)]

    analysis = analyze_track((TrackSegment(points=tuple(timed + untimed)),))

    assert AnalysisQuality.MISSING_TIMESTAMPS in analysis.quality
    assert analysis.value_of(MetricName.MOVING_DURATION) is not None
    assert analysis.value_of(MetricName.DISTANCE) is not None


@pytest.mark.unit
@pytest.mark.analysis
def test_sparse_sampling_is_not_mistaken_for_a_gap() -> None:
    """A track deliberately logged every five minutes is observed, not silent."""
    sparse = TrackSegment(
        points=tuple(
            _point(
                index * 300 * 1.4 / METRES_PER_LATITUDE_DEGREE,
                at=START + timedelta(minutes=5 * index),
            )
            for index in range(12)
        )
    )

    movement = analyse_movement((sparse,))

    assert not movement.large_unobserved_gaps
    assert movement.unobserved_seconds == pytest.approx(0.0, abs=1.0)
    assert movement.moving_seconds is not None
    assert movement.moving_seconds > 3000.0


# --- Elevation --------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.analysis
def test_a_perfectly_flat_profile_climbs_nothing() -> None:
    """The baseline the noisy cases are judged against."""
    elevation = analyse_elevation((_profile([120.0] * 200),))

    assert elevation.gain_metres == pytest.approx(0.0)
    assert elevation.loss_metres == pytest.approx(0.0)
    assert elevation.minimum_metres == pytest.approx(120.0)
    assert elevation.maximum_metres == pytest.approx(120.0)


@pytest.mark.unit
@pytest.mark.analysis
def test_a_steady_descent_is_reported_as_loss_and_not_as_gain() -> None:
    """Down is not up, and the deadband is symmetric."""
    elevation = analyse_elevation((_profile([500.0 - index for index in range(300)]),))

    assert elevation.loss_metres == pytest.approx(299.0, rel=0.05)
    assert elevation.gain_metres == pytest.approx(0.0, abs=5.0)


@pytest.mark.unit
@pytest.mark.analysis
def test_rolling_hills_accumulate_both_directions() -> None:
    """Real undulation above the noise band survives the filter."""
    hills: list[float | None] = []
    for _ in range(4):
        hills.extend(float(100 + step) for step in range(0, 60, 2))
        hills.extend(float(160 - step) for step in range(0, 60, 2))

    elevation = analyse_elevation((_profile(hills),))

    assert elevation.gain_metres is not None
    assert elevation.loss_metres is not None
    assert elevation.gain_metres == pytest.approx(232.0, rel=0.15)
    assert elevation.loss_metres == pytest.approx(232.0, rel=0.15)


@pytest.mark.unit
@pytest.mark.analysis
def test_elevation_holes_do_not_break_the_profile() -> None:
    """A missing altitude is skipped, never interpolated into a slope."""
    with_holes: list[float | None] = [
        None if index % 5 == 0 else 100.0 + index for index in range(200)
    ]

    elevation = analyse_elevation((_profile(with_holes),))

    assert elevation.gain_metres is not None
    assert elevation.gain_metres == pytest.approx(199.0, rel=0.1)


@pytest.mark.unit
@pytest.mark.analysis
def test_a_single_elevation_observation_supports_no_ascent() -> None:
    """One altitude is a height, not a climb -- and zero would be a claim."""
    single: list[float | None] = [None, 250.0, None]

    elevation = analyse_elevation((_profile(single),))

    assert elevation.minimum_metres == pytest.approx(250.0)
    assert elevation.gain_metres is None
    assert elevation.loss_metres is None


@pytest.mark.unit
@pytest.mark.analysis
def test_the_sampling_rate_does_not_change_the_ascent() -> None:
    """The giveaway of a naive sum: the same climb logged twice as often.

    A filtered profile reports the terrain. An unfiltered one reports the
    receiver, and therefore doubles when the receiver is asked twice as often.
    """
    noise = [1.0, -1.0, 0.5, -0.5]
    coarse: list[float | None] = [100.0 + index * 0.5 + noise[index % 4] for index in range(200)]
    fine: list[float | None] = [100.0 + index * 0.25 + noise[index % 4] for index in range(400)]

    coarse_gain = analyse_elevation((_profile(coarse),)).gain_metres
    fine_gain = analyse_elevation((_profile(fine),)).gain_metres

    assert coarse_gain is not None
    assert fine_gain is not None
    assert fine_gain == pytest.approx(coarse_gain, rel=0.1)


# --- Fail-closed boundaries -------------------------------------------------


@pytest.mark.unit
@pytest.mark.analysis
@pytest.mark.parametrize(
    "override",
    [
        {"distance_algorithm": "  "},
        {"movement_algorithm": ""},
        {"elevation_algorithm": "\t"},
    ],
)
def test_a_profile_missing_an_algorithm_name_is_rejected(override: dict[str, str]) -> None:
    """A profile that cannot name what produced a number proves nothing about it."""
    with pytest.raises(ValueError, match="algorithm"):
        replace(ANALYSIS_PROFILE, **override)


@pytest.mark.unit
@pytest.mark.analysis
@pytest.mark.parametrize(
    "override",
    [
        {"distance_algorithm_version": 0},
        {"movement_algorithm_version": -1},
        {"elevation_algorithm_version": 0},
    ],
)
def test_a_profile_with_an_unusable_version_is_rejected(override: dict[str, int]) -> None:
    """Version zero is not a version, and a negative one is not a mistake to keep."""
    with pytest.raises(ValueError, match="version must be positive"):
        replace(ANALYSIS_PROFILE, **override)


@pytest.mark.unit
@pytest.mark.analysis
def test_a_profile_with_an_unusable_metric_schema_is_rejected() -> None:
    """The metric set has a version too, for the same reason the algorithms do."""
    with pytest.raises(ValueError, match="metric schema version"):
        replace(ANALYSIS_PROFILE, metric_schema_version=0)


@pytest.mark.unit
@pytest.mark.analysis
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_metric_value_is_rejected(value: float) -> None:
    """NaN and infinity poison every later aggregation silently.

    One such value in one track would make a yearly total meaningless, and
    nothing downstream would report where it came from.
    """
    with pytest.raises(ValueError, match="finite"):
        MetricValue(value=value, unit=MetricUnit.METRES)


@pytest.mark.unit
@pytest.mark.analysis
def test_an_unproven_profile_is_never_current() -> None:
    """Absence of proof is not proof of currency.

    Reading a missing profile as "probably fine" would exclude exactly the
    results an algorithm upgrade is meant to reach.
    """
    assert not is_analysis_profile_current(None, ANALYSIS_PROFILE)
    assert not is_analysis_profile_current(ANALYSIS_PROFILE, None)
    assert is_analysis_profile_current(ANALYSIS_PROFILE, ANALYSIS_PROFILE)


@pytest.mark.unit
@pytest.mark.analysis
def test_a_newer_stored_profile_is_not_current_either() -> None:
    """The downgrade case.

    An older deployment must not report numbers a newer one wrote as up to
    date, or it would never regenerate them.
    """
    newer = replace(
        ANALYSIS_PROFILE,
        elevation_algorithm_version=ANALYSIS_PROFILE.elevation_algorithm_version + 1,
    )

    assert not is_analysis_profile_current(newer, ANALYSIS_PROFILE)


@pytest.mark.unit
@pytest.mark.analysis
def test_a_track_of_one_timed_position_yields_no_durations() -> None:
    """A single instant is not a span, and zero would claim it was one."""
    movement = analyse_movement((TrackSegment(points=(_point(0.0, at=START), _point(0.001))),))

    assert movement.moving_seconds is None
    assert movement.elapsed_seconds is None
    assert movement.missing_timestamps


@pytest.mark.unit
@pytest.mark.analysis
def test_a_track_whose_time_only_runs_backwards_reports_no_elapsed_duration() -> None:
    """A negative span is not a duration, so none is reported.

    Nor is any of the durations that would have to fit inside it. Reporting
    ``0`` for the moving time would say the track was observed and nobody moved,
    which is a claim about a clock that has already been shown to be broken.
    """
    backwards = TrackSegment(
        points=(
            _point(0.0, at=START + timedelta(minutes=10)),
            _point(0.001, at=START),
        )
    )

    movement = analyse_movement((backwards,))

    assert movement.elapsed_seconds is None
    assert movement.non_monotonic_timestamps
    assert movement.moving_seconds is None
    assert movement.stopped_seconds is None
    assert movement.unobserved_seconds is None
    assert movement.unattributed_seconds is None


@pytest.mark.unit
@pytest.mark.analysis
def test_segments_that_overlap_in_time_contribute_no_negative_gap() -> None:
    """Two segments whose instants overlap did not travel backwards between them."""
    first = TrackSegment(points=tuple(_moving(60, metres_per_second=1.4)))
    overlapping = TrackSegment(
        points=tuple(_moving(60, metres_per_second=1.4, start=START + timedelta(seconds=10)))
    )

    movement = analyse_movement((first, overlapping))

    assert movement.unobserved_seconds is not None
    assert movement.unobserved_seconds >= 0.0


@pytest.mark.unit
@pytest.mark.analysis
def test_a_quality_flag_set_is_ordered_and_deduplicated() -> None:
    """Stored quality has to compare equal however an algorithm noticed it."""
    first = quality_in_canonical_order(
        [
            AnalysisQuality.LARGE_UNOBSERVED_GAPS,
            AnalysisQuality.MISSING_TIMESTAMPS,
            AnalysisQuality.MISSING_TIMESTAMPS,
        ]
    )
    second = quality_in_canonical_order(
        [AnalysisQuality.MISSING_TIMESTAMPS, AnalysisQuality.LARGE_UNOBSERVED_GAPS]
    )

    assert first == second
    assert first == (
        AnalysisQuality.MISSING_TIMESTAMPS,
        AnalysisQuality.LARGE_UNOBSERVED_GAPS,
    )


@pytest.mark.unit
@pytest.mark.analysis
def test_a_hole_inside_a_segment_is_unattributable_rather_than_a_gap() -> None:
    """Positions without instants break the chain; they do not prove silence.

    Between two timed stretches of the same segment, the recorder *was*
    producing positions -- it simply did not timestamp them. Calling that an
    unobserved gap would claim the recording stopped, and bridging it would
    claim the time belonged to one interval or the other. It is neither, so it
    is left out of the decomposition and the missing timestamps are reported.
    """
    timed = _moving(60, metres_per_second=1.4)
    untimed = [_point(point.latitude) for point in _moving(10, metres_per_second=1.4)]
    resumed = _moving(60, metres_per_second=1.4, start=START + timedelta(minutes=30))

    movement = analyse_movement((TrackSegment(points=tuple(timed + untimed + resumed)),))

    assert movement.missing_timestamps
    assert movement.unobserved_seconds == pytest.approx(0.0)
    assert movement.moving_seconds is not None
    assert movement.elapsed_seconds is not None
    assert movement.moving_seconds < movement.elapsed_seconds
