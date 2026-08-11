"""Docker smoke test for the container image and the compose stack.

Opt-in: ``uv run pytest -m docker``. The default suite excludes this module so the
test suite stays runnable without a Docker daemon.

The test uses its own compose project name and always tears the stack down again,
so it never leaves containers behind.
"""

import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PROJECT = "gpx-view-smoke"
HEALTH_URL = "http://127.0.0.1:8080/healthz"
STARTUP_TIMEOUT_SECONDS = 90.0
POLL_INTERVAL_SECONDS = 1.0

pytestmark = pytest.mark.docker


def _docker_is_available() -> bool:
    """Report whether a usable Docker daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


def _compose(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    """Run a ``docker compose`` command for the smoke test project."""
    return subprocess.run(
        ["docker", "compose", "-p", COMPOSE_PROJECT, *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    )


@pytest.fixture(scope="module")
def compose_stack() -> Iterator[None]:
    """Build and start the compose stack, and always shut it down again."""
    if not _docker_is_available():
        pytest.skip("no usable Docker daemon")

    (PROJECT_ROOT / "data").mkdir(exist_ok=True)
    _compose("build")
    _compose("up", "-d")
    try:
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
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            response = httpx2.get(HEALTH_URL, timeout=5.0)
        except httpx2.HTTPError as error:
            last_error = error
        else:
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            return
        time.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail(f"container did not become healthy within {STARTUP_TIMEOUT_SECONDS}s: {last_error}")


@pytest.mark.integration
def test_container_runs_as_non_root(compose_stack: None) -> None:  # noqa: ARG001
    """The runtime container must not execute the application as root."""
    result = _compose("exec", "-T", "gpx-view", "id", "-u")

    assert result.stdout.decode().strip() != "0"
