"""Executable contract: a timestamp is not a calendar placement.

A track carries instants. That says something wrote a time, and by itself it
says nothing about *when the activity happened* -- which is the only question a
monthly total is asking.

```
timeline time            the instants the track's own positions carry
activity calendar time   the claim "this happened in this period"
```

The second needs the first to have been *observed*. A route planner writes
plausible instants onto geometry nobody travelled; a recording stripped of its
receiver metadata looks identical. Treating either as a calendar anchor puts a
real distance into a month it has nothing to do with, and nothing about the
resulting number looks wrong.

So a period total covers the tracks whose timing was shown to be measured, and
everything else is reported *beside* the period rather than inside it -- split,
because "no date at all" and "a date nothing vouches for" are different facts.

The sharp edge this pins down is the user override. Correcting a track's kind to
`recorded` says the track is a recording. It does not say its clock is real, and
it must not silently add its distance to a year.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trackvault.application.import_tracks import ImportRequest
from trackvault.config import Settings
from trackvault.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.statistics]

_DEGREE = 111_195.0

TRACK = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>{name}</name><type>walking</type><trkseg>
{points}
  </trkseg></trk>
</gpx>
"""


def _points(*, metres: float, count: int, timed: bool, measured: bool) -> str:
    """Build positions, optionally timed and optionally with receiver metadata."""
    step = metres / (count - 1)
    rows = []
    for index in range(count):
        parts = [
            f'    <trkpt lat="{index * step / _DEGREE:.8f}" lon="8.0">',
            f"<ele>{100 + index * 0.5:.1f}</ele>",
        ]
        if timed:
            parts.append(f"<time>2025-10-19T09:{index // 60:02d}:{index % 60:02d}Z</time>")
        if measured:
            parts.append("<hdop>1.1</hdop>")
        parts.append("</trkpt>")
        rows.append("".join(parts))
    return "\n".join(rows)


def _document(name: str, *, metres: float, timed: bool, measured: bool, count: int = 120) -> bytes:
    """Build one synthetic track document."""
    points = _points(metres=metres, count=count, timed=timed, measured=measured)
    return TRACK.format(name=name, points=points).encode("utf-8")


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


