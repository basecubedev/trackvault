# Business contracts

These invariants hold for every feature of GPX-View, present and future. Most were
written down before the first importer existed, because they constrain how an
importer may be built.

Items marked *implemented* are executable and protected by tests in
`tests/contract/`, `tests/unit/` and `tests/integration/`. Everything else is a
documented contract for work that has not started.

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
processing profile
    importer / importer_version
    normalization_schema_version
    classifier / classifier_version
processed_at
status + error code
```

> Reprocessing the same raw import with importer v2 must not mutate the raw
> import.

A run either succeeded or failed, and a failed run must name a stable error code.
"Failed silently" is not a state the model can express, which is what makes a
failed import recoverable rather than invisible.

### Processing currency

*Implemented as `gpx_view.domain.ProcessingProfile`,
`gpx_view.domain.is_processing_current` and
`gpx_view.application.InstalledProcessing`.*

The five versions are one value because they answer one question together: was
the stored generation produced by the processing this build installs? A run
records the whole profile, and one function compares it with the installed one.

> Semantically different output must not claim the same version.

A change to the normalized identity, the candidate set, the evidence semantics or
the interpretation of a field is a version bump. An internal refactoring with
identical output is not. Renaming a version is not an option either: a run that
claims a version whose output it could not have produced makes every later
currency decision wrong.

A generation counts as **not** current when any component differs, when the run
cannot prove which processing produced it, when there is no successful run at
all, when its adapter is no longer installed, or when its profile is *newer* than
the installed one. History is never rewritten to make an old run look current:
what ran back then is not knowable now.

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
confidence 0.95
method     evidence-weights / 2
evidence   track_element_present
           timestamps_present
           gps_accuracy_present
           course_measurements_present
```

```
kind       PLANNED
confidence 0.75
method     evidence-weights / 2
evidence   route_element_present
           timestamps_absent
           measurement_metadata_absent
```

Enforced invariants:

- `confidence` lies within `[0.0, 1.0]`.
- `method` and `method_version` are always set, so a stored result stays
  explainable and can be re-evaluated when the classifier changes.
- A `RECORDED` or `PLANNED` result must state at least one evidence code. Without
  evidence the only permitted answer is `UNKNOWN`.

## Classification rules

*Implemented as `gpx_view.domain.classify`, method `evidence-weights`, version 2.*

The classifier receives **evidence codes and nothing else**. It cannot see a
creator string, a filename or a namespace, which makes "a source name never
decides a track kind" a structural property rather than a promise: a lookup table
from a vendor to a kind is impossible to write here, not merely forbidden.

The guiding rule is conservatism:

> A false `unknown` is preferable to false certainty.

Two generic observations carry the decision:

| Supports `recorded` | Why |
| --- | --- |
| `gps_accuracy_present` | a device that measures positions reports how well it measured -- dilution of precision, satellite count, fix type. A routing engine has nothing to report. |
| `course_measurements_present` | a measured heading comes from hardware. |

| Supports `planned` | Why |
| --- | --- |
| `route_element_present` | the source described this as planning structure, not as a recording. |
| `route_instructions_present` | the document carries turn-by-turn navigation instructions. They exist because a route was computed for them; a recording has nowhere to get them from. |
| `measurement_metadata_absent` | nothing measured anything about these positions at all. |
| `timestamps_absent` | weak support only, and never on its own. |

Decision:

- `RECORDED` needs measurement evidence and a clear lead over the planned side.
- `PLANNED` needs a clear lead, at least one *positive* observation, and at least
  two distinct observations in total. Absence alone is never enough -- a recording
  stripped of its metadata looks exactly like planning data -- and no single
  structural feature decides either: a route element or a turn instruction says
  how the geometry was *produced*, not that nobody then travelled it.
- Anything else is `UNKNOWN`, which stays a normal, permanent result.

Strong measurement evidence outweighs a planning signal, because riding a computed
route is still riding: two independent measurement values can only come from a
device that actually moved through the positions. One measurement value against
one planning signal decides nothing.

Timing decides nothing. Route planners write synthetic times and recordings get
stripped of them, so neither the presence nor the absence of timestamps moves the
verdict on its own, and no timing-pattern heuristic is attempted at this stage.

An external link decides nothing either, and version 2 of these rules exists
because version 1 let it. A GPX `<link>` is a related web resource: applications
write their own home page into it, planners write a permalink, and both look
identical from here. Paired with `measurement_metadata_absent` -- an absence that
describes a stripped recording just as well -- it was enough to reach a verdict,
so an ordinary recording exported by an application that mentions its own website
came out `PLANNED`. `external_link_present` is now weighed on neither side. It
stays provenance worth keeping and stops being a business signal.

That is a deliberate loss of certainty. Documents that used to come out `PLANNED`
on a link alone now come out `UNKNOWN`, which is the direction of error this
project chose, and a user override exists for exactly those cases.

Confidence is derived from how far the winning side led, and `UNKNOWN` scores
zero: it is the answer given when the evidence supports no kind, so there is no
support to express.

## Evidence codes

*Implemented as `gpx_view.domain.EvidenceCode`.*

An importer states *what it saw*; it never states what that means. Codes are
lower case, underscore separated, and part of the stored data: renaming one
invalidates every stored explanation, so they are not renamed between parser
versions.

```
track_element_present          route_element_present
route_instructions_present     timestamps_present
timestamps_absent              gps_accuracy_present
course_measurements_present    measurement_metadata_absent
external_link_present          activity_metadata_present
```

