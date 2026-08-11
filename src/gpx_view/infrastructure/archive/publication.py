"""Making a restore survivable, and finishing one that was interrupted.

Publishing a restore moves the previous database and raw storage aside and moves
the archive's own into their place. Those are several renames, and several
renames are not one atomic act: between them the deployment holds a database
from the archive beside a raw storage from nowhere, which is a state nothing can
read correctly.

```
write marker  ->  displace  ->  publish database  ->  publish raw  ->  clean  ->  clear marker
```

The marker is the whole mechanism. It is written and flushed *before* anything
moves, and cleared only after everything has, so its presence is exactly the
statement "a publication started and did not finish".

> **A restore either completed, or it did not happen.**

There is no third outcome, and in particular no "it got far enough, so we
finished it for you". Somebody whose restore reported a failure must be able to
believe that their archive is the one they started with -- a command that
reports an error and replaced the data anyway is worse than one that simply
fails. So an unfinished publication is undone, and the staging directory says
whether there is anything to undo:

```
the staged database is still there    nothing was published  -> put the old data back
it is gone, the staged storage is not the database was       -> put the old data back
both are gone                         it all landed          -> only debris is left
```

Rolling back is always available while the displaced directory is intact, which
it is until the moment both moves have succeeded. That is what makes the
two-outcome promise keepable rather than aspirational.

Two callers, deliberately the same code. An exception during publication is
handled in-process by `abandon`; a killed container is handled at the next start
by the composition root. If those were two implementations, the day they
disagreed would be the day somebody was already restoring from a backup.

**Nothing here deletes data it cannot replace.** The displaced directory holds
the only copy of the previous archive, and it is removed at exactly one moment:
after the material that supersedes it is in place.
"""

import json
import logging
import os
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from gpx_view.infrastructure.private_data import create_private_file

logger = logging.getLogger(__name__)

RESTORE_MARKER_NAME = ".restore-in-progress.json"
"""Where an unfinished publication records itself, inside the data directory.

Beside the data it describes rather than in a temporary directory: the two have
to be lost together or not at all, and `/tmp` is emptied by exactly the reboot
this exists to survive.
"""

_STAGED_DATABASE_MEMBER = "database/gpx-view.sqlite3"
_STAGED_RAW_MEMBER = "raw"


class RestoreRecovery(StrEnum):
    """What an interrupted publication turned out to need.

    Attributes:
        ROLLED_BACK: The publication had not finished, so the previous archive
            was put back and the staged material discarded. A restore that did
            not complete did not happen.
        CLEANED: Everything had landed; only the temporary directories were left.
        UNREADABLE: A marker is present and cannot be interpreted. Nothing was
            moved -- see :func:`recover_interrupted_restore`.
    """

    ROLLED_BACK = "rolled_back"
    CLEANED = "cleaned"
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """What one publication is moving, and where.

    Every name is **relative to the data directory**, and that is not tidiness.
    A container's data directory is a mount point whose absolute path is a
    property of how the container was started; a marker holding absolute paths
    would stop resolving the moment somebody moved a volume, which is one of the
    reasons people restore in the first place.

    Attributes:
        staging: The directory the verified archive was extracted into.
        displaced: The directory the previous material was moved to.
        database: The database file's name inside the data directory.
        raw: The managed raw storage directory's name.
    """

    staging: str
    displaced: str
    database: str
    raw: str


