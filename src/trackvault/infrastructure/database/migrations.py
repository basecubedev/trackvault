"""Versioned schema migrations.

The schema version lives in SQLite's own ``PRAGMA user_version``. Every migration
is an explicit, numbered function, and the list below *is* the version: an empty
database runs all of them, an existing one runs the missing tail.

There is deliberately no collection of ``CREATE TABLE IF NOT EXISTS`` statements.
That pattern looks like a migration layer until the first column has to change,
and then there is no way to tell which shape a deployed database is in.
"""

import hashlib
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


# Metrics are typed columns in a narrow table rather than a JSON blob per run.
# A blob would make "sum the distance of every recorded track in March" a full
# scan and a parse, and it would let a metric's unit or meaning drift silently
# because nothing declares them.
#
# `track_metrics` is `WITHOUT ROWID`: the primary key *is* the row, and every
# read of it is by that key.
#
# `current_analysis_run_id` names the current derived generation the way
# `raw_imports.active_processing_run_id` names the current normalized one. It is
# nullable and starts null everywhere: no existing track has been analysed, and
# there is nothing to back-fill it from.
_SCHEMA_5 = """
CREATE TABLE analysis_runs (
    id                         INTEGER PRIMARY KEY,
    track_id                   INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    processing_run_id          INTEGER NOT NULL REFERENCES processing_runs(id) ON DELETE CASCADE,
    distance_algorithm         TEXT    NOT NULL,
    distance_algorithm_version INTEGER NOT NULL,
    movement_algorithm         TEXT    NOT NULL,
    movement_algorithm_version INTEGER NOT NULL,
    elevation_algorithm        TEXT    NOT NULL,
    elevation_algorithm_version INTEGER NOT NULL,
    metric_schema_version      INTEGER NOT NULL,
    analyzed_at                TEXT    NOT NULL,
    status                     TEXT    NOT NULL,
    error_code                 TEXT
);

CREATE INDEX ix_analysis_runs_track ON analysis_runs(track_id);

CREATE TABLE track_metrics (
    analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    metric          TEXT    NOT NULL,
    value           REAL    NOT NULL,
    unit            TEXT    NOT NULL,
    provenance      TEXT    NOT NULL,
    PRIMARY KEY (analysis_run_id, metric)
) WITHOUT ROWID;

CREATE TABLE analysis_quality_flags (
    analysis_run_id INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    flag            TEXT    NOT NULL,
    PRIMARY KEY (analysis_run_id, position)
) WITHOUT ROWID;

ALTER TABLE tracks ADD COLUMN current_analysis_run_id INTEGER REFERENCES analysis_runs(id);

CREATE INDEX ix_tracks_started_at ON tracks(started_at);
"""


# User-owned metadata is its own table for the same reason the classification
# override is: it must survive a reprocess that replaces every normalized column
# of the track row. `tracks.title` keeps stating what the document said, and this
# states what the owner said about it.
#
# The row is keyed on the track identity, which is stable across reprocessing --
# a candidate keeps its row and its corrections even while a later run stops
# producing it, and gets them back when it reappears.
#
# The CHECK is the invariant that "the user has said nothing" is the absence of a
# row rather than a row of nulls. Two representations of nothing would be two
# answers to "has this track been corrected?".
_SCHEMA_6 = """
CREATE TABLE track_user_metadata (
    track_id   INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    title      TEXT,
    note       TEXT,
    updated_at TEXT NOT NULL,
    CHECK (title IS NOT NULL OR note IS NOT NULL)
);
"""


