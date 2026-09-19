"""What one pass over the import directory found, and what became of it.

A scan is a reader in front of :class:`~trackvault.application.import_tracks.ImportTracks`.
Every file it offers gets the use case's own :class:`ImportOutcome`, so this
module adds no second verdict about a file. What it adds is what the use case
never sees: files that were not offered, and why.

```
offered      handed to ImportTracks -- imported, duplicate, repaired or failed
unchanged    already offered in exactly this state by this process; not read
waiting      still being written; a later scan offers it
unreadable   named like a track but could not be read as a regular file
crashed      offered, but the import stopped on an error it did not anticipate
```
"""

from dataclasses import dataclass
from datetime import datetime

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
            + len(self.waiting)
            + len(self.unreadable)
            + len(self.crashed)
        )

    def count(self, status: ImportStatus) -> int:
        """Return how many offered candidates ended with one outcome."""
        return sum(1 for _, outcome in self.offered if outcome.status is status)

    @property
    def skipped(self) -> int:
        """Return how many candidates had nothing new to do.

        Either the archive answered ``duplicate``, or the file is unchanged
        since an earlier pass of this process reported on it -- which includes
        a file that pass reported as failed. Which of the two is a question of
        cost, not of outcome.
        """
        return self.unchanged + self.count(ImportStatus.DUPLICATE)

    @property
    def failures(self) -> tuple[tuple[str, ImportErrorCode | None], ...]:
        """Return every candidate that did not make it, with the reason.

        ``None`` means the import reached no verdict: the file could not be
        read, or its import stopped on an error. The log says which.
        """
        rejected = tuple(
            (name, outcome.error_code)
            for name, outcome in self.offered
            if outcome.status is ImportStatus.FAILED
        )
        unfinished = (*self.unreadable, *self.crashed)
        return rejected + tuple((name, None) for name in unfinished)


__all__ = ["ImportScan"]
