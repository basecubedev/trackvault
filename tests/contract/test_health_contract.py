"""HTTP contract of the health endpoint."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.contract
def test_healthz_returns_ok_status(client: TestClient) -> None:
    """``GET /healthz`` answers 200 with exactly ``{"status": "ok"}``."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok"}


@pytest.mark.contract
def test_healthz_is_published_in_the_openapi_schema(client: TestClient) -> None:
    """The health endpoint is part of the documented API surface."""
    schema = client.get("/openapi.json").json()

    assert "/healthz" in schema["paths"]
    assert "get" in schema["paths"]["/healthz"]


@pytest.mark.contract
def test_no_track_endpoints_exist_yet(client: TestClient) -> None:
    """The foundation exposes no business endpoints; features come with contracts."""
    paths = set(client.get("/openapi.json").json()["paths"])

    assert paths == {"/healthz"}
