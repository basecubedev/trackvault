"""Executable contract: one analysis, two views of it, bounded output.

A map and a chart are two projections of one set of samples, and the failure
they invite is the quiet one: a chart drawn from raw altitude under a headline
computed from a filtered one, or a speed line drawn point to point under a
figure labelled "maximum sustained speed". Both look fine. Both are two
different tracks sharing an axis.

```
elevation_gain_m   accumulated from the filtered profile
elevation series   the same filtered profile
maximum speed      the highest sustained window speed
speed series       the same sustained window speeds
```

The second thing this pins down is identity. A marker on a map and a cursor on a
chart address a sample by `(segment_index, point_index)` -- the position's own
address in the geometry -- rather than by looking for a nearby latitude, which
is a second authority on identity and picks the wrong position exactly where a
track crosses itself.

The third is size. An archive holds recordings of a hundred thousand positions,
and a response nobody can render is a feature nobody has.
"""

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trackvault.application.import_tracks import ImportRequest
from trackvault.config import Settings
from trackvault.domain import TrackPoint, TrackSegment
from trackvault.domain.analysis import analyse_elevation, analyse_movement
from trackvault.domain.analysis.series import derive_profile
from trackvault.infrastructure.assembly import build_services
from trackvault.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.analysis]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

_DEGREE = 111_195.0
START = datetime(2025, 10, 5, 9, 0, tzinfo=UTC)

WALK = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>{name}</name><type>walking</type>
{segments}
  </trk>
