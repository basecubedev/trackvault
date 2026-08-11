"""Installing, updating and removing a regional map package.

The pipeline, and every arrow is a place it can stop without damage:

```
resolve the catalog entry
      ↓
preflight: is there room for the new package *beside* the old one?
      ↓
stream into a temporary file, hashing as the bytes go past
      ↓
already byte-identical?  ->  ALREADY_CURRENT, nothing switches
      ↓
publish the file, then validate what was published
      ↓
commit the row in one transaction
      ↓
delete the package that was current before
```

> A failed update leaves the previous map usable.

That is the contract the ordering exists for. The new package is fetched and
checked *beside* the installed one and never over it, which is also why the
free-space preflight asks for room for both. An installation that runs out of
disk, times out, arrives corrupt or turns out to speak an unknown tile schema
costs a reader nothing except the transfer.

Validation happens after publication rather than before, and that is not a
mistake: the inspector opens a real file in the managed area, which is the same
act the tile endpoint will perform later. Validating a temporary file and then
moving it would leave a gap in which the thing that was checked and the thing
that gets served are two different files. A package that fails validation is
removed again, and the row is never written -- so the file is an orphan for the
length of one function call and nothing points at it in between.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from trackvault.application.maps.catalog import GetMapCatalog
from trackvault.application.maps.errors import MapErrorCode, MapOperationError
from trackvault.application.maps.ports import (
    MapInstallJob,
    MapPackageInspector,
    MapPackageProvider,
    MapPackageRepository,
    MapPackageStorage,
)
from trackvault.application.ports import Clock
from trackvault.domain.maps import MapJobState, MapPackage, MapRegionId

logger = logging.getLogger(__name__)

DEFAULT_MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024 * 1024
"""How large a package may be before the transfer is refused.

Eight gigabytes. Chosen against the real catalog rather than as a round number:
the largest European package is about 5.3 GB and the largest anywhere is about
57 GB, so this admits every country a user is plausibly installing while still
bounding a provider that starts answering with something unexpected. There is
deliberately no "unlimited": an unbounded stream to a file is a way to fill a
disk with one request.
"""

FREE_SPACE_MARGIN_BYTES = 256 * 1024 * 1024
"""Head-room kept free beyond the package itself.

A filesystem that is exactly full is a filesystem where the archive's own
database cannot write its journal, and losing the archive to install a basemap
would be a poor trade.
"""

UNKNOWN_SIZE_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
"""How much room to require when the provider declares no size at all.

A guess, and named as one. It is not used as a progress total and never reaches
a reader -- it exists so that "the provider said nothing" does not silently skip
the disk check entirely.
"""

PROGRESS_INTERVAL_BYTES = 4 * 1024 * 1024
"""How much has to arrive before the job's progress is written again.

