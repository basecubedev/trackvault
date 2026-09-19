# ADR 0013: Automatic import from the import directory

## Status

Accepted (2026-09-19).

Amends ADR 0003, which chose "an explicit scan" over "a file system watcher" and
left running that scan regularly to the operator — the user guide suggested a
line of host `cron`. The server now runs the scan itself, on an interval. A file
system watcher stays rejected, for the reasons below, and nothing about the
import pipeline, the raw-import authority or the content-hash identity changes:
this adds a caller that runs on a timer, not a path.

## Context

The deployment TrackVault was built for is a phone that syncs recordings into a
folder on the server. Until now a synced ride reached the archive only once
somebody typed `trackvault scan` or set up `cron` on the host — a step outside
the application that most installations never take, that the application cannot
see, and whose failures land in a mailbox or nowhere.

The agent rules already name the shape this should have: import-folder scanning
is background processing, it must be safe to repeat and never import twice, and
application-level scheduling is not OS-level cron.

## Decision

### 1. The server scans on an interval

While the HTTP server runs, one worker thread scans the configured import
directory: once when the server starts, then `TRACKVAULT_IMPORT_SCAN_INTERVAL_MINUTES`
(default 15, one minute to one week) after the previous scan *ended*, so a slow
scan never runs back to back with the next. The bounds are enforced where the
value is configured: an interval longer than a timer can wait for would not
wait at all. `TRACKVAULT_IMPORT_SCAN_ENABLED` (default on) switches it off;
without `TRACKVAULT_IMPORT_DIR` there is nothing to scan either way.

The worker is built once in the service graph and started and stopped by the
application's lifespan, after storage is migrated. `start` refuses a second
thread, and the command line never starts one. Stopping is cooperative: it is
noticed before the next file, so shutdown waits for one import, never for a
folder, and the wait is bounded because a stuck network mount can hold a read.

### 2. One scan, one pipeline

`ImportDirectoryScanner` is the pass `trackvault scan` runs and the pass the
worker runs. Every candidate goes to `ImportTracks` with the input channel
`import_directory`, exactly as before; the scanner adds discovery, never a
verdict. A file becomes the same track whichever way it came in.

### 3. Candidates

A candidate is a visible regular file directly in the import directory whose
suffix an installed adapter declares (`TrackImporter.file_suffixes`; GPX:
`.gpx`), compared case-insensitively. Subdirectories are not descended into,
links are not followed, and everything else is neither read nor stored. The
suffix chooses what is offered; the content still decides what it is. A name is
opened by its exact bytes and stored, reported and logged as readable text:
bytes that are not UTF-8 become U+FFFD, and a log line quotes the name, so it
cannot carry a line of its own.

### 4. Only finished files

A candidate is read once its kernel change time is at least the settle time
old — `TRACKVAULT_IMPORT_SETTLE_MINUTES`, five by default — and only if the open
file is in exactly the state that was looked at before and after the read. The
change time is set by the kernel on every write and rename and cannot be set
back, which a modification time preserved by a copy tool cannot promise. An
empty file is not a document yet either: sync tools create the name before the
content. A file that fails any of these is *waiting* and is taken by a later
scan. `trackvault scan` keeps the before-and-after check but no settle time: an
operator running it has decided the folder is ready.

Five minutes rather than one because of recorders that write into a synced
folder while they record. GPSLogger writes a point a minute by default and keeps
the document valid after every point, so its file is quiet for a minute at a
time, well-formed, and not finished. A snapshot imported during such a pause is
a track of its own, and since raw imports are immutable and tracks cannot be
deleted, a wrong import is permanent while a late one costs minutes. No settle
time can recognise a recording that pauses for longer than it; the setting
exists for those, and the user guide says to sync finished recordings.

### 5. Idempotency rests on the content hash

The duplicate authority stays the raw import's content hash, which is persistent
and therefore survives a restart. A re-offered file is a `duplicate`, a lost
managed copy is `repaired`, exactly as for every other input path.

