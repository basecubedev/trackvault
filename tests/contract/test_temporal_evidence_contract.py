"""Executable contract: a timestamp is not proof that anybody moved.

The rule this file exists to protect:

```
timestamp present  !=  observed movement
```

A planner writes instants onto a route it computed. A recorder writes instants
onto positions it measured. Both produce a file full of timestamps, and the
arithmetic derived from them is identical in shape: a duration, an average
speed, a maximum. Only one of them is a statement about anybody's afternoon.

Analysis is not asked to withhold the arithmetic -- a planned route genuinely
has a derived route speed, and hiding it would be its own kind of lie. What must
never happen is that the arithmetic is *presented* as observed activity. So the
numbers carry the trust their inputs earned, and the eligibility for actual
activity totals is decided from that trust rather than from the presence of the
field.
"""

import pytest

from trackvault.domain import EvidenceCode, TrackKind
from trackvault.domain.temporal_evidence import (
    TemporalEvidence,
    supports_actual_metrics,
    supports_actual_timing,
    temporal_evidence_of,
)

pytestmark = [pytest.mark.contract, pytest.mark.analysis]


def _codes(*codes: EvidenceCode) -> tuple[str, ...]:
    """Return evidence codes as an importer stores them."""
    return tuple(code.value for code in codes)


# --- What earns which level of trust ----------------------------------------


def test_a_measuring_receiver_earns_observed_timing() -> None:
    """A device that reports how well it measured was measuring.

    Receiver quality is the evidence. A planner has no dilution of precision to
    write down, because it never took a fix.
    """
    evidence = _codes(
        EvidenceCode.TRACK_ELEMENT_PRESENT,
        EvidenceCode.TIMESTAMPS_PRESENT,
        EvidenceCode.GPS_ACCURACY_PRESENT,
    )

    assert temporal_evidence_of(evidence) is TemporalEvidence.OBSERVED


def test_a_measured_heading_earns_observed_timing_too() -> None:
    """A course is measured while moving, or not at all."""
    evidence = _codes(
        EvidenceCode.TIMESTAMPS_PRESENT,
        EvidenceCode.COURSE_MEASUREMENTS_PRESENT,
    )

    assert temporal_evidence_of(evidence) is TemporalEvidence.OBSERVED


def test_navigation_instructions_earn_estimated_timing() -> None:
    """Turn-by-turn instructions exist because something computed a route.

    A recording has nowhere to get them from, so the instants beside them are a
    planner's schedule. That is a real statement about time -- it is simply not
    a statement about anybody having travelled it.
    """
    evidence = _codes(
        EvidenceCode.TIMESTAMPS_PRESENT,
        EvidenceCode.ROUTE_INSTRUCTIONS_PRESENT,
        EvidenceCode.MEASUREMENT_METADATA_ABSENT,
    )

    assert temporal_evidence_of(evidence) is TemporalEvidence.ESTIMATED


def test_timestamps_alone_prove_nothing_about_their_origin() -> None:
    """The whole point. Instants with no measurement evidence stay unknown.

    This is the shape a planning export takes when it writes plausible instants
    and nothing else: structurally it is indistinguishable from a stripped
    recording, so neither claim may be made.
    """
    evidence = _codes(
        EvidenceCode.TRACK_ELEMENT_PRESENT,
        EvidenceCode.TIMESTAMPS_PRESENT,
        EvidenceCode.MEASUREMENT_METADATA_ABSENT,
    )

    assert temporal_evidence_of(evidence) is TemporalEvidence.UNKNOWN


def test_a_track_without_instants_has_no_timing_to_trust() -> None:
    """Nothing to be observed or estimated about."""
    evidence = _codes(
        EvidenceCode.ROUTE_ELEMENT_PRESENT,
        EvidenceCode.TIMESTAMPS_ABSENT,
    )

    assert temporal_evidence_of(evidence) is TemporalEvidence.UNKNOWN


