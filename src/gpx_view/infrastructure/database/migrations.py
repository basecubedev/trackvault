"""Versioned schema migrations.

The schema version lives in SQLite's own ``PRAGMA user_version``. Every migration
is an explicit, numbered function, and the list below *is* the version: an empty
database runs all of them, an existing one runs the missing tail.

There is deliberately no collection of ``CREATE TABLE IF NOT EXISTS`` statements.
That pattern looks like a migration layer until the first column has to change,
and then there is no way to tell which shape a deployed database is in.
"""

import sqlite3
from collections.abc import Callable

_SCHEMA_1 = """
CREATE TABLE raw_imports (
    sha256            TEXT    PRIMARY KEY,
    size_bytes        INTEGER NOT NULL,
    original_filename TEXT,
    received_at       TEXT    NOT NULL,
    media_type        TEXT,
    input_channel     TEXT    NOT NULL
);

CREATE TABLE processing_runs (
    id                           INTEGER PRIMARY KEY,
    raw_import_sha256            TEXT    NOT NULL REFERENCES raw_imports(sha256),
    importer                     TEXT    NOT NULL,
    importer_version             TEXT    NOT NULL,
    normalization_schema_version INTEGER NOT NULL,
    processed_at                 TEXT    NOT NULL,
    status                       TEXT    NOT NULL,
    error_code                   TEXT
);

CREATE INDEX ix_processing_runs_raw_import ON processing_runs(raw_import_sha256);

CREATE TABLE tracks (
    id                INTEGER PRIMARY KEY,
    raw_import_sha256 TEXT    NOT NULL REFERENCES raw_imports(sha256),
    source_index      INTEGER NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_runs(id),
    title             TEXT,
    activity          TEXT    NOT NULL,
    exchange_format   TEXT    NOT NULL,
    format_version    TEXT,
    creator           TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    point_count       INTEGER NOT NULL,
    segment_count     INTEGER NOT NULL,
    UNIQUE (raw_import_sha256, source_index)
);

CREATE TABLE track_classifications (
    track_id       INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    detected_kind  TEXT    NOT NULL,
    confidence     REAL    NOT NULL,
    method         TEXT    NOT NULL,
    method_version TEXT    NOT NULL
);

CREATE TABLE track_classification_overrides (
    track_id      INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    override_kind TEXT    NOT NULL,
    set_at        TEXT    NOT NULL
);

CREATE TABLE track_evidence (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    code     TEXT    NOT NULL,
    PRIMARY KEY (track_id, position)
);

CREATE TABLE track_source_links (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    url      TEXT    NOT NULL,
    PRIMARY KEY (track_id, position)
);

CREATE TABLE track_extension_namespaces (
    track_id  INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    namespace TEXT    NOT NULL,
    PRIMARY KEY (track_id, position)
);

CREATE TABLE track_segments (
    id       INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    UNIQUE (track_id, position)
);

CREATE TABLE track_points (
    segment_id INTEGER NOT NULL REFERENCES track_segments(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    latitude   REAL    NOT NULL,
    longitude  REAL    NOT NULL,
    elevation  REAL,
    recorded_at TEXT,
    PRIMARY KEY (segment_id, position)
);
"""


def _execute_all(connection: sqlite3.Connection, script: str) -> None:
    """Run the statements of a schema script inside the caller's transaction.

    ``executescript`` would commit the pending transaction first, which would give
    up exactly the atomicity a migration needs.
    """
    for statement in (part.strip() for part in script.split(";")):
        if statement:
            connection.execute(statement)


LEGACY_SOURCE_KEY_PREFIX = "legacy:"
"""Marks a candidate identity that was reconstructed rather than observed.

Schema 1 identified a candidate by its position in the source document, so that
is all a schema-1 row can be given. The prefix says so out loud: these keys were
derived from an index, not produced by an importer reading the source, and a
reprocess replaces them with the real thing.
"""

# `tracks` is rebuilt rather than altered: the uniqueness moves from
# (raw import, source index) to (raw import, source key), and SQLite cannot drop
# a table constraint in place. The rebuild follows SQLite's documented order, and
# `SqliteTrackStore.migrate` disables foreign keys around it -- dropping the old
# table with them on would cascade every classification, segment and position
# away. `PRAGMA foreign_key_check` runs before the transaction commits.
#
# The legacy prefix is spelled out rather than interpolated from
# LEGACY_SOURCE_KEY_PREFIX. A migration is a historical record of what was done to
# a database that already exists; wiring it to a constant would mean renaming the
# constant silently rewrites history. `test_persistence_contract` checks the two
# still agree.
_SCHEMA_2 = """
CREATE TABLE tracks_migrated (
    id                INTEGER PRIMARY KEY,
    raw_import_sha256 TEXT    NOT NULL REFERENCES raw_imports(sha256),
    source_key        TEXT    NOT NULL,
    source_index      INTEGER NOT NULL,
    processing_run_id INTEGER NOT NULL REFERENCES processing_runs(id),
    title             TEXT,
    activity          TEXT    NOT NULL,
    exchange_format   TEXT    NOT NULL,
    format_version    TEXT,
    creator           TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    point_count       INTEGER NOT NULL,
    segment_count     INTEGER NOT NULL,
    UNIQUE (raw_import_sha256, source_key)
);

INSERT INTO tracks_migrated
    (id, raw_import_sha256, source_key, source_index, processing_run_id, title, activity,
     exchange_format, format_version, creator, started_at, ended_at, point_count, segment_count)
SELECT id, raw_import_sha256, 'legacy:' || source_index, source_index,
       processing_run_id, title, activity, exchange_format, format_version, creator,
       started_at, ended_at, point_count, segment_count
  FROM tracks;

DROP TABLE tracks;

ALTER TABLE tracks_migrated RENAME TO tracks;

CREATE INDEX ix_tracks_raw_import ON tracks(raw_import_sha256);

ALTER TABLE raw_imports
    ADD COLUMN active_processing_run_id INTEGER REFERENCES processing_runs(id);

UPDATE raw_imports SET active_processing_run_id = (
    SELECT r.id FROM processing_runs r
     WHERE r.raw_import_sha256 = raw_imports.sha256
       AND r.status = 'succeeded'
     ORDER BY r.id DESC LIMIT 1
);
"""


