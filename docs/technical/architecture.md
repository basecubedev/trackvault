# Architecture

GPX-View is **a self-hosted, source-agnostic activity and route archive for
recorded and planned geospatial tracks.**

It is not a Locus Map viewer and not a GPX viewer. GPX is the first supported
exchange format, not the internal model. The application normalizes data from
different sources and formats and then analyses it format-independently.

Example sources: Locus Map, Komoot, Garmin, Wahoo, OsmAnd, GPSLogger, other
applications. Example formats: GPX, FIT, TCX, KML, GeoJSON.

That list is a direction, **not an implementation promise**. Only GPX is intended
as the first adapter, and no importer is implemented yet.

The guiding principle remains:

> Simple architecture, explicit contracts, one owner per concern.

## The central invariant

```
Input format != domain model

GPX    != Track
FIT    != Track
Locus  != Track
Komoot != Track
```

```
Raw source
    │
    ▼
Format adapter / importer
    │
    ▼
Canonical normalized track
    │
    ├── Classification
    ├── Analysis
    ├── Statistics
    └── Presentation
```

From the canonical normalized track onwards, the core application must not need to
know which exchange format a track originally came from. Format and source
information survives as metadata, but it does not shape the internal business
model.

A future FIT or TCX importer therefore plugs into the same normalization boundary
and requires no change to classification, analysis, statistics or presentation.

## Layers

```
gpx_view.domain          innermost, technology-free
      ^
gpx_view.application     use cases, ports
      ^                        ^
gpx_view.api             gpx_view.infrastructure
      ^                        ^
gpx_view.main            composition root
```

### `gpx_view.domain`

Owns the business vocabulary and rules: track concepts, track kind, activity,
metric provenance, classification results, business invariants and -- later --
the analysis algorithms.

The domain does **not** know: GPX XML, FIT SDKs, Locus extensions, Komoot XML,
FastAPI, SQLAlchemy, SQLite, file system paths, Docker, HTTP.

It uses the Python standard library only, and not even the standard library's
infrastructure corners (`xml`, `sqlite3`, `pathlib`, `os`, `json`, `csv`, `http`,
`urllib`). It must not import `gpx_view.api`, `gpx_view.application`,
`gpx_view.infrastructure` or `gpx_view.config`.

Current content: `TrackKind`, `Activity`, `MetricProvenance`,
`ClassificationResult`, `TrackClassification`. Deliberately no `NormalizedTrack`
record yet -- see below.

### `gpx_view.application`

Owns use cases and business orchestration. Planned: `ImportTrack`, `ListTracks`,
`GetTrack`, `CalculateStatistics`, `OverrideClassification`. None is implemented.

It orchestrates domain rules and ports, and knows no concrete parser. It must not
import FastAPI, `gpx_view.api` or concrete infrastructure adapters, and no GPX,
FIT or TCX type may appear here.

### `gpx_view.infrastructure`

Owns concrete adapters and is the only layer that knows formats and storage.
Planned packages: `gpx/`, `fit/`, `tcx/`, `filesystem/`, `database/`.

A format adapter ends at a clear normalization boundary. GPX-specific XML
structures must not travel through the application: the adapter produces the
canonical normalized track plus source metadata, and stops there.

It implements contracts declared by the inner layers and must not import
`gpx_view.api`.

### `gpx_view.api`

The HTTP projection, nothing more: validate the request, call an application use
case, project the result. No classification or analysis heuristic in a route.

Current content: `GET /healthz`.

### Future browser UI

Also projection only. Browser state is never business authority; every value the
UI shows must be recomputable by the backend.

### `gpx_view.main`

Composition root. Creates the FastAPI application and wires routers. No business
logic.

### `gpx_view.config`

The only configuration mechanism, reading `GPX_VIEW_*` environment variables via
`pydantic-settings`. No business logic, no second config system.

## Canonical normalized track

Every importer normalizes onto one shared model. Conceptually:

```
NormalizedTrack
├── identity
├── title
├── activity
├── geometry
│   ├── segments
│   └── points
├── temporal data
├── source metadata
└── classification
    ├── detected result
    └── user override
        └── effective_kind
```

There is deliberately **no** `kind` field beside `classification`. Two storable
kinds would be two authorities that can disagree:

```
TrackClassification
    detected
    user override
        ↓
    effective_kind          ← the only authority for the effective TrackKind
```

A convenience accessor on the normalized track is allowed, but only as a
projection of `classification.effective_kind`. It must never be an independently
assignable value.

Rule:

> The normalized business track contains no GPX-, FIT-, Garmin-, Locus- or
> Komoot-specific mandatory fields.

Source-specific information belongs in source metadata or in the raw import layer.

The record itself is **not implemented yet** on purpose: its field set is the
output contract of the first importer, and we do not yet know that contract. What
is fixed is the boundary, not the field list.

## Raw import authority

> The original import is immutable source evidence.

A raw import records the *source* only — the bytes and the circumstances they
arrived under:

```
RawImport
─────────────────────────
identity  (the content hash)
sha256
size
original filename
received/imported timestamp
media/format hint
immutable stored bytes
input-channel metadata
```

*Implemented as `gpx_view.domain.RawImport` and `gpx_view.domain.InputChannel`.*

Invariants:

- Original files are never modified on import.
- Reprocessing must always be possible from the original again.
- Parser and analysis upgrades may regenerate normalized data without touching the
  source file.
- A normalized database projection is not a replacement for the original source.
- Raw data is not deleted just because the current parser version succeeded.
- The filename is display metadata. It never authorises a storage location; the
  canonical path of the stored bytes is derived from the content hash.

