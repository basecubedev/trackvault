"""The HTTP contract for track analysis and statistics.

The API is a projection: it validates, calls a use case and shapes the result.
No route derives a metric, decides a track kind or draws a month boundary.

What is checked here is what a client may rely on:

* units are in the field names, so no reader has to guess metres from kilometres,
* a metric that could not be derived is `null`, never `0`,
* actual and planned totals are asked for separately and never arrive mixed,
* every statistics response names the timezone its buckets were drawn in.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.config import Settings
from gpx_view.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.statistics]

RECORDING = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Synthetic walk</name><type>walking</type><trkseg>
{points}
  </trkseg></trk>
</gpx>
"""

PLANNED = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <rte><name>Synthetic route</name>
    <rtept lat="0.0" lon="9.0"/>
    <rtept lat="0.9" lon="9.0"/>
  </rte>
</gpx>
"""


def _recording(count: int = 120, *, minute: int = 0) -> str:
    """Build a synthetic recording that analyses to real numbers."""
    points = "\n".join(
        f'    <trkpt lat="{index * 1.4 / 111195.0:.8f}" lon="8.0">'
        f"<ele>{100 + index}</ele>"
        f"<time>2026-05-04T{9 + (minute + index) // 3600:02d}:"
        f"{((minute * 60 + index) // 60) % 60:02d}:{index % 60:02d}Z</time>"
        f"<hdop>1.1</hdop></trkpt>"
        for index in range(count)
    )
    return RECORDING.format(points=points)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client over a throwaway archive holding one analysed recording."""
    settings = Settings(data_dir=tmp_path / "data", timezone="Europe/Berlin")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _import(client: TestClient, content: str, name: str) -> int:
    """Import one document through the canonical use case and return its track."""
    services = client.app.state.services  # type: ignore[attr-defined]
    from gpx_view.application.import_tracks import ImportRequest

    outcome = services.import_tracks(
        ImportRequest(content=content.encode("utf-8"), original_filename=name)
    )
    return outcome.track_ids[0]


# --- Per-track analysis -----------------------------------------------------


def test_a_tracks_analysis_is_its_own_resource(client: TestClient) -> None:
    """Metrics are a separate question from the track, and a separate URL."""
    track_id = _import(client, _recording(), "walk.gpx")

    response = client.get(f"/api/v1/tracks/{track_id}/analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["track_id"] == track_id
    assert body["status"] == "current"


def test_analysis_fields_carry_their_unit_in_their_name(client: TestClient) -> None:
    """`22.4` means four different things without one."""
    track_id = _import(client, _recording(), "walk.gpx")

    body = client.get(f"/api/v1/tracks/{track_id}/analysis").json()

    assert body["geometry"]["distance_m"] > 100.0
    assert body["geometry"]["elevation_gain_m"] is not None
    assert body["timed_path"]["elapsed_duration_s"] > 0.0
    assert set(body["geometry"]) >= {"distance_m", "elevation_gain_m"}
    assert set(body["timed_path"]) >= {
        "elapsed_duration_s",
        "moving_duration_s",
        "stopped_duration_s",
        "unobserved_gap_duration_s",
    }


def test_an_underivable_metric_is_null_rather_than_zero(client: TestClient) -> None:
    """A planned route has no moving time, and zero would be a claim that it does."""
    track_id = _import(client, PLANNED, "route.gpx")

    body = client.get(f"/api/v1/tracks/{track_id}/analysis").json()

    assert body["geometry"]["distance_m"] > 0.0
    assert body["timed_path"]["moving_duration_s"] is None
    assert body["timed_path"]["elapsed_duration_s"] is None


def test_the_analysis_names_the_algorithms_that_produced_it(client: TestClient) -> None:
    """Asking why a number changed has to be answerable from the response."""
    track_id = _import(client, _recording(), "walk.gpx")

    profile = client.get(f"/api/v1/tracks/{track_id}/analysis").json()["profile"]

    assert profile["distance_algorithm"]
    assert profile["movement_algorithm"]
    assert profile["elevation_algorithm"]
    assert profile["metric_schema_version"] >= 1


def test_the_analysis_reports_what_was_wrong_with_the_data(client: TestClient) -> None:
    """Quality flags are how an unexplained number stops looking like a defect."""
    track_id = _import(client, PLANNED, "route.gpx")

    body = client.get(f"/api/v1/tracks/{track_id}/analysis").json()

    assert "insufficient_temporal_data" in body["quality"]


def test_asking_for_the_analysis_of_an_unknown_track_is_a_named_error(
    client: TestClient,
) -> None:
    """One stable error envelope, with no path and no stack trace in it."""
    response = client.get("/api/v1/tracks/4242/analysis")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "track_not_found", "message": "no such track"}}


