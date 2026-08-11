"""Docker smoke and persistence tests for the container image and compose stack.

Opt-in: ``uv run pytest -m docker``. The default suite excludes this module so the
test suite stays runnable without a Docker daemon.

The tests use their own compose project name and always tear the stack down
again, volume included, so they never leave containers or data behind.

The GPX content is piped into the container over stdin rather than mounted: the
image deliberately ships no test fixtures.
"""

import hashlib
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "gpx"
COMPOSE_PROJECT = "gpx-view-smoke"
BASE_URL = "http://127.0.0.1:8080"
HEALTH_URL = f"{BASE_URL}/healthz"
TRACKS_URL = f"{BASE_URL}/api/v1/tracks"
STARTUP_TIMEOUT_SECONDS = 90.0
POLL_INTERVAL_SECONDS = 1.0

CONTAINER = "gpx-view"
CONTAINER_IMPORT_PATH = "/tmp/synthetic-import.gpx"  # noqa: S108
CONTAINER_MAP_SEED = "/tmp/seed-map.py"  # noqa: S108
MAPS_URL = f"{BASE_URL}/api/v1/maps"

pytestmark = pytest.mark.docker

_MAP_SEED_SCRIPT = '''
"""Install a minimal map package inside the container, without a network.

Written into the container rather than imported from the test suite: the image
deliberately ships no tests, so proving that the *shipped* code can install and
serve a package means handing it a script and a package it builds itself.
"""
import sqlite3, gzip, hashlib, os, shutil
from datetime import UTC, datetime
from pathlib import Path

from gpx_view.config import Settings
from gpx_view.domain.maps import (
    AttributionLink, MapAttribution, MapBounds, MapPackage, MapPackageFormat,
    MapRegionId, MapTileSchema,
)
from gpx_view.infrastructure.database import SqliteTrackStore
from gpx_view.infrastructure.database.map_store import SqliteMapPackageStore
from gpx_view.infrastructure.maps import FilesystemMapPackageStorage, MbtilesPackageInspector

settings = Settings()
storage = FilesystemMapPackageStorage(settings.map_storage_dir)
storage.prepare()
store = SqliteTrackStore(settings.database_path)
store.migrate()

scratch = Path("/tmp/seed.mbtiles")
scratch.unlink(missing_ok=True)
connection = sqlite3.connect(scratch)
connection.execute(
    "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)"
)
connection.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
connection.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
layers = [
    "boundaries", "land", "ocean", "place_labels", "street_labels", "streets", "water_polygons",
]
described = ",".join(
    '{"id":"%s","fields":{},"minzoom":0,"maxzoom":6}' % name for name in layers
)
connection.executemany(
    "INSERT INTO metadata VALUES (?, ?)",
    [
        ("name", "Shortbread"), ("version", "1.0"), ("format", "pbf"),
        ("minzoom", "0"), ("maxzoom", "6"), ("bounds", "-10,35,20,60"),
        ("author", "OpenStreetMap contributors, GPX-View container smoke test"),
        ("license", "Open Database License 1.0"),
        ("json", '{"vector_layers":[' + described + ']}'),
    ],
)
for zoom in range(0, 7):
    span = 1 << zoom
    connection.execute(
        "INSERT INTO tiles VALUES (?, ?, ?, ?)",
        (zoom, span // 2, span // 2, gzip.compress(b"", mtime=0)),
    )
connection.commit()
connection.close()

digest = hashlib.sha256(scratch.read_bytes()).hexdigest()
destination = storage.package_path(MapRegionId.parse("fixture:smoke"), digest)
destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
shutil.copyfile(scratch, destination)
os.chmod(destination, 0o600)

region = MapRegionId.parse("fixture:smoke")
inspected = MbtilesPackageInspector(storage).inspect(region, digest)
SqliteMapPackageStore(store).save_package(
    MapPackage(
        region_id=region,
        region_name="Container smoke fixture",
        provider="fixture",
        format=inspected.format,
        tile_schema=inspected.tile_schema,
        content_sha256=digest,
        size_bytes=destination.stat().st_size,
        bounds=inspected.bounds,
        min_zoom=inspected.min_zoom,
        max_zoom=inspected.max_zoom,
        attribution=inspected.attribution,
        downloaded_at=datetime.now(UTC),
        source_url="https://fixture.invalid/smoke.mbtiles",
    )
)
print("installed", digest[:12])
'''


