"""What an archived database says about itself.

Read from the **captured snapshot** rather than from the live deployment, and
that is the whole point of this module's shape. A manifest describes the
database inside its own archive; deriving it from a connection to something else
is a second authority, and the two disagree the moment anybody archives a data
directory that is not the running one -- which is exactly what a restore test,
a migration test and an operator moving an old backup all do.

Counting rows is the database's own business, which is why this lives here
rather than in the archive package. The archive container knows how many *files*
it holds; only the database knows how many tracks that amounts to, and a dry-run
that reports "412 tracks and 37 corrections" before anybody commits to a restore
needs the second number.

The counts are of the **current** generation, exactly as every other read surface
is. History is preserved in the archive because the whole database is, but what
gets reported is what a reader would see.
"""

import sqlite3
from pathlib import Path

from gpx_view.application.archive import ArchiveCounts

EMPTY_COUNTS = ArchiveCounts(
    raw_imports=0, tracks=0, classification_overrides=0, user_metadata=0, analyzed_tracks=0
)
"""What an unmigrated or empty database holds.

Asking a fresh data directory for a backup is a reasonable thing to do -- it is
how somebody checks the command works before they need it -- and an empty archive
is a valid archive. Restoring one is how somebody deliberately starts again.
"""


def read_counts(database: Path) -> ArchiveCounts:
    """Return what a database holds, without modifying it.

    Opened read-only so that describing a database can never be the thing that
    creates one. A file that is not there, or not a database, holds nothing --
    which is an answer rather than a failure, because the archive that carries it
    is still a valid empty archive.
    """
    if not database.is_file() or database.stat().st_size == 0:
        return EMPTY_COUNTS
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error:
        return EMPTY_COUNTS
    try:
        if not _is_migrated(connection):
            return EMPTY_COUNTS
        return ArchiveCounts(
            raw_imports=_count(connection, "raw_imports"),
            # The current generation only: a track row whose processing run is
            # not its source's active one is history, and history is a different
            # question from "what does this archive hold".
            tracks=_current_track_count(connection),
            classification_overrides=_count(connection, "track_classification_overrides"),
            user_metadata=_count(connection, "track_user_metadata"),
            analyzed_tracks=_analyzed_track_count(connection),
        )
    except sqlite3.DatabaseError:
        return EMPTY_COUNTS
    finally:
        connection.close()


def read_raw_import_digests(database: Path) -> tuple[str, ...]:
    """Return the content hash of every raw import a database knows about.

    **This is the archive's source list**, and it comes from the captured
    snapshot for the same reason the counts do. The alternative -- listing the
    storage directory -- describes a deployment that is still running and cannot
    tell an original that was never there from one that was imported a moment
    ago. Reading both from one snapshot is what makes "the manifest counts 412
    and the container holds 412" a fact rather than a coincidence.

    Sorted, so two archives of the same database describe their members in the
    same order and a difference between them is a difference in the data.

    An unreadable or unmigrated database names nothing. That is the same answer
    :func:`read_counts` gives it, and the two must agree: a database that holds
    no countable rows cannot be the one that references originals.
    """
    if not database.is_file() or database.stat().st_size == 0:
        return ()
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error:
        return ()
    try:
        if not _is_migrated(connection):
            return ()
        rows = connection.execute("SELECT sha256 FROM raw_imports ORDER BY sha256").fetchall()
    except sqlite3.DatabaseError:
        return ()
    else:
        return tuple(str(row[0]) for row in rows)
    finally:
        connection.close()


def _is_migrated(connection: sqlite3.Connection) -> bool:
    """Report whether the database holds the archive's tables at all."""
    row = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'raw_imports'"
    ).fetchone()
    return bool(row[0])


def _count(connection: sqlite3.Connection, table: str) -> int:
    """Return how many rows a table holds, or zero if the table predates it.

    A schema old enough to lack one of these tables is a schema this archive can
    still carry -- the manifest describes it, the restore migrates it. Counting a
    table that does not exist yet is therefore zero rather than an error.

    The table name is a literal from the call site above and never reaches here
    from outside this module, which is what makes the interpolation safe.
    """
    try:
        row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    except sqlite3.DatabaseError:
        return 0
    return int(row[0])


def _analyzed_track_count(connection: sqlite3.Connection) -> int:
    """Return how many current-generation tracks arrived with metrics attached.

    A fact about the archive, never a verdict on it. Whether those metrics are
    still *current* is decided by the build that reads them, against the
    algorithms it has installed -- so this counts what is there and leaves the
    judgement where it belongs.

    A schema too old to have analysis runs answers zero, which is exactly what
    was true of it.
    """
    try:
        row = connection.execute(
            "SELECT count(*) FROM tracks t"
            " JOIN raw_imports r ON r.sha256 = t.raw_import_sha256"
            " WHERE t.processing_run_id = r.active_processing_run_id"
            " AND t.current_analysis_run_id IS NOT NULL"
        ).fetchone()
    except sqlite3.DatabaseError:
        return 0
    return int(row[0])


def _current_track_count(connection: sqlite3.Connection) -> int:
    """Return how many tracks belong to the current normalized generations.

    Schema 1 had no ``active_processing_run_id``, so "the current generation" was
    not yet a stored fact. An archive of one still has to state a track count, so
    the fallback counts the rows -- which is what a schema-1 database was already
    showing implicitly.
    """
    try:
        row = connection.execute(
            "SELECT count(*) FROM tracks t"
            " JOIN raw_imports r ON r.sha256 = t.raw_import_sha256"
            " WHERE t.processing_run_id = r.active_processing_run_id"
        ).fetchone()
    except sqlite3.DatabaseError:
        return _count(connection, "tracks")
    return int(row[0])
