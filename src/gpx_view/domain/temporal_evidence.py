"""How much a track's instants are worth, and what they are allowed to authorise.

A number derived from timestamps is only as good as the timestamps. The metric
provenance vocabulary says how a value was *produced* -- a speed computed from
coordinates and instants is `DERIVED` -- but that is a statement about the
arithmetic, not about what went into it:

```
speed from measured fixes      derived, from observed instants
speed from a planner's route   derived, from instants nobody observed
```

Both are `DERIVED`, and calling them both that and stopping there loses the only
distinction that matters. This module supplies the missing half.

## Presence is not provenance

A planner writes instants onto a route it computed; a recorder writes instants
onto positions it measured. Structurally the two files are the same file. So the
rule here never reasons from the presence of a timestamp, and never from the
exporting application or the detected track kind either -- those would infer the
origin of the data from a label somebody else chose, which is exactly what
`docs/developer/agent-rules.md` forbids the classifier to do.

It reasons from evidence of *measurement*:

```
receiver quality or a measured heading  ->  OBSERVED
turn-by-turn navigation instructions    ->  ESTIMATED
anything else                           ->  UNKNOWN
```

A device that reports how well it measured was measuring. A document carrying
turn-by-turn instructions had a route computed for it, so its instants are a
plan. Everything else fails to `UNKNOWN`, which is permanent and first class:
`UNKNOWN` is not a weaker `OBSERVED`, and it never becomes one by default.

## Trust is separate from calculation

Nothing here withholds a number. An unknown-timing track still gets its elapsed
span, its moving time and its maximum speed derived, because they are real
properties of the path-through-time it describes and hiding them would be its
own kind of lie. What this module decides is narrower and more important: which
of those numbers a reader may add to *actual activity* totals.
"""

from collections.abc import Iterable
from enum import StrEnum

from gpx_view.domain.evidence import EvidenceCode
from gpx_view.domain.track_kind import TrackKind


class TemporalEvidence(StrEnum):
    """What a track's instants have been shown to be.

    Attributes:
        OBSERVED: The instants came from a device that was measuring. Durations
            and speeds derived from them describe something that happened.
        ESTIMATED: The instants came from a planner or a routing engine. They
            are a schedule: real, useful, and not a record of a journey.
        UNKNOWN: Nothing shows which of the two this is, or there are no
            instants at all. The permanent, honest answer -- not a placeholder
            for one of the others.
    """

    OBSERVED = "observed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


MEASUREMENT_EVIDENCE_CODES: tuple[str, ...] = (
    EvidenceCode.GPS_ACCURACY_PRESENT.value,
    EvidenceCode.COURSE_MEASUREMENTS_PRESENT.value,
)
"""Observations only a measuring device can make.

A dilution of precision or a satellite count is a receiver describing its own
fix. A course is a heading measured while moving. Neither has any meaning to
compute for a line somebody drew.

Public because a storage layer has to be able to select the tracks whose timing
was measured without deciding for itself what "measured" means. It projects this
tuple; it does not hold one of its own.
"""

_MEASUREMENT_CODES = frozenset(MEASUREMENT_EVIDENCE_CODES)

_PLANNING_CODES = frozenset({EvidenceCode.ROUTE_INSTRUCTIONS_PRESENT.value})
"""Observations only a route computation produces.

Turn-by-turn instructions exist because something solved a route to generate
them. A recording has nowhere to get them from.
"""


def temporal_evidence_of(evidence: Iterable[str]) -> TemporalEvidence:
    """Return what a track's instants have been shown to be.

    Args:
        evidence: The stable evidence codes an importer observed, as stored on
            the detected classification. Codes this build does not know decide
            nothing -- a later release's vocabulary is not authority for an
            older one's conclusions.

    Returns:
        ``OBSERVED`` when something measured, ``ESTIMATED`` when something
        planned, and ``UNKNOWN`` otherwise. Measurement outranks planning: an
        application that records while it navigates produces both, and it really
        was measuring.
    """
    codes = set(evidence)
    if EvidenceCode.TIMESTAMPS_PRESENT.value not in codes:
        return TemporalEvidence.UNKNOWN
    if codes & _MEASUREMENT_CODES:
        return TemporalEvidence.OBSERVED
    if codes & _PLANNING_CODES:
        return TemporalEvidence.ESTIMATED
    return TemporalEvidence.UNKNOWN


def supports_actual_calendar_placement(evidence: TemporalEvidence) -> bool:
    """Report whether a track's instants may say *when* the activity happened.

    A separate question from what the instants are worth as durations, and a
    stricter one than "does this track carry a time". A period total asks only
    this:

    ```
    timeline time            instants the track's own positions carry
    activity calendar time   the claim that this happened in this period
    ```

    A planner writes plausible instants onto geometry nobody travelled, and a
    recording stripped of its receiver metadata looks identical from here.
    Placing either in a month puts a real distance into a period it has nothing
    to do with -- and nothing about the resulting total looks wrong, which is
    what makes it worth a rule.

    It reads the temporal evidence and nothing else. In particular it does not
    read the track kind: a user correcting a route to ``RECORDED`` is saying
    what the track is, not that its clock was measured, and letting the
    correction reach this answer would make an override a way to manufacture a
    date.
    """
    return evidence is TemporalEvidence.OBSERVED


def supports_actual_metrics(kind: TrackKind) -> bool:
    """Report whether a track may contribute to actual activity totals at all.

    The effective kind decides it, and only the effective kind: distance and
    ascent are properties of geometry, which needs no clock and no opinion about
    one.
    """
    return kind.contributes_to_actual_totals


def supports_actual_timing(kind: TrackKind, evidence: TemporalEvidence) -> bool:
    """Report whether a track's durations and speeds are actual activity timing.

    Two independent conditions, both required. A recording whose instants
    nothing vouches for still has a length; what it does not have is a moving
    time anybody may quote as time spent moving. And a user correcting a route's
    kind moves it into the actual set without making its synthetic clock real --
    the correction is about the kind, not about the data.
    """
    return supports_actual_metrics(kind) and evidence is TemporalEvidence.OBSERVED


__all__ = [
    "MEASUREMENT_EVIDENCE_CODES",
    "TemporalEvidence",
    "supports_actual_calendar_placement",
    "supports_actual_metrics",
    "supports_actual_timing",
    "temporal_evidence_of",
]
