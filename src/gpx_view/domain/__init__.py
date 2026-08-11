"""Domain layer: the vocabulary and rules of the problem, free of technology.

The domain models activities and routes, not exchange formats. GPX, FIT and TCX
are input formats handled by infrastructure adapters; none of them is the domain
model. See ``docs/technical/architecture.md``.

This package must depend on the Python standard library only, and not even on the
standard library's infrastructure corners (``xml``, ``sqlite3``, ``pathlib``,
``json``, ...). The rule is enforced by
``tests/contract/test_architecture_contract.py``.
"""

from gpx_view.domain.activity import Activity
from gpx_view.domain.classification import ClassificationResult, TrackClassification
from gpx_view.domain.classifier import classify
from gpx_view.domain.evidence import EvidenceCode, in_canonical_order
from gpx_view.domain.geometry import TrackPoint, TrackSegment
from gpx_view.domain.metric_provenance import MetricProvenance
from gpx_view.domain.processing import (
    NORMALIZATION_SCHEMA_VERSION,
    ProcessingProfile,
    ProcessingRun,
    ProcessingStatus,
    is_processing_current,
)
from gpx_view.domain.raw_import import InputChannel, RawImport
from gpx_view.domain.source_metadata import SourceMetadata
from gpx_view.domain.temporal_evidence import (
    MEASUREMENT_EVIDENCE_CODES,
    TemporalEvidence,
    supports_actual_calendar_placement,
    supports_actual_metrics,
    supports_actual_timing,
    temporal_evidence_of,
)
from gpx_view.domain.track import ImportedTrack, NormalizedTrack
from gpx_view.domain.track_kind import TrackKind

__all__ = [
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
    "classify",
    "in_canonical_order",
    "is_processing_current",
    "supports_actual_calendar_placement",
    "supports_actual_metrics",
    "supports_actual_timing",
    "temporal_evidence_of",
]
