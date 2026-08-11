# Architecture

GPX-View is **a self-hosted, source-agnostic activity and route archive for
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

Current content: `TrackKind`, `Activity`, `MetricProvenance`, `EvidenceCode`,
`ClassificationResult`, `TrackClassification`, the `classify` rules, `TrackPoint`,
`TrackSegment`, `ImportedTrack`, `NormalizedTrack`, `SourceMetadata`, `RawImport`,
`InputChannel`, `ProcessingRun`, `ProcessingStatus`, `ProcessingProfile`,
`is_processing_current`, and the `analysis/` package: `AnalysisProfile`,
`MetricName`, `MetricUnit`, `MetricValue`, `AnalysisQuality`, `TrackAnalysis` and
`analyze_track` with the distance, movement and elevation algorithms.

### `gpx_view.application`

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

It also owns the ports -- `TrackImporter`, `TrackRepository`, `RawImportStore`,
`Clock` -- the import limits, the public error codes, `InstalledProcessing` and
`InstalledAnalysis`: the one statement of what processing and what analysis this
build applies, and therefore the one place each that answers whether a stored
generation, or a stored set of metrics, is still current.

It orchestrates domain rules and ports, and knows no concrete parser. It must not
import FastAPI, `gpx_view.api` or concrete infrastructure adapters, and no GPX,
FIT or TCX type may appear here.

### `gpx_view.infrastructure`

Owns concrete adapters and is the only layer that knows formats and storage.
Implemented packages: `gpx/` (the GPX 1.1/1.0 adapter, including the table of
extension schemas it understands), `database/` (SQLite, the only place that
imports `sqlite3`), `filesystem/` (managed raw storage and the import directory
boundary), plus `clock.py`, `private_data.py` and `assembly.py`. Planned: `fit/`,
`tcx/`.

`assembly.py` builds the object graph both entry points share -- the HTTP
application and the command line -- so a second, subtly different wiring cannot
appear.

A format adapter ends at a clear normalization boundary. GPX-specific XML
structures must not travel through the application: the adapter produces the
canonical normalized track plus source metadata, and stops there.

It implements contracts declared by the inner layers and must not import
`gpx_view.api`.

### `gpx_view.api`

The HTTP projection, nothing more: validate the request, call an application use
case, project the result. No classification or analysis heuristic in a route.

Current content:

```
GET    /healthz
GET    /api/v1/tracks
GET    /api/v1/tracks/{track_id}
GET    /api/v1/tracks/{track_id}/geometry
GET    /api/v1/tracks/{track_id}/analysis
PUT    /api/v1/tracks/{track_id}/classification
DELETE /api/v1/tracks/{track_id}/classification
GET    /api/v1/statistics/year/{year}
GET    /api/v1/statistics/year/{year}/monthly
```

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
there is no upload endpoint -- see "Input paths" below.

Track endpoints project the **current generation only**. What happened across
processing runs is a separate question with a separate answer, and it is answered
on the command line rather than over HTTP: `/api/v1/imports/{sha256}/processing`
would open a resource family whose other verbs do not exist, and the operator who
can act on the answer is on the machine that holds the data anyway.

### Future browser UI

Also projection only. Browser state is never business authority; every value the
UI shows must be recomputable by the backend.

### `gpx_view.main`

Composition root. Creates the FastAPI application and wires routers. No business
logic.

### `gpx_view.config`

The only configuration mechanism, reading `GPX_VIEW_*` environment variables via
`pydantic-settings`. No business logic, no second config system.

`GPX_VIEW_TIMEZONE` is the IANA zone month and year boundaries are drawn in. It
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

*Implemented as `gpx_view.domain.NormalizedTrack`, `TrackSegment`, `TrackPoint`
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
later decision. Nothing carries a heart rate, cadence, power or temperature field:
no contract needs one yet, and the model has to stay small to stay extensible.

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

*Implemented as `gpx_view.domain.ProcessingRun`, `gpx_view.domain.ProcessingStatus`
and `gpx_view.domain.NORMALIZATION_SCHEMA_VERSION`.*

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

`gpx_view.domain.is_processing_current` is the only comparison, and
`gpx_view.application.InstalledProcessing` is the only place that says what this
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
`gpx_view.infrastructure.gpx.extensions` maps `(namespace, element)` onto meaning
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

