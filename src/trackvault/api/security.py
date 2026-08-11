"""The response headers a same-origin browser application should carry.

TrackVault assumes a trusted network -- there is no authentication and there is
deliberately no upload endpoint -- so these headers are not a substitute for
one. They close the accidents a same-origin page can still have:

| Header | What it prevents |
| --- | --- |
| `X-Content-Type-Options: nosniff` | a browser deciding a stored note is markup |
| `Referrer-Policy: no-referrer` | a tile provider learning the archive's address |
| `X-Frame-Options: DENY` | the page being framed by something else |
| `Content-Security-Policy` | framing, `<base>` rewriting, plugins, form posts |

`Referrer-Policy` stays even though nothing leaves the origin any more. It costs
nothing and it is the header that would matter again the moment anything did.

**The policy names every source, and that is new.** It used to state only what
the page never does, because a configured basemap put a style on one host and
its tiles, sprites and glyphs on others -- none of them knowable when the image
was built, so a policy naming them would have broken somebody's provider.
Offline map packages removed that unknown: the basemap, its glyphs and its tiles
are served by this process from this origin. So the policy can say `default-src
'self'`, and the browser now enforces the offline contract the tests assert.

Two allowances are not slack:

| Directive | Why it is not `'self'` alone |
| --- | --- |
| `worker-src blob:` | MapLibre creates its tile worker from a blob URL |
| `img-src ... data: blob:` | MapLibre builds glyph and pattern textures in memory |
| `style-src ... 'unsafe-inline'` | MapLibre and ECharts set element style attributes |

`style-src 'unsafe-inline'` is the weakest line here and is worth being honest
about: it permits a style attribute, not a script, and removing it would mean
patching two libraries' entire rendering approach. `script-src` stays strict,
which is the one that matters -- and it can, because the page no longer carries
an injected inline script.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "worker-src blob:",
        "child-src blob:",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "object-src 'none'",
    )
)
"""Everything the page may load, and everything it never does.

`default-src 'self'` is the line that turns "this deployment makes no external
request while you look at a track" from a property of the code into a property
the browser refuses to violate. A defect that added a CDN font, a remote sprite
sheet or an analytics beacon would be blocked rather than shipped.
"""

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": CONTENT_SECURITY_POLICY,
}
"""The headers every response carries, whatever produced it."""


def apply_security_headers(app: FastAPI) -> None:
    """Add the security headers to every response the application produces.

    Middleware rather than a per-route decorator, so a route added later cannot
    be the one that forgets. Existing headers are never overwritten: a response
    that has already made a deliberate statement about one of these keeps it.
    """

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Answer as the application does, plus the headers above."""
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


__all__ = ["CONTENT_SECURITY_POLICY", "SECURITY_HEADERS", "apply_security_headers"]
