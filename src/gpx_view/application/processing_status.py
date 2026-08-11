"""What an operator can ask about one source's processing.

A separate read boundary from the ordinary track queries, and separate on
purpose. Track queries answer "what is in the archive"; this answers "what
happened to this source, and does it need doing again". Mixing them would make
one endpoint mean two things -- the same reason history and the current
generation are kept apart in the first place.

The view is data-sparse. It reports content hashes, run identities, versions,
timestamps, counts and error codes. It reports no coordinate, no file system
path, no original filename and no document content: the error contract already
forbids diagnostics becoming a side channel for private movement data, and a
status page is diagnostics.
"""

from dataclasses import dataclass
from datetime import datetime

from gpx_view.application.ports import TrackRepository
from gpx_view.application.processing import InstalledProcessing
from gpx_view.domain import ProcessingProfile, ProcessingStatus


@dataclass(frozen=True, slots=True)
class ProcessingStatusReport:
    """What one raw import's processing currently amounts to.

    Attributes:
        raw_import_sha256: Identity of the source. The hash is the identity, so
            reporting it discloses nothing the caller did not already ask with.
        current_run_id: The run whose candidates readers see, or ``None`` when
            the source has never been processed successfully.
        current_run_at: When that run finished.
        current_profile: The processing that produced the current generation, or
            ``None`` when there is none or the run cannot prove it.
        installed_profile: What this build would produce today, for that run's
            importer. ``None`` when no installed adapter claims that format.
        latest_attempt_id: The newest attempt, whatever it did.
        latest_attempt_at: When it finished.
        latest_attempt_status: Whether it succeeded or failed.
        latest_error_code: Why it failed, when it did.
        track_count: How many tracks the current generation holds. Zero is a
            normal answer -- a successful run may find nothing importable.
        is_outdated: Whether regenerating this source would change what readers
            see. The one authority, shared with ``reprocess --outdated``.
    """

    raw_import_sha256: str
    current_run_id: int | None
    current_run_at: datetime | None
    current_profile: ProcessingProfile | None
    installed_profile: ProcessingProfile | None
    latest_attempt_id: int | None
    latest_attempt_at: datetime | None
    latest_attempt_status: ProcessingStatus | None
    latest_error_code: str | None
    track_count: int
    is_outdated: bool


class GetProcessingStatus:
    """Answers what happened to one source, and whether it is still current."""

    def __init__(self, *, repository: TrackRepository, processing: InstalledProcessing) -> None:
        """Wire the query to its repository and to the currency authority."""
        self._repository = repository
        self._processing = processing

    def __call__(self, sha256: str) -> ProcessingStatusReport | None:
        """Return the report for one source, or ``None`` if the archive has none.

        The installed profile is looked up for the importer that actually ran,
        so the two profiles in the report are comparable. When nothing ran -- no
        successful generation at all -- the report names the profile of the only
        importer this build has, if there is exactly one, and otherwise leaves it
        open: guessing which adapter *would* claim the source means parsing it,
        and a status query does not parse anything.
        """
        snapshot = self._repository.processing_snapshot(sha256)
        if snapshot is None:
            return None

        current = snapshot.current_run
        installed = self._installed_for(current.importer if current else None)
        latest = snapshot.latest_run
        return ProcessingStatusReport(
            raw_import_sha256=snapshot.raw_import_sha256,
            current_run_id=snapshot.current_run_id,
            current_run_at=None if current is None else current.processed_at,
            current_profile=None if current is None else current.profile,
            installed_profile=installed,
            latest_attempt_id=snapshot.latest_run_id,
            latest_attempt_at=None if latest is None else latest.processed_at,
            latest_attempt_status=None if latest is None else latest.status,
            latest_error_code=None if latest is None else latest.error_code,
            track_count=snapshot.track_count,
            is_outdated=not self._processing.is_current(current),
        )

    def _installed_for(self, importer: str | None) -> ProcessingProfile | None:
        """Return the profile to compare against, when one can be named."""
        if importer is not None:
            return self._processing.profile_for(importer)
        profiles = self._processing.profiles
        return profiles[0] if len(profiles) == 1 else None
