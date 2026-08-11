"""HTTP contract of the track query and classification endpoints.

The API is a projection: it shapes what the use cases return and decides nothing.
The contract that matters here is the shape of the payloads, the stable error
envelope, and the fact that correcting a classification through HTTP goes through
the same authority as everywhere else.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.application.import_tracks import ImportRequest
from gpx_view.config import Settings
from gpx_view.domain import TrackClassification, classify
from gpx_view.infrastructure.assembly import build_services, import_limits_from
from gpx_view.infrastructure.gpx import GpxImporter
from gpx_view.main import create_app

pytestmark = pytest.mark.contract

_LATER = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"


@pytest.fixture
def archive(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client whose archive already holds a recording and a planned tour."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    for name in ("recorded-measurements.gpx", "route-only.gpx"):
        services.import_tracks(
            ImportRequest(content=(FIXTURES / name).read_bytes(), original_filename=name)
        )
    with TestClient(create_app(settings)) as client:
        yield client


def _recording(client: TestClient) -> dict[str, object]:
    """Return the recorded track from the archive, by what it is rather than where.

    Listing order is a contract of its own -- newest imported source first -- and
    a test that reads "the first track" would silently start checking a different
    one whenever that order or the fixture set changes.
    """
    return next(
        track
        for track in client.get("/api/v1/tracks").json()["tracks"]
        if track["title"] == "Synthetic morning walk"
    )


def _recording_id(client: TestClient) -> int:
    """Return the identity of the recorded track in the archive."""
    return int(_recording(client)["id"])


# --- Listing ----------------------------------------------------------------


def test_listing_an_empty_archive_answers_with_an_empty_list(client: TestClient) -> None:
    """No tracks is a normal answer, not an error."""
    response = client.get("/api/v1/tracks")

    assert response.status_code == 200
    assert response.json() == {"total": 0, "limit": 50, "offset": 0, "tracks": []}


def test_a_listing_carries_what_a_dashboard_needs(archive: TestClient) -> None:
    """Kind, activity, counts, extent and provenance, without any geometry."""
    payload = archive.get("/api/v1/tracks").json()

    assert payload["total"] == 2
    track = next(t for t in payload["tracks"] if t["title"] == "Synthetic morning walk")
    assert track["activity"] == "walking"
    assert track["point_count"] == 4
    assert track["segment_count"] == 1
    assert track["timeline"]["started_at"].startswith("2026-05-04T08:00:00")
    assert track["source"]["exchange_format"] == "gpx"
    assert track["source"]["format_version"] == "1.1"
    assert "segments" not in track


def test_a_listing_states_both_the_detected_and_the_effective_kind(
    archive: TestClient,
) -> None:
    """A reader must be able to tell a verdict from a correction."""
    classification = _recording(archive)["classification"]

    assert classification["detected_kind"] == "recorded"
    assert classification["effective_kind"] == "recorded"
    assert classification["override"] is None
    assert classification["is_overridden"] is False
    assert "gps_accuracy_present" in classification["evidence"]
    assert classification["method"]
    assert classification["method_version"]


def test_no_distance_or_elevation_figures_are_invented(archive: TestClient) -> None:
    """Analysis is not implemented, so the API states none."""
    track = _recording(archive)

    assert not [key for key in track if "distance" in key or "elevation_gain" in key]


# --- Reading one track ------------------------------------------------------


def test_reading_one_track_answers_the_same_shape(archive: TestClient) -> None:
    """A detail read is the same projection, not a second model."""
    track_id = _recording_id(archive)

    detail = archive.get(f"/api/v1/tracks/{track_id}").json()

    assert detail["id"] == track_id
    assert detail["classification"]["effective_kind"] == "recorded"


def test_geometry_is_a_question_of_its_own(archive: TestClient) -> None:
    """Positions are fetched deliberately, so a listing never pays for them."""
    track_id = _recording_id(archive)

    payload = archive.get(f"/api/v1/tracks/{track_id}/geometry").json()

    assert payload["track_id"] == track_id
    assert payload["segment_count"] == 1
    assert payload["point_count"] == 4
    assert len(payload["segments"][0]["points"]) == 4
    first = payload["segments"][0]["points"][0]
    assert first["latitude"] == pytest.approx(51.0)
    assert first["elevation"] == pytest.approx(40.0)


def test_segment_boundaries_survive_the_http_projection(tmp_path: Path) -> None:
    """Several segments must not be flattened into one point list on the wire."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    outcome = services.import_tracks(
        ImportRequest(content=(FIXTURES / "multiple-segments.gpx").read_bytes())
    )
    with TestClient(create_app(settings)) as fresh:
        payload = fresh.get(f"/api/v1/tracks/{outcome.track_ids[0]}/geometry").json()

    assert [len(segment["points"]) for segment in payload["segments"]] == [2, 1, 2]


