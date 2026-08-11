# TrackVault

**A self-hosted personal archive for activity tracks.**

Your tracks. Your data. Your server.

TrackVault keeps the tracks you record and the routes you plan on a machine you
control. It stores every original file byte-identically, works out what each
track was, and shows you a year of it on a map, a chart and a page of totals —
with nothing leaving the deployment.

It is not a social network. There is no feed, no following and no upload to
anybody else.

## Features

- **Your originals, untouched.** Every imported file is kept byte-identical and
  verified against its own content hash. Nothing is ever rewritten in place.
- **Recorded and planned stay apart.** A track is classified from evidence, with
  a confidence and a rule version, and never guessed from the exporting app.
  Totals for travelled distance never include routes you only planned.
- **Numbers that refuse to lie.** A metric that cannot be derived is shown as
  unavailable, never as zero, and a figure produced by algorithms this build no
  longer runs is not presented as current.
- **Offline maps.** A regional vector map is downloaded once into your own data
  directory. Opening a track then reaches no tile service at all.
- **Import from a watched folder**, from the command line, or from the browser.
  One import pipeline behind all three.
- **A dashboard, a track browser and a track page**, with heart rate and cadence
  charts where a recording carries them.
- **Backup and restore in one command**, with a manifest that states what it
  deliberately left out.
- **A read API and interactive documentation** at `/docs`.

GPX is currently the exchange format TrackVault reads and writes. The archive
model is deliberately format-independent: a format is an adapter at the edge,
never the internal track model.

## Quick start

You need Docker. You do not need Python, Node, or a copy of this repository.

```bash
mkdir trackvault && cd trackvault
curl -fsSL https://github.com/basecubedev/trackvault/releases/latest/download/install-docker.sh -o install-docker.sh
sh install-docker.sh
```

That writes a `docker-compose.yml`, a `.env` and three directories, then pulls
the image and starts it. Open <http://localhost:8081/>. The archive starts empty
and says so.

```
8081   on the host           what you open in a browser (--port to change it)
8080   inside the container  fixed
```

