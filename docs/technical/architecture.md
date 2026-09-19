# Architecture

TrackVault is **a self-hosted, source-agnostic activity and route archive for
recorded and planned geospatial tracks.**

It is not a Locus Map viewer and not a GPX viewer. GPX is the first supported
exchange format, not the internal model. The application normalizes data from
different sources and formats and then analyses it format-independently.

Example sources: Locus Map, Komoot, Garmin, Wahoo, OsmAnd, GPSLogger, other
applications. Example formats: GPX, FIT, TCX, KML, GeoJSON.

That list is a direction, **not an implementation promise**. GPX is the first and
currently only implemented adapter.

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
trackvault.domain          innermost, technology-free
      ^
trackvault.application     use cases, ports
      ^                          ^
trackvault.api             trackvault.infrastructure
      ^                          ^
trackvault.main            composition root
```

### `trackvault.domain`

Owns the business vocabulary and rules: track concepts, track kind, activity,
metric provenance, classification results, business invariants and -- later --
the analysis algorithms.

The domain does **not** know: GPX XML, FIT SDKs, Locus extensions, Komoot XML,
FastAPI, SQLAlchemy, SQLite, file system paths, Docker, HTTP.

It uses the Python standard library only, and not even the standard library's
infrastructure corners (`xml`, `sqlite3`, `pathlib`, `os`, `json`, `csv`, `http`,
`urllib`). It must not import `trackvault.api`, `trackvault.application`,
`trackvault.infrastructure` or `trackvault.config`.

It also holds the `maps/` package -- `MapRegionId`, `MapBounds`,
`MapAttribution`, `MapTileSchema`, `MapPackage`, `MapInstallState`,
`MapJobState` and `select_coverage`. Offline maps are a capability of their own
beside the track model, not part of it: nothing in `NormalizedTrack`,
`TrackAnalysis` or the statistics knows a map exists, and the only thing the two
share is a rectangle.

Current content: `TrackKind`, `Activity`, `MetricProvenance`, `EvidenceCode`,
`ClassificationResult`, `TrackClassification`, the `classify` rules, `TrackPoint`,
`TrackSegment`, `ImportedTrack`, `NormalizedTrack`, `SourceMetadata`, `RawImport`,
`InputChannel`, `ProcessingRun`, `ProcessingStatus`, `ProcessingProfile`,
`is_processing_current`, and the `analysis/` package: `AnalysisProfile`,
`MetricName`, `MetricUnit`, `MetricValue`, `AnalysisQuality`, `TrackAnalysis` and
`analyze_track` with the distance, movement and elevation algorithms.

### `trackvault.application`

Owns use cases and business orchestration. Implemented: `ImportTracks` (the single
canonical import authority), `ReprocessRawImport` (regenerating normalized data
from a source already held, and selecting which sources need it), `AnalyzeTrack`
(the single canonical analysis authority, and the selection of what needs
analysing again), `GetProcessingStatus` (what happened to one source),
`GetTrackAnalysis` (what one track's metrics amount to), `GetYearStatistics` and
`GetMonthlyStatistics` (actual, planned and unknown totals), and `TrackQueries`
(list, read, geometry, override, reset).

Importing and reprocessing share one `NormalizeRawImport`: detect, normalize,
classify, append a run, become the current generation. They differ only in where
the bytes come from and in what the caller is told. A second copy of that step
would be a second import pipeline, and it would be the copy that runs less often
-- the one nobody notices going wrong.

`maps/` holds the offline map use cases: `GetMapCatalog`, `InstallMapPackage`
(install and update are one authority), `RemoveMapPackage`, `RecoverMapStorage`,
`ListInstalledMaps`, `SelectMapCoverage` and `GetMapInstallJob`, behind the
ports `MapPackageProvider`, `MapPackageStorage`, `MapPackageInspector`,
`MapPackageRepository`, `MapCatalogCache`, `MapInstallationService` and
`MapTileSource`.

`export.py` owns the outbound direction, and it is deliberately two things
rather than one. `ExportRawSource` hands back the bytes that arrived,
byte-identical; `ExportTrackDocument` renders the *current normalized
generation* into an exchange format through the `TrackDocumentWriter` port. The
first is evidence and never changes; the second is what this build currently
believes a track is, and legitimately changes when the importer learns
something. A single "export" would have made those one word for two promises.

`archive.py` owns the whole deployment as one portable object: the
`ArchiveManifest` format, the `compatibility_of` rule that decides what may be
restored, and the `CreateArchive` / `RestoreArchive` use cases. It reaches the
container through `ArchiveBuilder` and `ArchiveExtractor` and names no path,
exactly as the map use cases name no URL.

`diagnostics.py` owns the *judgement* half of `trackvault doctor`: infrastructure
observes facts -- a schema says 9, a file is missing -- and this decides which
of them are warnings and which are errors. Keeping the rule out of the code that
reads the disk is what makes every severity testable without arranging a broken
one.

It also owns the ports -- `TrackImporter`, `TrackRepository`, `RawImportStore`,
`TrackDocumentWriter`, `ArchiveBuilder`, `ArchiveExtractor`, `Clock` -- the
import limits, the public error codes, `InstalledProcessing` and
`InstalledAnalysis`: the one statement of what processing and what analysis this
build applies, and therefore the one place each that answers whether a stored
generation, or a stored set of metrics, is still current.

It orchestrates domain rules and ports, and knows no concrete parser. It must not
import FastAPI, `trackvault.api` or concrete infrastructure adapters, and no GPX,
FIT or TCX type may appear here.

### `trackvault.infrastructure`

Owns concrete adapters and is the only layer that knows formats and storage.
Implemented packages: `gpx/` (the GPX 1.1/1.0 adapter -- the importer, the
writer, and the table of extension schemas both understand), `database/` (the
archive's own SQLite, its consistent snapshots and its read-only inspection),
`filesystem/` (managed raw storage and the import directory boundary),
`archive/` (the `tar.gz` container, its manifest codec and the safe extraction),
`maps/` (managed map storage, the MBTiles reader, the bounded HTTP transfer, the
Geofabrik adapter, the catalog cache and the install worker), plus `clock.py`,
`private_data.py`, `diagnostics.py` and `assembly.py`. Planned: `fit/`, `tcx/`.

The GPX package holds both directions of the adapter boundary. The importer
turns documents into normalized tracks; the writer turns normalized tracks into
documents. Neither lets an XML type travel inwards, and a future FIT writer is
another adapter behind the same port rather than a change to a use case.

`database/` splits three ways for three different jobs. `store.py` is the
repository. `snapshot.py` captures a database consistently and fails loudly,
because a backup that is silently wrong is worse than none. `inspection.py`
reads a database file read-only and answers quietly, because a diagnostic must
report a problem rather than become one -- opening a missing database the
ordinary way would *create* it, and the fresh deployment somebody asked about
would no longer be fresh.

Two modules may name the SQLite driver, and the split is deliberate.
`database/` owns the archive's own database. `maps/mbtiles.py` reads a *foreign*
container that happens to be SQLite -- a downloaded map package -- strictly
read-only, through its own connections, never touching the archive's file. That
is a format adapter, and keeping it out of the database package is what stops
"the store" from meaning two things.

`trackvault.release` is a leaf module holding the build's version string. Every
layer but the domain may read it; the first thing that needed it further in was
the User-Agent an outbound map download presents.

The string itself is read from the installed distribution, and the distribution
got it from the **Git tag** the build was made from -- `hatch-vcs` resolves it
while the wheel is built. The repository therefore states no version anywhere:
releasing is `git tag v1.2.3` and nothing else, and there is no second place
that can disagree with the tag. A build that cannot see the tags is *told* the
version through `SETUPTOOLS_SCM_PRETEND_VERSION` rather than guessing one; the
container build is exactly that case, because its context deliberately carries
no `.git`, and the argument that feeds it also writes the image label so the two
cannot describe different releases.

`assembly.py` builds the object graph both entry points share -- the HTTP
application and the command line -- so a second, subtly different wiring cannot
appear.

A format adapter ends at a clear normalization boundary. GPX-specific XML
structures must not travel through the application: the adapter produces the
canonical normalized track plus source metadata, and stops there.

It implements contracts declared by the inner layers and must not import
`trackvault.api`.

### `trackvault.api`

The HTTP projection, nothing more: validate the request, call an application use
case, project the result. No classification or analysis heuristic in a route.

Current content:

```
GET    /healthz
GET    /api/v1/system/info
GET    /api/v1/tracks
GET    /api/v1/tracks/{track_id}
GET    /api/v1/tracks/{track_id}/geometry
GET    /api/v1/tracks/{track_id}/profile
GET    /api/v1/tracks/{track_id}/analysis
PUT    /api/v1/tracks/{track_id}/classification
DELETE /api/v1/tracks/{track_id}/classification
PATCH  /api/v1/tracks/{track_id}/metadata
GET    /api/v1/statistics/years
GET    /api/v1/statistics/year/{year}
GET    /api/v1/statistics/year/{year}/monthly
```

`/system/info` describes the **build**, not the machine: the release, the schema
version, the aggregation zone and both currency profiles. It carries no path, no
data directory and no configured provider, and it is what makes "why did this
number change?" answerable from the deployment rather than from a changelog.

`/statistics/years` answers which local years the archive holds tracks in,
newest first, over exactly the tracks a period may date. The interface used to
compute "this year and the eleven before it" from the reader's own clock, which
is a second authority on the calendar and wrong in both directions: it offered
years the archive had nothing for and hid the years of an archive nobody had
added to since 2019. The response also carries the archive's total track count,
because "nothing imported yet" and "nothing matches this selection" are opposite
instructions to whoever is reading.

Every response carries `X-Content-Type-Options`, `Referrer-Policy: no-referrer`,
`X-Frame-Options` and a `Content-Security-Policy` that names **every** source
the page may load: `default-src 'self'`, `script-src 'self'`, `connect-src
'self'`, `font-src 'self'`, plus `blob:` for MapLibre's worker and `data: blob:`
for the textures it builds in memory. The policy used to name no source at all,
because a configured basemap put a style on one host and its tiles, sprites and
glyphs on others; offline map packages removed that unknown, so the browser now
enforces the offline contract rather than only the test suite.

Metrics are a resource of their own rather than a field on the track. They have
their own lifecycle -- missing, outdated or freshly derived while the track
itself never changed -- and folding that status into the track would make "this
track exists" mean two things again. The track listing still carries a compact
analysis summary, so a list view costs one request rather than one per row.

Units are in the field names (`distance_m`, `moving_duration_s`) and an
underivable metric is `null`, never `0`. Statistics are asked for by `scope`
(`recorded`, `planned`, `unknown`) with no combined default, and every response
names the timezone its buckets were drawn in.

Errors answer one stable envelope:

```json
{"error": {"code": "track_not_found", "message": "no such track"}}
```

No path, query, coordinate or stack trace ever appears in a response body.

**There is no authentication.** The read and override endpoints assume a trusted
network: a self-hosted deployment reachable only from the owner's own machines, or
one placed behind a reverse proxy that authenticates. That assumption is also why
the upload endpoint is bounded and switchable -- see "Input paths" below.

Track endpoints project the **current generation only**. What happened across
processing runs is a separate question with a separate answer, and it is answered
on the command line rather than over HTTP: `/api/v1/imports/{sha256}/processing`
would open a resource family whose other verbs do not exist, and the operator who
can act on the answer is on the machine that holds the data anyway.

### The browser application

Also projection only. Browser state is never business authority; every value the
page shows is recomputable by the backend, which is why there is no state
library: a cache of the truth beside the truth is the browser's version of a
second authority.

It lives in `web/` -- TypeScript, React, Vite, MapLibre GL JS and Apache ECharts
-- and is built into static assets. In production the same process serves them,
so the page and the data it reads share an origin and there is no CORS
configuration to get wrong. `trackvault.api.web` owns that: one route resolves
inside the build directory, never answers an `/api` path with HTML, and hands
the entry page to anything else, so a deep link to `/tracks/123` survives a
refresh. Hashed assets are cacheable for a year and the entry page is not,
because its name never changes.

The page's types are **generated from this API's own OpenAPI document**
(`trackvault openapi`, deterministic, checked for drift in CI). A hand-written
`TrackResponse` that has drifted from the server is a bug that type-checks, and
it is the same failure the whole architecture is arranged against.

Two rules the interface exists to keep. **Absent is not zero**: a metric the
archive could not derive renders as a dash and is announced as unavailable.
**Nothing is distinguished by colour alone**: every state carries a word and a
glyph, because `current` and `invalid` are exactly the two a reader has to act
on differently.

The basemap comes from packages installed into this deployment and served by
it. The page asks `/api/v1/maps/coverage?bbox=…` which packages belong behind a
track and composes the MapLibre style itself: *selection* is a decision about
data and belongs to the backend, *colour and line width* are presentation and
belong to the page. A style document assembled in Python would put a design
system in the API layer.

Two themes and none -- Outdoor, Light, No basemap. Glyphs are SDF ranges
generated from Noto Sans by `scripts/generate_glyphs.py` and committed under
`web/public/fonts/`, so they ship with the page. No sprite sheet exists, because
no layer uses `icon-image`. Every URL in a composed style is same-origin.

Attribution comes out of the installed package's own metadata and is rendered
beside every map that is drawn. See `docs/legal/third-party-notices.md`.

A track list draws one small map per row, as a picture rather than a map
component, and keeps the finished picture in the browser's own IndexedDB so a
second visit does not draw it again. That store is **projection only** in the
same sense as everything else here: it holds a derived image, it is found by
identities the archive publishes -- the track's geometry identity and the
content hash of each package drawn under it -- and losing it costs a redraw and
nothing else. See `docs/technical/map-caching.md`.

### `trackvault.main`

Composition root. Creates the FastAPI application and wires routers. No business
logic.

### `trackvault.config`

The only configuration mechanism, reading `TRACKVAULT_*` environment variables via
`pydantic-settings`. No business logic, no second config system.

`TRACKVAULT_TIMEZONE` is the IANA zone month and year boundaries are drawn in. It
defaults to `UTC` rather than to the host zone -- a container inherits whatever
its image carries, and a local-time default would make the same archive report
different monthly totals on two machines. An unknown zone fails at start-up
instead of falling back silently.

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

*Implemented as `trackvault.domain.NormalizedTrack`, `TrackSegment`, `TrackPoint`
and `SourceMetadata`.* The field set is what the first importer actually needed,
not a guess:

| Type | Holds |
| --- | --- |
| `TrackPoint` | latitude, longitude, optional elevation, optional instant |
| `TrackSegment` | an ordered, non-empty run of points |
| `NormalizedTrack` | segments, source metadata, classification, optional title, activity |
| `ImportedTrack` | what an adapter produces: the same minus the classification, plus observed evidence |

Coordinates are range-checked and NaN and infinity are refused. Instants are
stored in UTC; which timezone they are *displayed* or bucketed in is a separate,
later decision.

A position also carries what a sensor measured beside it: a heart rate and a
cadence, both optional, both *measurements* passed through untouched. They were
kept out while no contract needed them; a recording that carries two thousand of
them and an interface that shows none is the contract. Power and temperature
stay out on the same rule as before -- nothing in this project has evidence for
them yet, and a field nothing fills is a field that lies about being supported.

A reading of zero is a reading. A cadence sensor on a coasting bike reports zero,
and folding that into "absent" would be the conflation this model refuses
everywhere else.

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

*Implemented as `trackvault.domain.RawImport` and `trackvault.domain.InputChannel`.*

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
processing profile
    importer name
    importer version
    normalization schema version
    classifier name
    classifier version
processed timestamp
status  (succeeded / failed)
error code on failure
```

