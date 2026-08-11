"""The SQLite implementation of the track repository.

Infrastructure owns SQLite: no inner layer imports ``sqlite3``. The store speaks
domain objects on the outside and rows on the inside.

Transactions are explicit. One import is one transaction, so a failure leaves no
raw import without its run and no track without its geometry. Every connection
enforces foreign keys and waits instead of failing immediately when another
process holds the write lock -- the HTTP server and a command-line import may run
against the same file.
"""

import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from trackvault.application import ImportErrorCode, TrackImportError
from trackvault.application.ports import (
    MAX_PAGE_SIZE,
    AnalysisAvailability,
    AnalysisRun,
    AnalysisSnapshot,
    AnalysisStatus,
    DatedTrackRow,
    ProcessingSnapshot,
    StoredAnalysis,
    TrackAggregationRow,
    TrackOrder,
    TrackPage,
    TrackQuery,
    TrackSummary,
)
from trackvault.domain import (
    MEASUREMENT_EVIDENCE_CODES,
    Activity,
    ClassificationResult,
    EvidenceCode,
    InputChannel,
    MetricProvenance,
    NormalizedTrack,
    ProcessingRun,
    ProcessingStatus,
    RawImport,
    SourceMetadata,
    TrackClassification,
    TrackKind,
    TrackPoint,
    TrackSegment,
    UserTrackMetadata,
    recording_fingerprint,
    temporal_evidence_of,
)
from trackvault.domain.analysis import (
    AnalysisProfile,
    AnalysisQuality,
    MetricName,
    MetricUnit,
    MetricValue,
    TrackAnalysis,
)
from trackvault.domain.maps import MapBounds
from trackvault.infrastructure.database.migrations import (
    LEGACY_SOURCE_KEY_PREFIX,
    SCHEMA_VERSION,
    apply_migrations,
)
from trackvault.infrastructure.private_data import create_private_directory, create_private_file