*Implemented as `gpx_view.application.TrackImporter`, a `typing.Protocol`.* An
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
track_classification_overrides  what the user said
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
```

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
  identity a user corrected survives and the override table is never touched,
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
<data dir>/gpx-view.sqlite3
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

All persistent state lives under `GPX_VIEW_DATA_DIR`. Nothing is written to the
working directory, `/tmp`, a home directory or the container layer.

### Privacy at rest

A GPS archive is a movement profile of a real person, so GPX-View creates its own
artifacts readable by their owner and by nobody else:

| Artifact | Mode |
| --- | --- |
| data directory | `0700` |
| managed raw directories | `0700` |
| managed raw artifacts | `0600` |
| temporary raw artifacts | `0600` |
| SQLite database | `0600` |
| `-wal` and `-shm` | `0600` |

*Implemented as `gpx_view.infrastructure.private_data`.*

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

*Implemented as `gpx_view.domain.analysis.AnalysisProfile` and
`gpx_view.application.InstalledAnalysis`.*

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
Berlin -- so `GPX_VIEW_TIMEZONE` decides the boundaries and every response names
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

*Implemented as `gpx_view.application.ImportTracks`.*

| Path | State |
| --- | --- |
| `gpx-view import <file>...` | implemented |
| `gpx-view scan` over `GPX_VIEW_IMPORT_DIR` | implemented |
| `gpx-view reprocess <sha256>` | implemented |
| `gpx-view reprocess --failed` | implemented |
| `gpx-view reprocess --outdated` | implemented |
| `gpx-view processing-status <sha256>` | implemented |
| `gpx-view analyze <track_id>` | implemented |
| `gpx-view analyze --outdated` | implemented |
| `gpx-view analyze --all` | implemented |
| HTTP upload | **not implemented** -- deliberately, see below |
| Future API import | not implemented |

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

There is **no upload endpoint**. Without authentication, an unauthenticated
endpoint that accepts files and writes them to disk is not something to add
casually. Importing is therefore an operator action on the machine that holds the
data, and it stays that way until authentication exists.

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

`gpx-view processing-status <sha256>` is the read side of the same facts: the
current run against the latest attempt, each processing component as "what
produced this -> what is installed", the track count and the outdated verdict. It
names hashes, run identities, versions and error codes, and never a path, a
filename or a coordinate. There is no HTTP endpoint for it: `/api/v1/imports/...`
would be a new resource family whose other verbs do not exist, and the operator
who can reprocess is on the machine that holds the data anyway.

The import directory is **input, not authority**: nothing in it is written,
renamed, moved or deleted. Filenames are display metadata only, and a re-offered
file is recognised as an exact duplicate and skipped. There is no file system
watcher: the scan is explicit, which is easier to reason about and cannot hold a
thread open for weeks.

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

The configured root itself stays trusted and may be a symbolic link, exactly as
the managed raw storage root does. `read_bounded` remains for paths an operator
names on the command line: those were chosen deliberately.

## Logging

Structured and data-sparse. Logged: a content hash prefix, track and point counts,
the format, the processing status, the reason a file was skipped. **Not** logged:
coordinates, raw payloads, whole file contents, or personal filenames where they
add nothing. Turning up the log level must never start printing someone's movement
profile.

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

- frontend technology, map library and charts
- analysis algorithms beyond the first versions: 3D path length, grade, splits,
  personal records, streaks and lifetime totals
- digital elevation model correction, which needs a network dependency
- planned duration as an explicitly estimated metric
- week-based or custom statistics periods, and a per-request timezone override
- FIT, TCX, KML and GeoJSON adapters -- the boundary is proven, the adapters are
  not written, and no dummy adapter stands in for them
- semantic duplicate detection
- authentication, and with it any file upload endpoint
- a file system watcher, as opposed to the explicit scan
- Android client or companion app
- sensor schemas: heart rate, cadence, power, temperature, FIT developer fields

See `docs/adr/0001-project-foundation.md`,
`docs/adr/0002-source-agnostic-track-model.md`,
`docs/adr/0006-track-analysis-and-statistics.md` and
`docs/technical/contracts.md`.
