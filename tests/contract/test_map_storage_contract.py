"""What an installed map package is, and what an installation may not damage.

The contracts here are the ones an operator finds out about at the worst moment:
after a failed update, after a power cut, after a disk filled up. Each of them
is written as "what is still true afterwards".
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpx_view.application.maps import (
    GetMapCatalog,
    InstallMapPackage,
    InstallOutcome,
    ListInstalledMaps,
    MapErrorCode,
    MapOperationError,
    RecoverMapStorage,
    RemoveMapPackage,
    queued_job,
)
from gpx_view.domain.maps import MapInstallState, MapJobState, MapRegionId
from gpx_view.infrastructure.database import SqliteTrackStore
from gpx_view.infrastructure.database.map_store import SqliteMapPackageStore
from gpx_view.infrastructure.maps import (
    FilesystemMapCatalogCache,
    FilesystemMapPackageStorage,
    MbtilesPackageInspector,
    MbtilesTileReader,
)
from support.fake_provider import FakeMapProvider, region
from support.map_packages import build_package, layer_names

pytestmark = [pytest.mark.contract, pytest.mark.maps, pytest.mark.persistence]

MONACO = "fake:europe/monaco"
ANDORRA = "fake:europe/andorra"
MONACO_BOUNDS = (7.40, 43.48, 7.60, 43.76)
ANDORRA_BOUNDS = (1.40, 42.42, 1.79, 42.66)


class FixedClock:
    """A clock that does not move, so a stored instant is a fact not a race."""

    def __init__(self) -> None:
        """Start at a fixed instant."""
        self.instant = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the current instant."""
        return self.instant


class Archive:
    """Everything one test needs: storage, repository, installer, packages."""

    def __init__(self, root: Path) -> None:
        """Build a complete, isolated map installation under ``root``."""
        self.root = root
        self.clock = FixedClock()
        self.storage = FilesystemMapPackageStorage(root / "maps")
        self.storage.prepare()
        self.store = SqliteTrackStore(root / "gpx-view.sqlite3")
        self.store.migrate()
        self.repository = SqliteMapPackageStore(self.store)
        self.sources = root / "remote"
        self.sources.mkdir()
        self.provider = FakeMapProvider(
            regions=(
                region("fake:europe", "Europe"),
                region(MONACO, "Monaco", "fake:europe"),
                region(ANDORRA, "Andorra", "fake:europe"),
            ),
            packages={},
        )
        self.catalog = GetMapCatalog(
            provider=self.provider,
            cache=FilesystemMapCatalogCache(self.storage.catalog_directory()),
            clock=self.clock,
        )
        self.installer = InstallMapPackage(
            provider=self.provider,
            catalog=self.catalog,
            storage=self.storage,
            inspector=MbtilesPackageInspector(self.storage),
            repository=self.repository,
            clock=self.clock,
        )
        self.installed = ListInstalledMaps(self.repository, self.storage)
        self.tiles = MbtilesTileReader(self.storage)
        self._jobs = 0

    def publish(self, identity: str, **options: object) -> Path:
        """Make a package available from the fake provider."""
        path = self.sources / f"{identity.replace(':', '_').replace('/', '_')}.mbtiles"
        bounds = MONACO_BOUNDS if identity == MONACO else ANDORRA_BOUNDS
        build_package(path, bounds=options.pop("bounds", bounds), **options)  # type: ignore[arg-type]
        self.provider.publish(identity, path)
        return path

    def install(self, identity: str) -> object:
        """Run one installation to completion on the calling thread."""
        target = MapRegionId.parse(identity)
        self._jobs += 1
        job = queued_job(
            job_id=f"{self._jobs:032x}",
            region_id=target,
            region_name=target.path,
            at=self.clock.now(),
            is_update=False,
        )
        self.repository.create_job(job)
        return self.installer.run(job, is_cancelled=lambda: False)


@pytest.fixture
def archive(tmp_path: Path) -> Iterator[Archive]:
    """Yield an isolated map installation."""
    built = Archive(tmp_path)
    yield built
    built.tiles.close()