logger = logging.getLogger(__name__)

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
            "point_count, segment_count, geometry_sha256, "
            "min_longitude, min_latitude, max_longitude, max_latitude) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (raw_import_sha256, source_key) DO UPDATE SET "
            "source_index = excluded.source_index, "
            "processing_run_id = excluded.processing_run_id, title = excluded.title, "
            "activity = excluded.activity, exchange_format = excluded.exchange_format, "
            "format_version = excluded.format_version, creator = excluded.creator, "
            "started_at = excluded.started_at, ended_at = excluded.ended_at, "
            "point_count = excluded.point_count, segment_count = excluded.segment_count, "
            "geometry_sha256 = excluded.geometry_sha256, "
            "min_longitude = excluded.min_longitude, min_latitude = excluded.min_latitude, "
            "max_longitude = excluded.max_longitude, max_latitude = excluded.max_latitude",
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
                # Derived here rather than carried on the normalized track: it
                # is a projection of geometry the track already is, and a field
                # every construction site had to supply would be one more place
                # to get it wrong.
                recording_fingerprint(track.segments),
                *_extent_of(track.segments),
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
                "(segment_id, position, latitude, longitude, elevation, recorded_at, "
                "heart_rate, cadence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        segment_id,
                        point_position,
                        point.latitude,
                        point.longitude,
                        point.elevation,
                        _as_text(point.time),
                        point.heart_rate_bpm,
                        point.cadence_rpm,
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

    @property
    def max_page_size(self) -> int:
        """Return the largest page this store will return."""
        return MAX_PAGE_SIZE

    def list_tracks(self, query: TrackQuery) -> TrackPage:
        """Return one page of the current tracks, filtered and ordered in SQL.

        The total is counted over the same restriction as the page, in a second
        statement rather than by measuring the page: a total that ignored the
        filter, or that only counted what came back, would make every pager
        built on it wrong.
        """
        limit = max(1, min(query.limit, MAX_PAGE_SIZE))
        offset = max(0, query.offset)
        count_text, count_values = _count_statement(query)
        page_text, page_values = _page_statement(query)
        with self.connection() as connection:
            total = int(connection.execute(count_text, count_values).fetchone()[0])
            rows = connection.execute(page_text, (*page_values, limit, offset)).fetchall()
            tracks = self._summaries(connection, rows)
        return TrackPage(tracks=tracks, total=total, limit=limit, offset=offset)

    def get_track(
        self, track_id: int, installed_analysis: AnalysisProfile | None = None
    ) -> TrackSummary | None:
        """Return one current track without its geometry.

        A candidate that a later run stopped producing is history, and history is
        a different question from "show me this track". Answering it here would
        mix the two into one endpoint, so this reports absence instead.

        Args:
            track_id: Identity of the track.
            installed_analysis: The algorithms this build applies, so that the
                summary can say what the track's stored metrics amount to.
                ``None`` means nothing counts as current, which is the same
                fail-closed answer the domain's own currency check gives.
        """
        text, values = _summary_statement(installed_analysis)
        with self.connection() as connection:
            rows = connection.execute(f"{text} WHERE t.id = ?", (*values, track_id)).fetchall()
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
        # One batched lookup for the whole page rather than one per track. A
        # listing that costs a query per row is what an N+1 is, and it appears
        # exactly here -- where the convenient code would loop. Only the current
        # analyses are read: the others have nothing a row is allowed to show.
        metrics, _ = _metrics_for_runs(
            connection,
            [
                int(row["current_analysis_run_id"])
                for row in rows
                if row["current_analysis_run_id"] is not None
                and row["analysis_availability"] == AnalysisAvailability.CURRENT.value
            ],
        )
        siblings = _same_recording(connection, rows)
        return tuple(
            _summary_from(row, evidence, links, namespaces, metrics, siblings) for row in rows
        )

    def get_geometry(self, track_id: int) -> tuple[TrackSegment, ...] | None:
        """Return the segments of one stored track, in source order."""
        with self.connection() as connection:
            if not _is_current(connection, track_id):
                return None
            rows = connection.execute(
                "SELECT s.position AS segment_position, p.latitude, p.longitude, p.elevation, "
                "p.recorded_at, p.heart_rate, p.cadence "
                "FROM track_segments s JOIN track_points p ON p.segment_id = s.id "
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
                    heart_rate_bpm=None if row["heart_rate"] is None else int(row["heart_rate"]),
                    cadence_rpm=None if row["cadence"] is None else int(row["cadence"]),
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

    # --- user-owned metadata --------------------------------------------

    def set_user_metadata(self, track_id: int, metadata: UserTrackMetadata, at: datetime) -> bool:
        """Store what the user says about a current track, or clear it.

        Empty metadata deletes the row. "Nothing was said" is then the absence
        of a record rather than a record of two nulls, which the table's own
        CHECK also refuses -- one representation, so a reset really resets.
        """
        with self._transaction() as connection:
            if not _is_current(connection, track_id):
                return False
            if metadata.is_empty:
                connection.execute(
                    "DELETE FROM track_user_metadata WHERE track_id = ?", (track_id,)
                )
                return True
            connection.execute(
                "INSERT INTO track_user_metadata (track_id, title, note, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (track_id) DO UPDATE SET "
                "title = excluded.title, note = excluded.note, updated_at = excluded.updated_at",
                (track_id, metadata.title, metadata.note, _as_text(at)),
            )
            return True

    # --- analysis -------------------------------------------------------

    def record_analysis(self, run: AnalysisRun, analysis: TrackAnalysis | None) -> int:
        """Store one analysis attempt in a single transaction."""
        try:
            with self._transaction() as connection:
                run_id = self._insert_analysis_run(connection, run)
                if analysis is not None:
                    self._insert_metrics(connection, run_id, analysis)
                if run.status is AnalysisStatus.SUCCEEDED:
                    self._publish_analysis(connection, run, run_id)
                return run_id
        except sqlite3.Error as error:
            raise TrackImportError(ImportErrorCode.PERSISTENCE_FAILED) from error
        except (TypeError, ValueError, AttributeError) as error:
            raise TrackImportError(ImportErrorCode.PERSISTENCE_FAILED) from error

    @staticmethod
    def _publish_analysis(connection: sqlite3.Connection, run: AnalysisRun, run_id: int) -> None:
        """Make this run's metrics current, unless the geometry moved on.

        The generation is part of the ``WHERE`` clause rather than of a check
        before it. A reprocess that committed while this analysis ran has
        already changed ``processing_run_id``, and the update then matches
        nothing -- so metrics derived from geometry no reader can see never
        become the answer for geometry they do not describe. The run itself
        stays recorded; it simply does not win.
        """
        connection.execute(
            "UPDATE tracks SET current_analysis_run_id = ? WHERE id = ? AND processing_run_id = ?",
            (run_id, run.track_id, run.processing_run_id),
        )

    @staticmethod
    def _insert_analysis_run(connection: sqlite3.Connection, run: AnalysisRun) -> int:
        """Append an analysis run and return its identity."""
        profile = run.profile
        cursor = connection.execute(
            "INSERT INTO analysis_runs (track_id, processing_run_id, distance_algorithm, "
            "distance_algorithm_version, movement_algorithm, movement_algorithm_version, "
            "elevation_algorithm, elevation_algorithm_version, metric_schema_version, "
            "analyzed_at, status, error_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.track_id,
                run.processing_run_id,
                profile.distance_algorithm,
                profile.distance_algorithm_version,
                profile.movement_algorithm,
                profile.movement_algorithm_version,
                profile.elevation_algorithm,
                profile.elevation_algorithm_version,
                profile.metric_schema_version,
                _as_text(run.analyzed_at),
                run.status.value,
                run.error_code,
            ),
        )
        return int(cursor.lastrowid or 0)

    @staticmethod
    def _insert_metrics(
        connection: sqlite3.Connection, run_id: int, analysis: TrackAnalysis
    ) -> None:
        """Write the metrics and the quality flags of one run.

        Only metrics that were derived are written. A row per name would mean
        storing a filler value for every unavailable metric, and a stored zero
        is indistinguishable from a measured one.
        """
        connection.executemany(
            "INSERT INTO track_metrics (analysis_run_id, metric, value, unit, provenance) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (run_id, name.value, metric.value, metric.unit.value, metric.provenance.value)
                for name, metric in analysis.metrics.items()
            ],
        )
        connection.executemany(
            "INSERT INTO analysis_quality_flags (analysis_run_id, position, flag) VALUES (?, ?, ?)",
            [(run_id, position, flag.value) for position, flag in enumerate(analysis.quality)],
        )

    def current_analysis(self, track_id: int) -> StoredAnalysis | None:
        """Return the metrics a track currently has, or ``None``.

        ``None`` also answers "there is a stored analysis and it cannot be
        interpreted". That is deliberate: handing over half of a set whose other
        half is impossible would put an unexplainable number in front of a user.
        Whether the archive is holding damage or simply nothing is a question
        the snapshot answers, and the application layer asks it there.
        """
        with self.connection() as connection:
            row = connection.execute(
                _ANALYSIS_RUN_QUERY
                + " JOIN tracks t ON t.current_analysis_run_id = a.id WHERE t.id = ?",
                (track_id,),
            ).fetchone()
            if row is None:
                return None
            run_id = int(row["id"])
            run = _readable_analysis_run(row)
            metrics = _metrics_of(connection, run_id)
            quality = _quality_of(connection, run_id)
            if run is None or metrics is None or quality is None:
                return None
            return StoredAnalysis(run_id=run_id, run=run, metrics=metrics, quality=quality)

    def analysis_snapshot(self, track_id: int) -> AnalysisSnapshot | None:
        """Return what one current track's analysis amounts to, or ``None``."""
        with self.connection() as connection:
            row = connection.execute(
                _ANALYSIS_SNAPSHOT_QUERY + " AND t.id = ?", (track_id,)
            ).fetchone()
            return None if row is None else self._snapshot(connection, [row])[0]

    def analysis_snapshots(self) -> tuple[AnalysisSnapshot, ...]:
        """Return the same for every current track, oldest imported source first."""
        with self.connection() as connection:
            rows = connection.execute(
                _ANALYSIS_SNAPSHOT_QUERY
                + " ORDER BY i.received_at ASC, t.source_index ASC, t.id ASC"
            ).fetchall()
            return self._snapshot(connection, rows)

    @staticmethod
    def _snapshot(
        connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
    ) -> tuple[AnalysisSnapshot, ...]:
        """Turn snapshot rows into snapshots, withholding what cannot be read.

        The stored output is validated here rather than at presentation time, so
        that one authority answers "is this current?" for damage in the run row
        and damage in the metrics alike -- and so that ``analyze --outdated``
        offers to repair both.
        """
        unreadable = _unreadable_runs(
            connection, [int(row["cur_id"]) for row in rows if row["cur_id"] is not None]
        )
        return tuple(
            _analysis_snapshot_from(row, without_current=row["cur_id"] in unreadable)
            for row in rows
        )

    def analysis_run_count(self, track_id: int) -> int:
        """Return how many analysis runs a track has accumulated."""
        with self.connection() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM analysis_runs WHERE track_id = ?", (track_id,)
                ).fetchone()[0]
            )

    # --- aggregation ----------------------------------------------------

    def placed_aggregation_rows(
        self, since: datetime, until: datetime | None
    ) -> tuple[TrackAggregationRow, ...]:
        """Return the current tracks a window can vouch for as having happened in it.

        Two conditions, not one. The activity date has to fall in the window,
        *and* the instants it comes from have to have been measured -- a
        plausible clock on geometry nobody travelled is not a date, and adding
        its distance to a month is how a total about nothing gets built.

        Instants are stored as UTC ISO-8601 strings in one fixed shape, so the
        range compares lexicographically exactly as it compares chronologically,
        and ``ix_tracks_started_at`` serves it. A yearly total therefore reads
        one bounded set of rows and never touches a position.

        An ``until`` of ``None`` is an open upper bound, which is what the last
        supported year has: there is no instant after it to compare against.
        """
        anchored, anchor_values = _calendar_anchored()
        window = " AND t.started_at >= ?" + ("" if until is None else " AND t.started_at < ?")
        instants: tuple[object, ...] = (
            (_as_text(since),) if until is None else (_as_text(since), _as_text(until))
        )
        return self._aggregation_rows(f"{window} AND ({anchored})", (*instants, *anchor_values))

    def dated_track_rows(self) -> tuple[DatedTrackRow, ...]:
        """Return every current track a calendar period may date.

        Three columns per track and no join to a metric, because the question
        is which periods exist rather than what they hold. The cost is one short
        row per dated track and never a position, which is the same promise
        every other aggregate here makes.
        """
        anchored, anchor_values = _calendar_anchored()
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT t.id, t.activity, t.started_at, "
                f"{_EFFECTIVE_KIND} AS effective_kind{_CURRENT_GENERATION}"
                f" WHERE t.started_at IS NOT NULL AND ({anchored})",
                anchor_values,
            ).fetchall()
        return tuple(
            DatedTrackRow(
                effective_kind=TrackKind(row["effective_kind"]),
                activity=Activity(row["activity"]),
                started_at=instant,
            )
            for row in rows
            if (instant := _as_instant(row["started_at"])) is not None
        )

    def current_track_count(self) -> int:
        """Return how many tracks the archive currently holds, in any scope."""
        with self.connection() as connection:
            return int(connection.execute(f"SELECT count(*){_CURRENT_GENERATION}").fetchone()[0])

    def unplaced_aggregation_rows(self) -> tuple[TrackAggregationRow, ...]:
        """Return the current tracks that belong to no calendar period at all.

        Two populations under one heading, told apart afterwards by whether the
        row carries an instant: a track with no times, and a track whose times
        nothing showed to be measured. Both have a length and no period, and
        both are reported beside a year rather than inside one.
        """
        anchored, anchor_values = _calendar_anchored()
        return self._aggregation_rows(
            f" AND (t.started_at IS NULL OR NOT ({anchored}))", anchor_values
        )

    def _aggregation_rows(
        self, restriction: str, parameters: tuple[object, ...]
    ) -> tuple[TrackAggregationRow, ...]:
        """Return the aggregation base rows a restriction selects.

        The restriction is built from this module's own fragments. No
        caller-supplied value reaches the query text; every value is bound.
        """
        with self.connection() as connection:
            rows = connection.execute(
                _AGGREGATION_QUERY + restriction + " ORDER BY t.id", parameters
            ).fetchall()
            run_ids = [
                int(row["analysis_run_id"]) for row in rows if row["analysis_run_id"] is not None
            ]
            metrics, _ = _metrics_for_runs(connection, run_ids)
            unreadable = _unreadable_runs(connection, run_ids)
            # One batched lookup for every track's evidence rather than one per
            # track: the timing basis is per track metadata, and a yearly total
            # must not turn into a query per row for it.
            evidence = (
                _grouped(connection, "track_evidence", "code", [int(row["id"]) for row in rows])
                if rows
                else {}
            )
        return tuple(
            TrackAggregationRow(
                track_id=int(row["id"]),
                effective_kind=TrackKind(row["override_kind"] or row["detected_kind"]),
                activity=Activity(row["activity"]),
                started_at=_as_instant(row["started_at"]),
                temporal_evidence=temporal_evidence_of(evidence.get(int(row["id"]), ())),
                # A run whose metrics could not be read is presented as no
                # current run at all, so the one currency authority reaches the
                # same verdict it would for damage in the run row itself and an
                # impossible distance can never be summed into somebody's year.
                analysis=_analysis_snapshot_from(
                    row,
                    track_id=int(row["id"]),
                    without_current=row["analysis_run_id"] in unreadable,
                ),
                metrics=(
                    {}
                    if row["analysis_run_id"] is None
                    else metrics.get(int(row["analysis_run_id"]), {})
                ),
            )
            for row in rows
        )


