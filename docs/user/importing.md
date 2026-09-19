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
at `/import` — and run `docker compose exec trackvault trackvault scan`. This is the
path for a phone that auto-syncs to the server, and for the hundred files you
are not going to pick in a file dialog. **The folder is never modified**:
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
trackvault scan                        # imports new files from TRACKVAULT_IMPORT_DIR
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
trackvault scan  →  ImportTracks  →  archive
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

**5. Import what is there:**

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

### Running the scan regularly

There is deliberately no file system watcher. A scan is explicit, cannot hold a
thread open for weeks, and is easy to reason about. If you want it automatic,
that is one line of `cron` on the host:

```cron
*/30 * * * * cd /home/you/trackvault && docker compose exec -T trackvault trackvault scan >/dev/null
```

`scan` exits non-zero if any file failed, so a cron mail means something real.

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
