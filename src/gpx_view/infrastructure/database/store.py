"""The SQLite implementation of the track repository.

Infrastructure owns SQLite: no inner layer imports ``sqlite3``. The store speaks
domain objects on the outside and rows on the inside.

Transactions are explicit. One import is one transaction, so a failure leaves no
raw import without its run and no track without its geometry. Every connection
enforces foreign keys and waits instead of failing immediately when another
process holds the write lock -- the HTTP server and a command-line import may run
against the same file.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from gpx_view.application import ImportErrorCode, TrackImportError
from gpx_view.application.ports import ProcessingSnapshot, TrackSummary
from gpx_view.domain import (
    Activity,
    ClassificationResult,
    InputChannel,
    NormalizedTrack,
    ProcessingRun,
    ProcessingStatus,
    RawImport,
    SourceMetadata,
    TrackClassification,
    TrackKind,
    TrackPoint,
    TrackSegment,
)
from gpx_view.infrastructure.database.migrations import (
    LEGACY_SOURCE_KEY_PREFIX,
    SCHEMA_VERSION,
    apply_migrations,
)
from gpx_view.infrastructure.private_data import create_private_directory, create_private_file

BUSY_TIMEOUT_MILLISECONDS = 5000


def _as_text(moment: datetime | None) -> str | None:
    """Return an instant as an unambiguous UTC string, or ``None``."""
    return None if moment is None else moment.astimezone(UTC).isoformat()


def _as_instant(text: str | None) -> datetime | None:
    """Return a stored string as an instant, or ``None``."""
    return None if text is None else datetime.fromisoformat(text)


class SqliteTrackStore:
    """Stores raw imports, processing runs and normalized tracks in one file."""

    def __init__(self, path: Path) -> None:
        """Point the store at a database file. Nothing is created until migration."""
        self._path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and close it again.

        A fresh connection per operation keeps the store usable from the request
        threads FastAPI runs synchronous handlers in, without sharing state.

        The database file is claimed before the driver opens it. ``sqlite3``
        creates a missing file with its own default mode, which the ordinary
        umask leaves world-readable, and a database that was briefly readable by
        everyone was readable by everyone. Creating it privately first also
        decides the journal files: SQLite gives the write-ahead log and the
        shared-memory file the permissions of the database they belong to, and
        those hold the same movement data.
        """
        create_private_directory(self._path.parent)
        create_private_file(self._path)
        connection = sqlite3.connect(self._path, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MILLISECONDS:d}")
            # Write-ahead logging lets a command-line import and the HTTP server
            # use the same database file. All three journal files stay inside the
            # data directory, so a backup of that directory is complete.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside an explicit write transaction."""
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    # --- schema ---------------------------------------------------------

    def migrate(self) -> int:
        """Bring the database up to the schema this build expects.

        Foreign keys are disabled for the duration. A migration that has to
        rebuild a table drops the old one, and dropping it with foreign keys on
        would cascade every dependent row away -- the migration would "succeed"
        and leave the tracks without their geometry. ``PRAGMA foreign_keys`` is a
        no-op inside a transaction, so it is set before the transaction opens,
        and ``PRAGMA foreign_key_check`` proves the result is sound before the
        transaction commits.
        """
        with self.connection() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = int(connection.execute("PRAGMA user_version").fetchone()[0])
                version = apply_migrations(connection, current)
                _require_referential_integrity(connection)
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return version

    def schema_version(self) -> int:
        """Return the schema version currently stored in the database."""
        with self.connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    # --- raw imports and runs -------------------------------------------

    def find_raw_import(self, sha256: str) -> RawImport | None:
        """Return the raw import with that content hash, if it is known."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM raw_imports WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return None if row is None else _raw_import_from(row)

    def latest_run(self, sha256: str) -> ProcessingRun | None:
        """Return the most recent processing run for a raw import."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM processing_runs WHERE raw_import_sha256 = ? "
                "ORDER BY id DESC LIMIT 1",
                (sha256,),
            ).fetchone()
        return None if row is None else _run_from(row)

    def processing_snapshot(self, sha256: str) -> ProcessingSnapshot | None:
        """Return what one raw import's processing amounts to, or ``None``."""
        with self.connection() as connection:
            row = connection.execute(_SNAPSHOT_QUERY + " WHERE i.sha256 = ?", (sha256,)).fetchone()
        return None if row is None else _snapshot_from(row)

    def processing_snapshots(self) -> tuple[ProcessingSnapshot, ...]:
        """Return the same for every raw import, oldest received first."""
        with self.connection() as connection:
            rows = connection.execute(
                _SNAPSHOT_QUERY + " ORDER BY i.received_at ASC, i.sha256 ASC"
            ).fetchall()
        return tuple(_snapshot_from(row) for row in rows)

    def run_count(self, sha256: str) -> int:
        """Return how many processing runs a raw import has accumulated."""
        with self.connection() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM processing_runs WHERE raw_import_sha256 = ?",
                    (sha256,),
                ).fetchone()[0]
            )

    def track_ids_for(self, sha256: str) -> tuple[int, ...]:
        """Return the current tracks of a raw import, in source order."""
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT t.id FROM tracks t JOIN raw_imports i ON i.sha256 = t.raw_import_sha256 "
                "AND i.active_processing_run_id = t.processing_run_id "
                "WHERE t.raw_import_sha256 = ? ORDER BY t.source_index, t.id",
                (sha256,),
            ).fetchall()
        return tuple(int(row["id"]) for row in rows)

    def record_import(
        self,
        raw_import: RawImport,
        run: ProcessingRun,
        tracks: Sequence[NormalizedTrack],
    ) -> tuple[int, ...]:
        """Store one import attempt in a single transaction."""
        try:
            with self._transaction() as connection:
                self._insert_raw_import(connection, raw_import)
                run_id = self._insert_run(connection, run)
                identities = tuple(
                    self._upsert_track(connection, raw_import.sha256, run_id, index, track)
                    for index, track in enumerate(tracks)
                )
                if run.status is ProcessingStatus.SUCCEEDED:
                    self._activate(connection, raw_import.sha256, run_id)
                return identities
        except sqlite3.Error as error:
            raise TrackImportError(ImportErrorCode.PERSISTENCE_FAILED) from error
        except (TypeError, ValueError, AttributeError) as error:
            raise TrackImportError(ImportErrorCode.PERSISTENCE_FAILED) from error

    @staticmethod
    def _activate(connection: sqlite3.Connection, sha256: str, run_id: int) -> None:
        """Make this run's candidates the current normalized generation.

        The switch is one statement inside the same transaction as the tracks it
        activates, so a reader never sees half a generation. Only a successful run
        reaches this: a failed attempt is recorded as history and leaves the last
        good generation exactly where it was.
        """
        connection.execute(
            "UPDATE raw_imports SET active_processing_run_id = ? WHERE sha256 = ?",
            (run_id, sha256),
        )

    def _insert_raw_import(self, connection: sqlite3.Connection, raw: RawImport) -> None:
        """Insert the raw import, or leave the existing record untouched.

        ``DO NOTHING`` is the immutability rule in SQL: a second import of the
        same bytes never rewrites what was recorded the first time.
        """
        connection.execute(
            "INSERT INTO raw_imports "
            "(sha256, size_bytes, original_filename, received_at, media_type, input_channel) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (sha256) DO NOTHING",
            (
                raw.sha256,
                raw.size_bytes,
                raw.original_filename,
                _as_text(raw.received_at),
                raw.media_type,
                raw.input_channel.value,
            ),
        )

    def _insert_run(self, connection: sqlite3.Connection, run: ProcessingRun) -> int:
        """Append a processing run and return its identity."""
        cursor = connection.execute(
            "INSERT INTO processing_runs (raw_import_sha256, importer, importer_version, "
            "normalization_schema_version, processed_at, status, error_code, classifier, "
            "classifier_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.raw_import_sha256,
                run.importer,
                run.importer_version,
                run.normalization_schema_version,
                _as_text(run.processed_at),
                run.status.value,
                run.error_code,
                run.classifier,
                run.classifier_version,
            ),
        )
        return int(cursor.lastrowid or 0)

    def _upsert_track(
        self,
        connection: sqlite3.Connection,
        sha256: str,
        run_id: int,
        source_index: int,
        track: NormalizedTrack,
    ) -> int:
        """Store or replace one track, keeping its identity and any user override."""
        self._adopt_legacy_row(connection, sha256, source_index, track.source_key)
        connection.execute(
            "INSERT INTO tracks (raw_import_sha256, source_key, source_index, processing_run_id, "
            "title, activity, exchange_format, format_version, creator, started_at, ended_at, "
            "point_count, segment_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (raw_import_sha256, source_key) DO UPDATE SET "
            "source_index = excluded.source_index, "
            "processing_run_id = excluded.processing_run_id, title = excluded.title, "
            "activity = excluded.activity, exchange_format = excluded.exchange_format, "
            "format_version = excluded.format_version, creator = excluded.creator, "
            "started_at = excluded.started_at, ended_at = excluded.ended_at, "
            "point_count = excluded.point_count, segment_count = excluded.segment_count",
            (
                sha256,
                track.source_key,
                source_index,
                run_id,
                track.title,
                Activity(track.activity).value,
                track.source.exchange_format,
                track.source.format_version,
                track.source.creator,
                _as_text(track.started_at),
                _as_text(track.ended_at),
                track.point_count,
                track.segment_count,
            ),
        )
        track_id = int(
            connection.execute(
                "SELECT id FROM tracks WHERE raw_import_sha256 = ? AND source_key = ?",
                (sha256, track.source_key),
            ).fetchone()["id"]
        )

        # The user override table is deliberately not touched here: reprocessing
        # replaces what the classifier found, never what the user decided.
        for table in (
            "track_classifications",
            "track_evidence",
            "track_external_links",
            "track_extension_namespaces",
            "track_segments",
        ):
            connection.execute(f"DELETE FROM {table} WHERE track_id = ?", (track_id,))  # noqa: S608

        detected = track.classification.detected
        connection.execute(
            "INSERT INTO track_classifications "
            "(track_id, detected_kind, confidence, method, method_version) VALUES (?, ?, ?, ?, ?)",
            (
                track_id,
                detected.kind.value,
                detected.confidence,
                detected.method,
                detected.method_version,
            ),
        )
        connection.executemany(
            "INSERT INTO track_evidence (track_id, position, code) VALUES (?, ?, ?)",
            [(track_id, position, code) for position, code in enumerate(detected.evidence)],
        )
        connection.executemany(
            "INSERT INTO track_external_links (track_id, position, url) VALUES (?, ?, ?)",
            [(track_id, position, url) for position, url in enumerate(track.source.external_links)],
        )
        connection.executemany(
            "INSERT INTO track_extension_namespaces (track_id, position, namespace) "
            "VALUES (?, ?, ?)",
            [
                (track_id, position, namespace)
                for position, namespace in enumerate(track.source.extension_namespaces)
            ],
        )
        for position, segment in enumerate(track.segments):
            cursor = connection.execute(
                "INSERT INTO track_segments (track_id, position) VALUES (?, ?)",
                (track_id, position),
            )
            segment_id = int(cursor.lastrowid or 0)
            connection.executemany(
                "INSERT INTO track_points "
                "(segment_id, position, latitude, longitude, elevation, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        segment_id,
                        point_position,
                        point.latitude,
                        point.longitude,
                        point.elevation,
                        _as_text(point.time),
                    )
                    for point_position, point in enumerate(segment.points)
                ],
            )
        return track_id

    @staticmethod
    def _adopt_legacy_row(
        connection: sqlite3.Connection, sha256: str, source_index: int, source_key: str
    ) -> None:
        """Give a schema-1 row the identity the importer produces for it.

        Rows written before candidates had a source key were keyed by their
        position, and migration 2 could only record that position as a
        ``legacy:`` key. The first reprocess is where the real key becomes
        knowable, because the importer reads the source document again and
        reports its candidates in the same order.

        Adopting the row rather than inserting a new one is what keeps a user
        correction attached to the track it was made on. It happens once: after
        the rename there is no legacy row left to match, and a candidate that
        already carries a real key is never touched here.
        """
        if source_key.startswith(LEGACY_SOURCE_KEY_PREFIX):
            return
        connection.execute(
            "UPDATE tracks SET source_key = ? "
            "WHERE raw_import_sha256 = ? AND source_key = ? "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM tracks existing "
            "   WHERE existing.raw_import_sha256 = ? AND existing.source_key = ?)",
            (
                source_key,
                sha256,
                f"{LEGACY_SOURCE_KEY_PREFIX}{source_index}",
                sha256,
                source_key,
            ),
        )

    # --- queries --------------------------------------------------------

    def list_tracks(self) -> tuple[TrackSummary, ...]:
        """Return the current tracks, newest imported source first.

        The declared order is implemented rather than approximated: newest raw
        import first, then the candidates of that source in the order the
        document presents them. The track identity is the final tie-breaker, so
        two sources received in the same instant still list deterministically.

        Sorting by activity date is a different feature and belongs to whoever
        asks for it, not to the storage default.
        """
        with self.connection() as connection:
            rows = connection.execute(
                _SUMMARY_QUERY + " ORDER BY i.received_at DESC, t.source_index ASC, t.id ASC"
            ).fetchall()
            return tuple(self._summaries(connection, rows))

    def get_track(self, track_id: int) -> TrackSummary | None:
        """Return one current track without its geometry.

        A candidate that a later run stopped producing is history, and history is
        a different question from "show me this track". Answering it here would
        mix the two into one endpoint, so this reports absence instead.
        """
        with self.connection() as connection:
            rows = connection.execute(_SUMMARY_QUERY + " WHERE t.id = ?", (track_id,)).fetchall()
            summaries = self._summaries(connection, rows)
        return summaries[0] if summaries else None

    def _summaries(
        self, connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
    ) -> tuple[TrackSummary, ...]:
        """Turn summary rows plus their side tables into domain-shaped summaries."""
        if not rows:
            return ()
        ids = [int(row["id"]) for row in rows]
        evidence = _grouped(connection, "track_evidence", "code", ids)
        links = _grouped(connection, "track_external_links", "url", ids)
        namespaces = _grouped(connection, "track_extension_namespaces", "namespace", ids)
        return tuple(_summary_from(row, evidence, links, namespaces) for row in rows)

    def get_geometry(self, track_id: int) -> tuple[TrackSegment, ...] | None:
        """Return the segments of one stored track, in source order."""
        with self.connection() as connection:
            if not _is_current(connection, track_id):
                return None
            rows = connection.execute(
                "SELECT s.position AS segment_position, p.latitude, p.longitude, p.elevation, "
                "p.recorded_at FROM track_segments s JOIN track_points p ON p.segment_id = s.id "
                "WHERE s.track_id = ? ORDER BY s.position, p.position",
                (track_id,),
            ).fetchall()

        points: dict[int, list[TrackPoint]] = {}
        for row in rows:
            points.setdefault(int(row["segment_position"]), []).append(
                TrackPoint(
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    elevation=None if row["elevation"] is None else float(row["elevation"]),
                    time=_as_instant(row["recorded_at"]),
                )
            )
        return tuple(TrackSegment(points=tuple(points[key])) for key in sorted(points))

    # --- user override --------------------------------------------------

    def set_override(self, track_id: int, kind: TrackKind, at: datetime) -> bool:
        """Record an explicit user correction on a current track."""
        with self._transaction() as connection:
            if not _is_current(connection, track_id):
                return False
            connection.execute(
                "INSERT INTO track_classification_overrides (track_id, override_kind, set_at) "
                "VALUES (?, ?, ?) ON CONFLICT (track_id) DO UPDATE SET "
                "override_kind = excluded.override_kind, set_at = excluded.set_at",
                (track_id, kind.value, _as_text(at)),
            )
            return True

    def clear_override(self, track_id: int) -> bool:
        """Withdraw a user correction from a current track."""
        with self._transaction() as connection:
            if not _is_current(connection, track_id):
                return False
            connection.execute(
                "DELETE FROM track_classification_overrides WHERE track_id = ?", (track_id,)
            )
            return True


