"""What a live archive says about itself, for a manifest to state.

Counting rows is the database's own business, which is why this lives here rather
than in the archive package. The archive container knows how many *files* it
holds; only the database knows how many tracks that amounts to, and a dry-run
that reports "412 tracks and 37 corrections" before anybody commits to a restore
needs the second number.

The counts are of the **current** generation, exactly as every other read surface
is. History is preserved in the archive because the whole database is, but what
gets reported is what a reader would see.
"""

import sqlite3

from gpx_view.application.archive import ArchiveCounts
from gpx_view.infrastructure.database.store import SqliteTrackStore

EMPTY_COUNTS = ArchiveCounts(raw_imports=0, tracks=0, classification_overrides=0, user_metadata=0)
"""What an unmigrated database holds.

Asking a fresh data directory for a backup is a reasonable thing to do -- it is
how somebody checks the command works before they need it -- and an empty archive
is a valid archive. Restoring one is how somebody deliberately starts again.
"""


class SqliteArchiveSource:
    """Describes a live deployment in the terms an archive manifest needs."""

    def __init__(self, store: SqliteTrackStore) -> None:
        """Point the source at the archive's database."""
        self._store = store

    def schema_version(self) -> int:
        """Return the schema version this deployment's database currently holds."""
        return self._store.schema_version()

    def counts(self) -> ArchiveCounts:
        """Return what the archive holds, over four small queries."""
        with self._store.connection() as connection:
            if not _is_migrated(connection):
                return EMPTY_COUNTS
            return ArchiveCounts(
                raw_imports=_count(connection, "raw_imports"),
                # The current generation only: a track row whose processing run
                # is not its source's active one is history, and history is a
                # different question from "what does this archive hold".
                tracks=_current_track_count(connection),
                classification_overrides=_count(connection, "track_classification_overrides"),
                user_metadata=_count(connection, "track_user_metadata"),
            )


def _is_migrated(connection: sqlite3.Connection) -> bool:
    """Report whether the database holds the archive's tables at all."""
    row = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'raw_imports'"
    ).fetchone()
    return bool(row[0])


def _count(connection: sqlite3.Connection, table: str) -> int:
    """Return how many rows a table holds.

    The table name is a literal from the call site above. It never reaches here
    from outside this module, which is what makes the interpolation safe: there
    is no caller that could supply one.
    """
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    return int(row[0])


def _current_track_count(connection: sqlite3.Connection) -> int:
    """Return how many tracks belong to the current normalized generations."""
    row = connection.execute(
        "SELECT count(*) FROM tracks t"
        " JOIN raw_imports r ON r.sha256 = t.raw_import_sha256"
        " WHERE t.processing_run_id = r.active_processing_run_id"
    ).fetchone()
    return int(row[0])