Per network chunk would be several hundred database writes per second for the
length of a multi-gigabyte transfer, all of them to say the same thing slightly
later. Four megabytes is roughly one write per second on a fast connection and
one per few seconds on a slow one, which is what a progress bar needs.
"""


class InstallOutcome(StrEnum):
    """What an install or update actually did.

    Attributes:
        INSTALLED: A package is now installed where none was.
        UPDATED: A different package replaced the one that was installed.
        ALREADY_CURRENT: The provider served the same bytes that are already
            installed. Nothing was switched, because switching would have
            replaced a working package with an identical one and invalidated
            every cached tile URL for no reason.
    """

    INSTALLED = "installed"
    UPDATED = "updated"
    ALREADY_CURRENT = "already_current"


@dataclass(frozen=True, slots=True)
class InstallResult:
    """The end of one installation.

    Attributes:
        outcome: What happened.
        package: The package that is installed now.
    """

    outcome: InstallOutcome
    package: MapPackage


class InstallMapPackage:
    """The one authority for putting a map package on this machine.

    Install and update are the same use case. They differ in what was there
    before, which the pipeline reads rather than being told: a second entry
    point would be a second place to get the "keep the old one until the new one
    validates" ordering right, and it would be the one that runs less often.
    """

    def __init__(
        self,
        *,
        provider: MapPackageProvider,
        catalog: GetMapCatalog,
        storage: MapPackageStorage,
        inspector: MapPackageInspector,
        repository: MapPackageRepository,
        clock: Clock,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    ) -> None:
        """Wire the installer to its provider, its storage and its repository."""
        self._provider = provider
        self._catalog = catalog
        self._storage = storage
        self._inspector = inspector
        self._repository = repository
        self._clock = clock
        self._max_download_bytes = max_download_bytes

    def run(self, job: MapInstallJob, *, is_cancelled: Callable[[], bool]) -> InstallResult:
        """Carry out one queued job.

        Args:
            job: The job to run. Its state is written as the pipeline advances,
                so a caller polling it sees real progress rather than a spinner.
            is_cancelled: Polled between chunks and between stages.

        Returns:
            What the installation did.

        Raises:
            MapOperationError: With a named code. Whatever the reason, an
                already-installed package is still installed afterwards.
        """
        region = self._catalog.region(job.region_id)
        remote = self._provider.resolve(job.region_id)
        if remote is None:
            raise MapOperationError(MapErrorCode.MAP_PACKAGE_UNAVAILABLE)

        previous = self._repository.installed_package(job.region_id)
        self._require_disk_space(remote.size_bytes)

        job = self._advance(job, MapJobState.DOWNLOADING, bytes_total=remote.size_bytes)
        slot = self._storage.open_download_slot(job.job_id)
        try:
            transfer = self._provider.download(
                remote,
                slot.sink,
                max_bytes=self._max_download_bytes,
                on_progress=self._progress_writer(job),
                is_cancelled=is_cancelled,
            )
            slot.finish()

            unchanged = (
                previous is not None
                and previous.content_sha256 == transfer.content_sha256
                and self._storage.is_intact(job.region_id, previous.content_sha256)
            )
            if unchanged and previous is not None:
                slot.discard()
                self._advance(job, MapJobState.COMPLETED, bytes_downloaded=transfer.size_bytes)
                return InstallResult(InstallOutcome.ALREADY_CURRENT, previous)

            job = self._advance(job, MapJobState.VALIDATING, bytes_downloaded=transfer.size_bytes)
            self._storage.publish(job.region_id, transfer.content_sha256, slot)
        except MapOperationError:
            slot.discard()
            raise
        except Exception as error:
            slot.discard()
            raise MapOperationError(MapErrorCode.MAP_DOWNLOAD_FAILED) from error

        try:
            inspected = self._inspector.inspect(job.region_id, transfer.content_sha256)
        except BaseException as error:
            # The file was published a moment ago and no row points at it, so
            # removing it restores the state the archive was in. The package
            # that was current stays current.
            #
            # *Every* failure, not only a named one. Publishing before
            # validating is deliberate -- it makes what is checked and what is
            # later served the same file -- and that puts this handler between a
            # refused package and a managed file nothing points at. Catching
            # only `MapOperationError` left an orphan behind the first time an
            # unexpected exception got out of an inspector, and each of those is
            # the size of a country.
            self._storage.remove(job.region_id, transfer.content_sha256)
            if isinstance(error, MapOperationError):
                raise
            logger.exception("map_install.inspection_failed")
            raise MapOperationError(
                MapErrorCode.MAP_PACKAGE_INVALID, "the package could not be read"
            ) from error

        job = self._advance(job, MapJobState.PUBLISHING)
        package = MapPackage(
            region_id=job.region_id,
            region_name=region.name,
            provider=self._provider.slug,
            format=inspected.format,
            tile_schema=inspected.tile_schema,
            content_sha256=transfer.content_sha256,
            size_bytes=transfer.size_bytes,
            bounds=inspected.bounds,
            min_zoom=inspected.min_zoom,
            max_zoom=inspected.max_zoom,
            attribution=inspected.attribution,
            downloaded_at=self._clock.now(),
            source_url=remote.url,
            dataset_version=inspected.dataset_version,
            dataset_timestamp=remote.updated_at,
        )
        self._repository.save_package(package)
        # Every `_advance` returns the job it wrote and the result is kept.
        # Writing a later state from the object as it was queued would report a
        # completed transfer as nought bytes -- true when the job started, and
        # the last thing a reader sees.
        self._advance(job, MapJobState.COMPLETED)

        if previous is not None and previous.content_sha256 != package.content_sha256:
            # Only now. Until the row committed, the old file was the archive's
            # only working basemap for this region.
            self._storage.remove(job.region_id, previous.content_sha256)
            return InstallResult(InstallOutcome.UPDATED, package)
        return InstallResult(InstallOutcome.INSTALLED, package)

    def _require_disk_space(self, declared: int | None) -> None:
        """Refuse before the transfer when the result could not fit.

        The requirement is the new package *plus* the one already installed,
        because the old one is not deleted to make room. Freeing space by
        removing a working map in order to attempt an unproven download is
        exactly the trade this design refuses.
        """
        if declared is not None and declared > self._max_download_bytes:
            raise MapOperationError(
                MapErrorCode.MAP_DOWNLOAD_TOO_LARGE,
                f"{declared} bytes exceeds the {self._max_download_bytes} byte limit",
            )
        needed = (declared or UNKNOWN_SIZE_RESERVE_BYTES) + FREE_SPACE_MARGIN_BYTES
        available = self._storage.free_bytes()
        if available < needed:
            raise MapOperationError(
                MapErrorCode.MAP_INSUFFICIENT_DISK_SPACE,
                f"needs {needed} bytes, {available} available",
            )

    def _progress_writer(self, job: MapInstallJob) -> Callable[[int], None]:
        """Return a throttled progress callback bound to one job."""
        written = 0

        def record(downloaded: int) -> None:
            nonlocal written
            if downloaded - written < PROGRESS_INTERVAL_BYTES:
                return
            written = downloaded
            self._advance(job, MapJobState.DOWNLOADING, bytes_downloaded=downloaded)

        return record

    def _advance(
        self,
        job: MapInstallJob,
        state: MapJobState,
        *,
        bytes_downloaded: int | None = None,
        bytes_total: int | None = None,
    ) -> MapInstallJob:
        """Write a job's new state and return it."""
        updated = MapInstallJob(
            job_id=job.job_id,
            region_id=job.region_id,
            region_name=job.region_name,
            state=state,
            bytes_downloaded=job.bytes_downloaded if bytes_downloaded is None else bytes_downloaded,
            bytes_total=job.bytes_total if bytes_total is None else bytes_total,
            started_at=job.started_at,
            updated_at=self._clock.now(),
            is_update=job.is_update,
        )
        self._repository.update_job(updated)
        return updated


