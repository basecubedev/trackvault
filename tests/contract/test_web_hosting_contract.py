"""Executable contract: one origin, and a deep link that survives a refresh.

The browser application routes on the client, so `/tracks/123` is a URL the page
owns and the server has never heard of. A server that answered 404 for it would
break every refresh and every shared link -- which is the single most common way
a single-page application ships broken.

So anything the server does not own falls through to the entry page. What it
*does* own is a closed list, and two rules keep the fall-through from becoming a
hole:

```
/api/...      never answered with HTML   -- a JSON client must not have to parse a page
../../etc     never answered at all      -- a path that escapes the build reaches nothing
```

The whole thing is optional. An archive built without browser assets serves its
API and starts normally.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.api.web import IMMUTABLE_CACHE, NO_CACHE
from gpx_view.config import Settings
from gpx_view.main import create_app

pytestmark = pytest.mark.contract

PAGE = """<!doctype html>
<html lang="en"><head><title>GPX-View</title></head>
<body><div id="root"></div><script type="module" src="/assets/app-abc123.js"></script></body>
</html>
"""


@pytest.fixture
def build(tmp_path: Path) -> Path:
    """Return a directory shaped like a real Vite build."""
    directory = tmp_path / "dist"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text(PAGE, encoding="utf-8")
    (directory / "assets" / "app-abc123.js").write_text("export const x = 1\n", encoding="utf-8")
    (directory / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return directory


@pytest.fixture
def hosted(tmp_path: Path, build: Path) -> Iterator[TestClient]:
    """Yield a client over an archive that also serves the browser application."""
    settings = Settings(data_dir=tmp_path / "data", web_dir=build)
    with TestClient(create_app(settings)) as client:
        yield client


def test_the_root_serves_the_application(hosted: TestClient) -> None:
    """The page is what a browser asking for `/` gets."""
    response = hosted.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert '<div id="root">' in response.text


@pytest.mark.parametrize("path", ["/tracks", "/tracks/123", "/tracks/123?year=2025"])
def test_a_deep_link_survives_a_refresh(hosted: TestClient, path: str) -> None:
    """Client-side routes are the page's, and the server hands the page over."""
    response = hosted.get(path)

    assert response.status_code == 200
    assert '<div id="root">' in response.text


def test_the_api_is_never_answered_with_a_page(hosted: TestClient) -> None:
    """A client that asked for JSON must not have to parse HTML to find out."""
    response = hosted.get("/api/v1/nothing-here")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "track_not_found", "message": "no such endpoint"}}


@pytest.mark.parametrize("path", ["/healthz", "/openapi.json", "/docs"])
def test_the_server_keeps_its_own_endpoints(hosted: TestClient, path: str) -> None:
    """Mounting a page must not shadow the schema, the probe or the docs."""
    response = hosted.get(path)

    assert response.status_code == 200
    assert '<div id="root">' not in response.text


def test_the_archive_still_answers_its_own_api(hosted: TestClient) -> None:
    """The application is served beside the archive, never instead of it."""
    response = hosted.get("/api/v1/tracks")

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_a_hashed_asset_is_cacheable_and_the_page_is_not(hosted: TestClient) -> None:
    """A release arrives because the page is fresh and its assets are new files."""
    asset = hosted.get("/assets/app-abc123.js")
    page = hosted.get("/")

    assert asset.status_code == 200
    assert asset.headers["cache-control"] == IMMUTABLE_CACHE
    assert page.headers["cache-control"] == NO_CACHE


def test_a_build_file_beside_the_page_is_served(hosted: TestClient) -> None:
    """Not everything a build emits lives under `assets/`."""
    response = hosted.get("/favicon.svg")

    assert response.status_code == 200
    assert response.headers["cache-control"] == NO_CACHE


@pytest.mark.parametrize(
    "path", ["/../pyproject.toml", "/..%2F..%2Fetc%2Fpasswd", "/assets/../../pyproject.toml"]
)
def test_nothing_outside_the_build_is_served(hosted: TestClient, path: str) -> None:
    """A path that climbs out of the build reaches the page, never a file."""
    response = hosted.get(path)

    assert response.status_code in {200, 404}
    assert "[project]" not in response.text
    assert "root:" not in response.text


# --- The page is served as it was built --------------------------------------


def test_the_page_carries_no_injected_script(hosted: TestClient) -> None:
    """The entry page is the file the build produced, byte for byte.

    It used to carry an injected `<script>` holding a basemap style URL.
    Offline map packages replaced that configuration entirely, and removing the
    injection is what lets the content security policy say `script-src 'self'`
    -- an inline script would otherwise need a nonce, which is machinery in
    place of a deletion.
    """
    body = hosted.get("/").text

    assert body == PAGE
    assert "__GPX_VIEW__" not in body
    assert "<script" not in body.replace('<script type="module"', "", 1)


def test_the_page_asks_the_archive_rather_than_being_configured(hosted: TestClient) -> None:
    """Everything the map needs is an endpoint, not a build or a deployment constant.

    Which basemap to draw is a question about what is installed *now*, and the
    answer changes while the page is open. A value baked into the document at
    request time would be a second, staler authority on it.
    """
    assert hosted.get("/api/v1/maps/coverage?bbox=6,50,8,52").status_code == 200


# --- Optional ----------------------------------------------------------------


def test_an_archive_without_browser_assets_starts_and_serves_its_api(tmp_path: Path) -> None:
    """A development run and an API-only deployment are normal states."""
    settings = Settings(data_dir=tmp_path / "data", web_dir=tmp_path / "no-such-build")
    with TestClient(create_app(settings)) as client:
        assert client.app.state.web_mounted is False  # type: ignore[attr-defined]
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/tracks").status_code == 200
        assert client.get("/tracks/1").status_code == 404
