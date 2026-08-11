"""Track classification: what was detected, and what is business authority.

Two things are deliberately separated here:

* the *detected* classification, which a classifier produced from evidence, and
* the *effective* classification, which an explicit user correction may override.

Reprocessing after a parser or classifier upgrade replaces the detected result and
never silently discards a user correction.

This module carries no heuristic. Deciding which evidence implies which kind is the
job of a later classifier; the rules encoded here are the invariants that any such
classifier must respect.
"""

from dataclasses import dataclass, replace

from gpx_view.domain.track_kind import TrackKind


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """The outcome of one automatic classification run.

    A result is only meaningful together with the evidence it relied on and the
    classifier that produced it, so both are required: a stored result stays
    explainable and can be re-evaluated when the classifier changes.

    Attributes:
        kind: The detected track kind.
        confidence: How strongly the evidence supports ``kind``, within [0.0, 1.0].
        method: Name of the classifier that produced the result.
        method_version: Version of that classifier, so results of different
            generations can be told apart during reprocessing.
        evidence: Stable evidence codes, for example ``gps_accuracy_present`` or
            ``synthetic_timestamps``.

    Raises:
        ValueError: If the confidence is out of range, the classifier is not
            identified, an evidence code is blank, or a ``RECORDED``/``PLANNED``
            result states no evidence at all.
    """

    kind: TrackKind
    confidence: float
    method: str
    method_version: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject results that could not be explained or re-evaluated later."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0.0, 1.0], got {self.confidence!r}")
        if not self.method.strip():
            raise ValueError("method must name the classifier that produced this result")
        if not self.method_version.strip():
            raise ValueError("method_version must identify the classifier version")
        if any(not code.strip() for code in self.evidence):
            raise ValueError("evidence codes must not be blank")
        if self.kind is not TrackKind.UNKNOWN and not self.evidence:
            raise ValueError(
                f"a '{self.kind.value}' classification must state its evidence; "
                "return TrackKind.UNKNOWN when there is none"
            )


@dataclass(frozen=True, slots=True)
class TrackClassification:
    """A detected classification together with an optional explicit user override.

    Attributes:
        detected: The most recent automatic classification result.
        override: A kind the user set explicitly. ``None`` means the detected kind
            applies. ``TrackKind.UNKNOWN`` is a valid override -- a user may state
            that the kind cannot be decided.
    """

    detected: ClassificationResult
    override: TrackKind | None = None

    @property
    def effective_kind(self) -> TrackKind:
        """Return the authoritative kind: an explicit user override wins."""
        return self.detected.kind if self.override is None else self.override

    @property
    def is_overridden(self) -> bool:
        """Report whether a user has corrected the detected classification."""
        return self.override is not None

    @property
    def contributes_to_actual_totals(self) -> bool:
        """Report whether the track counts towards actual activity statistics."""
        return self.effective_kind.contributes_to_actual_totals

    @property
    def contributes_to_planned_totals(self) -> bool:
        """Report whether the track counts towards planned statistics."""
        return self.effective_kind.contributes_to_planned_totals

    def reclassified(self, detected: ClassificationResult) -> "TrackClassification":
        """Return a copy carrying a new detected result, keeping any user override.

        Args:
            detected: The result of a fresh classification run.

        Returns:
            The updated classification. An existing override survives, so a
            classifier upgrade cannot overrule the user.
        """
        return replace(self, detected=detected)

    def overridden_with(self, kind: TrackKind) -> "TrackClassification":
        """Return a copy in which the user has set the kind explicitly."""
        return replace(self, override=kind)

    def without_override(self) -> "TrackClassification":
        """Return a copy in which the user override has been withdrawn."""
        return replace(self, override=None)
