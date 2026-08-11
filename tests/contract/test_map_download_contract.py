"""What the one outbound request this application makes may and may not do.

A download is the only place TrackVault opens a connection to the internet, and
the failure modes are not theoretical: a caller choosing the host, a redirect
leaving the provider, an unbounded body filling a disk. Each of those is a
contract here, exercised against a real socket on loopback rather than a mock,
because the interesting behaviour lives in `urllib`'s redirect machinery and in
what a chunked read actually does when a server hangs up.

The loopback server speaks plain HTTP. Production speaks HTTPS and nothing else
-- `test_the_provider_only_ever_speaks_https` is what holds that -- and the
alternative would have been a private key committed to this repository to test
a code path that does not care about TLS.
"""

from __future__ import annotations

import io
from collections.abc import Callable

import pytest

from support.fake_provider import ServedFile, serve_packages, served
from trackvault.application.maps import MapErrorCode, MapOperationError
from trackvault.infrastructure.maps import ALLOWED_HOSTS, GeofabrikMapProvider, HttpTransfer
from trackvault.infrastructure.maps.transfer import SECURE_SCHEMES, user_agent

pytestmark = [pytest.mark.contract, pytest.mark.maps]

LOOPBACK = frozenset({"127.0.0.1"})
PLAIN = frozenset({"http"})
PACKAGE = b"a package worth of bytes" * 4096


def _transfer(hosts: frozenset[str] = LOOPBACK) -> HttpTransfer:
    """Return a transfer bound to loopback, speaking plain HTTP for the test."""
    return HttpTransfer(allowed_hosts=hosts, agent=user_agent("0.0.0-test"), allowed_schemes=PLAIN)


def _nothing(_: int) -> None:
    """Ignore progress."""


def _never() -> bool:
    """Never cancel."""
    return False


# --- Who may be contacted ----------------------------------------------------


def test_the_provider_only_ever_speaks_https() -> None:
    """The production wiring, checked rather than assumed.

    Everything else in this module runs over plain HTTP on loopback, which is a
    seam. This is the assertion that the seam is not what a deployment uses.
    """
    hosts, schemes = GeofabrikMapProvider(version="0.0.0-test").transfer.constraints

    assert schemes == SECURE_SCHEMES == frozenset({"https"})
    assert hosts == ALLOWED_HOSTS


def test_a_plain_http_address_is_refused_before_a_socket_opens() -> None:
    """A basemap somebody on the path can replace is a map that lies."""
    transfer = HttpTransfer(allowed_hosts=LOOPBACK, agent="test")

    with pytest.raises(MapOperationError) as raised:
        transfer.head("http://127.0.0.1:1/package.mbtiles")

    assert raised.value.code is MapErrorCode.MAP_DOWNLOAD_REDIRECT_REFUSED


def test_a_host_the_provider_never_declared_is_refused() -> None:
    """The allowlist is the provider's, not the caller's."""
    with pytest.raises(MapOperationError) as raised:
        _transfer().head("http://attacker.example/package.mbtiles")

    assert raised.value.code is MapErrorCode.MAP_DOWNLOAD_REDIRECT_REFUSED


def test_a_redirect_off_the_provider_is_not_followed() -> None:
    """The interesting attack: everything looked right until the second hop."""
    with (
        serve_packages(
            {"/package.mbtiles": ServedFile(redirect_to="http://attacker.example/x")}
        ) as server,
        pytest.raises(MapOperationError) as raised,
    ):
        _transfer().stream(
            server.url("/package.mbtiles"),
            io.BytesIO(),
            max_bytes=len(PACKAGE) * 2,
            on_progress=_nothing,
            is_cancelled=_never,
        )

    assert raised.value.code is MapErrorCode.MAP_DOWNLOAD_REDIRECT_REFUSED


def test_a_redirect_inside_the_provider_is_followed() -> None:
    """A provider moving a file within its own hosts is ordinary."""
    with serve_packages({}) as server:
        server.routes["/old.mbtiles"] = ServedFile(redirect_to=server.url("/new.mbtiles"))
        server.routes["/new.mbtiles"] = served(PACKAGE)

        sink = io.BytesIO()
        outcome = _transfer().stream(
            server.url("/old.mbtiles"),
            sink,
            max_bytes=len(PACKAGE) * 2,
            on_progress=_nothing,
            is_cancelled=_never,
        )

    assert sink.getvalue() == PACKAGE
    assert outcome.size_bytes == len(PACKAGE)


def test_the_user_agent_names_this_application() -> None:
    """A provider giving away bandwidth deserves to know who is asking.

    And it deserves not to be lied to: imitating a browser to get past somebody
    else's rules is not a thing this project does.
    """
    agent = user_agent("1.2.3")

    assert agent.startswith("trackvault/1.2.3")
    for browser in ("Mozilla", "Chrome", "Safari", "AppleWebKit"):
        assert browser not in agent

    with serve_packages({"/p.mbtiles": served(PACKAGE)}) as server:
        _transfer().head(server.url("/p.mbtiles"))


# --- What may be transferred -------------------------------------------------


def test_the_digest_is_computed_while_the_bytes_stream_past() -> None:
    """A second full read only to hash would double every install."""
    import hashlib

    with serve_packages({"/p.mbtiles": served(PACKAGE)}) as server:
        outcome = _transfer().stream(
            server.url("/p.mbtiles"),
            io.BytesIO(),
            max_bytes=len(PACKAGE) * 2,
            on_progress=_nothing,
            is_cancelled=_never,
        )

    assert outcome.content_sha256 == hashlib.sha256(PACKAGE).hexdigest()


