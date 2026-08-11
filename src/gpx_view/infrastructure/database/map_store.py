"""The SQLite implementation of the map package repository.

It shares the archive's database file, and that is a deliberate choice rather
than a convenience: an installed package and the tracks it draws behind belong
to one deployment, one backup and one schema version. What it does **not** share
is the packages themselves -- those are files of their own, read through their
own read-only connections, so a screenful of tile reads cannot take a lock the
track listing is waiting for.

Everything read out of here is validated on the way out. The row is the
authority for what was installed, which is exactly why what comes back is
untrusted input: it may have been written by a build that is gone.
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from gpx_view.application.maps import MapInstallJob
from gpx_view.domain.maps import (
    AttributionLink,
    MapAttribution,
    MapBounds,
    MapJobState,
    MapPackage,
    MapPackageFormat,
    MapRegionId,
    MapTileSchema,
)
from gpx_view.infrastructure.database.store import SqliteTrackStore

logger = logging.getLogger(__name__)

_ACTIVE_STATES = tuple(state.value for state in MapJobState if not state.is_terminal)

_ACTIVE_PLACEHOLDERS = ", ".join("?" for _ in _ACTIVE_STATES)
"""Bind markers for the unfinished states, built once from the enum.

Interpolated into three statements below. It is a run of question marks derived
from a module-level tuple -- no value from a caller, a row or a provider can
reach it -- and the states themselves are still bound as parameters.
"""


class SqliteMapPackageStore:
    """Installed map packages and their installation jobs, in the archive's database."""

    def __init__(self, store: SqliteTrackStore) -> None:
        """Share the archive's store, so there is one database and one migration."""
        self._store = store

    # --- packages -------------------------------------------------------

    def installed_packages(self) -> tuple[MapPackage, ...]:
        """Return every installed package, skipping any row that cannot be read."""
        with self._store.connection() as connection:
            rows = connection.execute("SELECT * FROM map_packages").fetchall()
            links = _links_by_region(connection)
        packages = []
        for row in rows:
            package = _package_of(row, links.get(row["region_id"], ()))
            if package is None:
                # A row this build cannot interpret is not a package. It is not
                # deleted either -- a newer build may understand it, and a
                # migration is a better place to decide than a read.
                logger.warning("map_package.unreadable_row")
                continue
            packages.append(package)
        return tuple(packages)

    def installed_package(self, region_id: MapRegionId) -> MapPackage | None:
        """Return one region's installed package, if there is one."""
        with self._store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM map_packages WHERE region_id = ?", (str(region_id),)
            ).fetchone()
            if row is None:
                return None
            links = _links_of(connection, row["region_id"])
        return _package_of(row, links)

    def package_by_delivery_id(self, delivery_id: str) -> MapPackage | None:
        """Return the package a delivery identity names, if any."""
        with self._store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM map_packages WHERE content_sha256 = ?", (delivery_id,)
            ).fetchone()
            if row is None:
                return None
            links = _links_of(connection, row["region_id"])
        return _package_of(row, links)

    def save_package(self, package: MapPackage) -> None:
        """Publish a package's metadata, replacing any earlier one atomically.

        One transaction, so no reader ever sees the new hash beside the old
        attribution. That combination would render a map with somebody else's
        credit under it, which is the one failure this whole capability is
        arranged to make impossible.
        """
        region = str(package.region_id)
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM map_package_attribution_links WHERE region_id = ?", (region,)
            )
            connection.execute("DELETE FROM map_packages WHERE region_id = ?", (region,))
            connection.execute(
                """
                INSERT INTO map_packages (
                    region_id, region_name, provider, format, tile_schema,
                    tile_schema_version, content_sha256, size_bytes,
                    min_longitude, min_latitude, max_longitude, max_latitude,
                    min_zoom, max_zoom, attribution_data_owner,
                    attribution_provider, license_identifier, license_name,
                    attribution_text, dataset_version, dataset_timestamp,
                    downloaded_at, source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    region,
                    package.region_name,
                    package.provider,
                    package.format.value,
                    package.tile_schema.name,
                    package.tile_schema.version,
                    package.content_sha256,
                    package.size_bytes,
                    package.bounds.min_longitude,
                    package.bounds.min_latitude,
                    package.bounds.max_longitude,
                    package.bounds.max_latitude,
                    package.min_zoom,
                    package.max_zoom,
                    package.attribution.data_owner,
                    package.attribution.provider,
                    package.attribution.license_identifier,
                    package.attribution.license_name,
                    package.attribution.required_text,
                    package.dataset_version,
                    _as_text(package.dataset_timestamp),
                    _as_text(package.downloaded_at),
                    package.source_url,
                ),
            )
            connection.executemany(
                "INSERT INTO map_package_attribution_links (region_id, position, label, url) "
                "VALUES (?, ?, ?, ?)",
                [
                    (region, position, link.label, link.url)
                    for position, link in enumerate(package.attribution.links)
                ],
            )

    def delete_package(self, region_id: MapRegionId) -> None:
        """Remove a package's metadata."""
        region = str(region_id)
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM map_package_attribution_links WHERE region_id = ?", (region,)
            )
            connection.execute("DELETE FROM map_packages WHERE region_id = ?", (region,))

    # --- jobs -----------------------------------------------------------

    def create_job(self, job: MapInstallJob) -> None:
        """Record a queued job."""
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO map_install_jobs (
                    job_id, region_id, region_name, state, bytes_downloaded,
                    bytes_total, error_code, is_update, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _job_row(job),
            )

    def update_job(self, job: MapInstallJob) -> None:
        """Record a job's new state."""
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE map_install_jobs
                   SET state = ?, bytes_downloaded = ?, bytes_total = ?,
                       error_code = ?, updated_at = ?
                 WHERE job_id = ?
                """,
                (
                    job.state.value,
                    job.bytes_downloaded,
                    job.bytes_total,
                    job.error_code,
                    _as_text(job.updated_at),
                    job.job_id,
                ),
            )

    def job(self, job_id: str) -> MapInstallJob | None:
        """Return one job."""
        with self._store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM map_install_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return None if row is None else _job_of(row)

    def active_job_for(self, region_id: MapRegionId) -> MapInstallJob | None:
        """Return the unfinished job for a region, if one exists."""
        with self._store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM map_install_jobs WHERE region_id = ? "  # noqa: S608
                f"AND state IN ({_ACTIVE_PLACEHOLDERS}) ORDER BY started_at DESC LIMIT 1",
                (str(region_id), *_ACTIVE_STATES),
            ).fetchone()
        return None if row is None else _job_of(row)

    def active_jobs(self) -> tuple[MapInstallJob, ...]:
        """Return every unfinished job."""
        with self._store.connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM map_install_jobs WHERE state IN ({_ACTIVE_PLACEHOLDERS}) "  # noqa: S608
                "ORDER BY started_at",
                _ACTIVE_STATES,
            ).fetchall()
        return tuple(job for row in rows if (job := _job_of(row)) is not None)

    def recent_jobs(self, limit: int = 20) -> tuple[MapInstallJob, ...]:
        """Return the newest jobs, finished or not, so a failure stays visible."""
        with self._store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM map_install_jobs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(job for row in rows if (job := _job_of(row)) is not None)

    def fail_job(self, job_id: str, error_code: str, at: datetime) -> None:
        """Record that a job failed, with the reason a reader can act on."""
        with self._transaction() as connection:
            connection.execute(
                "UPDATE map_install_jobs SET state = ?, error_code = ?, updated_at = ? "
                "WHERE job_id = ?",
                (MapJobState.FAILED.value, error_code, _as_text(at), job_id),
            )

    def cancel_job(self, job_id: str, at: datetime) -> None:
        """Record that a job was cancelled by its owner."""
        with self._transaction() as connection:
            connection.execute(
                "UPDATE map_install_jobs SET state = ?, updated_at = ? WHERE job_id = ?",
                (MapJobState.CANCELLED.value, _as_text(at), job_id),
            )

    def interrupt_unfinished_jobs(self, at: datetime) -> int:
        """Mark every unfinished job interrupted. Returns how many."""
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE map_install_jobs SET state = ?, updated_at = ? "  # noqa: S608
                f"WHERE state IN ({_ACTIVE_PLACEHOLDERS})",
                (MapJobState.INTERRUPTED.value, _as_text(at), *_ACTIVE_STATES),
            )
            return int(cursor.rowcount or 0)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside an explicit write transaction."""
        with self._store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")


