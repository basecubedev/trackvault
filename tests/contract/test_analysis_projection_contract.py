"""The HTTP contract for how derived metrics are *presented*.

The defect this file exists to prevent is not an arithmetic one. It is a
labelling one:

```json
{"moving_duration_s": 10296, "maximum_speed_mps": 1.11}
```

Read out of a flat object, that says somebody walked for two hours and fifty
minutes. It says the same thing whether the instants behind it came from a
receiver on somebody's arm or from a routing engine that drew the line last
Tuesday. A client cannot tell, so a client will get it wrong.

The projection therefore keeps two things apart -- what the geometry supports,
which needs no clock, and what the instants support, which is worth exactly what
the instants are worth -- and never hands over the second without saying what
they were worth.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.application.import_tracks import ImportRequest
from gpx_view.config import Settings
from gpx_view.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.analysis]

_DEGREE = 111_195.0

_DOCUMENT = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>{name}</name>{type}<trkseg>
{points}
  </trkseg></trk>
</gpx>
"""


def _document(*, name: str, measured: bool, count: int = 200) -> str:
    """Build a synthetic track that walks north at 1.4 m/s for `count` seconds.

    The only difference between the two documents this makes is whether the
    positions carry receiver quality. The geometry, the instants and the
    arithmetic derivable from them are identical -- which is the point: the
    presentation has to differ on evidence, not on anything else.
    """
    quality = "<hdop>1.1</hdop>" if measured else ""
    points = "\n".join(
        f'    <trkpt lat="{index * 1.4 / _DEGREE:.8f}" lon="8.0">'
        f"<ele>{100 + index * 0.5:.1f}</ele>"
        f"<time>2026-05-04T09:{index // 60:02d}:{index % 60:02d}Z</time>"
        f"{quality}</trkpt>"
        for index in range(count)
    )
    return _DOCUMENT.format(
        name=name, type="<type>walking</type>" if measured else "", points=points
    )


MEASURED = _document(name="Measured walk", measured=True)
UNMEASURED = _document(name="Timestamps of unknown origin", measured=False)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client over a throwaway archive."""
    settings = Settings(data_dir=tmp_path / "data", timezone="Europe/Berlin")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _import(client: TestClient, content: str, name: str) -> int:
    """Import one document through the canonical use case and return its track."""
    services = client.app.state.services  # type: ignore[attr-defined]
    outcome = services.import_tracks(
        ImportRequest(content=content.encode("utf-8"), original_filename=name)
    )
    return outcome.track_ids[0]


def _analysis(client: TestClient, track_id: int) -> dict[str, object]:
    """Return one track's analysis resource."""
    response = client.get(f"/api/v1/tracks/{track_id}/analysis")
    assert response.status_code == 200
    return dict(response.json())


# --- The two halves are separate ---------------------------------------------


def test_geometry_and_timed_path_are_reported_apart(client: TestClient) -> None:
    """Two groups, because they are worth two different amounts."""
    body = _analysis(client, _import(client, MEASURED, "walk.gpx"))

    geometry = body["geometry"]
    assert isinstance(geometry, dict)
    assert set(geometry) == {
        "distance_m",
        "elevation_min_m",
        "elevation_max_m",
        "elevation_gain_m",
        "elevation_loss_m",
    }

    timed = body["timed_path"]
    assert isinstance(timed, dict)
    assert set(timed) == {
        "basis",
        "is_actual_activity_timing",
        "elapsed_duration_s",
        "moving_duration_s",
        "stopped_duration_s",
        "unobserved_gap_duration_s",
        "unattributed_duration_s",
        "average_speed_mps",
        "moving_average_speed_mps",
        "maximum_sustained_speed_mps",
    }