*Implemented as `trackvault.domain.ProcessingRun`, `trackvault.domain.ProcessingStatus`
and `trackvault.domain.NORMALIZATION_SCHEMA_VERSION`.*

### Processing currency

```
RawImport
   ↓
ProcessingRun(profile)
   ↓
Current Generation
```

The five versions are one value, `ProcessingProfile`, because they answer one
question together: what would this source turn into today, and is that what is
stored? They used to live in three unrelated places -- importer and normalization
schema on the run, the classifier on each track's classification -- so a run that
produced no candidate recorded no classifier version at all, and no stored
generation could say which *combination* produced it.

`trackvault.domain.is_processing_current` is the only comparison, and
`trackvault.application.InstalledProcessing` is the only place that says what this
build installs -- one profile per installed adapter, because a GPX generation is
outdated when the GPX adapter moved on and a future FIT adapter's version says
nothing about it.

A generation is **not** current when the profile differs in any component, when
the run cannot prove which processing produced it, when there is no successful run
at all, when the adapter that wrote it is no longer installed, or when the stored
profile is *newer* than the installed one. The last one matters after a
downgrade: reporting newer data as up to date would mean never regenerating it.

Versions are data. A change to the normalized identity, the candidate set, the
evidence semantics or the interpretation of a field is a version bump; an
internal refactoring with identical output is not. Existing runs are never
back-filled to look current -- what ran back then is not knowable now, and a run
that cannot prove its processing counts as out of date rather than as current by
assumption.

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

