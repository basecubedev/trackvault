"""Scanning a server-side import directory, for example a phone auto-sync target.

The directory is **input, not authority**. Nothing in it is written, renamed,
moved or deleted: the archive copies what it accepts into its own managed raw
storage and leaves the source folder exactly as it found it. If the sync tool
puts a file back, the exact-duplicate check makes the re-import a no-op.

A scan is a pass, not a file system watcher: something runs it, it reads the
folder once, and it ends. Every candidate goes through the same ``ImportTracks``
use case as any other input path.

### Only finished files

A sync tool copies a file in pieces, and a pass may look at it half way. Half a
GPX document is not a shorter track; it is either unreadable or, worse, a
readable prefix. So a candidate is read only once its change time is at least
the settle time old -- the kernel sets that time on every write and rename, and
nothing can set it back -- and only if the open file is still in exactly the
state that was looked at, before and after the read. Anything else is
*waiting*, and a later pass takes it.

### Remembering is a cache

A scanner remembers which state of which name it has offered, so the next pass
skips an unchanged file without reading it. The memory is the process's own and
is never the duplicate authority: a restarted server offers every file once more,
and the archive's content hash answers ``duplicate`` for each.

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

Names are untrusted as well. They are logged quoted, so a name carrying a line
break cannot write a log line of its own.
"""

import logging
import os
import stat
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trackvault.application.errors import ImportErrorCode
from trackvault.application.import_scan import ImportScan
from trackvault.application.import_tracks import (
    ImportOutcome,
    ImportRequest,
    ImportStatus,
    ImportTracks,
)
from trackvault.application.ports import Clock
from trackvault.domain import InputChannel
from trackvault.infrastructure.clock import SystemClock
from trackvault.infrastructure.filesystem.bounded_read import MAX_READ_MARGIN

logger = logging.getLogger(__name__)

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY

# `O_NOFOLLOW` refuses a symbolic link instead of following it. `O_NONBLOCK`
# stops a named pipe or a device node from blocking the open; what the name
# actually refers to is decided afterwards, on the open file.
_ENTRY_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

_NANOSECONDS = 1_000_000_000

# Failures of the archive rather than of the file. The same bytes may well
# succeed on the next pass, so they are not remembered as offered.
_RETRIED_FAILURES = frozenset(
    {ImportErrorCode.RAW_STORAGE_FAILED, ImportErrorCode.PERSISTENCE_FAILED}
)


@dataclass(frozen=True, slots=True)
class ImportDirectoryEntry:
    """One name found directly below the import root.

    A name, not a path. Resolving it into a path would recreate exactly the gap
    this boundary exists to close -- it is only meaningful relative to the
    directory descriptor the scan holds open.

    Attributes:
        name: The file name exactly as the directory holds it. What it is
            opened by, and nothing else.
    """

    name: str

    @property
    def display_name(self) -> str:
        """Return the name as text that can be stored, logged and shown.

        A file system name is bytes. One that is not UTF-8 -- an old Windows
        share writes Latin-1 -- arrives with the undecodable bytes carried as
        surrogates, which a database, a JSON response and a log file all refuse.
        They become U+FFFD here; :attr:`name` still opens the file.
        """
        return self.name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


