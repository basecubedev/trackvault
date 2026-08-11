# Business contracts

These invariants hold for every future feature of GPX-View. They are written down
before the importer exists, because they constrain how the importer may be built.

Items marked *implemented* are executable and protected by tests in
`tests/contract/` and `tests/unit/`. Everything else is a documented contract for
work that has not started.

## Input format is never the domain model

GPX is an exchange format, not a track. So are FIT, TCX, KML and GeoJSON, and
Locus Map, Komoot and Garmin are sources, not business types. Every importer
normalizes onto the canonical normalized track; from there on the application does
not know where a track came from. See `architecture.md`.

## Raw imports are immutable source evidence

*Implemented as `gpx_view.domain.RawImport`.*

The original import is the authority for "what was imported":

- The original file is never modified.
- Reprocessing is always possible from the original again.
- A normalized projection in a database does not replace the original.
- Raw data is not deleted because the current parser version succeeded.

A raw import is identified by its content hash, so identical bytes are the same
raw import whatever their filename or input channel. The filename is display
metadata and never authorises a storage location.

## Processing provenance is separate from the source

*Implemented as `gpx_view.domain.ProcessingRun`.*

Importer name, importer version and normalization schema version describe the
processing, not the imported file. They therefore live on the processing run:

```
ProcessingRun
─────────────────────────
raw import identity
importer / importer_version
normalization_schema_version
processed_at
status + error code
```

> Reprocessing the same raw import with importer v2 must not mutate the raw
> import.

A run either succeeded or failed, and a failed run must name a stable error code.
"Failed silently" is not a state the model can express, which is what makes a
failed import recoverable rather than invisible.

## Duplicates

```
exact_duplicate != semantic_duplicate
```

An **exact duplicate** is a byte-identical import, SHA-256 identical. A **semantic
duplicate** is two different files that may describe the same activity -- a Locus
GPX and a Garmin FIT of one ride. The second case is much harder and is developed
separately. No premature heuristic.

## Track kind

*Implemented as `gpx_view.domain.TrackKind`.*

```
recorded
planned
unknown
```

`unknown` is explicitly allowed and permanent. Classification is evidence-based,
and failing to `unknown` is better than invented certainty.

The kind is format-independent. None of the following proves anything on its own:

| Observation | Does **not** prove |
| --- | --- |
| GPX `<trk>` element | `RECORDED` |
| GPX `<rte>` element | the full business semantics of `PLANNED` |
| timestamps present | `RECORDED` |
| timestamps absent | `PLANNED` |
| source is Komoot | `PLANNED` |
| source is Locus Map | `RECORDED` |

> The presence of GPX timestamps does not prove that a track was actually
> recorded.

Route planners write synthetic or estimated time values into planned routes, and
recordings can be stripped of time data.

## Classification result

*Implemented as `gpx_view.domain.ClassificationResult`.*

A classification is only meaningful with its evidence and its producer:

```
ClassificationResult
─────────────────────────
kind
confidence
evidence
method / method_version
```

Example:

```
kind       RECORDED
confidence 0.97
evidence   gps_accuracy_present
           course_measurements_present
           natural_timestamp_distribution
```

```
kind       PLANNED
confidence 0.95
evidence   route_planner_source
           synthetic_timestamps
           no_gps_accuracy
```

Enforced invariants:

- `confidence` lies within `[0.0, 1.0]`.
- `method` and `method_version` are always set, so a stored result stays
  explainable and can be re-evaluated when the classifier changes.
- A `RECORDED` or `PLANNED` result must state at least one evidence code. Without
  evidence the only permitted answer is `UNKNOWN`.

No productive classification heuristic exists yet. This contract fixes the shape of
the answer, not how it is found.

## Detected vs. effective classification

*Implemented as `gpx_view.domain.TrackClassification`.*

```
detected classification    what the classifier found
effective classification   what the application acts on
```

> An explicit user correction beats the automatic classification.

Example:

```
Detected:      PLANNED, confidence 0.72
User override: RECORDED
Effective:     RECORDED
```

Enforced invariants:

- `effective_kind` returns the override when one is present, otherwise the
  detected kind.
- Reprocessing (`reclassified`) replaces the detected result and **keeps** the
  user override. A parser or classifier upgrade must never silently overwrite a
  manual correction.
- `UNKNOWN` is a valid override: a user may state that the kind cannot be decided.

### One authority for the effective kind

```
TrackClassification
    detected
    user override
        ↓
    effective_kind
```