_SCHEMA_3 = """
ALTER TABLE track_source_links RENAME TO track_external_links;
"""

# The columns are nullable and are deliberately **not** backfilled. An existing
# run was produced by whatever classifier was installed when it ran, and this
# build cannot know which one that was. Writing today's version into yesterday's
# row would make every stored generation look current, which is precisely the
# question `--outdated` exists to answer. A run that cannot prove its processing
# is treated as out of date, and reprocessing gives it a profile it earned.
_SCHEMA_4 = """
ALTER TABLE processing_runs ADD COLUMN classifier TEXT;

ALTER TABLE processing_runs ADD COLUMN classifier_version TEXT;
"""


def _migrate_to_1(connection: sqlite3.Connection) -> None:
    """Create the first productive schema: imports, runs, tracks and geometry."""
    _execute_all(connection, _SCHEMA_1)


def _migrate_to_2(connection: sqlite3.Connection) -> None:
    """Give candidates a stable identity and name the current generation.

    Two authorities that schema 1 did not separate.

    A candidate was identified by ``source_index``, its position in the source
    document. A reprocess that reported one extra candidate in front therefore
    moved every later candidate's identity -- and with it any user correction --
    onto a different track. ``source_key`` replaces it as the identity;
    ``source_index`` stays, as presentation order and nothing more.

    Existing rows can only be keyed by what schema 1 recorded, so they get a
    ``legacy:`` key derived from their index. That is an honest reconstruction,
    not a promise: it is right if the importer still reads the same document the
    same way, and reprocessing replaces it with a key the importer actually
    produced. Adoption of a legacy row happens by position, once, and only
    through an explicit reprocess.

    ``active_processing_run_id`` names which run's candidates are current. It is
    backfilled to the newest successful run, which is what a schema-1 database
    was already showing implicitly.
    """
    _execute_all(connection, _SCHEMA_2)


def _migrate_to_3(connection: sqlite3.Connection) -> None:
    """Name the stored links for what they are rather than for what they suggest.

    A GPX ``<link>`` is a related web resource. The table called it a *source*
    link, which claims the document declared where its geometry came from -- more
    than an exchange format's link element says, and the same overstatement that
    made classifier version 1 reach `PLANNED` on a link alone.

    The rows are unchanged: the same URLs, in the same order, for the same
    tracks. Only the name stops making a claim about them.
    """
    _execute_all(connection, _SCHEMA_3)


def _migrate_to_4(connection: sqlite3.Connection) -> None:
    """Let a processing run state the classification rules it applied.

    Importer and normalization schema versions were already on the run; the
    classifier's version was on each track's classification result. A run that
    produced no candidate therefore recorded no classifier version at all, and
    no stored generation could say which *combination* produced it -- which is
    what "is this still current?" asks.

    Existing rows keep a null classifier on purpose. What ran back then is not
    knowable now, and a run that cannot prove its processing counts as out of
    date rather than as current by assumption.
    """
    _execute_all(connection, _SCHEMA_4)


MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migrate_to_1,
    _migrate_to_2,
    _migrate_to_3,
    _migrate_to_4,
)

SCHEMA_VERSION = len(MIGRATIONS)
"""The schema version this build expects. Equals the number of migrations."""


def apply_migrations(connection: sqlite3.Connection, current_version: int) -> int:
    """Bring a database from its current version up to the expected one.

    Args:
        connection: An open connection, inside an explicit transaction.
        current_version: The version read from ``PRAGMA user_version``.

    Returns:
        The version the database is at afterwards.

    Raises:
        RuntimeError: If the database was written by a newer build. Running an
            older schema against newer data would corrupt it silently.
    """
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"the database uses a newer schema (version {current_version}) than this "
            f"build supports (version {SCHEMA_VERSION})"
        )
    for index in range(current_version, SCHEMA_VERSION):
        MIGRATIONS[index](connection)
        # PRAGMA does not accept a bound parameter; the value is a loop counter.
        connection.execute(f"PRAGMA user_version = {index + 1:d}")
    return SCHEMA_VERSION
