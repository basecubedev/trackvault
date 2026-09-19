# Importing and exporting tracks

Three ways in, one pipeline behind them. GPX is currently the exchange format
TrackVault reads and writes; the archive model itself is format-independent, and
a second format is an adapter rather than a second pipeline.

## The three input paths

**From the browser.** Open **Tracks** and press *Import files*. Pick one file or
twenty; each one is offered on its own and gets its own answer — imported,
already in the archive, or not imported with the reason in words. Nothing is
guessed: a file the archive cannot read is named, and the rest still go in.

**A watched folder.** Drop files into `import/` — the folder mounted read-only
at `/import` — and TrackVault imports them on its own: when it starts, and every
15 minutes after that (see [Automatic import](#automatic-import)). To import
right away, run `docker compose exec trackvault trackvault scan`. This is the path
for a phone that auto-syncs to the server, and for the hundred files you are not
going to pick in a file dialog. **The folder is never modified**:
nothing in it is written, renamed, moved or deleted. Only files named like a
format TrackVault reads are offered — today that is `*.gpx`, in any case
(`.gpx`, `.GPX`, `.Gpx`). Hidden files and everything else are left alone rather
than reported as failures: a synced folder is full of `.DS_Store`, photos and
half-written downloads, and none of them is a track. The suffix only decides
what is offered; whether a file really is GPX is still decided by its content.

**On the machine itself**, with the commands below.

> **Uploading from the browser is on until you switch it off.** It is the one
> endpoint that lets a caller make the server write, and nothing here
> authenticates — so anyone who can reach the port can add files, not only read
> them. Set `TRACKVAULT_UPLOAD_ENABLED=false` in `.env` if your network does not
> make that acceptable; every read works either way. See
> [Configuration and security](configuration.md).

## The commands

```bash
trackvault import path/to/track.gpx another.gpx
trackvault scan                        # imports new files from TRACKVAULT_IMPORT_DIR, now
trackvault reprocess <sha256>          # normalize a stored source again
trackvault reprocess --failed          # ...every source whose last attempt failed
trackvault reprocess --outdated        # ...every source an upgrade would change
trackvault processing-status <sha256>  # what happened to one source
trackvault analyze --outdated          # derive the metrics that are behind
trackvault analyze --all               # ...for every current track
trackvault analyze <track_id>          # ...for one
trackvault export raw <sha256>         # the original file, byte-identical
trackvault export track <track_id>     # a generated GPX 1.1 document
trackvault backup create               # the whole archive, in one file
trackvault backup list                 # what is in the backup directory
trackvault restore <archive> --dry-run # what restoring it would do
trackvault restore <archive>           # do it
trackvault doctor                      # what is wrong with this deployment
```

Inside the container, prefix them with `docker compose exec trackvault`. Outside
it, with `uv run`.

`import` and `scan` go through the same use case. Importing the same bytes twice
is a no-op — as long as the archive can still produce the bytes it says it
holds. If the managed copy was lost, offering the same file again restores it
and reports `repaired`; if the copy is there but no longer matches its hash, the
import fails rather than overwriting the evidence.

`scan` gives every `.gpx` file in the folder a line of its own: what the import
made of it, or `unreadable`, `error` or `waiting` when there is no verdict.
`waiting` is a file that changed while it was being read; the next scan takes it,
and it does not count as a failure. An empty file gets no line: it is not a
document yet. Anything else without a
verdict makes `scan` exit non-zero, like a failed import.

`reprocess` answers a different question. Recognising a content hash makes an
import a duplicate; whether the *normalized* data of those bytes is still
current is not the same statement. After an importer or classifier upgrade — or
once a file that could not be read becomes readable — reprocessing regenerates
the normalized data from the archive's own byte-identical copy. It keeps the
source untouched, keeps every earlier processing run, and never overwrites a
classification or a title you corrected yourself. Nothing reprocesses itself.

## Syncing from your phone

The usual setup. Your phone records tracks, something syncs them to a folder on
the server, and the archive reads that folder. You never have to know a path
inside the container.

```
Locus Map (or any recorder)
        ↓  AutoSync / Syncthing / Nextcloud / rsync
host folder, e.g. ~/sync/locus
        ↓  read-only mount
/import inside the container
        ↓  TRACKVAULT_IMPORT_DIR
automatic import, every 15 minutes   (or: trackvault scan, right now)
        ↓
ImportTracks  →  archive
```

**1. Install TrackVault.** It creates an `import/` folder for you.

**2. Decide where the phone syncs to.** Either sync straight into the `import/`
folder the installer made, or point TrackVault at a folder you already have:

```bash
# in .env
TRACKVAULT_IMPORT_PATH=/home/you/sync/locus
```

```bash
docker compose up -d          # apply it
```

Only the host side changes. Inside the container the folder is always `/import`,
which is why nothing else has to be reconfigured.

**3. Set your phone to sync there.** In Locus Map that is *Settings → Backup &
Sync*, or an export target on a shared folder; with Syncthing or Nextcloud it is
whatever you already use. TrackVault does not care which one — it reads a folder.

**4. Check the archive can see it:**

```bash
docker compose exec trackvault trackvault doctor
```

`import_directory` should say `configured and readable`. If it says
`TRACKVAULT_IMPORT_DIR is not set`, the container was started without the setting;
if it says it cannot be read, the mount is pointing somewhere that does not
exist on the host.

**5. Wait for the next scan, or import what is there right now:**

```bash
docker compose exec trackvault trackvault scan
```

```
imported  a95629ddc1ae tracks=1  morning-ride.gpx
duplicate 4f2b91c00de1            yesterday.gpx
```

Every file gets its own answer. Run it as often as you like — a file already in
the archive is recognised by its content and skipped, so a folder your phone
never clears does not become forty copies of one ride.

**6. Open the browser.** The tracks are there.

### Automatic import

As long as TrackVault is running and an import directory is configured, it reads
that folder on its own: once when it starts, then 15 minutes after the previous
scan finished. There is nothing to set up — no `cron`, no command.

- **What is picked up:** files ending in `.gpx`, in any case, directly in the
  folder. Subfolders, hidden files and everything else are left alone.
- **Only finished files.** A file is imported once nothing has changed it for
  five minutes, and only if it does not change while it is being read. A file
  your sync tool is still copying is *waiting* and is taken by a later scan,
  never imported half way. An empty file it created ahead of the content is
  left alone until the content is there.
- **Sync finished recordings.** A recorder that writes into the synced folder
  *while* it records produces a file that grows with pauses in between. If a
  pause lasts longer than the settle time, the first half is imported as a track
  of its own, and the whole recording as a second one later. Locus AutoSync and
  similar tools sync a track once it is saved and are not affected; for a
  recorder that is, raise `TRACKVAULT_IMPORT_SETTLE_MINUTES` above its longest
  pause, or let it write elsewhere and move finished files in.
- **Never twice.** A file is recognised by its content, the same way as every
  other import. A folder your phone never clears, a restart, or the same ride
  under two names all end in one track. Files already imported cost almost
  nothing: an unchanged file is not even read again until TrackVault restarts.
- **One broken file is one broken file.** It is named on the **Tracks** page
  with the reason, and the files after it are imported as usual. So is a file
  the server is not allowed to read. A file that failed because the archive
  itself could not be written — a full disk, say — is tried again on the next
  scan.

The top of the **Tracks** page says whether the automatic import is on, which
folder it reads, when it last read it and what came of that — "nothing new" for
most scans — and lists every file it could not import, with the reason, until a
later scan imports or fails something else.

Three settings, all optional:

```bash
# in .env
TRACKVAULT_IMPORT_SCAN_INTERVAL_MINUTES=5    # scan more often (1 to 10080)
TRACKVAULT_IMPORT_SETTLE_MINUTES=15          # wait longer for a file to finish
TRACKVAULT_IMPORT_SCAN_ENABLED=false         # only import when you run `scan`
```

`trackvault scan` keeps working whether or not the automatic import is on — use
it when you do not want to wait. It does not wait for a file to settle: running
it is you saying the folder is ready.

### The folder is never modified

Nothing in the import directory is written, renamed, moved or deleted. The mount
is read-only as well, which is the second, independent reason: it stays true
even if the first is ever wrong. Your sync folder is input, and the archive
keeps its own byte-identical copy of everything it accepts.

## Getting data back out

Three different things wear the word "export", so they are three commands.

### Your original file, unchanged

```bash
docker compose exec -T trackvault trackvault export raw <sha256> > my-ride.gpx
```

Byte-identical to what you imported. The archive verifies the bytes against the
hash they are filed under before handing them over, so you get the original or a
named error — never something in between. The content hash is what `scan`,
`import` and the track page all show.

### A track as a fresh GPX file

```bash
docker compose exec -T trackvault trackvault export track 42 > track-42.gpx
```

This is **not** your original file. It is GPX 1.1 generated from what the
archive currently makes of the track: the title you gave it, the activity, the
segments, the positions, and elevation, timestamps and sensor readings where
they exist. Use it to get a track into another application; use `export raw`
when you want the file you imported.

It deliberately carries no "recorded" or "planned" marker. That is a conclusion
this archive reached from evidence, at a confidence, with a rule version — GPX
has no field that means it, and inventing one would publish a claim the
receiving application could not evaluate.

### Everything at once

That is a backup. See [Backup and restore](backup-and-restore.md).
