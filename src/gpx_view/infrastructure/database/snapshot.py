"""Taking a consistent copy of the archive's database, and checking one.

**Copying the file is not taking a backup.** Write-ahead logging is on, so the
``.sqlite3`` file on its own is an incomplete database: the newest transactions
live in ``-wal`` until a checkpoint moves them, and a copy taken mid-write can
hold a page from before a transaction beside a page from after it. That copy
opens fine and is wrong, which is the worst of the available failures.

SQLite's own online backup API is the answer to exactly this. It copies pages
under the source's own locking, retrying the pages that change underneath it, and
what it produces is a database as of one instant -- with the write-ahead log
already folded in, so the result is a single self-contained file.

This module lives in the database package because it names the driver, and the
architecture contract keeps that in one place. It is also the right home on its
own merits: what "a consistent copy of this database" means is the database's own
business, not the archive container's.
"""

import hashlib
import sqlite3
from pathlib import Path

from gpx_view.infrastructure.private_data import create_private_file

_HASH_CHUNK_BYTES = 1024 * 1024


class DatabaseSnapshotError(Exception):
    """A consistent copy could not be produced, or a copy is not sound."""


def capture_snapshot(source: Path, destination: Path) -> tuple[int, str]:
    """Copy a live database consistently, and describe what was written.

    The destination is created privately before the driver opens it, for the same
    reason the live database is: it holds the same movement data, and a file that
    was briefly world-readable was world-readable.

    Args:
        source: The live database file.
        destination: Where to write the copy. Must not already exist.

    Returns:
        The size of the copy and its SHA-256 digest, so a manifest can state
        both without reading the file a second time for each.

    Raises:
        DatabaseSnapshotError: If the copy could not be produced.
    """
    create_private_file(destination)
    try:
        with sqlite3.connect(source) as origin, sqlite3.connect(destination) as copy:
            origin.backup(copy)
    except sqlite3.Error as error:
        raise DatabaseSnapshotError("the database could not be copied consistently") from error
    finally:
        # `sqlite3.connect` used as a context manager commits or rolls back; it
        # does not close. Leaving either handle open would keep a `-wal` beside
        # the copy, and the copy is meant to be one self-contained file.
        _close_quietly(source, destination)
    return destination.stat().st_size, digest_of(destination)


def _close_quietly(*paths: Path) -> None:
    """Drop any connection state the copy left behind.

    ``sqlite3`` closes a connection when it is garbage collected, and both
    handles above go out of scope at the end of the ``with``. This exists to make
    that explicit rather than incidental: the guarantee the archive needs is that
    no journal file survives beside the snapshot.
    """
    for path in paths:
        for suffix in ("-wal", "-shm"):
            journal = path.with_name(path.name + suffix)
            if journal.exists() and journal.stat().st_size == 0:
                journal.unlink()


def digest_of(path: Path) -> str:
    """Return the SHA-256 of a file, read in bounded chunks.

    Chunked because a database is arbitrarily large and a backup must not need to
    hold one in memory to describe it.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def verify_database(path: Path) -> None:
    """Prove a restored database opens and is internally sound.

    A checksum says the bytes arrived intact. It says nothing about whether they
    were a valid database when they left, and a restore that published a
    structurally broken file would have verified everything except the thing that
    matters.

    ``PRAGMA integrity_check`` walks the whole file: pages, indexes and the
    relationships between them. It is slower than opening the file and slower is
    correct here, because this runs once per restore and the alternative is
    finding out later.

    Raises:
        DatabaseSnapshotError: If the file is not a database, or fails its own
            integrity check.
    """
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error as error:
        raise DatabaseSnapshotError("the restored file is not a database") from error
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise DatabaseSnapshotError("the restored database failed its integrity check")
        # A database whose schema version this build cannot read is a separate,
        # earlier decision made by the manifest. What is checked here is only
        # that the file is a sound database at all.
        connection.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.DatabaseError as error:
        raise DatabaseSnapshotError("the restored file is not a readable database") from error
    finally:
        connection.close()


def schema_version_of(path: Path) -> int:
    """Return the schema version a database file states.

    Raises:
        DatabaseSnapshotError: If the file cannot be read as a database.
    """
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error as error:
        raise DatabaseSnapshotError("the file is not a database") from error
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.DatabaseError as error:
        raise DatabaseSnapshotError("the file is not a readable database") from error
    finally:
        connection.close()