On top of that, a scanner remembers which state of which name it has already
offered. A later scan skips an unchanged file with a `stat` instead of reading
it, hashing it and verifying the managed copy — the normal case, "nothing new",
costs a directory listing. The memory is a cache and lives in the process: a
restarted server offers every file once more and gets `duplicate` for each.

### 6. Failures are per file

Each candidate has its own outcome, and one failure never stops the files after
it. A failure of the archive rather than of the file — raw storage or database
write — is not remembered, so the next scan retries it. A name that is there but
cannot be looked at — a folder with read but no search permission — is reported
as unreadable rather than taken for absent.

An error nobody anticipated inside one file's import — an adapter defect — is
caught around that file alone, logged with its traceback, reported as a failure
without an error code, and not remembered. Candidates are taken in name order,
so a document that broke the whole pass would stop every pass at the same place
and keep every file after it out for good. This is the same deliberate, narrow
boundary `AnalyzeTrack.best_effort` draws around one track. `trackvault scan`
reports such a file, and a file it could not read, as failed and exits
non-zero.

A folder that cannot be opened at all — a share not mounted yet — is not an
empty folder. The scan reports it unavailable, a warning is logged, and the
memory of offered files is kept, so a share that comes back unchanged is not
read all over again. An error that is
not about one file — the folder cannot be listed — ends that scan, is logged
with its traceback, and the next scan is due one interval later as usual; it
never ends the worker, and a scan is never started while another one runs.

### 7. What the user sees

The worker keeps the last scan and the last scan that imported, repaired or
failed something, and when the next one is due. Both are in memory: they are a
report on this process, not a record. Logging is quiet when a scan found
nothing.

### 8. The folder is still never modified

Imported files stay where they are. Moving or deleting them would make the
folder an output of the archive, and the container mounts it read-only.

## Consequences

Positive:

- A synced ride appears without anybody running anything.
- There is still one import implementation. Manual upload, `trackvault import`,
  `trackvault scan` and the automatic import differ only in how bytes arrive.
- A restart cannot import anything twice, because nothing about idempotency lives
  in the process.

Trade-offs, accepted:

- The server holds a thread for its whole lifetime. It sleeps between scans and
  is a daemon, so it cannot keep a stopped server alive.
- The first scan after a restart reads every file once. That is the price of not
  keeping a second record of what was imported.
- A managed copy lost while the server runs is restored from the folder by the
  first scan after the next restart, or at once by `trackvault scan` — not by
  the next automatic scan, which does not read an unchanged file again.
- A file that arrives within the settle time of a scan waits one more
  interval: with the defaults, a ride is in the archive five to twenty minutes
  after it lands.
- A recorder that pauses longer than the settle time while writing into the
  folder still gets its first half imported as a track of its own. The settle
  time can be raised; the guide says to sync finished recordings.
- The status starts empty after a restart. What was imported is in the archive;
  what the previous process was doing is in its log.
- Running several server processes against one data directory would run several
  workers. TrackVault is deployed as one process; the content hash keeps even
  that from creating a second track, it only wastes work.

## Alternatives considered

- **Host `cron` running `trackvault scan`.** Rejected as the default: it lives
  outside the application, the application cannot report on it, and most
  installations never set it up. It still works, unchanged.
- **A file system watcher.** Rejected again: it misses everything that arrived
  while the server was down, so a scan would be needed anyway; notification is
  unreliable on the network mounts sync folders often live on; and it would be a
  second way of noticing files beside the scan.
- **A persisted ledger of scanned files.** Rejected: a second record of "what
  was imported" beside the raw imports, with its own migration, backup and
  restore semantics, and its own way of disagreeing with the archive after a
  restore. It would save one read per file per restart.
- **Moving imported files into an archive folder.** Deferred. It would change
  the folder from input to output, needs a writable mount, and breaks sync tools
  that put a missing file back. Worth a decision of its own.
