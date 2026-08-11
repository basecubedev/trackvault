"""Looking at a database file without opening it for business.

Diagnostics need two facts from a database -- which schema it states, and
whether it is sound -- and getting them wrong in the obvious way is worse than
not asking. ``sqlite3.connect`` on a missing path **creates** it, so a
diagnostic written the natural way reports "schema version 0" about a database
it has just brought into existence, and the fresh deployment it was asked to
describe is no longer fresh.

Everything here therefore opens read-only, through a ``file:...?mode=ro`` URI,
and answers ``None`` rather than raising when there is nothing to read. Absence
is a normal answer to "what schema does this database have": most deployments
have none for their first few minutes.

This lives in the database package because it names the driver, and one package
owns that. It is separate from :mod:`~gpx_view.infrastructure.database.snapshot`
because the two want opposite things: a snapshot fails loudly so a backup is
never silently wrong, and an inspection answers quietly so a diagnostic can
report a problem instead of becoming one.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path


def read_schema_version(database: Path) -> int | None:
    """Return the schema version a database states, or ``None``.

    ``None`` covers all three ways there is no answer: no file, a file that is
    not a database, and a database that will not open. A diagnostic reports the
    absence; it does not need to tell those apart, because the advice is the
    same and the *integrity* check is what distinguishes damage from emptiness.
    """
    return _read(database, "PRAGMA user_version", _as_int)


def is_intact(database: Path) -> bool | None:
    """Return what the database's own integrity check said, or ``None``.

    ``None`` means there was nothing to check. ``False`` means there was, and it
    failed -- which is a different statement and calls for a restore.

    ``PRAGMA integrity_check`` walks every page, index and relationship. It is
    slower than opening the file, and slower is right for something that runs
    when somebody already suspects a problem.
    """
    if not database.is_file():
        return None
    result = _read(database, "PRAGMA integrity_check", _is_ok)
    return False if result is None else result


def _as_int(value: object) -> int:
    """Interpret a pragma result as a whole number."""
    return int(str(value))


def _is_ok(value: object) -> bool:
    """Interpret SQLite's integrity verdict."""
    return value == "ok"


def _read[T](database: Path, statement: str, interpret: Callable[[object], T]) -> T | None:
    """Run one read-only pragma, answering ``None`` if it cannot be run at all.

    The file is never created: a missing path is answered before the driver is
    asked, and the connection itself is opened read-only so that no code path
    here can write.
    """
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(statement).fetchone()
    except (sqlite3.DatabaseError, ValueError):
        return None
    else:
        return None if row is None else interpret(row[0])
    finally:
        connection.close()