class RemoveMapPackage:
    """Deleting an installed package, and nothing else.

    > Removing a map touches no track data.

    Worth stating because the two live in the same data directory and a reader
    about to free eight hundred megabytes deserves to know which eight hundred.
    """

    def __init__(
        self,
        *,
        storage: MapPackageStorage,
        repository: MapPackageRepository,
    ) -> None:
        """Wire removal to the storage and the repository."""
        self._storage = storage
        self._repository = repository

    def run(self, region_id: MapRegionId) -> None:
        """Remove one region's package.

        Raises:
            MapOperationError: ``map_package_not_installed`` when there is
                nothing to remove, ``map_mutation_in_progress`` when an install
                is running for the same region.
        """
        if self._repository.active_job_for(region_id) is not None:
            raise MapOperationError(MapErrorCode.MAP_MUTATION_IN_PROGRESS)
        package = self._repository.installed_package(region_id)
        if package is None:
            raise MapOperationError(MapErrorCode.MAP_PACKAGE_NOT_INSTALLED)
        # The row goes first. A row without a file is `INVALID` and visible; a
        # file without a row is an orphan nothing can reach, and the start-up
        # sweep collects it.
        self._repository.delete_package(region_id)
        self._storage.remove_region(region_id)


class RecoverMapStorage:
    """What has to be true again after a process stops mid-installation.

    Three kinds of debris, and each needs a different answer:

    ```
    a job stuck in `downloading`   the process that owned it is gone -> INTERRUPTED
    a `.part` file                 nothing was published from it     -> deleted
    a managed file with no row     published, never committed        -> deleted
    ```

    The last one is the interesting case. The file may be perfectly valid, and
    promoting it to an installed package on the strength of that would mean
    inferring an installation nobody completed -- from a file whose provenance
    the archive has no record of. It is removed, and the region installs again
    cleanly.
    """

    def __init__(
        self,
        *,
        storage: MapPackageStorage,
        repository: MapPackageRepository,
        clock: Clock,
    ) -> None:
        """Wire recovery to the storage and the repository."""
        self._storage = storage
        self._repository = repository
        self._clock = clock

    def run(self) -> None:
        """Bring the map area back to a state a reader can act on."""
        interrupted = self._repository.interrupt_unfinished_jobs(self._clock.now())
        partial = self._storage.discard_partial_downloads()
        keep = [
            (package.region_id, package.content_sha256)
            for package in self._repository.installed_packages()
        ]
        orphans = self._storage.discard_orphans(keep)
        if interrupted or partial or orphans:
            logger.info(
                "map storage recovered",
                extra={
                    "interrupted_jobs": interrupted,
                    "partial_downloads": partial,
                    "orphan_packages": orphans,
                },
            )


def queued_job(
    *,
    job_id: str,
    region_id: MapRegionId,
    region_name: str,
    at: datetime,
    is_update: bool,
) -> MapInstallJob:
    """Return a freshly queued job."""
    return MapInstallJob(
        job_id=job_id,
        region_id=region_id,
        region_name=region_name,
        state=MapJobState.QUEUED,
        bytes_downloaded=0,
        bytes_total=None,
        started_at=at,
        updated_at=at,
        is_update=is_update,
    )