# Only the current generation is a track. A row whose processing run is no longer
# the raw import's active one is history: the candidate it describes was not
# produced by the run that currently speaks for its source, so it is not
# something a reader may act on. The row stays -- with its user override -- for
# the day the same candidate reappears.
#
# The analysis run is joined too, because what a track's stored metrics amount to
# is part of every read of it. Classifying that in the same statement is what
# keeps a filtered, ordered page costing the page: deciding it afterwards in
# Python would mean either paging before filtering, which reports the wrong
# total, or reading the archive to page it, which is the thing pagination exists
# against.
_CURRENT_GENERATION = """
  FROM tracks t
  JOIN raw_imports i
    ON i.sha256 = t.raw_import_sha256
   AND i.active_processing_run_id = t.processing_run_id
  JOIN track_classifications c ON c.track_id = t.id
  LEFT JOIN track_classification_overrides o ON o.track_id = t.id
  LEFT JOIN track_user_metadata u ON u.track_id = t.id
  LEFT JOIN analysis_runs cur ON cur.id = t.current_analysis_run_id
"""

_SUMMARY_COLUMNS = """
SELECT t.id, t.raw_import_sha256, t.source_index, t.source_key, t.title, t.activity,
       t.exchange_format, t.format_version, t.creator, t.started_at, t.ended_at,
       t.point_count, t.segment_count, t.geometry_sha256,
       t.min_longitude, t.min_latitude, t.max_longitude, t.max_latitude,
       i.received_at AS raw_received_at,
       c.detected_kind, c.confidence, c.method, c.method_version,
       o.override_kind, t.current_analysis_run_id,
       u.title AS user_title, u.note AS user_note
"""

