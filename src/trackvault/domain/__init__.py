"""Domain layer: the vocabulary and rules of the problem, free of technology.

The domain models activities and routes, not exchange formats. GPX, FIT and TCX
are input formats handled by infrastructure adapters; none of them is the domain
model. See ``docs/technical/architecture.md``.

This package must depend on the Python standard library only, and not even on the
standard library's infrastructure corners (``xml``, ``sqlite3``, ``pathlib``,
``json``, ...). The rule is enforced by
``tests/contract/test_architecture_contract.py``.
"""

from trackvault.domain.activity import Activity
from trackvault.domain.classification import ClassificationResult, TrackClassification
from trackvault.domain.classifier import classify
from trackvault.domain.evidence import EvidenceCode, in_canonical_order
from trackvault.domain.geometry import (
    TrackPoint,
    TrackSegment,
    recording_fingerprint,
    shape_fingerprint,
)
from trackvault.domain.metric_provenance import MetricProvenance
from trackvault.domain.processing import (
    NORMALIZATION_SCHEMA_VERSION,
    ProcessingProfile,
    ProcessingRun,
    ProcessingStatus,
    is_processing_current,
)
from trackvault.domain.raw_import import InputChannel, RawImport
from trackvault.domain.source_metadata import SourceMetadata
from trackvault.domain.temporal_evidence import (
    MEASUREMENT_EVIDENCE_CODES,
    TemporalEvidence,
    supports_actual_calendar_placement,
    supports_actual_metrics,
    supports_actual_timing,
    temporal_evidence_of,
)
from trackvault.domain.track import ImportedTrack, NormalizedTrack
from trackvault.domain.track_kind import TrackKind
from trackvault.domain.user_metadata import (
    EMPTY_USER_METADATA,
    MAX_NOTE_LENGTH,
    MAX_TITLE_LENGTH,
    UserTrackMetadata,
    effective_title,
    normalize_note,
    normalize_title,
)

__all__ = [
    "EMPTY_USER_METADATA",
    "MAX_NOTE_LENGTH",
    "MAX_TITLE_LENGTH",
    "MEASUREMENT_EVIDENCE_CODES",
    "NORMALIZATION_SCHEMA_VERSION",
    "Activity",
    "ClassificationResult",
    "EvidenceCode",
    "ImportedTrack",
    "InputChannel",
    "MetricProvenance",
    "NormalizedTrack",
    "ProcessingProfile",
    "ProcessingRun",
    "ProcessingStatus",
    "RawImport",
    "SourceMetadata",
    "TemporalEvidence",
    "TrackClassification",
    "TrackKind",
    "TrackPoint",
    "TrackSegment",
    "UserTrackMetadata",
    "classify",
    "effective_title",
    "in_canonical_order",
    "is_processing_current",
    "normalize_note",
    "normalize_title",
    "recording_fingerprint",
    "shape_fingerprint",
    "supports_actual_calendar_placement",
    "supports_actual_metrics",
    "supports_actual_timing",
    "temporal_evidence_of",
]
