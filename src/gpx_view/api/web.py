"""Serving the built browser application from the same origin as the API.

One origin, for one reason: a browser application and an API on two origins
need a CORS configuration, and a CORS configuration is a security decision made
to solve a deployment accident. Serving `dist/` from here means the page and the
data it reads share an origin in production exactly as they do behind the
development proxy.

```
/api/v1/...   the archive          answered by the routers
/docs         the schema           answered by FastAPI
/healthz      the probe            answered by the health router
/assets/...   hashed build output  answered from disk, cacheable for a year
/anything     the application      answered with index.html
```

The last line is what makes a deep link work. Client-side routing means
`/tracks/123` is a URL the browser owns, and a server that answered 404 for it
would break every refresh and every shared link. So anything the server does not
own falls through to the page, which then routes.

Two rules keep that fall-through from becoming a hole:

- it never serves a path that escapes the build directory -- the file is
  resolved and checked against the root, so `..` reaches nothing;
- it never answers a `/api` path with HTML. An unknown endpoint is a 404 with
  the archive's own error envelope, because a client that asked for JSON and got
  a page has to guess what went wrong.

The whole thing is optional. A build that ships without `dist/` -- a test run, a
development server, an API-only deployment -- serves the API and says so at the
root rather than failing to start.
"""

from collections.abc import Iterable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

INDEX = "index.html"
ASSETS = "assets"

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
"""How long a hashed asset may be cached.

A year, because the name contains a hash of the contents: a new release is a new
file name rather than a stale copy somebody has to shift-reload away.
"""

NO_CACHE = "no-cache"
"""How long the entry page may be cached.

Not at all. `index.html` is the one file whose name never changes, so caching it
is how a release stops arriving.
"""

_RESERVED = ("/api", "/docs", "/redoc", "/openapi.json", "/healthz")
"""Prefixes the server owns. The application never answers for these."""


def mount_web_application(app: FastAPI, directory: Path) -> bool:
    """Serve a built browser application from ``directory``, if there is one.

    One route serves every build file rather than a static mount beside a
    fall-through. Two paths to the same directory would be two places to get the
    escape check and the cache lifetime right, and only one of them would be
    exercised by the tests somebody remembers to write.

    The page is served exactly as it was built. It used to carry an injected
    `<script>` holding a basemap style URL; offline map packages replaced that
    configuration entirely, and removing the injection is what lets the content
    security policy say `script-src 'self'` -- an inline script would need a
    nonce, and a nonce on a static page is machinery in place of a deletion.

    Args:
        app: The application to mount on.
        directory: The build output directory, usually ``web/dist``.

    Returns:
        Whether anything was mounted. ``False`` is a normal answer: a
        development server, a test run and an API-only deployment all have no
        build output, and none of them should fail to start over it.
    """
    index = directory / INDEX
    if not index.is_file():
        return False

    root = directory.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def serve_application(request: Request, path: str) -> Response:
        """Answer with a build file, or with the page that routes to it."""
        if _is_reserved(request.url.path):
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "track_not_found", "message": "no such endpoint"}},
            )
        candidate = _within(root, path)
        if candidate is not None:
            return FileResponse(
                candidate,
                headers={"cache-control": IMMUTABLE_CACHE if _is_hashed(candidate) else NO_CACHE},
            )
        return FileResponse(
            index,
            media_type="text/html; charset=utf-8",
            headers={"cache-control": NO_CACHE},
        )

    return True


def _is_reserved(path: str) -> bool:
    """Report whether the server owns a path rather than the application."""
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _RESERVED)


def _within(root: Path, path: str) -> Path | None:
    """Return the build file a request names, or ``None`` if it names none.

    The candidate is resolved and then checked against the root, so a path that
    climbs out with ``..`` or through a symbolic link resolves to somewhere the
    check rejects rather than to a file the archive then serves.
    """
    if not path or path.endswith("/"):
        return None
    candidate = (root / path).resolve()
    if not candidate.is_file():
        return None
    return candidate if candidate.is_relative_to(root) else None


def _is_hashed(candidate: Path) -> bool:
    """Report whether a file's name carries a content hash."""
    return ASSETS in candidate.parts


def reserved_prefixes() -> Iterable[str]:
    """Return the prefixes the browser application must never answer for."""
    return _RESERVED


__all__ = ["IMMUTABLE_CACHE", "NO_CACHE", "mount_web_application", "reserved_prefixes"]