# --- Stable errors ----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/api/v1/tracks/4711", "/api/v1/tracks/4711/geometry", "/api/v1/tracks/4711/classification"],
)
def test_an_unknown_track_answers_404_with_a_stable_code(archive: TestClient, path: str) -> None:
    """The error contract is a code plus a message, never an internal detail."""
    response = archive.delete(path) if path.endswith("classification") else archive.get(path)

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "track_not_found", "message": "no such track"}}


def test_an_error_carries_no_internals(archive: TestClient) -> None:
    """A 404 must not leak a path, a query or a stack trace."""
    body = archive.get("/api/v1/tracks/4711").text

    assert "Traceback" not in body
    assert "sqlite" not in body.lower()
    assert "/tmp" not in body  # noqa: S108


def test_a_nonsense_identity_is_rejected_before_the_use_case(archive: TestClient) -> None:
    """Validation happens at the edge, so the use case sees only real inputs."""
    assert archive.get("/api/v1/tracks/not-a-number").status_code == 422
    assert archive.get("/api/v1/tracks/0").status_code == 422


# --- The user override ------------------------------------------------------


def test_a_user_correction_decides_the_effective_kind(archive: TestClient) -> None:
    """`detected planned` + `override recorded` = `effective recorded`."""
    planned = next(
        track
        for track in archive.get("/api/v1/tracks").json()["tracks"]
        if track["classification"]["detected_kind"] == "planned"
    )

    response = archive.put(
        f"/api/v1/tracks/{planned['id']}/classification", json={"kind": "recorded"}
    )

    assert response.status_code == 200
    classification = response.json()["classification"]
    assert classification["detected_kind"] == "planned"
    assert classification["effective_kind"] == "recorded"
    assert classification["override"] == "recorded"
    assert classification["is_overridden"] is True


def test_a_correction_is_durable(archive: TestClient) -> None:
    """A correction is stored, not held in the response."""
    track_id = _recording_id(archive)
    archive.put(f"/api/v1/tracks/{track_id}/classification", json={"kind": "planned"})

    reread = archive.get(f"/api/v1/tracks/{track_id}").json()

    assert reread["classification"]["effective_kind"] == "planned"


def test_unknown_is_a_valid_correction(archive: TestClient) -> None:
    """A user may state that the kind cannot be decided."""
    track_id = _recording_id(archive)

    response = archive.put(f"/api/v1/tracks/{track_id}/classification", json={"kind": "unknown"})

    assert response.json()["classification"]["effective_kind"] == "unknown"


def test_a_correction_can_be_withdrawn(archive: TestClient) -> None:
    """Removing a correction hands authority back to the classifier."""
    track_id = _recording_id(archive)
    archive.put(f"/api/v1/tracks/{track_id}/classification", json={"kind": "planned"})

    response = archive.delete(f"/api/v1/tracks/{track_id}/classification")

    assert response.status_code == 200
    classification = response.json()["classification"]
    assert classification["effective_kind"] == "recorded"
    assert classification["override"] is None


def test_an_invalid_kind_is_refused(archive: TestClient) -> None:
    """The vocabulary is fixed; the API does not invent a fourth kind."""
    track_id = _recording_id(archive)

    response = archive.put(
        f"/api/v1/tracks/{track_id}/classification", json={"kind": "probably-recorded"}
    )

    assert response.status_code == 422


# --- The published surface --------------------------------------------------


