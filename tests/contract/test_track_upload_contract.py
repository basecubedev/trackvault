"""The contract for offering a file to the archive over HTTP.

This endpoint is new, and it is the first one that lets a caller make the server
*write*. Everything else here reads, or edits a field on something that was
already imported. So the rules it has to keep are stated as tests rather than as
intentions:

* the bytes go through the one canonical import use case and no other path,
* the read is bounded before the body is consumed, not after,
* a file that is not a track is an outcome, not a crash and not a 500,
* a filename is display metadata and never a place on disk,
* a deployment can refuse the whole capability.

**On the exposure.** The archive has no authentication, which is why it had no
upload endpoint: an unauthenticated write is a bigger thing than an
unauthenticated read. The project owner asked for it anyway, for a deployment on
a trusted network, and `TRACKVAULT_UPLOAD_ENABLED=false` is how a deployment that
cannot make that assumption says so. That is a decision recorded in
`docs/adr/0011-web-upload.md`, not a gap nobody noticed.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from trackvault.api.tracks import _bounded_body
from trackvault.config import Settings
from trackvault.domain.raw_import import InputChannel
from trackvault.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.gpx]

RECORDING = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Uploaded walk</name><type>walking</type><trkseg>
    <trkpt lat="39.60000000" lon="2.80000000"><ele>12</ele>
      <time>2026-05-04T09:00:00Z</time><hdop>1.1</hdop></trkpt>
    <trkpt lat="39.60100000" lon="2.80100000"><ele>18</ele>
      <time>2026-05-04T09:01:00Z</time><hdop>1.1</hdop></trkpt>
    <trkpt lat="39.60200000" lon="2.80200000"><ele>24</ele>
      <time>2026-05-04T09:02:00Z</time><hdop>1.1</hdop></trkpt>
  </trkseg></trk>
</gpx>
"""

UPLOAD = "/api/v1/tracks/imports"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client over an archive whose operator switched uploads on.

    Explicitly, because the default is off. Every test below that offers a file
    is describing a deployment that opted in.
    """
    settings = Settings(
        data_dir=tmp_path / "data", web_dir=tmp_path / "no-build", upload_enabled=True
    )
    with TestClient(create_app(settings)) as opened:
        yield opened


@pytest.fixture
def refusing(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client over an archive configured the way it arrives."""
    settings = Settings(data_dir=tmp_path / "data", web_dir=tmp_path / "no-build")
    with TestClient(create_app(settings)) as opened:
        yield opened


def test_an_archive_nobody_configured_does_not_accept_uploads(refusing: TestClient) -> None:
    """The default, asserted as a security property rather than left implicit.

    Writing is the one thing an unauthenticated caller must not be able to do
    because a container happened to start. Turning it on is a statement about a
    network, and a statement nobody made is not one a default may assume on
    their behalf.
    """
    assert Settings(data_dir=Path("data")).upload_enabled is False
    assert refusing.post(UPLOAD, content=RECORDING).status_code == 403
    assert refusing.get("/api/v1/tracks").status_code == 200