# Only the current generation is a track. A row whose processing run is no longer
# the raw import's active one is history: the candidate it describes was not
# produced by the run that currently speaks for its source, so it is not
# something a reader may act on. The row stays -- with its user override -- for
# the day the same candidate reappears.
_CURRENT_GENERATION = """
  FROM tracks t
  JOIN raw_imports i
    ON i.sha256 = t.raw_import_sha256
   AND i.active_processing_run_id = t.processing_run_id
  JOIN track_classifications c ON c.track_id = t.id
  LEFT JOIN track_classification_overrides o ON o.track_id = t.id
"""

_SUMMARY_QUERY = (
    """
SELECT t.id, t.raw_import_sha256, t.source_index, t.source_key, t.title, t.activity,
       t.exchange_format, t.format_version, t.creator, t.started_at, t.ended_at,
       t.point_count, t.segment_count, i.received_at AS raw_received_at,
       c.detected_kind, c.confidence, c.method, c.method_version,
       o.override_kind
"""
    + _CURRENT_GENERATION
)


_RUN_COLUMNS = (
    "raw_import_sha256",
    "importer",
    "importer_version",
    "normalization_schema_version",
    "processed_at",
    "status",
    "error_code",
    "classifier",
    "classifier_version",
)


def _aliased_run_columns(alias: str) -> str:
    """Return the run columns of one join, prefixed so two runs fit in one row."""
    columns = ", ".join(f"{alias}.{column} AS {alias}_{column}" for column in _RUN_COLUMNS)
    return f"{alias}.id AS {alias}_id, {columns}"