def test_the_api_publishes_exactly_the_implemented_surface(client: TestClient) -> None:
    """Nothing is documented that is not implemented, and nothing is hidden."""
    paths = set(client.get("/openapi.json").json()["paths"])

    assert paths == {
        "/healthz",
        "/api/v1/system/info",
        "/api/v1/tracks",
        "/api/v1/tracks/{track_id}",
        "/api/v1/tracks/{track_id}/geometry",
        "/api/v1/tracks/{track_id}/profile",
        "/api/v1/tracks/{track_id}/analysis",
        "/api/v1/tracks/{track_id}/classification",
        "/api/v1/tracks/{track_id}/metadata",
        "/api/v1/statistics/years",
        "/api/v1/statistics/year/{year}",
        "/api/v1/statistics/year/{year}/monthly",
        "/api/v1/maps",
        "/api/v1/maps/catalog",
        "/api/v1/maps/catalog/refresh",
        "/api/v1/maps/install",
        "/api/v1/maps/regions/{region_id}",
        "/api/v1/maps/jobs",
        "/api/v1/maps/jobs/{job_id}",
        "/api/v1/maps/jobs/{job_id}/cancel",
        "/api/v1/maps/coverage",
        "/api/v1/maps/credits",
        "/api/v1/maps/tiles/{delivery_id}/{zoom}/{column}/{row}.mvt",
    }


def test_no_write_endpoint_accepts_a_file(client: TestClient) -> None:
    """Importing is not an unauthenticated upload; it is an operator action."""
    schema = client.get("/openapi.json").json()

    assert "multipart/form-data" not in schema["components"].get("requestBodies", {})
    assert not [path for path in schema["paths"] if "import" in path or "upload" in path]


def test_no_endpoint_accepts_a_url_to_fetch(client: TestClient) -> None:
    """Installing a map takes a region, never an address.

    A `url` field anywhere on this surface would make the archive a
    general-purpose fetcher operated by whoever can reach it -- the
    server-side request forgery primitive the provider port exists to prevent.
    """
    schema = client.get("/openapi.json").json()
    requested = {
        media["schema"]["$ref"].rsplit("/", 1)[-1]
        for path in schema["paths"].values()
        for operation in path.values()
        for media in operation.get("requestBody", {}).get("content", {}).values()
        if "$ref" in media.get("schema", {})
    }
    fields = {
        name
        for component in requested
        for name in schema["components"]["schemas"][component].get("properties", {})
    }

    assert requested, "the surface has request bodies; this check must see them"
    assert not [name for name in fields if name in {"url", "href", "endpoint", "host"}]


# --- The API answers from the current generation only ------------------------


def test_a_candidate_a_later_run_stopped_producing_is_gone_from_the_api(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale candidate is not a track, however long its row survives.

    Its row is kept on purpose -- that is what lets a returning candidate recover
    the user's correction -- but a reader asking for tracks must not be handed
    one that the run currently speaking for its source did not produce. Mixing
    history into the ordinary endpoint would make a 200 mean two different things.
    """
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    outcome = services.import_tracks(
        ImportRequest(content=(FIXTURES / "multiple-tracks.gpx").read_bytes())
    )
    stale_id = outcome.track_ids[1]

    # A later run of the same source that reports only its first candidate.
    (survivor,) = GpxImporter().import_tracks(
        (FIXTURES / "multiple-tracks.gpx").read_bytes(), import_limits_from(settings)
    )[:1]
    raw = services.store.find_raw_import(outcome.sha256)
    assert raw is not None
    services.store.record_import(
        raw,
        replace(services.store.latest_run(outcome.sha256), processed_at=_LATER),  # type: ignore[arg-type]
        [survivor.classified_as(TrackClassification(detected=classify(survivor.evidence)))],
    )
    capsys.readouterr()

    with TestClient(create_app(settings)) as client:
        listing = client.get("/api/v1/tracks").json()
        detail = client.get(f"/api/v1/tracks/{stale_id}")
        geometry = client.get(f"/api/v1/tracks/{stale_id}/geometry")

    assert listing["total"] == 1
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "track_not_found"
    assert geometry.status_code == 404
