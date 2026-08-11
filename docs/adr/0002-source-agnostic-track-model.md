# ADR 0002: Source-agnostic track model

## Status

Accepted (2026-08-08)

Extends ADR 0001, which stays valid: Python, FastAPI, uv, `src/` layout, layered
architecture, contract-first development, Docker-first deployability, SQLite as the
intended first persistence backend.

## Context

ADR 0001 described the foundation with GPX from Locus Map as the concrete starting
point. The product goal has since been sharpened:

> GPX-View is a self-hosted, source-agnostic activity and route archive for
> recorded and planned geospatial tracks.

Tracks will arrive from several applications (Locus Map, Komoot, Garmin, Wahoo,
OsmAnd, GPSLogger) in several formats (GPX, FIT, TCX, KML, GeoJSON), through
several channels (manual upload, a phone auto-sync directory on the server, later
an API). GPX is only the first of these.

The failure mode this ADR exists to prevent is well known: the first parser's data
shape quietly becomes the domain model. Once GPX XML structures or Locus extensions
reach statistics and presentation code, adding FIT means rewriting the business
logic, and every input channel grows its own parsing and persistence path.

## Decision

1. **GPX is the first adapter, not the domain.** `Input format != domain model`.
   The same holds for FIT, TCX and for source applications: `Locus != Track`.
2. **One canonical normalized track.** Every importer normalizes onto a shared
   model; from there the core application is format-blind. The record itself is
   deliberately not designed yet, because its field set is the output contract of
   the first importer. The *boundary* is fixed now, the field list is not.
3. **Raw imports are immutable source evidence.** Originals are never modified,
   reprocessing always starts from the original, and a normalized projection never
   replaces it.
4. **`recorded` / `planned` / `unknown` stay distinct**, and `unknown` is
   permanent and first-class. Neither GPX element type, nor the presence of
   timestamps, nor the exporting application proves a kind.
5. **Classification results carry evidence.** `ClassificationResult` requires
   confidence, method, method version, and at least one evidence code for any
   `RECORDED` or `PLANNED` answer. Without evidence the only permitted answer is
   `UNKNOWN`.
6. **User override beats the classifier.** `TrackClassification` separates the
   detected result from the effective kind, and reprocessing keeps an existing
   override.
7. **Metric provenance is part of a metric's meaning.** `MEASURED`, `DERIVED` and
   `ESTIMATED` are never conflated.
8. **Actual and planned aggregates are separate sets**, selected by the *effective*
   kind. `UNKNOWN` contributes to neither.
9. **Source metadata is evidence, not authority.** No `if source == "locus"`
   shortcut decides a kind.
10. **Further formats arrive as adapters** behind one import port, and all input
    channels route through the single canonical `ImportTrack` use case.
11. **Activity keeps both `walking` and `hiking`** in a small, flat taxonomy.
    See the discussion below.
12. **The server application remains the main platform.** Python, FastAPI, Docker,
    self-hosted. An Android app would at most be a later optional client, and is
    out of scope.

## Walking vs. hiking

The question was whether the taxonomy should be shrunk by dropping `hiking`.

Both values are kept, because activity is not something the system infers. It comes
from explicit source metadata or from the user, and both Locus Map and Komoot
distinguish walking from hiking already. Dropping `hiking` would discard
information the source hands us, and re-splitting later would require manual review
of every historic track.

The cost is bounded because nothing guesses: an importer with no evidence emits
`UNKNOWN`, not `WALKING`. Keeping the value therefore adds no heuristic and no
speculative code, while keeping the owner's own vocabulary ("Wandern") available.

The taxonomy stays flat -- no sport hierarchy, no discipline subtypes -- and grows
only when a real source or a real user need requires a value.

## Consequences

Positive:

- A FIT or TCX importer can be added later without rewriting the domain,
  statistics or presentation: it terminates at the same normalization boundary.
- Statistics are format-independent by construction.
- Keeping raw imports makes reprocessing after parser or classifier upgrades a
  normal operation rather than a data-loss risk.
- Provenance and evidence make every number explainable, which is what makes an
  archive trustworthy over years.
- One import use case means one place to fix an import bug, no matter which channel
  the file arrived through.

Trade-offs, accepted:

- A normalization layer is required, and normalization is where information gets
  lost if done carelessly.
- Not every source format maps losslessly onto the same business fields. That is
  precisely why raw imports and source metadata are preserved separately.
- Requiring evidence on every non-`UNKNOWN` classification makes a trivial
  classifier impossible to write. That is intended.
- The domain currently holds value types without a `Track` to attach them to. This
  is deliberate incompleteness, not an oversight.

## Alternatives considered

- **Model GPX directly and generalise later.** Rejected: this is exactly the
  failure mode above, and "later" never has a good moment.
- **Design the full `NormalizedTrack` record now.** Rejected: its field set is a
  guess until a real importer produces one, and a guessed record would immediately
  become a second, competing authority.
- **Define `TrackImporter` as a `Protocol` now.** Rejected for the same reason: its
  return type is the undesigned normalized track. A protocol with a guessed result
  type documents nothing and constrains nothing. The boundary is documented and
  the architecture contract tests enforce that no parser type leaks inwards; the
  protocol lands with the first importer.
- **A registry/factory layer for formats up front.** Rejected: one adapter does not
  need a factory. A `Protocol` plus, if ever needed, a small dispatcher.
