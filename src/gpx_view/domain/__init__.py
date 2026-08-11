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
from gpx_view.domain.metric_provenance import MetricProvenance
from gpx_view.domain.processing import (
    NORMALIZATION_SCHEMA_VERSION,
    ProcessingRun,
    ProcessingStatus,
)
from gpx_view.domain.raw_import import InputChannel, RawImport
from gpx_view.domain.track_kind import TrackKind

__all__ = [
    "NORMALIZATION_SCHEMA_VERSION",
    "Activity",
    "ClassificationResult",
    "InputChannel",
    "MetricProvenance",
    "ProcessingRun",
    "ProcessingStatus",
    "RawImport",
    "TrackClassification",
    "TrackKind",
]