`TrackClassification.effective_kind` is the **only** authority for a track's
effective `TrackKind`. A normalized track therefore carries no second, separately
storable `kind` value beside its classification: two storable kinds are two
authorities that can disagree, and the disagreement would surface as wrong actual
and planned aggregates.

A convenience accessor on the normalized track is allowed, but only as a
projection that reads `classification.effective_kind`. Persisting a normalized
track stores the detected result and any override, never a third derived kind
column.

## Activity

*Implemented as `gpx_view.domain.Activity`.*

```
walking
hiking
cycling
running
scooter
motorcycle
other
unknown
```

Activity is independent of file format, source and track kind: a planned cycling
route and a recorded cycling ride share the activity and differ in kind.

The taxonomy is deliberately small and flat -- no sport hierarchy. Activity comes
from explicit source metadata or from the user; the classifier does not guess it,
so `unknown` is a normal outcome. `walking` and `hiking` are separate values
because the distinction matters for statistics, but neither may be inferred from
the other without evidence. See ADR 0002.

## Metric provenance

*Implemented as `gpx_view.domain.MetricProvenance`.*

```
measured
derived
estimated
```

| Value | Meaning | Examples |
| --- | --- | --- |
| `measured` | read directly from a sensor or the source system | heart rate, power, temperature, reported GPS accuracy |
| `derived` | computed from measured or normalized data | distance from coordinates, speed from coordinates and timestamps, moving time, elevation gain after filtering |
| `estimated` | produced by a model or planner | planned duration, predicted walking speed, routing-engine ETA |

> Provenance must never be silently discarded or conflated.

Provenance is part of a metric's meaning, not an annotation on it. Values of
different provenance must not be treated as if they carried the same business
meaning.

Forbidden — mixing provenance behind a single number:

```
measured + derived + estimated
```

summed, averaged, or presented under one label as if the parts meant the same
thing. An aggregate that deliberately mixes provenance says so in its own name
and never passes as a measured or derived result.

Explicitly allowed — and necessary — is comparing values across provenance *as a
comparison*, with both provenances visible:

```
source-reported distance   (measured)
vs.
distance derived from coordinates   (derived)
```

Such comparisons are how the application does quality checks, deviation analysis
and the choice of which value to present as canonical. The rule constrains
conflation, not comparison.

## Actual vs. planned aggregates

*Implemented as `TrackKind.contributes_to_actual_totals` and
`TrackKind.contributes_to_planned_totals`, and as the matching properties on
`TrackClassification`, which apply them to the **effective** kind.*

Actually completed and planned figures are separate sets.

Actual aggregates:

```
actual_distance
actual_duration
actual_moving_time
actual_elevation_gain
actual_monthly_distance
actual_yearly_distance
```

They include only tracks whose **effective** kind is `RECORDED`.

Planned aggregates:

```
planned_distance
planned_duration
planned_elevation_gain
```

They include only tracks whose effective kind is `PLANNED`.

`UNKNOWN` belongs to neither set and must never be silently assigned to one. An
aggregate that deliberately mixes sets must say so in its own name and must not be
presented as an actual result.

## Source metadata

Conceptually:

```
SourceMetadata
─────────────────────────
format
creator
application
vendor
source links
extensions summary
```

Examples:

```
format       GPX 1.1
creator      Locus Map
application  Locus Map 4.x
source link  komoot.de
```

> Source metadata is evidence and provenance information, not business authority.

Forbidden, without further evidence:

```python
if source == "locus":
    kind = RECORDED

if source == "komoot":
    kind = PLANNED
```

The classification step weighs evidence and may still answer `UNKNOWN`. Source
metadata is preserved so a decision can be explained and revisited.

The record is not implemented yet; its field set is part of the first importer's
output contract.

## Vendor and format extensions

- Known extensions may later be normalized into business fields.
- Unknown extensions must not needlessly fail the parse.
- Relevant unknown source metadata should be preservable for later reprocessing.
- Domain models still must not adopt arbitrary XML fragments as business fields.

## Single import authority

All import paths -- manual upload, watched import folder, future API import -- run
through the same canonical `ImportTrack` use case. There is never a second parsing
or persistence path per input channel. See `architecture.md`.

## Privacy

GPX, FIT and similar files are personal movement data. See
`docs/developer/agent-rules.md`, section "Personal GPS data". Committed test
fixtures are synthetic or explicitly cleared, never real personal recordings, and
coordinates or raw payloads never end up in normal logs or exception messages.
