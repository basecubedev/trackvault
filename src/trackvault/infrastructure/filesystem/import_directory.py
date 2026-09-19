"""Scanning a server-side import directory, for example a phone auto-sync target.

The directory is **input, not authority**. Nothing in it is written, renamed,
moved or deleted: the archive copies what it accepts into its own managed raw
storage and leaves the source folder exactly as it found it. If the sync tool
puts a file back, the exact-duplicate check makes the re-import a no-op.

The scan is explicit rather than a file system watcher: it is called from the
command line, it is easy to reason about, and it cannot hold a thread open for
weeks. Every candidate goes through the same ``ImportTracks`` use case as any
other input path.

### Discovery and opening are one authority

What lands in a sync folder is untrusted, and so is what happens to it between
the moment the scan lists a name and the moment the archive opens it:

```
list the directory   -> "this is a regular file"
                     <- something replaces it
open the path        -> whatever is there now gets read
```

A check on a path is a statement about the past. Closing that window means the
open itself has to carry the guarantee, so the root is opened once and every
candidate is opened relative to that descriptor, without following symbolic
links, and what the name turns out to refer to is decided by ``fstat`` on the
open file rather than by the earlier look. A name that has become a link, a
directory or nothing at all is refused rather than followed.

Non-blocking opens matter here too: opening a named pipe for reading waits for a
writer, and a scan that can be stopped indefinitely by dropping a FIFO into the
sync folder is a denial of service with no attacker skill required.

The configured root itself stays trusted and may be a symbolic link -- an
operator who points the import directory somewhere means it. That is the same
boundary the managed raw storage draws.
"""

import logging
import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from trackvault.application.import_tracks import ImportOutcome, ImportRequest, ImportTracks
from trackvault.domain import InputChannel
from trackvault.infrastructure.filesystem.bounded_read import MAX_READ_MARGIN

logger = logging.getLogger(__name__)

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY

# `O_NOFOLLOW` refuses a symbolic link instead of following it. `O_NONBLOCK`
# stops a named pipe or a device node from blocking the open; what the name
# actually refers to is decided afterwards, on the open file.
_ENTRY_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


@dataclass(frozen=True, slots=True)
class ImportDirectoryEntry:
    """One name found directly below the import root.

    A name, not a path. Resolving it into a path would recreate exactly the gap
    this boundary exists to close -- it is only meaningful relative to the
    directory descriptor the scan holds open.

    Attributes:
        name: The file name, which is display metadata and nothing more.
    """

    name: str


class ImportDirectory:
    """An open handle on the import root, through which candidates are read."""

    def __init__(self, descriptor: int) -> None:
        """Hold a descriptor for the directory being scanned."""
        self._descriptor = descriptor

    def entries(self) -> tuple[ImportDirectoryEntry, ...]:
        """Return the names directly below the root, in a stable order.

        Subdirectories are listed but never descended into: what a name refers to
        is decided when it is opened, and a directory is refused there. Sorting
        makes two identical scans see the same files in the same order.

        The listing goes through the held descriptor rather than through a path.
        ``Path.iterdir`` would resolve the root again, which is the resolution
        this boundary exists to do exactly once.

        **A hidden name is not a candidate.** A folder a phone syncs into fills
        up with `.DS_Store`, `.nomedia`, half-written `.part` files and the
        marker that makes the folder exist at all. None of them is a track, and
        reporting each as a failed import on every scan is noise -- which is
        where a real failure goes unnoticed. Not offered is a different
        statement from unreadable, and only the second belongs in a summary.
        """
        names = os.listdir(self._descriptor)  # noqa: PTH208 - the descriptor is the authority
        return tuple(
            ImportDirectoryEntry(name) for name in sorted(names) if not name.startswith(".")
        )

    def read(self, entry: ImportDirectoryEntry, max_bytes: int) -> bytes | None:
        """Return a candidate's bytes, or ``None`` if it must not be read.

        Args:
            entry: A name this directory reported. It is opened relative to the
                held descriptor, so no path is resolved and nothing outside the
                root can be reached.
            max_bytes: The import limit this deployment enforces. One byte past it
                is read, which is enough for the import use case -- the authority
                on the limit -- to refuse the file without a huge one ever being
                held in memory.

        Returns:
            The content, or ``None`` when the name is a symbolic link, a
            directory, something other than a regular file, gone, or unreadable.
            Each of those is a skipped candidate rather than a failed run: a sync
            folder changes under a scan as a matter of course.
        """
        try:
            descriptor = os.open(entry.name, _ENTRY_FLAGS, dir_fd=self._descriptor)
        except OSError as error:
            logger.info(
                "import.skipped name=%s reason=%s", entry.name, error.strerror or "cannot be opened"
            )
            return None
        try:
            # What the name refers to is decided here, on the object that is
            # already open, and not on the earlier listing. A directory, a device
            # node or a named pipe never reaches the read below.
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                logger.info("import.skipped name=%s reason=not_a_regular_file", entry.name)
                return None
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                return handle.read(max_bytes + MAX_READ_MARGIN)
        except OSError as error:
            logger.warning(
                "import.unreadable name=%s reason=%s",
                entry.name,
                error.strerror or "cannot be read",
            )
            return None
        finally:
            os.close(descriptor)


@contextmanager
def open_import_directory(root: Path) -> Iterator[ImportDirectory | None]:
    """Yield a handle on the import root, or ``None`` when there is none.

    The handle stays open for the whole scan on purpose: it is what makes every
    candidate open resolve inside the directory that was actually listed, even if
    the path to it changes meanwhile.

    A missing or unusable import directory is not an error. A sync target that
    does not exist yet simply has nothing to offer.
    """
    try:
        descriptor = os.open(root, _DIRECTORY_FLAGS)
    except OSError as error:
        logger.info("import.directory_unavailable reason=%s", error.strerror or "cannot be opened")
        yield None
        return
    try:
        yield ImportDirectory(descriptor)
    finally:
        os.close(descriptor)


def scan_import_directory(
    directory: Path, import_tracks: ImportTracks
) -> Sequence[tuple[str, ImportOutcome]]:
    """Offer every file in the directory to the canonical import use case.

    Args:
        directory: The directory to read. It is never modified.
        import_tracks: The single import use case every input path shares. Its
            own byte limit bounds how much of each file is read, so the scan can
            never bound differently from what is enforced.

    Returns:
        One ``(filename, outcome)`` pair per file that could be read, in scan
        order. A file that could not be read does not stop the ones after it.
        A name no installed adapter is exchanged under is not a candidate at
        all: it is neither read nor reported, and stays where it is.
    """
    max_bytes = import_tracks.limits.max_bytes
    suffixes = tuple(import_tracks.file_suffixes)
    results = []
    with open_import_directory(directory) as inbox:
        if inbox is None:
            return []
        for entry in inbox.entries():
            if not entry.name.lower().endswith(suffixes):
                continue
            content = inbox.read(entry, max_bytes)
            if content is None:
                continue
            results.append(
                (
                    entry.name,
                    import_tracks(
                        ImportRequest(
                            content=content,
                            original_filename=entry.name,
                            input_channel=InputChannel.IMPORT_DIRECTORY,
                        )
                    ),
                )
            )
    return results
