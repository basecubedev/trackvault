"""Executable business contracts for tracks.

These tests pin the invariants documented in ``docs/technical/contracts.md``. They
protect behaviour, not class structure: a refactoring may move these rules, but it
may not change their answers.
"""

import pytest

from gpx_view.domain import (
    Activity,
    ClassificationResult,
    MetricProvenance,
    TrackClassification,
    TrackKind,
)


def _detected(kind: TrackKind, *evidence: str, confidence: float = 0.9) -> ClassificationResult:
    """Build a classification result for a test scenario."""
    return ClassificationResult(
        kind=kind,
        confidence=confidence,
        method="test-classifier",
        method_version="1",
        evidence=evidence,
    )


# --- Unknown safety ---------------------------------------------------------


@pytest.mark.contract
def test_a_classification_may_remain_unknown_without_any_evidence() -> None:
    """Insufficient evidence is a valid answer, not a failure to be papered over."""
    result = ClassificationResult(
        kind=TrackKind.UNKNOWN,
        confidence=0.0,
        method="test-classifier",
        method_version="1",
    )

    assert result.kind is TrackKind.UNKNOWN
    assert result.evidence == ()


@pytest.mark.contract
@pytest.mark.parametrize("kind", [TrackKind.RECORDED, TrackKind.PLANNED])
def test_a_confident_classification_must_state_its_evidence(kind: TrackKind) -> None:
    """Recorded or planned may not be claimed without saying why."""
    with pytest.raises(ValueError, match="must state its evidence"):
        ClassificationResult(
            kind=kind,
            confidence=0.99,
            method="test-classifier",
            method_version="1",
        )


@pytest.mark.contract
def test_a_classification_stays_explainable() -> None:
    """Confidence, method and version travel with every result."""
    result = _detected(TrackKind.RECORDED, "gps_accuracy_present", confidence=0.97)

    assert result.confidence == pytest.approx(0.97)
    assert result.method == "test-classifier"
    assert result.method_version == "1"
    assert result.evidence == ("gps_accuracy_present",)


# --- Detected vs. effective classification ----------------------------------


@pytest.mark.contract
def test_detected_kind_applies_while_there_is_no_override() -> None:
    """Without a user correction the classifier's answer is effective."""
    classification = TrackClassification(detected=_detected(TrackKind.PLANNED, "planner_source"))

    assert classification.effective_kind is TrackKind.PLANNED
    assert not classification.is_overridden


@pytest.mark.contract
def test_user_override_beats_the_detected_classification() -> None:
    """An explicit user correction is the higher authority."""
    classification = TrackClassification(
        detected=_detected(TrackKind.PLANNED, "synthetic_timestamps", confidence=0.72),
    ).overridden_with(TrackKind.RECORDED)

    assert classification.detected.kind is TrackKind.PLANNED
    assert classification.effective_kind is TrackKind.RECORDED
    assert classification.is_overridden


@pytest.mark.contract
def test_reprocessing_keeps_an_existing_user_override() -> None:
    """A classifier upgrade must not silently overwrite a manual correction."""
    classification = TrackClassification(
        detected=_detected(TrackKind.PLANNED, "synthetic_timestamps"),
    ).overridden_with(TrackKind.RECORDED)

    reprocessed = classification.reclassified(
        _detected(TrackKind.PLANNED, "planner_source", "no_gps_accuracy", confidence=0.99)
    )

    assert reprocessed.detected.evidence == ("planner_source", "no_gps_accuracy")
    assert reprocessed.effective_kind is TrackKind.RECORDED


@pytest.mark.contract
def test_a_user_may_override_to_unknown() -> None:
    """A user may state that the kind cannot be decided."""
    classification = TrackClassification(
        detected=_detected(TrackKind.RECORDED, "gps_accuracy_present"),
    ).overridden_with(TrackKind.UNKNOWN)

    assert classification.effective_kind is TrackKind.UNKNOWN


@pytest.mark.contract
def test_withdrawing_an_override_restores_the_detected_kind() -> None:
    """Removing a correction hands authority back to the classifier."""
    classification = (
        TrackClassification(detected=_detected(TrackKind.PLANNED, "planner_source"))
        .overridden_with(TrackKind.RECORDED)
        .without_override()
    )

    assert classification.effective_kind is TrackKind.PLANNED
    assert not classification.is_overridden


# --- Actual vs. planned aggregates ------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize(
    ("kind", "actual", "planned"),
    [
        (TrackKind.RECORDED, True, False),
        (TrackKind.PLANNED, False, True),
        (TrackKind.UNKNOWN, False, False),
    ],
)
def test_only_recorded_tracks_reach_actual_totals(
    kind: TrackKind, actual: bool, planned: bool
) -> None:
    """Recorded distance counts as actual, planned counts as planned, unknown neither."""
    assert kind.contributes_to_actual_totals is actual
    assert kind.contributes_to_planned_totals is planned


@pytest.mark.contract
def test_unknown_is_never_silently_assigned_to_either_set() -> None:
    """Unknown belongs to neither aggregate and is not quietly reclassified."""
    assert not TrackKind.UNKNOWN.contributes_to_actual_totals
    assert not TrackKind.UNKNOWN.contributes_to_planned_totals


@pytest.mark.contract
def test_aggregation_follows_the_effective_kind_not_the_detected_one() -> None:
    """A track the user corrected to recorded counts towards actual totals."""
    corrected_to_recorded = TrackClassification(
        detected=_detected(TrackKind.PLANNED, "synthetic_timestamps"),
    ).overridden_with(TrackKind.RECORDED)

    assert corrected_to_recorded.contributes_to_actual_totals
    assert not corrected_to_recorded.contributes_to_planned_totals


@pytest.mark.contract
def test_a_planned_track_has_no_path_into_actual_totals() -> None:
    """Without a user override, a planned track never counts as actual."""
    planned = TrackClassification(detected=_detected(TrackKind.PLANNED, "planner_source"))

    assert not planned.contributes_to_actual_totals
    assert planned.contributes_to_planned_totals


# --- Format and source independence -----------------------------------------


@pytest.mark.contract
def test_track_kind_is_independent_of_activity() -> None:
    """Kind and activity are separate dimensions.

    ``unknown`` is the only string both value spaces share, because either
    dimension may legitimately be undecided. Any further overlap would mean one
    enum had started to encode the other's concept.
    """
    shared = {kind.value for kind in TrackKind} & {activity.value for activity in Activity}

    assert shared == {"unknown"}


@pytest.mark.contract
def test_activity_taxonomy_stays_small_and_flat() -> None:
    """The activity vocabulary is a flat set, not a sport hierarchy."""
    assert {activity.value for activity in Activity} == {
        "walking",
        "hiking",
        "cycling",
        "running",
        "scooter",
        "motorcycle",
        "other",
        "unknown",
    }


@pytest.mark.contract
def test_metric_provenance_keeps_three_distinct_origins() -> None:
    """Measured, derived and estimated values are different business statements."""
    origins = [origin.value for origin in MetricProvenance]

    assert origins == ["measured", "derived", "estimated"]
    assert len(set(origins)) == len(origins)
