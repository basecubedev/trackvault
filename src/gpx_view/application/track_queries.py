"""Reading tracks, and correcting what the classifier decided.

These are the query and correction use cases the HTTP layer projects. A track
that does not exist is reported as absence, not as an exception: what that means
over HTTP is the projection's business, not this layer's.
"""

from gpx_view.application.ports import Clock, TrackRepository, TrackSummary
from gpx_view.domain import TrackKind, TrackSegment


class TrackQueries:
    """Answers questions about stored tracks and records user corrections."""

    def __init__(self, repository: TrackRepository, clock: Clock) -> None:
        """Wire the use cases to their ports."""
        self._repository = repository
        self._clock = clock

    def list_tracks(self) -> tuple[TrackSummary, ...]:
        """Return every stored track, without geometry."""
        return self._repository.list_tracks()

    def get_track(self, track_id: int) -> TrackSummary | None:
        """Return one stored track without geometry, or ``None`` if it is unknown."""
        return self._repository.get_track(track_id)

    def get_geometry(self, track_id: int) -> tuple[TrackSegment, ...] | None:
        """Return the segments of one stored track, or ``None`` if it is unknown.

        Geometry is a separate question on purpose: a long recording holds tens of
        thousands of positions, and a listing must not pay for them. Callers can
        read ``point_count`` from the summary before asking for the geometry.
        """
        return self._repository.get_geometry(track_id)

    def override_classification(self, track_id: int, kind: TrackKind) -> TrackSummary | None:
        """Record an explicit user correction and return the updated track.

        The override is the higher authority: it survives reprocessing and it
        decides the effective kind from now on. ``UNKNOWN`` is a valid
        correction -- a user may state that the kind cannot be decided.
        """
        if not self._repository.set_override(track_id, kind, self._clock.now()):
            return None
        return self._repository.get_track(track_id)

    def reset_classification(self, track_id: int) -> TrackSummary | None:
        """Withdraw a user correction, handing authority back to the classifier."""
        if not self._repository.clear_override(track_id):
            return None
        return self._repository.get_track(track_id)
