"""Shared test fixtures."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from gpx_view.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Yield a test client backed by a freshly composed application."""
    with TestClient(create_app()) as test_client:
        yield test_client
