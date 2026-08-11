# GPX-View

A self-hosted, source-agnostic activity and route archive for recorded and planned
geospatial tracks.

Tracks may come from Locus Map, Komoot, Garmin, Wahoo, OsmAnd, GPSLogger or other
applications, in formats such as GPX, FIT, TCX, KML or GeoJSON. GPX is intended as
the first supported exchange format, and the first one implemented — an adapter,
not the internal model:

```
Raw source → format adapter → canonical normalized track → analysis → statistics → presentation
```

## Project status

The first production import slice works end to end:

```
GPX file → safe parse → normalized track → evidence → classification
        → SQLite + byte-identical raw copy → HTTP read API
```

Implemented and tested:

- a **GPX 1.1 and 1.0 adapter** that fails closed on hostile XML, enforces
  configurable limits, reads the core fields and normalizes an explicit activity
- a **source-agnostic normalized track**: points, segments with their boundaries
  preserved, provenance, and one file producing any number of tracks
- a **conservative classifier** answering recorded, planned or unknown from
  neutral evidence — never from a source name
- **immutable raw imports** kept byte-identically, content-addressed, with exact
  duplicates recognised by hash
- **versioned SQLite persistence** with explicit migrations and transactions,
  where reprocessing keeps a user's correction
- one canonical **`ImportTracks` use case** behind every input path
- a **read API** plus an executable classification override

**Not implemented:** distance, moving time, elevation gain, speed profiles,
monthly and yearly statistics, any map or chart, any frontend, FIT/TCX/KML/GeoJSON
adapters, semantic duplicate detection, authentication and any file upload.

See [docs/technical/contracts.md](docs/technical/contracts.md) for the invariants
the remaining features have to satisfy, and
[docs/adr/0003-import-and-persistence-model.md](docs/adr/0003-import-and-persistence-model.md)
for why this slice looks the way it does.

## Requirements

- Python 3.13 — exactly, the supported range is `>=3.13,<3.14`. Packaging metadata,
  `.python-version`, the Ruff and mypy targets, the container base image and CI all
  state that same range, and a contract test keeps them aligned.