### Import integrity

An exact duplicate is a no-op only while the archive can still produce the bytes
it says it holds:

```
DB metadata
+
hash-valid raw artifact
```

Those are two facts, and recognising the first says nothing about the second.
Answering "already imported" on the row alone means an archive that lost an
artifact keeps saying so to the only offer of those bytes it will ever get again.
`RawImportStore.integrity` reads and hashes the managed copy, and every caller
reads that one answer:

| State | Outcome |
| --- | --- |
| metadata + hash-valid artifact | `DUPLICATE` -- nothing to do |
| metadata + missing artifact | `REPAIRED` -- the copy is restored from the bytes offered again |
| metadata + wrong bytes | `FAILED`, `raw_storage_corrupt` |
| artifact without metadata | `IMPORTED` -- the artifact is adopted, not rewritten |

Repair is not a guess: the bytes hash to the digest the raw import is filed
under, which is the same proof the first import needed. It restores the copy and
nothing else -- no processing run is appended, because the normalized generation
was never in question.

Corruption fails closed. Disk corruption, tampering and a broken file system all
look alike, and the artifact is the only trace of any of them. Recovery is an
operator decision: restore the artifact from a backup, or delete it and offer the
source again, which then repairs.

An artifact without its row is what a crash between the two writes leaves behind
-- installing the bytes and committing the database transaction are not one
atomic act, and no file system makes them one. The honest crash windows are:

```
raw artifact installed, database transaction not committed   -> orphan, recovered on the next offer
database committed, artifact missing                         -> not reachable through the normal path,
                                                                and repairable if it ever happens
```

The semantic case is substantially harder and will be developed separately. No
heuristic for it is implemented, and none may be added prematurely.

## The GPX adapter

Supported: **GPX 1.1 and GPX 1.0**, told apart by the namespace of the root
element. A filename never influences detection, and neither does the `version`
attribute: the namespace is the reliable discriminator, so a mislabelled document
cannot be misread.

Detection reads the root element's namespaced name structurally, so a namespace
prefix changes nothing -- `<g:gpx xmlns:g="...">` is the same element as
`<gpx xmlns="...">` -- and the document's own XML declaration, or a byte order
mark, decides how its bytes are decoded. UTF-8, UTF-8 with a byte order mark and
declared UTF-16 are tested; no other encoding is claimed.

The root is **parsed for, not searched for in a prefix**. XML allows a
declaration, processing instructions, comments and whitespace before the root in
any amount, so a document with a long licence header is an ordinary document, and
a fixed-size look at the leading bytes refused it as an unknown format. Parsing
stops at the first start tag, and the input is already bounded by the import byte
limit, so detection needs no size assumption of its own.

Content whose root cannot be read falls back to a deliberately lenient look at
the bytes, which is what keeps `unsupported_format` and `invalid_gpx` apart: a
file that claims to be GPX and then breaks is still claimed, so the parser can
say what is wrong with it.

Normalized core fields: `<trk>`, `<trk><name>`, `<trkseg>`, `<trkpt lat lon>`,
`<ele>`, `<time>`, links (`<link href>` in 1.1, `<url>` in 1.0), the `creator`
attribute and the format version. `<rte>` becomes a track candidate of its own;
waypoints are not imported.

Extensions are read only where there is a purpose today, and always on the
**namespaced** name:

| Read | From | Becomes |
| --- | --- | --- |
| `<trk><type>` | the document's own GPX namespace | the `Activity` value, if the word is one we know |
| `activity` | the recording application's schema | the same |
| `<hdop>`, `<vdop>`, `<pdop>`, `<sat>`, `<fix>` | the document's own GPX namespace, directly on a position | `gps_accuracy_present` evidence |
| `course` | GPX 1.0's own element, and the track-point extension that defines one | `course_measurements_present` evidence |
| `rtePointAction` | the recording application's schema | `route_instructions_present` evidence |

A local name on its own is a word, not a schema: `course` in a track-point
extension is a measured heading, `course` in a golf application's namespace is a
golf course. Matching local names in any namespace let a foreign document
manufacture the measurement evidence that decides `RECORDED`, so
`trackvault.infrastructure.gpx.extensions` maps `(namespace, element)` onto meaning
instead.

That table is the one place in the codebase that names a source application, and
`tests/contract/test_architecture_contract.py` keeps it there. It is small on
purpose: it lists the schemas this project has evidence for -- every entry is
exercised by a fixture, and the two the developer's own recordings use are
exercised by the opt-in local regression -- and it is not a catalogue of
everything a vendor ever published. A schema no sample here uses is unknown data,
which parses fine and means nothing.

Evidence belongs to the candidate it was observed on. Instructions inside a
`<trk>` or `<rte>` are that candidate's. Instructions on waypoints sit *beside*
the candidates, so they are attributed only when the document holds exactly one
candidate and there is nothing else they could describe; with several, the
document never says which one they belong to and they decide nothing. Document
evidence is not automatically candidate evidence. Receiver quality and heading do not
become permanent point fields: they are evidence today, and the raw import keeps
the originals for the day they mean more.

Unknown extensions never fail a parse. What survives of them is a sorted list of
the namespaces the document used -- a neutral summary that records *that* extra
data was present without copying an XML fragment into the domain model.

### Safety

Imported XML is untrusted input, and the parser fails closed:

- no document type definition is processed,
- no entity is declared or expanded,
- no external reference is resolved, so parsing performs no network or file
  access,
- input size, track count, segments per track and total point count are bounded
  before anything is stored,
- a refusal names a stable error code and never quotes coordinates, markup or
  paths.

`defusedxml` provides the hardened parser configuration -- one small, established
dependency for exactly this problem, rather than hand-installed handlers on a
private attribute of the standard library parser.

## Import port

Each format is one adapter behind one port:

```
GpxImporter ─┐
FitImporter ─┼──►  ImportedTrack candidates + evidence
TcxImporter ─┘
```

*Implemented as `trackvault.application.TrackImporter`, a `typing.Protocol`.* An
adapter states its format id, its version and a media type, recognises its own
format from the content, and turns content plus limits into track candidates.
That is the whole contract: no `AbstractImporterFactory`, no
`BaseImporterManager`, no `ImporterCoordinatorFactory`, and not even a registry --
the composition root passes a tuple of adapters and the use case asks each one.

A future FIT importer terminates at exactly the same boundary: it produces
`ImportedTrack` values, and classification, persistence and the API do not change.

## Persistence

SQLite, through the standard library `sqlite3` module. No ORM and no migration
framework: neither has a contract that needs it yet.

The schema version lives in `PRAGMA user_version`, and every migration is an
explicit numbered function. An empty database runs all of them; an existing one
runs the missing tail; a database written by a newer build is refused rather than
downgraded silently. There is no `CREATE TABLE IF NOT EXISTS` collection
pretending to be a migration layer.

Tables:

```
raw_imports                     identified by content hash, written once
processing_runs                 append-only history per raw import, each stating
                                the whole processing profile it applied
tracks                          identified by (raw import, source key)
track_classifications           the detected result
track_classification_overrides  what the user said about the kind
track_user_metadata             what the user said about the track: title, note
track_evidence
track_external_links
track_extension_namespaces
track_segments
track_points
analysis_runs                   append-only history per track, each stating the
                                analysis profile it applied and the processing
                                run whose geometry it read
track_metrics                   one typed row per derived value, with its unit
                                and its provenance
analysis_quality_flags          what was wrong with the data a run worked from
map_packages                    one installed regional map, keyed by region
map_package_attribution_links   its structured credit links
map_install_jobs                what an installation did, kept after it finished
```

The map tables have no foreign key to anything else. Removing every track leaves
the maps; removing every map leaves the tracks. A package row also stores no
state column: whether a region is installed is the row **and** a managed file
that hashes to what the row says, and a state that can be written independently
of the thing it describes is a second authority.

Metrics are typed rows rather than a JSON blob: summing a month would otherwise
become a scan and a parse, and a value whose unit nothing declares is a value
whose meaning drifts. Only metrics that were derived are written -- a row per
name would mean storing a filler for every unavailable one, and a stored zero
cannot be told apart from a derived one.

`tracks.current_analysis_run_id` names the current derived generation exactly as
`raw_imports.active_processing_run_id` names the current normalized one.

Rules the schema encodes:

- one import is one explicit transaction, so a failure leaves neither a raw import
  without its run nor a track without its geometry,
- foreign keys are enforced and a busy timeout is set on every connection,
- write-ahead logging lets the HTTP server and a command-line import share the
  file; all journal files stay inside the data directory,