def test_no_evidence_at_all_is_unknown_rather_than_a_default() -> None:
    """Failing to unknown beats invented certainty, here as everywhere."""
    assert temporal_evidence_of(()) is TemporalEvidence.UNKNOWN


def test_measurement_evidence_outranks_a_planners_instructions() -> None:
    """A navigation app recording while it navigates produces both.

    It really was measuring, so what it measured is observed. The instructions
    say something about the route, not about the fixes taken along it.
    """
    evidence = _codes(
        EvidenceCode.TIMESTAMPS_PRESENT,
        EvidenceCode.ROUTE_INSTRUCTIONS_PRESENT,
        EvidenceCode.GPS_ACCURACY_PRESENT,
    )

    assert temporal_evidence_of(evidence) is TemporalEvidence.OBSERVED


def test_an_unknown_code_from_a_later_version_decides_nothing() -> None:
    """Evidence this build does not know is not evidence this build may act on."""
    assert temporal_evidence_of(("some_code_from_a_later_release",)) is TemporalEvidence.UNKNOWN


def test_the_source_application_is_never_consulted() -> None:
    """There is no vendor name in the rule, and no track kind either.

    The derivation reads evidence codes and nothing else. Anything that reached
    for the exporting application or for the detected kind would be inferring
    the origin of the data from a label somebody else chose.
    """
    stripped_recording = _codes(
        EvidenceCode.TIMESTAMPS_PRESENT, EvidenceCode.MEASUREMENT_METADATA_ABSENT
    )
    planning_export = _codes(
        EvidenceCode.TIMESTAMPS_PRESENT,
        EvidenceCode.MEASUREMENT_METADATA_ABSENT,
        EvidenceCode.EXTERNAL_LINK_PRESENT,
    )

    assert temporal_evidence_of(stripped_recording) is temporal_evidence_of(planning_export)


# --- What that trust is allowed to authorise --------------------------------


@pytest.mark.parametrize(
    ("kind", "eligible"),
    [
        (TrackKind.RECORDED, True),
        (TrackKind.PLANNED, False),
        (TrackKind.UNKNOWN, False),
    ],
)
def test_only_a_recorded_track_contributes_to_actual_totals(
    kind: TrackKind, eligible: bool
) -> None:
    """Geometry is eligible on the effective kind alone.

    Distance and ascent need no clock, so a recorded track contributes them
    whatever its instants turn out to be worth.
    """
    assert supports_actual_metrics(kind) is eligible


@pytest.mark.parametrize(
    ("kind", "evidence", "eligible"),
    [
        (TrackKind.RECORDED, TemporalEvidence.OBSERVED, True),
        (TrackKind.RECORDED, TemporalEvidence.ESTIMATED, False),
        (TrackKind.RECORDED, TemporalEvidence.UNKNOWN, False),
        (TrackKind.PLANNED, TemporalEvidence.OBSERVED, False),
        (TrackKind.UNKNOWN, TemporalEvidence.OBSERVED, False),
    ],
)
def test_actual_timing_needs_both_a_recorded_kind_and_observed_instants(
    kind: TrackKind, evidence: TemporalEvidence, eligible: bool
) -> None:
    """Two independent conditions, both required.

    A recording whose instants nothing vouches for still has a length. What it
    does not have is a moving time anybody may quote as time spent moving.
    """
    assert supports_actual_timing(kind, evidence) is eligible


def test_a_user_correction_cannot_manufacture_observed_timing() -> None:
    """Somebody calling a planned route "recorded" does not make its clock real.

    The override moves the track into the actual set, which is what an override
    is for -- and its synthetic instants stay unfit for actual timing, because
    the user corrected the kind and not the data.
    """
    corrected = TrackKind.RECORDED

    assert supports_actual_metrics(corrected) is True
    assert supports_actual_timing(corrected, TemporalEvidence.UNKNOWN) is False
