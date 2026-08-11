"""Shared test fixtures.

Every fixture here keeps the suite off the real data directory: applications are
composed against ``tmp_path``, so no test writes into ``./data``, ``/data`` or a
developer's home directory.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.config import Settings
from gpx_view.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Yield a test client backed by a freshly composed application."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client
