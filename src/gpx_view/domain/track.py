"""The canonical normalized track and the candidate an importer produces.

An importer answers "what is in this file": geometry, provenance, an explicit
activity if the source states one, and neutral evidence about what it observed.
It does not answer "what does that mean" -- the classifier does, and only then is
a candidate a :class:`NormalizedTrack`.

One source file may yield any number of candidates, including none.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from gpx_view.domain.activity import Activity
from gpx_view.domain.classification import TrackClassification
from gpx_view.domain.evidence import EvidenceCode, in_canonical_order
from gpx_view.domain.geometry import TrackSegment, earliest_time, latest_time, total_point_count
from gpx_view.domain.source_metadata import SourceMetadata
from gpx_view.domain.track_kind import TrackKind


def _require_segments(segments: tuple[TrackSegment, ...]) -> None:
    """Reject a track without geometry."""
    if not segments:
        raise ValueError("a track must hold at least one segment")


def _require_source_key(source_key: str) -> None:
    """Reject a candidate identity that could not identify anything."""
    if not source_key.strip():
        raise ValueError("a candidate must carry a source key")


@dataclass(frozen=True, slots=True)
class ImportedTrack:
    """One track candidate an importer produced, before it was classified.

    Attributes:
        segments: The segments in source order, boundaries preserved.
        source: Provenance of the document this candidate came from.
        source_key: Which candidate of its source document this is. The importer
            produces it and is the only thing that understands it -- to everything
            further in it is an opaque string that means "the same candidate as
            last time". See :class:`NormalizedTrack`.
        evidence: Neutral observations about the candidate, in canonical order.
        title: The name the source gave the track, if any.
        activity: The activity the source stated explicitly. ``UNKNOWN`` when the
            source stated none -- an importer never guesses it.

    Raises:
        ValueError: If the candidate holds no segment or no source key.
    """

    segments: tuple[TrackSegment, ...]
    source: SourceMetadata
    source_key: str
    evidence: tuple[EvidenceCode, ...] = ()
    title: str | None = None
    activity: Activity = Activity.UNKNOWN

    def __post_init__(self) -> None:
        """Reject empty geometry and put the evidence into canonical order."""
        _require_segments(self.segments)
        _require_source_key(self.source_key)
        object.__setattr__(self, "evidence", in_canonical_order(self.evidence))

    @property
    def segment_count(self) -> int:
        """Return how many segments this candidate holds."""
        return len(self.segments)

    @property
    def point_count(self) -> int:
        """Return how many positions this candidate holds in total."""
        return total_point_count(self.segments)

    def classified_as(self, classification: TrackClassification) -> "NormalizedTrack":
        """Return the canonical track this candidate becomes under a verdict.

        Args:
            classification: The verdict a classifier reached about this candidate.

        Returns:
            The normalized track. Geometry, provenance, title and activity are
            carried over unchanged -- classification adds meaning, it does not
            reshape data.
        """
        return NormalizedTrack(
            segments=self.segments,
            source=self.source,
            source_key=self.source_key,
            classification=classification,
            title=self.title,
            activity=self.activity,
        )


@dataclass(frozen=True, slots=True)
class NormalizedTrack:
    """The canonical track the application acts on, whatever format it came from.

    There is deliberately no ``kind`` field beside ``classification``: two storable
    kinds would be two authorities that can disagree. ``effective_kind`` is a
    projection of ``classification.effective_kind`` and cannot be assigned.

    Attributes:
        segments: The segments in source order, boundaries preserved.
        source: Provenance of the document the track came from, as evidence.
        source_key: Which candidate of its source document this track is. It is
            the track's identity within that source, and it is what makes
            reprocessing safe: a user correction belongs to a candidate, and a
            candidate that a newer importer reports in a different position is
            still the same one. The value is produced by the importer and is
            **opaque** here -- nothing outside the adapter that made it may parse
            it, order by it or read a format out of it.
        classification: Detected result plus any explicit user override. The only
            authority for the effective track kind.
        title: The name the source gave the track, if any.
        activity: The activity, from explicit source metadata or from the user.

    Raises:
        ValueError: If the track holds no segment or no source key.
    """

    segments: tuple[TrackSegment, ...]
    source: SourceMetadata
    source_key: str
    classification: TrackClassification
    title: str | None = None
    activity: Activity = Activity.UNKNOWN

    def __post_init__(self) -> None:
        """Reject a track without geometry or without an identity."""
        _require_segments(self.segments)
        _require_source_key(self.source_key)

    @property
    def effective_kind(self) -> TrackKind:
        """Return the authoritative kind, honouring an explicit user override."""
        return self.classification.effective_kind

    @property
    def detected_kind(self) -> TrackKind:
        """Return what the classifier found, ignoring any user override."""
        return self.classification.detected.kind

    @property
    def contributes_to_actual_totals(self) -> bool:
        """Report whether this track counts towards actual activity statistics."""
        return self.classification.contributes_to_actual_totals

    @property
    def contributes_to_planned_totals(self) -> bool:
        """Report whether this track counts towards planned statistics."""
        return self.classification.contributes_to_planned_totals

    @property
    def segment_count(self) -> int:
        """Return how many segments this track holds."""
        return len(self.segments)

    @property
    def point_count(self) -> int:
        """Return how many positions this track holds in total."""
        return total_point_count(self.segments)

    @property
    def started_at(self) -> datetime | None:
        """Return the earliest instant the track recorded, if it recorded any.

        This is the extent of the track's own positions. A document-level export
        timestamp is not an activity start and never reaches this value.
        """
        return earliest_time(self.segments)

    @property
    def ended_at(self) -> datetime | None:
        """Return the latest instant the track recorded, if it recorded any."""
        return latest_time(self.segments)

    def with_classification(self, classification: TrackClassification) -> "NormalizedTrack":
        """Return a copy carrying a new classification, leaving the geometry alone."""
        return replace(self, classification=classification)
