"""Composition root.

This module wires the application together and exposes the ASGI app. It is a
bootstrap only -- no business rule may be implemented here.
"""

from fastapi import FastAPI

from gpx_view import __version__
from gpx_view.api.health import router as health_router


def create_app() -> FastAPI:
    """Build the FastAPI application with all HTTP routers attached.

    Returns:
        A fully wired FastAPI application.
    """
    app = FastAPI(
        title="GPX-View",
        version=__version__,
        summary="Manage, analyse and visualise planned and recorded GPS tracks.",
    )
    app.include_router(health_router)
    return app


app = create_app()