def test_an_offered_file_becomes_a_track(client: TestClient) -> None:
    """The point of the whole thing."""
    response = client.post(UPLOAD, content=RECORDING, params={"filename": "walk.gpx"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "imported"
    assert len(body["track_ids"]) == 1
    assert client.get("/api/v1/tracks").json()["total"] == 1


def test_it_goes_through_the_same_pipeline_as_every_other_input(client: TestClient) -> None:
    """Not a second importer. The same use case, so the same promises hold.

    The clearest observable of that is the duplicate rule: content identity is
    decided by the pipeline, not by the channel, so the same bytes offered twice
    are one raw import however they arrived.
    """
    first = client.post(UPLOAD, content=RECORDING, params={"filename": "walk.gpx"}).json()
    second = client.post(UPLOAD, content=RECORDING, params={"filename": "again.gpx"}).json()

    assert first["status"] == "imported"
    assert second["status"] == "duplicate"
    assert second["sha256"] == first["sha256"]
    assert client.get("/api/v1/tracks").json()["total"] == 1


def test_the_channel_the_bytes_arrived_through_is_recorded(client: TestClient) -> None:
    """Metadata, never authority -- and metadata somebody will want to read."""
    sha = client.post(UPLOAD, content=RECORDING).json()["sha256"]

    services = client.app.state.services  # type: ignore[attr-defined]
    raw = services.store.find_raw_import(sha)

    assert raw is not None
    assert raw.input_channel is InputChannel.WEB_UPLOAD


def test_a_file_that_is_not_a_track_is_an_outcome_rather_than_a_crash(
    client: TestClient,
) -> None:
    """One unreadable file must not look like a broken server."""
    response = client.post(UPLOAD, content=b"this is not a gpx document")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"]
    assert client.get("/api/v1/tracks").json()["total"] == 0


def test_an_oversized_upload_is_refused_by_name(client: TestClient) -> None:
    """A limit that stores the file first is not a limit."""
    settings = client.app.state.services.settings  # type: ignore[attr-defined]

    response = client.post(UPLOAD, content=b"x" * (settings.import_max_bytes + 1))

    assert response.status_code == 413
    assert response.json()["detail"]["error"]["code"] == "import_too_large"
    assert client.get("/api/v1/tracks").json()["total"] == 0


def test_the_read_stops_at_the_limit_rather_than_after_the_body() -> None:
    """The bound is on the read, which is the only place it saves anything.

    Refusing an oversized file *after* loading all of it has already paid the
    cost the limit exists to prevent -- and on an endpoint anybody on the
    network can call, that cost is the whole point.

    Checked against the reader rather than through the test client: httpx
    buffers a request body before it reaches the application, so a test driven
    from there would prove the harness rather than the server.
    """
    chunk = b"x" * 1024
    offered = 0

    async def stream() -> AsyncIterator[bytes]:
        nonlocal offered
        for _ in range(1000):
            offered += len(chunk)
            yield chunk

    request = SimpleNamespace(headers={}, stream=stream)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_bounded_body(cast(Request, request), limit=4 * 1024))

    assert raised.value.status_code == 413
    # Five chunks read, not a thousand: it stopped one chunk past the ceiling.
    assert offered <= 5 * len(chunk)


def test_a_filename_is_display_metadata_and_never_a_place_on_disk(
    client: TestClient, tmp_path: Path
) -> None:
    """The one input on this endpoint that looks like a path."""
    response = client.post(
        UPLOAD, content=RECORDING, params={"filename": "../../../etc/passwd.gpx"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "imported"
    # Nothing outside the managed area was touched, and the stored name carries
    # no directory: the archive derives every path from the content hash.
    services = client.app.state.services  # type: ignore[attr-defined]
    raw = services.store.find_raw_import(response.json()["sha256"])
    assert raw is not None
    assert raw.original_filename is None or "/" not in raw.original_filename
    assert not (tmp_path / "etc").exists()


def test_an_empty_body_is_refused_rather_than_stored(client: TestClient) -> None:
    """Zero bytes are not a track, and they are not an import either."""
    response = client.post(UPLOAD, content=b"")

    assert response.status_code == 400
    assert client.get("/api/v1/tracks").json()["total"] == 0


def test_a_deployment_can_refuse_uploads_entirely(refusing: TestClient) -> None:
    """The setting exists because the exposure is real.

    An archive on a network its operator does not fully trust says no here, and
    keeps every read the interface offers.
    """
    response = refusing.post(UPLOAD, content=RECORDING)

    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "upload_disabled"
    assert refusing.get("/api/v1/tracks").status_code == 200


def test_the_interface_can_tell_whether_uploads_are_offered(
    client: TestClient, refusing: TestClient
) -> None:
    """A page that shows a control the server refuses is a page that lies."""
    assert client.get("/api/v1/system/info").json()["upload_enabled"] is True
    assert refusing.get("/api/v1/system/info").json()["upload_enabled"] is False


def test_no_upload_response_ever_names_a_place_on_disk(client: TestClient) -> None:
    """The same rule every other response here keeps."""
    body = client.post(UPLOAD, content=b"not a track", params={"filename": "x.gpx"}).text

    assert "/data" not in body
    assert "raw" not in body.lower() or "sha256" in body


# --- One ride, several files -------------------------------------------------


ROUTE_EXPORT = (
    RECORDING.replace(b"<trk>", b"<rte>")
    .replace(b"</trk>", b"</rte>")
    .replace(b"<trkseg>", b"")
    .replace(b"</trkseg>", b"")
    .replace(b"<trkpt", b"<rtept")
    .replace(b"</trkpt>", b"</rtept>")
)


def test_the_same_ride_in_two_formats_is_two_imports_that_say_so(client: TestClient) -> None:
    """The case this exists for: one afternoon, exported twice.

    Both files are kept. They are different bytes and different evidence -- one
    format carries readings the other cannot -- so refusing the second would
    throw away the richer one whenever it arrived last. What changes is that
    each row now says the other is the same recording.
    """
    first = client.post(UPLOAD, content=RECORDING, params={"filename": "ride.gpx"}).json()
    second = client.post(UPLOAD, content=ROUTE_EXPORT, params={"filename": "route.gpx"}).json()

    assert first["sha256"] != second["sha256"]
    tracks = {track["id"]: track for track in client.get("/api/v1/tracks").json()["tracks"]}
    assert len(tracks) == 2
    one, other = sorted(tracks)
    assert tracks[one]["same_recording_ids"] == [other]
    assert tracks[other]["same_recording_ids"] == [one]


def test_a_track_nothing_matches_says_nothing(client: TestClient) -> None:
    """A single import is not "one of one". It is a track."""
    client.post(UPLOAD, content=RECORDING)

    (track,) = client.get("/api/v1/tracks").json()["tracks"]

    assert track["same_recording_ids"] == []
