"""Reading a track as a series, bounded to what a client can render.

Two questions with two different answers, and one thing they share.

```
geometry   where the track goes            -> a map draws it
profile    what it does along the way      -> a chart draws it
```

Both are derived on demand from the current normalized geometry. Nothing is
stored: the profile is a projection of geometry that is already the authority,
and a third derived-state lifecycle -- written, versioned, invalidated,
repaired -- would be three more ways for the archive to hold something stale in
exchange for an optimisation nothing has yet shown it needs.

What they share is **sample identity**. Both address a position as
`(segment_index, point_index)`, so a chart cursor and a map marker point at the
same thing even when the two have been reduced to different subsets. That is the
whole reason the reduction happens on the server: a browser matching a
latitude to a line is a second, worse authority on identity, and it picks the
wrong position exactly where a track crosses itself.

## What the series are, and what the stored analysis is

The series are always derived by the algorithms **this build installs**. The
stored aggregates -- distance, ascent, maximum speed -- may have been derived by
older ones. Those are two different states and the profile reports both, so a
reader can tell whether the chart in front of them and the figure beside it
describe the same thing. Withholding the chart would help nobody: a track whose
metrics have never been derived still has a shape, and the shape is right.
"""

from dataclasses import dataclass

from trackvault.application.analysis import InstalledAnalysis
from trackvault.application.ports import AnalysisAvailability, TrackRepository
from trackvault.application.projection import decimate_profile, simplify_geometry
from trackvault.domain import (
    TemporalEvidence,
    TrackSegment,
    shape_fingerprint,
    supports_actual_timing,
)
from trackvault.domain.analysis import AnalysisProfile
from trackvault.domain.analysis.series import TrackProfile, derive_profile

DEFAULT_PROFILE_SAMPLES = 2000
"""How many samples a profile returns when nobody says.

More than a chart can distinguish on any screen, few enough that the response
stays small. A caller that wants the whole thing asks for it, up to the maximum.
"""

MAX_PROFILE_SAMPLES = 20_000
"""The most samples a profile returns, whatever is asked for.

A server maximum rather than a client one: an archive grows without anybody
deciding to, and a limit a query string can raise is a limit that exists until
somebody types a large number.
"""

MAX_GEOMETRY_POINTS = 50_000
"""The most positions a *simplified* geometry projection returns.

The canonical geometry endpoint is unbounded on purpose -- it is the normalized
track, and truncating it would make the canonical projection a lossy one. This
bounds the reduction a client asks for instead.
"""


@dataclass(frozen=True, slots=True)
class TrackProfileReport:
    """One track's series, with everything needed to read them honestly.

    Attributes:
        track_id: The track this describes.
        profile: The samples themselves, possibly reduced.
        sample_count: How many samples the report holds.
        total_sample_count: How many the track has, so a client can tell a
            reduced series from a complete one.
        total_distance_m: The upper end of the distance axis, from the *full*
            profile -- reducing the samples must not shorten the track.
        derived_with: The algorithms that produced these series. Always the
            installed ones: the series are derived now, from geometry, and never
            read back from a stored result.
        analysis_status: What the track's *stored* aggregates amount to. When
            this is not ``CURRENT``, the numbers a client holds beside the chart
            were produced by different algorithms and the two do not describe the
            same thing. Reported rather than resolved, because resolving it means
            running ``analyze --outdated``.
        timing_basis: What the instants behind the speed series were shown to be.
        is_actual_activity_timing: Whether the speeds may be presented as speeds
            somebody actually travelled at.
    """

    track_id: int
    profile: TrackProfile
    sample_count: int
    total_sample_count: int
    total_distance_m: float
    derived_with: AnalysisProfile
    analysis_status: AnalysisAvailability
    timing_basis: TemporalEvidence
    is_actual_activity_timing: bool