- [uv](https://docs.astral.sh/uv/) — pinned to the same patch version in CI and in
  the container build
- Docker (optional, for the container workflow)

## Development setup

```bash
uv sync
```

## Import tracks

Importing is a server-side operator action: there is no upload endpoint, because
there is no authentication yet.

```bash
uv run gpx-view import path/to/track.gpx another.gpx
uv run gpx-view scan          # imports new files from GPX_VIEW_IMPORT_DIR
uv run gpx-view reprocess <sha256>    # normalize a stored source again
uv run gpx-view reprocess --failed    # ...every source whose last attempt failed
uv run gpx-view reprocess --outdated  # ...every source an upgrade would change
uv run gpx-view processing-status <sha256>
```

`import` and `scan` go through the same `ImportTracks` use case. Importing the
same bytes twice is a no-op — as long as the archive can still produce the bytes
it says it holds. If the managed copy was lost, offering the same file again
restores it and reports `repaired`; if the copy is there but its content no
longer matches its hash, the import fails rather than overwriting the evidence.
The import directory itself is never modified.

`reprocess` answers a different question. Recognising a content hash makes an
import a duplicate; whether the *normalized* data of those bytes is still current
is not the same statement. After an importer or classifier upgrade — or once a
file that could not be read becomes readable — reprocessing regenerates the
normalized data from the archive's own byte-identical copy. It keeps the source
untouched, keeps every earlier processing run, and never overwrites a
classification you corrected yourself. Nothing reprocesses itself: it is always
an explicit operator action.

`--outdated` selects the sources whose current normalized generation was not
produced by the processing this build installs; `--failed` selects the ones whose
newest attempt failed, which is a different set. `processing-status` shows both
sides for one source:

```text
raw import:     a95629ddc1ae...
current run:    2
latest attempt: 3 failed invalid_gpx
tracks:         1
importer:       1 -> installed 2
normalization:  1 -> installed 2
classifier:     1 -> installed 2
outdated:       yes
```

All persistent data is created for your user only: directories `0700`, files
`0600`, including the database, its journal files and the managed raw copies.

## Run the server

```bash
uv run uvicorn gpx_view.main:app --reload --port 8080
curl http://127.0.0.1:8080/healthz
# {"status":"ok"}
```

Interactive API docs: <http://127.0.0.1:8080/docs>

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` | Liveness probe |
| `GET /api/v1/tracks` | List stored tracks, without geometry |
| `GET /api/v1/tracks/{id}` | Read one track |
| `GET /api/v1/tracks/{id}/geometry` | Read its segments and positions |
| `PUT /api/v1/tracks/{id}/classification` | Correct the kind: `{"kind": "recorded"}` |
| `DELETE /api/v1/tracks/{id}/classification` | Withdraw the correction |

Geometry is a separate request on purpose: a long recording holds tens of
thousands of positions, and `point_count` in the listing tells a client what it
would be asking for.

An explicit correction outranks the classifier and survives reprocessing.

> **No authentication.** These endpoints assume a trusted network — a self-hosted
> deployment reachable only from your own machines, or one behind a reverse proxy
> that authenticates. Do not expose them to the internet as they are.

## Tests

```bash
uv run pytest                 # default suite, no network, no Docker
uv run pytest -m contract      # contract tests only
uv run pytest -m gpx           # the GPX adapter
uv run pytest -m persistence   # SQLite and managed raw storage
uv run pytest -m storage       # the managed raw storage authority
uv run pytest -m reprocessing  # reprocessing and the current generation
uv run pytest -m docker        # opt-in, needs a running Docker daemon
uv run pytest -m local_tracks  # opt-in, needs private files under ./import-tracks
uv run pytest --cov           # with coverage
```

The default suite needs no network, no Docker, no real GPS files and no writable
`/data`: every test composes against `tmp_path`. Committed fixtures under
`tests/fixtures/gpx/` are synthetic throughout.

## Quality checks

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run python -m compileall -q src tests
```

## Docker

```bash
docker compose build
docker compose up -d
curl http://127.0.0.1:8080/healthz

docker compose exec gpx-view gpx-view import /tmp/track.gpx
curl http://127.0.0.1:8080/api/v1/tracks

docker compose down          # keeps the data volume
docker compose down -v       # also deletes it
```

The container runs as a non-root user (uid 10001) on port 8080 with a healthcheck
against `/healthz`.

Persistent data lives in the **named volume** `gpx-view-data` at `/data`. Docker
initialises a named volume from the image, so it arrives owned by the runtime user
and is writable without `chmod 777` and without running as root — which a host
bind mount is not, because it keeps the host directory's ownership.

To use a host directory deliberately, match the ownership instead of loosening the
permissions:

```yaml
volumes:
  - ./data:/data
```

```bash
mkdir -p data && sudo chown -R 10001:10001 data
```

Back up `/data` as a whole: it holds the database, its journal files and every
original import.

## Configuration

All configuration goes through `gpx_view.config`. Environment variables use the
`GPX_VIEW_` prefix:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GPX_VIEW_HOST` | `127.0.0.1` | Interface the HTTP server binds to |
| `GPX_VIEW_PORT` | `8080` | TCP port |
| `GPX_VIEW_DATA_DIR` | `data` | Holds *all* persistent data: the database and the original imports |
| `GPX_VIEW_IMPORT_DIR` | unset | Directory `gpx-view scan` reads. Unset disables the feature. Never modified. |
| `GPX_VIEW_IMPORT_MAX_BYTES` | `16777216` | Largest accepted input file |
| `GPX_VIEW_IMPORT_MAX_TRACKS` | `100` | Most tracks in one document |
| `GPX_VIEW_IMPORT_MAX_SEGMENTS_PER_TRACK` | `1000` | Most segments in one track |
| `GPX_VIEW_IMPORT_MAX_POINTS` | `500000` | Most positions in one document |

The limits bound a hostile or broken file; the defaults comfortably cover an
ordinary long GPS recording.

The typical deployment is phone → auto-sync → a folder on the server →
`GPX_VIEW_IMPORT_DIR`. The archive copies what it accepts into its own `/data` and
leaves that folder exactly as it found it.

## Documentation

- [Architecture](docs/technical/architecture.md) — layers, boundaries, normalization pipeline, single source of truth
- [Business contracts](docs/technical/contracts.md) — track kind, classification, provenance, actual vs. planned
- [ADR 0001](docs/adr/0001-project-foundation.md) — foundation decisions and deferred choices
- [ADR 0002](docs/adr/0002-source-agnostic-track-model.md) — why GPX is an adapter, not the domain
- [ADR 0003](docs/adr/0003-import-and-persistence-model.md) — the import and persistence model of this slice
- [ADR 0004](docs/adr/0004-processing-generations-and-candidate-identity.md) — candidate identity, current generations and reprocessing
- [ADR 0005](docs/adr/0005-processing-currency-and-local-filesystem-authority.md) — processing currency, raw-import integrity, namespace-aware extensions, filesystem authority and permissions
- [Documentation index](docs/README.md)

## Agent rules

Automated agents and contributors follow
[docs/developer/agent-rules.md](docs/developer/agent-rules.md), the single
canonical rule source. `AGENTS.md`, `CLAUDE.md` and
`.github/copilot-instructions.md` only point there.

## Privacy

GPX and FIT files are personal movement data. Personal recordings are never
committed; `*.gpx` is git-ignored except for synthetic fixtures under
`tests/fixtures/`. Coordinates and raw payloads never appear in normal logs or
exception messages.