def _as_text(moment: datetime | None) -> str | None:
    """Return an instant as an unambiguous UTC string, or ``None``."""
    return None if moment is None else moment.astimezone(UTC).isoformat()


def _as_instant(text: object) -> datetime | None:
    """Return a stored string as an instant, or ``None`` when unreadable."""
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _links_of(connection: sqlite3.Connection, region: str) -> tuple[AttributionLink, ...]:
    """Return one package's attribution links, in the order they were stored."""
    rows = connection.execute(
        "SELECT label, url FROM map_package_attribution_links "
        "WHERE region_id = ? ORDER BY position",
        (region,),
    ).fetchall()
    return _links_from(rows)


def _links_by_region(connection: sqlite3.Connection) -> dict[str, tuple[AttributionLink, ...]]:
    """Return every package's attribution links in one query."""
    rows = connection.execute(
        "SELECT region_id, label, url FROM map_package_attribution_links "
        "ORDER BY region_id, position"
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["region_id"]), []).append(row)
    return {region: _links_from(entries) for region, entries in grouped.items()}


def _links_from(rows: list[sqlite3.Row]) -> tuple[AttributionLink, ...]:
    """Return the links a set of rows holds, dropping any that no longer validate."""
    links = []
    for row in rows:
        try:
            links.append(AttributionLink(label=str(row["label"]), url=str(row["url"])))
        except ValueError:
            logger.warning("map_package.unusable_attribution_link")
    return tuple(links)


