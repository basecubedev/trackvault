"""The step from stored raw bytes to a normalized generation.

Importing and reprocessing differ in where the bytes come from and in what the
caller is told afterwards. What happens to the bytes is the same work, and it
lives here so that it stays one implementation:

```
detect the format
  -> normalize into candidates
  -> classify each candidate
  -> append a processing run
  -> on success, become the current generation
```

A second copy of this for reprocessing would be a second import pipeline, which
is the thing ``architecture.md`` forbids most explicitly. It would also drift:
the copy that runs less often is the one nobody notices going wrong.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from gpx_view.application.errors import ImportErrorCode, TrackImportError
from gpx_view.application.importing import ImportLimits, TrackImporter
from gpx_view.application.ports import Clock, TrackRepository
from gpx_view.application.processing import InstalledProcessing
from gpx_view.domain import (
    NORMALIZATION_SCHEMA_VERSION,
    ImportedTrack,
    NormalizedTrack,
    ProcessingRun,
    ProcessingStatus,
    RawImport,
    TrackClassification,
    classify,
)
from gpx_view.domain.classifier import CLASSIFIER_METHOD, CLASSIFIER_VERSION

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """What one processing attempt produced.

    Attributes:
        succeeded: Whether the attempt produced a generation. A successful
            attempt that produced no candidate is still a success -- a document
            can legitimately hold nothing importable.
        track_ids: The current tracks after this attempt, in source order.
        error_code: Why the attempt failed, when it did.
    """

    succeeded: bool
    track_ids: tuple[int, ...] = ()
    error_code: ImportErrorCode | None = None


class NormalizeRawImport:
    """Turns the bytes of one raw import into its current normalized generation."""

    def __init__(
        self,
        *,
        importers: Sequence[TrackImporter],
        repository: TrackRepository,
        clock: Clock,
        limits: ImportLimits,
    ) -> None:
        """Wire the step to its ports."""
        self._importers = tuple(importers)
        self._repository = repository
        self._clock = clock
        self._limits = limits
        self._processing = InstalledProcessing(self._importers)

    @property
    def limits(self) -> ImportLimits:
        """Return the limits this step enforces."""
        return self._limits

    @property
    def processing(self) -> InstalledProcessing:
        """Return the processing this step stamps its runs with.

        The same object answers "is a stored generation current?", so what wrote
        a run and what judges it can never be two different opinions.
        """
        return self._processing

    def importer_for(self, content: bytes) -> TrackImporter | None:
        """Return the adapter that recognises the content, if there is one."""
        return next((importer for importer in self._importers if importer.detects(content)), None)

    def __call__(self, raw_import: RawImport, content: bytes) -> NormalizationResult:
        """Process one raw import and record what happened.

        A failure is recorded, not raised: the raw import is kept together with a
        run naming the reason, so the source can be reprocessed once an importer
        learns to read it. The previous generation stays current in that case --
        a new importer version that cannot read a file must not cost the data an
        older one already produced from it.
        """
        importer = self.importer_for(content)
        if importer is None:
            return self._record_failure(raw_import, ImportErrorCode.UNSUPPORTED_FORMAT)

        try:
            candidates = importer.import_tracks(content, self._limits)
        except TrackImportError as error:
            return self._record_failure(raw_import, error.code, importer)

        tracks = [_classified(candidate) for candidate in candidates]
        run = self._run(raw_import, ProcessingStatus.SUCCEEDED, importer=importer)
        try:
            track_ids = self._repository.record_import(raw_import, run, tracks)
        except TrackImportError as error:
            return NormalizationResult(succeeded=False, error_code=error.code)

        logger.info(
            "processing.completed raw_import=%s format=%s tracks=%d points=%d",
            raw_import.short_sha256,
            importer.format_id,
            len(track_ids),
            sum(track.point_count for track in tracks),
        )
        return NormalizationResult(succeeded=True, track_ids=track_ids)

    def _record_failure(
        self,
        raw_import: RawImport,
        code: ImportErrorCode,
        importer: TrackImporter | None = None,
    ) -> NormalizationResult:
        """Keep the source evidence and record why processing failed."""
        run = self._run(raw_import, ProcessingStatus.FAILED, importer=importer, error_code=code)
        try:
            self._repository.record_import(raw_import, run, [])
        except TrackImportError as error:
            return NormalizationResult(succeeded=False, error_code=error.code)
        logger.info(
            "processing.failed raw_import=%s reason=%s", raw_import.short_sha256, code.value
        )
        return NormalizationResult(succeeded=False, error_code=code)

    def _run(
        self,
        raw_import: RawImport,
        status: ProcessingStatus,
        *,
        importer: TrackImporter | None,
        error_code: ImportErrorCode | None = None,
    ) -> ProcessingRun:
        """Describe this processing attempt, including what produced its output.

        The run records the whole processing profile, so it can later prove which
        combination of importer, normalized model and classification rules its
        generation came from. A run with no importer -- nothing recognised the
        content -- still names the classification rules this build installs: what
        it could not prove is the format, and that is what its importer says.
        """
        return ProcessingRun(
            raw_import_sha256=raw_import.sha256,
            importer="none" if importer is None else importer.format_id,
            importer_version="0" if importer is None else importer.importer_version,
            normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
            processed_at=self._clock.now(),
            status=status,
            error_code=None if error_code is None else error_code.value,
            classifier=CLASSIFIER_METHOD,
            classifier_version=CLASSIFIER_VERSION,
        )


def _classified(candidate: ImportedTrack) -> NormalizedTrack:
    """Turn one adapter candidate into a canonical track by classifying it."""
    return candidate.classified_as(TrackClassification(detected=classify(candidate.evidence)))