# Both facts about a source in one row: the run whose candidates are current, and
# the newest attempt whatever it did. They are joined rather than queried
# separately because they have to describe the same instant -- a snapshot taken in
# two reads could show a current run that a concurrent import has already
# replaced.
#
# The interpolated parts are the column list of this module's own table. No
# caller-supplied value reaches the query text; the one parameter it takes is
# bound.
_SNAPSHOT_QUERY = f"""
SELECT i.sha256,
       {_aliased_run_columns("cur")},
       {_aliased_run_columns("lat")},
       (SELECT count(*) FROM tracks t
         WHERE t.raw_import_sha256 = i.sha256
           AND t.processing_run_id = i.active_processing_run_id) AS track_count
  FROM raw_imports i
  LEFT JOIN processing_runs cur ON cur.id = i.active_processing_run_id
  LEFT JOIN processing_runs lat ON lat.id = (
      SELECT id FROM processing_runs
       WHERE raw_import_sha256 = i.sha256 ORDER BY id DESC LIMIT 1)
"""  # noqa: S608


def _snapshot_from(row: sqlite3.Row) -> ProcessingSnapshot:
    """Rebuild one raw import's processing snapshot from its joined row."""
    return ProcessingSnapshot(
        raw_import_sha256=str(row["sha256"]),
        current_run_id=None if row["cur_id"] is None else int(row["cur_id"]),
        current_run=_joined_run(row, "cur"),
        latest_run_id=None if row["lat_id"] is None else int(row["lat_id"]),
        latest_run=_joined_run(row, "lat"),
        track_count=int(row["track_count"]),
    )