- reprocessing updates the track row of the same `source_key` in place, so the
  identity a user corrected survives and neither the override table nor the
  user-metadata table is ever touched,
- **no effective kind is stored.** It is projected from the detected result and
  the override on read,
- **history is never rewritten.** A migration that adds a column to
  `processing_runs` leaves existing rows stating what they stated, so a run
  written by an earlier build keeps looking like one -- which is exactly what
  `--outdated` reads.

### Candidate identity and the current generation

Two authorities that must not be confused with each other.

A **`source_key`** says which candidate of its source document a track is. The
importer produces it -- `trk:0`, `rte:0` for GPX, numbered per container type so
that reading routes for the first time does not rename an existing track -- and
everything further in treats it as opaque: nothing parses it, orders by it, or
reads a format out of it. It is the identity a user correction is attached to.

`source_index` survives as presentation order and is explicitly **not** an
identity. It was one, and a reprocess reporting one extra candidate in front
moved every later candidate's identity -- and its correction -- onto a different
track.

The **current generation** is named by `raw_imports.active_processing_run_id`. A
track row belongs to the current view exactly when its processing run is that
run. Consequences, and they are the point:

- a successful run becomes current atomically and in full, so exactly the
  candidates it produced are current afterwards,
- a run that produces no candidate is a successful, *empty* generation -- which is
  a different thing from a failed run,
- a failed run is appended to the history and changes nothing about the current
  generation, so a broken importer version cannot destroy good data,
- a candidate a later run stopped producing keeps its row and its user
  correction, and gets the correction back if it reappears. Nothing is deleted
  because a parser temporarily saw less.

Ordinary track queries answer from the current generation only. What happened
across runs is a separate question with a separate answer, and mixing the two
into one endpoint would make "this track exists" mean two different things.

### Managed raw storage

Accepted bytes are kept byte-identically under the data directory:

```
<data dir>/trackvault.sqlite3
<data dir>/raw/sha256/ab/abcdef....raw
```

The canonical path is derived from the content hash and from nothing else. The
hash is validated before it is used as a path component, and a write goes to a
temporary file that is renamed into place, so a failed import leaves neither a
partial artifact nor a stray temporary file. An exact duplicate is a no-op.

Two invariants make this an authority rather than a naming convention.
**Content-addressed means content-verified**: bytes are hashed before they are
written and after they are read, and a mismatch fails with `raw_storage_failed`
rather than being accepted. A corrupt artifact is never overwritten or silently
repaired -- it is evidence of a problem, and replacing it would destroy that
evidence. **Nothing below the root escapes the root**: every path component under
the storage root is opened without following symbolic links, and the write, the
rename and the read work on the resulting directory descriptor rather than on a
path, so a path swapped between the check and the write cannot take effect. The
configured root itself stays trusted and may be a symbolic link -- an operator
who points the data directory somewhere means it.

This is a local integrity boundary, not a sandbox. A process running as the same
user can still replace the whole data directory; what it cannot do is make the
archive write outside its own root, or hand out bytes that do not match the hash
they are filed under.

All persistent state lives under `TRACKVAULT_DATA_DIR`. Nothing is written to the
working directory, `/tmp`, a home directory or the container layer.

### Privacy at rest

A GPS archive is a movement profile of a real person, so TrackVault creates its own
artifacts readable by their owner and by nobody else:

| Artifact | Mode |
| --- | --- |
| data directory | `0700` |
| managed raw directories | `0700` |
| managed raw artifacts | `0600` |
| temporary raw artifacts | `0600` |
| SQLite database | `0600` |
| `-wal` and `-shm` | `0600` |

*Implemented as `trackvault.infrastructure.private_data`.*

The mode is stated to the call that creates the object, never applied afterwards:
a file that was briefly world-readable was world-readable. That is why the
database file is claimed before `sqlite3` opens it -- the driver would create it
with its own default -- and the same act decides the journal files, because
SQLite gives `-wal` and `-shm` the permissions of the database they belong to. A
umask only removes bits, so a permissive one cannot widen any of this.

Nothing existing is chmodded. A path inside the data directory may carry an
operator's own access decision -- a shared group, an ACL, a backup agent -- and
overwriting that silently would replace one surprise with a worse one. The
container is unaffected: a named volume arrives owned by the non-root runtime
user, and what the archive creates inside it is private.

## User-owned metadata

> A correction belongs to the user, and it never edits the evidence.

Exports arrive titled `Track`, `2024-06-11 09:14`, or nothing at all, and a
personal archive stops being pleasant to keep the moment three rows read the
same. The correction is therefore the user's own data with its own authority,
stored *beside* the source rather than over it:

```
source title       what the document said         never modified
title override     what the user said             the display authority
effective title    the override, else the source  a projection of the two
```

That is deliberately the shape the classification override already has, and for
the same reason: a reprocess replaces every normalized column of a track row, so
a title stored there would be a title an importer upgrade silently discards.
`track_user_metadata` is keyed on the track identity, which survives
reprocessing, so a correction outlives a parser upgrade and comes back with a
candidate a later run temporarily stopped producing.

`display_title` is a projection, exactly as `effective_kind` is: no third value
is stored, and `None` stays a real answer -- some documents genuinely name
nothing, and inventing a name in the domain would put a guess where a
presentation layer can say something honest and admit that it did.

Titles and notes are **plain text**, trimmed, length-bounded, and refused rather
than truncated when they are too long. Nothing here is markup and nothing
interprets it as such. `PATCH` is a real partial update -- an absent field keeps
its value, an explicit `null` or a blank string clears it -- because a rename
that silently deleted a note is how a partial update loses data.

## Track analysis

> Derived metrics are rebuildable and never source authority.

```
raw import  ->  normalized track  ->  analysis
 authority        authority          rebuildable
```

Analysis turns the current normalized generation into numbers. Its entry point
takes segments and nothing else:

```python
analyze_track(segments: Sequence[TrackSegment]) -> TrackAnalysis
```

That signature *is* the source-agnostic guarantee. No parser, source application
or exchange format is reachable from a tuple of segments, so a future FIT
adapter reaches the same numbers without this code knowing it exists. The
architecture contract checks that the package imports no format module and no
outer layer, and that no route imports a calculation.

Nothing in the analysis path writes back into what it read. Outlier detection
produces an analysis *decision*, never a deletion: the geometry stays intact so
a better algorithm can revisit exactly the same data.

### Analysis versioning

`AnalysisProfile` names the distance, movement and elevation algorithms with
their versions, plus a metric schema version, and every stored result records
it. An elevation filter or a movement rule will change, and without that record
nobody could tell which of two numbers came from which rule.

*Implemented as `trackvault.domain.analysis.AnalysisProfile` and
`trackvault.application.InstalledAnalysis`.*

### What is calculated

| Metric | Definition |
| --- | --- |
| `distance_m` | horizontal geodesic distance between consecutive positions, summed **within** segments |
| `elevation_min_m`, `elevation_max_m` | extremes of the raw observations |
| `elevation_gain_m`, `elevation_loss_m` | ascent and descent of the *filtered* profile |
| `elapsed_duration_s` | first temporal observation to last |
| `moving_duration_s` | observed time going somewhere |
| `stopped_duration_s` | observed time not going anywhere |
| `unobserved_gap_duration_s` | time nothing was recorded for |
| `unattributed_duration_s` | elapsed time none of the other three could claim |
| `average_speed_mps` | distance over elapsed duration |
| `moving_average_speed_mps` | distance covered while moving, over moving duration |
| `maximum_speed_mps` | highest speed sustained across the analysis window |

Values are SI and their unit is in the name. Friendlier units are a presentation
decision made later and elsewhere.

Three rules decide the hard cases, and each exists because the obvious
calculation is wrong:

- **A segment boundary is never bridged**, and elevation never enters distance.
  Distance consults no timestamp, so a planned route has a length like anything
  else.
- **Silence is not a rest, and no time goes missing.**
  `moving + stopped + unobserved + unattributed == elapsed`, an equality. A stop
  is claimed only where positions kept arriving and showed no movement; a paused
  recording, a flat battery and a rest are indistinguishable, so that time is
  `unobserved`; and time no rule can classify is *named* rather than dropped.
  The gap threshold scales to the track's own sampling, because a fixed one
  would call a five-minute-sampled track one long gap.
