"""Executable contracts for the first productive classifier.

The rules are deliberately conservative:

> A false ``UNKNOWN`` is preferable to false certainty.

The classifier sees neutral evidence codes and nothing else. It cannot see a
creator string, a filename or a namespace, which is what makes "source name alone
never decides a kind" a structural property rather than a promise.
"""

import inspect

import pytest

from trackvault.domain import (
    ClassificationResult,
    EvidenceCode,
    TrackClassification,
    TrackKind,
    classify,
)
from trackvault.domain.classifier import CLASSIFIER_METHOD, CLASSIFIER_VERSION

E = EvidenceCode

# Evidence a device produces: it reports how well it measured.
STRONG_RECORDED = (
    E.TRACK_ELEMENT_PRESENT,
    E.TIMESTAMPS_PRESENT,
    E.GPS_ACCURACY_PRESENT,
    E.COURSE_MEASUREMENTS_PRESENT,
)

# Evidence a route planner produces: turn-by-turn instructions exist because a
# route was computed for navigation.
ROUTE_INSTRUCTIONS = (
    E.TRACK_ELEMENT_PRESENT,
    E.TIMESTAMPS_ABSENT,
    E.MEASUREMENT_METADATA_ABSENT,
    E.ROUTE_INSTRUCTIONS_PRESENT,
)

# Evidence a planner produces: the source called this a route, and nothing
# measured anything about it.
STRONG_PLANNED = (
    E.ROUTE_ELEMENT_PRESENT,
    E.TIMESTAMPS_ABSENT,
    E.MEASUREMENT_METADATA_ABSENT,
)


# --- The three outcomes are all reachable -----------------------------------


@pytest.mark.contract
def test_measurement_quality_evidence_supports_recorded() -> None:
    """Receiver quality and heading are things only a measuring device reports."""
    result = classify(STRONG_RECORDED)

    assert result.kind is TrackKind.RECORDED
    assert E.GPS_ACCURACY_PRESENT.value in result.evidence
    assert result.confidence > 0.5


@pytest.mark.contract
def test_a_route_structure_with_no_measurement_supports_planned() -> None:
    """A stated route that nothing measured anything about is planning data."""
    result = classify(STRONG_PLANNED)

    assert result.kind is TrackKind.PLANNED
    assert E.ROUTE_ELEMENT_PRESENT.value in result.evidence
    assert result.confidence > 0.5


@pytest.mark.contract
@pytest.mark.parametrize("link", [E.EXTERNAL_LINK_PRESENT, E.SOURCE_LINK_PRESENT])
def test_a_generic_external_link_and_missing_measurements_stay_unknown(link: EvidenceCode) -> None:
    """An external link plus an absence is not enough to call something planned.

    GPX ``<link>`` means "here is a related web resource". Recorders write their
    own home page into it as readily as planners write a permalink, so the
    observation says nothing about how the geometry was produced. Pairing it with
    ``measurement_metadata_absent`` only adds a second statement about what is
    *missing*, and a recording stripped of its metadata looks exactly the same.

    This is the direction of error the project chose: a false ``UNKNOWN`` beats
    false certainty, and a user override exists for the rest.

    The deprecated code is weighed here too. It is still present in stored
    results, and retiring a code means it stops deciding anything, not that it
    quietly keeps deciding under an old name.
    """
    result = classify(
        (
            E.TRACK_ELEMENT_PRESENT,
            E.TIMESTAMPS_PRESENT,
            link,
            E.MEASUREMENT_METADATA_ABSENT,
        )
    )

    assert result.kind is TrackKind.UNKNOWN
    assert result.confidence == pytest.approx(0.0)


@pytest.mark.contract
def test_navigation_instructions_support_planned() -> None:
    """Turn-by-turn instructions exist because a route was computed for them.

    A recording has nowhere to get them from, which makes this a property of the
    data rather than of whoever wrote the file.
    """
    result = classify(ROUTE_INSTRUCTIONS)

    assert result.kind is TrackKind.PLANNED
    assert E.ROUTE_INSTRUCTIONS_PRESENT.value in result.evidence