def test_no_duration_or_speed_is_reported_outside_its_basis(client: TestClient) -> None:
    """A timing field must be unreadable without the statement qualifying it.

    Structural rather than documentary: there is no path through this response
    that reaches a moving time without passing the basis on the way.
    """
    body = _analysis(client, _import(client, UNMEASURED, "unknown.gpx"))

    for field in ("moving_duration_s", "elapsed_duration_s", "maximum_sustained_speed_mps"):
        assert field not in body
        assert field not in body["geometry"]  # type: ignore[operator]


# --- What the basis says ------------------------------------------------------


def test_a_measuring_receiver_produces_observed_timing(client: TestClient) -> None:
    """Receiver quality is evidence that something was measuring."""
    body = _analysis(client, _import(client, MEASURED, "walk.gpx"))
    timed = body["timed_path"]
    assert isinstance(timed, dict)

    assert timed["basis"] == "observed"
    assert timed["is_actual_activity_timing"] is True
    assert timed["moving_duration_s"] > 0.0


def test_instants_without_measurement_evidence_are_not_observed(client: TestClient) -> None:
    """The whole point, over HTTP.

    The same geometry and the same instants as the measured document. Every
    number is still derived and still reported -- and the response says plainly
    that nothing showed where those instants came from.
    """
    body = _analysis(client, _import(client, UNMEASURED, "unknown.gpx"))
    timed = body["timed_path"]
    assert isinstance(timed, dict)

    assert timed["basis"] == "unknown"
    assert timed["is_actual_activity_timing"] is False
    assert timed["moving_duration_s"] > 0.0, (
        "the arithmetic is real and is not withheld; only the claim about it is"
    )


def test_the_geometry_of_an_unmeasured_track_is_worth_the_same(client: TestClient) -> None:
    """Distance and ascent need no clock, so no clock can devalue them."""
    measured = _analysis(client, _import(client, MEASURED, "walk.gpx"))["geometry"]
    unmeasured = _analysis(client, _import(client, UNMEASURED, "unknown.gpx"))["geometry"]
    assert isinstance(measured, dict)
    assert isinstance(unmeasured, dict)

    assert unmeasured["distance_m"] == pytest.approx(measured["distance_m"])
    assert unmeasured["elevation_gain_m"] == pytest.approx(measured["elevation_gain_m"])


def test_a_track_with_no_instants_still_reports_a_basis(client: TestClient) -> None:
    """There is no state in which a client has to guess what it is holding."""
    route = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <rte><name>Route</name><rtept lat="0.0" lon="9.0"/><rtept lat="0.9" lon="9.0"/></rte>
</gpx>
"""
    body = _analysis(client, _import(client, route, "route.gpx"))
    timed = body["timed_path"]
    assert isinstance(timed, dict)

    assert timed["basis"] == "unknown"
    assert timed["is_actual_activity_timing"] is False
    assert timed["moving_duration_s"] is None, "absent is not zero"


# --- Naming -------------------------------------------------------------------


def test_the_maximum_speed_is_named_for_the_window_it_is_measured_over(
    client: TestClient,
) -> None:
    """It is a sustained speed, and a three-second sprint is averaged down.

    Calling it `maximum_speed_mps` invites a reader to compare it with a bike
    computer's instantaneous peak, which it is not and never was.
    """
    body = _analysis(client, _import(client, MEASURED, "walk.gpx"))
    timed = body["timed_path"]
    assert isinstance(timed, dict)

    assert "maximum_sustained_speed_mps" in timed
    assert "maximum_speed_mps" not in timed


# --- The listing carries enough to render a row -------------------------------


def test_a_listed_track_carries_its_timing_basis(client: TestClient) -> None:
    """A list view must not have to fetch each track to know what it may say."""
    _import(client, UNMEASURED, "unknown.gpx")

    listed = client.get("/api/v1/tracks").json()["tracks"][0]

    assert listed["timeline"]["basis"] == "unknown"
    assert listed["timeline"]["is_actual_activity_timing"] is False
    assert listed["timeline"]["is_actual_calendar_time"] is False
    assert listed["analysis"]["distance_m"] > 0.0
