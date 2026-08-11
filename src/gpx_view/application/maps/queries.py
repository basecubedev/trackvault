"""Reading what is installed, and deciding what belongs behind a track.

Everything here is a read. None of it reaches the network, which is the
property that makes "viewing a track sends nothing anywhere" true rather than
merely intended: there is no code path from a track page to a provider.
"""

from collections.abc import Callable
from dataclasses import dataclass

from gpx_view.application.maps.errors import MapErrorCode, MapOperationError
from gpx_view.application.maps.ports import (
    CatalogRegion,
    InstalledMap,
    MapCatalogCache,
    MapInstallJob,
    MapPackageRepository,
    MapPackageStorage,
    RemotePackage,
)
from gpx_view.domain.maps import (
    MapBounds,
    MapInstallState,
    MapPackage,
    MapRegionId,
    RegionExtent,
    select_coverage,
    suggest_regions,
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


@dataclass(frozen=True, slots=True)
class MapStorageCensus:
    """What the managed map area amounts to, proved rather than believed.

    The counts a diagnosis needs, and they are deliberately three rather than
    two. `installed` and `invalid` are the pair an operator reads, but the two
    ways of being invalid call for opposite actions -- one is reinstalled by a
    person, the other is cleared by the next start -- and a single number would
    have to give one of them the wrong advice.

    Attributes:
        installed: Rows whose managed file exists and hashes to what the row
            says. The only state that is an installation.
        unprovable: Rows whose file is missing, unreadable, or not the bytes its
            digest names. Reinstalling the region fixes these.
        unclaimed: Managed files no installed row claims. Debris from a crash
            between publishing a file and committing its row; start-up recovery
            removes them.
    """

    installed: int
    unprovable: int
    unclaimed: int

    @property
    def invalid(self) -> int:
        """Return everything in the map area that is not an installation.

        Both halves, because both are storage the archive cannot account for.
        A count that omitted the unclaimed files would report a clean map area
        to somebody looking for the disk usage they cannot explain.
        """
        return self.unprovable + self.unclaimed


class ListInstalledMaps:
    """What is installed, and whether the archive can still prove it.

    > A row is not an installation.

    The database says a package is installed; the managed file is what makes
    that true. Checking only the row means a deployment that lost its volume
    keeps offering a map that answers every tile with nothing, which looks
    exactly like a rendering bug. So the file is checked, and a row without one
    reports `INVALID` -- a state with its own word and its own instruction.

    **How hard the file is checked is the one thing that varies here.** A page
    load asks :meth:`all`, which checks existence: hashing a gigabyte to render
    a table would make the map manager cost more than the map. A diagnosis asks
    :meth:`census`, which re-reads the bytes, because catching a file that is no
    longer what it claims to be is the entire reason somebody runs `doctor`. The
    *rule* -- row and matching file, or `INVALID` -- is written once and shared
    by both, so the cheap answer and the thorough one can differ in confidence
    and never in meaning.
    """

    def __init__(self, repository: MapPackageRepository, storage: MapPackageStorage) -> None:
        """Wire the listing to the repository and the storage."""
        self._repository = repository
        self._storage = storage

    def all(self) -> tuple[InstalledMap, ...]:
        """Return every installed package with its current state."""
        return self._listed(self._storage.exists)

    def verified(self) -> tuple[InstalledMap, ...]:
        """Return every installed package, its file re-read rather than found.

        The expensive listing. A package whose bytes were replaced or truncated
        is `INSTALLED` to :meth:`all` and `INVALID` here, which is the whole
        difference between the two.
        """
        return self._listed(self._storage.is_intact)

    def census(self) -> MapStorageCensus:
        """Return what the map area amounts to, files re-read and disk included.

        The one place that looks at the managed directory as well as at the
        database. A row without a healthy file and a file without a row are
        both failures of the same rule, and counting only the first would leave
        the archive unable to report storage nobody can account for.
        """
        verified = self.verified()
        installed = sum(entry.state is MapInstallState.INSTALLED for entry in verified)
        return MapStorageCensus(
            installed=installed,
            unprovable=len(verified) - installed,
            # Every row's file, not only the healthy ones. A file whose bytes are
            # wrong is still a file a row names: it is unprovable, and counting
            # it as unclaimed as well would report one fault twice.
            unclaimed=self._storage.count_orphans(
                [(entry.package.region_id, entry.package.content_sha256) for entry in verified]
            ),
        )

    def usable(self) -> tuple[MapPackage, ...]:
        """Return only the packages a map may actually be drawn from."""
        return tuple(
            installed.package
            for installed in self.all()
            if installed.state is MapInstallState.INSTALLED
        )

    def _listed(self, is_present: Callable[[MapRegionId, str], bool]) -> tuple[InstalledMap, ...]:
        """Return every stored package, judged by how deeply its file is checked."""
        return tuple(
            InstalledMap(
                package=package,
                state=(
                    MapInstallState.INSTALLED
                    if is_present(package.region_id, package.content_sha256)
                    else MapInstallState.INVALID
                ),
            )
            for package in sorted(
                self._repository.installed_packages(),
                key=lambda package: package.region_name.casefold(),
            )
        )


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


MAX_SUGGESTIONS = 4
"""How many regions are offered for a track nothing covers.

Enough that a border case has its alternatives on screen, few enough that the
list is an offer rather than the catalog again. A reader who wants the rest has
the map manager.
"""


@dataclass(frozen=True, slots=True)
class SuggestedRegion:
    """One region that could be installed for a track nothing covers.

    Attributes:
        region_id: What an install would name. Never an address: the adapter
            resolves the host, which is what keeps this server from becoming a
            fetcher operated by whoever can reach it.
        name: The provider's display name.
        ancestry: The names above it, outermost first, so "Limburg" can be told
            from a Limburg in another country before somebody downloads it.
        package: What the provider said a download would cost, if it has been
            asked. ``None`` means nobody has asked yet -- not that there is none.
        availability_known: Whether the package question was answered at all.
    """

    region_id: MapRegionId
    name: str
    ancestry: tuple[str, ...]
    package: RemotePackage | None
    availability_known: bool


class SuggestMapRegions:
    """Which regions could be installed for a rectangle nothing covers.

    > Reading a track must not contact anybody. This is why the catalog *cache*
    > is the dependency and the provider is not.

    The whole capability rests on one fact the archive already has and was not
    using: the provider's index says where each region is, and a track says
    where it went. Putting the two together turns "no offline map is installed
    for this area" from a dead end into an offer.

    What it will not do is decide. A catalog extent is a rectangle around an
    outline, so several regions can contain one track and the smallest is not
    always the right one -- see `gpx_view.domain.maps.suggestion`. It names
    candidates, most specific first, and stops there.

    An archive that has never read a catalog suggests nothing. Reading one is an
    action somebody presses; doing it because a page was opened would be exactly
    the provider request this must never make.
    """

    def __init__(
        self,
        cache: MapCatalogCache,
        installed: ListInstalledMaps,
        *,
        provider: str,
        limit: int = MAX_SUGGESTIONS,
    ) -> None:
        """Wire suggestions to the cached catalog and to what is already here."""
        self._cache = cache
        self._installed = installed
        self._provider = provider
        self._limit = limit

    def catalog_is_known(self) -> bool:
        """Report whether a catalog has ever been read on this deployment.

        "Nothing to suggest" and "nobody has read a catalog yet" are opposite
        instructions to a reader, and one empty list would render as the first.
        """
        return self._cache.read(self._provider) is not None

    def for_bounds(self, bounds: MapBounds) -> tuple[SuggestedRegion, ...]:
        """Return the regions that could hold ``bounds``, most specific first."""
        catalog = self._cache.read(self._provider)
        if catalog is None:
            return ()
        by_id = {region.region_id: region for region in catalog.regions}
        already_here = {package.region_id for package in self._installed.usable()}
        extents = tuple(
            RegionExtent(region_id=region.region_id, bounds=region.bounds)
            for region in catalog.regions
            if region.bounds is not None and region.region_id not in already_here
        )
        chosen = suggest_regions(bounds, extents)[: self._limit]
        return tuple(
            self._described(by_id[region_id], by_id, catalog.availability) for region_id in chosen
        )

    def _described(
        self,
        region: CatalogRegion,
        by_id: dict[MapRegionId, CatalogRegion],
        availability: dict[str, RemotePackage | None],
    ) -> SuggestedRegion:
        """Return one candidate with everything a reader needs to choose it."""
        key = str(region.region_id)
        return SuggestedRegion(
            region_id=region.region_id,
            name=region.name,
            ancestry=_ancestry_of(region, by_id),
            package=availability.get(key),
            availability_known=key in availability,
        )


def _ancestry_of(region: CatalogRegion, by_id: dict[MapRegionId, CatalogRegion]) -> tuple[str, ...]:
    """Return the display names above a region, outermost first."""
    names: list[str] = []
    parent = region.parent_id
    # Bounded by the catalog's own depth, and defensively by the region count:
    # a cached document is untrusted input, and a parent chain that loops would
    # otherwise be an infinite loop in a read.
    for _ in range(len(by_id)):
        if parent is None:
            break
        above = by_id.get(parent)
        if above is None:
            break
        names.append(above.name)
        parent = above.parent_id
    return tuple(reversed(names))


@dataclass(frozen=True, slots=True)
class ApproximateLocation:
    """Roughly where a track was, from the provider's own region rectangles.

    **Approximate is part of the name, because the method cannot be better than
    that.** What is compared is rectangles: the box around a track against the
    box around a region's outline. Inside a country that is right; near a
    border it is not, and it is confidently not -- a walk in Aachen falls inside
    the rectangle around the Dutch province of Limburg, and this will say so.

    The region and the country come from *one* selection: the country is the
    ancestor of the named region that carries an ISO code. They can therefore be
    wrong together but never contradict each other, which "Zeeland, Germany"
    would.

    Attributes:
        regions: The named regions, most specific first. More than one only when
            no single region's rectangle contains the whole track -- a tour long
            enough to leave every one of them.
        countries: The countries those regions belong to, in the same order and
            deduplicated. Several means the track reached across a border far
            enough for no one region to hold it.
    """

    regions: tuple[str, ...]
    countries: tuple["LocatedCountry", ...]


@dataclass(frozen=True, slots=True)
class LocatedCountry:
    """One country a track was approximately in."""

    name: str
    code: str | None


class LocateTracks:
    """Says roughly where a track was, from the catalog this deployment cached.

    > Reading a track must not contact anybody, so this holds the cache and not
    > the provider -- the same arrangement, and for the same reason, as
    > `SuggestMapRegions`.

    An archive that has never read a catalog locates nothing, and says nothing
    rather than guessing from a coordinate.
    """

    def __init__(self, cache: MapCatalogCache, *, provider: str, limit: int = 3) -> None:
        """Wire the lookup to the cached catalog."""
        self._cache = cache
        self._provider = provider
        self._limit = limit

    def for_bounds(self, bounds: MapBounds | None) -> ApproximateLocation | None:
        """Return roughly where ``bounds`` is, or ``None`` when nothing can say."""
        if bounds is None:
            return None
        catalog = self._cache.read(self._provider)
        if catalog is None:
            return None
        by_id = {region.region_id: region for region in catalog.regions}
        extents = tuple(
            RegionExtent(region_id=region.region_id, bounds=region.bounds)
            for region in catalog.regions
            if region.bounds is not None
        )
        chosen = suggest_regions(bounds, extents)[: self._limit]
        # The continent is not where somebody was. It is offered as a fallback
        # for *installing* a map, which is a different question.
        named = [by_id[region_id] for region_id in chosen if _country_of(by_id[region_id], by_id)]
        if not named:
            return None
        countries: list[LocatedCountry] = []
        for region in named:
            country = _country_of(region, by_id)
            if country is not None and all(seen.name != country.name for seen in countries):
                countries.append(country)
        return ApproximateLocation(
            regions=tuple(region.name for region in named),
            countries=tuple(countries),
        )


def _country_of(
    region: CatalogRegion, by_id: dict[MapRegionId, CatalogRegion]
) -> LocatedCountry | None:
    """Return the country a region belongs to, by walking its own ancestry.

    The provider states an ISO code on the country level and nowhere else, so
    the country of a province is found by going up rather than by matching a
    name. A region above every country -- a continent -- belongs to none, and
    saying "Europe" when asked where somebody was is not an answer.
    """
    current: CatalogRegion | None = region
    for _ in range(len(by_id)):
        if current is None:
            return None
        if current.country_code:
            return LocatedCountry(name=current.name, code=current.country_code)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return None


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
