# GPX-View

A self-hosted archive for the tracks you record and the routes you plan. It
keeps your original files byte-identically, works out what each track was, and
shows you a year of it on a map, a chart and a page of totals — on your own
machine, with nothing leaving it.

Tracks may come from Locus Map, Komoot, Garmin, Wahoo, OsmAnd, GPSLogger or
other applications. GPX is the first supported exchange format and the only one
implemented so far — an adapter, never the internal model:

```
Raw source → format adapter → canonical normalized track → analysis → statistics → presentation
```

The one thing the archive will not do is guess. A metric it could not derive is
shown as unavailable rather than as zero, a route with a planner's clock is
never dated by it, and a number produced by algorithms this build no longer runs
is not presented as the track's own.

## Installation

You need Docker. You do not need Python, Node, or a copy of this repository.

```bash
mkdir gpx-view && cd gpx-view
curl -fsSL https://github.com/basecubedev/gpx-view/releases/latest/download/install-docker.sh -o install-docker.sh
sh install-docker.sh
```

That writes a `docker-compose.yml`, a `.env`, and three directories, then pulls
the image and starts it. Open <http://127.0.0.1:8080/>. The archive starts empty
and says so.

```
gpx-view/
├── docker-compose.yml    written by the installer; --force replaces it
├── .env                  your settings; edit this, not the compose file
├── data/                 the archive: database, your originals, installed maps
├── import/               drop files here for `gpx-view scan` to read
└── backups/              where `gpx-view backup create` writes
```

`data/` is **application-owned** and `import/` is **external input**. They are
deliberately not the same directory: a backup written into the import folder
would be re-read as an import, and a restore would overwrite files you were
still syncing.

Useful flags — `sh install-docker.sh --help` lists them all:

| Flag | For |
| --- | --- |
| `--tag v1.2.3` | pin a release instead of following `latest` |
| `--port 9090` | something else already has 8080 |
| `--import-dir /srv/sync/locus` | your phone syncs somewhere else |
| `--dry-run` | print what would happen and change nothing |
| `--no-start` | write the files, start it yourself |
| `--force` | replace the compose file and `.env` |

`--force` replaces the two files the installer wrote. It never touches `data/`,
`import/` or `backups/`. Running the installer again without it keeps
everything, including a compose file you have edited.

**One setting is worth changing before you read a monthly total.**
`GPX_VIEW_TIMEZONE` in `.env` decides which month a late-evening activity counts
towards. It defaults to `UTC` — deliberately, because a container inherits
whatever zone its image carries and a local-time default would make the same
archive report different totals on two machines.