# --- A package is a row and a file ------------------------------------------


def test_installing_produces_both_a_row_and_a_verified_file(archive: Archive) -> None:
    """Either half alone is not an installation."""
    archive.publish(MONACO)

    result = archive.install(MONACO)

    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None
    assert result.outcome is InstallOutcome.INSTALLED  # type: ignore[attr-defined]
    assert archive.storage.is_intact(package.region_id, package.content_sha256)
    assert archive.installed.all()[0].state is MapInstallState.INSTALLED


def test_a_row_whose_file_is_gone_reports_invalid_rather_than_installed(
    archive: Archive,
) -> None:
    """“Installed" would keep offering a map that answers every tile with nothing."""
    archive.publish(MONACO)
    archive.install(MONACO)
    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None
    archive.storage.remove(package.region_id, package.content_sha256)

    entry = archive.installed.all()[0]

    assert entry.state is MapInstallState.INVALID
    assert archive.installed.usable() == ()


def test_a_corrupt_file_does_not_hash_to_its_name(archive: Archive) -> None:
    """The integrity check is the content, never the file name."""
    archive.publish(MONACO)
    archive.install(MONACO)
    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None
    path = archive.storage.package_path(package.region_id, package.content_sha256)
    path.write_bytes(b"SQLite format 3\x00" + b"rubbish" * 100)

    assert not archive.storage.is_intact(package.region_id, package.content_sha256)


def test_every_installed_package_is_content_hashed(archive: Archive) -> None:
    """The hash is the delivery identity as well as the integrity proof."""
    archive.publish(MONACO)
    archive.install(MONACO)

    package = archive.repository.installed_package(MapRegionId.parse(MONACO))

    assert package is not None
    assert len(package.content_sha256) == 64
    assert package.delivery_id == package.content_sha256


# --- Managed files are private and inside their root ------------------------


def test_managed_map_files_are_created_privately(archive: Archive) -> None:
    """The same rule the rest of the data directory follows."""
    archive.publish(MONACO)
    archive.install(MONACO)
    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None
    path = archive.storage.package_path(package.region_id, package.content_sha256)

    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert (archive.root / "maps").stat().st_mode & 0o777 == 0o700


def test_a_package_lands_under_a_digest_rather_than_under_its_region_name(
    archive: Archive,
) -> None:
    """Nothing a provider sent ever becomes a path component."""
    archive.publish(MONACO)
    archive.install(MONACO)

    written = sorted((archive.root / "maps" / "packages").iterdir())

    assert len(written) == 1
    assert written[0].name == MapRegionId.parse(MONACO).storage_key
    assert "monaco" not in str(written[0])


# --- Validation --------------------------------------------------------------


def test_a_file_that_is_not_a_container_is_refused(archive: Archive) -> None:
    """An extension proves nothing and a `Content-Type` proves nothing."""
    path = archive.sources / "not-a-package.mbtiles"
    path.write_bytes(b"<!doctype html><title>404</title>")
    archive.provider.publish(MONACO, path)

    with pytest.raises(MapOperationError) as raised:
        archive.install(MONACO)

    assert raised.value.code is MapErrorCode.MAP_PACKAGE_INVALID


def test_a_package_with_an_unknown_tile_vocabulary_fails_by_name(archive: Archive) -> None:
    """A blank map and a broken installation look identical to a reader."""
    archive.publish(MONACO, layers=("buildings", "pois"))

    with pytest.raises(MapOperationError) as raised:
        archive.install(MONACO)

    assert raised.value.code is MapErrorCode.MAP_PACKAGE_SCHEMA_UNSUPPORTED
    assert "streets" in raised.value.detail


def test_a_package_without_a_licence_does_not_install(archive: Archive) -> None:
    """There is no default attribution, because a default would be a claim."""
    archive.publish(MONACO, licence=None)

    with pytest.raises(MapOperationError) as raised:
        archive.install(MONACO)

    assert raised.value.code is MapErrorCode.MAP_PACKAGE_LICENCE_MISSING
    assert archive.repository.installed_packages() == ()


