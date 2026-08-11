"""What a track was used for, independent of format, source and track kind."""

from enum import StrEnum


class Activity(StrEnum):
    """The activity a track belongs to.

    Activity is orthogonal to :class:`~trackvault.domain.track_kind.TrackKind`: a
    planned cycling route and a recorded cycling ride share this value and differ
    only in kind.

    The taxonomy is deliberately small and flat -- no sport hierarchy. Values come
    from explicit source metadata or from the user; the classifier does not guess
    them, so ``UNKNOWN`` is a normal and permanently valid outcome. ``WALKING``
    and ``HIKING`` are separate values because the distinction matters for
    statistics, but nothing may infer one from the other without evidence.
    """

    WALKING = "walking"
    HIKING = "hiking"
    CYCLING = "cycling"
    RUNNING = "running"
    SCOOTER = "scooter"
    MOTORCYCLE = "motorcycle"
    OTHER = "other"
    UNKNOWN = "unknown"
