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

pytestmark = pytest.mark.docker


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
    assert before["count"] == 1
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
