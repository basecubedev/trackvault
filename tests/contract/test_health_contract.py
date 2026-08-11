"""HTTP contract of the health endpoint.

The published API surface as a whole is pinned in
``tests/contract/test_track_api_contract.py``.
"""

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
