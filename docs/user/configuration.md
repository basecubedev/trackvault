# Configuration, storage and security

## Environment variables

All configuration goes through environment variables with the `TRACKVAULT_`
prefix. There is no second prefix and no alias: a variable that is not spelled
this way is not read.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRACKVAULT_HTTP_PORT` | `8081` | Host port `compose.yaml` and the installer publish the container's 8080 on |
| `TRACKVAULT_HOST` | `127.0.0.1` | Interface the HTTP server binds to |
| `TRACKVAULT_PORT` | `8080` | Port the application binds *inside* the container |
| `TRACKVAULT_DATA_DIR` | `data` | Holds *all* persistent data: the database and every original import |
| `TRACKVAULT_IMPORT_DIR` | unset | Directory `trackvault scan` reads. Unset disables the feature. Never modified. |
| `TRACKVAULT_BACKUP_DIR` | `backups/` beside the data directory | Where `trackvault backup create` writes. Outside the data directory on purpose: a backup kept inside what it protects is lost with it. |
| `TRACKVAULT_UPLOAD_ENABLED` | `false` | Whether the browser interface may add files. Off unless you set it: uploading is unauthenticated, and every read works without it. |
| `TRACKVAULT_IMPORT_MAX_BYTES` | `16777216` | Largest accepted input file |
| `TRACKVAULT_IMPORT_MAX_TRACKS` | `100` | Most tracks in one document |
| `TRACKVAULT_IMPORT_MAX_SEGMENTS_PER_TRACK` | `1000` | Most segments in one track |
| `TRACKVAULT_IMPORT_MAX_POINTS` | `500000` | Most positions in one document |
| `TRACKVAULT_TIMEZONE` | `UTC` | IANA zone month and year boundaries are drawn in, e.g. `Europe/Berlin` |
| `TRACKVAULT_WEB_DIR` | `web/dist` | Built browser assets. Nothing there means the API is served alone. |
| `TRACKVAULT_MAPS_ENABLED` | `true` | Whether offline map packages may be installed. `false` refuses every management action; installed maps keep working. |
| `TRACKVAULT_MAP_MAX_DOWNLOAD_BYTES` | `8589934592` | Largest map package this deployment will fetch |

`TRACKVAULT_HTTP_PORT` and `TRACKVAULT_PORT` are two different ports on purpose:
one is what Docker publishes on the host, the other is what the process binds
inside the container. The image's health check asks the second, so it keeps
working however you publish the first.

The import limits bound a hostile or broken file; the defaults comfortably cover
an ordinary long GPS recording.

`TRACKVAULT_TIMEZONE` defaults to `UTC` rather than to the host's zone, because a
container inherits whatever its image carries and the same archive would
otherwise report different monthly totals on two machines. An unknown zone fails
at start-up rather than falling back silently. **It is worth setting before you
read a monthly total**: it decides which month a late-evening activity counts
towards.

[`.env.example`](../../.env.example) lists the same variables with the reasoning
beside each one.

## The data directory

Everything persistent lives under `TRACKVAULT_DATA_DIR`, which is `/data` in the
container:

```
<data dir>/trackvault.sqlite3        the database
<data dir>/trackvault.sqlite3-wal    its journal files
<data dir>/trackvault.sqlite3-shm
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

The repository's own [`compose.yaml`](../../compose.yaml) uses a **named volume**
instead, and the image's own user: Docker initialises a named volume from the
image, so it arrives owned correctly whoever the developer is. Both avoid the
same trap from opposite directions — see
[Installation](installation.md#running-from-a-checkout-instead).

## Security and the trusted network

> TrackVault currently assumes a **trusted network or an authenticated reverse
> proxy**.

There is no authentication. Anyone who can reach the port can read every track
in the archive — where you live, when you are out, and how long you were gone —
and can change a track's classification, title and notes.

- Run it on your own network, or behind a reverse proxy that authenticates.
- **Do not forward the port to the internet.**
- **Uploading is off by default, and unauthenticated when you enable it.**
  Writing is the one thing an unauthenticated caller must not be able to do
  because a container happened to start, so `TRACKVAULT_UPLOAD_ENABLED` defaults
  to `false` and turning it on is a statement about your network. The endpoint
  itself is bounded — one file per request, the size limit applied while
  reading, no archive or multipart parsing, the filename never used as a
  location — but bounded is not authenticated. Why the capability exists at all
  is recorded in [ADR 0011](../adr/0011-web-upload.md).

What the application does do:

- every response carries `X-Content-Type-Options`, `Referrer-Policy:
  no-referrer`, a frame policy and a small `Content-Security-Policy`.
  `no-referrer` is not boilerplate here: it stops a basemap request telling a
  tile provider the address of your archive;
- the container runs as a non-root user (uid 10001);
- errors answer one stable envelope and never carry a path, a coordinate or a
  stack trace;
- nothing reaches a network unless you install an offline map.

## Privacy

GPS files are personal movement data — a record of where a real person was and
when.

- Coordinates and raw payloads never appear in normal logs or exception
  messages, at any log level.
- Original imports are never modified, and parsing never writes near the file it
  read.
- Nothing leaves the deployment except the three map actions you press:
  refreshing the catalog, installing, updating.
- In this repository, personal recordings are never committed; `*.gpx` is
  git-ignored except for synthetic fixtures under `tests/fixtures/`.