@pytest.mark.contract
@pytest.mark.parametrize(
    "single",
    [E.ROUTE_INSTRUCTIONS_PRESENT, E.ROUTE_ELEMENT_PRESENT],
)
def test_a_single_structural_observation_never_proves_planned(single: EvidenceCode) -> None:
    """How the geometry was produced is not proof that nobody travelled it.

    A route element and a turn instruction each say something about how the
    geometry came about. On their own they say nothing about whether the route
    was then ridden.
    """
    assert classify((E.TRACK_ELEMENT_PRESENT, single)).kind is TrackKind.UNKNOWN


@pytest.mark.contract
def test_thin_measurement_against_instructions_stays_unknown() -> None:
    """One measurement signal against a planning signal decides nothing."""
    result = classify(
        (
            E.TRACK_ELEMENT_PRESENT,
            E.TIMESTAMPS_PRESENT,
            E.GPS_ACCURACY_PRESENT,
            E.ROUTE_INSTRUCTIONS_PRESENT,
        )
    )

    assert result.kind is TrackKind.UNKNOWN


@pytest.mark.contract
def test_measurements_outweigh_instructions_when_they_are_strong() -> None:
    """Riding a computed route is still riding: the receiver measured it.

    Turn instructions say a route was planned beforehand. They do not undo two
    independent measurement signals, which only a device that actually moved
    through the positions can produce.
    """
    result = classify((*STRONG_RECORDED, E.ROUTE_INSTRUCTIONS_PRESENT))

    assert result.kind is TrackKind.RECORDED


@pytest.mark.contract
def test_contradictory_evidence_stays_unknown() -> None:
    """Measured quality against a computed route is not a decidable case."""
    result = classify(
        (
            E.TRACK_ELEMENT_PRESENT,
            E.TIMESTAMPS_PRESENT,
            E.GPS_ACCURACY_PRESENT,
            E.ROUTE_INSTRUCTIONS_PRESENT,
        )
    )

    assert result.kind is TrackKind.UNKNOWN


@pytest.mark.contract
def test_an_external_link_does_not_contradict_measurement_evidence() -> None:
    """A link is not a counter-argument, because it is not an argument.

    A device reported how well it measured. That the file also mentions a website
    -- the application's own, most likely -- takes nothing away from it.
    """
    result = classify(
        (
            E.TRACK_ELEMENT_PRESENT,
            E.TIMESTAMPS_PRESENT,
            E.GPS_ACCURACY_PRESENT,
            E.EXTERNAL_LINK_PRESENT,
        )
    )

    assert result.kind is TrackKind.RECORDED


@pytest.mark.contract
def test_thin_evidence_stays_unknown() -> None:
    """A bare track with nothing but positions decides nothing."""
    assert classify((E.TRACK_ELEMENT_PRESENT, E.MEASUREMENT_METADATA_ABSENT)).kind is (
        TrackKind.UNKNOWN
    )


@pytest.mark.contract
def test_no_evidence_at_all_stays_unknown() -> None:
    """Absence of observation is never a verdict."""
    result = classify(())

    assert result.kind is TrackKind.UNKNOWN
    assert result.evidence == ()


# --- What must never decide a kind ------------------------------------------


@pytest.mark.contract
def test_the_classifier_cannot_see_the_source_at_all() -> None:
    """`source == komoot` cannot decide a kind if the classifier never receives it."""
    signature = inspect.signature(classify)

    assert list(signature.parameters) == ["evidence"]


@pytest.mark.contract
@pytest.mark.parametrize(
    "evidence",
    [
        (E.TIMESTAMPS_PRESENT,),
        (E.TRACK_ELEMENT_PRESENT, E.TIMESTAMPS_PRESENT),
        (E.TIMESTAMPS_PRESENT, E.ACTIVITY_METADATA_PRESENT),
    ],
)
def test_timestamps_alone_never_prove_recorded(evidence: tuple[EvidenceCode, ...]) -> None:
    """Route planners write synthetic times; present timestamps prove nothing."""
    assert classify(evidence).kind is TrackKind.UNKNOWN