def test_a_package_with_no_tiles_does_not_install(archive: Archive) -> None:
    """A valid container holding nothing is not a map."""
    archive.publish(MONACO, with_tiles=False)

    with pytest.raises(MapOperationError) as raised:
        archive.install(MONACO)

    assert raised.value.code is MapErrorCode.MAP_PACKAGE_EMPTY


def test_a_package_with_impossible_bounds_is_refused(archive: Archive) -> None:
    """Metadata arrives from a stranger and is checked like any other input."""
    path = archive.publish(MONACO)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE metadata SET value = '0,0,900,900' WHERE name = 'bounds'")
    connection.commit()
    connection.close()

    with pytest.raises(MapOperationError) as raised:
        archive.install(MONACO)

    assert raised.value.code is MapErrorCode.MAP_PACKAGE_INVALID


def test_a_refused_package_leaves_nothing_behind(archive: Archive) -> None:
    """Neither a row, nor a managed file, nor a temporary one."""
    archive.publish(MONACO, licence=None)

    with pytest.raises(MapOperationError):
        archive.install(MONACO)

    assert archive.repository.installed_packages() == ()
    assert not list((archive.root / "maps" / "packages").rglob("*.mbtiles"))
    assert not list((archive.root / "maps" / "downloads").iterdir())


def test_the_attribution_is_read_out_of_the_package(archive: Archive) -> None:
    """Not assembled from a constant this build happens to carry."""
    archive.publish(MONACO, author="Somebody else, Some Provider", licence="CC-BY-4.0")
    archive.install(MONACO)

    package = archive.repository.installed_package(MapRegionId.parse(MONACO))

    assert package is not None
    assert package.attribution.data_owner == "Somebody else"
    assert package.attribution.provider == "Some Provider"
    assert package.attribution.license_identifier == "CC-BY-4.0"
    assert package.attribution.required_text == "Map data © Somebody else"


# --- Update ------------------------------------------------------------------


def test_a_failed_update_leaves_the_previous_map_usable(archive: Archive) -> None:
    """The contract the whole install ordering exists for."""
    archive.publish(MONACO)
    archive.install(MONACO)
    before = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert before is not None

    archive.publish(MONACO, licence=None, place_name="Broken")
    with pytest.raises(MapOperationError):
        archive.install(MONACO)

    after = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert after is not None
    assert after.content_sha256 == before.content_sha256
    assert archive.installed.all()[0].state is MapInstallState.INSTALLED


def test_an_update_to_the_same_bytes_switches_nothing(archive: Archive) -> None:
    """Switching would invalidate every cached tile URL to change nothing."""
    archive.publish(MONACO)
    archive.install(MONACO)
    before = archive.repository.installed_package(MapRegionId.parse(MONACO))

    result = archive.install(MONACO)

    after = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert result.outcome is InstallOutcome.ALREADY_CURRENT  # type: ignore[attr-defined]
    assert before is not None and after is not None
    assert after.downloaded_at == before.downloaded_at


def test_a_real_update_replaces_the_package_and_removes_the_old_file(
    archive: Archive,
) -> None:
    """One package per region on disk, and the new one is the one served."""
    archive.publish(MONACO)
    archive.install(MONACO)
    before = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert before is not None

    archive.publish(MONACO, place_name="Fixturehausen")
    result = archive.install(MONACO)

    after = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert result.outcome is InstallOutcome.UPDATED  # type: ignore[attr-defined]
    assert after is not None and after.content_sha256 != before.content_sha256
    assert not archive.storage.exists(before.region_id, before.content_sha256)
    assert archive.storage.is_intact(after.region_id, after.content_sha256)


# --- Disk space --------------------------------------------------------------