def _joined_run(row: sqlite3.Row, alias: str) -> ProcessingRun | None:
    """Rebuild one of the two runs a snapshot row carries, if it is there."""
    if row[f"{alias}_id"] is None:
        return None
    processed_at = _as_instant(row[f"{alias}_processed_at"])
    if processed_at is None:
        raise ValueError("a stored processing run must carry a processed timestamp")
    return ProcessingRun(
        raw_import_sha256=str(row[f"{alias}_raw_import_sha256"]),
        importer=str(row[f"{alias}_importer"]),
        importer_version=str(row[f"{alias}_importer_version"]),
        normalization_schema_version=int(row[f"{alias}_normalization_schema_version"]),
        processed_at=processed_at,
        status=ProcessingStatus(row[f"{alias}_status"]),
        error_code=row[f"{alias}_error_code"],
        classifier=row[f"{alias}_classifier"],
        classifier_version=row[f"{alias}_classifier_version"],
    )


def _grouped(
    connection: sqlite3.Connection, table: str, column: str, track_ids: Sequence[int]
) -> dict[int, tuple[str, ...]]:
    """Return one ordered value list per track, fetched in a single query.

    The table and column names come from this module, never from a caller.
    """
    placeholders = ",".join("?" * len(track_ids))
    rows = connection.execute(
        f"SELECT track_id, {column} AS value FROM {table} "  # noqa: S608
        f"WHERE track_id IN ({placeholders}) ORDER BY track_id, position",
        tuple(track_ids),
    ).fetchall()
    grouped: dict[int, list[str]] = {}
    for row in rows:
        grouped.setdefault(int(row["track_id"]), []).append(str(row["value"]))
    return {key: tuple(values) for key, values in grouped.items()}


