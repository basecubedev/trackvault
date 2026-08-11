"""Shared test fixtures.

Every fixture here keeps the suite off the real data directory: applications are
composed against ``tmp_path``, so no test writes into ``./data``, ``/data`` or a
developer's home directory.

It also keeps the suite off the *built frontend*. Whether ``web/dist`` happens
to exist depends on whether somebody ran a build, and a test that behaves
differently on two machines for that reason is a flaky test waiting for a bad
afternoon. The browser assets are pointed somewhere that does not exist by
default; the tests that are about serving them say so explicitly.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.config import Settings
from gpx_view.main import create_app


@pytest.fixture(autouse=True)
def _without_browser_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Compose against an archive that ships no built browser application."""
    monkeypatch.setenv("GPX_VIEW_WEB_DIR", str(tmp_path / "no-such-build"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Yield a test client backed by a freshly composed application."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
