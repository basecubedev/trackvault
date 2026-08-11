"""Fetching bytes from a map provider, safely.

Everything here exists because a download is the one place this application
makes an outbound request, and the failure modes are not hypothetical:

```
a caller choosing the host        -> server-side request forgery
a redirect leaving the provider   -> the same, one hop later
plain HTTP                        -> a basemap somebody on the path can replace
an unbounded stream               -> a full disk from one request
reading the whole body into memory -> a container killed by the kernel on a 5 GB file
hashing afterwards                -> every install pays for a second full read
```

So: the URL is produced by a provider adapter and never accepted from above;
only `https` is spoken; redirects are followed only to hosts the provider
declares; the body is streamed in chunks with a hard ceiling; and the digest is
computed as the bytes go past, once.

The User-Agent names this application honestly. Imitating a browser to get past
somebody's rules would be lying to an operator who is giving us a free service.
"""

import hashlib
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Container
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from http.client import HTTPResponse
from typing import BinaryIO, cast
from urllib.parse import urlsplit

from trackvault.application.maps import MapErrorCode, MapOperationError, TransferOutcome

logger = logging.getLogger(__name__)

HTTPS_SCHEME = "https"
SECURE_SCHEMES = frozenset({HTTPS_SCHEME})
"""The schemes a provider may be reached over. HTTPS, and nothing else.

A basemap fetched over plain HTTP is a basemap anybody on the path can replace,
and a map is a picture somebody navigates by. The set is a constructor argument
only so the test suite can exercise the streaming, hashing and size-ceiling
paths against a loopback server without a private key in the repository;
`tests/contract/test_map_download_contract.py` asserts that the provider adapter
constructs its transfer with this default.
"""
CHUNK_BYTES = 1024 * 256
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_REDIRECTS = 5
CANCELLED_DETAIL = "cancelled"


def user_agent(version: str) -> str:
    """Return the identity this application presents to a map provider."""
    return f"trackvault/{version} (self-hosted track archive; offline map installer)"


@dataclass(frozen=True, slots=True)
class RemoteResource:
    """What a `HEAD` said about an address.

    Attributes:
        size_bytes: The declared length, or ``None`` if none was declared.
        modified_at: When the provider last changed it, if it said.
    """

    size_bytes: int | None
    modified_at: datetime | None


class _HostBoundRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only while it stays inside the provider's own hosts.

    A trusted provider redirecting to `attacker.example` is the interesting
    attack, because everything up to that point looked exactly right: the
    application chose the URL, the scheme was HTTPS, and the host was the one in
    the allowlist. The check therefore has to happen on the *target*, and it has
    to happen inside the redirect machinery rather than after it -- by the time
    a response object exists, the request has been made.
    """

    def __init__(self, allowed_hosts: Container[str], allowed_schemes: Container[str]) -> None:
        """Bind the handler to the hosts and schemes one provider declares."""
        self._allowed = allowed_hosts
        self._schemes = allowed_schemes

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Return the follow-up request, or refuse to make one."""
        _require_allowed(newurl, self._allowed, self._schemes)
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


def _require_allowed(
    url: str, allowed_hosts: Container[str], allowed_schemes: Container[str]
) -> str:
    """Return the URL, or refuse it before a socket is opened."""
    parts = urlsplit(url)
    if parts.scheme not in allowed_schemes:
        raise MapOperationError(MapErrorCode.MAP_DOWNLOAD_REDIRECT_REFUSED, "scheme not allowed")
    if parts.hostname is None or parts.hostname.lower() not in allowed_hosts:
        raise MapOperationError(MapErrorCode.MAP_DOWNLOAD_REDIRECT_REFUSED, "host not declared")
    return url


