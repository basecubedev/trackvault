"""The ports the map capability reaches the outside world through.

Four, and the split is deliberate:

```
MapPackageProvider    a catalog and a transfer            knows URLs, never paths
MapPackageStorage     the managed area under the data dir knows paths, never URLs
MapPackageInspector   what a downloaded container is      knows a file format
MapPackageRepository  installed packages and their jobs   knows the database
```

Keeping URLs and paths in different ports is not tidiness. The single worst
failure this feature can have is a caller-supplied string becoming either a
request this server makes or a file this server writes, and a port that cannot
express one of the two cannot be the one that does it.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Protocol

from gpx_view.domain.maps import (
    MapAttribution,
    MapBounds,
    MapInstallState,
    MapJobState,
    MapPackage,
    MapPackageFormat,
    MapRegionId,
    MapTileSchema,
)


@dataclass(frozen=True, slots=True)
class CatalogRegion:
    """One region a provider's catalog offers, before anything is known about it.

    Attributes:
        region_id: The provider-scoped identity.
        name: The provider's display name.
        parent_id: The region one level up, or ``None`` at the catalog root.
        country_code: The ISO 3166-1 alpha-2 code, where the region is a country.
    """

    region_id: MapRegionId
    name: str
    parent_id: MapRegionId | None = None
    country_code: str | None = None


@dataclass(frozen=True, slots=True)
class RemotePackage:
    """A package the provider will serve, as it described it.

    Attributes:
        region_id: Which region it covers.
        url: The address the transfer will read. Resolved by the provider
            adapter and never accepted from a caller.
        size_bytes: What the provider declared, or ``None`` if it declared
            nothing. ``None`` is reported as unknown and never guessed at.
        updated_at: When the provider last built it, if it said.
    """

    region_id: MapRegionId
    url: str
    size_bytes: int | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class InspectedPackage:
    """What a downloaded container turned out to be.

    Attributes:
        format: The container.
        tile_schema: The vector tile vocabulary its tiles speak.
        bounds: The rectangle it holds data for.
        min_zoom: Lowest zoom level with tiles.
        max_zoom: Highest zoom level with tiles.
        tile_count: How many tiles it holds. Zero is a refusal.
        attribution: Who made it and under what licence, read from the file.
        dataset_version: The version string the package declares, if any.
    """

    format: MapPackageFormat
    tile_schema: MapTileSchema
    bounds: MapBounds
    min_zoom: int
    max_zoom: int
    tile_count: int
    attribution: MapAttribution
    dataset_version: str | None = None


@dataclass(frozen=True, slots=True)
class TransferOutcome:
    """What a completed transfer produced.

    Attributes:
        content_sha256: The digest, computed while the bytes streamed past. A
            second full read only to hash would double the cost of every
            install for nothing.
        size_bytes: How many bytes arrived.
    """

    content_sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class MapInstallJob:
    """An installation in flight, or the record of one that finished.

    Attributes:
        job_id: The identity a caller polls.
        region_id: Which region it is about.
        region_name: The display name, so a job page needs no catalog.
        state: Where it has got to.
        bytes_downloaded: How much has arrived.
        bytes_total: How much is expected, or ``None`` when the provider
            declared nothing. A percentage is only ever derived from a real
            total -- a made-up one is worse than a byte count.
        started_at: When it was queued.
        updated_at: When its state last changed.
        error_code: Why it failed, for a job that did.
        is_update: Whether a package was already installed when it started.
    """

    job_id: str
    region_id: MapRegionId
    region_name: str
    state: MapJobState
    bytes_downloaded: int
    bytes_total: int | None
    started_at: datetime
    updated_at: datetime
    error_code: str | None = None
    is_update: bool = False

    @property
    def percentage(self) -> int | None:
        """Return progress as whole percent, or ``None`` if the total is unknown."""
        if not self.bytes_total:
            return None
        return min(100, int(self.bytes_downloaded * 100 / self.bytes_total))


@dataclass(frozen=True, slots=True)
class InstalledMap:
    """An installed package and what the archive currently thinks of it.

    Attributes:
        package: The stored metadata.
        state: ``INSTALLED`` only when a managed file backs the row and hashes
            to what the row says. Anything else is ``INVALID``.
    """

    package: MapPackage
    state: MapInstallState


class MapPackageProvider(Protocol):
    """A catalog of regions and the transfers that fetch them.

    The application never learns a URL through this port -- it hands over a
    region identity and receives bytes. That is what makes "a caller cannot
    choose the host this server talks to" a structural property.
    """

    @property
    def slug(self) -> str:
        """Return the provider's catalog slug, matching the region identities."""
        ...

    @property
    def display_name(self) -> str:
        """Return the provider's name as a reader should see it."""
        ...

    def regions(self) -> tuple[CatalogRegion, ...]:
        """Fetch the provider's region hierarchy. Reaches the network."""
        ...

    def resolve(self, region_id: MapRegionId) -> RemotePackage | None:
        """Ask whether a package exists for a region, and how big it is.

        Returns:
            The package description, or ``None`` when the provider offers none
            for this region. Reaches the network.
        """
        ...

    def download(
        self,
        package: RemotePackage,
        sink: BinaryIO,
        *,
        max_bytes: int,
        on_progress: Callable[[int], None],
        is_cancelled: Callable[[], bool],
    ) -> TransferOutcome:
        """Stream a package into ``sink``, hashing as it goes.

        Args:
            package: What to fetch.
            sink: An open, writable binary file the storage handed out.
            max_bytes: Hard ceiling. Reaching it aborts the transfer, whatever
                the provider declared beforehand.
            on_progress: Called with the running byte count, throttled by the
                caller rather than per chunk.
            is_cancelled: Polled between chunks.

        Returns:
            The digest and the size.
        """
        ...