# The distance of the current analysis, joined so that a listing can be ordered
# by it in SQL. A left join, because a track that has not been analysed still
# appears in the list -- it simply has no length to sort by, and sorts last.
_DISTANCE_JOIN = """
  LEFT JOIN track_metrics m
    ON m.analysis_run_id = t.current_analysis_run_id
   AND m.metric = ?
"""


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


_ANALYSIS_RUN_COLUMNS = (
    "track_id",
    "processing_run_id",
    "distance_algorithm",
    "distance_algorithm_version",
    "movement_algorithm",
    "movement_algorithm_version",
    "elevation_algorithm",
    "elevation_algorithm_version",
    "metric_schema_version",
    "analyzed_at",
    "status",
    "error_code",
)

_ANALYSIS_RUN_SELECTION = ", ".join(f"a.{column}" for column in _ANALYSIS_RUN_COLUMNS)

# The interpolated part is this module's own column list. No caller-supplied
# value reaches the query text; the one parameter it takes is bound.
_ANALYSIS_RUN_QUERY = f"SELECT a.id, {_ANALYSIS_RUN_SELECTION} FROM analysis_runs a"  # noqa: S608


def _aliased_analysis_columns(alias: str) -> str:
    """Return one analysis run's columns, prefixed so two runs fit in one row."""
    columns = ", ".join(f"{alias}.{column} AS {alias}_{column}" for column in _ANALYSIS_RUN_COLUMNS)
    return f"{alias}.id AS {alias}_id, {columns}"


# The current derived generation and the newest attempt, in one row, for every
# track a reader can see. The join to `raw_imports` is what restricts it to the
# current normalized generation: a candidate a later run stopped producing keeps
# its rows but is history, and history is not analysed.
#
# The interpolated parts are this module's own column lists. No caller-supplied
# value reaches the query text.
_ANALYSIS_SNAPSHOT_QUERY = f"""
SELECT t.id AS track_id, t.processing_run_id,
       {_aliased_analysis_columns("cur")},
       {_aliased_analysis_columns("lat")}
  FROM tracks t
  JOIN raw_imports i
    ON i.sha256 = t.raw_import_sha256
   AND i.active_processing_run_id = t.processing_run_id
  LEFT JOIN analysis_runs cur ON cur.id = t.current_analysis_run_id
  LEFT JOIN analysis_runs lat ON lat.id = (
      SELECT id FROM analysis_runs WHERE track_id = t.id ORDER BY id DESC LIMIT 1)
 WHERE 1 = 1
"""  # noqa: S608


def _analysis_profile_from(row: sqlite3.Row, prefix: str = "") -> AnalysisProfile:
    """Rebuild the analysis profile one run recorded."""
    return AnalysisProfile(
        distance_algorithm=str(row[f"{prefix}distance_algorithm"]),
        distance_algorithm_version=int(row[f"{prefix}distance_algorithm_version"]),
        movement_algorithm=str(row[f"{prefix}movement_algorithm"]),
        movement_algorithm_version=int(row[f"{prefix}movement_algorithm_version"]),
        elevation_algorithm=str(row[f"{prefix}elevation_algorithm"]),
        elevation_algorithm_version=int(row[f"{prefix}elevation_algorithm_version"]),
        metric_schema_version=int(row[f"{prefix}metric_schema_version"]),
    )


def _analysis_run_from(row: sqlite3.Row, prefix: str = "") -> AnalysisRun:
    """Rebuild one analysis run from its row.

    Raises:
        ValueError: If the row does not describe a run this build can interpret.
            Every caller here turns that into an absent run rather than letting
            it out; the exception exists so that the domain's own invariants do
            the checking rather than a second copy of them.
    """
    analyzed_at = _as_instant(row[f"{prefix}analyzed_at"])
    if analyzed_at is None:
        raise ValueError("a stored analysis run must carry an analysed timestamp")
    return AnalysisRun(
        track_id=int(row[f"{prefix}track_id"]),
        processing_run_id=int(row[f"{prefix}processing_run_id"]),
        profile=_analysis_profile_from(row, prefix),
        analyzed_at=analyzed_at,
        status=AnalysisStatus(row[f"{prefix}status"]),
        error_code=row[f"{prefix}error_code"],
    )


def _readable_analysis_run(row: sqlite3.Row, prefix: str = "") -> AnalysisRun | None:
    """Rebuild one analysis run, or report that it cannot be interpreted.

    Persisted derived state is untrusted input. A row can come from a version
    that is gone, a failing disk or a defect since fixed, and interpreting one
    that does not make sense is worse than admitting it: a profile version this
    build cannot parse must never be mistaken for the installed one.

    An unreadable run therefore reads as *absent*, which is the fail-closed
    direction -- the track shows as needing analysis again, and the geometry it
    would be derived from was never touched.
    """
    try:
        return _analysis_run_from(row, prefix)
    except (ValueError, TypeError):
        logger.warning("analysis.unreadable_run column_prefix=%r", prefix)
        return None