def begin_publication(
    data_dir: Path, *, staging: Path, displaced: Path, database: Path, raw_root: Path
) -> None:
    """Record what is about to be moved, durably, before anything moves.

    Flushed to disk rather than merely written. A marker still sitting in the
    page cache when the power goes is a marker that describes a publication
    nobody can find afterwards, which is the one failure it exists to prevent.

    Raises:
        OSError: If the marker cannot be written. The publication must not begin
            -- moving data whose recovery cannot be recorded is worse than not
            restoring at all.
    """
    plan = RestorePlan(
        staging=staging.name,
        displaced=displaced.name,
        database=database.name,
        raw=raw_root.name,
    )
    marker = data_dir / RESTORE_MARKER_NAME
    marker.unlink(missing_ok=True)
    create_private_file(marker)
    document = json.dumps(
        {
            "staging": plan.staging,
            "displaced": plan.displaced,
            "database": plan.database,
            "raw": plan.raw,
        },
        indent=2,
        sort_keys=True,
    )
    with marker.open("w", encoding="utf-8") as handle:
        handle.write(document + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    sync_directory(data_dir)


def finish_publication(data_dir: Path) -> None:
    """Clear the marker. The publication is complete and needs no recovery."""
    (data_dir / RESTORE_MARKER_NAME).unlink(missing_ok=True)
    sync_directory(data_dir)


def restore_is_pending(data_dir: Path) -> bool:
    """Report whether a publication started and did not finish.

    Presence, not readability. A marker nobody can parse still means a restore
    was interrupted, and that is the fact a diagnostic must report.
    """
    return (data_dir / RESTORE_MARKER_NAME).is_file()


def pending_restore(data_dir: Path) -> RestorePlan | None:
    """Return the plan of an unfinished publication, or ``None``.

    A read. ``None`` covers both "no publication was interrupted" and "one was,
    and its marker cannot be interpreted" -- the caller that needs to tell those
    apart asks :func:`restore_is_pending` as well.
    """
    marker = data_dir / RESTORE_MARKER_NAME
    if not marker.is_file():
        return None
    try:
        document = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    names = {key: document.get(key) for key in ("staging", "displaced", "database", "raw")}
    if not all(isinstance(value, str) and value for value in names.values()):
        return None
    if any(_is_unsafe(str(value)) for value in names.values()):
        return None
    return RestorePlan(
        staging=str(names["staging"]),
        displaced=str(names["displaced"]),
        database=str(names["database"]),
        raw=str(names["raw"]),
    )


def _is_unsafe(name: str) -> bool:
    """Report whether a recorded name could refer to anything outside its directory.

    The marker is written by this process into a private directory, so this is
    not defence against an attacker so much as against a bug: recovery *moves
    and deletes directories*, and a name that had come to hold `..` would do it
    somewhere unintended. A name is one component or it is refused.
    """
    return name in ("", ".", "..") or "/" in name or "\\" in name or Path(name).is_absolute()


def recover_interrupted_restore(data_dir: Path) -> RestoreRecovery | None:
    """Resolve an unfinished publication, one way or the other.

    Called at every start, so its ordinary answer is ``None``. When there is
    something to do, which way it goes is read off the staging directory rather
    than remembered, because the process that would have remembered is the one
    that died.

    Returns:
        What was done, or ``None`` if no publication was interrupted.
    """
    if not restore_is_pending(data_dir):
        return None
    plan = pending_restore(data_dir)
    if plan is None:
        # Deliberately inert. Recovery moves data, and a marker that cannot be
        # read names no directories -- there is nothing this can do except
        # choose between two copies at random, which is exactly what it must
        # not do. The marker stays, so `doctor` keeps saying so.
        logger.error("restore.marker_unreadable")
        return RestoreRecovery.UNREADABLE

    staging = data_dir / plan.staging
    displaced = data_dir / plan.displaced
    staged_database = staging.joinpath(*_STAGED_DATABASE_MEMBER.split("/"))
    staged_raw = staging / _STAGED_RAW_MEMBER

    if staged_database.exists() or staged_raw.exists():
        # Something is still waiting to be published, so the restore did not
        # finish -- and an unfinished restore is undone rather than completed.
        # The displaced directory is intact for exactly as long as that is true.
        outcome = RestoreRecovery.ROLLED_BACK
        _restore_displaced(displaced, data_dir)
    else:
        outcome = RestoreRecovery.CLEANED

    _discard(staging)
    _discard(displaced)
    sync_directory(data_dir)
    finish_publication(data_dir)
    logger.warning("restore.recovered", extra={"outcome": outcome.value})
    return outcome


def _restore_displaced(displaced: Path, data_dir: Path) -> None:
    """Move everything that was set aside back where it came from.

    Whatever is currently in its place came out of the archive and is about to
    be discarded anyway, so it is removed first -- a rename onto a non-empty
    directory would otherwise fail and leave the previous archive stranded.

    Written so that running it twice is safe: each entry is moved only while it
    is still in the displaced directory, which is what lets an interrupted
    recovery simply be run again.
    """
    if not displaced.is_dir():
        return
    for entry in sorted(displaced.iterdir()):
        _publish(entry, data_dir / entry.name)


def _publish(source: Path, destination: Path) -> None:
    """Move one entry into place, clearing whatever is standing there."""
    if destination.is_dir() and not destination.is_symlink():
        shutil.rmtree(destination, ignore_errors=True)
    else:
        destination.unlink(missing_ok=True)
    source.replace(destination)


def _discard(directory: Path) -> None:
    """Remove a temporary directory, if it is still there."""
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)


def sync_directory(directory: Path) -> None:
    """Force a directory's entries to disk, so a rename survives a power cut.

    The half of durability that is easy to forget. Flushing a file writes its
    *contents*; the rename that gives those contents their name lives in the
    parent directory, and a directory entry sitting in the page cache when the
    power goes is a file whose bytes are safe under a name nothing points at.

    Shared with :mod:`gpx_view.infrastructure.archive.container`, which ends a
    backup with the same write-flush-rename-sync sequence this module ends a
    restore with. One implementation, because a second one would be the one that
    quietly stopped at the rename.

    Raises:
        OSError: If the directory could not be flushed. Reported rather than
            swallowed: a caller that has just renamed something into place has
            to be able to tell "durable" from "written", and a silent failure
            would leave it claiming the first while only the second is true. A
            data directory this cannot even open is a deployment in trouble
            whichever operation happens to notice first.
    """
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
