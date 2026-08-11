"""Provenance of one attempt to normalize a raw import.

Which importer ran, in which version, against which normalization schema, with
which classifier, when, and with what outcome -- all of that describes the
*processing*, not the source file. Keeping it here is what allows a raw import to
be reprocessed by a newer importer without being modified.

Together those versions are the run's :class:`ProcessingProfile`: one value that
says what produced a stored generation. Before it existed the versions lived in
three unrelated places -- two on the run, the classifier's on each track -- so a
run that produced no candidate recorded no classifier version at all, and nothing
could state which combination a stored generation came from.

:func:`is_processing_current` is the only place that compares a stored profile
with an installed one. Deciding "this is out of date" in more than one place
means deciding it differently in more than one place.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

NORMALIZATION_SCHEMA_VERSION = 3
"""Version of the normalized track model a run produced.

Bumped when the normalized model changes in a way that makes older normalized
data worth regenerating from the raw imports.

Version 2: candidates carry a ``source_key`` identity of their own rather than
being identified by their position in the source document, evidence belongs to
the candidate it was observed on rather than to the document, and one successful
run is the current generation. Data normalized under version 1 describes
candidates that cannot be matched to what an importer reports today.
"""


class ProcessingStatus(StrEnum):
    """Outcome of a processing run."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessingProfile:
    """The processing a generation was produced by, as one comparable value.

    Every component changes what a source turns into: a newer importer reports
    different candidates or different evidence, a newer normalization schema
    describes them differently, and a newer classifier reaches a different
    verdict from the same observations. "Is this still current" is therefore one
    question about the whole set, not three questions about its parts.

    Attributes:
        importer: Name of the adapter that read the source, for example ``gpx``.
        importer_version: Version of that adapter.
        normalization_schema_version: Version of the normalized track model.
        classifier: Name of the classification method, for example
            ``evidence-weights``.
        classifier_version: Version of those rules.

    Raises:
        ValueError: If any component is blank. A profile that cannot name what
            produced a generation proves nothing about it.
    """

    importer: str
    importer_version: str
    normalization_schema_version: int
    classifier: str
    classifier_version: str

    def __post_init__(self) -> None:
        """Reject a profile that could not identify the processing it names."""
        if not self.importer.strip() or not self.importer_version.strip():
            raise ValueError("a processing profile must name its importer and version")
        if not self.classifier.strip() or not self.classifier_version.strip():
            raise ValueError("a processing profile must name its classifier and version")


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
        classifier: Name of the classification method the run applied. ``None``
            on runs recorded before the profile existed.
        classifier_version: Version of those rules. ``None`` on runs recorded
            before the profile existed.

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
    classifier: str | None = None
    classifier_version: str | None = None

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

    @property
    def profile(self) -> ProcessingProfile | None:
        """Return the processing this run proves it used, or ``None``.

        ``None`` means the run cannot prove it: runs recorded before the profile
        existed carry no classifier identity, and a partial claim is not a claim.
        """
        if self.classifier is None or self.classifier_version is None:
            return None
        return ProcessingProfile(
            importer=self.importer,
            importer_version=self.importer_version,
            normalization_schema_version=self.normalization_schema_version,
            classifier=self.classifier,
            classifier_version=self.classifier_version,
        )


def is_processing_current(run: ProcessingRun | None, installed: ProcessingProfile | None) -> bool:
    """Report whether a stored generation was produced by the installed processing.

    Args:
        run: The successful run that owns the current generation, or ``None``
            when a source has none.
        installed: The profile this build offers for that run's importer, or
            ``None`` when it offers none.

    Returns:
        ``True`` only when the run succeeded and proves exactly the installed
        profile. Everything else answers ``False``, deliberately:

        * a source with no successful generation has nothing that could be
          current,
        * a run that cannot prove which processing produced it has not proved it
          is current -- reading that absence as "probably fine" would exclude
          exactly the data an upgrade is meant to reach,
        * an importer this build no longer has cannot vouch for its output,
        * a profile *newer* than the installed one is not current either. An
          older deployment must not report data written by a newer one as up to
          date and then never regenerate it after a downgrade.
    """
    if run is None or not run.succeeded or installed is None:
        return False
    return run.profile == installed
