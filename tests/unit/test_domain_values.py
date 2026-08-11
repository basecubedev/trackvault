"""Unit tests for the domain value types.

These pin the business contracts documented in ``docs/technical/contracts.md``.
"""

import pytest

from trackvault.domain import Activity, MetricProvenance, TrackKind


@pytest.mark.unit
def test_track_kind_allows_unknown() -> None:
    """A track may stay unclassified; the application never guesses."""
    assert TrackKind.UNKNOWN in TrackKind


@pytest.mark.unit
def test_track_kind_separates_recorded_from_planned() -> None:
    """Recorded and planned are distinct kinds and never collapse into one."""
    assert TrackKind.RECORDED != TrackKind.PLANNED
    assert set(TrackKind) == {TrackKind.RECORDED, TrackKind.PLANNED, TrackKind.UNKNOWN}


@pytest.mark.unit
def test_track_kind_values_are_stable_wire_strings() -> None:
    """The string values are part of the contract with future storage and HTTP."""
    assert [kind.value for kind in TrackKind] == ["recorded", "planned", "unknown"]


@pytest.mark.unit
def test_metric_provenance_distinguishes_three_origins() -> None:
    """Measured, derived and estimated values are different business statements."""
    assert set(MetricProvenance) == {
        MetricProvenance.MEASURED,
        MetricProvenance.DERIVED,
        MetricProvenance.ESTIMATED,
    }


@pytest.mark.unit
def test_metric_provenance_values_are_stable_wire_strings() -> None:
    """The string values are part of the contract with future storage and HTTP."""
    assert [origin.value for origin in MetricProvenance] == ["measured", "derived", "estimated"]


@pytest.mark.unit
def test_activity_allows_unknown() -> None:
    """Activity is never guessed, so it may stay undecided."""
    assert Activity.UNKNOWN in Activity


@pytest.mark.unit
def test_activity_values_are_stable_wire_strings() -> None:
    """The string values are part of the contract with future storage and HTTP."""
    assert [activity.value for activity in Activity] == [
        "walking",
        "hiking",
        "cycling",
        "running",
        "scooter",
        "motorcycle",
        "other",
        "unknown",
    ]
