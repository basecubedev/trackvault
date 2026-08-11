"""Composition root.

This module wires the application together and exposes the ASGI app. It is a
bootstrap only -- no business rule may be implemented here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gpx_view import __version__
from gpx_view.api.health import router as health_router
from gpx_view.api.tracks import router as tracks_router
from gpx_view.application.track_queries import TrackQueries
from gpx_view.config import Settings, get_settings
from gpx_view.infrastructure.assembly import build_services
from gpx_view.infrastructure.clock import SystemClock


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with all HTTP routers attached.

    Args:
        settings: Configuration to compose against. Defaults to the process-wide
            settings; tests pass their own so nothing touches a real data
            directory.

    Returns:
        A fully wired FastAPI application. The data directory is prepared when
        the application starts, not when it is composed, so importing this module
        writes nothing.
    """
    services = build_services(settings or get_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Bring storage up to the current schema before serving requests."""
        services.prepare_storage()
        app.state.track_queries = TrackQueries(services.store, SystemClock())
        yield

    app = FastAPI(
        title="GPX-View",
        version=__version__,
        summary="Manage, analyse and visualise planned and recorded GPS tracks.",
        lifespan=lifespan,
    )
    app.state.services = services
    app.include_router(health_router)
    app.include_router(tracks_router)
    return app


app = create_app()
