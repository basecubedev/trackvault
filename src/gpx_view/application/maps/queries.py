"""Reading what is installed, and deciding what belongs behind a track.

Everything here is a read. None of it reaches the network, which is the
property that makes "viewing a track sends nothing anywhere" true rather than
merely intended: there is no code path from a track page to a provider.
"""

from dataclasses import dataclass

from gpx_view.application.maps.errors import MapErrorCode, MapOperationError
from gpx_view.application.maps.ports import (
    InstalledMap,
    MapInstallJob,
    MapPackageRepository,
    MapPackageStorage,
)
from gpx_view.domain.maps import (
    MapBounds,
    MapInstallState,
    MapPackage,
    MapRegionId,
    select_coverage,
)

COVERAGE_PADDING_DEGREES = 0.05
"""How far outside a track's own extent coverage still has to reach.

Roughly five kilometres. A track that runs along a region's edge is inside the
region; the view around it is not, and a basemap that stops at the edge of the
geometry looks like a rendering fault rather than like the edge of a download.
"""


@dataclass(frozen=True, slots=True)
class MapSource:
    """One package as the browser needs to draw it.

    Attributes:
        region_id: Which region, so the page can name what it is showing.
        region_name: The display name.
        delivery_id: The immutable identity its tiles are served under.
        tile_schema: Which vocabulary its layers speak, so the page can pick a
            style written for it rather than guessing at layer names.
        tile_schema_version: That schema's version.
        bounds: What it covers, so MapLibre does not request tiles outside it.
        min_zoom: Lowest zoom with tiles.
        max_zoom: Highest zoom with tiles.
        package: The full metadata, for attribution and for the manager.
    """

    region_id: MapRegionId
    region_name: str
    delivery_id: str
    tile_schema: str
    tile_schema_version: str
    bounds: MapBounds
    min_zoom: int
    max_zoom: int
    package: MapPackage


@dataclass(frozen=True, slots=True)
class MapCoverage:
    """What to draw behind one rectangle.

    Attributes:
        sources: The packages to stack, most specific first. Empty is a normal
            answer and means "draw the track on a neutral background".
        requested: The rectangle that was asked about, before padding.
    """

    sources: tuple[MapSource, ...]
    requested: MapBounds

    @property
    def has_basemap(self) -> bool:
        """Report whether anything at all will be drawn behind the track."""
        return bool(self.sources)


class ListInstalledMaps:
    """What is installed, and whether the archive can still prove it.

    > A row is not an installation.

    The database says a package is installed; the managed file is what makes
    that true. Checking only the row means a deployment that lost its volume
    keeps offering a map that answers every tile with nothing, which looks
    exactly like a rendering bug. So the file is checked, and a row without one
    reports `INVALID` -- a state with its own word and its own instruction.

    The check is existence, not a re-hash. Hashing a gigabyte on every page load
    would make the map manager cost more than the map; the digest is verified
    when the package is published and whenever a tile read finds something it
    cannot parse.
    """

    def __init__(self, repository: MapPackageRepository, storage: MapPackageStorage) -> None:
        """Wire the listing to the repository and the storage."""
        self._repository = repository
        self._storage = storage

    def all(self) -> tuple[InstalledMap, ...]:
        """Return every installed package with its current state."""
        return tuple(
            InstalledMap(package=package, state=self._state_of(package))
            for package in sorted(
                self._repository.installed_packages(),
                key=lambda package: package.region_name.casefold(),
            )
        )

    def usable(self) -> tuple[MapPackage, ...]:
        """Return only the packages a map may actually be drawn from."""
        return tuple(
            installed.package
            for installed in self.all()
            if installed.state is MapInstallState.INSTALLED
        )

    def _state_of(self, package: MapPackage) -> MapInstallState:
        """Return what a stored package currently amounts to."""
        if not self._storage.exists(package.region_id, package.content_sha256):
            return MapInstallState.INVALID
        return MapInstallState.INSTALLED


class SelectMapCoverage:
    """The one authority for which installed maps belong behind a rectangle.

    Called by the track page and by nothing else. It reads the installed
    packages and the domain's selection rules, and it never touches a provider
    -- which is what makes an offline track view offline.
    """

    def __init__(self, installed: ListInstalledMaps) -> None:
        """Wire coverage selection to the installed-package listing."""
        self._installed = installed

    def for_bounds(self, bounds: MapBounds) -> MapCoverage:
        """Return the packages to draw behind ``bounds``."""
        padded = bounds.padded(COVERAGE_PADDING_DEGREES)
        chosen = select_coverage(padded, self._installed.usable())
        return MapCoverage(
            sources=tuple(_source_of(package) for package in chosen),
            requested=bounds,
        )


class GetMapInstallJob:
    """Reading the state of one installation."""

    def __init__(self, repository: MapPackageRepository) -> None:
        """Wire the job query to the repository."""
        self._repository = repository

    def by_id(self, job_id: str) -> MapInstallJob:
        """Return one job.

        Raises:
            MapOperationError: ``map_job_not_found``.
        """
        job = self._repository.job(job_id)
        if job is None:
            raise MapOperationError(MapErrorCode.MAP_JOB_NOT_FOUND)
        return job

    def active(self) -> tuple[MapInstallJob, ...]:
        """Return every unfinished job, so the manager can show them all."""
        return self._repository.active_jobs()


def _source_of(package: MapPackage) -> MapSource:
    """Project an installed package into what the browser needs to draw it."""
    return MapSource(
        region_id=package.region_id,
        region_name=package.region_name,
        delivery_id=package.delivery_id,
        tile_schema=package.tile_schema.name,
        tile_schema_version=package.tile_schema.version,
        bounds=package.bounds,
        min_zoom=package.min_zoom,
        max_zoom=package.max_zoom,
        package=package,
    )
