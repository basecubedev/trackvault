"""Browsing what a provider offers, without making it a runtime dependency.

> The catalog is a cache. Reading a track never touches it.

Two properties matter more than the browsing itself.

**Provider downtime must not look like data loss.** An installed map keeps
working while its provider's DNS is broken, and the map manager says so instead
of showing an empty list. The last catalog that was read successfully is
therefore kept, and it is what a reader sees when the network is gone.

**Availability is probed, not assumed.** The provider's own index lists every
region it holds *data* for; whether a ready-made package exists is a second
question with a second answer, and 199 of 555 regions answer no. Guessing would
mean offering a download that 404s. So the regions a reader is actually looking
at are resolved, the answers are cached beside the index, and a region nobody
asked about is reported as unknown rather than as available.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from gpx_view.application.maps.errors import MapErrorCode, MapOperationError
from gpx_view.application.maps.ports import (
    CachedCatalog,
    CatalogRegion,
    MapCatalogCache,
    MapPackageProvider,
    RemotePackage,
)
from gpx_view.application.ports import Clock
from gpx_view.domain.maps import MapRegionId


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One row of the catalog as a reader sees it.

    Attributes:
        region_id: The provider-scoped identity.
        name: The provider's display name.
        parent_id: The region one level up.
        has_children: Whether this region can be opened further.
        package: What is known about a downloadable package, or ``None`` when
            the provider offers none.
        availability_known: Whether the package question was answered at all.
            ``False`` means the provider could not be asked, which is a
            different statement from "there is none" and is rendered as one.
    """

    region_id: MapRegionId
    name: str
    parent_id: MapRegionId | None
    has_children: bool
    package: RemotePackage | None
    availability_known: bool


@dataclass(frozen=True, slots=True)
class CatalogPage:
    """The children of one node of the catalog tree.

    Attributes:
        parent_id: Which node was opened, or ``None`` for the root.
        entries: Its children, in display order.
        provider: The provider's slug.
        provider_name: The provider's display name.
        provider_available: Whether the provider answered during this request.
            ``False`` with entries present means these came from the cache.
        fetched_at_iso: When the cached index was last read successfully.
    """

    parent_id: MapRegionId | None
    entries: tuple[CatalogEntry, ...]
    provider: str
    provider_name: str
    provider_available: bool
    fetched_at_iso: str | None


class GetMapCatalog:
    """Answers "what can I install?", from the provider or from the last answer.

    Args:
        provider: The catalog adapter.
        cache: Where the last good catalog is kept.
        clock: Injected, so a cached timestamp is a fact rather than a race.
        probe_limit: How many regions one page may resolve against the provider.
            A bound rather than a preference: opening "United States" would
            otherwise fire fifty requests at somebody's server because a reader
            clicked a triangle.
    """

    def __init__(
        self,
        provider: MapPackageProvider,
        cache: MapCatalogCache,
        clock: Clock,
        *,
        probe_limit: int = 64,
    ) -> None:
        """Wire the catalog query to its provider and its cache."""
        self._provider = provider
        self._cache = cache
        self._clock = clock
        self._probe_limit = probe_limit

    def refresh(self) -> CachedCatalog:
        """Re-read the provider's index and replace the cache.

        Raises:
            MapOperationError: ``map_provider_unavailable`` when the provider
                cannot be reached, ``map_catalog_invalid`` when it answers with
                something unreadable. Neither touches the cache, so a bad answer
                cannot destroy a good one.
        """
        regions = self._provider.regions()
        previous = self._cache.read(self._provider.slug)
        catalog = CachedCatalog(
            provider=self._provider.slug,
            fetched_at=self._clock.now(),
            regions=regions,
            # Availability answers survive a refresh. They are about the same
            # regions and re-probing every one of them would be the request
            # storm the probe limit exists to prevent.
            availability=dict(previous.availability) if previous else {},
        )
        self._cache.write(catalog)
        return catalog

    def children_of(self, parent_id: MapRegionId | None) -> CatalogPage:
        """Return one node's children, resolving what they cost to install."""
        catalog = self._cached_or_refreshed()
        if catalog is None:
            raise MapOperationError(MapErrorCode.MAP_PROVIDER_UNAVAILABLE)

        children = [region for region in catalog.regions if region.parent_id == parent_id]
        children.sort(key=lambda region: region.name.casefold())
        parents = {region.parent_id for region in catalog.regions if region.parent_id is not None}

        availability, reachable = self._availability_for(catalog, children)
        if reachable and availability != catalog.availability:
            self._cache.write(
                CachedCatalog(
                    provider=catalog.provider,
                    fetched_at=catalog.fetched_at,
                    regions=catalog.regions,
                    availability=availability,
                )
            )

        return CatalogPage(
            parent_id=parent_id,
            entries=tuple(
                CatalogEntry(
                    region_id=region.region_id,
                    name=region.name,
                    parent_id=region.parent_id,
                    has_children=region.region_id in parents,
                    package=availability.get(str(region.region_id)),
                    availability_known=str(region.region_id) in availability,
                )
                for region in children
            ),
            provider=self._provider.slug,
            provider_name=self._provider.display_name,
            provider_available=reachable,
            fetched_at_iso=catalog.fetched_at.isoformat(),
        )

    def region(self, region_id: MapRegionId) -> CatalogRegion:
        """Return one catalog region.

        Raises:
            MapOperationError: ``map_region_unknown`` when the catalog holds no
                such region. An install starts here, so an identity that never
                came from a catalog never reaches a provider adapter.
        """
        catalog = self._cached_or_refreshed()
        if catalog is None:
            raise MapOperationError(MapErrorCode.MAP_PROVIDER_UNAVAILABLE)
        for region in catalog.regions:
            if region.region_id == region_id:
                return region
        raise MapOperationError(MapErrorCode.MAP_REGION_UNKNOWN)

    def _cached_or_refreshed(self) -> CachedCatalog | None:
        """Return the cached catalog, reading the provider only if there is none."""
        cached = self._cache.read(self._provider.slug)
        if cached is not None:
            return cached
        try:
            return self.refresh()
        except MapOperationError:
            return None

    def _availability_for(
        self, catalog: CachedCatalog, children: Sequence[CatalogRegion]
    ) -> tuple[dict[str, RemotePackage | None], bool]:
        """Resolve the children this page shows, reusing what is already known.

        Returns:
            The availability map and whether the provider answered. A provider
            that goes away mid-page leaves the answers already collected in
            place: they were true when they were read, and discarding them would
            turn one failed request into an empty page.
        """
        availability = dict(catalog.availability)
        reachable = True
        probed = 0
        for region in children:
            key = str(region.region_id)
            if key in availability:
                continue
            if probed >= self._probe_limit:
                break
            probed += 1
            try:
                availability[key] = self._provider.resolve(region.region_id)
            except MapOperationError:
                reachable = False
                break
        return availability, reachable
