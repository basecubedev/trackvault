# ADR 0003: Import and persistence model

## Status

Accepted (2026-08-08)

Extends ADR 0001 and ADR 0002, both of which stay valid. ADR 0002 deliberately
left the normalized track record, the import port and the persistence schema
open, because their shape was the output contract of an importer that did not
exist. This ADR closes those three decisions with the first one that does.

## Context

The first productive vertical slice had to run all the way from an untrusted file
to a queryable track:

```
immutable raw source → format detection → GPX adapter → normalized track
→ evidence → classification → one import use case → SQLite → read API
```

Two forces shaped every decision along it. GPX must stay an adapter and must not
become the domain model, and the archive is meant to hold a decade of personal
movement data, so nothing may quietly become unrecoverable.

## Decision

### 1. Raw imports and processing provenance are separate authorities

`RawImport` records the source only: content hash as identity, size, original
filename, received instant, media hint, input channel. `ProcessingRun` records
who normalized it, in which version, against which normalization schema, when,
and with what outcome.

The importer version was previously documented as part of the raw import. It is
not a property of a file. Separating them makes the invariant enforceable:

> Reprocessing the same raw import with importer v2 does not mutate the raw
> import.

A failed run is a named, recoverable state rather than silent absence, so a file
we cannot read today is kept and can be reprocessed tomorrow.

### 2. The normalized track is the field set the importer actually needed

`TrackPoint`, `TrackSegment`, `NormalizedTrack` and `SourceMetadata`, and nothing
else. No heart rate, cadence, power, temperature or developer-field schema: no
contract needs one, and a model that is small stays extensible.

Segment boundaries are preserved — a paused recording is one track of several
segments, never several tracks and never one flattened point list. One file may
produce any number of tracks, including none.

There is no `kind` field beside the classification, so the effective track kind
has exactly one authority.

### 3. Evidence is the boundary between adapter and verdict

An adapter observes; the classifier decides. The classifier's only input is a set
of stable evidence codes, so it cannot see a creator, a filename or a namespace.
"A source name never decides a track kind" therefore holds structurally: a lookup
table from a vendor to a kind cannot be written there.

The first classifier is conservative on purpose. `RECORDED` needs measurement
evidence — the receiver quality and heading only a measuring device produces.
`PLANNED` needs a positive observation that the geometry was computed rather than
travelled — a route structure, a link back to where it was obtained, or
turn-by-turn navigation instructions — and never a single one of them alone.
Everything else is `UNKNOWN`, which stays a normal result.

Turn instructions were added after checking the rules against real private files:
three planned exports carried them and the recording did not. The observation is
about the data, not the writer — the same application produced both files and gets
opposite verdicts — and it is matched by element name in any namespace, exactly
like `hdop` and `course`.

### 4. Content hash is the exact-duplicate authority and the storage path

Accepted bytes are kept byte-identically under `<data dir>/raw/sha256/ab/…`. The
path is derived from the content hash and from nothing else; a filename is display
metadata and never authorises a location. Identical bytes are one artifact and one
archive entry, whatever the file was called.

Semantic duplicates stay out of scope, and no heuristic for them is added.

### 5. SQLite through the standard library, with explicit migrations

No ORM and no migration framework. `PRAGMA user_version` plus numbered migration
functions covers "empty database → current schema" and "version N → N+1", and a
database from a newer build is refused rather than downgraded.

One import is one explicit transaction. Foreign keys are enforced, a busy timeout
is set, and write-ahead logging lets the HTTP server and a command-line import
share the file. Tracks are identified by their raw import and their position in
the source document, so reprocessing replaces the normalized data and the detected
classification while an existing user override survives untouched.

### 6. One import use case, several input paths

`ImportTracks` is the only place a file becomes an archive entry. The command-line
import and the import-directory scan are readers that hand bytes to it. A future
upload endpoint or API import will be another reader, not another pipeline.

The import directory is input, not authority: nothing in it is written, renamed or
deleted, symbolic links are skipped, and re-offered files are recognised as exact
duplicates.

### 7. The HTTP API is read-first, with one correction endpoint

Listing, reading, geometry, and the classification override. There is no upload
endpoint: without authentication, an endpoint that accepts files and writes them
to disk is not something to add casually. The read and override endpoints carry a
documented trusted-network assumption.

### 8. A managed Docker volume, not a host bind mount

Once the archive actually writes, the reserved `./data:/data` bind mount turned
out not to work: Docker leaves a bind mount with the host directory's ownership,
and the non-root runtime user cannot write into it without `chmod 777` or running
as root. A named volume is initialised from the image and arrives owned by the
runtime user. The user has a fixed uid/gid so an operator who wants a bind mount
deliberately can match it.

## Consequences

Positive:

- The archive is reconstructible. Raw imports are the authority, processing runs
  are history, and the SQLite content is a projection that a future importer can
  regenerate.
- A parser or classifier upgrade is a normal operation rather than a data-loss
  risk, and it cannot overrule a user's correction.
- A future FIT importer terminates at the same boundary: it produces the same
  candidates, and classification, persistence and the API do not change.
- One import bug has one place to be fixed, whichever channel the file arrived
  through.
- All persistent state lives under one directory, so a backup is a directory copy.

Trade-offs, accepted:

- `defusedxml` is a new runtime dependency. Rolling the same hardened parser
  configuration by hand would mean installing handlers on a private attribute of
  the standard library parser — clever code that breaks quietly.
- Documents are parsed into memory rather than streamed. The input byte limit
  bounds the cost, and streaming would complicate the adapter for a case the limit
  already covers.
- The conservative classifier will answer `UNKNOWN` for files a human would
  classify at a glance. That is the intended direction of error.
- Raw imports are kept even when processing fails, including for formats we cannot
  read at all. Storage is spent on files that may never become tracks; the
  alternative is discarding evidence that a later importer could use.
- The named volume is less immediately inspectable than a host directory. The
  fixed uid/gid and the documented bind-mount variant are the answer.

## Alternatives considered

- **SQLAlchemy and Alembic now.** Rejected: the schema is ten small tables with
  no dynamic querying. An ORM would add a dependency, a second modelling language
  and the standing temptation to let database models become the domain model.
- **Storing an `effective_kind` column.** Rejected: a second storable kind is a
  second authority that goes stale the moment an override changes.
- **Deriving planned from the writing application.** Rejected outright — it is the
  exact shortcut ADR 0002 forbids, and it fails on the first recording exported by
  a planner or the first planned route exported by a recorder.
- **Timing-pattern heuristics for synthetic timestamps.** Rejected for now: a
  one-second logger and a route planner both produce regular intervals, so the
  signal is not robust enough to decide anything, and a wrong `PLANNED` is worse
  than an honest `UNKNOWN`.
- **A file system watcher for the import directory.** Rejected: an explicit scan
  is easier to reason about, cannot leak a thread, and loses nothing because the
  duplicate check makes rescanning free. *(ADR 0013 has the server run that
  scan on an interval; a watcher stays rejected.)*
- **An authenticated upload endpoint.** *(Superseded by ADR 0011, which adds an
  unauthenticated one, bounded and switchable, for a trusted network.)*
  Deferred: authentication is a decision of
  its own, and a server-side import covers the actual deployment (phone → sync →
  server folder) without it.