- **Noise is not terrain, a bad fix is not a sprint, a loop is not a pause, and
  slow is not stopped.** Movement is decided from the widest positional *spread*
  the window covers -- not from where the window ended up, which is zero for
  every path that returns to itself and would report a walked circuit as
  standing still. The spread is measured at **two scales**, because one scale
  cannot separate slow progress from receiver noise: over thirty seconds they
  look alike, and over three minutes a still receiver's cloud is no wider while
  a walker's has grown sixfold. The wander a still receiver produces anyway is
  subtracted before a rate is taken, so what is left is a statement about
  progress. A stretch neither scale can separate from noise is reported as
  `unattributed` rather than decided. One threshold pair for every activity; an
  interval too fast for the track's own median is excluded and ends the window
  stretch, so it cannot reach its neighbours either. Ascent comes from a rolling
  median plus a five-metre
  deadband measured to the turning point the profile reached, so reversing a
  track swaps its ascent and descent exactly; summing raw positive differences
  instead would grow with the sampling rate rather than with the terrain.
- **A timestamp is not a measurement.** Temporal evidence -- `observed`,
  `estimated`, `unknown` -- is derived from evidence that something was
  *measuring*, never from the presence of an instant or the exporting
  application. Every clock-dependent metric is still derived and is never
  presented without it; only a recorded track with observed instants may have
  its durations called actual activity timing.

### Series, and why they are not a second analysis

The metrics above are aggregates: one distance, one ascent, one maximum speed. A
map and a chart need the other shape of the same analysis -- a value at every
position -- and the failure that invites is the quiet one:

```
elevation_gain_m   accumulated from the filtered profile
elevation series   the same filtered profile        <- not the raw altitude
maximum speed      the highest sustained window speed
speed series       the same sustained window speeds <- not point-to-point speed
```

`trackvault.domain.analysis.series.derive_profile` therefore computes nothing of
its own: it asks the elevation filter and the movement window for their own
series and lays them out against the positions. It takes segments, like every
other entry point in that package, which is what makes it source-agnostic by
construction.

A sample is addressed by `(segment_index, point_index)` -- the position's own
address in the geometry -- so a map marker and a chart cursor mean the same
sample without the browser matching a coordinate. Nearest-coordinate matching is
a second authority on identity and it picks the wrong position exactly where a
track crosses itself.

Cumulative distance is summed **within** segments and carried across them: the
ground between two segments was not travelled and never enters the total, while
the axis stays one axis with no jump at a boundary.

The series are always derived by the algorithms this build installs, from the
geometry a reader currently sees. The response reports the track's *stored*
analysis availability beside them, so a reader can tell whether the chart and
the figures next to it were produced by the same rules.

### Bounded projections

`trackvault.application.projection` makes a large track small enough to send, and
it is **presentation, never normalization** -- nothing it does is written back,
so the reduction can be redone differently tomorrow and asking for fewer points
is not a way to lose data.

A map and a chart need different points, so there are two reductions over one
set of samples:

| Projection | Keeps | Because |
| --- | --- | --- |
| map simplification | the positions carrying the shape (Ramer--Douglas--Peucker, run as a heap) | a corner matters, a straight kilometre does not |
| profile decimation | ends, segment boundaries, elevation and speed turning points | a summit sits on nearly straight ground and is the first thing a shape simplifier drops |

Both run per segment and keep each segment's ends, so no reduction ever joins
two segments. `/profile` is bounded by default; `/geometry` is the canonical
normalized geometry unless a caller asks for `max_points`, and says which of the
two it returned.

Nothing is persisted. A third derived-state lifecycle -- written, versioned,
invalidated, repaired -- is three more ways to hold something stale, and no
benchmark has yet shown it is needed.

**Missing is never zero.** An underivable metric is absent from the stored set
and `null` in the API. A planned route has no moving time, and `0` would claim
it was travelled and nobody moved. A small set of quality flags says what was
wrong when something is absent.

**Stored analysis is untrusted input.** A run row that cannot be interpreted, a
metric value that could not have been derived and a quality flag this build
cannot name all fail closed: the analysis reads as absent, is never current, and
reaches no caller as a traceback. A metric *name* this build does not know is
skipped instead -- a value nobody needs says nothing about the rows beside it.
The repair is always available, because the geometry the metrics came from was
never touched.

### Analysis currency

Stored metrics are current only when a successful run applied the installed
profile **and** read the generation a reader currently sees:

```
never analysed    -> outdated
older algorithms  -> outdated
newer geometry    -> outdated
```

A classification override outdates nothing: it changes which totals a track
reaches, not how long it is. A *failed* reprocess outdates nothing either -- the
generation a reader sees did not change, so neither did its metrics.

Publication is one transaction and is conditional on the generation. A reprocess
that commits while an analysis is running has already moved the track on, so the
update matches nothing and metrics describing geometry nobody can see never
become current. The run is still recorded. That is the concurrency contract, and
it needs no lock. Old runs are kept: history is not rewritten here either.

A successful import or reprocess derives metrics for what it produced, best
effort. Losing them costs one `analyze --outdated`; letting an analysis defect
abort an import would lose source evidence, which is the one thing the archive
cannot reconstruct. Analysis is therefore its own lifecycle with its own status,
and "track available, analysis absent" is a normal simultaneous state.

## Statistics

Actual, planned and unknown are three separate sets, selected by the
**effective** track kind:

```
Recorded  10 km
Planned  100 km
Unknown   50 km

actual total = 10 km
```

There is deliberately no combined scope: a number that adds routes somebody
planned to distances somebody travelled is about neither, and offering it as the
default would make it the one people quote. Because the effective kind is read
at query time, a user correction moves a track between the sets immediately and
nothing is derived again.

### Periods and the aggregation timezone

A month is a *local* month -- 23:30 UTC on 31 January is already February in
Berlin -- so `TRACKVAULT_TIMEZONE` decides the boundaries and every response names
the zone it used. The window is computed in that zone, the archive is queried in
UTC, and the bucketing happens in Python: an offset is not a constant, and doing
that arithmetic in SQL with a fixed one is how a daylight-saving transition
moves a track into the wrong month.

A track's period comes from its own positions:

```
import received_at    when the archive learned about it     -> not an activity date
metadata export time  when the file was written             -> not an activity date
first trackpoint time                                       -> the timeline
```

And a timeline is not yet a date. `timeline time` is what the positions carry;
`activity calendar time` is the claim that this happened then, and only instants
that were shown to have been *measured* support it -- a planner's synthetic
clock and a recording stripped of its receiver metadata are structurally
identical. `supports_actual_calendar_placement` reads the temporal evidence and
nothing else, so a user correcting a track's kind moves it between the scopes
without dating it.

A track with no calendar placement has a length and belongs to no month. It is
reported *beside* the year rather than assigned to 1970 or to the import date,
split into `unplaced.without_date` and `unplaced.with_unverified_date`: no
instants at all and instants nothing vouches for are different facts, and only
the second one looks like a date until somebody checks. The listing's `year` and
`month` filters apply the same rule, so clicking a bar reaches exactly the
tracks that bar counted.

An empty period totals zero -- there was nothing to total, and `track_count`
says so. A period that holds tracks but no value for a metric reports `null` for
it.

### Only current analyses are totalled

After a profile bump the archive holds numbers from two algorithm generations,
and adding them produces a figure that measures neither. Only tracks whose
analysis is current contribute, decided by the same `InstalledAnalysis` that
`analyze --outdated` selects with -- one authority, so a total and a batch run
cannot disagree about what is stale. There is no `allow_mixed` option.

Refusing to answer would be worse, so the total is of the current ones and says
how much of the period it covers: `analysed_track_count` beside `track_count`,
with `tracks_without_analysis`, `tracks_with_outdated_analysis` and
`tracks_with_invalid_analysis` separating the three shortfalls. Those four
partition the period exactly -- they are the four availability states, and every
track is in one. `tracks_with_failed_analysis` is orthogonal and overlaps them
all rather than replacing any: a track can hold current metrics from an earlier
run and a failed newest attempt at once.

Durations are summed only over tracks whose instants were shown to be measured,
counted by `tracks_without_observed_timing`.

### Cost

No aggregate reads a position. A year is one indexed range over
`tracks.started_at` plus one batched metric lookup, so the cost depends on how
many tracks the archive holds and not on how long they are.

A track listing is a **page**: bounded by a server maximum no query string can
raise, filtered by effective kind, activity, year, month and analysis
availability, and ordered by import date, activity date or current distance --
all in SQL, so a page costs the page rather than the archive. Every ordering
ends in the track identity, because a row that moves between two pages is a row
the reader sees twice or not at all, and undated tracks sort last in both
chronological directions rather than wherever `NULL` falls.

The availability filter is applied in the same statement that counts and pages.
Deciding it afterwards in Python would page first and hide second, which is a
page of the wrong size under a total of the wrong number. The rule itself still
has one owner: the application hands the repository the *values* of the
installed profile, and the repository compares them the way it already projects
`effective_kind` from the classification -- a projection, never a second
authority.

