"""Reading the import directory on an interval, for as long as the server runs.

A phone that syncs into a folder should not need somebody to type
``trackvault scan`` afterwards. So the server runs one worker thread that scans
the folder once when it starts and again every interval after the previous scan
ended:

```
start ──scan──┐         ┌──scan──┐         ┌──scan── ...
              └─interval┘        └─interval┘
```

**Scheduling, not watching.** The worker is a loop around an ordinary pass of
:class:`~trackvault.infrastructure.filesystem.ImportDirectoryScanner`, the same
one ``trackvault scan`` runs. A file system watcher would be a second way of
noticing files, would miss everything that arrived while the server was down,
and does not work on the network mounts sync folders tend to live on. A pass
does the same thing on its first run after a restart as on its hundredth.

**One worker per application.** The composition root builds exactly one and
starts it with the application's lifespan, and :meth:`AutomaticImport.start`
refuses a second thread. The command line never starts it.

**Stopping is cooperative.** A stop is noticed before the next file, so shutting
down waits for the import in hand -- one transaction -- and never for the whole
folder. The thread is a daemon and the wait for it is bounded: a sync folder on a
network mount that has stopped answering can hold a read indefinitely, and that
must not hold the server's shutdown with it.
"""

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from trackvault.application.import_scan import AutomaticImportStatus, ImportScan
from trackvault.application.import_tracks import ImportStatus, ImportTracks
from trackvault.application.ports import Clock
from trackvault.infrastructure.filesystem import ImportDirectoryScanner

logger = logging.getLogger(__name__)

WORKER_THREAD_NAME = "automatic-import"

SHUTDOWN_GRACE = timedelta(seconds=30)
"""How long stopping waits for the file in hand before giving up on the thread."""


class AutomaticImport:
    """Scans the import directory on an interval while the server runs.

    Args:
        directory: The configured import directory, or ``None``.
        enabled: Whether automatic import is switched on. It runs only when it
            is and a directory is configured.
        import_tracks: The one import use case every input path shares.
        interval: Time between the end of one scan and the start of the next.
        clock: Decides when a scan is due and when a file has settled.
        settle_time: How long a file must have been left alone to be read. The
            default and its reasoning live in the configuration.
    """

    def __init__(
        self,
        *,
        directory: Path | None,
        enabled: bool,
        import_tracks: ImportTracks,
        interval: timedelta,
        clock: Clock,
        settle_time: timedelta,
    ) -> None:
        """Wire the worker; nothing is scanned and no thread runs until ``start``."""
        self._directory = directory
        self._interval = interval
        self._settle_time = settle_time
        self._clock = clock
        self._scanner = (
            ImportDirectoryScanner(directory, import_tracks, clock=clock, settle_time=settle_time)
            if enabled and directory is not None
            else None
        )
        self._guard = threading.Lock()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._scanning = False
        self._last_scan: ImportScan | None = None
        self._last_activity: ImportScan | None = None
        self._next_scan_at: datetime | None = None

    def start(self) -> None:
        """Begin scanning in the background: once now, then every interval.

        Does nothing when automatic import is off.

        Raises:
            RuntimeError: When this worker was already started, even if it has
                been stopped since. Two loops over one folder would race each
                other for every file in it, and an application's lifespan
                starts its worker once.
        """
        if self._scanner is None:
            logger.info("automatic_import.disabled")
            return
        with self._guard:
            if self._thread is not None:
                raise RuntimeError("the automatic import was already started")
            self._thread = threading.Thread(target=self._run, name=WORKER_THREAD_NAME, daemon=True)
        logger.info(
            "automatic_import.started interval_minutes=%d",
            self._interval // timedelta(minutes=1),
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop scanning, and wait for the file in hand. Safe to call twice.

        A stopped worker stays stopped.
        """
        self._stopping.set()
        with self._guard:
            thread = self._thread
        if thread is None:
            return
        thread.join(timeout=SHUTDOWN_GRACE.total_seconds())
        if thread.is_alive():
            logger.warning("automatic_import.stop_timed_out")

    def run_due(self) -> ImportScan | None:
        """Scan now if a scan is due, and schedule the next one.

        Returns:
            The scan, or ``None`` when none was due, one is already running,
            the import is off, or it has been stopped.
        """
        if self._scanner is None or self._stopping.is_set():
            return None
        with self._guard:
            if self._scanning:
                return None
            if self._next_scan_at is not None and self._clock.now() < self._next_scan_at:
                return None
            self._scanning = True
        logger.debug("automatic_import.scan_started")
        try:
            scan = self._scanner.scan(stopping=self._stopping.is_set)
        finally:
            # Scheduled even when the scan broke, so an archive that cannot be
            # written right now is retried an interval later, not in a loop.
            with self._guard:
                self._scanning = False
                self._next_scan_at = self._clock.now() + self._interval
        with self._guard:
            self._last_scan = scan
            if scan.had_activity:
                self._last_activity = scan
        _log_completed(scan)
        return scan

    def status(self) -> AutomaticImportStatus:
        """Return what the automatic import is doing, right now."""
        with self._guard:
            return AutomaticImportStatus(
                enabled=self._scanner is not None,
                directory=None if self._directory is None else str(self._directory),
                interval=self._interval,
                settle_time=self._settle_time,
                scanning=self._scanning,
                last_scan=self._last_scan,
                last_activity=self._last_activity,
                next_scan_at=self._next_scan_at,
            )

    def _run(self) -> None:
        """Scan whenever a scan is due, until asked to stop.

        The one broad handler in this module, and deliberately so. A background
        thread has no caller to hand an error to: an unexpected one would end
        automatic import silently for the life of the process. It is logged with
        its traceback instead, and the next scan is due as usual.
        """
        while not self._stopping.is_set():
            try:
                self.run_due()
            except Exception:
                logger.exception("automatic_import.scan_failed")
            self._stopping.wait(self._seconds_until_due())

    def _seconds_until_due(self) -> float:
        """Return how long the worker may sleep before the next scan is due."""
        with self._guard:
            due = self._next_scan_at
        if due is None:
            return 0.0
        return max(0.0, (due - self._clock.now()).total_seconds())


def _log_completed(scan: ImportScan) -> None:
    """Summarise a scan: noticeably when it did something, quietly otherwise."""
    if scan.unavailable:
        logger.warning("automatic_import.directory_unavailable")
        return
    level = logging.INFO if scan.had_activity or scan.waiting else logging.DEBUG
    logger.log(
        level,
        "automatic_import.scan_completed discovered=%d imported=%d repaired=%d "
        "skipped=%d failed=%d waiting=%d",
        scan.discovered,
        scan.count(ImportStatus.IMPORTED),
        scan.count(ImportStatus.REPAIRED),
        scan.skipped,
        len(scan.failures),
        len(scan.waiting),
    )
