"""The backup and restore commands, driven the way an operator drives them.

The command line is the whole operator interface to the data lifecycle, and the
things it owes are blunt: never destroy data nobody said to destroy, never report
a backup that is not one, and make the dry-run usable in a script.
"""

import tarfile
from pathlib import Path

import pytest

from trackvault.cli import EXIT_DEGRADED, EXIT_FAILED, EXIT_OK, main
from trackvault.config import Settings
from trackvault.infrastructure.archive import ARCHIVE_SUFFIX

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"
SOURCE = "recorded-measurements.gpx"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings with separate data, backup and restore directories."""
    return Settings(data_dir=tmp_path / "data", backup_dir=tmp_path / "backups")


@pytest.fixture
def archive(tmp_path: Path, settings: Settings) -> Path:
    """Import one fixture, back the archive up, and return the backup."""
    source = tmp_path / SOURCE
    source.write_bytes((FIXTURES / SOURCE).read_bytes())
    assert main(["import", str(source)], settings=settings) == EXIT_OK
    assert main(["backup", "create"], settings=settings) == EXIT_OK
    return next(iter(sorted((tmp_path / "backups").glob(f"*{ARCHIVE_SUFFIX}"))))


def test_a_backup_lands_in_the_configured_directory(archive: Path) -> None:
    """A backup written into the data directory would be lost with it."""
    assert archive.parent.name == "backups"
    assert archive.name.startswith("trackvault-")
    assert tarfile.is_tarfile(archive)


def test_creating_a_backup_reports_what_it_holds_and_what_it_omits(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator has to be able to check the backup against what they expected."""
    source = tmp_path / SOURCE
    source.write_bytes((FIXTURES / SOURCE).read_bytes())
    assert main(["import", str(source)], settings=settings) == EXIT_OK

    assert main(["backup", "create"], settings=settings) == EXIT_OK

    output = capsys.readouterr().out
    assert "sources:   1" in output
    assert "tracks:    1" in output
    assert "map_packages" in output