## Processing provenance

Which importer produced normalized data, in which version, is **not** a property
of the imported file. It belongs to the processing step:

```
ProcessingRun
─────────────────────────
raw import identity
importer name
importer version
normalization schema version
processed timestamp
status  (succeeded / failed)
error code on failure
```

*Implemented as `gpx_view.domain.ProcessingRun`, `gpx_view.domain.ProcessingStatus`
and `gpx_view.domain.NORMALIZATION_SCHEMA_VERSION`.*

The resulting invariant, enforced by
`tests/contract/test_raw_import_contracts.py`:

> Reprocessing the same `RawImport` with importer v2 must not mutate the
> `RawImport`.

A newer importer adds another run, replaces the normalized projection and the
detected classification, and leaves both the raw bytes and an explicit user
override untouched.

A failed run is a named, recoverable state: it references its raw import and
states a stable error code, so the file can be reprocessed once the importer
learns to read it.

## Duplicate detection

Two distinct levels, and they must not be conflated:

| Level | Meaning |
| --- | --- |
| `exact_duplicate` | byte-identical import object, SHA-256 identical |
| `semantic_duplicate` | different files that may describe the same activity, e.g. a Locus GPX, a Garmin FIT and a Komoot export of one ride |

```
exact_duplicate != semantic_duplicate
```

The semantic case is substantially harder and will be developed separately. No
heuristic for it is implemented, and none may be added prematurely.

## Vendor and format extensions

GPX supports extensions, and other formats carry vendor-specific fields.

- Known extensions may later be normalized into business fields.
- Unknown extensions must not needlessly fail the parse.
- Relevant unknown source metadata should be preservable for later reprocessing.
- Domain models still must not adopt arbitrary XML fragments as business fields.

No extension system is implemented.

## Import port

Conceptually, each format is one adapter behind one port:

```
GPXImporter ─┐
FITImporter ─┼──►  normalized import result
TCXImporter ─┘
```

The port itself (`TrackImporter` as a `typing.Protocol`) is **not implemented
yet**, because its return type is the canonical normalized track that we have
deliberately not designed. A protocol whose result type is a guess is not a
contract. The boundary is documented and enforced by the architecture contract
tests; the protocol lands together with the first importer.

When it lands: a `Protocol` plus, only if genuinely needed, a small
registry/dispatcher. No `AbstractImporterFactory`, `BaseImporterManager` or
`ImporterCoordinatorFactory`. Keep it boring.

## Single import authority

There will be several ways to get data in:

```
manual upload
import folder (e.g. a phone auto-sync target directory)
future API import
```

They are input paths, not pipelines. All of them must run through the same
canonical use case:

```
Web upload ───┐
Import folder ┼──►  ImportTrack use case
CLI / API ────┘
                        │
                        ▼
                 importer adapter
                        │
                        ▼
                  normalization
                        │
                        ▼
                   persistence
```

Explicitly forbidden:

```
Web upload    → parser A → DB
Folder watch  → parser B → DB
CLI           → parser C → DB
```

No file system watcher is implemented, and none may be added in the current scope.

## Enforcement

The boundaries above are not a convention, they are an executable contract.
`tests/contract/test_architecture_contract.py` parses every module in
`src/gpx_view` with `ast` and checks:

- the allowed internal import table per layer,
- that the domain uses the standard library only,
- that the domain imports no web, persistence, format or file system module,
- that domain, application and api import no concrete parser or XML module,
- that domain, application and api contain no vendor-specific identifiers and do
  not branch on vendor name literals.

`tests/contract/test_documentation_contract.py` checks that this document and
`contracts.md` still state the invariants the code relies on.

Changing a boundary means changing this document and the contract test in the same
commit.

## Single source of truth

Exactly one component owns each concern. Everything else is a projection.

| Concern | Authority |
| --- | --- |
| What was imported | the raw import: the original file, byte-identical, plus its content hash |
| Normalized track data | the canonical normalized track model |
| Detected classification | the classifier result, with confidence, evidence and method version |
| Effective classification | an explicit user override if present, otherwise the detected kind |
| Activity | explicit source metadata or the user, never a guess |
| Calculated statistics | the analysis layer, computed from the canonical normalized track |
| Metric meaning | the metric's provenance: measured, derived or estimated, never silently discarded |
| Actual aggregates | tracks whose effective kind is `RECORDED` |
| Planned aggregates | tracks whose effective kind is `PLANNED` |
| Source metadata | evidence and provenance information, never business authority |
| Import | the single canonical `ImportTrack` use case |
| HTTP representation | projection only |
| Browser/frontend state | projection only |
| Configuration | `gpx_view.config` backend application settings |

Consequences:

- No import adapter decides business meaning on its own; it produces evidence, the
  classification step decides.
- No UI may hold a value the backend cannot recompute.
- Statistics are never stored in two places with independent update paths.
- An automatic reclassification never silently overwrites a user correction.

## Deliberately deferred

Open on purpose, and not to be pre-empted by "preparation" code:

- `NormalizedTrack` record and the `TrackImporter` protocol
- GPX parser library, and any FIT/TCX/KML/GeoJSON support
- persistence schema (SQLite is the currently intended first backend)
- frontend technology and map library
- analysis algorithms (distance, moving time, elevation smoothing)
- classification heuristic (only its result shape is fixed)
- duplicate detection, exact and semantic
- import folder watcher, file upload, authentication
- Android client or companion app

See `docs/adr/0001-project-foundation.md`,
`docs/adr/0002-source-agnostic-track-model.md` and
`docs/technical/contracts.md`.
