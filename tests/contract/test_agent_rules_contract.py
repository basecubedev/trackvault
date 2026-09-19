"""Contract for the canonical agent rules.

There is exactly one complete rule source. The other agent files are entry points
that reference it. This test checks that structure and the semantic anchors of the
rules -- not their exact wording, so that the documents can be edited freely.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CANONICAL_RULES = "docs/developer/agent-rules.md"
ENTRY_POINTS = ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md")

# Topics the canonical rules must cover. Substring match, case-insensitive.
REQUIRED_RULE_ANCHORS = (
    "scope discipline",
    "contract-first",
    "single source of truth",
    "architecture boundaries",
    "test isolation",
    "deterministic tests",
    "secrets",
    "personal gps data",
    "source code language",
    "docstrings",
    "git discipline",
    "no push without explicit instruction",
    "tooling rules",
    "validation and reporting",
    "prohibited anti-patterns",
    # Source-agnostic architecture and the business invariants that depend on it.
    "input format is never the domain model",
    "single import authority",
    "raw imports are immutable source evidence",
    "source metadata is evidence, not authority",
    "recorded, planned and unknown are distinct",
    "measured, derived and estimated metrics must not be conflated",
    "explicit user overrides beat automatic classification",
    # Engineering baseline for every change.
    "understand before changing",
    "architectural conflict",
    "data integrity",
    "idempotency",
    "background and scheduled processing",
    "error handling",
    "logging",
    "documentation is part of the implementation",
)

# An entry point stays a pointer: short, and never a full copy of every topic.
MAX_ENTRY_POINT_LINES = 40
MAX_ANCHORS_IN_ENTRY_POINT = 5


def _read(relative_path: str) -> str:
    """Return the text of a repository file."""
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.contract
def test_canonical_agent_rules_exist() -> None:
    """The single canonical rule source is present."""
    assert (PROJECT_ROOT / CANONICAL_RULES).is_file()


@pytest.mark.contract
@pytest.mark.parametrize("anchor", REQUIRED_RULE_ANCHORS)
def test_canonical_agent_rules_cover_topic(anchor: str) -> None:
    """The canonical rules address every mandatory topic."""
    assert anchor in _read(CANONICAL_RULES).lower(), f"agent rules do not cover '{anchor}'"


@pytest.mark.contract
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_entry_point_exists(entry_point: str) -> None:
    """Every agent entry point file is present."""
    assert (PROJECT_ROOT / entry_point).is_file()


@pytest.mark.contract
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_entry_point_references_canonical_rules(entry_point: str) -> None:
    """Every entry point points at the canonical rule source."""
    assert CANONICAL_RULES in _read(entry_point)


@pytest.mark.contract
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_entry_point_is_not_a_second_rule_copy(entry_point: str) -> None:
    """Entry points stay pointers instead of duplicating the rule set."""
    text = _read(entry_point)
    line_count = len(text.splitlines())
    assert line_count <= MAX_ENTRY_POINT_LINES, (
        f"{entry_point} has {line_count} lines; keep it a pointer, not a rule copy"
    )

    covered = [anchor for anchor in REQUIRED_RULE_ANCHORS if anchor in text.lower()]
    assert len(covered) <= MAX_ANCHORS_IN_ENTRY_POINT, (
        f"{entry_point} restates too many rule topics ({covered}); they belong in {CANONICAL_RULES}"
    )


@pytest.mark.contract
def test_canonical_rules_are_the_most_detailed_source() -> None:
    """No entry point is longer than the canonical rules."""
    canonical_length = len(_read(CANONICAL_RULES))
    for entry_point in ENTRY_POINTS:
        assert len(_read(entry_point)) < canonical_length