def test_a_backup_of_an_inconsistent_archive_fails_by_name(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator interface to the failure this must never get wrong.

    A named code and a non-zero exit, not a traceback: the archive is missing an
    original its database names, and what an operator does next is run `doctor`,
    not read a stack trace.
    """
    source = tmp_path / SOURCE
    source.write_bytes((FIXTURES / SOURCE).read_bytes())
    assert main(["import", str(source)], settings=settings) == EXIT_OK
    next(settings.raw_storage_dir.rglob("*.raw")).unlink()

    assert main(["backup", "create"], settings=settings) == EXIT_FAILED

    captured = capsys.readouterr()
    assert "archive_source_incomplete" in captured.err
    assert "Traceback" not in captured.err
    assert not list(settings.backup_storage_dir.glob(f"*{ARCHIVE_SUFFIX}"))
    assert not list(settings.backup_storage_dir.glob("*.part"))


def test_a_backup_that_passes_over_stored_debris_says_how_much(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An archive that leaves bytes behind reports the count, not a shrug."""
    source = tmp_path / SOURCE
    source.write_bytes((FIXTURES / SOURCE).read_bytes())
    assert main(["import", str(source)], settings=settings) == EXIT_OK
    orphan = next(settings.raw_storage_dir.rglob("*.raw")).with_name(f"{'b' * 64}.raw")
    orphan.write_bytes(b"bytes no row can account for")

    assert main(["backup", "create"], settings=settings) == EXIT_OK

    assert "unreferenced_raw_objects x1" in capsys.readouterr().out
    assert orphan.is_file()


def test_listing_backups_says_so_when_there_are_none(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty listing and a broken command must not look the same."""
    assert main(["backup", "list"], settings=settings) == EXIT_OK

    assert "no backups" in capsys.readouterr().out


def test_listing_backups_names_the_ones_that_exist(
    archive: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """The listing is what an operator checks before they trust a restore."""
    assert main(["backup", "list"], settings=settings) == EXIT_OK

    assert archive.name in capsys.readouterr().out


def test_a_dry_run_reports_the_archive_and_changes_nothing(
    archive: Path, settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command that lets somebody decide before they commit."""
    target = tmp_path / "fresh"

    exit_code = main(
        ["restore", str(archive), "--dry-run", "--into", str(target)], settings=settings
    )

    output = capsys.readouterr().out
    assert exit_code == EXIT_OK
    assert "compatibility: supported" in output
    assert "tracks:        1" in output
    assert not target.exists()


def test_a_dry_run_of_an_unrestorable_archive_exits_non_zero(
    settings: Settings, tmp_path: Path
) -> None:
    """A dry-run is usable as a check in a script, not only as something to read."""
    junk = tmp_path / "not-a-backup.tar.gz"
    junk.write_bytes(b"definitely not a tar file")

    exit_code = main(["restore", str(junk), "--dry-run"], settings=settings)

    assert exit_code == EXIT_FAILED


def test_a_restore_refuses_to_replace_the_live_archive_by_default(
    archive: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """The single most destructive thing this command could do, refused by default."""
    exit_code = main(["restore", str(archive)], settings=settings)

    assert exit_code == EXIT_FAILED
    assert "restore_target_occupied" in capsys.readouterr().err
    assert settings.database_path.is_file()


def test_a_restore_into_a_fresh_directory_succeeds(
    archive: Path, settings: Settings, tmp_path: Path
) -> None:
    """The ordinary disaster-recovery path: a new machine and a backup."""
    target = tmp_path / "fresh"

    exit_code = main(["restore", str(archive), "--into", str(target)], settings=settings)

    assert exit_code == EXIT_OK
    assert (target / "trackvault.sqlite3").is_file()
    assert any((target / "raw").rglob("*.raw"))


def test_a_restore_with_replace_replaces(
    archive: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """Replacing is possible, and it is something somebody had to type."""
    exit_code = main(["restore", str(archive), "--replace"], settings=settings)

    assert exit_code == EXIT_OK
    assert "replaced existing data: yes" in capsys.readouterr().out


def test_restoring_a_file_that_is_not_an_archive_fails_cleanly(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A wrong file is a named refusal, not a traceback and not a half-restore."""
    junk = tmp_path / "holiday-photos.tar.gz"
    junk.write_bytes(b"not a tar")
    target = tmp_path / "fresh"

    exit_code = main(["restore", str(junk), "--into", str(target)], settings=settings)

    assert exit_code == EXIT_FAILED
    assert "archive_unreadable" in capsys.readouterr().err
    assert not (target / "trackvault.sqlite3").exists()


def test_a_backup_of_a_fresh_archive_is_still_a_backup(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """Checking the command works before you need it must not be an error."""
    exit_code = main(["backup", "create"], settings=settings)

    assert exit_code == EXIT_OK
    assert "sources:   0" in capsys.readouterr().out


def test_the_restored_archive_serves_the_same_tracks(
    archive: Path, settings: Settings, tmp_path: Path
) -> None:
    """Restore, then read the restored deployment with the ordinary commands.

    Driving the restored data directory through the same command line an operator
    would is what makes this a restore rather than a set of files in the right
    places.
    """
    target = tmp_path / "fresh"
    assert main(["restore", str(archive), "--into", str(target)], settings=settings) == EXIT_OK

    restored = Settings(data_dir=target)
    assert main(["analyze", "--outdated"], settings=restored) == EXIT_OK
    assert (
        main(["export", "track", "1", "--output", str(tmp_path / "out.gpx")], settings=restored)
        == EXIT_OK
    )
    assert (tmp_path / "out.gpx").read_bytes().startswith(b"<?xml")


@pytest.mark.usefixtures("archive")
def test_doctor_reports_a_configured_deployment_as_sound(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Data, backups and an import directory all present: nothing is an error.

    The suite deliberately composes without a built browser application, so
    ``web_assets`` warns here whatever the deployment does. What is asserted is
    the part this test is about: every check that describes the *data* is clean.
    """
    scanned = tmp_path / "import"
    scanned.mkdir()
    configured = Settings(
        data_dir=settings.data_dir, backup_dir=settings.backup_dir, import_dir=scanned
    )

    main(["doctor"], settings=configured)

    output = capsys.readouterr().out
    for line in output.splitlines():
        if any(
            line.startswith(status) and name in line
            for status in ("ok", "warning", "error")
            for name in ("data_directory", "database", "schema", "raw_storage", "backups")
        ):
            assert line.startswith("ok"), line
    assert "ok       import_directory" in output


@pytest.mark.usefixtures("archive")
def test_doctor_reports_a_missing_import_directory_as_degraded(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check that would have caught the folder nobody mounted.

    Two is degraded rather than failed on purpose: scanning being switched off
    is somebody's configuration, not a fault, and it should not page anyone.
    """
    exit_code = main(["doctor"], settings=settings)

    assert exit_code == EXIT_DEGRADED
    assert "TRACKVAULT_IMPORT_DIR" in capsys.readouterr().out


@pytest.mark.usefixtures("archive")
def test_doctor_reports_a_corrupted_original_as_an_error(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one thing in the archive nobody can recreate, checked by reading it."""
    next(settings.raw_storage_dir.rglob("*.raw")).write_bytes(b"not the original bytes")

    exit_code = main(["doctor"], settings=settings)

    assert exit_code == EXIT_FAILED
    assert "do not match their content hash" in capsys.readouterr().out


def test_doctor_on_a_fresh_directory_creates_no_database(tmp_path: Path) -> None:
    """Running `doctor` when unsure must not be the thing that changes the answer."""
    fresh = Settings(data_dir=tmp_path / "untouched")

    exit_code = main(["doctor"], settings=fresh)

    assert exit_code == EXIT_DEGRADED
    assert not (tmp_path / "untouched" / "trackvault.sqlite3").exists()
