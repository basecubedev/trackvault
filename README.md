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

## Quick start

```bash
docker compose up -d
```

Then open <http://127.0.0.1:8080/>. The archive starts empty and says so.

Put something in it:

```bash
docker compose exec gpx-view gpx-view import /path/inside/the/container.gpx
```

One setting is worth changing before you read a monthly total:
**`GPX_VIEW_TIMEZONE`** decides which month a late-evening activity counts
towards. It defaults to `UTC`; set it to your own zone in
[`compose.yaml`](compose.yaml), where it is commented out with the reason.

Then open **Offline maps** and install the region you walk in. Until you do,
your tracks are drawn over a neutral background — see
[Offline maps](#offline-maps).

Files can also be dropped into `import/` beside `compose.yaml` and picked up with
`docker compose exec gpx-view gpx-view scan`. The folder ships with the
repository so the bind mount never has to create it: a source Docker creates is
owned by root, and you would need `sudo` to write into the folder that exists for
you to write into.

> **Run this on a network you trust.** There is no authentication. See
> [Security](#security-and-the-trusted-network) below before you expose the
> port to anything.

## Importing tracks

Three ways in, one pipeline behind them.

**From the browser.** Open **Tracks** and press *Import files*. Pick one file or
twenty; each one is offered on its own and gets its own answer — imported,
already in the archive, or not imported with the reason in words. Nothing is
guessed: a file the archive cannot read is named, and the rest still go in.

**A watched folder.** Drop files into `import/` — the folder `compose.yaml`
mounts read-only at `/import` — and run `docker compose exec gpx-view gpx-view
scan`. This is the path for a phone that auto-syncs to the server, and for the
hundred files you are not going to pick in a file dialog. **The folder is never
modified**: nothing in it is written, renamed, moved or deleted. Hidden files are
skipped rather than reported as failures — a synced folder is full of `.DS_Store`
and half-written downloads, and none of them is a track.

**On the machine itself**, with the commands below.

> **The upload endpoint has no authentication in front of it**, because nothing
> here does. Anyone who can reach the port can add files as well as read them.
> Set `GPX_VIEW_UPLOAD_ENABLED=false` in [`compose.yaml`](compose.yaml) to refuse
> uploads and keep everything else — see
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

The typical deployment is phone → auto-sync → a folder on the server →
`GPX_VIEW_IMPORT_DIR` → `gpx-view scan`.

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

In Docker this is the **named volume** `gpx-view-data`. Docker initialises a
named volume from the image, so it arrives owned by the runtime user and is
writable without `chmod 777` and without running as root. A host bind mount is
not, because it keeps the host directory's ownership; to use one deliberately,
match the ownership rather than loosening the permissions:

```yaml
volumes:
  - ./data:/data
```

```bash
mkdir -p data && sudo chown -R 10001:10001 data
```

## Backup

**Two halves are essential, and one is not:**

```
the database          what the archive knows            irreplaceable
the managed raw store your original files               irreplaceable
maps/                 installed offline maps            re-downloadable
```

The raw store alone can be reimported, but every title you corrected, every
classification you fixed and every derived metric is in the database. The
database alone describes files it no longer has. Back up both.

`maps/` is the one part you may skip. It holds nothing you created — a map is a
public dataset you can fetch again — and it is by far the largest thing in the
directory. Backing up `GPX_VIEW_DATA_DIR` as a whole is simplest and includes
it; excluding `maps/` is a deliberate, safe choice if the size matters.

One caveat, so the choice is an informed one: a provider serves the *current*
build of a region. If you skip the backup and reinstall later, you get today's
data rather than the dataset you had. For a basemap that is almost always what
you wanted; if you need a specific dataset date preserved, back `maps/` up too.

**Do not copy the live SQLite file.** Write-ahead logging is on, so the `.sqlite3`
file on its own is an incomplete database and copying it while the server runs
can produce one that will not open. Two safe ways:

```bash
# 1. Stop the archive, copy the directory, start it again.
docker compose stop
docker run --rm -v gpx-view-data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/gpx-view-$(date +%F).tar.gz -C /data .
docker compose start

# 2. Or use SQLite's own online backup for the database, plus a copy of raw/.
docker compose exec gpx-view python -c \
  "import sqlite3; s=sqlite3.connect('/data/gpx-view.sqlite3'); d=sqlite3.connect('/data/backup.sqlite3'); s.backup(d); d.close(); s.close()"
```

The first is simpler and is what most people should do. Restoring is the reverse:
stop the archive, replace the directory contents, start it again.

There is no automatic backup and no scheduled export. Adding one would be a
feature with its own failure modes, and an archive that silently stopped backing
itself up would be worse than one that never claimed to.

## Upgrading

```bash
# 1. Back up, as above. The database is about to be migrated.
# 2. Pull or rebuild the new version.
docker compose build
# 3. Start it. The schema migration runs at start-up.
docker compose up -d
```

The database schema version lives in SQLite's own `PRAGMA user_version`. An
existing database runs the migrations it is missing, automatically, inside one
transaction each. A database written by a **newer** build is refused rather than
downgraded silently — so a downgrade is not supported, and the backup you took
in step 1 is how you go back.

After an upgrade that changes an analysis algorithm or the importer, the archive
does **not** reprocess itself: that would turn every deployment into a full
re-parse of the archive. It reports what is behind instead, on the dashboard and
in the track list, and you run:

```bash
docker compose exec gpx-view gpx-view reprocess --outdated
docker compose exec gpx-view gpx-view analyze --outdated
```

Until you do, the old numbers are shown as needing re-analysis rather than as
current. That is the point: a stale number presented as a headline is worse than
a blank one.

Which release and which algorithms a deployment is running is answered by
`GET /api/v1/system/info`, and by the **About** page.

## Security and the trusted network

> GPX-View currently assumes a **trusted network or an authenticated reverse
> proxy**.

There is no authentication. Anyone who can reach the port can read every track
in the archive — where you live, when you are out, and how long you were gone —
and can change a track's classification, title and notes.

- Run it on your own network, or behind a reverse proxy that authenticates.
- **Do not forward the port to the internet.**
- **Uploading is unauthenticated too.** Anyone who can reach the port can add
  files, not only read them. The endpoint is bounded — one file per request, the
  size limit applied while reading, the filename never used as a location — and
  `GPX_VIEW_UPLOAD_ENABLED=false` turns it off entirely while leaving every read
  working. Why it exists at all is recorded in
  [`docs/adr/0011-web-upload.md`](docs/adr/0011-web-upload.md).

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
- [Documentation index](docs/README.md) — including all nine architecture decision records

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
