"""Classification of a track as actually recorded or merely planned."""

from enum import StrEnum


class TrackKind(StrEnum):
    """How a track came into existence.

    The value is independent of the exchange format a track arrived in. A GPX
    ``<trk>`` element does not prove ``RECORDED``, a ``<rte>`` element does not
    prove ``PLANNED``, present timestamps prove neither, and the exporting
    application proves neither on its own.

    ``UNKNOWN`` is a first-class, permanently valid outcome. The application must
    never force a track into ``RECORDED`` or ``PLANNED`` when the available
    evidence is insufficient.
    """

    RECORDED = "recorded"
    PLANNED = "planned"
    UNKNOWN = "unknown"

    @property
    def contributes_to_actual_totals(self) -> bool:
        """Report whether this kind counts towards actual activity statistics.

        Only recorded tracks do. Planned and unknown tracks must never reach
        ``actual_*`` aggregates such as monthly or yearly distance. Callers pass
        the *effective* kind, which honours an explicit user override.
        """
        return self is TrackKind.RECORDED

    @property
    def contributes_to_planned_totals(self) -> bool:
        """Report whether this kind counts towards planned statistics.

        Only planned tracks do. Unknown tracks belong to neither set and must not
        be silently assigned to one of them.
        """
        return self is TrackKind.PLANNED