`tests/integration/test_statistics_performance.py` asserts that neither a yearly
total nor a track listing touches `track_points`, that neither issues a query
per track, and -- at the query-plan level -- that the year window is served by
its index rather than by a scan.

## Offline map packages

> A map package is a replaceable external dataset, not source evidence.

That sentence decides the whole model, and it is the one place this capability
deliberately does *not* copy the raw import:

```
raw GPX import    private user source authority   irreplaceable, immutable,
                                                  corruption fails closed and
                                                  is preserved as evidence

map package       external reference dataset      replaceable, re-downloadable,
                                                  corruption is discarded and
                                                  the package reinstalled
```

What *is* copied is the integrity rule, because that one is about honesty
rather than about evidence. An installed package is two facts -- a database row
**and** a managed file that hashes to what the row says -- and either alone is
`INVALID`, never `INSTALLED`.

### Provider, catalog and the network

A provider is a port. Geofabrik is one adapter, and it is the only module in the
codebase that knows a map provider exists -- exactly as `gpx/extensions.py` is
the only one that knows a recording application's schema.

The network is reached in three situations, all of them something a person
pressed: refreshing the catalog, installing, updating. The catalog itself is a
cache; reading a track never touches it, and provider downtime shows a sentence
in the map manager while every installed map carries on.

An API caller supplies a `region_id` and never a URL. The adapter resolves the
address, redirects are followed only within the hosts that adapter declares, and
the transfer is HTTPS-only, streamed, hashed as it goes and bounded by a
configured ceiling. A caller who could name the host would have a server-side
request forgery primitive, and there is no feature here worth that.

### Storage and identity

```
<data dir>/maps/packages/<sha256-of-region-id>/<content-sha256>.mbtiles
<data dir>/maps/downloads/<job-id>.part
<data dir>/maps/catalog/<provider>.json
```

Neither name comes from a caller. The directory is a digest of the validated
region identity and the file is the digest of the bytes themselves, so there is
no code path from a provider's string to a path component. `MapRegionId` is
validated to segments of `[a-z0-9-]`, a class with no `.` in it, which makes
`..` unwriteable rather than merely refused -- and the digest is the second,
independent reason, the one that still holds if the first is ever wrong.

### Installation is atomic

```
resolve → preflight (size, free space for old + new) → stream to .part, hashing
        → publish the file → validate what was published → commit the row
        → delete the package that was current before
```

> A failed update leaves the previous map usable.

The new package is fetched and checked *beside* the installed one and never over
it, which is why the free-space preflight asks for room for both and why the old
file is deleted only after the row has committed. A byte-identical re-download
answers `ALREADY_CURRENT` and switches nothing: switching would invalidate every
cached tile URL to change nothing.

Three kinds of debris survive a crash, and each gets a different answer at
start-up. A job stuck in `downloading` becomes `interrupted`; a `.part` file is
deleted; a managed file no row points at is deleted rather than adopted --
promoting it would be inferring an installation nobody completed from a file
whose provenance the archive has no record of.

### Delivery

`/api/v1/maps/tiles/{content-sha256}/{z}/{x}/{y}.mvt`, from this process, out of
a read-only SQLite connection per worker thread. The identity in the URL is
matched against the database and is never a path; because it names the content,
a tile is `Cache-Control: immutable` for a year and an update is a new address
rather than a cache somebody has to clear. Stored tiles are gzipped vector tiles
and are served **as stored**, with `Content-Encoding: gzip`. A missing tile is
`204`: a hole in coverage happens at every edge of every region and is not an
error.

Nothing here touches the archive's own database file, so a screenful of tile
reads cannot take a lock a track listing is waiting for.

### Which map goes behind a track

`SelectMapCoverage` takes a rectangle and the installed packages. Two facts
decide it, and they are different kinds of fact: whether a package *covers* the
rectangle is geometry, and whether one package *supersedes* another is
**hierarchy**, taken from the region identity the provider published.

That distinction is the point. Germany's bounding box reaches well into the
Netherlands, so a rectangle near Aachen is "covered" by both and geometry alone
cannot tell that only one of them holds data there. The region tree can.

| Situation | Answer |
| --- | --- |
| a package covers the rectangle | the covering ones, minus any whose descendant also covers |
| several cover it and one is inside another | the descendant alone |
| a parent covers it and its child does not | the parent alone -- the child would leave a gap |
| nothing covers it | every intersecting package, minus superseded ones |
| nothing intersects | nothing; the track draws on a neutral background |

### Attribution

`MapAttribution` is read out of the package at install time and stored beside
it: data owner, provider, licence identifier, licence name, the line that must
stay visible, and structured `(label, https url)` credit links. A package that
states no author and no licence does not install, because a hard-coded
"© OpenStreetMap contributors" would be a claim this build makes about a file it
did not read. Links are structured rather than markup: remote metadata reaching
`dangerouslySetInnerHTML` is the shortest path from a provider to a cross-site
scripting hole.

## Vendor and format extensions

GPX supports extensions, and other formats carry vendor-specific fields.

- Known extensions may later be normalized into business fields.
- Unknown extensions must not needlessly fail the parse.
- Relevant unknown source metadata should be preservable for later reprocessing.
- Domain models still must not adopt arbitrary XML fragments as business fields.

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

### Input paths

*Implemented as `trackvault.application.ImportTracks`.*

| Path | State |
| --- | --- |
| `trackvault import <file>...` | implemented |
| `trackvault scan` over `TRACKVAULT_IMPORT_DIR` | implemented |
| Automatic import: the server scans `TRACKVAULT_IMPORT_DIR` on an interval | implemented -- see "Automatic import" |
| `trackvault reprocess <sha256>` | implemented |
| `trackvault reprocess --failed` | implemented |
| `trackvault reprocess --outdated` | implemented |
| `trackvault processing-status <sha256>` | implemented |
| `trackvault analyze <track_id>` | implemented |
| `trackvault analyze --outdated` | implemented |
| `trackvault analyze --all` | implemented |
| `POST /api/v1/tracks/imports` | implemented -- see below |
| `trackvault export raw <sha256>` | implemented |
| `trackvault export track <track_id>` | implemented |
| `trackvault backup create` / `backup list` | implemented |
| `trackvault restore <archive>` | implemented |
| `trackvault doctor` | implemented |
| Future API import | not implemented |
| An HTTP export endpoint | not implemented -- see "Output paths" |

The pipeline, once, for all of them:

```
bytes → size limit → content hash → exact duplicate check → managed raw storage
      → format detection → adapter → classification → one transaction → outcome
```

An import failure is returned, not raised, so one unreadable file does not stop a
sync run. The command line keeps the same promise: every path it is given is
attempted, an unreadable one is named in the summary, and the run still ends with
a non-zero exit code. Both input paths read through one bounded reader, so the
byte limit bounds the *read* rather than only the verdict -- refusing an oversized
file after loading all of it has already paid the cost the limit exists to
prevent. The raw import is kept even when processing fails, with a failed run
naming the reason, so a file can be reprocessed once an importer learns to read
it. The one exception is an oversized file: it is refused before anything is
stored.

**The upload endpoint, and what bounds it.** There was none for a long time,
because an unauthenticated endpoint that accepts files and writes them to disk
is a bigger thing than an unauthenticated one that reads. It exists now because
the archive's owner asked for it, and the reasoning that kept it out is what
shapes it now rather than what is missing from it:

* it is a caller of `ImportTracks`, not a second path -- the duplicate rule, the
  storage layout and the classification are decided where they always were,
* the read is bounded *while* reading, so an oversized body is refused before it
  has been paid for, and a declared length over the ceiling before a chunk,
* one file per request, so twenty files produce twenty verdicts,
* the filename is display metadata and never a location,
* `TRACKVAULT_UPLOAD_ENABLED` defaults to `true`, because an archive somebody
  cannot add a track to from the browser reads as broken; a deployment whose
  network does not make an unauthenticated write acceptable sets it to `false`,
  which refuses the whole capability and keeps every read.

It is not authentication and does not reduce the need for it. See
`docs/adr/0011-web-upload.md`.

### Known is not current

Recognising a content hash makes an import a duplicate, and that is all it makes
it. Whether the *normalized representation* of those bytes is still current is a
different question:

```
raw source already known        the duplicate check answers this
normalized data is current      the processing run answers this
```

