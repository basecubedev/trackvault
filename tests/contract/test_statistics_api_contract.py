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

from trackvault.config import Settings
from trackvault.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.statistics]

RECORDING = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Synthetic {kind}</name><type>{kind}</type><trkseg>
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


def _recording(count: int = 120, *, minute: int = 0, kind: str = "walking") -> str:
    """Build a synthetic recording that analyses to real numbers.

    ``kind`` is the activity the document declares. It is stated by the source
    rather than inferred, which is the only way this archive ever assigns one.
    """
    points = "\n".join(
        f'    <trkpt lat="{index * 1.4 / 111195.0:.8f}" lon="8.0">'
        f"<ele>{100 + index}</ele>"
        f"<time>2026-05-04T{9 + (minute + index) // 3600:02d}:"
        f"{((minute * 60 + index) // 60) % 60:02d}:{index % 60:02d}Z</time>"
        f"<hdop>1.1</hdop></trkpt>"
        for index in range(count)
    )
    return RECORDING.format(points=points, kind=kind)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client over a throwaway archive holding one analysed recording."""
    settings = Settings(data_dir=tmp_path / "data", timezone="Europe/Berlin")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _import(client: TestClient, content: str, name: str) -> int:
    """Import one document through the canonical use case and return its track."""
    services = client.app.state.services  # type: ignore[attr-defined]
    from trackvault.application.import_tracks import ImportRequest

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


# --- What each activity did in a month ---------------------------------------
#
# A month's total answers "how far", and a reader looking at it usually wants
# "how far doing what". The rows are already there, so the month says it --
# without ever becoming a second authority on the month's own total.


def test_a_month_says_what_each_activity_did_in_it(client: TestClient) -> None:
    """The question a monthly total on its own cannot answer."""
    _import(client, _recording(), "walk.gpx")
    _import(client, _recording(kind="cycling"), "ride.gpx")

    may = client.get("/api/v1/statistics/year/2026/monthly").json()["months"][4]

    assert {entry["activity"]: entry["totals"]["track_count"] for entry in may["by_activity"]} == {
        "walking": 1,
        "cycling": 1,
    }


def test_the_breakdown_partitions_the_month_exactly(client: TestClient) -> None:
    """A track has one activity, so nothing is counted twice and nothing dropped.

    The month's own total stays the authority. This is the same rows grouped a
    second way, and a breakdown that did not add up would make one of the two
    numbers a guess.
    """
    _import(client, _recording(), "walk.gpx")
    _import(client, _recording(kind="cycling"), "ride.gpx")
    _import(client, _recording(kind="hiking"), "hike.gpx")

    may = client.get("/api/v1/statistics/year/2026/monthly").json()["months"][4]

    assert (
        sum(entry["totals"]["track_count"] for entry in may["by_activity"])
        == (may["totals"]["track_count"])
    )
    assert sum(entry["totals"]["distance_m"] for entry in may["by_activity"]) == pytest.approx(
        may["totals"]["distance_m"]
    )


def test_a_year_names_the_activities_it_actually_holds(client: TestClient) -> None:
    """A legend of eight for an archive of two is a claim about the archive."""
    _import(client, _recording(), "walk.gpx")
    _import(client, _recording(kind="cycling"), "ride.gpx")

    payload = client.get("/api/v1/statistics/year/2026/monthly").json()

    assert payload["activities"] == ["walking", "cycling"]


def test_the_activities_come_out_in_the_taxonomys_own_order(client: TestClient) -> None:
    """Ordering by size would repaint a chart whenever somebody imported a file.

    The order is the domain's, so an activity keeps its place -- and therefore
    its colour -- however much of it the archive holds.
    """
    _import(client, _recording(kind="cycling"), "ride.gpx")
    _import(client, _recording(kind="cycling"), "ride-two.gpx")
    _import(client, _recording(), "walk.gpx")

    payload = client.get("/api/v1/statistics/year/2026/monthly").json()

    assert payload["activities"] == ["walking", "cycling"]


def test_an_activity_that_did_nothing_in_a_month_is_absent_from_it(client: TestClient) -> None:
    """Absent rather than zero: no cycling in March is not a March of no kilometres."""
    _import(client, _recording(), "walk.gpx")

    payload = client.get("/api/v1/statistics/year/2026/monthly").json()

    assert payload["months"][2]["by_activity"] == []
    assert [entry["activity"] for entry in payload["months"][4]["by_activity"]] == ["walking"]


def test_a_narrowed_request_breaks_down_only_what_it_asked_for(client: TestClient) -> None:
    """The filter is the question; the breakdown answers inside it."""
    _import(client, _recording(), "walk.gpx")
    _import(client, _recording(kind="cycling"), "ride.gpx")

    payload = client.get("/api/v1/statistics/year/2026/monthly?activity=cycling").json()

    assert payload["activities"] == ["cycling"]
    assert [entry["activity"] for entry in payload["months"][4]["by_activity"]] == ["cycling"]


def test_a_breakdown_never_mixes_two_scopes(client: TestClient) -> None:
    """Planned routes are not a quieter kind of recorded ones."""
    _import(client, _recording(), "walk.gpx")
    _import(client, PLANNED, "route.gpx")

    recorded = client.get("/api/v1/statistics/year/2026/monthly").json()

    assert recorded["activities"] == ["walking"]


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


# --- The whole archive at once ------------------------------------------------
#
# A year is a period. "Everything" is not one, and the difference shows up in
# what the answer is shaped like: a year has months inside it, and everything
# has years. What does not change is who decides -- the same window arithmetic,
# the same currency rule, the same refusal to date a track nothing measured.


def test_the_whole_archive_can_be_totalled_at_once(client: TestClient) -> None:
    """A reader with three years of tracks has a question a year cannot answer."""
    _import(client, _recording(), "walk.gpx")
    _import(client, _recording(kind="cycling"), "ride.gpx")

    payload = client.get("/api/v1/statistics/overall").json()

    assert payload["totals"]["track_count"] == 2
    assert payload["totals"]["distance_m"] > 0.0


def test_the_overall_total_is_the_years_added_up(client: TestClient) -> None:
    """The invariant a dashboard will rely on without checking it."""
    _import(client, _recording(), "walk.gpx")
    _import(client, _recording(kind="cycling"), "ride.gpx")

    payload = client.get("/api/v1/statistics/overall").json()

    assert sum(bucket["totals"]["distance_m"] for bucket in payload["years"]) == pytest.approx(
        payload["totals"]["distance_m"]
    )


def test_it_holds_one_bucket_per_year_the_archive_has_something_for(
    client: TestClient,
) -> None:
    """Twelve months are always twelve. Years are however many there are.

    A month is a slot in a calendar and an empty one is a fact about the year.
    A year the archive holds nothing for is not a fact about anything -- padding
    the axis out to the supported calendar would draw a hundred empty bars.
    """
    _import(client, _recording(), "walk.gpx")

    payload = client.get("/api/v1/statistics/overall").json()

    assert [bucket["year"] for bucket in payload["years"]] == [2026]


def test_the_years_come_out_oldest_first(client: TestClient) -> None:
    """A time axis reads left to right, and this is what a chart plots."""
    _import(client, _recording(), "walk.gpx")

    payload = client.get("/api/v1/statistics/overall").json()

    assert payload["years"] == sorted(payload["years"], key=lambda bucket: bucket["year"])


def test_a_year_of_the_whole_archive_splits_by_activity_too(client: TestClient) -> None:
    """The same partition the months carry, one level up."""
    _import(client, _recording(), "walk.gpx")
    _import(client, _recording(kind="cycling"), "ride.gpx")

    bucket = client.get("/api/v1/statistics/overall").json()["years"][0]

    counts = {entry["activity"]: entry["totals"]["track_count"] for entry in bucket["by_activity"]}
    assert counts == {"walking": 1, "cycling": 1}


def test_the_whole_archive_names_the_activities_it_holds(client: TestClient) -> None:
    """So a chart of every year keeps one colour per activity."""
    _import(client, _recording(kind="cycling"), "ride.gpx")
    _import(client, _recording(), "walk.gpx")

    payload = client.get("/api/v1/statistics/overall").json()

    assert payload["activities"] == ["walking", "cycling"]


def test_it_reports_what_belongs_to_no_year_at_all(client: TestClient) -> None:
    """A route with no clock is not in "everything" either. It is beside it."""
    _import(client, _recording(), "walk.gpx")
    _import(client, PLANNED, "route.gpx")

    payload = client.get("/api/v1/statistics/overall?scope=planned").json()

    assert payload["totals"]["track_count"] == 0
    assert payload["unplaced"]["without_date"]["track_count"] == 1
    assert payload["unplaced"]["without_date"]["distance_m"] > 0.0


def test_the_whole_archive_is_still_asked_for_by_scope(client: TestClient) -> None:
    """Widening the period does not create a combined total.

    Everything means everything *in one scope*. A number that added planned
    routes to travelled distance would be about neither, however wide the
    period it covered.
    """
    _import(client, _recording(), "walk.gpx")

    recorded = client.get("/api/v1/statistics/overall?scope=recorded").json()
    planned = client.get("/api/v1/statistics/overall?scope=planned").json()

    assert recorded["totals"]["track_count"] == 1
    assert planned["totals"]["track_count"] == 0


def test_the_whole_archive_can_be_narrowed_to_one_activity(client: TestClient) -> None:
    """The filter is the question, whatever the period is."""
    _import(client, _recording(), "walk.gpx")
    _import(client, _recording(kind="cycling"), "ride.gpx")

    payload = client.get("/api/v1/statistics/overall?activity=cycling").json()

    assert payload["totals"]["track_count"] == 1
    assert payload["activities"] == ["cycling"]


def test_an_empty_archive_totals_zero_years_rather_than_failing(client: TestClient) -> None:
    """The first thing a fresh installation asks for."""
    payload = client.get("/api/v1/statistics/overall").json()

    assert payload["years"] == []
    assert payload["totals"]["track_count"] == 0
    assert payload["timezone"] == "Europe/Berlin"
