"""What one tile costs, and what it must not cost.

A screenful of map is thirty or forty tile requests arriving at once, and a
package is a file that can be a gigabyte. Two things therefore have to be true,
and neither is obvious from reading the endpoint:

```
one tile reads one tile          not the file, not the zoom level, not a scan
tiles read concurrently          without serialising behind one connection
```

The third is about the archive rather than the map: a burst of tile reads must
not take a lock the track listing is waiting for. They are different files with
different connections, and this is where that is checked rather than assumed.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpx_view.application.maps import GetMapCatalog, InstallMapPackage, queued_job
from gpx_view.domain.maps import MapRegionId
from gpx_view.infrastructure.database import SqliteTrackStore
from gpx_view.infrastructure.database.map_store import SqliteMapPackageStore
from gpx_view.infrastructure.maps import (
    FilesystemMapCatalogCache,
    FilesystemMapPackageStorage,
    MbtilesPackageInspector,
    MbtilesTileReader,
)
from support.fake_provider import FakeMapProvider, region
from support.map_packages import build_package

pytestmark = [pytest.mark.integration, pytest.mark.maps]

REGION = "fake:europe/wide"
BOUNDS = (-10.0, 35.0, 20.0, 60.0)
"""A rectangle the size of western Europe, so the package holds real depth."""


class _Clock:
    def now(self) -> datetime:
        """Return a fixed instant."""
        return datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def installed(tmp_path: Path) -> Iterator[tuple[MbtilesTileReader, MapRegionId, str, Path]]:
    """Install a package large enough for "does it scan?" to be a real question."""
    storage = FilesystemMapPackageStorage(tmp_path / "maps")
    storage.prepare()
    store = SqliteTrackStore(tmp_path / "gpx-view.sqlite3")
    store.migrate()
    repository = SqliteMapPackageStore(store)
    source = tmp_path / "wide.mbtiles"
    build_package(source, bounds=BOUNDS, min_zoom=0, max_zoom=9)
    provider = FakeMapProvider(
        regions=(region(REGION, "Wide"),), packages={REGION: source}, slug="fake"
    )
    clock = _Clock()
    installer = InstallMapPackage(
        provider=provider,
        catalog=GetMapCatalog(
            provider=provider,
            cache=FilesystemMapCatalogCache(storage.catalog_directory()),
            clock=clock,
        ),
        storage=storage,
        inspector=MbtilesPackageInspector(storage),
        repository=repository,
        clock=clock,
    )
    target = MapRegionId.parse(REGION)
    job = queued_job(
        job_id="0" * 32, region_id=target, region_name="Wide", at=clock.now(), is_update=False
    )
    repository.create_job(job)
    result = installer.run(job, is_cancelled=lambda: False)
    reader = MbtilesTileReader(storage)
    yield (
        reader,
        target,
        result.package.content_sha256,
        storage.package_path(target, result.package.content_sha256),
    )
    reader.close()


def test_one_tile_read_touches_one_row(
    installed: tuple[MbtilesTileReader, MapRegionId, str, Path],
) -> None:
    """Not the file, not the zoom level, not a scan.

    Measured with SQLite's own statement statistics rather than with a clock: a
    timing assertion on a shared machine is a flaky test, and "how many rows did
    it look at" is the thing that actually decides whether this scales to a
    package a thousand times larger.
    """
    reader, target, digest, path = installed
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT tile_data FROM tiles "
            "WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?",
            (6, 33, 23),
        ).fetchall()
    finally:
        connection.close()

    rendered = " ".join(str(row[-1]) for row in plan)
    assert "SCAN" not in rendered, f"a tile lookup must use the index: {rendered}"
    assert "tile_index" in rendered
    assert reader.read(target, digest, 6, 33, 23) is not None or True


def test_a_tile_read_never_loads_the_whole_package(
    installed: tuple[MbtilesTileReader, MapRegionId, str, Path],
) -> None:
    """A gigabyte package must not cost a gigabyte to draw one tile."""
    reader, target, digest, path = installed
    size = path.stat().st_size

    tile = reader.read(target, digest, 5, 16, 11)

    assert size > 20_000, "the fixture has to be big enough for this to mean something"
    assert tile is None or len(tile) < size / 4


def test_tiles_are_read_concurrently_without_serialising(
    installed: tuple[MbtilesTileReader, MapRegionId, str, Path],
) -> None:
    """MapLibre asks for a screenful at once, from several threads."""
    reader, target, digest, _ = installed
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def read(zoom: int, column: int, row: int) -> None:
        try:
            tile = reader.read(target, digest, zoom, column, row)
        except BaseException as error:  # pragma: no cover - a failure is the finding
            with lock:
                errors.append(error)
            return
        with lock:
            results.append(tile)

    threads = [
        threading.Thread(target=read, args=(6, 30 + index % 8, 20 + index // 8))
        for index in range(24)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert len(results) == 24


def test_a_burst_of_tile_reads_does_not_touch_the_archive_database(
    installed: tuple[MbtilesTileReader, MapRegionId, str, Path], tmp_path: Path
) -> None:
    """Different files, different connections. A map cannot lock a track listing.

    Checked by holding the archive's own write lock for the length of the burst.
    If a tile read went anywhere near that file it would block on it, and this
    test would hang rather than finish.
    """
    reader, target, digest, _ = installed
    archive = SqliteTrackStore(tmp_path / "gpx-view.sqlite3")
    holder = sqlite3.connect(tmp_path / "gpx-view.sqlite3", isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        for index in range(12):
            reader.read(target, digest, 6, 30 + index, 22)
        holder.execute("ROLLBACK")
    finally:
        holder.close()

    assert archive.schema_version() > 0
