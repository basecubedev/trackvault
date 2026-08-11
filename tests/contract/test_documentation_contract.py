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
    "single import authority",
    "immutable source evidence",
    "projection only",
    "single source of truth",
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
def test_deferred_decisions_are_named_rather_than_implemented() -> None:
    """The architecture names what is intentionally not built yet."""
    architecture = _read(ARCHITECTURE_DOC)

    for deferred in ("normalizedtrack", "trackimporter", "persistence schema"):
        assert deferred in architecture