def _track(client: TestClient, track_id: int) -> dict[str, object]:
    """Read one track."""
    response = client.get(f"/api/v1/tracks/{track_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _year(client: TestClient, year: int = 2025, **params: object) -> dict[str, object]:
    """Read one year's statistics."""
    response = client.get(f"/api/v1/statistics/year/{year}", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _listed(client: TestClient, **params: object) -> list[int]:
    """Return the track identities one listing selects."""
    response = client.get("/api/v1/tracks", params=params)
    assert response.status_code == 200, response.text
    return [int(row["id"]) for row in response.json()["tracks"]]


@pytest.fixture
def measured(client: TestClient) -> int:
    """A recording whose receiver said how well it was measuring."""
    return _import(
        client, _document("measured", metres=10_000.0, timed=True, measured=True), "m.gpx"
    )


@pytest.fixture
def unverified(client: TestClient) -> int:
    """Timestamps nothing vouches for: a plausible clock and no measurement."""
    return _import(client, _document("route", metres=8_900.0, timed=True, measured=False), "u.gpx")


@pytest.fixture
def undated(client: TestClient) -> int:
    """Geometry with no instants at all."""
    return _import(client, _document("bare", metres=5_000.0, timed=False, measured=False), "d.gpx")


# --- The timeline is reported, and qualified --------------------------------


def test_a_track_reports_its_timeline_with_the_basis_of_that_timeline(
    client: TestClient, unverified: int
) -> None:
    """Instants are shown. What they are worth is shown beside them."""
    timeline = _track(client, unverified)["timeline"]

    assert isinstance(timeline, dict)
    assert timeline["started_at"] is not None, "the timeline itself is not withheld"
    assert timeline["basis"] == "unknown"
    assert timeline["is_actual_calendar_time"] is False


def test_a_measured_recording_reports_a_trusted_calendar_time(
    client: TestClient, measured: int
) -> None:
    """The ordinary case keeps the ordinary answer."""
    timeline = _track(client, measured)["timeline"]

    assert isinstance(timeline, dict)
    assert timeline["basis"] == "observed"
    assert timeline["is_actual_calendar_time"] is True
    assert timeline["is_actual_activity_timing"] is True


# --- A period covers what it can vouch for ----------------------------------


def test_an_unverified_timestamp_does_not_place_a_track_in_a_month(
    client: TestClient, unverified: int
) -> None:
    """October 2025 is not where a route with a synthetic clock happened."""
    assert unverified not in _listed(client, year=2025, month=10)
    assert unverified not in _listed(client, year=2025)


def test_a_measured_recording_is_placed_in_its_month(client: TestClient, measured: int) -> None:
    """Gating the untrusted ones must not cost the trusted ones their month."""
    assert _listed(client, year=2025, month=10) == [measured]


def test_an_unverified_timestamp_contributes_to_no_year_total(
    client: TestClient, unverified: int
) -> None:
    """A distance is real. The year it is put in has to be real too."""
    assert unverified
    year = _year(client, scope="unknown")

    assert year["totals"]["track_count"] == 0, "an unverified date reached a period total"
    assert year["unplaced"]["with_unverified_date"]["track_count"] == 1  # type: ignore[index]
    assert year["unplaced"]["with_unverified_date"]["distance_m"] == pytest.approx(  # type: ignore[index]
        8_900.0, rel=0.01
    )
    assert year["unplaced"]["without_date"]["track_count"] == 0  # type: ignore[index]


def test_a_track_with_no_instants_is_reported_apart_from_one_with_untrusted_ones(
    client: TestClient, unverified: int, undated: int
) -> None:
    """No-date and dated-by-something-unverifiable are different facts."""
    assert unverified != undated

    unplaced = _year(client, scope="unknown")["unplaced"]

    assert isinstance(unplaced, dict)
    assert unplaced["without_date"]["track_count"] == 1
    assert unplaced["with_unverified_date"]["track_count"] == 1


# --- A kind correction is not a clock correction ----------------------------


def test_correcting_a_kind_does_not_make_untrusted_timestamps_trusted(
    client: TestClient, unverified: int
) -> None:
    """The override says what the track is, not what its clock is worth."""
    response = client.put(f"/api/v1/tracks/{unverified}/classification", json={"kind": "recorded"})
    assert response.status_code == 200, response.text

    body = dict(response.json())
    assert body["classification"]["effective_kind"] == "recorded"
    assert body["timeline"]["basis"] == "unknown"
    assert body["timeline"]["is_actual_calendar_time"] is False


def test_an_override_does_not_add_a_distance_to_a_recorded_year(
    client: TestClient, unverified: int
) -> None:
    """The reproduction: 8.9 km appearing in 2025 because somebody said "recorded"."""
    before = _year(client, scope="recorded")["totals"]
    assert before["track_count"] == 0  # type: ignore[index]

    client.put(f"/api/v1/tracks/{unverified}/classification", json={"kind": "recorded"})
    after = _year(client, scope="recorded")

    assert after["totals"]["track_count"] == 0, "an override placed a track in a year"  # type: ignore[index]
    assert after["totals"]["distance_m"] == 0.0  # type: ignore[index]
    assert after["unplaced"]["with_unverified_date"]["track_count"] == 1  # type: ignore[index]
    assert after["unplaced"]["with_unverified_date"]["distance_m"] == pytest.approx(  # type: ignore[index]
        8_900.0, rel=0.01
    )


def test_an_override_still_moves_a_track_between_the_scopes(
    client: TestClient, unverified: int
) -> None:
    """What the correction *is* for still works."""
    assert _year(client, scope="unknown")["unplaced"]["with_unverified_date"]["track_count"] == 1  # type: ignore[index]

    client.put(f"/api/v1/tracks/{unverified}/classification", json={"kind": "recorded"})

    assert _year(client, scope="unknown")["unplaced"]["with_unverified_date"]["track_count"] == 0  # type: ignore[index]
    assert _year(client, scope="recorded")["unplaced"]["with_unverified_date"]["track_count"] == 1  # type: ignore[index]


# --- One period authority ----------------------------------------------------


def test_a_month_bar_and_a_month_filter_select_the_same_tracks(
    client: TestClient, measured: int, unverified: int, undated: int
) -> None:
    """Clicking a bar must reach the tracks that bar counted."""
    assert measured and unverified and undated

    monthly = client.get("/api/v1/statistics/year/2025/monthly", params={"scope": "recorded"})
    assert monthly.status_code == 200, monthly.text
    october = next(bucket for bucket in monthly.json()["months"] if bucket["month"] == 10)

    listed = _listed(client, year=2025, month=10, kind="recorded")

    assert october["totals"]["track_count"] == len(listed)
    assert listed == [measured]