@dataclass(frozen=True, slots=True)
class TrackGeometryReport:
    """One track's geometry, canonical or reduced for a map.

    Attributes:
        track_id: The track this describes.
        segments: The positions, segment boundaries intact.
        point_count: How many positions the report holds.
        total_point_count: How many the track has. Equal to ``point_count``
            unless the geometry was reduced.
        simplified: Whether a reduction was applied. A client that asked for a
            bound and got fewer positions than the track holds needs to know
            that the shape it has is a projection.
    """

    track_id: int
    segments: tuple[TrackSegment, ...]
    point_count: int
    total_point_count: int
    simplified: bool

    @property
    def shape_sha256(self) -> str:
        """Return the identity of the line these segments draw.

        Derived here rather than stored, and derived from *this* report rather
        than from the track. That is what makes it the identity of the answer
        instead of the identity of the archive: a reduced projection identifies
        the reduction, so a different position budget or a changed
        simplification is a different value without anybody remembering to say
        so. A stored one would describe the canonical geometry and be handed to
        a client that is holding something else.
        """
        return shape_fingerprint(self.segments)


class GetTrackProfile:
    """Answers what one track does along its own length."""

    def __init__(self, *, repository: TrackRepository, analysis: InstalledAnalysis) -> None:
        """Wire the query to its repository and to the currency authority."""
        self._repository = repository
        self._analysis = analysis

    def __call__(
        self, track_id: int, *, max_samples: int = DEFAULT_PROFILE_SAMPLES
    ) -> TrackProfileReport | None:
        """Return one track's series, or ``None`` if the archive has no such track.

        Args:
            track_id: Identity of the track.
            max_samples: The most samples to return. Bounded by the server.
        """
        summary = self._repository.get_track(track_id, self._analysis.profile)
        segments = self._repository.get_geometry(track_id)
        if summary is None or segments is None:
            return None

        full = derive_profile(segments)
        budget = max(2, min(max_samples, MAX_PROFILE_SAMPLES))
        reduced = full if full.sample_count <= budget else decimate_profile(full, budget)
        evidence = summary.temporal_evidence
        return TrackProfileReport(
            track_id=track_id,
            profile=reduced,
            sample_count=reduced.sample_count,
            total_sample_count=full.sample_count,
            # From the full profile: a reduced series must not report a shorter
            # track than the one it is a projection of.
            total_distance_m=full.total_distance_m,
            derived_with=self._analysis.profile,
            analysis_status=summary.analysis,
            timing_basis=evidence,
            is_actual_activity_timing=supports_actual_timing(summary.effective_kind, evidence),
        )


class GetTrackGeometry:
    """Answers where one track goes, canonically or bounded for a map."""

    def __init__(self, *, repository: TrackRepository) -> None:
        """Wire the query to its repository."""
        self._repository = repository

    def __call__(
        self, track_id: int, *, max_points: int | None = None
    ) -> TrackGeometryReport | None:
        """Return one track's geometry, or ``None`` if the archive has no such track.

        Args:
            track_id: Identity of the track.
            max_points: The most positions to return, or ``None`` for the
                canonical geometry. Without a bound this is the normalized track
                exactly as it is stored -- truncating it silently would make the
                canonical projection a lossy one, and nothing downstream could
                tell.
        """
        segments = self._repository.get_geometry(track_id)
        if segments is None:
            return None
        total = sum(segment.point_count for segment in segments)
        if max_points is None or total <= max_points:
            return TrackGeometryReport(
                track_id=track_id,
                segments=segments,
                point_count=total,
                total_point_count=total,
                simplified=False,
            )
        reduced = simplify_geometry(segments, min(max_points, MAX_GEOMETRY_POINTS))
        return TrackGeometryReport(
            track_id=track_id,
            segments=reduced,
            point_count=sum(segment.point_count for segment in reduced),
            total_point_count=total,
            simplified=True,
        )


__all__ = [
    "DEFAULT_PROFILE_SAMPLES",
    "MAX_GEOMETRY_POINTS",
    "MAX_PROFILE_SAMPLES",
    "GetTrackGeometry",
    "GetTrackProfile",
    "TrackGeometryReport",
    "TrackProfileReport",
]
