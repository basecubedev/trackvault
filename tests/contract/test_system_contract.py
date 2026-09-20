"""What a deployment says about itself, and what every response carries.

Two small contracts that only matter once somebody else runs this.

**A deployment can name what it is running.** "Why did my elevation gain
change?" is answerable from the data when the archive can state which
algorithms produced it, and unanswerable when it cannot. The endpoint reports
the release, the schema and both currency authorities -- and nothing about the
machine it runs on.

**Every response carries the same small set of headers.** TrackVault assumes a
trusted network, so these are not a substitute for authentication; they close
the accidents a same-origin page can still have. Since the basemap became a
locally installed package, the content security policy can name every source
the page is allowed to load -- so the browser, and not only the test suite,
enforces that viewing a track reaches nothing outside this deployment.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trackvault import __version__
from trackvault.api.security import SECURITY_HEADERS
from trackvault.config import Settings
from trackvault.infrastructure.database import SCHEMA_VERSION
from trackvault.main import create_app

pytestmark = pytest.mark.contract

INFO_URL = "/api/v1/system/info"


def test_the_archive_states_the_release_it_is_running(client: TestClient) -> None:
    """One version, from the distribution metadata that declares it."""
    payload = client.get(INFO_URL).json()

    assert payload["version"] == __version__
    assert payload["schema_version"] == SCHEMA_VERSION


def test_the_archive_states_the_algorithms_that_produced_its_numbers(
    client: TestClient,
) -> None:
    """The reason this is not just a version string in a footer."""
    payload = client.get(INFO_URL).json()

    assert payload["analysis"]["distance_algorithm"]
    assert payload["analysis"]["movement_algorithm_version"] >= 1
    assert [entry["importer"] for entry in payload["processing"]] == ["gpx"]
    assert payload["processing"][0]["classifier_version"]


def test_the_reported_timezone_is_the_one_periods_are_drawn_in(tmp_path: Path) -> None:
    """The same zone every statistics response names, from the same setting."""
    settings = Settings(data_dir=tmp_path / "data", timezone="Europe/Berlin")

    with TestClient(create_app(settings)) as client:
        assert client.get(INFO_URL).json()["timezone"] == "Europe/Berlin"
        assert client.get("/api/v1/statistics/year/2026").json()["timezone"] == "Europe/Berlin"


def test_system_information_describes_the_build_and_never_the_machine(
    client: TestClient,
) -> None:
    """No path, no data directory, no host, no configured provider.

    This endpoint exists on an unauthenticated surface. Everything it reports
    is true of the image rather than of the installation, so reading it tells
    nobody anything about where the archive lives.
    """
    body = client.get(INFO_URL).text

    assert set(client.get(INFO_URL).json()) == {
        "version",
        "schema_version",
        "timezone",
        "processing",
        "analysis",
        # Whether this deployment accepts files. A capability rather than a
        # configuration detail: it says what the server will do, not where it
        # keeps anything, and the interface needs it to avoid offering a
        # control the server would refuse.
        "upload_enabled",
    }
    for leak in ("/data", "data_dir", "sqlite", "map_style", "host", "/home"):
        assert leak not in body


# --- Response headers -------------------------------------------------------


@pytest.fixture
def hosting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Yield a client whose archive also serves a built browser application."""
    build = tmp_path / "web"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")
    (build / "assets" / "app-abc123.js").write_text("export default 1\n", encoding="utf-8")
    monkeypatch.setenv("TRACKVAULT_WEB_DIR", str(build))
    settings = Settings(data_dir=tmp_path / "data", web_dir=build)
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize("path", ["/healthz", "/api/v1/tracks", INFO_URL])
def test_every_api_response_carries_the_security_headers(client: TestClient, path: str) -> None:
    """Middleware rather than per-route, so a new route cannot be the one that forgets."""
    headers = client.get(path).headers

    for header, value in SECURITY_HEADERS.items():
        assert headers[header] == value


def test_the_page_and_its_assets_carry_them_too(hosting: TestClient) -> None:
    """The browser application is served by the same process and the same rules."""
    page = hosting.get("/")
    asset = hosting.get("/assets/app-abc123.js")

    assert page.headers["x-content-type-options"] == "nosniff"
    assert asset.headers["x-frame-options"] == "DENY"
    assert "immutable" in asset.headers["cache-control"]


def test_the_referrer_never_reaches_another_service(client: TestClient) -> None:
    """Nothing leaves the origin now, and the header is what keeps it that way.

    It costs nothing and it is the header that would matter again the moment a
    future feature did reach outwards: the address it would hand over is the
    location of somebody's private movement archive.
    """
    assert client.get("/healthz").headers["referrer-policy"] == "no-referrer"


def test_the_policy_confines_the_page_to_this_origin(client: TestClient) -> None:
    """The browser enforces the offline contract, not only the tests.

    The policy used to name no source at all, because a configured basemap put
    a style, its tiles, its sprites and its glyphs on hosts this image could not
    know. Offline map packages removed that unknown, so `default-src 'self'` is
    now a statement this build can actually keep -- and a defect that added a
    CDN font or an analytics beacon would be blocked rather than shipped.
    """
    policy = client.get("/healthz").headers["content-security-policy"]

    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "font-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy


def test_the_policy_allows_only_what_the_map_and_charts_genuinely_need(
    client: TestClient,
) -> None:
    """Two allowances, and neither of them is a remote host.

    MapLibre builds its glyph atlases as in-memory images; MapLibre and ECharts
    both set style attributes on elements. Those are the exceptions, they are
    local by construction, and listing them here is what stops a third one being
    added quietly.

    Workers are no longer among them. A blob worker is a script assembled at
    runtime, which is the shape `script-src 'self'` exists to refuse, so the
    directive is pinned to this origin and a library that went back to blobs
    would fail here rather than quietly widen the policy.
    """
    policy = client.get("/healthz").headers["content-security-policy"]

    assert "worker-src 'self'" in policy
    assert "child-src 'self'" in policy
    assert [directive.strip() for directive in policy.split(";") if "blob:" in directive] == [
        "img-src 'self' data: blob:"
    ]
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "http://" not in policy
    assert "https://" not in policy


def test_an_error_response_carries_them_as_well(client: TestClient) -> None:
    """Including the ones nobody wrote a route for."""
    response = client.get("/api/v1/tracks/999999")

    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"