_SCHEMA_7 = """
CREATE TABLE map_packages (
    region_id              TEXT    PRIMARY KEY,
    region_name            TEXT    NOT NULL,
    provider               TEXT    NOT NULL,
    format                 TEXT    NOT NULL,
    tile_schema            TEXT    NOT NULL,
    tile_schema_version    TEXT    NOT NULL,
    content_sha256         TEXT    NOT NULL,
    size_bytes             INTEGER NOT NULL,
    min_longitude          REAL    NOT NULL,
    min_latitude           REAL    NOT NULL,
    max_longitude          REAL    NOT NULL,
    max_latitude           REAL    NOT NULL,
    min_zoom               INTEGER NOT NULL,
    max_zoom               INTEGER NOT NULL,
    attribution_data_owner TEXT    NOT NULL,
    attribution_provider   TEXT    NOT NULL,
    license_identifier     TEXT    NOT NULL,
    license_name           TEXT    NOT NULL,
    attribution_text       TEXT    NOT NULL,
    dataset_version        TEXT,
    dataset_timestamp      TEXT,
    downloaded_at          TEXT    NOT NULL,
    source_url             TEXT    NOT NULL
);

CREATE UNIQUE INDEX ix_map_packages_delivery ON map_packages(content_sha256);

CREATE TABLE map_package_attribution_links (
    region_id TEXT    NOT NULL REFERENCES map_packages(region_id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    label     TEXT    NOT NULL,
    url       TEXT    NOT NULL,
    PRIMARY KEY (region_id, position)
);

CREATE TABLE map_install_jobs (
    job_id           TEXT    PRIMARY KEY,
    region_id        TEXT    NOT NULL,
    region_name      TEXT    NOT NULL,
    state            TEXT    NOT NULL,
    bytes_downloaded INTEGER NOT NULL,
    bytes_total      INTEGER,
    error_code       TEXT,
    is_update        INTEGER NOT NULL,
    started_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

CREATE INDEX ix_map_install_jobs_region ON map_install_jobs(region_id, started_at);
CREATE INDEX ix_map_install_jobs_state ON map_install_jobs(state);
"""