@pytest.mark.contract
@pytest.mark.parametrize(
    "evidence",
    [
        (E.TIMESTAMPS_ABSENT,),
        (E.TRACK_ELEMENT_PRESENT, E.TIMESTAMPS_ABSENT),
        (E.TIMESTAMPS_ABSENT, E.MEASUREMENT_METADATA_ABSENT),
    ],
)
def test_missing_timestamps_alone_never_prove_planned(
    evidence: tuple[EvidenceCode, ...],
) -> None:
    """A recording stripped of time data is still a recording."""
    assert classify(evidence).kind is TrackKind.UNKNOWN


@pytest.mark.contract
def test_an_external_link_alone_never_proves_planned() -> None:
    """Linking back to a web page is not proof that nobody walked the route."""
    assert classify((E.TRACK_ELEMENT_PRESENT, E.EXTERNAL_LINK_PRESENT)).kind is TrackKind.UNKNOWN


@pytest.mark.contract
def test_a_track_element_alone_never_proves_recorded() -> None:
    """A GPX `<trk>` is a container, not a statement about how it came to be."""
    assert classify((E.TRACK_ELEMENT_PRESENT,)).kind is TrackKind.UNKNOWN


@pytest.mark.contract
def test_an_explicit_activity_never_decides_a_kind() -> None:
    """Activity is orthogonal: a planned cycling route is still planned."""
    with_activity = classify((*STRONG_RECORDED, E.ACTIVITY_METADATA_PRESENT))
    without_activity = classify(STRONG_RECORDED)

    assert with_activity.kind is without_activity.kind


# --- Explainability and stability -------------------------------------------


@pytest.mark.contract
def test_every_result_names_the_classifier_that_produced_it() -> None:
    """A stored verdict must be re-evaluable when the classifier changes."""
    result = classify(STRONG_RECORDED)

    assert result.method == CLASSIFIER_METHOD
    assert result.method_version == CLASSIFIER_VERSION


@pytest.mark.contract
def test_a_result_cites_all_the_evidence_it_weighed() -> None:
    """The explanation lists what was observed, in the canonical vocabulary order.

    The order must not depend on what the parser happened to notice first, so a
    reversed input yields the same stored explanation.
    """
    result = classify(reversed(STRONG_PLANNED))

    assert result.evidence == (
        E.ROUTE_ELEMENT_PRESENT.value,
        E.TIMESTAMPS_ABSENT.value,
        E.MEASUREMENT_METADATA_ABSENT.value,
    )
    assert result.evidence == classify(STRONG_PLANNED).evidence


@pytest.mark.contract
def test_classification_is_deterministic() -> None:
    """The same observations always produce the same verdict."""
    assert classify(STRONG_RECORDED) == classify(STRONG_RECORDED)


@pytest.mark.contract
def test_duplicate_observations_do_not_strengthen_a_verdict() -> None:
    """Noticing the same fact twice is not two facts."""
    once = classify(STRONG_PLANNED)
    twice = classify((*STRONG_PLANNED, *STRONG_PLANNED))

    assert once == twice


# --- Reprocessing and the user override -------------------------------------


@pytest.mark.contract
def test_reclassification_replaces_the_detected_result() -> None:
    """A better classifier is allowed to change its own mind."""
    classification = TrackClassification(detected=classify(STRONG_PLANNED))

    reprocessed = classification.reclassified(classify(STRONG_RECORDED))

    assert reprocessed.detected.kind is TrackKind.RECORDED
    assert reprocessed.effective_kind is TrackKind.RECORDED


@pytest.mark.contract
def test_reclassification_never_overwrites_a_user_override() -> None:
    """A parser or classifier upgrade must not overrule a manual correction."""
    corrected = TrackClassification(detected=classify(STRONG_PLANNED)).overridden_with(
        TrackKind.RECORDED
    )

    reprocessed = corrected.reclassified(classify(STRONG_PLANNED))

    assert reprocessed.detected.kind is TrackKind.PLANNED
    assert reprocessed.effective_kind is TrackKind.RECORDED
    assert reprocessed.is_overridden


@pytest.mark.contract
def test_a_classifier_result_is_a_plain_classification_result() -> None:
    """The classifier produces the documented shape, not a variant of its own."""
    assert isinstance(classify(STRONG_RECORDED), ClassificationResult)
