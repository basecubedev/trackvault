"""Contracts for the command-line input path.

The command line is an *input path*, not a pipeline: it reads bytes and hands
them to the one canonical import use case. What it owes the operator is
therefore small and specific -- try every path it was given, bound what it reads,
and report an exit code that says whether anything went wrong.

These tests use no permission tricks. A test whose outcome depends on whether the
suite happens to run as root is not a contract, and running as root inside a
container is normal enough that it would eventually happen.
"""

from pathlib import Path

import pytest

from trackvault.cli import EXIT_FAILED, EXIT_OK, main
from trackvault.config import Settings

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data")


def _fixture_copy(directory: Path, name: str, as_name: str | None = None) -> Path:
    """Put a synthetic fixture where the command line can be pointed at it."""
    target = directory / (as_name or name)
    target.write_bytes((FIXTURES / name).read_bytes())
    return target


def test_one_unreadable_path_does_not_stop_the_ones_after_it(
    tmp_path: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every path on the command line is attempted, whatever happened before it.

    An operator who imports a directory's worth of files wants the ones that can
    be read to arrive. Returning at the first failure silently drops the rest,
    and the summary the operator reads does not even mention them.

    A path that does not exist is the deterministic unreadable case: it fails the
    same way for every user, root included.
    """
    missing = tmp_path / "not-there.gpx"
    valid = _fixture_copy(tmp_path, "recorded-measurements.gpx")

    exit_code = main(["import", str(missing), str(valid)], settings=settings)
    output = capsys.readouterr().out

    assert "not-there.gpx" in output
    assert "recorded-measurements.gpx" in output
    assert "imported" in output, "the readable file after the failure was never imported"
    assert exit_code == EXIT_FAILED


def test_an_unreadable_path_still_makes_the_run_fail(
    tmp_path: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """Continuing past a failure is not the same as forgiving it."""
    valid = _fixture_copy(tmp_path, "recorded-measurements.gpx")

    assert main(["import", str(valid)], settings=settings) == EXIT_OK
    capsys.readouterr()

    other = _fixture_copy(tmp_path, "ambiguous-minimal.gpx")
    exit_code = main(
        ["import", str(tmp_path / "gone.gpx"), str(other)],
        settings=settings,
    )

    assert exit_code == EXIT_FAILED


def test_a_local_input_path_never_reads_a_file_unbounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The byte limit has to bound the *read*, not just the verdict.

    Refusing an oversized file after loading all of it into memory protects
    nothing: the cost the limit exists to prevent has already been paid. Reading
    one byte past the limit is enough to prove a file is over it, which is what
    the import directory scan already does -- both input paths owe the same
    guarantee.
    """
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: pytest.fail(f"an input path read {self.name} without a bound"),
    )
    oversized = tmp_path / "oversized.gpx"
    oversized.write_bytes(b"<gpx>" + b"x" * 4096 + b"</gpx>")

    exit_code = main(
        ["import", str(oversized)],
        settings=Settings(data_dir=tmp_path / "data", import_max_bytes=64),
    )

    assert "import_too_large" in capsys.readouterr().out
    assert exit_code == EXIT_FAILED