def _docker_is_available() -> bool:
    """Report whether a usable Docker daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def _compose(*arguments: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run a ``docker compose`` command for the smoke test project."""
    return subprocess.run(
        ["docker", "compose", "-p", COMPOSE_PROJECT, *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
        input=stdin,
    )


def _exec(*arguments: str, stdin: bytes | None = None) -> str:
    """Run a command inside the running container and return its output."""
    return _compose("exec", "-T", CONTAINER, *arguments, stdin=stdin).stdout.decode()


def _wait_until_healthy() -> None:
    """Poll the health endpoint until the container answers, or fail."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            response = httpx2.get(HEALTH_URL, timeout=5.0)
        except httpx2.HTTPError as error:
            last_error = error
        else:
            if response.status_code == 200:
                return
        time.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail(f"container did not become healthy within {STARTUP_TIMEOUT_SECONDS}s: {last_error}")


@pytest.fixture(scope="module")
def compose_stack() -> Iterator[None]:
    """Build and start the compose stack, and always shut it down again."""
    if not _docker_is_available():
        pytest.skip("no usable Docker daemon")

    _compose("build")
    _compose("up", "-d")
    try:
        _wait_until_healthy()
        yield
    finally:
        _compose("down", "-v")


@pytest.mark.integration
def test_compose_file_is_valid() -> None:
    """``compose.yaml`` parses and resolves."""
    if not _docker_is_available():
        pytest.skip("no usable Docker daemon")

    assert _compose("config", "--quiet").returncode == 0


@pytest.mark.integration
def test_container_serves_healthz(compose_stack: None) -> None:  # noqa: ARG001
    """The running container answers the health contract on port 8080."""
    response = httpx2.get(HEALTH_URL, timeout=5.0)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
def test_container_runs_as_non_root(compose_stack: None) -> None:  # noqa: ARG001
    """The runtime container must not execute the application as root."""
    assert _exec("id", "-u").strip() != "0"


@pytest.mark.integration
@pytest.mark.persistence
def test_the_container_writes_and_keeps_data_as_a_non_root_user(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """The whole deployment promise, in one scenario.

    A non-root container imports a track into ``/data``, serves it over HTTP,
    survives being recreated, and still has both the normalized track and the
    byte-identical raw artifact afterwards. Anything less would mean the archive
    only looks persistent.
    """
    assert _exec("id", "-u").strip() != "0"

    content = (FIXTURES / "recorded-measurements.gpx").read_bytes()
    _exec("sh", "-c", f"cat > {CONTAINER_IMPORT_PATH}", stdin=content)
    _exec("gpx-view", "import", CONTAINER_IMPORT_PATH)

    stored = _exec("find", "/data", "-type", "f")
    assert "gpx-view.sqlite3" in stored
    assert ".raw" in stored

    before = httpx2.get(TRACKS_URL, timeout=5.0).json()
    assert before["total"] == 1
    track = before["tracks"][0]
    assert track["title"] == "Synthetic morning walk"

    raw_artifact = _exec("sh", "-c", "find /data -name '*.raw' -print -quit").strip()
    assert raw_artifact
    checksum_before = _exec("sha256sum", raw_artifact).split()[0]
    assert checksum_before == track["raw_import_sha256"]

    _compose("up", "-d", "--force-recreate")
    _wait_until_healthy()

    after = httpx2.get(TRACKS_URL, timeout=5.0).json()
    assert after == before
    assert _exec("id", "-u").strip() != "0"
    assert _exec("sha256sum", raw_artifact).split()[0] == checksum_before


@pytest.mark.integration
@pytest.mark.persistence
def test_the_data_directory_is_owned_by_the_runtime_user(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """No `chmod 777` and no root process: the volume simply belongs to the app."""
    owner = _exec("stat", "-c", "%u", "/data").strip()

    assert owner == _exec("id", "-u").strip()
    assert _exec("stat", "-c", "%a", "/data").strip() != "777"


_CONTAINER_SHA = hashlib.sha256((FIXTURES / "recorded-measurements.gpx").read_bytes()).hexdigest()
"""Identity of the fixture the container scenarios import."""


def _import_the_reference(name: str = "recorded-measurements.gpx") -> str:
    """Import a synthetic fixture into the container and return its content hash."""
    content = (FIXTURES / name).read_bytes()
    _exec("sh", "-c", f"cat > {CONTAINER_IMPORT_PATH}", stdin=content)
    _exec("gpx-view", "import", CONTAINER_IMPORT_PATH)
    return hashlib.sha256(content).hexdigest()


@pytest.mark.integration
@pytest.mark.persistence
def test_private_data_in_the_container_is_not_world_readable(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """The permissions the archive promises hold inside the container too.

    A named volume arrives with ordinary directory permissions, so what protects
    the movement data is what GPX-View creates, not what Docker set up.
    """
    _import_the_reference()

    modes = _exec(
        "sh",
        "-c",
        "find /data -name 'gpx-view.sqlite3*' -o -name '*.raw' | "
        "while read -r f; do stat -c '%a' \"$f\"; done",
    ).split()
    directories = _exec(
        "sh", "-c", "find /data/raw -type d | while read -r d; do stat -c '%a' \"$d\"; done"
    ).split()

    assert modes, "no database or raw artifact was found to check"
    assert all(mode == "600" for mode in modes), modes
    assert all(mode == "700" for mode in directories), directories


@pytest.mark.integration
@pytest.mark.persistence
@pytest.mark.reprocessing
def test_the_container_reports_and_repairs_an_outdated_generation(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """The operator upgrade path, inside the deployment it is meant for.

    An older processing generation is simulated by rewriting what the run
    recorded -- which is exactly what makes a generation outdated -- so no old
    build has to be installed to prove the path works.
    """
    sha256 = _import_the_reference()
    assert "outdated:       no" in _exec("gpx-view", "processing-status", sha256)

    _exec(
        "python",
        "-c",
        "import sqlite3\n"
        "connection = sqlite3.connect('/data/gpx-view.sqlite3')\n"
        "connection.execute(\"UPDATE processing_runs SET importer_version = '1'\")\n"
        "connection.commit()\n",
    )
    outdated = _exec("gpx-view", "processing-status", sha256)
    _exec("gpx-view", "reprocess", "--outdated")
    repaired = _exec("gpx-view", "processing-status", sha256)

    assert "outdated:       yes" in outdated
    assert "importer:       1 -> installed" in outdated
    assert "outdated:       no" in repaired
    assert _exec("gpx-view", "reprocess", "--outdated") == "nothing to reprocess\n"


@pytest.mark.integration
@pytest.mark.analysis
@pytest.mark.statistics
def test_analysis_and_statistics_survive_recreating_the_container(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """Derived metrics live in the volume, not in the container.

    A container is disposable and its filesystem is not a place to keep
    anything. What is checked is the whole promise of the deployment: import,
    analyse and total inside one container, replace the container, and get the
    same numbers back from the same volume.
    """
    _import_the_reference()

    before_track = httpx2.get(TRACKS_URL, timeout=10.0).json()["tracks"][0]
    before_year = httpx2.get(
        f"{BASE_URL}/api/v1/statistics/year/2026?scope=recorded", timeout=10.0
    ).json()

    assert before_track["analysis"]["status"] == "current"
    assert before_track["analysis"]["distance_m"] > 0.0

    _compose("up", "-d", "--force-recreate")
    _wait_until_healthy()

    after_track = httpx2.get(TRACKS_URL, timeout=10.0).json()["tracks"][0]
    after_year = httpx2.get(
        f"{BASE_URL}/api/v1/statistics/year/2026?scope=recorded", timeout=10.0
    ).json()
    analysis = httpx2.get(
        f"{BASE_URL}/api/v1/tracks/{after_track['id']}/analysis", timeout=10.0
    ).json()

    assert after_track["analysis"] == before_track["analysis"]
    assert after_year["totals"] == before_year["totals"]
    assert analysis["status"] == "current"
    assert _exec("gpx-view", "analyze", "--outdated") == "nothing to analyze\n"


@pytest.mark.integration
@pytest.mark.analysis
def test_the_container_reanalyses_what_an_algorithm_change_outdates(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """The operator upgrade path for analysis, inside its own deployment.

    A different analysis profile is simulated by rewriting what a run recorded
    -- which is exactly what makes stored metrics outdated -- so proving the
    path needs no second build. The stored version is moved *up*, which is the
    downgrade case: an older deployment must not report numbers a newer one
    wrote as current, or it would never regenerate them.
    """
    _import_the_reference()
    assert "analysis outdated: no" in _exec("gpx-view", "processing-status", _CONTAINER_SHA)

    _exec(
        "python",
        "-c",
        "import sqlite3\n"
        "connection = sqlite3.connect('/data/gpx-view.sqlite3')\n"
        'connection.execute("UPDATE analysis_runs SET elevation_algorithm_version = 2")\n'
        "connection.commit()\n",
    )
    outdated = _exec("gpx-view", "processing-status", _CONTAINER_SHA)
    _exec("gpx-view", "analyze", "--outdated")
    repaired = _exec("gpx-view", "processing-status", _CONTAINER_SHA)

    assert "analysis outdated: yes" in outdated
    assert "analysis outdated: no" in repaired
    assert _exec("gpx-view", "analyze", "--outdated") == "nothing to analyze\n"


# --- The browser application ships with the archive ---------------------------


@pytest.mark.integration
def test_the_final_image_needs_no_node_runtime(compose_stack: None) -> None:  # noqa: ARG001
    """Node builds the page. It does not run it.

    A package manager, a bundler and a `node_modules` tree in a runtime image
    are an attack surface shipped to save a build stage.
    """
    missing = _compose(
        "exec", "-T", CONTAINER, "sh", "-c", "command -v node || echo absent"
    ).stdout.decode()

    assert missing.strip() == "absent"
    assert "node_modules" not in _exec("sh", "-c", "ls -A /app")


@pytest.mark.integration
@pytest.mark.persistence
def test_a_users_own_corrections_survive_replacing_the_container(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """A rename is data, and data lives in the volume rather than in a process.

    The other half of the promise is that it never edited the file: the source
    title is still what the document said, and resetting hands the display back
    to it.
    """
    _import_the_reference()
    track_id = httpx2.get(TRACKS_URL, timeout=10.0).json()["tracks"][0]["id"]
    metadata_url = f"{BASE_URL}/api/v1/tracks/{track_id}/metadata"

    renamed = httpx2.patch(
        metadata_url, json={"title": "Sunday around the lake"}, timeout=10.0
    ).json()
    assert renamed["title"] == "Sunday around the lake"
    assert renamed["metadata"]["source_title"] == "Synthetic morning walk"

    _compose("up", "-d", "--force-recreate")
    _wait_until_healthy()

    after = httpx2.get(f"{BASE_URL}/api/v1/tracks/{track_id}", timeout=10.0).json()
    assert after["title"] == "Sunday around the lake"
    assert after["metadata"]["source_title"] == "Synthetic morning walk"

    reset = httpx2.patch(metadata_url, json={"title": None}, timeout=10.0).json()
    assert reset["title"] == "Synthetic morning walk"


@pytest.mark.integration
def test_every_container_response_carries_the_security_headers(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """The headers are middleware, so the served page carries them too."""
    page = httpx2.get(f"{BASE_URL}/", timeout=10.0)

    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


@pytest.mark.integration
def test_the_image_ships_the_third_party_notices(compose_stack: None) -> None:  # noqa: ARG001
    """The attribution and licence statements travel with what they describe.

    An operator who pulls an image and never sees this repository is exactly who
    the notices are for. A document that stays behind in a Git tree is not a
    notice, it is a note to the people who already knew.
    """
    notices = _exec("cat", "/app/THIRD_PARTY_NOTICES.md")

    assert "# Third-party notices" in notices
    assert "maplibre-gl" in notices
    assert "GPX-View itself" in notices


@pytest.mark.integration
def test_the_image_states_the_version_it_was_built_from(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """One version, readable without starting anything.

    The label and the running application answer the same question, so an
    operator reporting a problem and a registry listing an image cannot disagree
    about which release it is.
    """
    from gpx_view import __version__

    labelled = _compose("images", "--format", "json", CONTAINER).stdout.decode()
    running = _exec("python", "-c", "from gpx_view import __version__; print(__version__)").strip()

    assert running == __version__
    assert labelled  # the image exists under this project
    inspected = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{{ index .Config.Labels "org.opencontainers.image.version" }}',
            "gpx-view:dev",
        ],
        capture_output=True,
        check=True,
    ).stdout.decode()
    assert inspected.strip() == __version__


@pytest.mark.integration
def test_the_container_serves_the_browser_application(compose_stack: None) -> None:  # noqa: ARG001
    """One origin: the page and the data it reads come from one process."""
    page = httpx2.get(f"{BASE_URL}/", timeout=10.0)

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert '<div id="root">' in page.text
    # The page is served exactly as the build produced it. It used to carry an
    # injected script holding a basemap style URL; offline map packages replaced
    # that configuration, and its removal is what lets the content security
    # policy say `script-src 'self'`.
    assert "__GPX_VIEW__" not in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]


@pytest.mark.integration
def test_a_deep_link_into_the_container_survives_a_refresh(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """The single most common way a single-page application ships broken."""
    _import_the_reference()
    track_id = httpx2.get(TRACKS_URL, timeout=10.0).json()["tracks"][0]["id"]

    response = httpx2.get(f"{BASE_URL}/tracks/{track_id}", timeout=10.0)

    assert response.status_code == 200
    assert '<div id="root">' in response.text
    assert httpx2.get(f"{BASE_URL}/api/v1/tracks/{track_id}", timeout=10.0).status_code == 200


@pytest.mark.integration
def test_the_container_serves_its_hashed_assets_cacheably(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """A release arrives because the page is fresh and its assets are new files."""
    listing = _exec("sh", "-c", "ls /app/web/assets").split()
    script = next(name for name in listing if name.endswith(".js"))

    asset = httpx2.get(f"{BASE_URL}/assets/{script}", timeout=10.0)
    page = httpx2.get(f"{BASE_URL}/", timeout=10.0)

    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]
    assert "no-cache" in page.headers["cache-control"]


@pytest.mark.integration
@pytest.mark.maps
def test_an_unreachable_map_provider_leaves_the_archive_healthy(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """A map provider is somebody else's service. The archive is not.

    Nothing in the container reaches one while it serves, so nothing in the
    container can be made unhealthy by one being down. The map manager says the
    catalog is unavailable; every installed map and the whole archive carry on.
    """
    assert httpx2.get(HEALTH_URL, timeout=5.0).json() == {"status": "ok"}
    assert httpx2.get(TRACKS_URL, timeout=10.0).status_code == 200
    assert httpx2.get(MAPS_URL, timeout=10.0).status_code == 200


@pytest.mark.integration
@pytest.mark.maps
def test_the_image_carries_the_glyphs_the_map_draws_labels_with(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """A label rendered from a font service is a label that leaves the deployment.

    So the glyph ranges are in the image, served from the same origin as
    everything else, and this is the check that a build has not dropped them.
    """
    listing = _exec("sh", "-c", "ls '/app/web/fonts/Noto Sans Regular'").split()

    assert "0-255.pbf" in listing
    response = httpx2.get(f"{BASE_URL}/fonts/Noto%20Sans%20Regular/0-255.pbf", timeout=10.0)
    assert response.status_code == 200
    assert len(response.content) > 10_000


@pytest.mark.integration
@pytest.mark.maps
def test_the_image_ships_no_map_data(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """One image, everywhere. What a deployment installed is in its volume.

    A regional package is hundreds of megabytes and is somebody's choice of
    where they walk. Baking one in would make the image both enormous and a
    statement about its users.
    """
    packages = _exec("sh", "-c", "find /app -name '*.mbtiles' -o -name '*.pmtiles' | head").strip()

    assert packages == ""


@pytest.mark.integration
@pytest.mark.maps
def test_an_installed_map_survives_recreating_the_container(
    compose_stack: None,  # noqa: ARG001
) -> None:
    """A map lives in the volume, is served by the container, and outlives it.

    The package is generated and installed *inside* the container, through the
    real pipeline, so this exercises the managed storage, the inspector, the
    atomic publish and the tile endpoint as the image actually ships them --
    without downloading anything.
    """
    _exec("sh", "-c", f"cat > {CONTAINER_MAP_SEED}", stdin=_MAP_SEED_SCRIPT.encode())
    _exec("python", CONTAINER_MAP_SEED)

    installed = httpx2.get(MAPS_URL, timeout=10.0).json()
    assert [entry["state"] for entry in installed["maps"]] == ["installed"]
    delivery = installed["maps"][0]["delivery_id"]
    assert installed["maps"][0]["attribution"]["required_text"].startswith("Map data")

    tile = httpx2.get(f"{MAPS_URL}/tiles/{delivery}/2/2/1.mvt", timeout=10.0)
    assert tile.status_code in {200, 204}
    if tile.status_code == 200:
        assert tile.headers["content-type"] == "application/vnd.mapbox-vector-tile"

    _compose("up", "-d", "--force-recreate")
    _wait_until_healthy()

    after = httpx2.get(MAPS_URL, timeout=10.0).json()
    assert after["maps"][0]["delivery_id"] == delivery
    assert after["maps"][0]["state"] == "installed"

    removed = httpx2.request(
        "DELETE", f"{MAPS_URL}/regions/{after['maps'][0]['region_id']}", timeout=20.0
    )
    assert removed.status_code == 204
    assert httpx2.get(MAPS_URL, timeout=10.0).json()["maps"] == []
    assert httpx2.get(TRACKS_URL, timeout=10.0).status_code == 200
