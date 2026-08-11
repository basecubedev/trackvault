"""What analysis this build installs, and whether a stored result matches.

The mirror of :mod:`gpx_view.application.processing`, one layer further out in
the pipeline. An :class:`~gpx_view.domain.analysis.AnalysisProfile` describes
analysis that already happened; this answers what the *current* build would
derive today, so the two can be compared.

Three conditions have to hold before stored metrics count as current, and they
fail for three different reasons:

```
a successful run exists          -> otherwise there is nothing to be current
its profile is the installed one -> otherwise the algorithms moved on
its generation is the current one-> otherwise the geometry moved on
```

They are checked together, in one place. Splitting them across the batch
selection, the diagnostics view and the API is how three callers come to
disagree about whether a track needs redoing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from gpx_view.application.ports import AnalysisAvailability, AnalysisSnapshot, TrackRepository
from gpx_view.domain import TemporalEvidence, supports_actual_timing
from gpx_view.domain.analysis import (
    ANALYSIS_PROFILE,
    AnalysisProfile,
    AnalysisQuality,
    MetricName,
    MetricValue,
)
from gpx_view.domain.analysis.profile import is_analysis_profile_current


class InstalledAnalysis:
    """The analysis profile this build offers, and the currency authority.

    Every question of the form "do these metrics need deriving again?" is
    answered here, so ``analyze --outdated``, the diagnostics view and anything
    added later cannot answer it differently.
    """

    def __init__(self, profile: AnalysisProfile = ANALYSIS_PROFILE) -> None:
        """Install a profile, defaulting to the one this build ships.

        The parameter exists so that an upgrade can be exercised without
        rewriting the constant: a test installs a newer profile and watches
        stored results become outdated, which is exactly what a released
        algorithm change will do.
        """
        self._profile = profile

    @property
    def profile(self) -> AnalysisProfile:
        """Return the analysis this build applies."""
        return self._profile

    def is_current(self, snapshot: AnalysisSnapshot | None) -> bool:
        """Report whether a track's stored metrics still describe what it is.

        Args:
            snapshot: What the track's analysis amounts to, or ``None`` when the
                archive holds no such current track.

        Returns:
            ``True`` only when a successful run exists, applied exactly the
            installed algorithms, and read the generation a reader currently
            sees. Everything else is ``False``, which is the safe direction: a
            track that does not need analysing is cheap to skip, and one that
            silently kept stale numbers is not cheap at all.
        """
        if snapshot is None:
            return False
        run = snapshot.current_run
        if run is None or not run.succeeded:
            return False
        if run.processing_run_id != snapshot.processing_run_id:
            return False
        return is_analysis_profile_current(run.profile, self._profile)

    def availability(self, snapshot: AnalysisSnapshot | None) -> AnalysisAvailability:
        """Report which of the four states a track's stored metrics are in.

        The one place the four states are told apart, so a listing row, a detail
        view and a yearly total cannot reach three different words for one
        track. The distinction that matters most is the last one:

        ```
        no current run at all              -> MISSING
        a current run that cannot be read  -> INVALID
        a readable run from other rules    -> OUTDATED
        ```

        A snapshot reports a current run *identity* whatever happens, and
        withholds the run itself when the stored output could not be
        interpreted. Naming an identity while producing nothing is therefore the
        signature of damage, and reporting it as "not analysed yet" would be
        true of the metrics and quietly wrong about the archive.

        Args:
            snapshot: What the track's analysis amounts to, or ``None`` when the
                archive holds no such current track -- which has nothing
                derived, so it reads as ``MISSING``.
        """
        if snapshot is None or snapshot.current_run_id is None:
            return AnalysisAvailability.MISSING
        if snapshot.current_run is None:
            return AnalysisAvailability.INVALID
        if self.is_current(snapshot):
            return AnalysisAvailability.CURRENT
        return AnalysisAvailability.OUTDATED


@dataclass(frozen=True, slots=True)
class TrackAnalysisReport:
    """What one track's analysis amounts to, ready to be projected.

    Attributes:
        track_id: The track this describes.
        status: Whether the metrics are current, outdated or absent.
        profile: The algorithms that produced them, or ``None`` when there are
            none. Reported so that "why did this number change?" is answerable.
        analyzed_at: When they were derived.
        metrics: The derived values. A metric that could not be derived is
            absent, and a reader must not fill the hole with a zero.
        quality: What was wrong with the data they came from.
        latest_error_code: Why the newest attempt failed, when it did. Present
            beside existing metrics too: a track can hold good metrics from an
            earlier run and a failure from the newest one.
        temporal_evidence: What the instants behind the clock-dependent metrics
            have been shown to be. Carried beside the metrics rather than left
            to a reader, because the numbers look identical whatever it says.
        is_actual_activity_timing: Whether those metrics may be presented as
            time somebody actually spent. Needs a recorded effective kind *and*
            observed instants, and the answer is computed once, here.
    """

    track_id: int
    status: AnalysisAvailability
    profile: AnalysisProfile | None
    analyzed_at: datetime | None
    metrics: Mapping[MetricName, MetricValue]
    quality: tuple[AnalysisQuality, ...]
    latest_error_code: str | None
    temporal_evidence: TemporalEvidence
    is_actual_activity_timing: bool


class GetTrackAnalysis:
    """Answers what metrics one track has, and whether they are still right."""

    def __init__(self, *, repository: TrackRepository, analysis: InstalledAnalysis) -> None:
        """Wire the query to its repository and to the currency authority."""
        self._repository = repository
        self._analysis = analysis

    def __call__(self, track_id: int) -> TrackAnalysisReport | None:
        """Return one track's analysis, or ``None`` if the archive has no such track.

        Absence of *metrics* is reported as a ``MISSING`` status rather than as
        absence of the resource: the track exists, and "not analysed yet" is
        something a client has to be able to render.
        """
        snapshot = self._repository.analysis_snapshot(track_id)
        summary = self._repository.get_track(track_id)
        if snapshot is None or summary is None:
            return None

        stored = self._repository.current_analysis(track_id)
        latest = snapshot.latest_run
        status = self._analysis.availability(snapshot)

        evidence = summary.temporal_evidence
        return TrackAnalysisReport(
            track_id=track_id,
            status=status,
            profile=None if stored is None else stored.run.profile,
            analyzed_at=None if stored is None else stored.run.analyzed_at,
            metrics={} if stored is None else stored.metrics,
            quality=() if stored is None else stored.quality,
            latest_error_code=(None if latest is None or latest.succeeded else latest.error_code),
            temporal_evidence=evidence,
            is_actual_activity_timing=supports_actual_timing(summary.effective_kind, evidence),
        )