def _joined_analysis_run(row: sqlite3.Row, alias: str) -> AnalysisRun | None:
    """Rebuild one of the two runs a snapshot row carries, if it is readable."""
    if row[f"{alias}_id"] is None:
        return None
    return _readable_analysis_run(row, f"{alias}_")


def _analysis_snapshot_from(
    row: sqlite3.Row, *, track_id: int | None = None, without_current: bool = False
) -> AnalysisSnapshot:
    """Rebuild one track's analysis snapshot from its joined row.

    The identity is passed in where the row names the track's column something
    else -- the aggregation query selects ``t.id`` because it also carries the
    classification -- rather than aliasing one query to suit the other.

    ``without_current`` withholds a run the caller has already found unusable
    for a reason this row cannot see, such as a metric it could not read. The
    identity stays, which is what keeps "damaged" distinguishable from "never
    analysed".
    """
    return AnalysisSnapshot(
        track_id=int(row["track_id"]) if track_id is None else track_id,
        processing_run_id=int(row["processing_run_id"]),
        current_run_id=None if row["cur_id"] is None else int(row["cur_id"]),
        current_run=None if without_current else _joined_analysis_run(row, "cur"),
        latest_run_id=None if row["lat_id"] is None else int(row["lat_id"]),
        latest_run=_joined_analysis_run(row, "lat"),
    )


def _metric_value(name: MetricName, row: sqlite3.Row) -> MetricValue:
    """Rebuild one stored metric, rejecting values it could never have had.

    ``MetricValue`` already refuses NaN and infinity, which poison every later
    aggregation silently. The sign is checked here because only the name knows
    it: a distance, a duration and a speed cannot be negative, while an altitude
    below sea level is an ordinary Tuesday by the Dead Sea.

    Raises:
        ValueError: If the row does not describe a value this metric can hold.
    """
    value = float(row["value"])
    if value < 0.0 and not name.may_be_negative:
        raise ValueError(f"{name.value} cannot be negative")
    return MetricValue(
        value=value,
        unit=MetricUnit(row["unit"]),
        provenance=MetricProvenance(row["provenance"]),
    )


def _metrics_of(
    connection: sqlite3.Connection, run_id: int
) -> dict[MetricName, MetricValue] | None:
    """Return the metrics one analysis run produced, or ``None`` if unreadable.

    Two different kinds of surprise, deliberately handled differently.

    A stored *name* this build does not know is skipped. A metric a later
    release added, or an older one removed, says nothing about the rows beside
    it, and refusing the whole set over it would lose metrics that are still
    perfectly meaningful.

    A stored *value* that could not have been derived -- infinite, or negative
    where the metric has no negative -- condemns the whole run. It is evidence
    that something wrote rows this build cannot account for, and a set where one
    number is impossible is not a set where the others have been shown to be
    fine.
    """
    rows = connection.execute(
        "SELECT metric, value, unit, provenance FROM track_metrics WHERE analysis_run_id = ?",
        (run_id,),
    ).fetchall()
    metrics: dict[MetricName, MetricValue] = {}
    for row in rows:
        try:
            name = MetricName(row["metric"])
        except ValueError:
            continue
        try:
            metrics[name] = _metric_value(name, row)
        except (ValueError, TypeError):
            logger.warning("analysis.unreadable_metric run=%d", run_id)
            return None
    return metrics


def _quality_of(connection: sqlite3.Connection, run_id: int) -> tuple[AnalysisQuality, ...] | None:
    """Return the quality flags one run recorded, or ``None`` if unreadable.

    Unlike an unknown metric name, an unknown flag is not skipped. A flag says
    *what was wrong with the data these numbers came from*, so one this build
    cannot render is a caveat it would be dropping silently -- and presenting
    numbers without their caveat is the failure this vocabulary exists against.
    """
    rows = connection.execute(
        "SELECT flag FROM analysis_quality_flags WHERE analysis_run_id = ? ORDER BY position",
        (run_id,),
    ).fetchall()
    try:
        return tuple(AnalysisQuality(row["flag"]) for row in rows)
    except ValueError:
        logger.warning("analysis.unreadable_quality_flag run=%d", run_id)
        return None


# One row per current track, carrying only what an aggregate needs: no geometry,
# no evidence, no source metadata. The effective kind is projected here from the
# detected result and the override rather than stored, which is what lets a user
# correction move a track between the actual and planned totals without a single
# metric being derived again.
_AGGREGATION_QUERY = f"""
SELECT t.id, t.activity, t.started_at, t.processing_run_id,
       c.detected_kind, o.override_kind,
       t.current_analysis_run_id AS analysis_run_id,
       {_aliased_analysis_columns("cur")},
       {_aliased_analysis_columns("lat")}
  FROM tracks t
  JOIN raw_imports i
    ON i.sha256 = t.raw_import_sha256
   AND i.active_processing_run_id = t.processing_run_id
  JOIN track_classifications c ON c.track_id = t.id
  LEFT JOIN track_classification_overrides o ON o.track_id = t.id
  LEFT JOIN analysis_runs cur ON cur.id = t.current_analysis_run_id
  LEFT JOIN analysis_runs lat ON lat.id = (
      SELECT id FROM analysis_runs WHERE track_id = t.id ORDER BY id DESC LIMIT 1)
 WHERE 1 = 1
"""  # noqa: S608

# A listing filters on the effective kind, which is the override where there is
# one and the detected kind otherwise. Projected in SQL rather than compared in
# Python, so that the filter, the count and the page all agree and a correction
# moves a track between the sets with nothing recalculated.
_EFFECTIVE_KIND = "coalesce(o.override_kind, c.detected_kind)"


def _placeholders(values: Sequence[object]) -> str:
    """Return one bind placeholder per value."""
    return ",".join("?" * len(values))