def _is_current(connection: sqlite3.Connection, track_id: int) -> bool:
    """Report whether a track belongs to its raw import's current generation."""
    return (
        connection.execute(
            "SELECT 1 FROM tracks t JOIN raw_imports i ON i.sha256 = t.raw_import_sha256 "
            "AND i.active_processing_run_id = t.processing_run_id WHERE t.id = ?",
            (track_id,),
        ).fetchone()
        is not None
    )


def _require_referential_integrity(connection: sqlite3.Connection) -> None:
    """Refuse to commit a migration that left an orphaned row behind.

    Foreign keys are off while a table is rebuilt, so the database itself cannot
    object during the migration. This asks it afterwards, while a rollback is
    still possible.
    """
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("the migration left the database referentially inconsistent")


def _raw_import_from(row: sqlite3.Row) -> RawImport:
    """Rebuild a raw import from its row."""
    received_at = _as_instant(row["received_at"])
    if received_at is None:
        raise ValueError("a stored raw import must carry a received timestamp")
    return RawImport(
        sha256=str(row["sha256"]),
        size_bytes=int(row["size_bytes"]),
        original_filename=row["original_filename"],
        received_at=received_at,
        media_type=row["media_type"],
        input_channel=InputChannel(row["input_channel"]),
    )


def _run_from(row: sqlite3.Row) -> ProcessingRun:
    """Rebuild a processing run from its row."""
    processed_at = _as_instant(row["processed_at"])
    if processed_at is None:
        raise ValueError("a stored processing run must carry a processed timestamp")
    return ProcessingRun(
        raw_import_sha256=str(row["raw_import_sha256"]),
        importer=str(row["importer"]),
        importer_version=str(row["importer_version"]),
        normalization_schema_version=int(row["normalization_schema_version"]),
        processed_at=processed_at,
        status=ProcessingStatus(row["status"]),
        error_code=row["error_code"],
        classifier=row["classifier"],
        classifier_version=row["classifier_version"],
    )