def test_a_declared_size_over_the_ceiling_is_refused_before_the_body() -> None:
    """Reading the body to find out it was too large has already paid the cost."""
    with (
        serve_packages({"/p.mbtiles": served(PACKAGE)}) as server,
        pytest.raises(MapOperationError) as raised,
    ):
        _transfer().stream(
            server.url("/p.mbtiles"),
            io.BytesIO(),
            max_bytes=16,
            on_progress=_nothing,
            is_cancelled=_never,
        )

    assert raised.value.code is MapErrorCode.MAP_DOWNLOAD_TOO_LARGE


def test_a_body_that_outgrows_its_declaration_is_stopped_mid_stream() -> None:
    """A provider that declares nothing, or lies, still cannot fill a disk."""
    oversized = ServedFile(body=PACKAGE, declared_length=None)
    with (
        serve_packages({"/p.mbtiles": oversized}) as server,
        pytest.raises(MapOperationError) as raised,
    ):
        _transfer().stream(
            server.url("/p.mbtiles"),
            io.BytesIO(),
            max_bytes=1024,
            on_progress=_nothing,
            is_cancelled=_never,
        )

    assert raised.value.code is MapErrorCode.MAP_DOWNLOAD_TOO_LARGE


def test_a_transfer_with_no_declared_size_still_completes() -> None:
    """“The provider said nothing" is a normal answer, not a refusal."""
    with serve_packages({"/p.mbtiles": served(PACKAGE, declared=False)}) as server:
        sink = io.BytesIO()
        outcome = _transfer().stream(
            server.url("/p.mbtiles"),
            sink,
            max_bytes=len(PACKAGE) * 2,
            on_progress=_nothing,
            is_cancelled=_never,
        )

    assert outcome.size_bytes == len(PACKAGE)
    assert sink.getvalue() == PACKAGE


def test_a_dropped_connection_fails_rather_than_producing_a_short_package() -> None:
    """Half a package that hashes to something is still half a package."""
    truncated = ServedFile(
        body=PACKAGE, declared_length=len(PACKAGE), truncate_after=len(PACKAGE) // 3
    )
    with (
        serve_packages({"/p.mbtiles": truncated}) as server,
        pytest.raises(MapOperationError) as raised,
    ):
        _transfer().stream(
            server.url("/p.mbtiles"),
            io.BytesIO(),
            max_bytes=len(PACKAGE) * 2,
            on_progress=_nothing,
            is_cancelled=_never,
        )

    assert raised.value.code is MapErrorCode.MAP_DOWNLOAD_FAILED


def test_a_cancelled_transfer_stops_at_the_next_chunk() -> None:
    """Cooperative, because a socket read cannot be interrupted from outside."""
    with (
        serve_packages({"/p.mbtiles": served(PACKAGE)}) as server,
        pytest.raises(MapOperationError),
    ):
        _transfer().stream(
            server.url("/p.mbtiles"),
            io.BytesIO(),
            max_bytes=len(PACKAGE) * 2,
            on_progress=_nothing,
            is_cancelled=lambda: True,
        )


def test_progress_is_reported_as_the_bytes_arrive() -> None:
    """A progress bar needs the running count, not the final one."""
    seen: list[int] = []
    with serve_packages({"/p.mbtiles": served(PACKAGE)}) as server:
        _transfer().stream(
            server.url("/p.mbtiles"),
            io.BytesIO(),
            max_bytes=len(PACKAGE) * 2,
            on_progress=seen.append,
            is_cancelled=_never,
        )

    assert seen
    assert seen[-1] == len(PACKAGE)
    assert seen == sorted(seen)


def test_a_missing_package_is_absent_rather_than_a_failure() -> None:
    """199 of the provider's regions have no package. That is data, not an error."""
    with serve_packages({}) as server:
        assert _transfer().head(server.url("/nothing.mbtiles")) is None


def test_a_declared_size_and_modification_time_are_read_from_the_provider() -> None:
    """Which is what "Download size" and "Dataset date" in the manager are."""
    with serve_packages({"/p.mbtiles": served(PACKAGE)}) as server:
        resource = _transfer().head(server.url("/p.mbtiles"))

    assert resource is not None
    assert resource.size_bytes == len(PACKAGE)
    assert resource.modified_at is not None
    assert resource.modified_at.tzinfo is not None


def test_a_catalog_larger_than_expected_is_refused_rather_than_buffered() -> None:
    """The index is half a megabyte. Anything else is not the index."""
    with (
        serve_packages({"/index.json": served(PACKAGE)}) as server,
        pytest.raises(MapOperationError) as raised,
    ):
        _transfer().fetch(server.url("/index.json"), limit_bytes=64)

    assert raised.value.code is MapErrorCode.MAP_CATALOG_INVALID


@pytest.mark.parametrize(
    "call",
    [
        lambda transfer, url: transfer.head(url),
        lambda transfer, url: transfer.fetch(url, limit_bytes=1024),
    ],
)
def test_an_unreachable_provider_is_reported_as_unavailable(
    call: Callable[[HttpTransfer, str], object],
) -> None:
    """Installed maps are unaffected, and the message has to be able to say so."""
    with pytest.raises(MapOperationError) as raised:
        # Port 1 on loopback: nothing listens, and the refusal is immediate.
        call(_transfer(), "http://127.0.0.1:1/anything")

    assert raised.value.code is MapErrorCode.MAP_PROVIDER_UNAVAILABLE
