# Backup, restore and checking the archive

## What is irreplaceable

```
the database          every correction, title and derived metric   irreplaceable
the managed raw store your original files                          irreplaceable
maps/                 installed offline maps                       re-downloadable
```

`trackvault backup create` takes the two that matter and states that it left the
third. The raw store alone could be re-imported, but every title you corrected,
every classification you fixed and every derived metric is in the database; the
database alone describes files it no longer has.

One caveat, so the choice is informed: a provider serves the *current* build of
a region. Reinstalling a map later gives you today's data rather than the
dataset you had. For a basemap that is almost always what you wanted.

## Backup

```bash
docker compose exec trackvault trackvault backup create
```

```
created:   /backups/trackvault-20260810-164301.tar.gz
size:      5482 bytes
taken at:  2026-08-10T16:43:01+00:00
release:   0.1.0
schema:    10
sources:   1
tracks:    1
overrides: 0
notes:     0
omits:     map_packages (offline maps are public datasets that can be downloaded again)
```

The file lands in `backups/` beside your compose file, where you can copy it
somewhere else — which is the only thing that makes it a backup. It holds:

```
the database          every track, correction, title and derived metric
every original file   byte-identical
a manifest            what is in it, a checksum for each part, and what is not
```

**What it leaves out, and says so:** installed offline maps. They are public
datasets you can download again and they are by far the largest thing in the
archive. The manifest states the omission rather than staying quiet about it,
because "complete" and "complete except for the part nobody mentioned" are
different promises.

**Why not just `tar czf data/`?** Because that produces a database that may not
open. Write-ahead logging means the `.sqlite3` file on its own is incomplete,
and a copy taken while anything is writing can mix pages from before and after a
transaction. `backup create` uses SQLite's own online backup instead, checks
every stored original against its hash, and then **reads the finished archive
back through the restore validation** — so a backup that reports success is one
that has already been proved restorable.

```bash
docker compose exec trackvault trackvault backup list
```

There is no automatic or scheduled backup. Adding one would be a feature with
its own failure modes, and an archive that silently stopped backing itself up
would be worse than one that never claimed to. A cron line does it:

```cron
0 3 * * 0 cd /home/you/trackvault && docker compose exec -T trackvault trackvault backup create
```

> **A backup is your whole movement history in one file.** Everywhere you have
> been, when, and for how long. Treat it exactly as carefully as you would treat
> the archive itself — and more carefully than the machine it is copied to.

## Restore

Look before you leap:

```bash
docker compose exec trackvault trackvault restore /backups/trackvault-20260810-164301.tar.gz --dry-run
```

```
archive:       trackvault-archive v1
taken at:      2026-08-10T16:43:01+00:00
written by:    TrackVault 0.1.0
schema:        10
compatibility: supported
sources:       1
tracks:        1
analysed:      1
overrides:     0
notes:         0
omits:         map_packages (offline maps are public datasets that can be downloaded again)
target:        /data
target holds data: no
needs:         161281 bytes
```

`trackvault-archive` is the archive format's own identifier: what the file says
about itself, and what the restore compares before it reads a single byte
further. An archive that names a different format is refused rather than
guessed at.

A dry run changes nothing at all, and exits non-zero if the archive could not be
restored — so it works in a script as well as on a screen.

Then do it. **Restoring wants the archive to itself**: it replaces the database
and the stored originals, and an import running at the same time would be
writing into storage that is moving out from under it. Backing up is the
opposite and needs no such care — it is a read, and it runs happily while you
use the archive.

```bash
docker compose stop trackvault
docker compose run --rm trackvault trackvault restore /backups/trackvault-20260810-164301.tar.gz
docker compose start trackvault
```

**Nothing is touched until everything has been checked.** The manifest, the
format version, the schema compatibility, every checksum, and the restored
database's own integrity check all pass before a single file moves. A damaged
archive costs you nothing to attempt.

**An archive that already holds tracks is never replaced silently.** Restoring
over one needs `--replace`, which you have to type. Restoring into an empty
archive — a new machine, or one you have just lost — needs nothing extra.

```bash
trackvault restore <archive> --replace            # replace what is there
trackvault restore <archive> --into /some/other   # somewhere else entirely
```

**A restore never claims a map it does not have.** Map packages are not in the
archive — they are public data you can download again — but their metadata is,
because the whole database is. So the rows come back, and what happens to each
one depends on what the machine already has:

| The machine has | After restoring |
| --- | --- |
| the same package | it works immediately, with nothing downloaded |
| no package | the entry reads `invalid`, and `doctor` tells you to reinstall it |
| a *different* package | it belonged to the database you just replaced, and the next start clears it |

You will never be shown a map that is "installed" and draws nothing.

**If a restore is interrupted** — the container is killed, the machine loses
power — the next start undoes it and leaves the archive you had. A restore
either completed or did not happen; there is no half-restored state to untangle,
and `doctor` reports one that has not been resolved yet.

### Restoring onto a new machine

```bash
mkdir trackvault && cd trackvault
sh install-docker.sh --no-start
cp /media/backup/trackvault-20260810-164301.tar.gz backups/
docker compose up -d
docker compose exec trackvault trackvault restore /backups/trackvault-20260810-164301.tar.gz
docker compose restart
```

### Which backups this version can read

| The backup's schema | What happens |
| --- | --- |
| the same as this build | restores as it is |
| older than this build | restores, then migrates when the archive next starts |
| newer than this build | **refused** — upgrade TrackVault first |

The last row is the same rule the database itself applies: a build that does not
know what the newer columns mean must not open them and guess.

## Checking the archive

```bash
docker compose exec trackvault trackvault doctor
```

```
ok       release              TrackVault 0.1.0, in a container
ok       data_directory       present and writable
ok       database             present and readable
ok       database_integrity   passed
ok       schema               version 10
ok       raw_storage          412 original(s), all matching their hash
ok       import_directory     configured and readable
warning  backups              no backups found; `trackvault backup create` writes one
ok       web_assets           present
ok       maps                 2 installed
ok       processing_profile   gpx@2
ok       analysis_profile     distance@1, movement@2, elevation@1, metrics@2
```

It **changes nothing** — no import, no migration, no repair, no network. Run it
whenever you are unsure; that is what it is for.

Exit codes: `0` everything fine, `2` something wants attention, `1` something is
broken. `raw_storage` reads and re-hashes every original you have, so it is the
check that notices a disk going bad before a track does.