def test_an_install_that_would_not_fit_is_refused_before_the_transfer(
    archive: Archive, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An update must never free space by deleting the map it is replacing."""
    archive.publish(MONACO)
    monkeypatch.setattr(archive.storage, "free_bytes", lambda: 1024)

    with pytest.raises(MapOperationError) as raised:
        archive.install(MONACO)

    assert raised.value.code is MapErrorCode.MAP_INSUFFICIENT_DISK_SPACE
    assert archive.provider.downloads == 0


# --- Removal -----------------------------------------------------------------


def test_removing_a_package_removes_its_row_and_its_bytes(archive: Archive) -> None:
    """And nothing else. Track data lives in the same database and is untouched."""
    archive.publish(MONACO)
    archive.install(MONACO)
    remove = RemoveMapPackage(storage=archive.storage, repository=archive.repository)

    remove.run(MapRegionId.parse(MONACO))

    assert archive.repository.installed_packages() == ()
    assert not list((archive.root / "maps" / "packages").rglob("*.mbtiles"))


def test_removing_something_that_is_not_installed_says_so(archive: Archive) -> None:
    """Rather than succeeding quietly, which reads as "it was there"."""
    remove = RemoveMapPackage(storage=archive.storage, repository=archive.repository)

    with pytest.raises(MapOperationError) as raised:
        remove.run(MapRegionId.parse(MONACO))

    assert raised.value.code is MapErrorCode.MAP_PACKAGE_NOT_INSTALLED


def test_a_region_being_installed_cannot_be_removed_at_the_same_time(
    archive: Archive,
) -> None:
    """Two mutations of one region is a race with a file in the middle of it."""
    archive.publish(MONACO)
    archive.install(MONACO)
    target = MapRegionId.parse(MONACO)
    archive.repository.create_job(
        queued_job(
            job_id="f" * 32,
            region_id=target,
            region_name="Monaco",
            at=archive.clock.now(),
            is_update=True,
        )
    )
    remove = RemoveMapPackage(storage=archive.storage, repository=archive.repository)

    with pytest.raises(MapOperationError) as raised:
        remove.run(target)

    assert raised.value.code is MapErrorCode.MAP_MUTATION_IN_PROGRESS


# --- Recovery ----------------------------------------------------------------


def test_a_job_left_running_by_a_stopped_process_becomes_interrupted(
    archive: Archive,
) -> None:
    """A job reading `downloading` forever is a region nobody can install again."""
    archive.repository.create_job(
        queued_job(
            job_id="a" * 32,
            region_id=MapRegionId.parse(MONACO),
            region_name="Monaco",
            at=archive.clock.now(),
            is_update=False,
        )
    )

    _recover(archive)

    job = archive.repository.job("a" * 32)
    assert job is not None and job.state is MapJobState.INTERRUPTED


def test_a_partial_download_is_never_published_and_is_cleaned_up(
    archive: Archive,
) -> None:
    """A `.part` file is a transfer nobody finished, not a map."""
    (archive.root / "maps" / "downloads" / "abc.part").write_bytes(b"half a package")

    _recover(archive)

    assert not list((archive.root / "maps" / "downloads").iterdir())


def test_a_published_file_no_row_points_at_is_removed_rather_than_adopted(
    archive: Archive,
) -> None:
    """Inferring an installation nobody completed would be inventing provenance.

    The file may be perfectly valid. What is missing is any record of where it
    came from, which dataset it is and what has to be credited when it renders
    -- so it is removed and the region installs again cleanly.
    """
    archive.publish(MONACO)
    archive.install(MONACO)
    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None
    orphan = archive.storage.package_path(package.region_id, "b" * 64)
    orphan.write_bytes(b"SQLite format 3\x00")

    _recover(archive)

    assert not orphan.exists()
    assert archive.storage.is_intact(package.region_id, package.content_sha256)


# --- Reading tiles -----------------------------------------------------------


def test_a_tile_read_returns_the_stored_vector_tile(archive: Archive) -> None:
    """Including its layers, so a delivery test is about the real thing."""
    archive.publish(MONACO)
    archive.install(MONACO)
    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None

    tile = archive.tiles.read(package.region_id, package.content_sha256, 6, 33, 23)

    assert tile is not None
    assert tile[:2] == b"\x1f\x8b"
    assert "streets" in layer_names(tile)


def test_a_tile_outside_the_package_is_absent_rather_than_an_error(
    archive: Archive,
) -> None:
    """A hole in coverage is normal at every edge of every region."""
    archive.publish(MONACO)
    archive.install(MONACO)
    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None

    assert archive.tiles.read(package.region_id, package.content_sha256, 6, 1, 1) is None


def test_a_package_is_opened_read_only(archive: Archive) -> None:
    """A downloaded database is a stranger's file, and this build never writes to it."""
    archive.publish(MONACO)
    archive.install(MONACO)
    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None
    path = archive.storage.package_path(package.region_id, package.content_sha256)
    before = path.stat().st_mtime_ns

    archive.tiles.read(package.region_id, package.content_sha256, 6, 33, 23)

    assert path.stat().st_mtime_ns == before
    assert not list(path.parent.glob("*-wal"))
    assert not list(path.parent.glob("*-journal"))


# --- Two regions -------------------------------------------------------------


def test_two_regions_are_installed_side_by_side(archive: Archive) -> None:
    """Each in its own directory, each with its own delivery identity."""
    archive.publish(MONACO)
    archive.publish(ANDORRA)

    archive.install(MONACO)
    archive.install(ANDORRA)

    installed = archive.installed.all()
    assert {entry.package.region_name for entry in installed} == {"Monaco", "Andorra"}
    assert len({entry.package.delivery_id for entry in installed}) == 2


def _recover(archive: Archive) -> None:
    """Run the start-up recovery the composition root runs."""
    RecoverMapStorage(
        storage=archive.storage, repository=archive.repository, clock=archive.clock
    ).run()


# --- Regressions --------------------------------------------------------------


@pytest.mark.regression
def test_a_relative_data_directory_still_installs_and_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GPX_VIEW_DATA_DIR` defaults to a *relative* path, and that has to work.

    It did not. Reading a package builds a SQLite URI, `Path.as_uri()` refuses a
    relative path, and every install failed at validation with a `ValueError`
    after the whole file had been downloaded. Every test used `tmp_path` and the
    container uses `/data`, both absolute -- so the one configuration nobody
    exercised was the documented default a developer gets by running the server
    in a checkout.
    """
    monkeypatch.chdir(tmp_path)
    archive = Archive(tmp_path)
    archive.storage = FilesystemMapPackageStorage(Path("data") / "maps")
    archive.storage.prepare()
    archive.installer = InstallMapPackage(
        provider=archive.provider,
        catalog=archive.catalog,
        storage=archive.storage,
        inspector=MbtilesPackageInspector(archive.storage),
        repository=archive.repository,
        clock=archive.clock,
    )
    archive.tiles = MbtilesTileReader(archive.storage)
    archive.publish(MONACO)

    archive.install(MONACO)

    package = archive.repository.installed_package(MapRegionId.parse(MONACO))
    assert package is not None
    assert archive.tiles.read(package.region_id, package.content_sha256, 6, 33, 23) is not None


@pytest.mark.regression
def test_an_inspector_that_fails_unexpectedly_leaves_no_orphan(archive: Archive) -> None:
    """Cleanup was conditional on the failure having a name, and one did not.

    The file is published before it is validated -- deliberately, so that what is
    checked and what is later served are the same file. That makes the failure
    path load-bearing: it is the only thing standing between a refused package
    and a managed file nothing points at. Catching only `MapOperationError` left
    2.6 GB of orphans behind the first time an unexpected exception got out.
    """
    archive.publish(MONACO)

    archive.installer._inspector = _Exploding()

    with pytest.raises(MapOperationError) as raised:
        archive.install(MONACO)

    assert raised.value.code is MapErrorCode.MAP_PACKAGE_INVALID
    assert not list((archive.root / "maps" / "packages").rglob("*.mbtiles"))
    assert archive.repository.installed_packages() == ()


class _Exploding:
    """An inspector that fails in a way nothing declared."""

    def inspect(self, region_id: MapRegionId, content_sha256: str) -> object:
        """Fail with an exception the install path has no case for."""
        raise RuntimeError(f"unpredicted: {region_id} {content_sha256[:8]}")
