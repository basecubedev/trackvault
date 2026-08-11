"""The server import directory and the command-line import path.

Both are input paths, not pipelines: they read bytes and hand them to the one
canonical use case. What they must guarantee on their own is that the source
directory is treated as untrusted input and is never modified.
"""

import errno
import os
import shutil
from pathlib import Path

import pytest

from gpx_view.application import ImportErrorCode
from gpx_view.application.import_tracks import ImportStatus
from gpx_view.application.ports import TrackQuery
from gpx_view.cli import EXIT_DISABLED, EXIT_FAILED, EXIT_OK, main
from gpx_view.config import Settings
from gpx_view.domain import InputChannel
from gpx_view.infrastructure.assembly import build_services
from gpx_view.infrastructure.database import SqliteTrackStore
from gpx_view.infrastructure.filesystem import import_directory, scan_import_directory

pytestmark = [pytest.mark.integration, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    """Return an import directory holding a few synthetic files."""
    directory = tmp_path / "inbox"
    directory.mkdir()
    for name in ("recorded-measurements.gpx", "generic-external-link.gpx", "malformed.gpx"):
        shutil.copy(FIXTURES / name, directory / name)
    return directory


def _settings(tmp_path: Path, import_dir: Path | None = None, **overrides: int) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data", import_dir=import_dir, **overrides)


# --- The import directory is input, never authority -------------------------


def test_every_file_in_the_directory_reaches_the_one_use_case(tmp_path: Path, inbox: Path) -> None:
    """The scan is a reader; the import use case does all the work."""
    services = build_services(_settings(tmp_path, inbox))
    services.prepare_storage()

    results = scan_import_directory(inbox, services.import_tracks)

    assert [name for name, _ in results] == sorted(path.name for path in inbox.iterdir())
    assert len(services.store.list_tracks(TrackQuery()).tracks) == 2


def test_a_broken_file_does_not_stop_the_others(tmp_path: Path, inbox: Path) -> None:
    """One unreadable file must not cost a whole sync run."""
    services = build_services(_settings(tmp_path, inbox))
    services.prepare_storage()

    results = dict(scan_import_directory(inbox, services.import_tracks))

    assert results["malformed.gpx"].error_code is ImportErrorCode.INVALID_GPX
    assert results["recorded-measurements.gpx"].status is ImportStatus.IMPORTED
    assert results["generic-external-link.gpx"].status is ImportStatus.IMPORTED


def test_the_source_directory_is_left_untouched(tmp_path: Path, inbox: Path) -> None:
    """Nothing in the sync folder is written, renamed or deleted."""
    services = build_services(_settings(tmp_path, inbox))
    services.prepare_storage()
    before = {path.name: path.read_bytes() for path in inbox.iterdir()}

    scan_import_directory(inbox, services.import_tracks)

    assert {path.name: path.read_bytes() for path in inbox.iterdir()} == before


def test_scanning_twice_imports_nothing_new(tmp_path: Path, inbox: Path) -> None:
    """A sync tool that keeps putting files back must not duplicate anything."""
    services = build_services(_settings(tmp_path, inbox))
    services.prepare_storage()
    scan_import_directory(inbox, services.import_tracks)

    second = dict(scan_import_directory(inbox, services.import_tracks))

    assert all(outcome.status is ImportStatus.DUPLICATE for outcome in second.values())
    assert len(services.store.list_tracks(TrackQuery()).tracks) == 2


def test_the_channel_is_recorded_on_the_raw_import(tmp_path: Path, inbox: Path) -> None:
    """Where bytes came from is metadata worth keeping, and nothing more."""
    services = build_services(_settings(tmp_path, inbox))
    services.prepare_storage()

    results = dict(scan_import_directory(inbox, services.import_tracks))
    raw = services.store.find_raw_import(results["recorded-measurements.gpx"].sha256)

    assert raw is not None
    assert raw.input_channel is InputChannel.IMPORT_DIRECTORY


def test_a_symlink_is_skipped(tmp_path: Path, inbox: Path) -> None:
    """A link in an input directory must not make the archive read elsewhere."""
    outside = tmp_path / "outside.gpx"
    shutil.copy(FIXTURES / "ambiguous-minimal.gpx", outside)
    (inbox / "link.gpx").symlink_to(outside)

    services = build_services(_settings(tmp_path, inbox))
    services.prepare_storage()
    results = dict(scan_import_directory(inbox, services.import_tracks))

    assert "link.gpx" not in results


def test_a_subdirectory_is_not_descended_into(tmp_path: Path, inbox: Path) -> None:
    """The scan reads one directory, not a tree it does not control."""
    nested = inbox / "nested"
    nested.mkdir()
    shutil.copy(FIXTURES / "ambiguous-minimal.gpx", nested / "hidden.gpx")

    services = build_services(_settings(tmp_path, inbox))
    services.prepare_storage()
    results = dict(scan_import_directory(inbox, services.import_tracks))

    assert "nested" not in results
    assert "hidden.gpx" not in results


def test_an_oversized_file_is_refused_without_being_read_whole(tmp_path: Path, inbox: Path) -> None:
    """A huge file must not be loaded into memory before it is refused."""
    services = build_services(_settings(tmp_path, inbox, import_max_bytes=64))
    services.prepare_storage()

    results = dict(scan_import_directory(inbox, services.import_tracks))

    assert all(
        outcome.error_code is ImportErrorCode.IMPORT_TOO_LARGE for outcome in results.values()
    )
    assert services.store.list_tracks(TrackQuery()).tracks == ()


def test_an_unreadable_file_is_skipped_rather_than_fatal(
    tmp_path: Path, inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that cannot be read must not abort the whole sync run.

    The failure is injected rather than provoked with `chmod 000`, which root
    ignores: whether a contract holds must not depend on which user runs the
    suite, and running as root inside a container is normal enough that it would
    eventually happen. Injecting it at the open states exactly which operation is
    expected to fail, and the open is where a real permission problem surfaces.
    """
    unreadable = inbox / "unreadable.gpx"
    shutil.copy(FIXTURES / "ambiguous-minimal.gpx", unreadable)
    real_open = os.open

    def refuse_one(path: object, flags: int, /, *args: object, **kwargs: object) -> int:
        if path == "unreadable.gpx":
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(import_directory.os, "open", refuse_one)

    services = build_services(_settings(tmp_path, inbox))
    services.prepare_storage()
    results = dict(scan_import_directory(inbox, services.import_tracks))

    assert "unreadable.gpx" not in results
    assert results["recorded-measurements.gpx"].status is ImportStatus.IMPORTED


def test_a_missing_import_directory_is_not_an_error(tmp_path: Path) -> None:
    """A sync target that does not exist yet simply has nothing to offer."""
    services = build_services(_settings(tmp_path))
    services.prepare_storage()

    assert scan_import_directory(tmp_path / "absent", services.import_tracks) == []


# --- The command line -------------------------------------------------------


def test_the_command_line_imports_named_files(tmp_path: Path) -> None:
    """A server-side import is an operator action, and it works."""
    settings = _settings(tmp_path)

    code = main(["import", str(FIXTURES / "recorded-measurements.gpx")], settings=settings)

    assert code == EXIT_OK
    assert len(SqliteTrackStore(settings.database_path).list_tracks(TrackQuery()).tracks) == 1


def test_the_command_line_reports_a_failure(tmp_path: Path) -> None:
    """An import that failed must not look like a success in a script."""
    assert main(["import", str(FIXTURES / "malformed.gpx")], settings=_settings(tmp_path)) == (
        EXIT_FAILED
    )


def test_the_command_line_scan_uses_the_configured_directory(tmp_path: Path, inbox: Path) -> None:
    """`scan` is the same use case, reached through the configured directory."""
    settings = _settings(tmp_path, inbox)

    assert main(["scan"], settings=settings) == EXIT_FAILED  # the inbox holds a broken file
    assert len(SqliteTrackStore(settings.database_path).list_tracks(TrackQuery()).tracks) == 2


def test_scanning_without_a_configured_directory_says_so(tmp_path: Path) -> None:
    """An unset import directory disables the feature instead of guessing one."""
    assert main(["scan"], settings=_settings(tmp_path)) == EXIT_DISABLED


def test_all_persistent_data_stays_inside_the_data_directory(tmp_path: Path, inbox: Path) -> None:
    """A backup of the data directory has to be a backup of everything."""
    settings = _settings(tmp_path, inbox)

    main(["scan"], settings=settings)

    written = {path for path in tmp_path.rglob("*") if path.is_file()}
    outside = {path for path in written if settings.data_dir not in path.parents}

    assert outside == {path for path in inbox.iterdir() if path.is_file()}