Then open **Offline maps** and install the region you walk in. Until you do,
your tracks are drawn over a neutral background — see
[Offline maps](#offline-maps).

> **Run this on a network you trust.** There is no authentication. See
> [Security](#security-and-the-trusted-network) before you expose the port to
> anything.

### Who the container runs as

As you. The installer writes your own uid and gid into `.env` as `PUID`/`PGID`,
so the process writing `data/` and `backups/` is the person who owns them. That
is why nothing here asks you to run `chmod 777` or to run anything as root: the
bind mounts work because the ownership already matches.

If you move the installation to another user, update those two values.

### Windows and macOS

The same installer and the same compose file. On Windows, run it from Git Bash
or WSL — Docker Desktop shares the daemon with both, and the compose file needs
no change.

There is deliberately **no PowerShell installer**. It would be a second copy of
the same logic, and the copy that gets less use is the one that silently rots;
the one thing it could offer over `sh` — knowing a Windows path — is exactly the
thing bind mounts on Docker Desktop want in POSIX form anyway. `PUID`/`PGID` are
ignored on Docker Desktop, which manages ownership itself, so a Windows
installation is the Linux one minus a problem.

### Running from a checkout instead

[`compose.yaml`](compose.yaml) in this repository builds from source and is what
development and the test suite use. It makes the opposite choice about
ownership: managed volumes and the image's own user, so nothing depends on whose
uid happens to be 1000. Get a backup out of it with

```bash
docker compose cp gpx-view:/backups/<name>.tar.gz .
```

Both are correct; they are answers to different questions. A user deployment
wants its files where its owner can read them, and a development checkout wants
to be independent of the developer.

## Importing tracks

Three ways in, one pipeline behind them.

**From the browser.** Open **Tracks** and press *Import files*. Pick one file or
twenty; each one is offered on its own and gets its own answer — imported,
already in the archive, or not imported with the reason in words. Nothing is
guessed: a file the archive cannot read is named, and the rest still go in.

**A watched folder.** Drop files into `import/` — the folder mounted read-only
at `/import` — and run `docker compose exec gpx-view gpx-view scan`. There is a
[step-by-step guide](#syncing-from-your-phone) for setting this up with a
phone. This is the path for a phone that auto-syncs to the server, and for the
hundred files you are not going to pick in a file dialog. **The folder is never
modified**: nothing in it is written, renamed, moved or deleted. Hidden files are
skipped rather than reported as failures — a synced folder is full of `.DS_Store`
and half-written downloads, and none of them is a track.

**On the machine itself**, with the commands below.

> **Uploading from the browser is off until you switch it on.** It is the one
> endpoint that lets a caller make the server write, and nothing here
> authenticates — so anyone who can reach the port could add files, not only
> read them. Set `GPX_VIEW_UPLOAD_ENABLED=true` in `.env` once you are happy
> that your network makes that acceptable; every read works either way. See
> [Security](#security-and-the-trusted-network).

```bash
gpx-view import path/to/track.gpx another.gpx
gpx-view scan                        # imports new files from GPX_VIEW_IMPORT_DIR
gpx-view reprocess <sha256>          # normalize a stored source again
gpx-view reprocess --failed          # ...every source whose last attempt failed
gpx-view reprocess --outdated        # ...every source an upgrade would change
gpx-view processing-status <sha256>  # what happened to one source
gpx-view analyze --outdated          # derive the metrics that are behind
gpx-view analyze --all               # ...for every current track
gpx-view analyze <track_id>          # ...for one
gpx-view export raw <sha256>         # the original file, byte-identical
gpx-view export track <track_id>     # a generated GPX 1.1 document
gpx-view backup create               # the whole archive, in one file
gpx-view backup list                 # what is in the backup directory
gpx-view restore <archive> --dry-run # what restoring it would do
gpx-view restore <archive>           # do it
gpx-view doctor                      # what is wrong with this deployment
```

Inside the container, prefix them with
`docker compose exec gpx-view`. Outside it, with `uv run`.

`import` and `scan` go through the same use case. Importing the same bytes
twice is a no-op — as long as the archive can still produce the bytes it says
it holds. If the managed copy was lost, offering the same file again restores
it and reports `repaired`; if the copy is there but no longer matches its hash,
the import fails rather than overwriting the evidence. **The import directory
is never modified**: nothing in it is written, renamed, moved or deleted.

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
        ↓  auto-sync / Syncthing / Nextcloud / rsync
host folder, e.g. ~/sync/locus
        ↓  read-only mount
/import inside the container
        ↓  GPX_VIEW_IMPORT_DIR
gpx-view scan  →  ImportTracks  →  archive
```

**1. Install GPX-View**, as above. It creates an `import/` folder for you.

**2. Decide where the phone syncs to.** Either sync straight into the `import/`
folder the installer made, or point GPX-View at a folder you already have:

```bash
# in .env
GPX_VIEW_IMPORT_PATH=/home/you/sync/locus
```

```bash
docker compose up -d          # apply it
```

Only the host side changes. Inside the container the folder is always `/import`,
which is why nothing else has to be reconfigured.

**3. Set your phone to sync there.** In Locus Map that is *Settings → Backup &
Sync*, or an export target on a shared folder; with Syncthing or Nextcloud it is
whatever you already use. GPX-View does not care which one — it reads a folder.

**4. Check the archive can see it:**

```bash
docker compose exec gpx-view gpx-view doctor
```

`import_directory` should say `configured and readable`. If it says
`GPX_VIEW_IMPORT_DIR is not set`, the container was started without the setting;
if it says it cannot be read, the mount is pointing somewhere that does not
exist on the host.

**5. Import what is there:**

```bash
docker compose exec gpx-view gpx-view scan
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
*/30 * * * * cd /home/you/gpx-view && docker compose exec -T gpx-view gpx-view scan >/dev/null
```

`scan` exits non-zero if any file failed, so a cron mail means something real.

### The folder is never modified

Nothing in the import directory is written, renamed, moved or deleted. The mount
is read-only as well, which is the second, independent reason: it stays true even
if the first is ever wrong. Your sync folder is input, and the archive keeps its
own byte-identical copy of everything it accepts.

## Getting data back out

Three different things wear the word "export", so they are three commands.

### Your original file, unchanged

```bash
docker compose exec -T gpx-view gpx-view export raw <sha256> > my-ride.gpx
```

Byte-identical to what you imported. The archive verifies the bytes against the
hash they are filed under before handing them over, so you get the original or a
named error — never something in between. The content hash is what `scan`,
`import` and the track page all show.

### A track as a fresh GPX file

```bash
docker compose exec -T gpx-view gpx-view export track 42 > track-42.gpx
```

This is **not** your original file. It is GPX 1.1 generated from what the archive
currently makes of the track: the title you gave it, the activity, the segments,
the positions, and elevation, timestamps and sensor readings where they exist.
Use it to get a track into another application; use `export raw` when you want
the file you imported.

It deliberately carries no "recorded" or "planned" marker. That is a conclusion
this archive reached from evidence, at a confidence, with a rule version — GPX
has no field that means it, and inventing one would publish a claim the
receiving application could not evaluate.

### Everything at once

That is a backup. See below.

## Backup

```bash
docker compose exec gpx-view gpx-view backup create
```

```
created:   /backups/gpx-view-20260810-164301.tar.gz
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
open. Write-ahead logging means the `.sqlite3` file on its own is incomplete, and
a copy taken while anything is writing can mix pages from before and after a
transaction. `backup create` uses SQLite's own online backup instead, checks
every stored original against its hash, and then **reads the finished archive
back through the restore validation** — so a backup that reports success is one
that has already been proved restorable.

```bash
docker compose exec gpx-view gpx-view backup list
```

There is no automatic or scheduled backup. Adding one would be a feature with
its own failure modes, and an archive that silently stopped backing itself up
would be worse than one that never claimed to. A cron line does it:

```cron
0 3 * * 0 cd /home/you/gpx-view && docker compose exec -T gpx-view gpx-view backup create
```

> **A backup is your whole movement history in one file.** Everywhere you have
> been, when, and for how long. Treat it exactly as carefully as you would treat
> the archive itself — and more carefully than the machine it is copied to.

## Restore

Look before you leap:

```bash
docker compose exec gpx-view gpx-view restore /backups/gpx-view-20260810-164301.tar.gz --dry-run
```

```
archive:       gpx-view-archive v1
taken at:      2026-08-10T16:43:01+00:00
written by:    GPX-View 0.1.0
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

A dry run changes nothing at all, and exits non-zero if the archive could not be
restored — so it works in a script as well as on a screen.

Then do it. **Restoring wants the archive to itself**: it replaces the database
and the stored originals, and an import running at the same time would be
writing into storage that is moving out from under it. Backing up is the
opposite and needs no such care — it is a read, and it runs happily while you
use the archive.

```bash
docker compose stop gpx-view
docker compose run --rm gpx-view gpx-view restore /backups/gpx-view-20260810-164301.tar.gz
docker compose start gpx-view
```

**Nothing is touched until everything has been checked.** The manifest, the
format version, the schema compatibility, every checksum, and the restored
database's own integrity check all pass before a single file moves. A damaged
archive costs you nothing to attempt.

**An archive that already holds tracks is never replaced silently.** Restoring
over one needs `--replace`, which you have to type. Restoring into an empty
archive — a new machine, or one you have just lost — needs nothing extra.

```bash
gpx-view restore <archive> --replace            # replace what is there
gpx-view restore <archive> --into /some/other   # somewhere else entirely
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
either completed or did not happen; there is no half-restored state to
untangle, and `doctor` reports one that has not been resolved yet.

### Restoring onto a new machine

```bash
mkdir gpx-view && cd gpx-view
sh install-docker.sh --no-start
cp /media/backup/gpx-view-20260810-164301.tar.gz backups/
docker compose up -d
docker compose exec gpx-view gpx-view restore /backups/gpx-view-20260810-164301.tar.gz
docker compose restart
```

### Which backups this version can read

| The backup's schema | What happens |
| --- | --- |
| the same as this build | restores as it is |
| older than this build | restores, then migrates when the archive next starts |
| newer than this build | **refused** — upgrade GPX-View first |

The last row is the same rule the database itself applies: a build that does not
know what the newer columns mean must not open them and guess.

## Checking the archive

```bash
docker compose exec gpx-view gpx-view doctor
```

```
ok       release              GPX-View 0.1.0, in a container
ok       data_directory       present and writable
ok       database             present and readable
ok       database_integrity   passed
ok       schema               version 10
ok       raw_storage          412 original(s), all matching their hash
ok       import_directory     configured and readable
warning  backups              no backups found; `gpx-view backup create` writes one
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

## The web interface

Three pages, served from the same origin as the data.

**Dashboard.** A year at a time: distance, track count, moving time and ascent,
with a monthly chart and the same numbers in a table beside it. The year is the
newest one *the archive* has something to show for, not the one your computer
thinks it is. Recorded, planned and undecided tracks are three separate scopes
and are never added together.

Each month is drawn as **one bar per activity**, with a legend above the chart
and a column per activity in the table below it — so a month of cycling and a
month of walking are two answers rather than one sum. An activity keeps its
colour whatever else the year holds, so narrowing the filter never repaints what
is left. A month an activity was not out in shows a dash, not a zero: nobody
walked no kilometres.

The year selector also offers **All years**, which is a different question and
says so in its shape: the totals cover everything the scope holds, and the chart
draws one bar per year instead of per month — one bucket for each year the
archive has something for, not a padded calendar. Opening one lists that year's
tracks. The archive answers it in one request; the browser never adds years up
itself, because summing periods correctly means knowing which analyses may be
totalled at all.

**Tracks.** The archive, one page at a time, with every filter in the address
bar — so a filtered view is a link you can send and a refresh does not lose it.
*Import files* at the top offers files to the archive from the browser.
Each row carries a small map of where that track went, drawn over the same
offline basemap the track's own page uses. A row draws nothing until you have
scrolled to it, and it asks for a reduced shape rather than the whole recording,
so a page of rows costs a page of rows.

A row also says **roughly where the track was** — `≈ Zeeland · Netherlands`.
The tilde is not decoration: the archive compares the rectangle around your
track with the rectangle around each region the map provider publishes, which is
right well inside a country and wrong at a border. A walk in Aachen is named for
the Dutch province next door, and the mark is what stops that reading as a fact.
The region and the country come from one selection — the country is the named
region's own ancestor — so they can be wrong together but never contradict each
other. It needs a region catalog, so **Offline maps → refresh** once; without
one the archive says nothing rather than guessing from a coordinate.

A row that says **"3 imports"** means the same recording arrived three times, in
three different files — the same ride exported as GPX 1.1, as GPX 1.0 and as a
route, say. All three are kept: the bytes are different evidence, and one format
carries readings another cannot. What the badge stops is the list presenting one
afternoon as three. It is an *equality* — identical positions at identical
instants — so it never guesses that two rides are one, and it does not find the
same loop ridden on two different days.

The arrow at the right of a row opens **the track's whole page inside the list**
— the same component, not a summary of it — so looking for the right track is
scrolling and reading rather than a page load and a back button each time. One
row at a time, and which one is in the address too. Corrections made in there
are corrections: the row above updates rather than keeping the answer it had.

**One track.** What it is, what it did, where it went, and how the archive
decided all of that. You can correct its kind, give it a title of your own and
keep a note; the file keeps saying what it said, and resetting hands the display
back to it.

If a recording carries **heart rate or cadence**, they get a chart of their own
below the elevation one — measurements passed through exactly as the sensors
reported them. A gap is a reading the sensor missed; a cadence of zero is a
reading, not a gap. Tracks without sensors show no such chart rather than an
empty one.

If a track's metrics were derived by algorithms this build no longer runs, the
headline figures are left blank and the page says why — while the map and the
chart, which are derived now, stay exactly right.

## Offline maps

GPX-View downloads a regional map **once** and stores it in your own data
directory. After that, looking at a track draws its background from your own
machine and sends nothing to anybody.

That is the whole point. A map from somebody else's tile service tells that
service where you walked, one request at a time, every time you open a track.
No amount of privacy headers changes that, because the coordinates *are* the
request.

### Why not just use a public tile server

The community-run OpenStreetMap tile servers are paid for by donations and their
usage policy exists to prevent exactly what an offline archive would need: bulk
downloading an area to keep. Commercial providers want an account and a key.
Neither belongs in a self-hosted archive of your own movements, so GPX-View uses
neither.

### Installing a region

Open **Offline maps** in the navigation. Regions come from
[Geofabrik](https://download.geofabrik.de/), which publishes a ready-made vector
map per region built from OpenStreetMap data.

Open a continent, then a country, and press **Download** on the region you want.
The size is shown before you start. Installing answers immediately and the
download runs in the background — you can keep using the dashboard, the track
list and any track while it does.

A few things worth knowing:

- **Not every region has one.** Geofabrik publishes packages for 356 of its 555
  regions. Germany, the Netherlands and Spain are only offered as their states,
  provinces and communities; the list shows "No package" for the rest, rather
  than a button that would fail.
- **Sizes are real.** Monaco is 1.7 MB, North Rhine-Westphalia about 800 MB,
  Bavaria about 1 GB. Check the number before you press the button.
- **Install more than one.** A track that crosses a border draws its basemap
  from both, if both are installed.

### Which region do I need?

You do not have to work that out yourself. A track knows where it went, and the
provider's index says where each of its 555 regions is — so a track with nothing
behind it **names the regions that would cover it**, with a Download button, on
the track's own page and in the list. There is no need to guess which province
a walk was in.

They are candidates, and the page says so. What the archive compares is
rectangles: the box around your track against the box around a region's
outline. That is enough to shortlist and not enough to decide — a box around the
Netherlands contains Aachen, which is in Germany. So several regions are
offered, each with the regions above it (`Limburg — Europe / Netherlands`), and
**you** pick the one you actually walked in.

Two honest limits:

- **A catalog has to have been read once.** Suggesting reaches no provider: it
  reads the catalog this deployment already fetched, because opening a track
  must not contact anybody. Before the first visit to **Offline maps**, the page
  says so instead of guessing.
- **Regions that cross the antimeridian get no suggestion.** New Zealand, Fiji,
  Alaska and Russia reduce to a rectangle spanning the whole globe, which would
  match every track on Earth. They are left out rather than offered everywhere,
  so a track there is offered nothing — as it was before this existed.

Nothing downloads on its own. A region is hundreds of megabytes, and which one
you want is your decision, not a page-load's.

### Where it is stored

```
<data dir>/maps/packages/…    the installed map files
<data dir>/maps/catalog/…     the last catalog that was read
```

Same directory as everything else, same permissions: `0700` directories, `0600`
files, owned by the runtime user. In Docker that is the `gpx-view-data` volume,
so an installed map survives `docker compose up --force-recreate`. Regional maps
are **never** baked into the image — the image is the same everywhere and what
you installed is yours.

### Updating and removing

Press **Update** when you want newer data. Maps change slowly; every few months
is plenty, and there is deliberately no background updater downloading hundreds
of megabytes while you are not looking.

An update downloads the new package *beside* the one you have and only switches
when the new one has been checked. If the download fails, runs out of disk or
turns out to be broken, **the map you had is still the map you have**.

**Remove** deletes the map and nothing else. Your tracks, titles, corrections
and statistics are untouched, and the region can be downloaded again later.

### What happens offline

With a region installed, unplug the network and everything still works: the
dashboard, the track list and the small maps beside its rows, a track's detail,
its elevation and speed profile, the track itself, the basemap behind it, the
place and street labels on it, and the attribution. `web/e2e/offline-maps.spec.ts` blocks every request that is not
this deployment and asserts exactly that.

The provider is contacted in exactly three situations, all of them something you
pressed: browsing the catalog, installing, updating. If it is unreachable, the
map manager says so and your installed maps carry on.

### Attribution

Map data is **© OpenStreetMap contributors**, under the Open Database License
1.0, packaged by Geofabrik GmbH. That is read out of the package rather than
written into this application, it is shown beside every map that is drawn, and a
package that states no licence is refused rather than installed. See
[Credits](docs/legal/third-party-notices.md) and the `/credits` page.

### Disk space

Budget for the package plus a little room: the installer refuses to start if the
new map, the one you already have and 256 MB of head-room would not fit, and it
never frees space by deleting a working map to attempt an unproven download.

### Styling

Two themes and none: **Outdoor**, which draws paths, tracks and cycleways as
first-class features with everything else muted, **Light**, which is as plain as
possible, and **No basemap**. It is called Outdoor rather than Topographic
because the data carries no contours, no hillshade and no elevation model, and
promising those would be a lie.

## The data directory

Everything persistent lives under `GPX_VIEW_DATA_DIR`, which is `/data` in the
container:

```
<data dir>/gpx-view.sqlite3          the database
<data dir>/gpx-view.sqlite3-wal      its journal files
<data dir>/gpx-view.sqlite3-shm
<data dir>/raw/sha256/ab/abcd….raw   every original import, byte-identical
<data dir>/maps/packages/…           installed offline maps, re-downloadable
<data dir>/maps/catalog/…            the last provider catalog that was read
```

Nothing is written to the working directory, `/tmp`, a home directory or the
container layer.

It is created for your user and nobody else: directories `0700`, files `0600`,
including the database, its journal files and the managed raw copies. Existing
files are reported rather than chmodded — they may carry an access decision you
made.

In an installed deployment this is `data/` beside your compose file, bind
mounted, and the container runs as you (`PUID`/`PGID` in `.env`) so the
ownership already matches. Nothing needs `chmod 777` and nothing runs as root.

The repository's own [`compose.yaml`](compose.yaml) uses a **named volume**
instead, and the image's own user: Docker initialises a named volume from the
image, so it arrives owned correctly whoever the developer is. Both avoid the
same trap from opposite directions — see
[Running from a checkout](#running-from-a-checkout-instead).

## Backup, restore and updates

Covered above: [Backup](#backup), [Restore](#restore) and
[Checking the archive](#checking-the-archive). What belongs here is the part
about the directory rather than the commands.

**Two halves are irreplaceable, and one is not:**

```
the database          every correction, title and derived metric   irreplaceable
the managed raw store your original files                          irreplaceable
maps/                 installed offline maps                       re-downloadable
```

`gpx-view backup create` takes the two that matter and states that it left the
third. The raw store alone could be re-imported, but every title you corrected,
every classification you fixed and every derived metric is in the database; the
database alone describes files it no longer has.

One caveat, so the choice is informed: a provider serves the *current* build of
a region. Reinstalling a map later gives you today's data rather than the
dataset you had. For a basemap that is almost always what you wanted.

## Updating

```bash
docker compose exec gpx-view gpx-view backup create   # 1. before anything
docker compose pull                                    # 2. get the new image
docker compose up -d                                   # 3. start it
```

That is the whole procedure when no migration is needed, and it is safe when one
is: the schema migration runs at start-up, inside one transaction per step.

To move to a specific release rather than the newest, set the tag in `.env`:

```
GPX_VIEW_IMAGE=ghcr.io/basecubedev/gpx-view:v1.2.3
```

**Downgrading is not supported.** A database written by a newer build is refused
rather than converted silently — this build cannot know what the newer columns
mean, and guessing would be worse than stopping. Going back means restoring the
backup you took in step 1, which is why it is step 1. `gpx-view doctor` reports
the situation in words if you ever end up in it.

After an upgrade that changes an analysis algorithm or the importer, the archive
does **not** reprocess itself: that would turn every deployment into a full
re-parse. It reports what is behind, on the dashboard and in the track list, and
you run:

```bash
docker compose exec gpx-view gpx-view reprocess --outdated
docker compose exec gpx-view gpx-view analyze --outdated
```

Until you do, the old numbers are shown as needing re-analysis rather than as
current. That is the point: a stale number presented as a headline is worse than
a blank one.

Which release and which algorithms a deployment is running is answered by
`GET /api/v1/system/info`, by the **About** page, and by `gpx-view doctor`.

## Troubleshooting

**`gpx-view scan` says "no import directory configured".**
`GPX_VIEW_IMPORT_DIR` is not set in the container. In an installed deployment it
is set for you; if you edited the compose file, check the `environment:` block
still has `GPX_VIEW_IMPORT_DIR: /import`. `gpx-view doctor` says which of the two
is wrong.

**`gpx-view scan` finds nothing, and the files are definitely there.**
You are probably looking at two different folders. The path you type on the host
and the path the container reads are not the same thing — inside the container
it is always `/import`, and what that maps to on the host is
`GPX_VIEW_IMPORT_PATH` in `.env`. Check what the container actually sees:

```bash
docker compose exec gpx-view ls -la /import
```

If that is empty, the mount is pointing somewhere else. Never pass a host path
to a command running inside the container.

**"permission denied" writing to `data/` or `backups/`.**
The container is running as a different user than the one that owns the folders.
Check `PUID`/`PGID` in `.env` against `id -u` and `id -g`, then
`docker compose up -d`. Do not `chmod 777` — it does not fix the cause and it
makes your movement history world-readable.

**A backup fails with "the backup directory is not writable by this user".**
Same cause, for `backups/`.

**`doctor` reports originals that do not match their content hash.**
The managed copy of a file is not the bytes it claims to be. That is disk
corruption or tampering, and GPX-View deliberately does not overwrite it —
the artifact is the only evidence of what happened. Restore your newest backup,
or delete the affected artifact and import the original file again.

**`doctor` reports missing originals.**
The database knows a file the disk no longer has. Importing the same file again
restores it and reports `repaired`; the bytes hash to the digest it is filed
under, which is the same proof the first import needed.

**Restore says `restore_target_occupied`.**
The archive you are restoring into already holds tracks. That is the safety
catch. Use `--replace` if you meant it, or `--into <directory>` to restore
somewhere else and look first.

**Restore says `archive_schema_unsupported`.**
The backup was written by a newer GPX-View than the one running. Update the
image and try again.

**The map is a neutral background.**
No offline map covers the track. See [Offline maps](#offline-maps).

**Something else.** `gpx-view doctor` first, then
`docker compose logs gpx-view`. The logs never contain coordinates or track
titles, so they are safe to share.

## Security and the trusted network

> GPX-View currently assumes a **trusted network or an authenticated reverse
> proxy**.

There is no authentication. Anyone who can reach the port can read every track
in the archive — where you live, when you are out, and how long you were gone —
and can change a track's classification, title and notes.

- Run it on your own network, or behind a reverse proxy that authenticates.
- **Do not forward the port to the internet.**
- **Uploading is off by default, and unauthenticated when you enable it.**
  Writing is the one thing an unauthenticated caller must not be able to do
  because a container happened to start, so `GPX_VIEW_UPLOAD_ENABLED` defaults
  to `false` and turning it on is a statement about your network. The endpoint
  itself is bounded — one file per request, the size limit applied while
  reading, no archive or multipart parsing, the filename never used as a
  location — but bounded is not authenticated. Why the capability exists at all
  is recorded in [`docs/adr/0011-web-upload.md`](docs/adr/0011-web-upload.md).

What the application does do:

- every response carries `X-Content-Type-Options`, `Referrer-Policy:
  no-referrer`, a frame policy and a small `Content-Security-Policy`.
  `no-referrer` is not boilerplate here: it stops a basemap request telling a
  tile provider the address of your archive;
- the container runs as a non-root user (uid 10001);
- errors answer one stable envelope and never carry a path, a coordinate or a
  stack trace;
- nothing reaches a network unless you configured a basemap.

## Configuration

All configuration goes through environment variables with the `GPX_VIEW_`
prefix:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GPX_VIEW_HOST` | `127.0.0.1` | Interface the HTTP server binds to |
| `GPX_VIEW_PORT` | `8080` | TCP port |
| `GPX_VIEW_DATA_DIR` | `data` | Holds *all* persistent data: the database and every original import |
| `GPX_VIEW_IMPORT_DIR` | unset | Directory `gpx-view scan` reads. Unset disables the feature. Never modified. |
| `GPX_VIEW_BACKUP_DIR` | `backups/` beside the data directory | Where `gpx-view backup create` writes. Outside the data directory on purpose: a backup kept inside what it protects is lost with it. |
| `GPX_VIEW_UPLOAD_ENABLED` | `false` | Whether the browser interface may add files. Off unless you set it: uploading is unauthenticated, and every read works without it. |
| `GPX_VIEW_IMPORT_MAX_BYTES` | `16777216` | Largest accepted input file |
| `GPX_VIEW_IMPORT_MAX_TRACKS` | `100` | Most tracks in one document |
| `GPX_VIEW_IMPORT_MAX_SEGMENTS_PER_TRACK` | `1000` | Most segments in one track |
| `GPX_VIEW_IMPORT_MAX_POINTS` | `500000` | Most positions in one document |
| `GPX_VIEW_TIMEZONE` | `UTC` | IANA zone month and year boundaries are drawn in, e.g. `Europe/Berlin` |
| `GPX_VIEW_WEB_DIR` | `web/dist` | Built browser assets. Nothing there means the API is served alone. |
| `GPX_VIEW_MAPS_ENABLED` | `true` | Whether offline map packages may be installed. `false` refuses every management action; installed maps keep working. |
| `GPX_VIEW_MAP_MAX_DOWNLOAD_BYTES` | `8589934592` | Largest map package this deployment will fetch |

The import limits bound a hostile or broken file; the defaults comfortably cover
an ordinary long GPS recording.

`GPX_VIEW_TIMEZONE` defaults to `UTC` rather than to the host's zone, because a
container inherits whatever its image carries and the same archive would
otherwise report different monthly totals on two machines. An unknown zone fails
at start-up rather than falling back silently.

## What the numbers mean

All stored values are SI; the interface converts for reading and never back.

| Metric | Meaning |
| --- | --- |
| `distance_m` | horizontal distance between consecutive positions, summed **within** segments |
| `elevation_min_m`, `elevation_max_m` | extremes of the raw observations |
| `elevation_gain_m`, `elevation_loss_m` | ascent and descent of the *filtered* profile |
| `elapsed_duration_s` | first position timestamp to last |
| `moving_duration_s` | observed time actually going somewhere |
| `stopped_duration_s` | observed time standing still |
| `unobserved_gap_duration_s` | time nothing was recorded at all |
| `unattributed_duration_s` | observed time no rule could classify |
| `average_speed_mps` | distance over elapsed duration |
| `moving_average_speed_mps` | distance covered while moving, over moving time |
| `maximum_speed_mps` | highest speed **sustained** across the analysis window |

Four of those are deliberately not what the obvious formula would give:

- **A twenty-minute silence is not a rest.** A paused app, a flat battery and a
  break look identical in the data, so that time is an *unobserved gap*. A stop
  is only claimed where positions kept arriving and showed no movement.
- **A stationary phone is not walking.** A receiver standing still reports
  positions metres apart every second, so movement is decided from how far the
  track got across a window, at two scales — and the same window keeps one bad
  fix from becoming your maximum speed.
- **Elevation noise is not climbing.** Summing every upward step turns a flat
  hour into hundreds of imaginary metres and grows with the sampling rate, so
  ascent comes from a filtered profile. Minimum and maximum stay raw.
- **A timestamp is not a measurement.** A route planner writes plausible times
  onto geometry nobody travelled. Durations from such a track are reported as
  the path's own timeline, never as time somebody spent, and the track belongs
  to no month.

A metric that cannot be derived is *absent*, never zero. Everything states the
algorithm version that produced it, so when an algorithm changes,
`analyze --outdated` regenerates and the old numbers were never mistaken for the
new ones.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` | Liveness probe |
| `GET /api/v1/system/info` | Release, schema version and the installed algorithms |
| `GET /api/v1/tracks` | List stored tracks, without geometry |
| `GET /api/v1/tracks/{id}` | Read one track |
| `GET /api/v1/tracks/{id}/geometry` | Its segments and positions; `?max_points=` for a simplified shape |
| `GET /api/v1/tracks/{id}/profile` | Its elevation and speed against distance; `?max_samples=` |
| `GET /api/v1/tracks/{id}/analysis` | Its derived metrics and their status |
| `PUT /api/v1/tracks/{id}/classification` | Correct the kind: `{"kind": "recorded"}` |
| `DELETE /api/v1/tracks/{id}/classification` | Withdraw the correction |
| `PATCH /api/v1/tracks/{id}/metadata` | Your own title and note: `{"title": "…"}` |
| `GET /api/v1/statistics/years` | Which years this archive holds tracks in |
| `GET /api/v1/statistics/year/{year}` | Total one year |
| `GET /api/v1/statistics/year/{year}/monthly` | Total each of its twelve months |

Statistics take `?scope=recorded|planned|unknown` (default `recorded`) and an
optional `?activity=walking`. Actual and planned are never mixed, and every
response names the timezone its buckets were drawn in.

```bash
curl 'http://127.0.0.1:8080/api/v1/statistics/year/2025?scope=recorded'
```

Every unit is in its field name, and a metric that could not be derived is
`null` rather than `0`. Geometry is a separate request on purpose: a long
recording holds tens of thousands of positions, and `point_count` in the listing
tells a client what it would be asking for.

Interactive documentation is at `/docs`.

## Project status

The archive works end to end, from a file to a year on screen:

```
GPX file → safe parse → normalized track → evidence → classification
        → SQLite + byte-identical raw copy
        → versioned analysis → per-track metrics
        → month and year statistics → HTTP read API → browser application
```

**Not implemented:** FIT, TCX, KML and GeoJSON adapters; 3D path length, grade,
splits, personal records and lifetime totals; elevation correction against a
terrain model; aggregate sensor metrics (average or maximum heart rate) and
sensor schemas beyond heart rate and cadence; semantic duplicate
detection; authentication; a file system watcher; week-based or custom
statistics periods.

**GPX-View declares no licence of its own.** That is a gap rather than a
statement, and it is the project owner's decision to make: until a `LICENSE`
file exists, no licence is granted to anybody. The third-party dependencies are
a separate matter and are documented.

## Documentation

- [Development](docs/developer/development.md) — setup, tests, gates, tooling
- [Agent rules](docs/developer/agent-rules.md) — the canonical rules for changes
- [Architecture](docs/technical/architecture.md) — layers, boundaries, authorities
- [Business contracts](docs/technical/contracts.md) — the invariants every feature holds to
- [Third-party notices](docs/legal/third-party-notices.md) — dependencies, licences, and what the map does not ship
- [ADR 0012](docs/adr/0012-export-archive-and-restore.md) — export, the archive format and restore
- [Documentation index](docs/README.md) — including every architecture decision record

## Privacy

GPX and FIT files are personal movement data — a record of where a real person
was and when.

- Personal recordings are never committed; `*.gpx` is git-ignored except for
  synthetic fixtures under `tests/fixtures/`.
- Coordinates and raw payloads never appear in normal logs or exception
  messages, at any log level.
- Original imports are never modified, and parsing never writes near the file it
  read.
- Nothing leaves the deployment unless you configure a basemap, and then only
  the tile requests that basemap needs — without a referrer.
