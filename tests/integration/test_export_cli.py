"""The export commands, driven the way an operator drives them.

Exporting is an operator action on the machine that holds the data, exactly as
importing is. These tests check what the command line owes: the original bytes
come back unchanged, a generated document is a different thing and says so, and
an unknown identity is a non-zero exit rather than an empty file.
"""

import hashlib
from pathlib import Path

import pytest

from trackvault.cli import EXIT_FAILED, EXIT_OK, main
from trackvault.config import Settings

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"
SOURCE = "recorded-measurements.gpx"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def imported(tmp_path: Path, settings: Settings) -> str:
    """Import one synthetic fixture and return its content hash."""
    source = tmp_path / SOURCE
    source.write_bytes((FIXTURES / SOURCE).read_bytes())
    assert main(["import", str(source)], settings=settings) == EXIT_OK
    return hashlib.sha256(source.read_bytes()).hexdigest()


def test_exporting_a_raw_source_writes_the_bytes_that_arrived(
    tmp_path: Path, settings: Settings, imported: str
) -> None:
    """The archive's central promise, at the command line."""
    destination = tmp_path / "out.gpx"

    exit_code = main(["export", "raw", imported, "--output", str(destination)], settings=settings)

    assert exit_code == EXIT_OK
    assert destination.read_bytes() == (FIXTURES / SOURCE).read_bytes()


def test_exporting_a_raw_source_writes_to_standard_output_by_default(
    settings: Settings, imported: str, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    """An export composes into a pipe rather than inventing a file somewhere."""
    exit_code = main(["export", "raw", imported], settings=settings)

    assert exit_code == EXIT_OK
    assert capsysbinary.readouterr().out == (FIXTURES / SOURCE).read_bytes()


def test_exporting_an_unknown_source_fails_rather_than_writing_nothing(
    tmp_path: Path, settings: Settings
) -> None:
    """A wrong hash is an error, and it must not leave a plausible empty file."""
    destination = tmp_path / "out.gpx"

    exit_code = main(["export", "raw", "0" * 64, "--output", str(destination)], settings=settings)

    assert exit_code == EXIT_FAILED
    assert not destination.exists()


@pytest.mark.usefixtures("imported")
def test_exporting_a_track_writes_a_document_the_archive_reads_back(
    tmp_path: Path, settings: Settings
) -> None:
    """A generated document is importable: the round trip closes at the CLI too."""
    destination = tmp_path / "track.gpx"
    assert (
        main(["export", "track", "1", "--output", str(destination)], settings=settings) == EXIT_OK
    )

    assert main(["import", str(destination)], settings=settings) == EXIT_OK


def test_a_generated_document_is_not_the_original_bytes(
    tmp_path: Path, settings: Settings, imported: str
) -> None:
    """The distinction the two commands exist to keep, asserted rather than assumed.

    If these two ever produced the same bytes, one of them would be lying about
    what it is -- and the one that lies is whichever gets used for a backup.
    """
    raw = tmp_path / "raw.gpx"
    generated = tmp_path / "generated.gpx"

    assert main(["export", "raw", imported, "--output", str(raw)], settings=settings) == EXIT_OK
    assert main(["export", "track", "1", "--output", str(generated)], settings=settings) == EXIT_OK

    assert raw.read_bytes() != generated.read_bytes()
    assert b"TrackVault" in generated.read_bytes()


def test_exporting_an_unknown_track_fails(tmp_path: Path, settings: Settings) -> None:
    """A track identity nobody holds is a non-zero exit, not an empty document."""
    destination = tmp_path / "out.gpx"

    exit_code = main(["export", "track", "424242", "--output", str(destination)], settings=settings)

    assert exit_code == EXIT_FAILED
    assert not destination.exists()
