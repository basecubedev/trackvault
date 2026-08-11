"""Executable contract for the file permissions of private movement data.

A GPS archive is a movement profile of a real person. On a self-hosted machine it
sits beside other people's home directories, other services' data and whatever
else runs as another user, so the archive's own artifacts are created readable by
their owner and by nobody else:

```
data directory      0700
raw directories     0700
raw artifacts       0600
SQLite database     0600
-wal and -shm       0600
temporary artifacts 0600, from the first byte
```

Two properties matter as much as the numbers. The mode is set **at creation**, so
there is no window in which a file is briefly world-readable, and it is set
independently of the process umask, so a permissive umask cannot widen it.

TrackVault only decides the permissions of the artifacts it creates itself. It does
not chmod files it finds, because a path inside the data directory may carry an
operator's own access decisions -- an ACL, a shared group -- and destroying those
silently would be a worse failure than the one being prevented.
"""

import os
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from trackvault.application.import_tracks import ImportRequest
from trackvault.config import Settings
from trackvault.infrastructure.assembly import TrackServices, build_services
from trackvault.infrastructure.filesystem import raw_store as raw_store_module

pytestmark = [pytest.mark.contract, pytest.mark.storage, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

PRIVATE_FILE = 0o600
PRIVATE_DIRECTORY = 0o700

# The permissive umask a normal interactive login has. Under it, an artifact
# created with the usual 0o666/0o777 request comes out world-readable, which is
# exactly the failure this contract exists to prevent.
PERMISSIVE_UMASK = 0o022


@pytest.fixture(autouse=True)
def permissive_umask() -> Iterator[None]:
    """Run every scenario under a permissive umask and restore the old one.

    The umask is process state, so the contract is only meaningful if the test
    states which one it ran under. Relying on the developer's happens to be
    strict would prove nothing on the machine where it is not.
    """
    previous = os.umask(PERMISSIVE_UMASK)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    """Return the permission bits of a path."""
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def archive(tmp_path: Path) -> TrackServices:
    """Return a wired archive whose data directory does not exist yet."""
    services = build_services(Settings(data_dir=tmp_path / "data"))
    services.prepare_storage()
    return services


def _import(archive: TrackServices, name: str = "recorded-measurements.gpx") -> str:
    """Import a synthetic fixture and return its content hash."""
    outcome = archive.import_tracks(
        ImportRequest(content=(FIXTURES / name).read_bytes(), original_filename=name)
    )
    return outcome.sha256


# --- What the archive creates -------------------------------------------------


def test_the_data_directory_is_private(archive: TrackServices) -> None:
    """Everything persistent lives here, so the directory itself is the first fence."""
    assert _mode(archive.settings.data_dir) == PRIVATE_DIRECTORY


def test_the_database_is_private_from_creation(archive: TrackServices) -> None:
    """A database that is briefly world-readable was briefly world-readable.

    Creating the file with the right mode is not the same as fixing it up
    afterwards: between the two there is a window, and a window is all an
    unrelated process on the same machine needs.
    """
    assert _mode(archive.settings.database_path) == PRIVATE_FILE


def test_the_write_ahead_log_and_shared_memory_files_are_private(
    archive: TrackServices,
) -> None:
    """`-wal` and `-shm` hold the same movement data as the database itself."""
    _import(archive)
    database = archive.settings.database_path
    with archive.store.connection() as connection:
        connection.execute("SELECT count(*) FROM tracks").fetchone()
        journals = [database.with_name(database.name + suffix) for suffix in ("-wal", "-shm")]
        present = [path for path in journals if path.exists()]

        assert present, "write-ahead logging left no journal files to check"
        assert all(_mode(path) == PRIVATE_FILE for path in present), {
            path.name: oct(_mode(path)) for path in present
        }


def test_the_managed_raw_directories_are_private(archive: TrackServices) -> None:
    """The fan-out directories name content hashes, which are themselves data."""
    sha256 = _import(archive)
    artifact = archive.raw_store.path_for(sha256)

    assert _mode(archive.raw_store.root) == PRIVATE_DIRECTORY
    assert _mode(artifact.parent.parent) == PRIVATE_DIRECTORY
    assert _mode(artifact.parent) == PRIVATE_DIRECTORY


def test_a_managed_raw_artifact_is_private(archive: TrackServices) -> None:
    """The artifact is the original recording, byte for byte."""
    assert _mode(archive.raw_store.path_for(_import(archive))) == PRIVATE_FILE


def test_a_temporary_artifact_is_private_before_it_is_renamed(
    archive: TrackServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes are complete and private before the artifact has its final name.

    The temporary file holds the whole recording, so a permissive mode on it is
    the same exposure as a permissive mode on the artifact -- for a shorter time,
    which is not a defence. The mode is read at the one moment it exists: just
    before the rename that puts it in place.
    """
    observed: list[int] = []
    real_replace = os.replace

    def watch(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        observed.append(stat.S_IMODE(os.stat(source, dir_fd=src_dir_fd).st_mode))
        real_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(raw_store_module.os, "replace", watch)
    _import(archive)

    assert observed == [PRIVATE_FILE]


# --- What the archive finds ---------------------------------------------------


def test_an_existing_file_is_not_chmodded(archive: TrackServices, tmp_path: Path) -> None:
    """TrackVault tightens what it creates; it does not rewrite what it finds.

    A path inside the data directory may carry an operator's own decision -- a
    shared group, an ACL, a backup agent's access. Silently overwriting that
    would replace one surprise with a worse one.
    """
    foreign = archive.settings.data_dir / "operator-notes.txt"
    foreign.write_text("kept by the operator", encoding="utf-8")
    foreign.chmod(0o644)

    _import(archive)

    assert _mode(foreign) == 0o644
    assert tmp_path in foreign.parents


def test_an_existing_data_directory_keeps_its_mode(tmp_path: Path) -> None:
    """An operator who widened the directory on purpose keeps it that way."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o750)
    data_dir.chmod(0o750)

    services = build_services(Settings(data_dir=data_dir))
    services.prepare_storage()
    _import(services)

    assert _mode(data_dir) == 0o750


# --- The database driver is not asked to guess --------------------------------


def test_a_database_created_by_the_driver_alone_would_not_be_private(
    tmp_path: Path,
) -> None:
    """States the failure this contract prevents, so the fix cannot quietly lapse.

    ``sqlite3.connect`` creates its file with 0o644 minus the umask, like any
    other library would. That is why the archive creates the file itself first,
    rather than trusting the driver's default.
    """
    path = tmp_path / "driver.sqlite3"
    sqlite3.connect(path).close()

    assert _mode(path) != PRIVATE_FILE