def _calendar_anchored() -> tuple[str, tuple[object, ...]]:
    """Return the SQL that selects tracks whose timing may date them, and its values.

    A projection of :func:`~trackvault.domain.temporal_evidence.temporal_evidence_of`
    reaching `OBSERVED`, in the same sense that ``_EFFECTIVE_KIND`` projects the
    classification: the codes come from the domain, and the storage layer only
    asks whether a track carries them.

    It has to be a projection rather than a Python pass, because "which tracks
    belong to this month" decides a *count* as well as a page, and a filter
    applied after paging reports the wrong total.
    """
    codes = MEASUREMENT_EVIDENCE_CODES
    sql = f"""
        EXISTS (SELECT 1 FROM track_evidence ce
                 WHERE ce.track_id = t.id AND ce.code = ?)
    AND EXISTS (SELECT 1 FROM track_evidence me
                 WHERE me.track_id = t.id AND me.code IN ({_placeholders(codes)}))
    """  # noqa: S608
    return sql, (EvidenceCode.TIMESTAMPS_PRESENT.value, *codes)


def _readable_analysis() -> tuple[str, tuple[object, ...]]:
    """Return the SQL that decides whether a stored analysis can be interpreted.

    A projection of the rules the Python rebuild applies -- in exactly the sense
    that ``_EFFECTIVE_KIND`` is a projection of ``TrackClassification``. Every
    vocabulary it compares against is read from the domain enums that own it, so
    the storage layer applies the rule without holding a second copy of what the
    rule *is*: adding a quality flag or a unit changes the enum and this follows.

    It exists because "invalid" has to be filterable and countable in the same
    statement that pages the rows. Deciding it afterwards in Python would put a
    correct verdict behind a wrong ``total``.
    """
    statuses = tuple(status.value for status in AnalysisStatus)
    units = tuple(unit.value for unit in MetricUnit)
    provenances = tuple(item.value for item in MetricProvenance)
    names = tuple(name.value for name in MetricName)
    signed = tuple(name.value for name in MetricName if name.may_be_negative)
    flags = tuple(flag.value for flag in AnalysisQuality)
    # Only bind placeholders are interpolated; every vocabulary value is bound.
    sql = f"""
        cur.status IN ({_placeholders(statuses)})
    AND (cur.status <> ? OR cur.error_code IS NULL)
    AND (cur.status <> ? OR coalesce(cur.error_code, '') <> '')
    AND cur.analyzed_at IS NOT NULL
    AND datetime(cur.analyzed_at) IS NOT NULL
    AND cur.analyzed_at LIKE '%+00:00'
    AND trim(coalesce(cur.distance_algorithm, '')) <> ''
    AND trim(coalesce(cur.movement_algorithm, '')) <> ''
    AND trim(coalesce(cur.elevation_algorithm, '')) <> ''
    AND cur.distance_algorithm_version >= 1
    AND cur.movement_algorithm_version >= 1
    AND cur.elevation_algorithm_version >= 1
    AND cur.metric_schema_version >= 1
    AND NOT EXISTS (
        SELECT 1 FROM track_metrics dm
         WHERE dm.analysis_run_id = cur.id
           AND dm.metric IN ({_placeholders(names)})
           AND (typeof(dm.value) NOT IN ('integer', 'real')
             OR NOT (dm.value > -1e308 AND dm.value < 1e308)
             OR dm.unit NOT IN ({_placeholders(units)})
             OR dm.provenance NOT IN ({_placeholders(provenances)})
             OR (dm.value < 0 AND dm.metric NOT IN ({_placeholders(signed)}))))
    AND NOT EXISTS (
        SELECT 1 FROM analysis_quality_flags qf
         WHERE qf.analysis_run_id = cur.id
           AND qf.flag NOT IN ({_placeholders(flags)}))
    """  # noqa: S608
    values = (
        *statuses,
        AnalysisStatus.SUCCEEDED.value,
        AnalysisStatus.FAILED.value,
        *names,
        *units,
        *provenances,
        *signed,
        *flags,
    )
    return sql, values


def _matches_installed_analysis(profile: AnalysisProfile | None) -> tuple[str, tuple[object, ...]]:
    """Return the SQL that decides whether a stored analysis is the installed one.

    The seven values come from the caller. The storage layer is allowed to
    compare them; it is not allowed to know what they are, because then an
    algorithm change would have to be made twice and the second place is the one
    nobody remembers.

    ``None`` means this build installs no analysis, and nothing can then be
    current -- the same fail-closed answer
    :func:`~trackvault.domain.analysis.profile.is_analysis_profile_current` gives.
    """
    if profile is None:
        return "0 = 1", ()
    sql = """
        cur.status = ?
    AND cur.processing_run_id = t.processing_run_id
    AND cur.distance_algorithm = ? AND cur.distance_algorithm_version = ?
    AND cur.movement_algorithm = ? AND cur.movement_algorithm_version = ?
    AND cur.elevation_algorithm = ? AND cur.elevation_algorithm_version = ?
    AND cur.metric_schema_version = ?
    """
    values: tuple[object, ...] = (
        AnalysisStatus.SUCCEEDED.value,
        profile.distance_algorithm,
        profile.distance_algorithm_version,
        profile.movement_algorithm,
        profile.movement_algorithm_version,
        profile.elevation_algorithm,
        profile.elevation_algorithm_version,
        profile.metric_schema_version,
    )
    return sql, values


def _availability(profile: AnalysisProfile | None) -> tuple[str, tuple[object, ...]]:
    """Return the SQL that classifies one row's stored analysis, and its values.

    ```
    no current run             -> missing
    a run that cannot be read  -> invalid
    a run from other rules     -> outdated
    otherwise                  -> current
    ```

    The same four states, in the same order of precedence, as
    :meth:`trackvault.application.analysis.InstalledAnalysis.availability`. Damage
    is checked before currency deliberately: a run whose profile is *also*
    unreadable is damage rather than an old algorithm, and telling somebody to
    re-run an analysis is a different instruction from telling them a row is
    broken.
    """
    readable, readable_values = _readable_analysis()
    current, current_values = _matches_installed_analysis(profile)
    sql = (
        "CASE WHEN t.current_analysis_run_id IS NULL THEN ?"
        f" WHEN NOT ({readable}) THEN ?"
        f" WHEN {current} THEN ? ELSE ? END"
    )
    values = (
        AnalysisAvailability.MISSING.value,
        *readable_values,
        AnalysisAvailability.INVALID.value,
        *current_values,
        AnalysisAvailability.CURRENT.value,
        AnalysisAvailability.OUTDATED.value,
    )
    return sql, values


