"""The first productive classifier: neutral evidence in, a track kind out.

The classifier is source-agnostic by construction. Its only input is a set of
:class:`~gpx_view.domain.evidence.EvidenceCode` values, so it cannot see a creator
string, a filename, a namespace or a vendor name. `source == "komoot"` cannot
decide a kind here because the information never arrives.

The guiding rule is conservatism:

> A false ``UNKNOWN`` is preferable to false certainty.

Two generic observations carry the decision:

*Recorded* -- a device that measures positions reports **how well** it measured.
Receiver quality (dilution of precision, satellite count, fix type) and a measured
heading are produced by hardware, not by a routing engine.

*Planned* -- planning data is geometry that was computed rather than travelled.
It is a route structure, or it carries turn-by-turn navigation instructions --
and it carries no measurement metadata whatsoever.

Everything else -- a track container, the presence or absence of timestamps, an
explicit activity -- is recorded as evidence but decides nothing. Route planners
write synthetic times and recordings get stripped of them, so timing alone is not
a discriminator, and this classifier deliberately makes no attempt to read one out
of timing patterns.

An external link decides nothing either, and version 2 of these rules exists
because version 1 let it. An exchange-format link means "here is a related web
resource": applications write their own home page into it, planners write a
permalink, and both look identical from here. Paired with "nothing measured
anything", which is an absence and describes a stripped recording just as well, it
was enough to reach a verdict -- so an ordinary recording exported by an
application that mentions its website came out `PLANNED`. It is now weighed on
neither side.
"""

from collections.abc import Iterable

from gpx_view.domain.classification import ClassificationResult
from gpx_view.domain.evidence import EvidenceCode, in_canonical_order
from gpx_view.domain.track_kind import TrackKind

CLASSIFIER_METHOD = "evidence-weights"
CLASSIFIER_VERSION = "2"

STRONG = 2
WEAK = 1

RECORDED_WEIGHTS: dict[EvidenceCode, int] = {
    EvidenceCode.GPS_ACCURACY_PRESENT: STRONG,
    EvidenceCode.COURSE_MEASUREMENTS_PRESENT: STRONG,
}

# An external link carries no weight on either side. It is deliberately absent
# from this table rather than present with a weight of zero: the classifier
# weighs what discriminates, and a link does not.
PLANNED_WEIGHTS: dict[EvidenceCode, int] = {
    EvidenceCode.ROUTE_ELEMENT_PRESENT: STRONG,
    EvidenceCode.ROUTE_INSTRUCTIONS_PRESENT: STRONG,
    EvidenceCode.MEASUREMENT_METADATA_ABSENT: WEAK,
    EvidenceCode.TIMESTAMPS_ABSENT: WEAK,
}

# Planned support that is a positive observation rather than an absence. Without
# one of these, "nothing was recorded about this data" is all we know, and that is
# not enough: a recording stripped of its metadata looks exactly the same.
POSITIVE_PLANNED_EVIDENCE = frozenset(
    {
        EvidenceCode.ROUTE_ELEMENT_PRESENT,
        EvidenceCode.ROUTE_INSTRUCTIONS_PRESENT,
    }
)

# The winning side needs this much weight, and this much of a lead.
DECISION_STRENGTH = 2
DECISION_MARGIN = 2

# ...and `planned` additionally needs this many *distinct* observations, so that no
# single structural feature decides on its own. A `<rte>` element or a turn
# instruction says how the geometry was produced, not that nobody ever travelled
# it, which is why `contracts.md` says neither proves the full business semantics
# of `planned`.
MINIMUM_PLANNED_OBSERVATIONS = 2

BASE_CONFIDENCE = 0.55
CONFIDENCE_PER_MARGIN = 0.1
MAX_CONFIDENCE = 0.95


def classify(evidence: Iterable[EvidenceCode]) -> ClassificationResult:
    """Decide a track kind from neutral evidence alone.

    Args:
        evidence: What an importer observed about one track. Duplicates and order
            do not matter; the result cites the observations in canonical order.

    Returns:
        The classification result, including every observation that was weighed
        and the classifier identity, so the verdict stays explainable and can be
        re-evaluated when the rules change.
    """
    observed = in_canonical_order(evidence)
    unique = frozenset(observed)

    recorded_strength = sum(RECORDED_WEIGHTS.get(code, 0) for code in unique)
    planned_strength = sum(PLANNED_WEIGHTS.get(code, 0) for code in unique)

    kind, margin = _decide(recorded_strength, planned_strength, unique)

    return ClassificationResult(
        kind=kind,
        confidence=_confidence(kind, margin),
        method=CLASSIFIER_METHOD,
        method_version=CLASSIFIER_VERSION,
        evidence=tuple(code.value for code in observed),
    )


def _decide(
    recorded_strength: int,
    planned_strength: int,
    observed: frozenset[EvidenceCode],
) -> tuple[TrackKind, int]:
    """Return the decided kind and by how much the winning side led."""
    margin = recorded_strength - planned_strength

    if recorded_strength >= DECISION_STRENGTH and margin >= DECISION_MARGIN:
        return TrackKind.RECORDED, margin

    planned_observations = observed & PLANNED_WEIGHTS.keys()
    if (
        planned_strength >= DECISION_STRENGTH
        and -margin >= DECISION_MARGIN
        and len(planned_observations) >= MINIMUM_PLANNED_OBSERVATIONS
        and observed & POSITIVE_PLANNED_EVIDENCE
    ):
        return TrackKind.PLANNED, -margin

    return TrackKind.UNKNOWN, 0


def _confidence(kind: TrackKind, margin: int) -> float:
    """Return how strongly the evidence supports a decided kind.

    ``UNKNOWN`` scores zero: it is the answer given when the evidence supports no
    kind, so there is no support to express.
    """
    if kind is TrackKind.UNKNOWN:
        return 0.0
    return min(MAX_CONFIDENCE, BASE_CONFIDENCE + CONFIDENCE_PER_MARGIN * margin)
