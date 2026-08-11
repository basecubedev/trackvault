"""Reading tracks, and correcting what the classifier decided.

These are the query and correction use cases the HTTP layer projects. A track
that does not exist is reported as absence, not as an exception: what that means
over HTTP is the projection's business, not this layer's.
"""

from datetime import datetime
from enum import Enum, auto
from zoneinfo import ZoneInfo

from gpx_view.application.analysis import InstalledAnalysis
from gpx_view.application.calendar import period_window
from gpx_view.application.ports import (
    AnalysisAvailability,
    Clock,
    TrackOrder,
    TrackPage,
    TrackQuery,
    TrackRepository,
    TrackSummary,
)
from gpx_view.domain import (
    Activity,
    TrackKind,
    TrackSegment,
    UserTrackMetadata,
    normalize_note,
    normalize_title,
)


class Unchanged(Enum):
    """A field a partial update did not mention.

    ``None`` already means something here -- clear this value -- so "the caller
    said nothing about it" needs a token of its own. Without one, a request that
    only renames a track would silently delete its note, which is the classic
    way a PATCH endpoint loses data.
    """

    SENTINEL = auto()


UNCHANGED = Unchanged.SENTINEL
"""The default of every field of a partial metadata update."""

DEFAULT_PAGE_SIZE = 50
"""How many tracks a listing returns when nobody says.

Enough to fill a screen, few enough that the default costs nothing. The
repository decides the maximum; this is only the default.
"""


class TrackQueries:
    """Answers questions about stored tracks and records user corrections."""

    def __init__(
        self,
        repository: TrackRepository,
        clock: Clock,
        timezone: str,
        analysis: InstalledAnalysis,
    ) -> None:
        """Wire the use cases to their ports, the aggregation zone and currency.

        The zone belongs here for the same reason it belongs to the statistics
        queries: which local month an instant falls in is one decision, and a
        listing filtered by month has to draw its boundary exactly where the
        monthly chart drew it, or clicking a bar shows the wrong tracks.

        The installed analysis belongs here for the same shape of reason. A
        listing has to say what each track's metrics amount to, and it has to
        reach the same verdict the track's own analysis resource does. Handing
        the repository the *values* of the installed profile lets it filter and
        order on that verdict in SQL without owning the question.
        """
        self._repository = repository
        self._clock = clock
        self._zone = ZoneInfo(timezone)
        self._analysis = analysis

    @property
    def max_page_size(self) -> int:
        """Return the largest page the archive will return."""
        return self._repository.max_page_size

    def list_tracks(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        kind: TrackKind | None = None,
        activity: Activity | None = None,
        year: int | None = None,
        month: int | None = None,
        analysis_status: AnalysisAvailability | None = None,
        order: TrackOrder = TrackOrder.IMPORTED_NEWEST_FIRST,
    ) -> TrackPage:
        """Return one page of stored tracks, without geometry.

        Args:
            limit: How many rows to return, bounded by the repository.
            offset: How many rows to skip.
            kind: Narrow to one *effective* kind, honouring a user correction.
            activity: Narrow to one activity.
            year: Narrow to one year, read in the configured zone.
            month: Narrow further to one month of that year. Requires a year --
                February of no particular year is not a period.
            analysis_status: Narrow to tracks whose stored metrics are in one
                availability state.
            order: How to sort the selection.

        Raises:
            ValueError: If a month is given without a year, or either is outside
                the calendar.
        """
        since, until = self._window(year, month)
        return self._repository.list_tracks(
            TrackQuery(
                limit=limit,
                offset=offset,
                effective_kind=kind,
                activity=activity,
                started_at_or_after=since,
                started_before=until,
                installed_analysis=self._analysis.profile,
                analysis_status=analysis_status,
                # Asking for a period is asking when something happened. A
                # track whose clock nothing measured cannot answer that, and
                # listing it under a month the monthly total left out would
                # make clicking a bar reach tracks the bar never counted.
                calendar_anchored=year is not None,
                order=order,
            )
        )

    def _window(
        self, year: int | None, month: int | None
    ) -> tuple[datetime | None, datetime | None]:
        """Return the half-open UTC window one local period covers.

        Drawn by the one calendar authority, so a listing filtered by a month
        covers exactly the instants the monthly total counted.
        """
        if month is not None and year is None:
            raise ValueError("a month needs the year it belongs to")
        if year is None:
            return None, None
        return period_window(year, month, self._zone)

    def get_track(self, track_id: int) -> TrackSummary | None:
        """Return one stored track without geometry, or ``None`` if it is unknown."""
        return self._repository.get_track(track_id, self._analysis.profile)

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
        return self._repository.get_track(track_id, self._analysis.profile)

    def reset_classification(self, track_id: int) -> TrackSummary | None:
        """Withdraw a user correction, handing authority back to the classifier."""
        if not self._repository.clear_override(track_id):
            return None
        return self._repository.get_track(track_id, self._analysis.profile)

    def set_metadata(
        self,
        track_id: int,
        *,
        title: str | Unchanged | None = UNCHANGED,
        note: str | Unchanged | None = UNCHANGED,
    ) -> TrackSummary | None:
        """Record what the user says about a track, and return the updated track.

        A partial update: a field nobody mentioned keeps its stored value, and a
        field explicitly set to ``None`` -- or to whitespace -- is cleared. That
        is what makes "reset the title" a real operation rather than a rename to
        something empty, and clearing the title hands the source title back the
        display.

        Nothing here reaches the raw import, the normalized track or the
        detected classification. The correction is stored beside them, so a
        later reprocess replaces every one of those and leaves this standing.

        Args:
            track_id: Identity of the track.
            title: A new title, ``None`` to fall back to the source title, or
                left out to keep the stored one.
            note: A new note, ``None`` to remove it, or left out to keep it.

        Returns:
            The updated track, or ``None`` if there is no such current track.

        Raises:
            ValueError: If a value is too long or is not plain text. Deciding
                what to do with that is the caller's business; silently
                truncating somebody's sentence is not this layer's decision.
        """
        stored = self._repository.get_track(track_id, self._analysis.profile)
        if stored is None:
            return None
        current = stored.user_metadata
        updated = UserTrackMetadata(
            title=current.title if isinstance(title, Unchanged) else normalize_title(title),
            note=current.note if isinstance(note, Unchanged) else normalize_note(note),
        )
        if not self._repository.set_user_metadata(track_id, updated, self._clock.now()):
            return None
        return self._repository.get_track(track_id, self._analysis.profile)
