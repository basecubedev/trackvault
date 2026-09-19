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
from typing import cast

import pytest
from fastapi.testclient import TestClient

from trackvault.application.import_tracks import ImportRequest
from trackvault.config import Settings
from trackvault.domain import (
    TrackClassification,
    TrackPoint,
    TrackSegment,
    classify,
    recording_fingerprint,
    shape_fingerprint,
)
from trackvault.infrastructure.assembly import build_services, import_limits_from
from trackvault.infrastructure.gpx import GpxImporter
from trackvault.main import create_app

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


# --- Geometry identity ------------------------------------------------------
#
# Two identities, because there are two questions and one value cannot answer
# both. A track says which *recording* it is, which is what groups two exports
# of one afternoon. A geometry response says which *line* it draws, which is
# what anything holding a picture of it needs. They disagree exactly where a
# route export flattened a paused recording, and that disagreement is the point.
#
# Both are published rather than recomputed by a client: an identity invented in
# a browser would be a second answer to a question the archive already answers.


_RUN_POSITIONS = ((51.0, 7.0), (51.1, 7.1), (51.2, 7.2), (51.3, 7.3))


def _two_runs(first: int, second: int) -> bytes:
    """Return a GPX whose four positions are split into two runs of the given sizes.

    The same ground, walked once and written down twice with the pause in a
    different place. Nothing else about the two documents differs.
    """
    assert first + second == len(_RUN_POSITIONS)
    runs = (_RUN_POSITIONS[:first], _RUN_POSITIONS[first:])
    segments = "".join(
        "<trkseg>"
        + "".join(f'<trkpt lat="{lat}" lon="{lon}"><ele>10</ele></trkpt>' for lat, lon in run)
        + "</trkseg>"
        for run in runs
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="contract" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>Two runs</name>{segments}</trk></gpx>"
    ).encode()


def _segments_of(payload: dict[str, object]) -> list[TrackSegment]:
    """Rebuild the normalized segments a geometry response describes."""
    segments = cast(list[dict[str, list[dict[str, object]]]], payload["segments"])
    return [
        TrackSegment(
            points=tuple(
                TrackPoint(
                    latitude=float(cast(float, point["latitude"])),
                    longitude=float(cast(float, point["longitude"])),
                    time=(
                        None
                        if point["time"] is None
                        else datetime.fromisoformat(cast(str, point["time"]))
                    ),
                )
                for point in segment["points"]
            )
        )
        for segment in segments
    ]


def test_a_track_states_the_identity_of_the_geometry_it_currently_has(
    archive: TestClient,
) -> None:
    """Sixty-four lowercase hex digits, and the same in a listing as in a detail read."""
    listed = _recording(archive)
    identity = listed["geometry_sha256"]

    assert isinstance(identity, str)
    assert len(identity) == 64
    assert identity == identity.lower()
    assert set(identity) <= set("0123456789abcdef")
    assert archive.get(f"/api/v1/tracks/{listed['id']}").json()["geometry_sha256"] == identity


def test_the_published_identity_is_the_identity_of_the_current_geometry(
    archive: TestClient,
) -> None:
    """Not a stored label beside the shape: a projection *of* the shape.

    Rebuilt here from the canonical geometry the same API answers with, so a
    reprocessing that changes a single position changes this value with it. That
    is the whole reason a client may key derived state on it.
    """
    track_id = _recording_id(archive)

    geometry = archive.get(f"/api/v1/tracks/{track_id}/geometry").json()
    listed = archive.get(f"/api/v1/tracks/{track_id}").json()

    assert listed["geometry_sha256"] == recording_fingerprint(_segments_of(geometry))


def test_two_tracks_of_different_geometry_have_different_identities(
    archive: TestClient,
) -> None:
    """It describes the geometry, never the row it is stored in."""
    tracks = archive.get("/api/v1/tracks").json()["tracks"]

    identities = {track["geometry_sha256"] for track in tracks}

    assert len(identities) == len(tracks) == 2


def test_the_identity_follows_the_geometry_through_a_reprocess(tmp_path: Path) -> None:
    """It is republished from what the new generation holds, not carried over.

    The distinction only shows when a reprocess changes something, and by then
    a stale label would already have been handed to a client as this track's
    current shape. What is checked here is the mechanism: after a regeneration
    the published identity is still the identity of the geometry the archive now
    answers with, whatever that turned out to be.
    """
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    outcome = services.import_tracks(
        ImportRequest(content=(FIXTURES / "recorded-measurements.gpx").read_bytes())
    )

    services.reprocess(outcome.sha256)

    track_id = outcome.track_ids[0]
    with TestClient(create_app(settings)) as fresh:
        geometry = fresh.get(f"/api/v1/tracks/{track_id}/geometry").json()
        track = fresh.get(f"/api/v1/tracks/{track_id}").json()
    assert track["geometry_sha256"] == recording_fingerprint(_segments_of(geometry))


