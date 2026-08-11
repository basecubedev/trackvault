"""Unit tests for the normalized geometry value objects."""

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from trackvault.domain import TrackPoint, TrackSegment, recording_fingerprint

START = datetime(2026, 5, 4, 8, 0, tzinfo=UTC)


def _point(**overrides: object) -> TrackPoint:
    """Build a track point, overriding single fields for a scenario."""
    values: dict[str, object] = {"latitude": 51.2, "longitude": 6.8}
    values.update(overrides)
    return TrackPoint(**values)  # type: ignore[arg-type]


# --- Coordinate validation --------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("latitude", [-90.0, -45.5, 0.0, 45.5, 90.0])
def test_latitudes_inside_the_valid_range_are_accepted(latitude: float) -> None:
    """The poles are valid positions, so the range is inclusive."""
    assert _point(latitude=latitude).latitude == pytest.approx(latitude)


@pytest.mark.unit
@pytest.mark.parametrize("latitude", [-90.001, 90.001, 180.0, -1000.0])
def test_a_latitude_outside_the_valid_range_is_rejected(latitude: float) -> None:
    """A position that cannot exist is a parse defect, not data."""
    with pytest.raises(ValueError, match="latitude"):
        _point(latitude=latitude)


@pytest.mark.unit
@pytest.mark.parametrize("longitude", [-180.0, -6.8, 0.0, 6.8, 180.0])
def test_longitudes_inside_the_valid_range_are_accepted(longitude: float) -> None:
    """The antimeridian is a valid position, so the range is inclusive."""
    assert _point(longitude=longitude).longitude == pytest.approx(longitude)


@pytest.mark.unit
@pytest.mark.parametrize("longitude", [-180.001, 180.001, 360.0])
def test_a_longitude_outside_the_valid_range_is_rejected(longitude: float) -> None:
    """A position that cannot exist is a parse defect, not data."""
    with pytest.raises(ValueError, match="longitude"):
        _point(longitude=longitude)


@pytest.mark.unit
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_latitude_is_rejected(value: float) -> None:
    """NaN and infinity silently poison every later calculation."""
    with pytest.raises(ValueError, match="latitude"):
        _point(latitude=value)


@pytest.mark.unit
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_longitude_is_rejected(value: float) -> None:
    """NaN and infinity silently poison every later calculation."""
    with pytest.raises(ValueError, match="longitude"):
        _point(longitude=value)


@pytest.mark.unit
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_elevation_is_rejected(value: float) -> None:
    """An unusable elevation is rejected rather than stored as a number."""
    with pytest.raises(ValueError, match="elevation"):
        _point(elevation=value)


@pytest.mark.unit
def test_elevation_may_be_missing() -> None:
    """Not every source records elevation, and a missing value is not an error."""
    assert _point().elevation is None


@pytest.mark.unit
def test_a_negative_elevation_is_accepted() -> None:
    """Positions below sea level exist."""
    assert _point(elevation=-12.5).elevation == pytest.approx(-12.5)


# --- Timestamps -------------------------------------------------------------


@pytest.mark.unit
def test_a_point_timestamp_may_be_missing() -> None:
    """Tracks without time data are imported, not rejected."""
    assert _point().time is None


@pytest.mark.unit
def test_a_naive_point_timestamp_is_rejected() -> None:
    """Every stored instant is unambiguous, so local wall-clock time is refused."""
    with pytest.raises(ValueError, match="time"):
        _point(time=datetime(2026, 5, 4, 8, 0))


@pytest.mark.unit
def test_an_offset_timestamp_is_normalized_to_the_same_instant_in_utc() -> None:
    """Instants are stored unambiguously; the display timezone is a later decision."""
    point = _point(time=datetime(2026, 5, 4, 12, 0, tzinfo=timezone(timedelta(hours=2))))

    assert point.time == datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    assert point.time is not None
    assert point.time.utcoffset() == timedelta(0)


# --- Segments ---------------------------------------------------------------


@pytest.mark.unit
def test_a_segment_without_points_is_rejected() -> None:
    """A segment is a run of positions; an empty one carries no geometry."""
    with pytest.raises(ValueError, match="at least one point"):
        TrackSegment(points=())


@pytest.mark.unit
def test_a_segment_preserves_the_order_it_was_given() -> None:
    """Point order is source data and is never rearranged."""
    first = _point(latitude=51.0)
    second = _point(latitude=51.1)
    third = _point(latitude=51.2)

    segment = TrackSegment(points=(first, second, third))

    assert segment.points == (first, second, third)
    assert segment.point_count == 3


@pytest.mark.unit
def test_segments_are_compared_by_value() -> None:
    """Two segments holding the same positions in the same order are equal."""
    points = (_point(latitude=51.0), _point(latitude=51.1))

    assert TrackSegment(points=points) == TrackSegment(points=tuple(points))


@pytest.mark.unit
def test_a_reversed_segment_is_a_different_value() -> None:
    """Direction of travel is part of the data."""
    first = _point(latitude=51.0)
    second = _point(latitude=51.1)

    assert TrackSegment(points=(first, second)) != TrackSegment(points=(second, first))


@pytest.mark.unit
def test_a_segment_reports_the_extent_of_the_instants_it_observed() -> None:
    """The temporal extent is a fact about instants, not a claim about point order."""
    segment = TrackSegment(
        points=(
            _point(time=START + timedelta(minutes=5)),
            _point(time=START),
            _point(),
            _point(time=START + timedelta(minutes=9)),
        )
    )

    assert segment.earliest_time == START
    assert segment.latest_time == START + timedelta(minutes=9)