def _summary_statement(profile: AnalysisProfile | None) -> tuple[str, tuple[object, ...]]:
    """Return the statement that reads track summaries, and the values it binds."""
    availability, values = _availability(profile)
    return (
        f"{_SUMMARY_COLUMNS}, {availability} AS analysis_availability{_CURRENT_GENERATION}",
        values,
    )


def _count_statement(query: TrackQuery) -> tuple[str, tuple[object, ...]]:
    """Return the statement counting what a listing's filter selected."""
    restriction, values = _listing_restriction(query)
    return f"SELECT count(*){_CURRENT_GENERATION} WHERE 1 = 1{restriction}", values


def _page_statement(query: TrackQuery) -> tuple[str, tuple[object, ...]]:
    """Return the statement that reads one page of a listing, and its values.

    Assembled rather than concatenated from constants because the availability
    projection carries bound values, and those have to arrive in the order the
    fragments do. Only this module's own fragments reach the statement text.
    """
    text, values = _summary_statement(query.installed_analysis)
    bound = list(values)
    if query.order is TrackOrder.LONGEST_FIRST:
        # The distance is joined only where the analysis is current, so the
        # ordering compares lengths this build would derive today. A stale
        # 20 km is what the track measured under algorithms that have been
        # corrected since, and sorting by it answers a question nobody asked.
        readable, readable_values = _readable_analysis()
        matches, matches_values = _matches_installed_analysis(query.installed_analysis)
        text += f"{_DISTANCE_JOIN}   AND ({readable})\n   AND ({matches})\n"
        bound += [MetricName.DISTANCE.value, *readable_values, *matches_values]
    restriction, restriction_values = _listing_restriction(query)
    bound += restriction_values
    text += " WHERE 1 = 1" + restriction + _LISTING_ORDER[query.order] + " LIMIT ? OFFSET ?"
    return text, tuple(bound)


# Every ordering ends in the track identity. Two rows that compare equal on the
# sort key would otherwise come back in an arbitrary order, and a row that moves
# between two pages is a row the reader sees twice or not at all.
#
# `started_at IS NULL` first in the chronological orderings sorts the undated
# tracks last in *both* directions. Letting NULL fall where SQLite puts it would
# make a planned route look like the oldest thing in the archive.
_LISTING_ORDER = {
    TrackOrder.IMPORTED_NEWEST_FIRST: (
        " ORDER BY i.received_at DESC, t.source_index ASC, t.id ASC"
    ),
    TrackOrder.ACTIVITY_NEWEST_FIRST: (
        " ORDER BY t.started_at IS NULL, t.started_at DESC, t.id ASC"
    ),
    TrackOrder.ACTIVITY_OLDEST_FIRST: (
        " ORDER BY t.started_at IS NULL, t.started_at ASC, t.id ASC"
    ),
    TrackOrder.LONGEST_FIRST: (" ORDER BY m.value IS NULL, m.value DESC, t.id ASC"),
}


def _listing_restriction(query: TrackQuery) -> tuple[str, tuple[object, ...]]:
    """Return the SQL restriction one listing query asks for, and its values.

    Only this module's own fragments reach the statement text. Every value the
    caller supplied is bound.

    The analysis-status filter belongs here rather than to a pass over the page,
    so that the count and the rows are restricted by one statement. Filtering
    afterwards would page first and hide second, which is a page of the wrong
    size under a total of the wrong number.
    """
    clauses: list[str] = []
    values: list[object] = []
    if query.effective_kind is not None:
        clauses.append(f" AND {_EFFECTIVE_KIND} = ?")
        values.append(query.effective_kind.value)
    if query.activity is not None:
        clauses.append(" AND t.activity = ?")
        values.append(query.activity.value)
    if query.started_at_or_after is not None:
        clauses.append(" AND t.started_at >= ?")
        values.append(_as_text(query.started_at_or_after))
    if query.started_before is not None:
        clauses.append(" AND t.started_at < ?")
        values.append(_as_text(query.started_before))
    if query.analysis_status is not None:
        availability, availability_values = _availability(query.installed_analysis)
        clauses.append(f" AND ({availability}) = ?")
        values.extend(availability_values)
        values.append(query.analysis_status.value)
    if query.calendar_anchored:
        anchored, anchor_values = _calendar_anchored()
        clauses.append(f" AND ({anchored})")
        values.extend(anchor_values)
    return "".join(clauses), tuple(values)


_PARAMETER_BATCH = 500
"""How many run identities go into one ``IN`` clause.

SQLite bounds the number of bound parameters per statement, and an archive with
a very busy year would otherwise walk into that limit at the worst moment.
"""


def _metrics_for_runs(
    connection: sqlite3.Connection, run_ids: Sequence[int]
) -> tuple[dict[int, dict[MetricName, MetricValue]], set[int]]:
    """Return the metrics of many analysis runs, in batched queries.

    One query per track would make a yearly total a few hundred round trips
    through the driver for data that fits in one result set.

    A run holding a value it could not have produced is left out entirely, on
    the same reasoning as the single-run reader, and named in the second return
    value so that a caller can tell "this run is damaged" from "this run has not
    been written yet".
    """
    metrics: dict[int, dict[MetricName, MetricValue]] = {}
    unreadable: set[int] = set()
    for start in range(0, len(run_ids), _PARAMETER_BATCH):
        batch = run_ids[start : start + _PARAMETER_BATCH]
        placeholders = ",".join("?" * len(batch))
        rows = connection.execute(
            "SELECT analysis_run_id, metric, value, unit, provenance FROM track_metrics "  # noqa: S608
            f"WHERE analysis_run_id IN ({placeholders})",
            tuple(batch),
        ).fetchall()
        for row in rows:
            run_id = int(row["analysis_run_id"])
            try:
                name = MetricName(row["metric"])
            except ValueError:
                continue
            try:
                metrics.setdefault(run_id, {})[name] = _metric_value(name, row)
            except (ValueError, TypeError):
                logger.warning("analysis.unreadable_metric run=%d", run_id)
                unreadable.add(run_id)
    for run_id in unreadable:
        metrics.pop(run_id, None)
    return metrics, unreadable