> **Run this on a network you trust.** There is no authentication yet. See
> [Configuration and security](docs/user/configuration.md#security-and-the-trusted-network)
> before you expose the port to anything.

More: [Installation](docs/user/installation.md) — installer flags, who the
container runs as, Windows and macOS, running from a checkout, updating.

## Importing tracks

The usual setup, and the one this was built for:

```
Locus Map (or any recorder)
        ↓  AutoSync / Syncthing / Nextcloud / rsync
host folder, e.g. ~/sync/locus   →  import/ in your installation
        ↓  read-only mount
trackvault scan
        ↓
Dashboard · Track browser · Track detail
```

Point your phone's sync target at the `import/` folder the installer made — or
point TrackVault at a folder you already sync to, by setting
`TRACKVAULT_IMPORT_PATH` in `.env`. Then:

```bash
docker compose exec trackvault trackvault scan
```

```
imported  a95629ddc1ae tracks=1  morning-ride.gpx
duplicate 4f2b91c00de1            yesterday.gpx
```

Every file gets its own answer, and a file already in the archive is recognised
by its content and skipped — so a sync folder that never clears itself does not
become forty copies of one ride. **The import folder is never modified**:
nothing in it is written, renamed, moved or deleted, and the mount is read-only
as well.

You can also import from the command line (`trackvault import <file>`) or from the
browser, once you have switched uploading on.

### Getting a GPX file out of the app you recorded with

TrackVault does not connect to any of these services. There is no account to
link and nothing to authorise: you export a GPX file from the app that holds the
activity, and you import that file. That is the whole reason an archive built
this way keeps working when a service changes its API, its terms or its mind.

| Where the track is now | How to export it | Worth knowing |
| --- | --- | --- |
| [Locus Map](https://docs.locusmap.app/doku.php?id=manual:user_guide:tracks:export) | Track or route → **Export** → GPX | Locus AutoSync can write into the synced folder above, which is the setup at the top of this section |
| [komoot](https://support.komoot.com/hc/en-us/articles/10115477099674-Export-and-import-Routes-and-Activities) | Tour → **Export GPX** | The region the tour starts in has to be unlocked. The file carries the geometry, not komoot's waypoints or voice directions |
| [Strava](https://support.strava.com/hc/en-us/articles/216918437-Exporting-your-Data-and-Bulk-Export) | Activity → **Export GPX** | An activity recorded without GPS exports an empty file. Power from a meter is included, estimated power is not |
| [Garmin Connect](https://support.garmin.com/en-US/?faq=W1TvTPW8JZ6LfJSfK512Q8) | Activity → **Export to GPX** | TCX and the original FIT are offered beside it; GPX is the one TrackVault reads today |

A planned route and a recorded activity are both welcome — TrackVault keeps them
apart rather than adding them together. Richer formats carry sensor data a GPX
document does not, which is why the archive model is format-independent and FIT
is a later adapter rather than a rewrite.

More: [Importing and exporting](docs/user/importing.md) — the full command list,
the phone-sync walkthrough, scheduling a scan, and the three ways to get data
back out.

## The web interface

**Dashboard** — a year at a time, or all of them: distance, track count, moving
time and ascent, one chart bar per activity per month. Recorded, planned and
undecided are separate scopes and are never added together.

**Tracks** — the archive one page at a time, every filter in the address bar, a
small map beside each row and roughly where the track was. Opening a row expands
the track's whole page inside the list.

**One track** — what it is, what it did, where it went, and how the archive
decided. Correct its kind, give it your own title, keep a note; the source file
keeps saying what it said, and resetting hands the display back to it.

More: [Reading the data](docs/user/reading-the-data.md) — the pages in detail,
what each metric means, and the HTTP API.

## Maps

Open **Offline maps** and install the region you walk in. Until you do, tracks
are drawn over a neutral background.

A regional vector map is downloaded **once**, from
[Geofabrik](https://download.geofabrik.de/), and stored in your own data
directory. After that, opening a track draws its background from your own
machine. A public tile service would learn where you walked one request at a
time, which is exactly what a self-hosted archive exists to avoid — so the
provider is contacted only for the three things you press: refresh the catalog,
install, update.

Map data is **© OpenStreetMap contributors**, under the Open Database License
1.0, packaged by Geofabrik GmbH; the label glyphs are Noto Sans under the SIL
Open Font License 1.1. Both are read from the package rather than written into
the build, and both are shown beside the map and on the `/credits` page.

More: [Offline maps](docs/user/maps.md).

## Backup and restore

```bash
docker compose exec trackvault trackvault backup create
docker compose exec trackvault trackvault restore /backups/<name>.tar.gz --dry-run
docker compose exec trackvault trackvault restore /backups/<name>.tar.gz
```

One file holds the database and every original, with a manifest and a checksum
per part. It uses SQLite's own online backup and then reads the finished archive
back through the restore validation, so a backup that reports success has
already been proved restorable. It leaves out installed offline maps — public
data you can download again — and the manifest says so.

A restore checks everything before it moves a single file, refuses an archive
written by a newer release, and never silently replaces an archive that already
holds tracks.

> A backup is your whole movement history in one file. Treat it as carefully as
> the archive itself.

More: [Backup and restore](docs/user/backup-and-restore.md), including
`trackvault doctor`, which reports what is wrong with a deployment and changes
nothing.

## Configuration

Everything is an environment variable in `.env`. The ones worth knowing:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRACKVAULT_HTTP_PORT` | `8081` | Host port the container's 8080 is published on |
| `TRACKVAULT_TIMEZONE` | `UTC` | Which zone month and year boundaries are drawn in |
| `TRACKVAULT_IMPORT_PATH` | `./import` | Host folder mounted read-only as the import directory |
| `TRACKVAULT_UPLOAD_ENABLED` | `false` | Whether the browser may add files. Unauthenticated when on. |
| `TRACKVAULT_MAPS_ENABLED` | `true` | Whether offline map packages may be downloaded at all |

`TRACKVAULT_TIMEZONE` is worth setting before you read a monthly total: it decides
which month a late-evening activity counts towards.

More: [Configuration, storage and security](docs/user/configuration.md) — the
full variable list, the data directory layout, and what "trusted network"
means here.

## Updating

```bash
docker compose exec trackvault trackvault backup create   # 1. before anything
docker compose pull                                       # 2. get the new image
docker compose up -d                                      # 3. start it
```

Migrations run at start-up. Downgrading is refused rather than guessed at, which
is why step 1 is step 1. After an upgrade that changes an algorithm, run
`trackvault reprocess --outdated` and `trackvault analyze --outdated` — the archive
reports what is behind rather than silently re-parsing itself.

## Development

```bash
uv sync                # Python 3.13, dependencies from uv.lock
cd web && npm ci       # Node 24, the browser application
uv run pytest          # no network, no Docker, no real GPS files
```

More: [Development](docs/developer/development.md) — setup, the test suites and
quality gates, API type generation and the licence audits.
[Agent rules](docs/developer/agent-rules.md) are the canonical rules every change
follows.

## Documentation

| For | Document |
| --- | --- |
| Installing, updating, running it | [docs/user/installation.md](docs/user/installation.md) |
| Getting tracks in and out | [docs/user/importing.md](docs/user/importing.md) |
| Offline maps | [docs/user/maps.md](docs/user/maps.md) |
| Backup, restore, `doctor` | [docs/user/backup-and-restore.md](docs/user/backup-and-restore.md) |
| Settings, storage, security | [docs/user/configuration.md](docs/user/configuration.md) |
| The pages, the metrics, the API | [docs/user/reading-the-data.md](docs/user/reading-the-data.md) |
| When something is wrong | [docs/user/troubleshooting.md](docs/user/troubleshooting.md) |
| How it is built and why | [docs/README.md](docs/README.md) |

## Project status

The archive works end to end, from a file to a year on screen. **Not
implemented:** FIT, TCX, KML and GeoJSON adapters; 3D path length, grade,
splits, personal records and lifetime totals; elevation correction against a
terrain model; aggregate sensor metrics and sensor schemas beyond heart rate and
cadence; semantic duplicate detection; authentication; a file system watcher;
week-based or custom statistics periods.

## License

TrackVault is licensed under the GNU Affero General Public License
version 3.0 only (AGPL-3.0-only). The full text is in [LICENSE](LICENSE), and it
ships inside the container image at `/app/LICENSE`.

## Third-party data and licences

Dependencies keep their own licences, and so does everything the map draws:

| What | Licence |
| --- | --- |
| TrackVault itself | AGPL-3.0-only |
| Python and npm dependencies | their own; audited against `license-policy.json` |
| OpenStreetMap map data, packaged by Geofabrik | ODbL-1.0 |
| Noto Sans label glyphs | SIL Open Font License 1.1 |

Nothing here relicenses anybody else's work. The canonical list — every direct
dependency, its version, its purpose and its licence — is
[docs/legal/third-party-notices.md](docs/legal/third-party-notices.md), and it
ships in the image as `/app/THIRD_PARTY_NOTICES.md`.