class MapDownloadSlot(Protocol):
    """A temporary file a transfer writes into, before anything is published."""

    @property
    def sink(self) -> BinaryIO:
        """Return the open, writable file."""
        ...

    def finish(self) -> None:
        """Flush and fsync, so what is published survives a power cut."""
        ...

    def discard(self) -> None:
        """Remove the temporary file. Safe to call more than once."""
        ...


class MapPackageStorage(Protocol):
    """The managed area under the data directory that holds map packages."""

    def open_download_slot(self, job_id: str) -> MapDownloadSlot:
        """Create a private temporary file for one transfer."""
        ...

    def publish(self, region_id: MapRegionId, content_sha256: str, slot: MapDownloadSlot) -> None:
        """Move a finished, validated download into the managed area."""
        ...

    def remove(self, region_id: MapRegionId, content_sha256: str) -> None:
        """Delete one managed package file. A missing file is not an error."""
        ...

    def remove_region(self, region_id: MapRegionId) -> None:
        """Delete everything managed for a region."""
        ...

    def is_intact(self, region_id: MapRegionId, content_sha256: str) -> bool:
        """Report whether the managed file exists and hashes to its name."""
        ...

    def exists(self, region_id: MapRegionId, content_sha256: str) -> bool:
        """Report whether the managed file exists, without reading it."""
        ...

    def free_bytes(self) -> int:
        """Return the space available where packages are stored."""
        ...

    def discard_partial_downloads(self) -> int:
        """Remove leftover temporary files. Returns how many were removed."""
        ...

    def discard_orphans(self, keep: Sequence[tuple[MapRegionId, str]]) -> int:
        """Remove managed files no installed package claims.

        A crash between publishing the file and committing the row leaves a
        valid file nothing points at. It is not promoted to an authority on the
        next start -- a file whose row never committed was never installed --
        so it is removed and the region reinstalls cleanly.
        """
        ...


class MapPackageInspector(Protocol):
    """Reads a downloaded container and says what it is, or refuses it."""

    def inspect(self, region_id: MapRegionId, content_sha256: str) -> InspectedPackage:
        """Validate a published file and extract its metadata.

        Raises:
            MapOperationError: With a named code when the file is not a package
                this build can serve.
        """
        ...


class MapPackageRepository(Protocol):
    """Installed packages and the jobs that installed them."""

    def installed_packages(self) -> tuple[MapPackage, ...]:
        """Return every installed package."""
        ...

    def installed_package(self, region_id: MapRegionId) -> MapPackage | None:
        """Return one region's installed package, if there is one."""
        ...

    def package_by_delivery_id(self, delivery_id: str) -> MapPackage | None:
        """Return the package a delivery identity names, if any."""
        ...

    def save_package(self, package: MapPackage) -> None:
        """Publish a package's metadata, replacing any earlier one atomically."""
        ...

    def delete_package(self, region_id: MapRegionId) -> None:
        """Remove a package's metadata."""
        ...

    def create_job(self, job: MapInstallJob) -> None:
        """Record a queued job."""
        ...

    def update_job(self, job: MapInstallJob) -> None:
        """Record a job's new state."""
        ...

    def job(self, job_id: str) -> MapInstallJob | None:
        """Return one job."""
        ...

    def active_job_for(self, region_id: MapRegionId) -> MapInstallJob | None:
        """Return the unfinished job for a region, if one exists."""
        ...

    def active_jobs(self) -> tuple[MapInstallJob, ...]:
        """Return every unfinished job."""
        ...

    def recent_jobs(self, limit: int = 20) -> tuple[MapInstallJob, ...]:
        """Return the newest jobs, finished or not.

        Finished jobs are kept and shown. "Why is this region not installed?"
        is answered by the failed job that says so, and hiding it would make the
        answer "nothing ever happened".
        """
        ...

    def interrupt_unfinished_jobs(self, at: datetime) -> int:
        """Mark every unfinished job interrupted. Returns how many.

        Called at start-up. A job whose process is gone will never progress, and
        leaving it reading ``downloading`` forever means a region that can never
        be installed again.
        """
        ...


class MapInstallationService(Protocol):
    """Queues installations and reports them, off the request thread.

    The API needs to start and cancel work without knowing that a thread pool
    exists, and the application needs to describe that need without owning a
    scheduler. This is that seam.
    """

    def start(self, region_id: MapRegionId) -> MapInstallJob:
        """Queue an install or an update and return its job immediately."""
        ...

    def cancel(self, job_id: str) -> None:
        """Ask a running job to stop at the next chunk boundary."""
        ...


class MapTileSource(Protocol):
    """Reads single tiles out of installed packages."""

    def read(
        self, region_id: MapRegionId, content_sha256: str, zoom: int, column: int, row: int
    ) -> bytes | None:
        """Return one tile's stored bytes, or ``None`` where there is no tile."""
        ...


@dataclass(frozen=True, slots=True)
class CachedCatalog:
    """A provider catalog as it was last successfully read.

    Attributes:
        provider: Whose catalog it is.
        fetched_at: When it was read.
        regions: The hierarchy.
        availability: What is known about which regions have a package, keyed by
            the canonical region identity.
    """

    provider: str
    fetched_at: datetime
    regions: tuple[CatalogRegion, ...]
    availability: dict[str, RemotePackage | None]


class MapCatalogCache(Protocol):
    """Where the last good catalog is kept, so provider downtime is survivable."""

    def read(self, provider: str) -> CachedCatalog | None:
        """Return the cached catalog, or ``None`` if there is none."""
        ...

    def write(self, catalog: CachedCatalog) -> None:
        """Replace the cached catalog atomically."""
        ...