def _package_of(row: sqlite3.Row, links: tuple[AttributionLink, ...]) -> MapPackage | None:
    """Return the package a row describes, or ``None`` when it cannot be read."""
    try:
        return MapPackage(
            region_id=MapRegionId.parse(str(row["region_id"])),
            region_name=str(row["region_name"]),
            provider=str(row["provider"]),
            format=MapPackageFormat(str(row["format"])),
            tile_schema=MapTileSchema(
                name=str(row["tile_schema"]), version=str(row["tile_schema_version"])
            ),
            content_sha256=str(row["content_sha256"]),
            size_bytes=int(row["size_bytes"]),
            bounds=MapBounds(
                min_longitude=float(row["min_longitude"]),
                min_latitude=float(row["min_latitude"]),
                max_longitude=float(row["max_longitude"]),
                max_latitude=float(row["max_latitude"]),
            ),
            min_zoom=int(row["min_zoom"]),
            max_zoom=int(row["max_zoom"]),
            attribution=MapAttribution(
                data_owner=str(row["attribution_data_owner"]),
                provider=str(row["attribution_provider"]),
                license_identifier=str(row["license_identifier"]),
                license_name=str(row["license_name"]),
                required_text=str(row["attribution_text"]),
                links=links,
            ),
            downloaded_at=_require_instant(row["downloaded_at"]),
            source_url=str(row["source_url"]),
            dataset_version=None if row["dataset_version"] is None else str(row["dataset_version"]),
            dataset_timestamp=_as_instant(row["dataset_timestamp"]),
        )
    except (ValueError, TypeError):
        return None


def _require_instant(value: object) -> datetime:
    """Return a stored instant, refusing a row that has none."""
    parsed = _as_instant(value)
    if parsed is None:
        raise ValueError("a stored package states when it was downloaded")
    return parsed


def _job_row(job: MapInstallJob) -> tuple[object, ...]:
    """Return the column values one job is stored as."""
    return (
        job.job_id,
        str(job.region_id),
        job.region_name,
        job.state.value,
        job.bytes_downloaded,
        job.bytes_total,
        job.error_code,
        1 if job.is_update else 0,
        _as_text(job.started_at),
        _as_text(job.updated_at),
    )


def _job_of(row: sqlite3.Row) -> MapInstallJob | None:
    """Return the job a row describes, or ``None`` when it cannot be read."""
    try:
        started = _require_instant(row["started_at"])
        return MapInstallJob(
            job_id=str(row["job_id"]),
            region_id=MapRegionId.parse(str(row["region_id"])),
            region_name=str(row["region_name"]),
            state=MapJobState(str(row["state"])),
            bytes_downloaded=int(row["bytes_downloaded"]),
            bytes_total=None if row["bytes_total"] is None else int(row["bytes_total"]),
            started_at=started,
            updated_at=_as_instant(row["updated_at"]) or started,
            error_code=None if row["error_code"] is None else str(row["error_code"]),
            is_update=bool(row["is_update"]),
        )
    except (ValueError, TypeError):
        logger.warning("map_job.unreadable_row")
        return None
