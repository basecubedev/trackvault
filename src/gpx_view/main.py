"""Composition root.

This module wires the application together and exposes the ASGI app. It is a
bootstrap only -- no business rule may be implemented here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gpx_view import __version__
from gpx_view.api.health import router as health_router
from gpx_view.api.maps import router as maps_router
from gpx_view.api.security import apply_security_headers
from gpx_view.api.statistics import router as statistics_router
from gpx_view.api.system import router as system_router
from gpx_view.api.tracks import router as tracks_router
from gpx_view.api.web import mount_web_application
from gpx_view.application.analysis import GetTrackAnalysis
from gpx_view.application.statistics import (
    GetAvailableYears,
    GetMonthlyStatistics,
    GetYearStatistics,
)
from gpx_view.application.system import GetSystemInfo
from gpx_view.application.track_profile import GetTrackGeometry, GetTrackProfile
from gpx_view.application.track_queries import TrackQueries
from gpx_view.config import Settings, get_settings
from gpx_view.infrastructure.assembly import build_services
from gpx_view.infrastructure.clock import SystemClock
from gpx_view.infrastructure.database import SCHEMA_VERSION


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
        app.state.track_queries = TrackQueries(
            services.store,
            SystemClock(),
            services.settings.timezone,
            # The same currency authority the analysis resource and the totals
            # ask, so a listing row cannot call current what a detail view calls
            # stale.
            services.analyze.installed,
        )
        # The series and the aggregates come from one analysis authority, so a
        # chart and the figure beside it cannot be two different calculations.
        app.state.track_profile = GetTrackProfile(
            repository=services.store, analysis=services.analyze.installed
        )
        app.state.track_geometry = GetTrackGeometry(repository=services.store)
        app.state.track_analysis = GetTrackAnalysis(
            repository=services.store,
            # The same authority the command line and the batch selection use,
            # so a track the API calls current is one `analyze --outdated`
            # leaves alone.
            analysis=services.analyze.installed,
        )
        # Statistics leave out exactly what `analyze --outdated` picks up,
        # because both ask this one object. Two authorities would let a
        # total quietly include what a batch run considers stale.
        app.state.year_statistics = GetYearStatistics(
            repository=services.store,
            timezone=services.settings.timezone,
            analysis=services.analyze.installed,
        )
        app.state.monthly_statistics = GetMonthlyStatistics(
            repository=services.store,
            timezone=services.settings.timezone,
            analysis=services.analyze.installed,
        )
        # Which years exist is drawn in the same zone as the totals that fill
        # them, so a year the interface offers is a year the archive agrees it
        # has something for.
        app.state.available_years = GetAvailableYears(
            repository=services.store, timezone=services.settings.timezone
        )
        # The two currency authorities themselves, so "which algorithms produced
        # this number?" is answerable from the deployment rather than from a
        # changelog somebody has to find.
        app.state.system_info = GetSystemInfo(
            version=__version__,
            schema_version=SCHEMA_VERSION,
            timezone=services.settings.timezone,
            processing=services.processing,
            analysis=services.analyze.installed,
        )
        # The map capability is wired service by service rather than as one
        # object: the API layer may not name an infrastructure type, and a bag
        # holding several of them would be exactly that.
        app.state.maps_enabled = services.maps.enabled
        app.state.map_catalog = services.maps.catalog
        app.state.map_installed = services.maps.installed
        # One coverage authority. The track page asks this and nothing else,
        # which is what makes viewing a track reach no network.
        app.state.map_coverage = services.maps.coverage
        app.state.map_installer = services.maps.jobs
        app.state.map_job_query = services.maps.job_query
        app.state.map_remove = services.maps.remove
        app.state.map_repository = services.maps.repository
        app.state.map_tiles = services.maps.tiles
        yield
        services.maps.jobs.shutdown()

    app = FastAPI(
        title="GPX-View",
        version=__version__,
        summary="Manage, analyse and visualise planned and recorded GPS tracks.",
        lifespan=lifespan,
    )
    app.state.services = services
    apply_security_headers(app)
    app.include_router(health_router)
    app.include_router(system_router)
    app.include_router(tracks_router)
    app.include_router(statistics_router)
    app.include_router(maps_router)
    # Mounted last, because its catch-all route must never shadow an endpoint.
    # A build that ships without browser assets serves the API and nothing else,
    # which is exactly what a development run and a test want.
    app.state.web_mounted = mount_web_application(app, (settings or services.settings).web_dir)
    return app


app = create_app()