@dataclass(frozen=True, slots=True)
class FileState:
    """What a regular file looked like at one moment, enough to notice a change.

    The change time carries the guarantee. The kernel sets it on every write,
    rename and metadata change and nothing can set it back, so an equal state
    means nothing was written in between -- which a modification time, which
    any copy tool may set to whatever it likes, could not promise.
    """

    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def of(cls, result: os.stat_result) -> "FileState":
        """Return the state a ``stat`` call reported."""
        return cls(
            device=result.st_dev,
            inode=result.st_ino,
            size=result.st_size,
            modified_ns=result.st_mtime_ns,
            changed_ns=result.st_ctime_ns,
        )

    @property
    def changed_at(self) -> datetime:
        """Return when the file system last changed the file."""
        return datetime.fromtimestamp(self.changed_ns / _NANOSECONDS, UTC)


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

    def state(self, entry: ImportDirectoryEntry) -> FileState | None:
        """Return what a name refers to now, or ``None`` unless it is a regular file.

        A look, not a read: nothing is opened and a symbolic link is not
        followed. It decides whether a candidate is worth opening; what is
        actually read is still decided by :meth:`read`, on the open file.

        Raises:
            OSError: When the name is there but cannot be looked at -- a folder
                that lists its names and refuses to show them. Treating that as
                "gone" would make an unreadable folder look like an empty one.
        """
        try:
            result = os.stat(entry.name, dir_fd=self._descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        return FileState.of(result) if stat.S_ISREG(result.st_mode) else None

    def read(
        self,
        entry: ImportDirectoryEntry,
        max_bytes: int,
        expected: FileState | None = None,
    ) -> bytes | None:
        """Return a candidate's bytes, or ``None`` if it must not be read.

        Args:
            entry: A name this directory reported. It is opened relative to the
                held descriptor, so no path is resolved and nothing outside the
                root can be reached.
            max_bytes: The import limit this deployment enforces. One byte past it
                is read, which is enough for the import use case -- the authority
                on the limit -- to refuse the file without a huge one ever being
                held in memory.
            expected: The state the caller looked at. When given, the bytes are
                returned only if the open file is in exactly that state before
                and after the read, so a file still being written never yields
                half of itself.

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
                "import.skipped name=%r reason=%s",
                entry.display_name,
                error.strerror or "cannot be opened",
            )
            return None
        try:
            # What the name refers to is decided here, on the object that is
            # already open, and not on the earlier listing. A directory, a device
            # node or a named pipe never reaches the read below.
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                logger.info("import.skipped name=%r reason=not_a_regular_file", entry.display_name)
                return None
            if expected is not None and FileState.of(opened) != expected:
                logger.info("import.waiting name=%r reason=changed_before_read", entry.display_name)
                return None
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = handle.read(max_bytes + MAX_READ_MARGIN)
            if expected is not None and FileState.of(os.fstat(descriptor)) != expected:
                logger.info("import.waiting name=%r reason=changed_while_read", entry.display_name)
                return None
            return content
        except OSError as error:
            logger.warning(
                "import.unreadable name=%r reason=%s",
                entry.display_name,
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


def _never() -> bool:
    """Return that a pass is not being asked to stop."""
    return False


@dataclass(slots=True)
class _Findings:
    """What one pass has found so far."""

    seen: set[str] = field(default_factory=set)
    offered: list[tuple[str, ImportOutcome]] = field(default_factory=list)
    unchanged: int = 0
    waiting: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    crashed: list[str] = field(default_factory=list)


class ImportDirectoryScanner:
    """Offers the import directory's finished candidates to the import use case.

    One scanner serves one process for as long as it scans, and remembers which
    state of each name it has offered: an unchanged file costs a ``stat`` on the
    next pass rather than a read, a hash and an integrity check. The memory is a
    cache and never the duplicate authority -- see the module documentation.

    Args:
        directory: The import root. Opened afresh for every pass.
        import_tracks: The one import use case. Its limit bounds every read, and
            its installed adapters decide which names are candidates.
        clock: When a pass happens, measured against a file's change time.
        settle_time: How long a file must have been left alone before it is
            read. Zero trusts the caller's timing, which is what an operator
            running ``trackvault scan`` by hand has already decided.
    """

    def __init__(
        self,
        directory: Path,
        import_tracks: ImportTracks,
        *,
        clock: Clock,
        settle_time: timedelta = timedelta(0),
    ) -> None:
        """Point the scanner at a directory; nothing is read until a pass."""
        self._directory = directory
        self._import_tracks = import_tracks
        self._clock = clock
        self._settle_time = settle_time
        self._offered: dict[str, FileState] = {}

    def scan(self, stopping: Callable[[], bool] = _never) -> ImportScan:
        """Offer every finished candidate this scanner has not offered as it is now.

        Args:
            stopping: Asked before each candidate. A pass told to stop ends after
                the file in hand, so shutting down never waits for a whole folder
                and never interrupts an import half way.

        Returns:
            What the pass found. One file's failure is one entry in it; it never
            stops the files after it.
        """
        started_at = self._clock.now()
        findings = _Findings()
        with open_import_directory(self._directory) as inbox:
            if inbox is None:
                # Nothing was seen, so nothing is forgotten: a share that comes
                # back unchanged is not read all over again.
                return ImportScan(
                    started_at=started_at, finished_at=self._clock.now(), unavailable=True
                )
            complete = self._pass(inbox, started_at, stopping, findings)
        if complete:
            # A name that has gone is forgotten, so the memory never outgrows
            # the folder and a file put back later is offered again.
            self._offered = {
                name: state for name, state in self._offered.items() if name in findings.seen
            }
        return ImportScan(
            started_at=started_at,
            finished_at=self._clock.now(),
            offered=tuple(findings.offered),
            unchanged=findings.unchanged,
            waiting=tuple(findings.waiting),
            unreadable=tuple(findings.unreadable),
            crashed=tuple(findings.crashed),
        )

    def _pass(
        self,
        inbox: ImportDirectory,
        started_at: datetime,
        stopping: Callable[[], bool],
        findings: _Findings,
    ) -> bool:
        """Consider every candidate in turn; return whether none was left out."""
        suffixes = tuple(self._import_tracks.file_suffixes)
        for entry in inbox.entries():
            if not entry.name.lower().endswith(suffixes):
                continue
            if stopping():
                return False
            self._consider(inbox, entry, started_at, findings)
        return True

    def _consider(
        self,
        inbox: ImportDirectory,
        entry: ImportDirectoryEntry,
        started_at: datetime,
        findings: _Findings,
    ) -> None:
        """Decide what one candidate is, and offer it once it is ready.

        The memory is keyed by the name the file is opened by; everything that
        is stored, reported or logged uses the name as readable text.
        """
        label = entry.display_name
        try:
            state = inbox.state(entry)
        except OSError as error:
            _unreadable(label, error, findings)
            return
        if state is None:
            logger.debug("import.skipped name=%r reason=not_a_regular_file", label)
            return
        findings.seen.add(entry.name)
        if self._offered.get(entry.name) == state:
            findings.unchanged += 1
            return
        logger.debug("import.discovered name=%r", label)
        if state.size == 0:
            # Sync tools create the name before the content, so an empty file is
            # not a document yet. Offering it would store a raw import of
            # nothing, which the upload endpoint refuses for the same reason.
            logger.info("import.waiting name=%r reason=empty", label)
            findings.waiting.append(label)
            return
        if self._settle_time and started_at - state.changed_at < self._settle_time:
            logger.info("import.waiting name=%r reason=recently_changed", label)
            findings.waiting.append(label)
            return
        content = inbox.read(entry, self._import_tracks.limits.max_bytes, expected=state)
        if content is None:
            try:
                now = inbox.state(entry)
            except OSError as error:
                _unreadable(label, error, findings)
                return
            if now is not None and now != state:
                findings.waiting.append(label)
            elif now is not None:
                findings.unreadable.append(label)
            return
        request = ImportRequest(
            content=content,
            original_filename=label,
            input_channel=InputChannel.IMPORT_DIRECTORY,
        )
        try:
            outcome = self._import_tracks(request)
        # Deliberately broad, and around one file only. Candidates are taken in
        # name order, so a document that breaks an adapter would otherwise stop
        # every pass at the same place and keep every file after it out for
        # good. The error is logged with its traceback, reported as a failure,
        # and not remembered: the next pass tries the file again.
        except Exception:
            logger.exception("import.crashed name=%r", label)
            findings.crashed.append(label)
            return
        findings.offered.append((label, outcome))
        if outcome.error_code not in _RETRIED_FAILURES:
            self._offered[entry.name] = state
        _log_offered(label, outcome)


def _unreadable(label: str, error: OSError, findings: _Findings) -> None:
    """Record a candidate that is there but cannot be looked at."""
    logger.warning(
        "import.unreadable name=%r reason=%s", label, error.strerror or "cannot be looked at"
    )
    findings.unreadable.append(label)


def _log_offered(name: str, outcome: ImportOutcome) -> None:
    """Connect a file name to what the import made of it.

    The use case logs duplicates and repairs itself, by content hash; what it
    cannot know is which file of the folder that hash came from.
    """
    if outcome.status is ImportStatus.IMPORTED:
        logger.info(
            "import.accepted name=%r raw_import=%s tracks=%d",
            name,
            outcome.sha256[:12],
            len(outcome.track_ids),
        )
    elif outcome.status is ImportStatus.FAILED:
        logger.info(
            "import.rejected name=%r raw_import=%s reason=%s",
            name,
            outcome.sha256[:12],
            "unknown" if outcome.error_code is None else outcome.error_code.value,
        )


def scan_import_directory(
    directory: Path, import_tracks: ImportTracks
) -> Sequence[tuple[str, ImportOutcome]]:
    """Offer every file in the directory to the canonical import use case, once.

    One pass of a fresh :class:`ImportDirectoryScanner` without a settle time:
    whoever runs it by hand has already decided the folder is ready.

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
    return list(
        ImportDirectoryScanner(directory, import_tracks, clock=SystemClock()).scan().offered
    )
