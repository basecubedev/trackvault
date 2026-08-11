"""Provenance of one attempt to normalize a raw import.

Which importer ran, in which version, against which normalization schema, when,
and with what outcome -- all of that describes the *processing*, not the source
file. Keeping it here is what allows a raw import to be reprocessed by a newer
importer without being modified.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

NORMALIZATION_SCHEMA_VERSION = 1
"""Version of the normalized track model a run produced.

Bumped when the normalized model changes in a way that makes older normalized
data worth regenerating from the raw imports.
"""


class ProcessingStatus(StrEnum):
    """Outcome of a processing run."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessingRun:
    """One normalization attempt for one raw import.

    Attributes:
        raw_import_sha256: Identity of the raw import that was processed.
        importer: Name of the importer that ran, for example ``gpx``.
        importer_version: Version of that importer, so two runs over the same
            source can be told apart.
        normalization_schema_version: Version of the normalized model the run
            produced.
        processed_at: The timezone-aware instant the run finished.
        status: Whether the run succeeded or failed.
        error_code: Stable error code of a failed run. Required for a failure and
            forbidden for a success, so that a failed import is always a named,
            recoverable state rather than silent absence.

    Raises:
        ValueError: If the importer is unidentified, the timestamp is naive, or
            the status and the error code contradict each other.
    """

    raw_import_sha256: str
    importer: str
    importer_version: str
    normalization_schema_version: int
    processed_at: datetime
    status: ProcessingStatus
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Reject runs that could not be attributed or recovered from."""
        if not self.importer.strip():
            raise ValueError("importer must name the importer that produced this run")
        if not self.importer_version.strip():
            raise ValueError("importer_version must identify the importer version")
        if self.processed_at.tzinfo is None or self.processed_at.utcoffset() is None:
            raise ValueError("processed_at must be timezone-aware")
        if self.status is ProcessingStatus.FAILED and not self.error_code:
            raise ValueError("a failed run must state a stable error_code")
        if self.status is ProcessingStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("a successful run must not state an error_code")

    @property
    def succeeded(self) -> bool:
        """Report whether this run produced normalized data."""
        return self.status is ProcessingStatus.SUCCEEDED
