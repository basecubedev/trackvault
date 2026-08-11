"""Deriving the metrics of a track the archive already holds.

The one canonical analysis use case. Every caller -- the command line, the
post-import step, anything added later -- goes through here, for the same reason
every import goes through ``ImportTracks``: an analysis bug is fixed in one place
only if there is one place.

No API route and no command line derives a metric of its own. A route that
computed a distance would be a second answer to a question that already has an
authority, and it would be the answer nobody reruns when the algorithm changes.

Analysis never endangers what it reads. It writes nothing back into the
normalized track, it publishes only complete results, and a failure leaves the
track exactly as readable as it was -- with whatever metrics it already had.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from gpx_view.application.analysis import InstalledAnalysis
from gpx_view.application.errors import ImportErrorCode, TrackImportError
from gpx_view.application.ports import (
    AnalysisRun,
    AnalysisStatus,
    Clock,
    TrackRepository,
)
from gpx_view.domain.analysis import analyze_track

logger = logging.getLogger(__name__)


class AnalyzeStatus(StrEnum):
    """How one analysis attempt ended.

    Attributes:
        ANALYZED: Metrics were derived and published.
        FAILED: The attempt did not complete. ``error_code`` says why, and the
            track keeps whatever metrics it already had.
        UNKNOWN_TRACK: The archive holds no such current track. A candidate a
            later run stopped producing is history, and history is not analysed.
    """

    ANALYZED = "analyzed"
    FAILED = "failed"
    UNKNOWN_TRACK = "unknown_track"


@dataclass(frozen=True, slots=True)
class AnalyzeOutcome:
    """What one analysis attempt produced.

    Attributes:
        status: Whether metrics became current, the attempt failed, or the
            archive holds no such track.
        track_id: The track that was asked about.
        error_code: Why a failed attempt failed.
    """

    status: AnalyzeStatus
    track_id: int
    error_code: ImportErrorCode | None = None


class AnalyzeTrack:
    """Derives and publishes the metrics of one current normalized track."""

    def __init__(
        self,
        *,
        repository: TrackRepository,
        clock: Clock,
        analysis: InstalledAnalysis,
    ) -> None:
        """Wire the use case to its ports and to the currency authority."""
        self._repository = repository
        self._clock = clock
        self._analysis = analysis

    @property
    def installed(self) -> InstalledAnalysis:
        """Return the profile this use case stamps its runs with.

        The same object judges whether a stored result is current, so what wrote
        a version and what reads it can never be two opinions.
        """
        return self._analysis

    def analyzable_tracks(self) -> tuple[int, ...]:
        """Return every current track, oldest imported source first."""
        return tuple(snapshot.track_id for snapshot in self._repository.analysis_snapshots())

    def outdated_tracks(self) -> tuple[int, ...]:
        """Return the tracks whose metrics the installed analysis outdates.

        A track qualifies when it has never been analysed, when its newest
        successful analysis applied different algorithms, or when a reprocess
        gave it geometry that analysis never saw. All three are one question
        asked of one authority.

        Ordered oldest imported source first, so a batch run is reproducible.
        """
        return tuple(
            snapshot.track_id
            for snapshot in self._repository.analysis_snapshots()
            if not self._analysis.is_current(snapshot)
        )

    def __call__(self, track_id: int) -> AnalyzeOutcome:
        """Derive and publish the metrics of one track.

        The generation is read before the geometry and recorded with the result,
        so a reprocess that lands in between is detected at publication rather
        than producing metrics that quietly describe geometry nobody can see.
        """
        snapshot = self._repository.analysis_snapshot(track_id)
        if snapshot is None:
            return AnalyzeOutcome(AnalyzeStatus.UNKNOWN_TRACK, track_id)

        segments = self._repository.get_geometry(track_id)
        if segments is None:
            return AnalyzeOutcome(AnalyzeStatus.UNKNOWN_TRACK, track_id)

        try:
            analysis = analyze_track(segments)
        except (ArithmeticError, ValueError):
            # Geometry is validated on the way in, so this is not expected. It
            # is caught rather than propagated because one unanalysable track
            # must not stop a batch run, and the failure is recorded so that it
            # is visible instead of silent.
            logger.exception("analysis.failed track=%d", track_id)
            return self._record_failure(snapshot.track_id, snapshot.processing_run_id)

        run = AnalysisRun(
            track_id=track_id,
            processing_run_id=snapshot.processing_run_id,
            profile=self._analysis.profile,
            analyzed_at=self._clock.now(),
            status=AnalysisStatus.SUCCEEDED,
        )
        try:
            self._repository.record_analysis(run, analysis)
        except TrackImportError as error:
            return AnalyzeOutcome(AnalyzeStatus.FAILED, track_id, error.code)

        logger.info("analysis.completed track=%d metrics=%d", track_id, len(analysis.metrics))
        return AnalyzeOutcome(AnalyzeStatus.ANALYZED, track_id)

    def best_effort(self, track_ids: Iterable[int]) -> None:
        """Analyse tracks without letting a failure reach the caller.

        This is what the import and reprocessing paths use, and the swallowing
        is the point rather than an oversight. Metrics are derived state: losing
        them is recoverable by running ``analyze --outdated``, while letting an
        analysis defect abort an import would lose the source evidence, which is
        the one thing the archive cannot reconstruct.

        Anything that did not work stays visible -- as a failed run where one
        could be recorded, in the log otherwise, and in the outdated selection
        either way.
        """
        for track_id in track_ids:
            try:
                outcome = self(track_id)
            # Deliberately broad: a derived value may never break the source it
            # was derived from, and this is the boundary that promise lives on.
            except Exception:
                logger.exception("analysis.skipped track=%d", track_id)
                continue
            if outcome.status is not AnalyzeStatus.ANALYZED:
                logger.info("analysis.skipped track=%d status=%s", track_id, outcome.status.value)

    def _record_failure(self, track_id: int, processing_run_id: int) -> AnalyzeOutcome:
        """Record that analysis failed, without disturbing what the track has."""
        run = AnalysisRun(
            track_id=track_id,
            processing_run_id=processing_run_id,
            profile=self._analysis.profile,
            analyzed_at=self._clock.now(),
            status=AnalysisStatus.FAILED,
            error_code=ImportErrorCode.ANALYSIS_FAILED.value,
        )
        try:
            self._repository.record_analysis(run, None)
        except TrackImportError as error:
            return AnalyzeOutcome(AnalyzeStatus.FAILED, track_id, error.code)
        return AnalyzeOutcome(AnalyzeStatus.FAILED, track_id, ImportErrorCode.ANALYSIS_FAILED)