A scan must stay cheap and must not re-parse a file it already failed on, so the
duplicate check keeps stopping there -- after establishing that the archive can
still produce the bytes it claims, which is the "Import integrity" table above.
Reprocessing is the explicit operator action that asks the second question, and
it does no duplicate check at all. It reads the managed raw copy -- which
verifies the bytes against their content hash first, so a corrupt artifact can
never become the input of a regeneration.

Nothing reprocesses itself. There is no mass reprocessing on start-up: it would
turn every deployment into a full re-parse of the archive.

Two batch selections exist, and they answer different questions:

| Selection | Covers |
| --- | --- |
| `--outdated` | the current generation was not produced by the installed profile |
| `--failed` | the newest attempt failed, whatever readers currently see |

They overlap without being equal. A source that never processed successfully is
in both. A source whose newest attempt failed beside a matching, healthy
generation is only in `--failed` -- regenerating it would change nothing.
A source that works but was normalized by an older importer is only in
`--outdated`. Both read one snapshot query and the one currency authority, so a
batch run and the status view cannot disagree about a source. Each source is
attempted separately, one failure does not stop the rest, and the exit code
reports a partial failure.

`trackvault processing-status <sha256>` is the read side of the same facts: the
current run against the latest attempt, each processing component as "what
produced this -> what is installed", the track count and the outdated verdict. It
names hashes, run identities, versions and error codes, and never a path, a
filename or a coordinate. There is no HTTP endpoint for it: `/api/v1/imports/...`
would be a new resource family whose other verbs do not exist, and the operator
who can reprocess is on the machine that holds the data anyway.

The import directory is **input, not authority**: nothing in it is written,
renamed, moved or deleted. Filenames are display metadata only, and a re-offered
file is recognised as an exact duplicate and skipped. There is no file system
watcher: a scan is a pass over the folder, run by an operator or by the server
on an interval, and it does the same thing on its first run after a restart as
on its hundredth.

#### Automatic import

While the HTTP server runs, `trackvault.infrastructure.automatic_import` scans
the import directory once at start-up and then `TRACKVAULT_IMPORT_SCAN_INTERVAL_MINUTES`
after the previous scan ended. It is a timer around the same
`ImportDirectoryScanner` pass `trackvault scan` runs, and every file it finds
goes to `ImportTracks`:

```
lifespan start ─► AutomaticImport ─► ImportDirectoryScanner ─► ImportTracks
                  (one thread,        (discovery, settle,       (the one
                   interval)           state memory)             pipeline)
```

There is exactly one worker per application: it is built once in the service
graph, started by the lifespan once storage is migrated, and `start` refuses a
second thread. The command line never starts it. `TRACKVAULT_IMPORT_SCAN_ENABLED=false`
leaves the folder to `trackvault scan` alone. Stopping is noticed between files,
so shutdown waits for one import and never for a folder; the wait is bounded and
the thread is a daemon, because a network mount that stopped answering can hold
a read indefinitely.

An adapter error nobody anticipated is caught around the one file whose import
raised it, logged with its traceback, reported as a failure and offered again on
the next pass: files are taken in name order, and a document that stopped the
whole pass would keep every file after it out for good. A folder that cannot be
opened at all is reported as unavailable rather than as empty, and the scanner's
memory is kept for when it comes back. An error that is not
about one file -- the folder cannot be listed -- ends the scan, is logged, and
the next one is due an interval later rather than at once; a broken scan never
ends the worker, and no scan starts while another runs.
What the worker last did is kept in memory as a report on this process; what it
imported is in the archive. `GET /api/v1/tracks/imports/automatic` projects that
report through the application's `AutomaticImportMonitor` port -- read-only, so
no route can make the worker scan. See `docs/adr/0013-automatic-import.md`.

#### The AutoSync boundary

What lands in a sync folder is untrusted, and so is what happens to it between
the moment the scan lists a name and the moment the archive opens it. A check on
a path is a statement about the past:

```
list the directory   -> "this is a regular file"
                     <- something replaces it
open the path        -> whatever is there now gets read
```

Discovery and opening are therefore one boundary. The root is opened once, every
candidate is opened relative to that descriptor with `O_NOFOLLOW` and
`O_NONBLOCK`, and what a name refers to is decided by `fstat` on the open file.
`ImportDirectoryEntry` carries a name, not a path, so nothing downstream can
resolve one again. A symbolic link, a subdirectory, a device node, a named pipe,
a deleted name and an unreadable file are all skipped candidates rather than
failed runs -- a sync folder changes under a scan as a matter of course. The
non-blocking open matters on its own: opening a FIFO for reading waits for a
writer, and a scan that can be stopped by dropping one into the folder is a
denial of service with no attacker skill required.

A candidate is a visible name whose suffix an installed adapter declares
(`TrackImporter.file_suffixes`, compared case-insensitively). Everything else in
the folder is neither read nor stored: a photo offered to `ImportTracks` would
become a raw import with a failed run in every archive sharing the folder. It
stays where it is instead, so an adapter that learns its format later still finds
it. The suffix chooses what is offered and nothing more; `detects` still decides
from the content what the bytes are.

**Only finished files are read.** A sync tool writes a file in pieces, and half a
GPX document is either unreadable or -- worse -- a readable prefix that becomes a
short, wrong track. `ImportDirectoryScanner` therefore reads a candidate only
once its change time is at least the settle time old
(`TRACKVAULT_IMPORT_SETTLE_MINUTES`, five by default, because a recorder writing
a point a minute into a synced folder is quiet for a minute at a time), only if
it is not empty, and only if the open file is in exactly the state it looked at,
before and after the read. The change time
is the kernel's: every write and rename sets it and nothing sets it back, which a
modification time a copy tool may preserve cannot promise. Anything else is
*waiting*, which is not a verdict; a later pass takes it. A scan an operator runs
by hand uses no settle time -- they have already decided the folder is ready --
and keeps the before-and-after check.

**Remembering is a cache, never the duplicate authority.** A scanner remembers
which state of which name it has offered, so a later pass costs a `stat` per
unchanged file instead of a read, a hash and an integrity check of the managed
copy. That memory lives in the process and dies with it, deliberately: the
archive's content hash is what makes a re-offer a no-op, so a restarted server
offers every file once more, gets `duplicate` for each, and cannot import
anything twice. Persisting the memory would have been a second record of "what
was imported", with its own migration, backup and restore semantics, to save one
read per file per restart.

The configured root itself stays trusted and may be a symbolic link, exactly as
the managed raw storage root does. `read_bounded` remains for paths an operator
names on the command line: those were chosen deliberately.

## Output paths

Data leaves in three shapes, and they are three things rather than one feature
with options:

```
raw source          the bytes that arrived         byte-identical, always
exchange document   generated GPX 1.1              what this build believes today
archive             database + every raw source    the whole deployment
```

Confusing the first two is the failure the split exists to prevent. A raw export
is evidence and must survive an importer upgrade unchanged; a document export is
produced from the current normalized generation by the rules this build
installs, so it *should* change when they do. A contract test asserts the two
never produce identical bytes, because if they ever did, whichever somebody had
chosen for their backups would be the one that was lying.

An exchange document carries the displayed title, the activity, the segments,
the positions, and elevation, instants and sensor readings where the archive
holds them. It deliberately carries **no classification**: `RECORDED` and
`PLANNED` are verdicts reached from this project's own evidence rules, at a
confidence, with a classifier version, and no exchange format has a field that
means that. A private extension would publish a claim no reader could evaluate.
Nothing absent is invented either -- no elevation where none was measured, no
clock on a planned route.

Sensor readings go back out through the same namespaced vocabulary the importer
reads them from, so an export loses no measurement and invents no schema.

### The archive format

*Implemented as `trackvault.application.archive` and
`trackvault.infrastructure.archive`. See `docs/adr/0012-export-archive-and-restore.md`.*

```
manifest.json                    first member, so a dry-run reads one small file
database/trackvault.sqlite3      captured through SQLite's own online backup
raw/sha256/ab/abcdef….raw        every original, byte-identical
```

An ordinary `tar.gz` on purpose. The situation somebody needs a backup in is the
one where this application may not run, and a backup format only its own
application can open is a backup with a dependency.

**Copying the data directory is not taking a backup.** Write-ahead logging means
the `.sqlite3` file alone is an incomplete database, and a copy taken while
anything is writing can produce one that will not open. The database is captured
through SQLite's own online backup, every artifact is checked against the hash it
is filed under, and the manifest records what was taken so a restore can tell
"all of it" from "most of it".

**The manifest states its omissions.** Installed map packages are excluded --
public data, re-downloadable, and by far the largest thing a deployment holds --
and `ArchiveOmission` says so in every manifest. An archive that quietly held
part of a deployment while calling itself a backup is the failure this whole
format is arranged against.

