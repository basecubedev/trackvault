"""Reprocessing a raw import that is already in the archive.

Raw imports and processing runs exist so that a parser or classifier upgrade can
regenerate normalized data from the original bytes. Until now nothing did:
importing was the only way in, and importing recognises a known content hash as a
duplicate and stops there.

That conflated two different statements. "This source is already known" is not
"its normalized representation is current". A file that failed under an older
importer, or that was classified by older rules, is known *and* out of date, and
the ordinary directory scan must keep skipping it while an operator can still say
"process this again".

Reprocessing is therefore explicit and does no duplicate check. It reads the
managed raw copy -- which verifies the bytes against their content hash, so a
corrupt artifact can never become the input of a regeneration -- and hands them
to the same normalization step an import uses.

Two batch selections exist, and they answer different questions:

```
--outdated   the current generation was not produced by the installed processing
--failed     the newest attempt failed, whatever the current generation is
```

They overlap without being the same. A source that never processed successfully
is in both: it has no current generation, and its newest attempt failed. A source
whose newest attempt failed while an older, matching generation still serves
readers is only in `--failed`. A source that works perfectly but was normalized
by an older importer is only in `--outdated`. Both read the same snapshots and
the same currency authority, so a batch run and the status view cannot disagree
about a source.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum

from trackvault.application.analyze import AnalyzeTrack
from trackvault.application.errors import ImportErrorCode, TrackImportError
from trackvault.application.normalization import NormalizeRawImport
from trackvault.application.ports import RawImportStore, TrackRepository
from trackvault.domain import ProcessingStatus

logger = logging.getLogger(__name__)


class ReprocessStatus(StrEnum):
    """How one reprocessing attempt ended."""

    REPROCESSED = "reprocessed"
    FAILED = "failed"
    UNKNOWN_SOURCE = "unknown_source"


@dataclass(frozen=True, slots=True)
class ReprocessOutcome:
    """What one reprocessing attempt produced.

    Attributes:
        status: Whether a new generation became current, the attempt failed, or
            the archive holds no such source at all.
        sha256: The raw import that was asked about.
        track_ids: The current tracks after the attempt, in source order. Empty
            after a failure, which does not mean the archive lost anything: the
            previous generation is still current.
        error_code: Why a failed attempt failed.
    """

    status: ReprocessStatus
    sha256: str
    track_ids: tuple[int, ...] = ()
    error_code: ImportErrorCode | None = None


class ReprocessRawImport:
    """Regenerates the normalized data of one raw import from its original bytes."""

    def __init__(
        self,
        *,
        raw_store: RawImportStore,
        repository: TrackRepository,
        normalize: NormalizeRawImport,
        analyze: AnalyzeTrack | None = None,
    ) -> None:
        """Wire the use case to its ports.

        ``analyze`` is the same optional, best-effort step the import path
        takes. A successful reprocess produces new geometry and therefore
        outdates the old metrics, so deriving them again here is what stops a
        regeneration from quietly leaving every statistic describing geometry
        that no longer exists.
        """
        self._raw_store = raw_store
        self._repository = repository
        self._normalize = normalize
        self._analyze = analyze

    def outdated_sources(self) -> tuple[str, ...]:
        """Return the sources whose current generation the installed processing outdates.

        A source qualifies when its current generation was not produced by the
        profile this build installs -- an older importer, an older normalized
        model, older classification rules, or a run that cannot prove which of
        them it used. A source with no successful generation at all qualifies
        too: a failed attempt is not a current generation, and treating it as one
        would leave the archive's least healthy data out of the very upgrade
        meant to reach it.

        Ordered oldest received first, so a batch run is reproducible.
        """
        return tuple(
            snapshot.raw_import_sha256
            for snapshot in self._repository.processing_snapshots()
            if not self._normalize.processing.is_current(snapshot.current_run)
        )

    def failed_sources(self) -> tuple[str, ...]:
        """Return the sources whose newest processing attempt failed.

        This is the operator's "I fixed the importer, try those again" set, and
        it deliberately says nothing about currency: a source whose newest
        attempt failed is worth retrying whether or not an older generation of it
        still serves readers. A source that never processed successfully is
        included, because its newest attempt is a failure too.

        Ordered oldest received first, so a batch run is reproducible.
        """
        return tuple(
            snapshot.raw_import_sha256
            for snapshot in self._repository.processing_snapshots()
            if snapshot.latest_run is not None
            and snapshot.latest_run.status is ProcessingStatus.FAILED
        )

    def __call__(self, sha256: str) -> ReprocessOutcome:
        """Reprocess one raw import and report what happened.

        User corrections are not touched. That is not a special case here: the
        override belongs to the candidate's ``source_key``, and this use case
        never writes to the override table at all.
        """
        raw_import = self._repository.find_raw_import(sha256)
        if raw_import is None:
            logger.info("reprocess.unknown raw_import=%s", sha256[:12])
            return ReprocessOutcome(ReprocessStatus.UNKNOWN_SOURCE, sha256)

        try:
            content = self._raw_store.read(sha256)
        except TrackImportError as error:
            logger.info(
                "reprocess.failed raw_import=%s reason=%s", raw_import.short_sha256, error.code
            )
            return ReprocessOutcome(ReprocessStatus.FAILED, sha256, (), error.code)

        result = self._normalize(raw_import, content)
        if not result.succeeded:
            return ReprocessOutcome(ReprocessStatus.FAILED, sha256, (), result.error_code)
        if self._analyze is not None:
            self._analyze.best_effort(result.track_ids)
        return ReprocessOutcome(ReprocessStatus.REPROCESSED, sha256, result.track_ids)