def _runs_with_unreadable_quality(
    connection: sqlite3.Connection, run_ids: Sequence[int]
) -> set[int]:
    """Return the runs carrying a quality flag this build cannot name."""
    unreadable: set[int] = set()
    known = {flag.value for flag in AnalysisQuality}
    for start in range(0, len(run_ids), _PARAMETER_BATCH):
        batch = run_ids[start : start + _PARAMETER_BATCH]
        placeholders = ",".join("?" * len(batch))
        rows = connection.execute(
            "SELECT analysis_run_id, flag FROM analysis_quality_flags "  # noqa: S608
            f"WHERE analysis_run_id IN ({placeholders})",
            tuple(batch),
        ).fetchall()
        unreadable.update(
            int(row["analysis_run_id"]) for row in rows if str(row["flag"]) not in known
        )
    return unreadable


def _unreadable_runs(connection: sqlite3.Connection, run_ids: Sequence[int]) -> set[int]:
    """Return the runs whose stored output this build cannot interpret.

    Currency has to account for this, not just the presentation. A run whose
    metrics are unreadable is one ``analyze --outdated`` must pick up, and it
    only does that if the authority it asks already knows the run is unusable --
    otherwise the archive reports damage it never offers to repair.

    Two batched queries whatever the number of runs, so a diagnostics view over
    a large archive stays a constant number of round trips.
    """
    if not run_ids:
        return set()
    _, unreadable = _metrics_for_runs(connection, run_ids)
    return unreadable | _runs_with_unreadable_quality(connection, run_ids)


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


def _extent_of(
    segments: Sequence[TrackSegment],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return the rectangle a track occupies, or four nulls for no positions."""
    positions = [
        (point.longitude, point.latitude) for segment in segments for point in segment.points
    ]
    if not positions:
        return (None, None, None, None)
    longitudes = [longitude for longitude, _ in positions]
    latitudes = [latitude for _, latitude in positions]
    return (min(longitudes), min(latitudes), max(longitudes), max(latitudes))


def _same_recording(
    connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
) -> dict[int, tuple[int, ...]]:
    """Return, per track on this page, the other tracks that are the same recording.

    One batched query for the page, like every other side table here. A track
    whose fingerprint is not known -- written by a build before the column
    existed and never persisted since -- reports nothing, which says "not
    known" rather than "unique". Claiming the second from the first is how a
    missing value becomes a false statement.
    """
    fingerprints = {row["geometry_sha256"] for row in rows if row["geometry_sha256"]}
    if not fingerprints:
        return {}
    placeholders = ",".join("?" for _ in fingerprints)
    found = connection.execute(
        # Current generations only, by the same join every other read uses: a
        # superseded track is not a second import of anything, it is an older
        # normalization of one.
        f"SELECT t.id AS id, t.geometry_sha256 AS geometry_sha256 FROM tracks t "  # noqa: S608 - placeholders only
        "JOIN raw_imports i ON i.sha256 = t.raw_import_sha256 "
        "AND i.active_processing_run_id = t.processing_run_id "
        f"WHERE t.geometry_sha256 IN ({placeholders}) ORDER BY t.id",
        tuple(fingerprints),
    ).fetchall()
    by_fingerprint: dict[str, list[int]] = {}
    for row in found:
        by_fingerprint.setdefault(str(row["geometry_sha256"]), []).append(int(row["id"]))
    return {
        int(row["id"]): tuple(
            other
            for other in by_fingerprint.get(str(row["geometry_sha256"]), ())
            if other != int(row["id"])
        )
        for row in rows
        if row["geometry_sha256"]
    }


def _summary_from(
    row: sqlite3.Row,
    evidence: dict[int, tuple[str, ...]],
    links: dict[int, tuple[str, ...]],
    namespaces: dict[int, tuple[str, ...]],
    metrics: dict[int, dict[MetricName, MetricValue]],
    siblings: dict[int, tuple[int, ...]],
) -> TrackSummary:
    """Rebuild a track summary from its row and its side tables.

    Metrics reach the summary only while the analysis they came from is current.
    A stale or damaged value is not what the track *is*, and a listing row reads
    as a statement about the track rather than about its analysis history.
    """
    track_id = int(row["id"])
    override = row["override_kind"]
    analysis_run_id = row["current_analysis_run_id"]
    availability = AnalysisAvailability(row["analysis_availability"])
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
        analysis=availability,
        metrics=(
            metrics.get(int(analysis_run_id), {})
            if availability is AnalysisAvailability.CURRENT and analysis_run_id is not None
            else {}
        ),
        user_metadata=UserTrackMetadata(title=row["user_title"], note=row["user_note"]),
        same_recording_ids=siblings.get(track_id, ()),
        bounds=_bounds_of_row(row),
        geometry_sha256=None if row["geometry_sha256"] is None else str(row["geometry_sha256"]),
    )


def _bounds_of_row(row: sqlite3.Row) -> MapBounds | None:
    """Return the rectangle a stored track occupies, or ``None`` if it has none."""
    values = [
        row["min_longitude"],
        row["min_latitude"],
        row["max_longitude"],
        row["max_latitude"],
    ]
    if any(value is None for value in values):
        return None
    west, south, east, north = (float(value) for value in values)
    try:
        return MapBounds(
            min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north
        )
    except ValueError:  # pragma: no cover - stored positions are range-checked
        return None


__all__ = ["SCHEMA_VERSION", "SqliteTrackStore"]
