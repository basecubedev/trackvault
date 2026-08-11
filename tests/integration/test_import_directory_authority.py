"""The import directory as a threat boundary, not just a folder.

``TRACKVAULT_IMPORT_DIR`` is where a phone auto-sync tool, a download manager or
anything else on the machine drops files. What lands there is untrusted, and so
is what happens to it between the moment the scan lists a name and the moment the
archive opens it.

The mistake this file pins is a check-then-open sequence over a path:

```
list the directory        -> "this is a regular file"
                          <- something replaces it
open the path             -> the archive reads whatever is there now
```

Closing that window needs the open itself to carry the guarantee. The directory
is opened once, every candidate is opened relative to that descriptor without
following symbolic links, and what the descriptor turns out to be is decided by
``fstat`` on the open file rather than by a stale look at the path.

The scenarios below are deterministic: the file system is mutated between the two
calls that the window sits between, so no timing, thread or sleep is involved.
"""

import os
import stat
from pathlib import Path

import pytest

from trackvault.application import ImportLimits
from trackvault.infrastructure.filesystem import open_import_directory

pytestmark = [pytest.mark.integration, pytest.mark.storage]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

LIMIT = ImportLimits().max_bytes


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    """Return an import directory holding one ordinary synthetic file."""
    directory = tmp_path / "inbox"
    directory.mkdir()
    recording = (FIXTURES / "recorded-measurements.gpx").read_bytes()
    (directory / "candidate.gpx").write_bytes(recording)
    return directory


def _outside(tmp_path: Path, content: bytes = b"secret") -> Path:
    """Return a file outside the import root that must never be read."""
    target = tmp_path / "outside.txt"
    target.write_bytes(content)
    return target


# --- The ordinary case --------------------------------------------------------


def test_a_regular_file_is_listed_and_read(inbox: Path) -> None:
    """The boundary has to let normal files through, or it protects nothing."""
    with open_import_directory(inbox) as directory:
        assert directory is not None
        (entry,) = directory.entries()

        assert entry.name == "candidate.gpx"
        assert directory.read(entry, LIMIT) == (inbox / "candidate.gpx").read_bytes()


def test_entries_are_listed_in_a_stable_order(inbox: Path) -> None:
    """Two identical scans see the same files in the same order."""
    for name in ("b.gpx", "a.gpx", "c.gpx"):
        (inbox / name).write_bytes(b"<gpx/>")

    with open_import_directory(inbox) as directory:
        assert directory is not None
        first = [entry.name for entry in directory.entries()]
        second = [entry.name for entry in directory.entries()]

    assert first == second == sorted(first)


def test_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    """A sync target that does not exist yet simply has nothing to offer."""
    with open_import_directory(tmp_path / "absent") as directory:
        assert directory is None


# --- Nothing below the root escapes the root ----------------------------------


def test_a_symbolic_link_is_never_followed(inbox: Path, tmp_path: Path) -> None:
    """A link in an input directory must not make the archive read elsewhere."""
    (inbox / "link.gpx").symlink_to(_outside(tmp_path))

    with open_import_directory(inbox) as directory:
        assert directory is not None
        link = next(entry for entry in directory.entries() if entry.name == "link.gpx")

        assert directory.read(link, LIMIT) is None


def test_a_regular_file_replaced_by_a_link_after_discovery_is_refused(
    inbox: Path, tmp_path: Path
) -> None:
    """The window between listing and opening is where a path check is worthless.

    The scan sees a regular file. Before the archive opens it, the name is a
    symbolic link to somewhere else entirely. An open that follows links reads
    the target and files it under the import directory's name.
    """
    outside = _outside(tmp_path, b"<gpx>content from outside the import root</gpx>")

    with open_import_directory(inbox) as directory:
        assert directory is not None
        (entry,) = directory.entries()

        (inbox / entry.name).unlink()
        (inbox / entry.name).symlink_to(outside)

        assert directory.read(entry, LIMIT) is None


def test_a_regular_file_replaced_by_a_directory_after_discovery_is_refused(
    inbox: Path,
) -> None:
    """What a name refers to is decided on the open file, not on the earlier look."""
    with open_import_directory(inbox) as directory:
        assert directory is not None
        (entry,) = directory.entries()

        (inbox / entry.name).unlink()
        (inbox / entry.name).mkdir()

        assert directory.read(entry, LIMIT) is None