Two versions, moving for two reasons: `format_version` changes when the
*container* does, `schema_version` describes the database inside it.

**Writing one is durable, not merely atomic.** The archive is built under a
`.part` name and renamed into place, and the sequence does not end at the
rename:

```
write → flush → fsync the archive → atomic rename → fsync the parent directory
```

The last step is what makes the rename survive a power cut, and it uses the same
directory-flush helper the restore publication does -- both end a multi-step
write with a rename, and a second implementation would be the one that quietly
stopped early. A flush that fails fails the backup, and the finished file is
removed again: a backup the command reported as unwritten is the one somebody
finds later and trusts.

**The manifest is checked against itself, never against the database inside
it.** A member name may be stated only once, no source may claim the database
member's name, and the manifest is not one of its own members -- all three are
contradictions within one document, caught where the document is decoded. What
is deliberately *not* checked is `counts` against the member list: what an
archive holds in an operator's terms is the captured database's to say, and
reconciling the two would make the manifest a second authority for numbers it
only describes.

### Restore

```
manifest → format version → schema compatibility → checksums → database integrity → publish
```

Nothing in the destination is touched until every step before `publish` has
passed, which is what makes attempting a restore safe: a damaged archive costs
nothing to try.

| Situation | Answer |
| --- | --- |
| schema equals this build's | `SUPPORTED` |
| schema older than this build's | `MIGRATION_REQUIRED` -- restores, migrates at the next start |
| schema newer, or another format, or a newer container | `UNSUPPORTED` |

Publishing replaces **what the archive carries**, not the data directory. In a
container that directory is a mount point and a mount point cannot be renamed,
so a restore that replaced it wholesale would work on a laptop and fail on every
real deployment. Installed maps therefore survive a restore, which is the same
decision that keeps them out of the archive. The journal files move with the
database they belong to: a write-ahead log left beside a restored database
describes a different database, and SQLite would apply it.

**"Already holds data" means data, not files.** Starting the server creates and
migrates an empty database; counting that as data would make `restore --replace`
the ordinary recovery path, and making `--replace` normal is how somebody
eventually types it at an archive that mattered. A database this build cannot
account for does count -- fail safe.

**Extraction refuses rather than sanitises.** A member is extracted only when the
manifest declares it *and* its name survives an allow-list check, and it is then
written to a path built from validated components -- the member's own name never
reaches the file system. Links, device nodes and anything that is not a regular
file are refused. Two independent reasons, so a mistake in one is not a
vulnerability.

There is deliberately **no HTTP export endpoint**. Raw sources are personal
movement data, the archive has no authentication, and a download URL for
somebody's recordings is not something to add on the strength of convenience.
Exporting is an operator action on the machine that holds the data, exactly as
reprocessing is.

## Diagnostics

`trackvault doctor` reports what is wrong with a deployment and **changes
nothing** -- no import, no migration, no repair, no network. That is a contract
rather than an intention: it runs before anything migrates, and it reads the
schema version through a read-only connection, because the obvious
implementation creates the database it was asked about and then reports on the
deployment it just made.

Infrastructure observes and `trackvault.application.diagnostics` judges. Three
statuses, and the middle one carries its weight: a pending migration, a missing
backup and an unmounted import folder are *degraded* rather than broken, and
collapsing them into "error" trains an operator to ignore the output. Exit codes
are `0`, `2` and `1` for healthy, degraded and broken.

Every detail line is structural. No coordinate, no track title, no personal
filename -- a diagnostic that is safe to paste into an issue is one people
actually paste.

## Logging

Structured and data-sparse. Logged: a content hash prefix, track and point counts,
the format, the processing status, the reason a file was skipped. **Not** logged:
coordinates, raw payloads, whole file contents, or personal filenames where they
add nothing. Turning up the log level must never start printing someone's movement
profile.

## Enforcement

The boundaries above are not a convention, they are an executable contract.
`tests/contract/test_architecture_contract.py` parses every module in
`src/trackvault` with `ast` and checks:

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
| Displayed track title | an explicit user title if present, otherwise the source title |
| User-owned metadata | `track_user_metadata` — never the normalized track, never the raw import |
| Which years exist | the calendar-eligible tracks the archive holds, in the aggregation zone |
| Activity | explicit source metadata or the user, never a guess |
| Analysis algorithms | the installed `AnalysisProfile` |
| Per-track derived metrics | the current successful `AnalysisRun` for the current processing generation |
| Calculated statistics | the analysis layer, computed from the canonical normalized track |
| Metric meaning | the metric's provenance: measured, derived or estimated, never silently discarded |
| Actual aggregate membership | the effective `TrackKind` |
| Actual aggregates | tracks whose effective kind is `RECORDED` |
| Planned aggregates | tracks whose effective kind is `PLANNED` |
| Month and year bucket | the canonical activity timestamp plus the configured aggregation timezone |
| Source metadata | evidence and provenance information, never business authority |
| Import | the single canonical `ImportTrack` use case |
| HTTP representation | projection only |
| Analysis availability | `InstalledAnalysis.availability` — one answer, projected by list, detail and totals |
| List headline metrics | the current analysis only; a stale or damaged number is not a headline |
| Longest-first ordering | the current distance only |
| Actual calendar placement | trusted temporal evidence — `supports_actual_calendar_placement` |
| Profile and map series | the current normalized geometry plus the installed analysis algorithms |
| Presentation projections | decimation and simplification; never normalization, never written back |
| Browser/frontend state | projection only |
| Installed map packages | a database row **and** a managed file that hashes to it — either alone is `INVALID` |
| Map attribution | the installed package's own metadata, never a build-time string |
| Which map draws behind a track | `SelectMapCoverage`, from installed coverage and the provider's region tree |
| Map provider addresses | the provider adapter; never an API caller |
| Exported original bytes | the managed raw artifact, verified against its hash |
| Exported exchange document | the current normalized generation plus the installed writer -- never the source file |
| What an archive holds | its own manifest, including what it deliberately omits |
| Whether an archive may be restored | `compatibility_of`, from the format version and the schema version |
| Deployment health | `Diagnose` over one observation; infrastructure observes, the application judges |
| Which release a build is | the Git tag it was built from, resolved into the distribution metadata |
| Configuration | `trackvault.config` backend application settings |

Consequences:

- No import adapter decides business meaning on its own; it produces evidence, the
  classification step decides.
- No UI may hold a value the backend cannot recompute.
- Statistics are never stored in two places with independent update paths.
- An automatic reclassification never silently overwrites a user correction.

## Deliberately deferred

Open on purpose, and not to be pre-empted by "preparation" code:

- analysis algorithms beyond the first versions: 3D path length, grade, splits,
  personal records, streaks and lifetime totals
- digital elevation model correction, which needs a network dependency
- map contours, hillshade and terrain: the chosen packages carry none, and a
  DEM pipeline is a separate decision with its own storage and licensing
- resuming an interrupted map download; a retry re-downloads
- automatic background map updates
- planned duration as an explicitly estimated metric
- week-based or custom statistics periods, and a per-request timezone override
- FIT, TCX, KML and GeoJSON adapters -- the boundary is proven, the adapters are
  not written, and no dummy adapter stands in for them
- semantic duplicate detection: the same loop ridden twice, a trimmed export,
  a file whose coordinates were rounded on the way out. Those need a
  heuristic that can be wrong, and a wrong one hides a real track.
  *Recording identity* -- byte-identical normalized positions and instants --
  is implemented instead and is not the same thing: it is an equality with
  nothing to tune, and it catches exactly one ride exported more than once detection
- authentication. The archive is unauthenticated on purpose and is meant for a
  trusted network; every endpoint assumes that. The one endpoint that *writes*
  can be refused outright with `TRACKVAULT_UPLOAD_ENABLED=false`; the reads are
  open to anyone who can reach the port either way.
- a file system watcher, as opposed to the scan the server runs on an interval
- Android client or companion app
- sensor schemas beyond heart rate and cadence: power, temperature, FIT
  developer fields. The two that are implemented are read from the Garmin
  track-point schema v2 and from nothing else; a document carrying the v1
  spelling is unknown data rather than a guess.
- aggregate sensor metrics: an average or maximum heart rate. Those are
  *derived* values and would be a new `AnalysisProfile` version, which
  outdates every stored analysis in an archive. Worth doing deliberately
  rather than as a side effect of reading the series.

See `docs/adr/0001-project-foundation.md`,
`docs/adr/0002-source-agnostic-track-model.md`,
`docs/adr/0006-track-analysis-and-statistics.md`,
`docs/adr/0008-read-authority-calendar-and-track-profiles.md` and
`docs/technical/contracts.md`.