def test_the_track_list_carries_an_analysis_summary(client: TestClient) -> None:
    """A list view must not need one request per track to show a distance."""
    _import(client, _recording(), "walk.gpx")

    (track,) = client.get("/api/v1/tracks").json()["tracks"]

    assert track["analysis"]["status"] == "current"
    assert track["analysis"]["distance_m"] > 100.0


# --- Statistics -------------------------------------------------------------


def test_a_year_is_asked_for_by_scope(client: TestClient) -> None:
    """Recorded and planned are separate questions with separate answers."""
    _import(client, _recording(), "walk.gpx")
    _import(client, PLANNED, "route.gpx")

    actual = client.get("/api/v1/statistics/year/2026?scope=recorded").json()
    planned = client.get("/api/v1/statistics/year/2026?scope=planned").json()

    assert actual["scope"] == "recorded"
    assert actual["totals"]["track_count"] == 1
    assert planned["scope"] == "planned"
    assert planned["totals"]["track_count"] == 0


def test_a_year_defaults_to_what_actually_happened(client: TestClient) -> None:
    """The unqualified question is "what did I do", not "what did I add up"."""
    _import(client, _recording(), "walk.gpx")

    body = client.get("/api/v1/statistics/year/2026").json()

    assert body["scope"] == "recorded"


def test_a_statistics_response_names_its_timezone(client: TestClient) -> None:
    """Otherwise a monthly number is unexplainable to whoever reads it."""
    body = client.get("/api/v1/statistics/year/2026").json()

    assert body["timezone"] == "Europe/Berlin"


def test_a_year_reports_the_tracks_that_belong_to_no_month(client: TestClient) -> None:
    """A route with no timestamps has a length and no date, both at once.

    It is reported beside the year rather than inside it. Assigning it to the
    import date, or to 1970, would put a real distance into a period it has
    nothing to do with.
    """
    _import(client, PLANNED, "route.gpx")

    body = client.get("/api/v1/statistics/year/2026?scope=planned").json()

    assert body["unplaced"]["without_date"]["track_count"] == 1
    assert body["unplaced"]["without_date"]["distance_m"] > 0.0
    assert body["totals"]["track_count"] == 0


def test_the_monthly_view_always_returns_twelve_buckets(client: TestClient) -> None:
    """A caller never has to invent the empty months."""
    _import(client, _recording(), "walk.gpx")

    months = client.get("/api/v1/statistics/year/2026/monthly").json()["months"]

    assert [bucket["month"] for bucket in months] == list(range(1, 13))
    assert months[4]["totals"]["track_count"] == 1
    assert months[0]["totals"]["track_count"] == 0
    assert months[0]["totals"]["distance_m"] == 0.0


def test_the_monthly_totals_agree_with_the_year(client: TestClient) -> None:
    """The invariant a dashboard will rely on without checking it."""
    _import(client, _recording(), "walk.gpx")

    year = client.get("/api/v1/statistics/year/2026").json()
    months = client.get("/api/v1/statistics/year/2026/monthly").json()["months"]

    assert sum(bucket["totals"]["distance_m"] for bucket in months) == pytest.approx(
        year["totals"]["distance_m"]
    )


def test_statistics_can_be_narrowed_to_one_activity(client: TestClient) -> None:
    """Walking kilometres and cycling kilometres are not one statistic."""
    _import(client, _recording(), "walk.gpx")

    walking = client.get("/api/v1/statistics/year/2026?activity=walking").json()
    cycling = client.get("/api/v1/statistics/year/2026?activity=cycling").json()

    assert walking["totals"]["track_count"] == 1
    assert cycling["totals"]["track_count"] == 0


def test_an_unknown_scope_is_refused_rather_than_guessed(client: TestClient) -> None:
    """No query language, and no silently ignored parameter either."""
    assert client.get("/api/v1/statistics/year/2026?scope=everything").status_code == 422


def test_statistics_never_expose_a_coordinate(client: TestClient) -> None:
    """A total is a number about movement, not a channel for the movement."""
    _import(client, _recording(), "walk.gpx")

    body = client.get("/api/v1/statistics/year/2026/monthly").text

    assert "latitude" not in body
    assert "8.0" not in body
