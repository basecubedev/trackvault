"""Integration test for the application bootstrap.

Proves the chain package -> composition root -> FastAPI -> HTTP without touching
the network, a database or the file system.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gpx_view import __version__
from gpx_view.main import app, create_app


@pytest.mark.integration
def test_create_app_returns_an_independent_application() -> None:
    """Each call composes a fresh application, so tests cannot leak state."""
    first = create_app()
    second = create_app()

    assert isinstance(first, FastAPI)
    assert first is not second


@pytest.mark.integration
def test_module_level_app_is_the_asgi_entry_point() -> None:
    """``gpx_view.main:app`` is what uvicorn and the container serve."""
    assert isinstance(app, FastAPI)
    assert app.title == "GPX-View"
    assert app.version == __version__


@pytest.mark.integration
def test_bootstrapped_app_serves_requests(client: TestClient) -> None:
    """The composed application actually answers HTTP requests."""
    assert client.get("/healthz").status_code == 200