@pytest.mark.unit
def test_a_segment_without_any_timestamp_reports_no_extent() -> None:
    """Missing time data stays missing instead of being invented."""
    segment = TrackSegment(points=(_point(), _point(latitude=51.1)))

    assert segment.earliest_time is None
    assert segment.latest_time is None


class TestSensorReadings:
    """What a device measured beside the position.

    Heart rate and cadence are *measured* values, not derived ones, and they are
    the first thing here that is neither geometry nor a clock. They are optional
    exactly as elevation is: a phone in a pocket records neither, and a missing
    reading is data rather than a defect.
    """

    def test_a_position_carries_what_the_sensors_read(self) -> None:
        """The point of the whole thing."""
        point = TrackPoint(latitude=51.0, longitude=7.0, heart_rate_bpm=142, cadence_rpm=84)

        assert point.heart_rate_bpm == 142
        assert point.cadence_rpm == 84

    def test_a_position_without_sensors_is_normal(self) -> None:
        """Most tracks carry none, and that is not a gap to fill."""
        point = TrackPoint(latitude=51.0, longitude=7.0)

        assert point.heart_rate_bpm is None
        assert point.cadence_rpm is None

    @pytest.mark.parametrize("reading", [-1, -40])
    def test_a_negative_reading_is_refused_rather_than_stored(self, reading: int) -> None:
        """A negative heart rate is a parsing accident, not a measurement."""
        with pytest.raises(ValueError, match="heart rate"):
            TrackPoint(latitude=51.0, longitude=7.0, heart_rate_bpm=reading)
        with pytest.raises(ValueError, match="cadence"):
            TrackPoint(latitude=51.0, longitude=7.0, cadence_rpm=reading)

    def test_zero_is_a_reading_rather_than_an_absence(self) -> None:
        """A stopped cadence sensor reports zero, and that is a fact about the ride.

        Folding it into `None` would turn "the wheel was not turning" into "no
        sensor was here", which is the same conflation this model refuses for
        every other absent value.
        """
        point = TrackPoint(latitude=51.0, longitude=7.0, cadence_rpm=0)

        assert point.cadence_rpm == 0


class TestRecordingFingerprint:
    """What makes two imports the same recording.

    Deliberately an *equality*, not a similarity. Two documents whose normalized
    positions and instants match exactly, to full precision, are the same
    recording -- there is no other way for two receivers to produce that. This
    is therefore not the semantic-duplicate problem the project defers: no
    threshold, no window, nothing to tune.

    What it will not catch is exactly what that deferred problem is about: the
    same loop ridden twice, a trimmed export, a file whose coordinates were
    rounded on the way out. Those need a heuristic, and a heuristic that hides a
    real track is worse than showing two rows.
    """

    def _segments(self, *runs: tuple[tuple[float, float], ...]) -> tuple[TrackSegment, ...]:
        return tuple(
            TrackSegment(points=tuple(TrackPoint(latitude=lat, longitude=lon) for lat, lon in run))
            for run in runs
        )

    def test_the_same_positions_fingerprint_the_same(self) -> None:
        """The case that started this: one ride exported in three formats."""
        one = self._segments(((51.0, 7.0), (51.1, 7.1)))
        again = self._segments(((51.0, 7.0), (51.1, 7.1)))

        assert recording_fingerprint(one) == recording_fingerprint(again)

    def test_segment_boundaries_do_not_change_the_recording(self) -> None:
        """A route export flattens a paused recording into one run.

        It is the same positions at the same instants, so it is the same
        recording. Segment structure is source data about the document rather
        than about where somebody went.
        """
        split = self._segments(((51.0, 7.0),), ((51.1, 7.1),))
        flat = self._segments(((51.0, 7.0), (51.1, 7.1)))

        assert recording_fingerprint(split) == recording_fingerprint(flat)

    def test_a_different_ride_fingerprints_differently(self) -> None:
        """The property that makes this safe to group by."""
        one = self._segments(((51.0, 7.0), (51.1, 7.1)))
        other = self._segments(((51.0, 7.0), (51.1, 7.2)))

        assert recording_fingerprint(one) != recording_fingerprint(other)

    def test_the_clock_is_part_of_the_recording(self) -> None:
        """Two laps of one loop are two rides, and their instants say so."""
        morning = (
            TrackSegment(
                points=(
                    TrackPoint(
                        latitude=51.0, longitude=7.0, time=datetime(2026, 5, 4, 8, tzinfo=UTC)
                    ),
                )
            ),
        )
        evening = (
            TrackSegment(
                points=(
                    TrackPoint(
                        latitude=51.0, longitude=7.0, time=datetime(2026, 5, 4, 18, tzinfo=UTC)
                    ),
                )
            ),
        )

        assert recording_fingerprint(morning) != recording_fingerprint(evening)

    def test_a_reading_the_position_did_not_move_does_not_change_it(self) -> None:
        """A richer export of one ride is the same ride.

        The 1.1 export carries a heart rate and the 1.0 export cannot. Letting a
        sensor reading into the fingerprint would make the two different
        recordings, which is the opposite of what it is for.
        """
        plain = (TrackSegment(points=(TrackPoint(latitude=51.0, longitude=7.0),)),)
        with_sensors = (
            TrackSegment(points=(TrackPoint(latitude=51.0, longitude=7.0, heart_rate_bpm=140),)),
        )

        assert recording_fingerprint(plain) == recording_fingerprint(with_sensors)