_SCHEMA_11 = """
CREATE TABLE page_layouts (
    page           TEXT    PRIMARY KEY,
    format_version INTEGER NOT NULL,
    document       TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
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


def _migrate_to_5(connection: sqlite3.Connection) -> None:
    """Give tracks derived metrics, and let them say what produced them.

    Analysis output is rebuildable state, not source authority, which is exactly
    why it has to be versioned: an elevation filter or a movement rule will
    change, and the old numbers must not pass for the new ones. Every run
    therefore records its whole analysis profile and the processing run whose
    geometry it read.

    Nothing is back-filled. No track has been analysed before this migration,
    and a metric invented here would be a number nobody derived.

    ``ix_tracks_started_at`` serves the statistics queries: a year is a bounded
    window over that column, so an aggregate never scans the archive and never
    touches a single position.
    """
    _execute_all(connection, _SCHEMA_5)


def _migrate_to_6(connection: sqlite3.Connection) -> None:
    """Let the archive's owner correct a track's title and keep a note.

    Source titles are what an exporting application happened to write, and a
    personal archive stops being pleasant to keep the moment three of them read
    `Track`. The correction is user-owned data with its own authority and it
    lives beside the source rather than over it: the raw import stays
    byte-identical, the normalized projection keeps saying what the file said,
    and a reprocess replaces neither this row nor the classification override.

    Nothing is back-filled -- nobody has corrected anything yet, and copying a
    source title in here would manufacture an override that says nothing and
    then hide every later improvement to the importer behind it.
    """
    _execute_all(connection, _SCHEMA_6)


def _migrate_to_7(connection: sqlite3.Connection) -> None:
    """Record which regional map packages this deployment has installed.

    Two tables and no foreign key to anything else, because a map has nothing to
    do with a track. Removing every track leaves the maps; removing every map
    leaves the tracks. The only thing the two share is a bounding box, and it is
    compared at query time rather than stored as a relationship.

    A package row is deliberately *not* the authority for "installed". The
    managed file beside it is the other half, and a row whose file is gone
    reports ``INVALID`` rather than ``INSTALLED`` -- which is why nothing here
    stores a state column. A state that can be written independently of the
    thing it describes is a second authority, and it is the one that goes stale.

    ``content_sha256`` is unique across regions: it is the identity tiles are
    served under, and two rows claiming one delivery identity would make a tile
    URL ambiguous. Two regions with byte-identical packages is not a case worth
    supporting -- it cannot happen for real extracts, and allowing it would cost
    the immutable URL guarantee.

    Jobs are kept after they finish. "Why is this region not installed?" is
    answered by the failed job that says so, and deleting the record would make
    the answer "nothing ever happened".
    """
    _execute_all(connection, _SCHEMA_7)


def _migrate_to_8(connection: sqlite3.Connection) -> None:
    """Give a position room for what a sensor measured beside it.

    Two nullable columns rather than a table of readings. A reading belongs to
    exactly one position, arrives with it and is read with it, so a join would
    buy nothing and cost every geometry read a second query.

    Nullable because most positions have neither, and because every position
    already stored has neither: this migration adds room, not data. The stored
    geometry is not rewritten -- the readings were never parsed, so they are not
    in the archive to recover. Reprocessing is what puts them there, from the
    byte-identical raw copy, and the normalization schema version is bumped in
    the same change so every affected track reports itself outdated and says so.
    """
    connection.execute("ALTER TABLE track_points ADD COLUMN heart_rate INTEGER")
    connection.execute("ALTER TABLE track_points ADD COLUMN cadence INTEGER")


def _migrate_to_9(connection: sqlite3.Connection) -> None:
    """Give every track the identity of the *recording* it describes.

    One ride exported as GPX 1.1, as GPX 1.0 and as a route is three raw
    imports -- correctly, because the bytes are different evidence and only one
    of them carries a heart rate. It is one afternoon, and until now nothing
    said so.

    Backfilled here rather than left for reprocessing. The value is derived from
    geometry the archive already holds, so it can be computed for every existing
    track in one pass; bumping the normalization version instead would claim the
    *normalized data* changed, which it did not, and would outdate every track
    to add a column that could be filled without touching one.
    """
    connection.execute("ALTER TABLE tracks ADD COLUMN geometry_sha256 TEXT")
    rows = connection.execute(
        "SELECT s.track_id AS track_id, p.latitude, p.longitude, p.recorded_at "
        "FROM track_segments s JOIN track_points p ON p.segment_id = s.id "
        "ORDER BY s.track_id, s.position, p.position"
    ).fetchall()
    lines: dict[int, list[bytes]] = {}
    for row in rows:
        instant = row["recorded_at"] or ""
        lines.setdefault(int(row["track_id"]), []).append(
            f"{float(row['latitude'])!r},{float(row['longitude'])!r},{instant}\n".encode()
        )
    connection.executemany(
        "UPDATE tracks SET geometry_sha256 = ? WHERE id = ?",
        [
            (hashlib.sha256(b"".join(parts)).hexdigest(), track_id)
            for track_id, parts in lines.items()
        ],
    )
    connection.execute("CREATE INDEX idx_tracks_geometry ON tracks(geometry_sha256)")


def _migrate_to_10(connection: sqlite3.Connection) -> None:
    """Give every track the rectangle it occupies.

    A fact about the geometry, stored beside the other facts a listing already
    reads without touching a position -- the point count, the first and last
    instant. Where that rectangle *is* stays a question answered at read time,
    because the answer comes from a catalog that changes and a stored name would
    be the copy nobody updates.

    Backfilled here for the same reason the recording fingerprint was: it is
    derived from geometry the archive already holds.
    """
    for column in ("min_longitude", "min_latitude", "max_longitude", "max_latitude"):
        connection.execute(f"ALTER TABLE tracks ADD COLUMN {column} REAL")
    connection.execute(
        "UPDATE tracks SET "
        "min_longitude = (SELECT MIN(p.longitude) FROM track_segments s "
        "  JOIN track_points p ON p.segment_id = s.id WHERE s.track_id = tracks.id), "
        "min_latitude = (SELECT MIN(p.latitude) FROM track_segments s "
        "  JOIN track_points p ON p.segment_id = s.id WHERE s.track_id = tracks.id), "
        "max_longitude = (SELECT MAX(p.longitude) FROM track_segments s "
        "  JOIN track_points p ON p.segment_id = s.id WHERE s.track_id = tracks.id), "
        "max_latitude = (SELECT MAX(p.latitude) FROM track_segments s "
        "  JOIN track_points p ON p.segment_id = s.id WHERE s.track_id = tracks.id)"
    )


def _migrate_to_11(connection: sqlite3.Connection) -> None:
    """Keep the owner's arrangement of a page.

    One row per page, holding the arrangement as one document. It is
    presentation, so it references nothing: removing every track leaves it, and
    arranging the page changes no track. It lives in this database rather than
    in a browser because it is still somebody's work -- this way it is the same
    on every device and a backup carries it.

    A document rather than a table of tiles. The arrangement is read and written
    whole, never queried by tile, and ``format_version`` says which shape the
    document has, so a later shape is a new version a reader can refuse rather
    than a column it misreads.

    Nothing is back-filled: nobody has arranged anything yet, and a stored copy
    of the default would hide every later improvement to it.
    """
    _execute_all(connection, _SCHEMA_11)


MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migrate_to_1,
    _migrate_to_2,
    _migrate_to_3,
    _migrate_to_4,
    _migrate_to_5,
    _migrate_to_6,
    _migrate_to_7,
    _migrate_to_8,
    _migrate_to_9,
    _migrate_to_10,
    _migrate_to_11,
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
