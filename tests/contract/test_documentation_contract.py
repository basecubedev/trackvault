"""Contract for the architecture and business documentation.

The code relies on invariants that only the documents state in full. These tests
check that each invariant is still addressed, by topic anchor rather than by
full-text snapshot, so the documents stay freely editable.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ARCHITECTURE_DOC = "docs/technical/architecture.md"
CONTRACTS_DOC = "docs/technical/contracts.md"

ARCHITECTURE_ANCHORS = (
    "source-agnostic",
    "input format != domain model",
    "canonical normalized track",
    "normalization boundary",
    "raw import",
    "processing provenance",
    "processing currency",
    "import integrity",
    "the autosync boundary",
    "privacy at rest",
    "single import authority",
    "immutable source evidence",
    "projection only",
    "single source of truth",
    "track analysis",
    "analysis currency",
    "aggregation timezone",
    "only current analyses are totalled",
    "a timestamp is not a measurement",
    "stored analysis is untrusted input",
    "deliberately deferred",
)

CONTRACTS_ANCHORS = (
    "input format is never the domain model",
    "raw imports are immutable source evidence",
    "processing provenance is separate from the source",
    "exact_duplicate != semantic_duplicate",
    "detected vs. effective classification",
    "one authority for the effective kind",
    "source metadata is evidence and provenance information, not business authority",
    "measured",
    "derived",
    "estimated",
    "provenance must never be silently discarded or conflated",
    "actual vs. planned aggregates",
    "activity is independent of file format, source and track kind",
    "semantically different output must not claim the same version",
    "a known database record does not prove its managed raw artifact is healthy",
    "an unknown namespace is metadata, not semantic authority",
    "derived metrics are rebuildable and never source authority",
    "analysis semantics require an explicit version bump",
    "silence is not a rest",
    "missing metrics are not zero metrics",
    "activity date must not be inferred from import time",
    "actual and planned aggregates must never be conflated",
    "timestamp presence is not observed movement",
    "analysis derives everything; eligibility is decided separately",
    "persisted derived state is validated before it is interpreted",
    "default statistics never mix analysis profile versions",
)


def _read(relative_path: str) -> str:
    """Return the lower-cased text of a repository document."""
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").lower()


@pytest.mark.contract
@pytest.mark.parametrize("anchor", ARCHITECTURE_ANCHORS)
def test_architecture_document_states_invariant(anchor: str) -> None:
    """The architecture document still states the boundary the code relies on."""
    assert anchor in _read(ARCHITECTURE_DOC), f"{ARCHITECTURE_DOC} no longer states '{anchor}'"


@pytest.mark.parametrize("anchor", CONTRACTS_ANCHORS)
@pytest.mark.contract
def test_contracts_document_states_invariant(anchor: str) -> None:
    """The business contracts document still states the invariant the code enforces."""
    assert anchor in _read(CONTRACTS_DOC), f"{CONTRACTS_DOC} no longer states '{anchor}'"


@pytest.mark.contract
@pytest.mark.parametrize(
    "deferred",
    [
        "analysis algorithms",
        "frontend technology",
        "semantic duplicate",
        "authentication",
        "file system watcher",
    ],
)
def test_deferred_decisions_are_named_rather_than_implemented(deferred: str) -> None:
    """The architecture names what is intentionally not built yet.

    Naming a gap is what stops "preparation" code from being written against a
    guessed interface. The list changes as work lands; what must not change is
    that open decisions are stated instead of silently pre-empted.
    """
    assert deferred in _read(ARCHITECTURE_DOC)


@pytest.mark.contract
@pytest.mark.parametrize(
    "anchor",
    [
        "gpx 1.1 and gpx 1.0",
        "import port",
        "persistence",
        "managed raw storage",
        "input paths",
        "analysis versioning",
        "what is calculated",
    ],
)
def test_the_architecture_documents_the_implemented_pipeline(anchor: str) -> None:
    """What was built is described where the boundaries are described."""
    assert anchor in _read(ARCHITECTURE_DOC)
