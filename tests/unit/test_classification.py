"""Unit tests for the classification value objects."""

import dataclasses

import pytest

from gpx_view.domain import ClassificationResult, TrackClassification, TrackKind


@pytest.mark.unit
@pytest.mark.parametrize("confidence", [-0.01, 1.01, 2.0, -1.0])
def test_confidence_outside_the_unit_interval_is_rejected(confidence: float) -> None:
    """Confidence is a probability-like value in [0.0, 1.0]."""
    with pytest.raises(ValueError, match=r"confidence must be within \[0.0, 1.0\]"):
        ClassificationResult(
            kind=TrackKind.UNKNOWN,
            confidence=confidence,
            method="test",
            method_version="1",
        )


@pytest.mark.unit
@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
def test_confidence_at_the_interval_bounds_is_accepted(confidence: float) -> None:
    """Both ends of the interval are valid."""
    result = ClassificationResult(
        kind=TrackKind.UNKNOWN,
        confidence=confidence,
        method="test",
        method_version="1",
    )

    assert result.confidence == pytest.approx(confidence)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "version"),
    [("", "1"), ("   ", "1"), ("test", ""), ("test", " ")],
)
def test_an_unidentified_classifier_is_rejected(method: str, version: str) -> None:
    """A result that cannot be attributed cannot be re-evaluated later."""
    with pytest.raises(ValueError, match="method"):
        ClassificationResult(
            kind=TrackKind.UNKNOWN,
            confidence=0.0,
            method=method,
            method_version=version,
        )


@pytest.mark.unit
def test_blank_evidence_codes_are_rejected() -> None:
    """Evidence has to be readable, not padding."""
    with pytest.raises(ValueError, match="evidence codes must not be blank"):
        ClassificationResult(
            kind=TrackKind.RECORDED,
            confidence=0.9,
            method="test",
            method_version="1",
            evidence=("gps_accuracy_present", "  "),
        )


@pytest.mark.unit
def test_classification_result_is_immutable() -> None:
    """A stored result is a historical fact and cannot be edited in place."""
    result = ClassificationResult(
        kind=TrackKind.UNKNOWN,
        confidence=0.0,
        method="test",
        method_version="1",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.kind = TrackKind.RECORDED  # type: ignore[misc]


@pytest.mark.unit
def test_track_classification_is_immutable_and_updates_by_copy() -> None:
    """Override and reclassification return new values instead of mutating."""
    original = TrackClassification(
        detected=ClassificationResult(
            kind=TrackKind.UNKNOWN,
            confidence=0.0,
            method="test",
            method_version="1",
        )
    )

    overridden = original.overridden_with(TrackKind.RECORDED)

    assert original.override is None
    assert overridden.override is TrackKind.RECORDED
    assert overridden is not original