def test_a_geometry_response_states_the_identity_of_the_line_it_draws(
    archive: TestClient,
) -> None:
    """Sixty-four lowercase hex digits, over exactly the shape that was answered."""
    track_id = _recording_id(archive)

    payload = archive.get(f"/api/v1/tracks/{track_id}/geometry").json()

    identity = payload["shape_sha256"]
    assert isinstance(identity, str)
    assert len(identity) == 64
    assert identity == identity.lower()
    assert identity == shape_fingerprint(_segments_of(payload))


def test_the_same_positions_split_differently_are_a_different_line(tmp_path: Path) -> None:
    """The defect this contract exists for.

    Two documents with the same positions and the same number of runs, split a
    position apart. They are drawn as different pairs of lines, so anything
    keyed on what they look like has to be able to tell them apart. The
    *recording* identity says they are one ride -- correctly -- which is exactly
    why it cannot be the identity of a drawing.
    """
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    early = services.import_tracks(
        ImportRequest(content=_two_runs(2, 2), original_filename="break-late.gpx")
    )
    late = services.import_tracks(
        ImportRequest(content=_two_runs(1, 3), original_filename="break-early.gpx")
    )

    with TestClient(create_app(settings)) as client:
        one = client.get(f"/api/v1/tracks/{early.track_ids[0]}/geometry").json()
        other = client.get(f"/api/v1/tracks/{late.track_ids[0]}/geometry").json()
        tracks = {track["id"]: track for track in client.get("/api/v1/tracks").json()["tracks"]}

    assert [len(segment["points"]) for segment in one["segments"]] == [2, 2]
    assert [len(segment["points"]) for segment in other["segments"]] == [1, 3]
    assert one["shape_sha256"] != other["shape_sha256"]
    # ...and the recording identity, correctly, does not distinguish them.
    assert (
        tracks[early.track_ids[0]]["geometry_sha256"]
        == tracks[late.track_ids[0]]["geometry_sha256"]
    )


def test_a_reduced_shape_is_identified_as_the_reduction_it_is(archive: TestClient) -> None:
    """The identity describes the answer, never the archive behind it.

    A client holding four positions and a client holding two are looking at two
    different lines. An identity that named the track would tell them they hold
    the same thing.
    """
    track_id = _recording_id(archive)

    canonical = archive.get(f"/api/v1/tracks/{track_id}/geometry").json()
    reduced = archive.get(f"/api/v1/tracks/{track_id}/geometry?max_points=2").json()

    assert canonical["simplified"] is False
    assert reduced["simplified"] is True
    assert reduced["shape_sha256"] != canonical["shape_sha256"]
    assert reduced["shape_sha256"] == shape_fingerprint(_segments_of(reduced))


def test_asking_for_the_same_shape_twice_gives_the_same_identity(archive: TestClient) -> None:
    """The reuse half: nothing about a request's timing may reach the value."""
    track_id = _recording_id(archive)
    query = f"/api/v1/tracks/{track_id}/geometry?max_points=3"

    first = archive.get(query).json()
    second = archive.get(query).json()

    assert first["shape_sha256"] == second["shape_sha256"]


def test_correcting_what_a_user_says_leaves_the_geometry_identity_alone(
    archive: TestClient,
) -> None:
    """A renamed track is the same track, drawn the same way.

    The point of publishing this at all: state derived from the *shape* must not
    be thrown away because somebody typed a title. Only the geometry decides it.
    """
    track_id = _recording_id(archive)
    before = archive.get(f"/api/v1/tracks/{track_id}").json()["geometry_sha256"]

    archive.patch(f"/api/v1/tracks/{track_id}/metadata", json={"title": "A better name"})
    archive.put(f"/api/v1/tracks/{track_id}/classification", json={"kind": "planned"})

    assert archive.get(f"/api/v1/tracks/{track_id}").json()["geometry_sha256"] == before


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
        "/api/v1/tracks/imports",
        "/api/v1/tracks/imports/automatic",
        "/api/v1/tracks/{track_id}",
        "/api/v1/tracks/{track_id}/geometry",
        "/api/v1/tracks/{track_id}/profile",
        "/api/v1/tracks/{track_id}/analysis",
        "/api/v1/tracks/{track_id}/classification",
        "/api/v1/tracks/{track_id}/metadata",
        "/api/v1/statistics/overall",
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
        "/api/v1/layouts/track-detail",
    }


def test_the_one_write_endpoint_that_accepts_a_file_is_the_import(client: TestClient) -> None:
    """Exactly one endpoint takes bytes, and it is the canonical import.

    This test used to assert that *no* endpoint accepted a file. The archive's
    owner asked for uploads and that decision is recorded in
    `docs/adr/0011-web-upload.md`; what remains worth pinning is that adding
    one did not quietly grow a second. An upload path that is not the import
    use case is the failure this now guards against.
    """
    schema = client.get("/openapi.json").json()

    accepting = [
        path
        for path, operations in schema["paths"].items()
        if any("requestBody" in operation for operation in operations.values())
        and ("import" in path or "upload" in path)
    ]
    assert accepting == ["/api/v1/tracks/imports"]
    # And it takes a body of bytes rather than a form: no multipart parser is
    # in this dependency tree, and a filename never arrives as one.
    assert "multipart/form-data" not in client.get("/openapi.json").text


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
