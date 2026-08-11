# GPX-View

A self-hosted, source-agnostic activity and route archive for recorded and planned
geospatial tracks.

Tracks may come from Locus Map, Komoot, Garmin, Wahoo, OsmAnd, GPSLogger or other
applications, in formats such as GPX, FIT, TCX, KML or GeoJSON. GPX is intended as
the first supported exchange format — it is an adapter, not the internal model:

```
Raw source → format adapter → canonical normalized track → analysis → statistics → presentation
```

## Project status

Foundation and architecture only. This repository currently contains the project
scaffold, the documented architecture and business contracts, a small set of domain
value types, and a single health endpoint that proves the chain from package to
application bootstrap to FastAPI to tests to Docker.

**GPX import and track analysis are not implemented yet.** Neither is any other
format, nor the dashboard, the map view, the elevation and speed profiles, the
database, duplicate detection, the classification heuristic, file upload, an import
folder watcher or any frontend.

What *is* implemented and tested: `TrackKind` (recorded / planned / unknown),
`Activity`, `MetricProvenance` (measured / derived / estimated),
`ClassificationResult` with required evidence, and `TrackClassification` with the
rule that an explicit user override beats the classifier. See
[docs/technical/contracts.md](docs/technical/contracts.md) for the invariants the
remaining features have to satisfy.

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

## Run the server

```bash
uv run uvicorn gpx_view.main:app --reload --port 8080
curl http://127.0.0.1:8080/healthz
# {"status":"ok"}
```

Interactive API docs: <http://127.0.0.1:8080/docs>

## Tests

```bash
uv run pytest                 # default suite, no network, no Docker
uv run pytest -m contract     # contract tests only
uv run pytest -m docker       # opt-in, needs a running Docker daemon
uv run pytest --cov           # with coverage
```

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
mkdir -p data
docker compose build
docker compose up -d
curl http://127.0.0.1:8080/healthz
docker compose down
```

The container runs as a non-root user on port 8080 and has a healthcheck against
`/healthz`. `./data` is mounted at `/data` to reserve a stable path for later
persistence; nothing is written there yet.

## Configuration

All configuration goes through `gpx_view.config`. Environment variables use the
`GPX_VIEW_` prefix:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GPX_VIEW_HOST` | `127.0.0.1` | Interface the HTTP server binds to |
| `GPX_VIEW_PORT` | `8080` | TCP port |
| `GPX_VIEW_DATA_DIR` | `data` | Directory for local application data |

## Documentation

- [Architecture](docs/technical/architecture.md) — layers, boundaries, normalization pipeline, single source of truth
- [Business contracts](docs/technical/contracts.md) — track kind, classification, provenance, actual vs. planned
- [ADR 0001](docs/adr/0001-project-foundation.md) — foundation decisions and deferred choices
- [ADR 0002](docs/adr/0002-source-agnostic-track-model.md) — why GPX is an adapter, not the domain
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