def _summary_from(
    row: sqlite3.Row,
    evidence: dict[int, tuple[str, ...]],
    links: dict[int, tuple[str, ...]],
    namespaces: dict[int, tuple[str, ...]],
) -> TrackSummary:
    """Rebuild a track summary from its row and its side tables."""
    track_id = int(row["id"])
    override = row["override_kind"]
    return TrackSummary(
        track_id=track_id,
        raw_import_sha256=str(row["raw_import_sha256"]),
        source_index=int(row["source_index"]),
        source_key=str(row["source_key"]),
        title=row["title"],
        activity=Activity(row["activity"]),
        classification=TrackClassification(
            detected=ClassificationResult(
                kind=TrackKind(row["detected_kind"]),
                confidence=float(row["confidence"]),
                method=str(row["method"]),
                method_version=str(row["method_version"]),
                evidence=evidence.get(track_id, ()),
            ),
            override=None if override is None else TrackKind(override),
        ),
        point_count=int(row["point_count"]),
        segment_count=int(row["segment_count"]),
        started_at=_as_instant(row["started_at"]),
        ended_at=_as_instant(row["ended_at"]),
        source=SourceMetadata(
            exchange_format=str(row["exchange_format"]),
            format_version=row["format_version"],
            creator=row["creator"],
            external_links=links.get(track_id, ()),
            extension_namespaces=namespaces.get(track_id, ()),
        ),
    )


__all__ = ["SCHEMA_VERSION", "SqliteTrackStore"]
