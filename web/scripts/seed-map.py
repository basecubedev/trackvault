#!/usr/bin/env python
"""Install a synthetic offline map into the archive the browser tests drive.

The browser suite has to prove that a track page renders a real basemap with
real labels and makes no external request while doing it. Proving that needs an
*installed package*, so this runs the actual installation pipeline -- storage,
inspector, repository, atomic publish -- against a package generated on the
spot and a provider that reads it off disk.

No map is downloaded and nothing is committed: the package is a few kilobytes
of synthetic geometry covering the area the seeded tracks are in.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tests"))

from gpx_view.application.maps import (  # noqa: E402
    GetMapCatalog,
    InstallMapPackage,
    queued_job,
)
from gpx_view.config import Settings  # noqa: E402
from gpx_view.domain.maps import MapRegionId  # noqa: E402
from gpx_view.infrastructure.database import SqliteTrackStore  # noqa: E402
from gpx_view.infrastructure.database.map_store import SqliteMapPackageStore  # noqa: E402
from gpx_view.infrastructure.maps import (  # noqa: E402
    FilesystemMapCatalogCache,
    FilesystemMapPackageStorage,
    MbtilesPackageInspector,
)
from support.fake_provider import FakeMapProvider, region  # noqa: E402
from support.map_packages import build_package  # noqa: E402

REGION = "fixture:mallorca"
# The seeded tracks sit around 39.8 N, 3.1 E. The package covers a rectangle
# comfortably around them, so a viewport with a little padding is still inside.
BOUNDS = (2.60, 39.30, 3.60, 40.20)


class _Clock:
    """A clock that does not move, so a seeded archive is reproducible."""

    def now(self) -> datetime:
        """Return a fixed instant."""
        return datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def main() -> int:
    """Install the fixture package into the configured data directory."""
    settings = Settings()
    store = SqliteTrackStore(settings.database_path)
    store.migrate()
    storage = FilesystemMapPackageStorage(settings.map_storage_dir)
    storage.prepare()
    clock = _Clock()

    with TemporaryDirectory() as scratch:
        source = Path(scratch) / "mallorca.mbtiles"
        build_package(source, bounds=BOUNDS, min_zoom=0, max_zoom=12, place_name="Fixtureberg")
        provider = FakeMapProvider(
            regions=(region(REGION, "Mallorca (test fixture)"),),
            packages={REGION: source},
            slug="fixture",
            display_name="GPX-View test fixtures",
        )
        catalog = GetMapCatalog(
            provider=provider,
            cache=FilesystemMapCatalogCache(storage.catalog_directory()),
            clock=clock,
        )
        repository = SqliteMapPackageStore(store)
        installer = InstallMapPackage(
            provider=provider,
            catalog=catalog,
            storage=storage,
            inspector=MbtilesPackageInspector(storage),
            repository=repository,
            clock=clock,
        )
        target = MapRegionId.parse(REGION)
        job = queued_job(
            job_id="0" * 32,
            region_id=target,
            region_name="Mallorca (test fixture)",
            at=clock.now(),
            is_update=False,
        )
        repository.create_job(job)
        result = installer.run(job, is_cancelled=lambda: False)

    sys.stdout.write(f"installed {result.package.region_name} ({result.outcome.value})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