def test_a_file_deleted_after_discovery_is_skipped(inbox: Path) -> None:
    """A sync tool may take a file back; that is not a failure of the run."""
    with open_import_directory(inbox) as directory:
        assert directory is not None
        (entry,) = directory.entries()

        (inbox / entry.name).unlink()

        assert directory.read(entry, LIMIT) is None


def test_a_directory_entry_is_never_read_as_a_candidate(inbox: Path) -> None:
    """The scan reads one directory, not a tree it does not control."""
    (inbox / "nested").mkdir()

    with open_import_directory(inbox) as directory:
        assert directory is not None
        nested = next(entry for entry in directory.entries() if entry.name == "nested")

        assert directory.read(nested, LIMIT) is None


def test_a_named_pipe_does_not_block_the_scan(inbox: Path) -> None:
    """An entry that is not a regular file is refused rather than waited on.

    Opening a FIFO for reading blocks until somebody writes to it. A scan that
    can be stopped indefinitely by dropping a named pipe into the sync folder is
    a denial of service with no attacker skill required.
    """
    os.mkfifo(inbox / "pipe.gpx")

    with open_import_directory(inbox) as directory:
        assert directory is not None
        pipe = next(entry for entry in directory.entries() if entry.name == "pipe.gpx")

        assert directory.read(pipe, LIMIT) is None


# --- Reading stays bounded ----------------------------------------------------


def test_a_candidate_is_read_at_most_one_byte_past_the_limit(inbox: Path) -> None:
    """The limit bounds the read, not only the verdict.

    One byte past the limit is enough to prove a file is over it, and refusing an
    oversized file after loading all of it has already paid the cost the limit
    exists to prevent.
    """
    (inbox / "large.gpx").write_bytes(b"x" * 4096)

    with open_import_directory(inbox) as directory:
        assert directory is not None
        large = next(entry for entry in directory.entries() if entry.name == "large.gpx")

        assert len(directory.read(large, 64) or b"") == 65


def test_a_candidate_that_fails_mid_read_is_skipped_rather_than_fatal(
    inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A device error after a successful open must not abort the sync run.

    The open succeeded and the file was a regular file, so everything the
    boundary checks was satisfied and the read still failed. That is a skipped
    candidate, not a broken scan.
    """

    def fail_the_read(*_: object, **__: object) -> None:
        raise OSError(5, "Input/output error")

    with open_import_directory(inbox) as directory:
        assert directory is not None
        (entry,) = directory.entries()
        monkeypatch.setattr(os, "fdopen", fail_the_read)

        assert directory.read(entry, LIMIT) is None


def test_an_unreadable_candidate_is_skipped_rather_than_fatal(
    inbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that cannot be opened must not abort the whole sync run.

    The failure is injected at the open rather than provoked with ``chmod 000``,
    which root ignores: whether a contract holds must not depend on which user
    runs the suite.
    """
    real_open = os.open

    def refuse(path: object, flags: int, /, *args: object, **kwargs: object) -> int:
        if path == "candidate.gpx":
            raise PermissionError(13, "Permission denied")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    with open_import_directory(inbox) as directory:
        assert directory is not None
        (entry,) = directory.entries()
        monkeypatch.setattr(os, "open", refuse)

        assert directory.read(entry, LIMIT) is None


# --- The source directory is never modified -----------------------------------


def test_reading_a_candidate_leaves_the_directory_untouched(inbox: Path) -> None:
    """The sync folder is input. Nothing in it is written, renamed or deleted."""
    before = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in inbox.iterdir()
    }

    with open_import_directory(inbox) as directory:
        assert directory is not None
        for entry in directory.entries():
            directory.read(entry, LIMIT)

    assert {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in inbox.iterdir()
    } == before


def test_a_hidden_file_is_not_a_candidate(tmp_path: Path) -> None:
    """A sync folder is full of dotfiles, and none of them is a track.

    `.DS_Store`, `.nomedia`, a client's partial download, the `.gitkeep` that
    makes the deployment's import folder exist -- reporting each of them as a
    failed import on every scan is noise, and noise is where a real failure
    goes unnoticed. They are not candidates, which is a different statement
    from "unreadable": nothing is hidden from the summary that was ever offered
    to it.
    """
    (tmp_path / "walk.gpx").write_bytes(b"<gpx/>")
    (tmp_path / ".gitkeep").write_bytes(b"")
    (tmp_path / ".DS_Store").write_bytes(b"junk")

    with open_import_directory(tmp_path) as directory:
        assert directory is not None
        assert [entry.name for entry in directory.entries()] == ["walk.gpx"]
