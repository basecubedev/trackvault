"""Running an installation without holding an HTTP request open for an hour.

A country package is hundreds of megabytes. Doing that inside the request that
asked for it would mean a browser waiting on a connection for as long as the
transfer takes, a proxy timing it out somewhere in the middle, and a reader with
no way to find out what happened. So the request queues a job, answers with its
identity, and the work happens on a worker thread.

**One mutation at a time.** A single worker thread, deliberately. Three
concurrent country downloads would compete for the same disk and the same
uplink, all three would be slower than doing them in sequence, and the
free-space preflight of each would have been computed without knowing about the
other two.

**One job per region.** Enforced against the database rather than against a
process-local set, so two requests that arrive at once cannot both pass.

**Cancellation is cooperative.** The worker checks a flag between chunks. A job
that has already reached publishing runs to completion -- interrupting an
atomic switch to save a few hundred milliseconds would be trading the one
guarantee that matters for the one that does not.
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import uuid4

from trackvault.application.maps import (
    GetMapCatalog,
    InstallMapPackage,
    MapErrorCode,
    MapOperationError,
    queued_job,
)
from trackvault.application.maps.ports import MapInstallJob
from trackvault.application.ports import Clock
from trackvault.domain.maps import MapRegionId
from trackvault.infrastructure.database.map_store import SqliteMapPackageStore

logger = logging.getLogger(__name__)


class MapInstallJobs:
    """Queues, runs and reports installations.

    Args:
        installer: The use case that does the work.
        catalog: Asked for the region's display name before anything is queued,
            so an unknown region fails immediately rather than on a worker
            thread nobody is watching.
        repository: Where jobs live.
        clock: Injected, so a queued timestamp is deterministic in tests.
    """

    def __init__(
        self,
        *,
        installer: InstallMapPackage,
        catalog: GetMapCatalog,
        repository: SqliteMapPackageStore,
        clock: Clock,
    ) -> None:
        """Build the job runner with its single worker thread."""
        self._installer = installer
        self._catalog = catalog
        self._repository = repository
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="map-install")
        self._cancelled: set[str] = set()
        self._guard = threading.Lock()

    def start(self, region_id: MapRegionId) -> MapInstallJob:
        """Queue an installation or an update for one region.

        Install and update are one call. Whether a package was already there is
        something the pipeline reads, and offering two endpoints for it would
        mean a caller could ask for the wrong one and get a worse answer.

        Raises:
            MapOperationError: ``map_region_unknown`` when the catalog holds no
                such region, ``map_mutation_in_progress`` when one is already
                running for it.
        """
        region = self._catalog.region(region_id)
        with self._guard:
            if self._repository.active_job_for(region_id) is not None:
                raise MapOperationError(MapErrorCode.MAP_MUTATION_IN_PROGRESS)
            job = queued_job(
                job_id=uuid4().hex,
                region_id=region_id,
                region_name=region.name,
                at=self._clock.now(),
                is_update=self._repository.installed_package(region_id) is not None,
            )
            self._repository.create_job(job)
        self._submit(job)
        return job

    def cancel(self, job_id: str) -> None:
        """Ask a running job to stop at the next chunk boundary."""
        with self._guard:
            self._cancelled.add(job_id)

    def shutdown(self) -> None:
        """Stop accepting work and let a running transfer finish."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _submit(self, job: MapInstallJob) -> Future[None]:
        """Hand a queued job to the worker."""
        return self._executor.submit(self._run, job)

    def _run(self, job: MapInstallJob) -> None:
        """Run one job, and make sure it always reaches a terminal state.

        A job that fails without recording why is a region a reader can never
        install again and can never find out about, so every exit from here
        writes an outcome -- including the one nobody planned for.
        """
        try:
            self._installer.run(job, is_cancelled=lambda: job.job_id in self._cancelled)
        except MapOperationError as error:
            if job.job_id in self._cancelled:
                self._repository.cancel_job(job.job_id, self._clock.now())
            else:
                self._repository.fail_job(job.job_id, error.code.value, self._clock.now())
                logger.warning("map_install.failed", extra={"error_code": error.code.value})
        except Exception:
            self._repository.fail_job(
                job.job_id, MapErrorCode.MAP_DOWNLOAD_FAILED.value, self._clock.now()
            )
            logger.exception("map_install.unexpected_failure")
        finally:
            with self._guard:
                self._cancelled.discard(job.job_id)

    def run_now(self, region_id: MapRegionId) -> MapInstallJob:
        """Queue and run an installation on the calling thread.

        For the command line and for tests. The HTTP path must not use it: it is
        the same pipeline without the thread, which is exactly the difference
        between an operator watching a terminal and a browser waiting on a
        socket.
        """
        job = self.start(region_id)
        self._executor.shutdown(wait=True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="map-install")
        found = self._repository.job(job.job_id)
        return found if found is not None else job