class HttpTransfer:
    """The one place this application opens an outbound connection.

    Args:
        allowed_hosts: The hosts the owning provider declares. Nothing outside
            them is ever contacted, on a first request or on a redirect.
        agent: The User-Agent header.
        timeout_seconds: Applied to the connection and to each read, so a
            provider that accepts a connection and then says nothing cannot
            hold an installation open forever.
    """

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        agent: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        allowed_schemes: frozenset[str] = SECURE_SCHEMES,
    ) -> None:
        """Build a transfer bound to one provider's hosts."""
        self._allowed = allowed_hosts
        self._agent = agent
        self._timeout = timeout_seconds
        self._schemes = allowed_schemes
        self._opener = urllib.request.build_opener(
            _HostBoundRedirectHandler(allowed_hosts, allowed_schemes)
        )
        self._opener.addheaders = []

    @property
    def constraints(self) -> tuple[frozenset[str], frozenset[str]]:
        """Return the hosts and schemes this transfer will speak to."""
        return frozenset(self._allowed), frozenset(self._schemes)

    def head(self, url: str) -> RemoteResource | None:
        """Ask what is at an address without fetching it.

        Returns:
            What the provider declared, or ``None`` when there is nothing there.

        Raises:
            MapOperationError: ``map_provider_unavailable`` when the provider
                could not be reached at all.
        """
        try:
            with self._open(url, method="HEAD") as response:
                return RemoteResource(
                    size_bytes=_content_length(response.headers.get("Content-Length")),
                    modified_at=_http_date(response.headers.get("Last-Modified")),
                )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise MapOperationError(
                MapErrorCode.MAP_PROVIDER_UNAVAILABLE, f"http {error.code}"
            ) from error
        except OSError as error:
            raise MapOperationError(MapErrorCode.MAP_PROVIDER_UNAVAILABLE) from error

    def fetch(self, url: str, *, limit_bytes: int) -> bytes:
        """Return a small document in full, refusing one that is not small.

        Used for the catalog index, which is half a megabyte. Anything using
        this for a package would be the mistake the streaming path exists to
        prevent, so the ceiling is a parameter without a generous default.
        """
        try:
            with self._open(url, method="GET") as response:
                body: bytes = response.read(limit_bytes + 1)
        except urllib.error.HTTPError as error:
            raise MapOperationError(
                MapErrorCode.MAP_PROVIDER_UNAVAILABLE, f"http {error.code}"
            ) from error
        except OSError as error:
            raise MapOperationError(MapErrorCode.MAP_PROVIDER_UNAVAILABLE) from error
        if len(body) > limit_bytes:
            raise MapOperationError(
                MapErrorCode.MAP_CATALOG_INVALID, "catalog is larger than expected"
            )
        return body

    def stream(
        self,
        url: str,
        sink: BinaryIO,
        *,
        max_bytes: int,
        on_progress: Callable[[int], None],
        is_cancelled: Callable[[], bool],
    ) -> TransferOutcome:
        """Stream an address into a file, hashing and bounding as it goes."""
        digest = hashlib.sha256()
        received = 0
        declared: int | None = None
        try:
            with self._open(url, method="GET") as response:
                declared = _content_length(response.headers.get("Content-Length"))
                if declared is not None and declared > max_bytes:
                    raise MapOperationError(
                        MapErrorCode.MAP_DOWNLOAD_TOO_LARGE,
                        f"{declared} bytes exceeds the {max_bytes} byte limit",
                    )
                while chunk := response.read(CHUNK_BYTES):
                    if is_cancelled():
                        raise MapOperationError(MapErrorCode.MAP_DOWNLOAD_FAILED, CANCELLED_DETAIL)
                    received += len(chunk)
                    if received > max_bytes:
                        # The ceiling holds even when the provider declared
                        # nothing, which is the case it exists for: a chunked
                        # response has no length to check beforehand.
                        raise MapOperationError(
                            MapErrorCode.MAP_DOWNLOAD_TOO_LARGE,
                            f"exceeded the {max_bytes} byte limit mid-stream",
                        )
                    digest.update(chunk)
                    sink.write(chunk)
                    on_progress(received)
        except urllib.error.HTTPError as error:
            raise MapOperationError(
                MapErrorCode.MAP_DOWNLOAD_FAILED, f"http {error.code}"
            ) from error
        except OSError as error:
            raise MapOperationError(MapErrorCode.MAP_DOWNLOAD_FAILED) from error
        if received == 0:
            raise MapOperationError(MapErrorCode.MAP_DOWNLOAD_FAILED, "empty response")
        if declared is not None and received != declared:
            # `http.client` deliberately does *not* raise when a connection
            # closes before a declared body is complete: `read(amt)` returns an
            # empty chunk and the loop above ends normally. A short package
            # hashes to something, validates as a container often enough, and
            # would be installed as a map with half a country missing. The
            # length the provider declared is the only thing that can tell the
            # difference, so it is checked rather than trusted to have held.
            raise MapOperationError(
                MapErrorCode.MAP_DOWNLOAD_FAILED,
                f"received {received} of {declared} bytes",
            )
        on_progress(received)
        return TransferOutcome(content_sha256=digest.hexdigest(), size_bytes=received)

    def _open(self, url: str, *, method: str) -> HTTPResponse:
        """Open a validated request. Every outbound call goes through here.

        `_require_allowed` runs before the request object exists, so the only
        scheme that reaches `urllib` is one this transfer declared -- `https` in
        every production wiring -- and the only host is one the provider
        declared. That is what the audit suppression below refers to, rather
        than a hope.
        """
        request = urllib.request.Request(  # noqa: S310
            _require_allowed(url, self._allowed, self._schemes),
            method=method,
            headers={"User-Agent": self._agent, "Accept-Encoding": "identity"},
        )
        return cast(HTTPResponse, self._opener.open(request, timeout=self._timeout))


def _content_length(value: str | None) -> int | None:
    """Return a declared length, or ``None`` when there is none to believe."""
    if not value:
        return None
    try:
        length = int(value)
    except ValueError:
        return None
    return length if length >= 0 else None


def _http_date(value: str | None) -> datetime | None:
    """Return an HTTP date as an instant, or ``None`` when it is unreadable."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None
