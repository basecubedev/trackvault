"""Health endpoint.

The liveness probe proves that the package, the application bootstrap, FastAPI,
the test suite and the container image all fit together. It intentionally checks
nothing else -- there are no dependencies to report on yet.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Response body of ``GET /healthz``."""

    status: Literal["ok"] = "ok"


@router.get("/healthz", summary="Liveness probe")
def read_health() -> HealthResponse:
    """Report that the application process is able to serve requests."""
    return HealthResponse()