</gpx>
"""


def _segment_xml(points: list[tuple[float, float, float]], offset: int) -> str:
    """Render one segment of (north metres, elevation, second) triples."""
    rows = "\n".join(
        f'      <trkpt lat="{north / _DEGREE:.8f}" lon="8.0"><ele>{elevation:.1f}</ele>'
        f"<time>{(START + timedelta(seconds=second)).isoformat().replace('+00:00', 'Z')}</time>"
        f"<hdop>1.1</hdop></trkpt>"
        for north, elevation, second in points
    )
    assert offset >= 0
    return f"    <trkseg>\n{rows}\n    </trkseg>"


def _hill(count: int = 240, *, mps: float = 1.4) -> list[tuple[float, float, float]]:
    """A walk up a hill and down again, so the profile has a real summit."""
    return [
        (
            index * mps,
            100.0 + 60.0 * math.sin(math.pi * index / (count - 1)),
            float(index),
        )
        for index in range(count)
    ]


def _document(name: str, segments: list[list[tuple[float, float, float]]]) -> bytes:
    """Render a whole GPX document from segments of triples."""
    body = "\n".join(_segment_xml(points, index) for index, points in enumerate(segments))
    return WALK.format(name=name, segments=body).encode("utf-8")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data", timezone="UTC")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Yield a client over a throwaway archive."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _import(client: TestClient, content: bytes, name: str) -> int:
    """Import one document through the canonical use case."""
    services = client.app.state.services  # type: ignore[attr-defined]
    outcome = services.import_tracks(ImportRequest(content=content, original_filename=name))
    return int(outcome.track_ids[0])


@pytest.fixture
def track(client: TestClient) -> int:
    """A single-segment walk over a hill."""
    return _import(client, _document("hill", [_hill()]), "hill.gpx")


@pytest.fixture
def two_segments(client: TestClient) -> int:
    """A walk recorded in two segments, with a real interruption between them."""
    first = _hill(120)
    second = [
        (north + 5_000.0, elevation, second + 3_600.0) for north, elevation, second in _hill(120)
    ]
    return _import(client, _document("two", [first, second]), "two.gpx")


def _profile(client: TestClient, track_id: int, **params: object) -> dict[str, object]:
    """Ask for one track's series."""
    response = client.get(f"/api/v1/tracks/{track_id}/profile", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _samples(body: dict[str, object]) -> list[dict[str, object]]:
    """Flatten a profile response into its samples, in traversal order."""
    return [sample for segment in body["segments"] for sample in segment["samples"]]  # type: ignore[attr-defined,index]


# --- One analysis, two views -------------------------------------------------


def test_the_elevation_series_is_the_one_the_ascent_was_accumulated_from() -> None:
    """A filtered figure over a raw line is two pictures of two tracks."""
    points = tuple(
        TrackPoint(
            latitude=index * 1.4 / _DEGREE,
            longitude=8.0,
            elevation=100.0 + 60.0 * math.sin(math.pi * index / 239) + (2.0 if index % 7 else -2.0),
            time=START + timedelta(seconds=index),
        )
        for index in range(240)
    )
    segments = (TrackSegment(points=points),)

    aggregate = analyse_elevation(segments)
    series = [
        sample.filtered_elevation_m
        for segment in derive_profile(segments).segments
        for sample in segment.samples
    ]
    values = [value for value in series if value is not None]

    assert aggregate.gain_metres is not None
    assert values, "the chart had no filtered elevation to draw"
    assert values != [point.elevation for point in points], "the series was the raw signal"
    # The ascent of the series a chart draws has to be the ascent that is
    # reported beside it. Recomputing it from the series is the check.
    redrawn = sum(max(0.0, later - earlier) for earlier, later in pairwise(values))
    assert redrawn >= aggregate.gain_metres * 0.9


def test_the_speed_series_shares_the_movement_authority(track: int, client: TestClient) -> None:
    """The peak of the series must not exceed the maximum the headline quotes."""
    body = _profile(client, track, max_samples=20_000)
    speeds = [sample["speed_mps"] for sample in _samples(body) if sample["speed_mps"] is not None]
    analysis = client.get(f"/api/v1/tracks/{track}/analysis").json()

    assert speeds
    assert analysis["timed_path"]["maximum_sustained_speed_mps"] == pytest.approx(
        max(speeds), rel=1e-6
    )


def test_an_underivable_speed_is_absent_rather_than_zero(client: TestClient) -> None:
    """A hole in the series is a hole. Zero would draw it as a stop."""
    untimed = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>route</name><trkseg>
    <trkpt lat="0.0" lon="9.0"><ele>10.0</ele></trkpt>
    <trkpt lat="0.01" lon="9.0"><ele>20.0</ele></trkpt>
    <trkpt lat="0.02" lon="9.0"><ele>30.0</ele></trkpt>
  </trkseg></trk>
</gpx>
"""
    track_id = _import(client, untimed.encode("utf-8"), "untimed.gpx")

    samples = _samples(_profile(client, track_id))

    assert samples
    assert all(sample["speed_mps"] is None for sample in samples)
    assert all(sample["time"] is None for sample in samples)
    assert all(sample["elevation_m"] is not None for sample in samples)


# --- The distance axis -------------------------------------------------------


def test_cumulative_distance_is_monotonic(track: int, client: TestClient) -> None:
    """An axis that goes backwards is not an axis."""
    distances = [sample["distance_m"] for sample in _samples(_profile(client, track))]

    assert distances == sorted(distances)
    assert distances[0] == pytest.approx(0.0)


def test_a_segment_boundary_is_not_bridged(two_segments: int, client: TestClient) -> None:
    """The five kilometres between two segments were not travelled."""
    body = _profile(client, two_segments, max_samples=20_000)
    segments = body["segments"]
    assert isinstance(segments, list)
    assert len(segments) == 2

    first_end = segments[0]["samples"][-1]["distance_m"]
    second_start = segments[1]["samples"][0]["distance_m"]

    assert second_start == pytest.approx(first_end), "the gap between segments entered the axis"
    assert body["total_distance_m"] == pytest.approx(segments[1]["samples"][-1]["distance_m"])


def test_the_reported_distance_matches_the_analysis(track: int, client: TestClient) -> None:
    """The axis a chart draws and the distance a headline quotes are one length."""
    body = _profile(client, track)
    analysis = client.get(f"/api/v1/tracks/{track}/analysis").json()

    assert body["total_distance_m"] == pytest.approx(analysis["geometry"]["distance_m"], rel=1e-6)


# --- Sample identity ---------------------------------------------------------


def test_a_sample_carries_the_identity_the_geometry_uses(
    two_segments: int, client: TestClient
) -> None:
    """A map marker and a chart cursor address the same position by index."""
    body = _profile(client, two_segments, max_samples=20_000)
    geometry = client.get(f"/api/v1/tracks/{two_segments}/geometry").json()

    for segment in body["segments"]:
        for sample in segment["samples"]:
            point = geometry["segments"][sample["segment_index"]]["points"][sample["point_index"]]
            assert sample["latitude"] == pytest.approx(point["latitude"])
            assert sample["longitude"] == pytest.approx(point["longitude"])


def test_identity_survives_decimation(two_segments: int, client: TestClient) -> None:
    """A reduced chart still points at positions the full map holds."""
    full = {
        (sample["segment_index"], sample["point_index"])
        for sample in _samples(_profile(client, two_segments, max_samples=20_000))
    }
    reduced = {
        (sample["segment_index"], sample["point_index"])
        for sample in _samples(_profile(client, two_segments, max_samples=40))
    }

    assert reduced <= full
    assert reduced != full


# --- Bounded output ----------------------------------------------------------


@pytest.fixture(scope="module")
def large_archive(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """A client whose archive holds one very large track.

    Module scoped: importing a hundred thousand positions is the expensive part
    of these tests and it is the same track every time, so paying for it once is
    the difference between a suite people run and one they skip.
    """
    settings = Settings(data_dir=tmp_path_factory.mktemp("large") / "data", timezone="UTC")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def enormous(large_archive: TestClient) -> int:
    """A hundred thousand positions, generated rather than committed.

    A fixture file of this size in the repository would be a hundred thousand
    lines nobody reads, and the point of the test is the *shape* of the track
    rather than any particular position in it.
    """
    points = [
        (index * 1.4, 100.0 + 200.0 * math.sin(index / 3_000.0), float(index))
        for index in range(100_000)
    ]
    return _import(large_archive, _document("enormous", [points]), "enormous.gpx")


def test_a_very_large_track_returns_a_bounded_profile(
    enormous: int, large_archive: TestClient
) -> None:
    """The default is a page of samples, not the recording."""
    body = _profile(large_archive, enormous)

    assert body["total_sample_count"] == 100_000
    assert body["sample_count"] <= 2000
    assert len(_samples(body)) == body["sample_count"]


def test_the_server_keeps_the_last_word_on_the_sample_count(
    enormous: int, large_archive: TestClient
) -> None:
    """A query string may not raise a server maximum."""
    assert enormous
    response = large_archive.get(
        f"/api/v1/tracks/{enormous}/profile", params={"max_samples": 10_000_000}
    )

    assert response.status_code == 422


def test_a_decimated_profile_keeps_the_ends_and_the_summit(
    enormous: int, large_archive: TestClient
) -> None:
    """What a chart is read for must survive being made smaller."""
    body = _profile(large_archive, enormous, max_samples=500)
    samples = _samples(body)
    peak = max(sample["filtered_elevation_m"] for sample in samples)
    full_peak = _profile(large_archive, enormous, max_samples=20_000)
    absolute = max(sample["filtered_elevation_m"] for sample in _samples(full_peak))

    assert samples[0]["point_index"] == 0
    assert samples[-1]["point_index"] == 99_999
    assert peak == pytest.approx(absolute, rel=1e-6), "the summit was decimated away"


def test_a_very_large_track_returns_a_bounded_geometry(
    enormous: int, large_archive: TestClient
) -> None:
    """A map asks for a shape it can draw."""
    response = large_archive.get(f"/api/v1/tracks/{enormous}/geometry", params={"max_points": 1000})

    assert response.status_code == 200
    body = response.json()
    assert body["simplified"] is True
    assert body["point_count"] <= 1000
    assert body["total_point_count"] == 100_000


def test_the_canonical_geometry_is_still_canonical(track: int, client: TestClient) -> None:
    """Without a bound, the geometry endpoint is the normalized track."""
    body = client.get(f"/api/v1/tracks/{track}/geometry").json()

    assert body["simplified"] is False
    assert body["point_count"] == body["total_point_count"] == 240


def test_simplification_never_joins_two_segments(two_segments: int, client: TestClient) -> None:
    """A line across a recording interruption is ground nobody travelled."""
    body = client.get(f"/api/v1/tracks/{two_segments}/geometry", params={"max_points": 10}).json()

    assert len(body["segments"]) == 2
    assert all(len(segment["points"]) >= 2 for segment in body["segments"])


def test_simplification_never_touches_the_stored_geometry(track: int, client: TestClient) -> None:
    """Asking for fewer points is not a way to lose data."""
    before = client.get(f"/api/v1/tracks/{track}/geometry").json()
    client.get(f"/api/v1/tracks/{track}/geometry", params={"max_points": 10})
    after = client.get(f"/api/v1/tracks/{track}/geometry").json()

    assert after == before


# --- What the series are, and what the stored analysis is --------------------


def test_the_profile_states_which_algorithms_derived_it(track: int, client: TestClient) -> None:
    """A chart that cannot say what produced it cannot be checked."""
    body = _profile(client, track)

    assert body["analysis_status"] == "current"
    assert isinstance(body["derived_with"], dict)
    assert body["derived_with"]["movement_algorithm"]
    assert body["timing_basis"] == "observed"
    assert body["is_actual_activity_timing"] is True


def test_a_profile_is_served_for_a_track_with_no_stored_analysis(
    client: TestClient, settings: Settings, track: int
) -> None:
    """Geometry has a shape whether or not anything has been derived from it."""
    import sqlite3

    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE tracks SET current_analysis_run_id = NULL WHERE id = ?", (track,)
        )
        connection.commit()

    body = _profile(client, track)

    assert body["analysis_status"] == "missing"
    assert body["sample_count"] > 0
    assert any(sample["filtered_elevation_m"] is not None for sample in _samples(body))


def test_an_unknown_track_has_no_profile(client: TestClient) -> None:
    """The same stable error envelope as everywhere else."""
    response = client.get("/api/v1/tracks/4711/profile")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "track_not_found", "message": "no such track"}}


def test_the_profile_is_source_agnostic() -> None:
    """The entry point takes segments. Nothing about a format is reachable."""
    segments = (
        TrackSegment(
            points=(
                TrackPoint(latitude=0.0, longitude=9.0, elevation=10.0, time=START),
                TrackPoint(
                    latitude=0.01, longitude=9.0, elevation=20.0, time=START + timedelta(seconds=60)
                ),
            )
        ),
    )

    profile = derive_profile(segments)
    movement = analyse_movement(segments)

    assert profile.sample_count == 2
    assert profile.segments[0].samples[0].segment_index == 0
    assert movement.elapsed_seconds is not None


def test_a_profile_carries_what_the_sensors_measured(tmp_path: Path) -> None:
    """A recording with a chest strap has a heart rate at every position it read one.

    It travels beside the derived series rather than inside them. The elevation
    appears twice because the ascent is accumulated from one of the two; nothing
    is accumulated from this, so it is passed through exactly as measured.
    """
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    outcome = services.import_tracks(
        ImportRequest(content=(FIXTURES / "sensor-readings.gpx").read_bytes())
    )

    with TestClient(create_app(settings)) as client:
        payload = client.get(f"/api/v1/tracks/{outcome.track_ids[0]}/profile").json()

    samples = payload["segments"][0]["samples"]
    assert [sample["heart_rate_bpm"] for sample in samples] == [112, 128, None, 141]
    assert [sample["cadence_rpm"] for sample in samples] == [78, 82, 85, 0]


def test_a_track_without_sensors_reports_their_absence_rather_than_zero(
    tmp_path: Path,
) -> None:
    """Nobody's heart stopped. There was no monitor."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    outcome = services.import_tracks(
        ImportRequest(content=(FIXTURES / "recorded-measurements.gpx").read_bytes())
    )

    with TestClient(create_app(settings)) as client:
        payload = client.get(f"/api/v1/tracks/{outcome.track_ids[0]}/profile").json()

    samples = payload["segments"][0]["samples"]
    assert all(sample["heart_rate_bpm"] is None for sample in samples)
    assert all(sample["cadence_rpm"] is None for sample in samples)
