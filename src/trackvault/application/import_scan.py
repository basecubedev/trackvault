"""What one pass over the import directory found, and what became of it.

A scan is a reader in front of :class:`~trackvault.application.import_tracks.ImportTracks`.
Every file it offers gets the use case's own :class:`ImportOutcome`, so this
module adds no second verdict about a file. What it adds is what the use case
never sees: files that were not offered, and why.

```
offered      handed to ImportTracks -- imported, duplicate, repaired or failed
unchanged    already offered in exactly this state by this process; not read
failing      unchanged since this process offered it, and not imported then
waiting      still being written; a later scan offers it
unreadable   named like a track but could not be read as a regular file
crashed      offered, but the import stopped on an error it did not anticipate
```

The running server scans on an interval, and :class:`AutomaticImportStatus` is
what it can say about that to somebody who is not reading its log.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from trackvault.application.errors import ImportErrorCode
from trackvault.application.import_tracks import ImportOutcome, ImportStatus


@dataclass(frozen=True, slots=True)
class ImportScan:
    """One pass over the import directory.

    Attributes:
        started_at: When the pass began. Files changed later than this minus
            the settle time were still being written.
        finished_at: When the pass ended.
        offered: Every candidate handed to the import use case, with its
            outcome, in scan order.
        unchanged: How many candidates this process had already offered in
            exactly their current state, and skipped without reading.
        failing: Candidates unchanged since this process offered them, which
            did not become tracks then, with the reason they did not. Still
            reported, so a broken file stays visible for as long as it is in
            the folder, however much else is imported beside it.
        waiting: Candidates that were still changing. Not a verdict: the next
            pass offers them once they are quiet.
        unreadable: Candidates that could not be read as a regular file.
        crashed: Candidates whose import stopped on an error nobody
            anticipated. The traceback is in the log, and the next pass offers
            them again.
        unavailable: Whether the folder itself could not be opened -- not
            mounted, not there yet, or not readable. Nothing in it was seen,
            which is a different statement from "nothing new".
    """

    started_at: datetime
    finished_at: datetime
    offered: tuple[tuple[str, ImportOutcome], ...] = ()
    unchanged: int = 0
    failing: tuple[tuple[str, ImportErrorCode], ...] = ()
    waiting: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    crashed: tuple[str, ...] = ()
    unavailable: bool = False

    @property
    def discovered(self) -> int:
        """Return how many candidates the directory held."""
        return (
            len(self.offered)
            + self.unchanged
            + len(self.failing)
            + len(self.waiting)
            + len(self.unreadable)
            + len(self.crashed)
        )

    def count(self, status: ImportStatus) -> int:
        """Return how many offered candidates ended with one outcome."""
        return sum(1 for _, outcome in self.offered if outcome.status is status)

    @property
    def skipped(self) -> int:
        """Return how many candidates the archive already holds as tracks.

        Either the archive answered ``duplicate``, or the file is unchanged
        since an earlier pass of this process found it imported. Which of the
        two is a question of cost, not of outcome.
        """
        held = sum(
            1
            for _, outcome in self.offered
            if outcome.status is ImportStatus.DUPLICATE and outcome.error_code is None
        )
        return self.unchanged + held

    @property
    def failures(self) -> tuple[tuple[str, ImportErrorCode | None], ...]:
        """Return every candidate in the folder that is not a track, with the reason.

        That includes a file offered before whose bytes the archive holds but
        could never read -- a duplicate that carries a reason -- and one this
        process remembers failing. ``None`` means the import reached no verdict:
        the file could not be read, or its import stopped on an error. The log
        says which.
        """
        rejected = tuple(
            (name, outcome.error_code)
            for name, outcome in self.offered
            if outcome.error_code is not None
        )
        unfinished = (*self.unreadable, *self.crashed)
        return rejected + self.failing + tuple((name, None) for name in unfinished)

    @property
    def had_activity(self) -> bool:
        """Return whether the pass imported, repaired or newly failed anything.

        A failure already known -- remembered, or recognised in bytes the
        archive holds -- is reported by every pass, and is news in none of them.
        """
        return bool(self.unreadable or self.crashed) or any(
            outcome.status is not ImportStatus.DUPLICATE for _, outcome in self.offered
        )


@dataclass(frozen=True, slots=True)
class AutomaticImportStatus:
    """What the automatic import is doing, right now.

    Attributes:
        enabled: Whether the server reads the import directory on its own.
        directory: The import directory as the server sees it, or ``None``
            when none is configured. Inside the container that is ``/import``.
        interval: Time between the end of one scan and the start of the next.
        settle_time: How long a file must have been left alone to be read.
        scanning: Whether a scan is running at this moment.
        last_scan: The most recent scan, which usually found nothing new.
        last_activity: The most recent scan that imported, repaired or failed
            anything. Kept beside ``last_scan`` so that a quiet quarter hour
            never hides the file that could not be imported before it.
        next_scan_at: When the next scan is due, once one is scheduled.
    """

    enabled: bool
    directory: str | None
    interval: timedelta
    settle_time: timedelta
    scanning: bool = False
    last_scan: ImportScan | None = None
    last_activity: ImportScan | None = None
    next_scan_at: datetime | None = None


class AutomaticImportMonitor(Protocol):
    """Reports what the automatic import is doing, without being able to run it."""

    def status(self) -> AutomaticImportStatus:
        """Return the automatic import's current state."""
        ...


__all__ = ["AutomaticImportMonitor", "AutomaticImportStatus", "ImportScan"]
