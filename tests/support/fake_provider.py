"""A map provider that runs on loopback, so no test needs the internet.

Two things live here.

`serve_packages` starts a real HTTP server. The transfer under test opens a real
socket, reads a real chunked body and follows a real redirect, which is the only
way to find out whether the streaming, hashing, ceiling and redirect rules
actually hold. It can be told to misbehave in the specific ways a provider might:
declare a length it does not deliver, deliver more than it declared, hang up
mid-body, redirect somewhere else, or answer with something that is not a
package at all.

`FakeMapProvider` is the port implementation the install pipeline tests use. It
reads a package off disk. The pipeline does not care where bytes come from, and
making every install test pay for a socket would make them slow and flaky for no
extra coverage -- the socket is exercised where it matters, in the transfer
tests.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO

from gpx_view.application.maps import (
    CatalogRegion,
    MapErrorCode,
    MapOperationError,
    RemotePackage,
    TransferOutcome,
)
from gpx_view.domain.maps import MapBounds, MapRegionId

CHUNK = 64 * 1024


@dataclass(slots=True)
class ServedFile:
    """One response the fake server is prepared to give.

    Attributes:
        body: What it sends.
        declared_length: What the `Content-Length` header claims. ``None`` sends
            no header at all, which is the case a size-unknown download has to
            survive.
        truncate_after: Stop writing and close the socket after this many bytes,
            which is what a dropped connection looks like from the client.
        redirect_to: Answer `302` to this address instead of sending a body.
        status: The status code, for the not-found and server-error cases.
    """

    body: bytes = b""
    declared_length: int | None = None
    truncate_after: int | None = None
    redirect_to: str | None = None
    status: int = 200


@dataclass(slots=True)
class FakeServer:
    """A running loopback server and the routes it answers."""

    host: str
    port: int
    routes: dict[str, ServedFile]
    requests: list[str] = field(default_factory=list)

    @property
    def base(self) -> str:
        """Return the base URL, always plain HTTP on loopback."""
        return f"http://{self.host}:{self.port}"

    def url(self, path: str) -> str:
        """Return the address of one route."""
        return f"{self.base}{path}"


@contextmanager
def serve_packages(routes: dict[str, ServedFile]) -> Iterator[FakeServer]:
    """Run a loopback HTTP server for the length of a test."""
    state = FakeServer(host="127.0.0.1", port=0, routes=routes)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_: object) -> None:
            """Keep the test output readable."""

        def _served(self) -> ServedFile | None:
            state.requests.append(f"{self.command} {self.path}")
            return state.routes.get(self.path)

        def _respond(self, *, body: bool) -> None:
            served = self._served()
            if served is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if served.redirect_to is not None:
                self.send_response(302)
                self.send_header("Location", served.redirect_to)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(served.status)
            if served.declared_length is not None:
                self.send_header("Content-Length", str(served.declared_length))
            self.send_header("Last-Modified", "Sat, 08 Aug 2026 02:40:56 GMT")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            if not body:
                return
            payload = served.body
            if served.truncate_after is not None:
                payload = payload[: served.truncate_after]
            for start in range(0, len(payload), CHUNK):
                self.wfile.write(payload[start : start + CHUNK])
            if served.truncate_after is not None:
                self.close_connection = True

        def do_GET(self) -> None:
            """Answer a body request."""
            self._respond(body=True)

        def do_HEAD(self) -> None:
            """Answer a metadata request."""
            self._respond(body=False)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def served(body: bytes, *, declared: bool = True) -> ServedFile:
    """Return a well-behaved response carrying ``body``."""
    return ServedFile(body=body, declared_length=len(body) if declared else None)


class FakeMapProvider:
    """A `MapPackageProvider` that serves packages from disk.

    Args:
        regions: The catalog it offers.
        packages: Which region identities have a package, and where its bytes
            are. A region absent from this mapping resolves to ``None``, which
            is how "the provider offers no package for this region" is tested.
        slug: The catalog slug, matching the region identities.
    """

    def __init__(
        self,
        *,
        regions: tuple[CatalogRegion, ...],
        packages: dict[str, Path],
        slug: str = "fake",
        display_name: str = "Fixture Provider",
    ) -> None:
        """Build the fake provider."""
        self._regions = regions
        self._packages = packages
        self._slug = slug
        self._display_name = display_name
        self.unavailable = False
        self.downloads = 0
        self.on_chunk: Callable[[int], None] | None = None

    def publish(self, region_id: str, path: Path) -> None:
        """Make a package available for a region, or replace the one there."""
        self._packages[region_id] = path

    @property
    def slug(self) -> str:
        """Return the catalog slug."""
        return self._slug

    @property
    def display_name(self) -> str:
        """Return the provider's display name."""
        return self._display_name

    def regions(self) -> tuple[CatalogRegion, ...]:
        """Return the catalog, or fail as an unreachable provider would."""
        self._require_reachable()
        return self._regions

    def resolve(self, region_id: MapRegionId) -> RemotePackage | None:
        """Return what would be downloaded for a region."""
        self._require_reachable()
        path = self._packages.get(str(region_id))
        if path is None or not path.is_file():
            return None
        return RemotePackage(
            region_id=region_id,
            url=f"https://provider.invalid/{region_id.path}.mbtiles",
            size_bytes=path.stat().st_size,
            updated_at=datetime(2026, 8, 8, 2, 40, 56, tzinfo=UTC),
        )

    def download(
        self,
        package: RemotePackage,
        sink: BinaryIO,
        *,
        max_bytes: int,
        on_progress: Callable[[int], None],
        is_cancelled: Callable[[], bool],
    ) -> TransferOutcome:
        """Stream a package from disk exactly as the real transfer would."""
        self._require_reachable()
        self.downloads += 1
        path = self._packages[str(package.region_id)]
        digest = hashlib.sha256()
        received = 0
        with path.open("rb") as handle:
            while chunk := handle.read(8192):
                if is_cancelled():
                    raise MapOperationError(MapErrorCode.MAP_DOWNLOAD_FAILED, "cancelled")
                received += len(chunk)
                if received > max_bytes:
                    raise MapOperationError(MapErrorCode.MAP_DOWNLOAD_TOO_LARGE, "mid-stream")
                digest.update(chunk)
                sink.write(chunk)
                on_progress(received)
                if self.on_chunk is not None:
                    self.on_chunk(received)
        return TransferOutcome(content_sha256=digest.hexdigest(), size_bytes=received)

    def _require_reachable(self) -> None:
        """Fail the way a provider whose DNS is gone fails."""
        if self.unavailable:
            raise MapOperationError(MapErrorCode.MAP_PROVIDER_UNAVAILABLE)


def region(
    identity: str,
    name: str,
    parent: str | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    country: str | None = None,
) -> CatalogRegion:
    """Return one catalog region, for building a fixture catalog readably.

    ``bounds`` is west, south, east, north -- what the provider's index says the
    region occupies, and the only thing that lets the archive answer "which map
    does this track need".
    """
    return CatalogRegion(
        region_id=MapRegionId.parse(identity),
        name=name,
        parent_id=None if parent is None else MapRegionId.parse(parent),
        country_code=country,
        bounds=None
        if bounds is None
        else MapBounds(
            min_longitude=bounds[0],
            min_latitude=bounds[1],
            max_longitude=bounds[2],
            max_latitude=bounds[3],
        ),
    )