`source_link_present` is **deprecated** and no longer produced. It claimed the
document declared where its geometry *came from*, which is more than an exchange
format's link element says; `external_link_present` states the observation
without the claim. The old code keeps its old meaning in results written by
classifier version 1, so those explanations stay readable, and reprocessing
replaces them.

They are observations, never user-facing text.

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
column, and the HTTP payload projects both so a reader can tell a verdict from a
correction.

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
external links
extensions summary
```

Examples:

```
format         GPX 1.1
creator        Locus Map
application    Locus Map 4.x
external link  https://example.test/route/7
```

They are **external links**, not source links. A GPX `<link>` is a related web
resource: an application writes its own home page into it as readily as a planner
writes a permalink, and both look identical from here. Calling the field a
*source* link claimed the document declared where its geometry came from, which
is more than the exchange format states — the same overstatement that let
classifier version 1 reach `PLANNED` on a link alone.

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

*Implemented as `gpx_view.domain.SourceMetadata`:* exchange format, format
version, creator, external links and a sorted summary of the extension namespaces a
document used. It stays format-independent -- a FIT importer fills the same
fields and gets none of its own.

## Vendor and format extensions

- Known extensions may later be normalized into business fields.
- Unknown extensions must not needlessly fail the parse.
- Relevant unknown source metadata should be preservable for later reprocessing.
- Domain models still must not adopt arbitrary XML fragments as business fields.

> An unknown namespace is metadata, not semantic authority.

An extension element is interpreted on its **namespaced** name, from a small
table of schemas the project has evidence for. A local name on its own is a word
rather than a schema: `course` in a track-point extension is a measured heading,
`course` in a golf application's namespace is a golf course. Matching a local
name in any namespace let a foreign document manufacture the measurement evidence
that decides `RECORDED`.

An element from an unknown namespace therefore produces no activity and no
evidence. It still never fails the parse, and the namespace survives in the
extension summary, so the raw import can be reprocessed once that schema gains a
meaning.

## Duplicates and raw integrity

*Implemented as `gpx_view.application.RawArtifactState` and the
`ImportTracks` duplicate path.*

An exact duplicate is a no-op only while the archive can still produce the bytes
it says it holds:

```
DB metadata + hash-valid managed artifact   -> duplicate
DB metadata + missing artifact              -> repaired from the same bytes
DB metadata + wrong bytes                   -> fail closed
managed artifact without DB metadata        -> adopted, not written again
```

> A known database record does not prove its managed raw artifact is healthy.

Recognising a content hash proves the archive *once* held those bytes. Answering
"already imported" on the row alone means an archive that lost an artifact keeps
saying so to the only offer of those bytes it will ever get again.

Repair is not a guess: the bytes offered again hash to the digest the raw import
is filed under, which is the same proof the first import needed. It restores the
copy and appends no processing run, because the normalized generation was never
in question.

## Single import authority

All import paths -- manual upload, watched import folder, future API import -- run
through the same canonical `ImportTrack` use case. There is never a second parsing
or persistence path per input channel. See `architecture.md`.

## Privacy

GPX, FIT and similar files are personal movement data. See
`docs/developer/agent-rules.md`, section "Personal GPS data". Committed test
fixtures are synthetic or explicitly cleared, never real personal recordings, and
coordinates or raw payloads never end up in normal logs or exception messages.

## Import limits and error codes

*Implemented as `gpx_view.application.ImportLimits` and `ImportErrorCode`.*

Imported files are untrusted input. Configurable limits bound what one document
may cost before anything is stored: input bytes, tracks per document, segments per
track and total positions. The defaults comfortably cover an ordinary long GPS
recording; they exist to bound a hostile or broken file, not to ration normal use.

Every refusal names one stable code:

```
unsupported_format        invalid_gpx              unsafe_xml
import_too_large          too_many_tracks          too_many_track_segments
too_many_track_points     invalid_coordinate       invalid_timestamp
raw_storage_failed        raw_storage_missing      raw_storage_corrupt
persistence_failed        track_not_found
```

The three storage codes are three different problems for whoever has to fix them.
`raw_storage_missing` is recoverable -- offering the same bytes again restores the
managed copy, because they hash to the digest the raw import is filed under.
`raw_storage_corrupt` is not: an artifact whose bytes are not the ones its name
claims is evidence of disk corruption or tampering, so it is never overwritten,
and recovery is an operator decision.

`invalid_gpx` names the format on purpose: "not GPX at all" and "broken GPX" are
different problems for whoever has to fix the file.

An error carries a code and a short structural detail. It never carries a
coordinate, a payload excerpt, a file system path or a stack trace: diagnostics
must not become a side channel for personal movement data.

## Time

*Partly implemented; the display decision is deliberately open.*

- Timestamps are parsed timezone-aware and stored as unambiguous UTC instants. A
  value without a zone is refused rather than guessed at.
- Point order is source data and is never rearranged, and a missing timestamp
  stays missing rather than being interpolated.
- A track's temporal extent is the earliest and latest instant its own positions
  carry.

> A document's export time is not an activity start time.

A GPX `<metadata><time>` is when the file was written. It is deliberately not
normalized into any track field, because an export date months after the activity
would otherwise fall into the wrong month of a future statistic. The raw import
keeps it.

Which timezone instants are *displayed* or bucketed in -- and therefore which
month a late-evening activity counts towards -- is a separate decision that comes
with the statistics feature.
