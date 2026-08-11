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

## User-owned track metadata

*Implemented as `gpx_view.domain.UserTrackMetadata`, `effective_title` and
`TrackQueries.set_metadata`.*

> A correction belongs to the user, and it never edits the evidence.

```
source title       what the document said         never modified
title override     what the user said             the display authority
effective title    the override, else the source  a projection of the two
```

The same arrangement as the classification override, for the same reason: a
reprocess replaces every normalized column of a track row, so a title stored
there would be one an importer upgrade silently discards. The correction lives
in its own table, keyed on the track identity, and therefore survives
reprocessing and returns with a candidate a later run temporarily stopped
producing.

Enforced invariants:

- **The raw import stays byte-identical.** Nothing on this path writes to the
  source, the normalized track or the detected classification.
- **`display_title` is a projection.** No third title is stored, and `None`
  stays a real answer for a document that names nothing.
- **Blank is absence, not an empty name.** A title of spaces clears the
  correction; storing it would hide the source title behind something that
  renders as nothing.
- **Plain text, bounded, never truncated.** Titles are a single line, notes may
  hold line breaks, both refuse control characters, and a value that is too long
  is refused rather than shortened -- somebody's sentence is not the archive's
  to cut. Nothing here is markup.
- **A partial update is partial.** An absent field keeps its value; an explicit
  `null` clears it. A rename that silently deleted a note is how a `PATCH`
  endpoint loses data.

## Which periods an archive has

*Implemented as `gpx_view.application.GetAvailableYears`.*

> The calendar an archive offers comes from the archive, not from a clock.

Years are read from the tracks a period may date -- the same
`supports_actual_calendar_placement` rule the totals and the listing's `year`
filter apply -- and bucketed in the configured aggregation zone. A browser
generating "this year and the eleven before it" is a second authority on the
calendar, and it is wrong in both directions at once: it offers years the
archive has nothing for, and it hides the years of an archive nobody has added
to since 2019.

The response reports the unplaced count beside the years and the archive's total
track count beside both, because "nothing has been imported" and "nothing
matches this selection" are opposite instructions to whoever is reading.

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

## Track analysis

*Implemented as `gpx_view.domain.analysis`, `gpx_view.application.AnalyzeTrack`
and `gpx_view.application.InstalledAnalysis`.*

> Derived metrics are rebuildable and never source authority.

```
raw import  ->  normalized track  ->  analysis
 authority        authority          rebuildable
```

Analysis reads the canonical normalized geometry and nothing else. Its entry
point takes segments, so no parser, source application or exchange format is
reachable from it, and a future FIT adapter reaches the same numbers without the
analysis knowing it exists.

Nothing in the analysis path modifies what it read. Outlier detection produces an
analysis decision, never a deletion.

### Analysis versioning

> Analysis semantics require an explicit version bump.

`AnalysisProfile` names the distance, movement and elevation algorithms with
their versions, plus a metric schema version. Every stored result records it, so
a changed algorithm produces numbers that can be told apart from the old ones
rather than replacing them silently. An internal refactoring with identical
output is not a bump.

### What each metric means

| Metric | Definition |
| --- | --- |
| `distance_m` | horizontal geodesic distance between consecutive positions, summed **within** segments |
| `elevation_min_m`, `elevation_max_m` | extremes of the raw observations |
| `elevation_gain_m`, `elevation_loss_m` | ascent and descent of the filtered profile |
| `elapsed_duration_s` | first temporal observation to last, in traversal order |
| `moving_duration_s` | observed time going somewhere |
| `stopped_duration_s` | observed time not going anywhere |
| `unobserved_gap_duration_s` | time nothing was recorded for |
| `unattributed_duration_s` | elapsed time none of the other three could claim |
| `average_speed_mps` | distance over elapsed duration |
| `moving_average_speed_mps` | distance covered while moving, over moving duration |
| `maximum_speed_mps` | highest speed **sustained** across the analysis window, projected over HTTP as `maximum_sustained_speed_mps` |

Values are SI, and the unit is part of the name. Enforced invariants:

- A segment boundary is never bridged, and elevation never enters distance.
- Distance consults no timestamp, so planned geometry has a length.
- `moving + stopped + unobserved + unattributed == elapsed`. An equality: time
  that no rule can classify is *named* rather than dropped, because a
  decomposition that does not add up is how lost time hides.
- An interval with `dt <= 0` produces no speed and is excluded. Nothing takes an
  absolute value and nothing divides by zero.
- Movement is decided from the widest positional spread across the window, never
  from where the window ended up. A walked loop, an out-and-back and a
  switchback all return to themselves, and none of them is a pause.
- The spread is read at **two scales**. A still receiver's cloud is bounded --
  three minutes are no wider than thirty seconds -- while a walker's spread
  grows at the rate they advance, and that difference is the only thing that
  separates slow movement from noise. A single threshold could not, so it made
  the unstated claim that nothing below 0.5 m/s is movement. Steady progression
  well below that is movement now, bounded noise is still a stop, and a stretch
  neither scale can separate from noise is `unattributed` rather than decided.
  What the archive claims to detect is bounded and stated: net progress below
  `SLOW_MOVEMENT_SPEED_MPS`, and any path staying inside the receiver's own
  envelope, are admitted as undecidable.
- Ascent is measured to the turning point the profile reached, so
  `gain(reversed) == loss(original)` exactly. Direction of travel is not a
  property of the ground.
- Maximum speed is a *sustained* speed. A three-second sprint is averaged down,
  which is the price of a single bad fix not becoming a headline.

> Silence is not a rest.

A gap in a recording can be a break, a paused app, a flat battery or a lost fix,
and the data cannot tell them apart. That time is `unobserved`. A stop is claimed
only where positions kept arriving and showed no movement.

> Missing metrics are not zero metrics.

An underivable metric is absent from the stored set and `null` over HTTP. A
planned route has no moving time, and `0` would claim it was travelled and nobody
moved.

### Temporal vocabulary

Four words, and keeping them apart is what the last two ADRs were about:

| Term | Means |
| --- | --- |
| timeline time | the instants a track's own positions carry, whatever they are worth |
| activity calendar time | the claim that the activity happened in a given period |
| `observed` | the instants came from something that was measuring |
| `estimated` | the instants came from a route computation: a schedule, not a journey |
| `unknown` | nothing shows which. Permanent, and never a weaker `observed` |

Timeline time is always reported. Activity calendar time and actual activity
timing are *claims about* it, and each has its own gate.

### Temporal evidence

> Timestamp presence is not observed movement.

A planner writes instants onto a route it computed; a recorder writes instants
onto positions it measured. The files are structurally the same and so is the
arithmetic, so `moving_duration_s: 10296` reads as somebody's afternoon either
way. `TemporalEvidence` says which it is, and it is derived from evidence of
**measurement** -- never from the presence of a timestamp, the exporting
application, or the detected track kind:

| Evidence observed | Temporal evidence |
| --- | --- |
| receiver quality or a measured heading | `observed` |
| turn-by-turn navigation instructions | `estimated` |
| anything else, or no instants at all | `unknown` |

Measurement outranks planning: an application that records while it navigates
produces both, and it really was measuring. `unknown` is permanent and first
class -- it is not a weaker `observed` and never becomes one by default. It is
read from the *detected* evidence, so a user correcting a track's kind does not
make its synthetic clock real.

> Analysis derives everything; eligibility is decided separately.

Nothing is withheld. A planned route genuinely has a derived route speed, and
hiding it would be its own kind of lie. What the numbers may be *presented* as is
two independent gates:

```
actual metrics   effective kind == RECORDED
actual timing    effective kind == RECORDED  AND  temporal evidence == OBSERVED
```

A recording whose instants nothing vouches for still contributes its distance --
geometry needs no clock -- and contributes no moving time.

Over HTTP the two halves are reported apart, as `geometry` and `timed_path`, and
the timed half always carries its `basis`. There is no path through the response
that reaches a duration without passing the statement that qualifies it.

### Persisted analysis integrity

> Persisted derived state is validated before it is interpreted.

SQLite is the authority, which is why what comes out of it is untrusted input: a
row may come from a version that is gone, a failing disk, or a defect since
fixed. Every read fails closed.

| Surprise | Reaction |
| --- | --- |
| run row that cannot be interpreted | reads as absent |
| metric value that could not have been derived | condemns its whole run |
| quality flag this build cannot name | condemns its whole run |
| metric *name* this build does not know | skipped |

The last two differ deliberately. A value this build does not need says nothing
about the rows beside it; a caveat it cannot render is a caveat it would be
dropping silently.

Currency is decided from the same validation, so `analyze --outdated` repairs the
damage rather than reporting it forever. A damaged analysis reports as `invalid`
rather than as `missing`: "nothing derived yet" and "something is wrong with what
was" call for different reactions. Service health is unaffected -- one damaged
row is a data problem, not an unhealthy process.

### One availability answer, three read surfaces

> A stale number is not a headline.

A track is read three ways -- in a listing, through its own analysis resource,
and as part of a total -- and all three project one value:

```
current   the installed algorithms produced the metrics from the geometry a reader sees
outdated  metrics exist, from algorithms or geometry that have moved on
missing   nothing has ever been derived
invalid   something was derived and cannot be interpreted
```

`InstalledAnalysis.availability` decides it, once. Damage is checked before
currency: a run whose profile is *also* unreadable is damage rather than an old
algorithm, and "re-run the analysis" is a different instruction from "a row is
broken".

Consequences the listing had to be corrected for:

- A row carries a metric only while its analysis is `current`. In the other
  three states the metric is `null`, because a headline number is a claim about
  what the track *is*, not about what was once derived from it. The last thing
  derived stays readable through the track's own analysis resource, which says
  what it is.
- `longest_first` means longest *according to a current analysis*. A track with
  no current distance sorts last, in front of the track identity tie-breaker.
- Tracks can be filtered by availability state, and the filter is applied in the
  same statement that counts and pages, so `total` counts the filtered selection
  rather than the archive.

The repository is handed the *values* of the installed profile as query
parameters and compares them. It does not hold a second table of what is
installed: an algorithm change must not have to be made twice, because the
second place is the one nobody remembers.

### Analysis currency

Metrics are current only when a successful run applied the installed profile
**and** read the generation a reader currently sees. Never analysed, older
algorithms and newer geometry are all "outdated", answered by one authority.

A classification override outdates nothing -- it changes which totals a track
reaches, not how long it is. A failed reprocess outdates nothing either: the
generation a reader sees did not change.

Publication is atomic and conditional on the generation, so no reader sees a new
distance beside an old duration, and metrics derived from geometry nobody can see
never become current. Old runs are kept.

## Actual and planned aggregation

*Implemented as `gpx_view.application.GetYearStatistics` and
`GetMonthlyStatistics`.*

> Actual and planned aggregates must never be conflated, and `UNKNOWN` belongs to
> neither.

```
Recorded  10 km
Planned  100 km
Unknown   50 km

actual total = 10 km
```

Membership is decided by the **effective** track kind, read at query time, so a
user correction moves a track between the sets immediately with nothing
recalculated. There is no combined scope: a number that adds planned routes to
travelled distances is about neither.

### The activity date

> Activity date must not be inferred from import time.

```
import received_at    when the archive learned about a track  -> not an activity date
metadata export time  when the file was written               -> not an activity date
first trackpoint time                                         -> the timeline
```

A 2025 recording imported in 2026 is a 2025 activity.

> Timestamp presence is not calendar placement.

The first position's instant is the track's **timeline**, and a timeline becomes
an **activity calendar date** only when its instants were shown to have been
measured:

```
timeline time            the instants the track's own positions carry
activity calendar time   the claim that this happened in this period
```

`supports_actual_calendar_placement` decides it, from the temporal evidence and
from nothing else. A route planner writes plausible instants onto geometry
nobody travelled, and a recording stripped of its receiver metadata looks
identical from here; placing either in a month puts a real distance into a
period it has nothing to do with, and nothing about the resulting total looks
wrong.

It deliberately does **not** read the track kind. A user correcting a route to
`RECORDED` is saying what the track is, not that its clock was measured, and
letting the correction reach this answer would make an override a way to
manufacture a date. The correction still moves the track between the actual,
planned and unknown sets -- that is what it is for.

Tracks with no calendar placement are reported *beside* the year rather than
inside it, and split, because the two halves are different facts:

| Reported as | Means |
| --- | --- |
| `unplaced.without_date` | the track carries no instants at all |
| `unplaced.with_unverified_date` | it carries instants nothing showed to be measured |

The listing's `year` and `month` filters select the same set the totals do:
asking for a period is asking when something happened, so a track whose clock
nothing measured is not in it. Clicking a bar in a monthly chart therefore
reaches exactly the tracks that bar counted. The timeline itself is never
withheld -- every track reports `started_at`, `ended_at` and the `basis` that
qualifies them.

### The aggregation timezone

A month is a local month, so `GPX_VIEW_TIMEZONE` decides its boundaries and every
statistics response names the zone it used. The default is `UTC` rather than the
host zone, and an unknown zone fails at start-up rather than falling back.

An empty period totals zero, because there was nothing to total and the track
count says so. A period holding tracks but no value for a metric reports `null`
for that metric.

### Only current analyses are totalled

> Default statistics never mix analysis profile versions.

After a profile bump the archive holds two kinds of number: some produced by the
algorithms this build applies and some by the previous ones. Adding them produces
a figure that measures neither, and nothing about it looks wrong. Only tracks
whose analysis is current contribute, decided by the same `InstalledAnalysis`
that `analyze --outdated` selects with -- one authority, so what a total leaves
out and what a batch run picks up cannot drift apart. There is no `allow_mixed`
option; offering one would make it the number people quote.

Refusing to answer would be worse than mixing, so the total is of the current
ones and the response says how much of the period it covers:

| Field | Meaning |
| --- | --- |
| `track_count` | tracks the period and scope hold |
| `analysed_track_count` | how many of them contributed, having a `current` analysis |
| `tracks_without_analysis` | `missing` -- nothing has ever been derived |
| `tracks_with_outdated_analysis` | `outdated` -- analysed by algorithms or geometry that moved on |
| `tracks_with_invalid_analysis` | `invalid` -- a stored analysis that cannot be interpreted |
| `tracks_with_failed_analysis` | newest attempt failed, whatever it left behind |
| `tracks_without_observed_timing` | contributed a distance but no time |

The first four are the four availability states, so they partition the period
exactly:

```
analysed + missing + outdated + invalid == track_count
```

They stay separate because they need different actions: one is a track waiting
to be analysed, one is a re-run, one is damage. `tracks_with_failed_analysis` is
orthogonal and overlaps all four rather than replacing any of them -- a track
can hold current metrics from an earlier run and a failed newest attempt at
once, so it is deliberately outside the equality above.

Durations are summed only over tracks whose instants were shown to be measured,
which is the temporal-evidence gate applied to an aggregate.

## Offline map packages

*Implemented as `gpx_view.domain.maps`, `gpx_view.application.maps` and
`gpx_view.infrastructure.maps`.*

> A map package is a replaceable external dataset, not source evidence.

A raw import is the only copy of somebody's afternoon: immutable, never
overwritten, and a corrupt artifact preserved because it is evidence of a
problem. A map package came from a public server that still has it. Corruption
there is a reason to discard and reinstall.

What carries over is the integrity rule, because that one is about honesty:

```
database row + managed file that hashes to what the row says   ->  INSTALLED
either one on its own                                          ->  INVALID
```

Enforced invariants:

- **A public tile service is never an offline-download authority.** Community
  tile servers exist on donated bandwidth and their usage policies forbid
  exactly the bulk download an offline archive would need.
- **Normal map viewing makes no external request.** Reading a track reaches the
  catalog, the provider and the network not at all. The provider is contacted
  during three actions a person pressed: refresh, install, update.
- **A caller supplies a region, never an address.** The provider adapter
  resolves the URL, redirects are followed only to hosts that adapter declares,
  and the scheme is `https`. Anything else would be a server-side request
  forgery primitive wearing a feature's clothes.
- **A remote package is untrusted input.** It is streamed under a hard byte
  ceiling, hashed as it arrives, checked against the length the provider
  declared, and validated as a container, a metadata set and a tile vocabulary
  before it is used. A downloaded SQLite file is opened read-only, with no
  extensions and no statement built from its contents.
- **Nothing a provider sends becomes a path.** A region identity is validated
  to segments of `[a-z0-9-]`, and the managed directory is a digest of it
  rather than a transformation of it.
- **Installation is atomic and a failed update preserves the previous
  package.** The new package is fetched and validated beside the installed one,
  the old file is deleted only after the new row has committed, and the
  free-space preflight asks for room for both. A byte-identical re-download is
  `ALREADY_CURRENT` and switches nothing.
- **Attribution is package metadata and survives every projection.** Data
  owner, provider, licence and the required line are read out of the package
  and rendered by the map, the manager and the credits page. A package that
  states no author and no licence is refused rather than installed under an
  assumption. Credit links are structured `(label, https url)` pairs, never
  markup.
- **A tile is addressed by content.** The delivery identity is the package's
  SHA-256, matched against the database rather than used as a path, which makes
  a tile URL immutable and an update a different address.
- **Coverage is decided by geometry *and* hierarchy.** A package supersedes
  another only when the provider's own region tree says one is inside the
  other; two neighbouring countries whose rectangles overlap at a border are
  both kept, and a parent whose child does not cover the whole view wins.
- **Removing a map touches nothing else.** No track, no title, no correction,
  no statistic.

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

## Export: the original and a generated copy are different things

*Implemented as `gpx_view.application.export`. See
`docs/adr/0012-export-archive-and-restore.md`.*

> An exported raw source is byte-identical to what was imported. An exported
> exchange document is not the file that arrived, and is never presented as one.

```
export raw     the bytes that arrived               never changes
export track   GPX 1.1 from the current generation  changes when the rules do
```

A contract test asserts the two never produce identical bytes. If they ever did,
one of them would be lying about what it is, and it would be whichever somebody
had chosen for their backups.

An exchange document carries no classification. `RECORDED` and `PLANNED` are
verdicts reached from this project's evidence rules, at a confidence, with a
classifier version, and no exchange format has a field that means that -- a
private extension would publish a claim no reader could evaluate. It invents
nothing either: no elevation where none was measured, no clock on a planned
route, and `UNKNOWN` written as nothing rather than as the word.

Measurements survive. Heart rate and cadence go back out through the same
namespaced vocabulary they were read from, so an export loses no reading and
introduces no schema.

## The archive format

*Implemented as `gpx_view.application.archive`.*

> An archive is either complete or explicit about what it is missing.

Every archive carries a manifest stating `format_name`, `format_version`,
`created_at`, `gpx_view_version`, `schema_version`, a size and digest for every
member, counts of what it holds, and **what it deliberately omits**. Installed
map packages are omitted -- public data that can be fetched again -- and
`ArchiveOmission` records that rather than leaving a silence. "Complete" and
"complete except for the part nobody mentioned" are different promises about a
backup.

The database is captured through SQLite's own online backup. Copying the file
would be the failure this contract exists to prevent: with write-ahead logging
on, the `.sqlite3` file alone is an incomplete database, and a copy taken during
a write can produce one that will not open.

`format_version` and `schema_version` are separate and move for separate
reasons: the container changing and a table changing are different events.

### A backup that returned successfully is on the disk

*Implemented as `gpx_view.infrastructure.archive.container.FilesystemArchiveBuilder`.*

> A backup command that returns has written a backup, not scheduled one.

```
write → flush → fsync the archive → atomic rename → fsync the parent directory
```

The same sequence, in the same order, that the managed raw storage makes for an
imported original. The last step is the one that is easy to leave off and
impossible to notice afterwards: flushing the archive makes its *bytes* durable,
but the rename that gives those bytes the archive's own name is an entry in the
destination's directory, and an entry still in the page cache when the power
goes leaves a complete backup that nothing points at. The moment somebody needs
a backup is disproportionately often the moment after something went wrong with
the machine.

The directory flush is the *same helper* the restore publication uses. Both end
a multi-step write with a rename and are only as durable as the directory entry
it produced; a second implementation would be the one that quietly stopped at
the rename.

A failure to flush fails the backup, and the finished file is removed again even
though it is complete and already carries the archive's name. A backup the
command told somebody it could not write is the one they find later and trust.

### The manifest describes the container; the database says what it holds

> The manifest is an integrity description, never a second business authority.

Two different jobs, and keeping them apart is what stops a restore from ever
having to pick a winner between them.

**What the manifest is checked against is itself.** A member name may be stated
only once, no source may claim the database member's name, and the manifest is
not one of the members it describes. All three are self-contradictions rather
than disagreements with anything else: every use of the member list keys it by
name -- the expected checksums, the completeness check, the path a member is
written to -- so a repeated name collapses to one entry wherever it is *checked*
and stays two wherever it is *counted*. The result would be an archive that
verifies perfectly while describing more sources than it can be carrying, which
is exactly the quiet incompleteness the manifest exists to make impossible.

**`counts` is deliberately not reconciled with the member list.** It answers
"what does this archive hold" in an operator's terms -- tracks, corrections,
titles -- and only the captured database can answer that; one raw import row is
one source whatever the container looks like. Cross-checking the two would make
the manifest a second authority for numbers the database owns, and the coupling
would have to be paid for at every future schema change. What the container is
not allowed to do is carry a member it did not describe, or describe one twice;
both of those are statements about the container alone, and both are enforced.

### The captured database decides what belongs in the archive

> A backup lists its sources from the snapshot it took, never from the disk it
> is standing on.

For every raw import the captured snapshot names, three things must hold:

```
the database row exists   +   the managed original exists   +   its bytes hash to it
```

Any one of them missing fails the backup. `archive_source_incomplete` names the
first two and `archive_checksum_mismatch` the third, and neither leaves a file
behind that could be mistaken for a backup.

Listing the storage directory instead would answer a different question, and
answer it about a deployment that is still running. A missing original simply
would not appear, and the result is the failure this rule exists for: a
well-formed archive whose manifest counts three sources and whose container
carries two, with nothing detectably wrong about it until somebody restores it
and goes looking for the third recording.

**Stored originals that no row accounts for are not exported.** There is nothing
to restore them *as* -- no import instant, no original filename, no
classification -- and inventing one would make a backup the place where
unattributed bytes acquire a provenance they never had. They stay where they
are, and the manifest states how many were passed over. What is refused is the
silence, not the file.

The manifest also states how many tracks arrived with metrics attached. That is
a fact about the archive and deliberately not a currency verdict: whether an
analysis is `current` is decided by the algorithms the *reading* build has
installed, and that decision has one owner elsewhere.

## Restore validates before it publishes

*Implemented as `gpx_view.application.archive.RestoreArchive`.*

```
manifest → format version → schema compatibility → checksums → database integrity → publish
```

Nothing in the destination is touched until every step before `publish` has
passed, which is the property that makes attempting a restore safe.

| Archive schema | Answer |
| --- | --- |
| equal to this build's | `SUPPORTED` |
| older | `MIGRATION_REQUIRED` -- restores, then migrates at the next start |
| newer | `UNSUPPORTED` -- the same refusal the database itself makes |

- **Existing data is never silently replaced.** "Holds data" means data rather
  than files: an empty database the server created on start-up is not something
  to protect, and treating it as such would make `--replace` the ordinary
  recovery path. A database this build cannot account for *does* count.
- **A restore replaces what the archive carries, not the data directory.** In a
  container that directory is a mount point, and a mount point cannot be
  renamed. Installed maps survive, and the journal files move with the database
  they belong to -- a stale write-ahead log describes a different database and
  SQLite would apply it.
- **Archive members are refused rather than sanitised.** A member is extracted
  only when the manifest declares it *and* its name survives an allow-list
  check, and it is written to a path built from validated components. Links,
  device nodes and anything that is not a regular file are refused.
- **A backup is verified by the code that would restore it.** A bespoke check
  would be a second opinion, and the day the two disagreed the backup would
  already be the thing at stake.

### Backing up is a read; restoring is exclusive

> A backup may run while the archive is being used. A restore may not.

They are asymmetric because what they do is asymmetric, and stating the rule is
better than leaving each caller to discover it.

**A backup runs alongside everything.** The database is captured through
SQLite's own online backup, which is built for a live file. Managed originals
are immutable, so hashing one cannot race a writer. And the source list comes
from the snapshot, so an import that commits after it is simply not in that
backup — which is what a snapshot means.

What makes that safe is one ordering, in the import: **the managed original is
written before the row that names it.** A snapshot taken between the two halves
sees neither, never a row whose bytes are missing — and a row whose bytes are
missing is exactly what fails a backup. The bytes on disk are then counted as
unreferenced and stated in the manifest, which is the honest description of a
file that arrived a moment ago.

**A restore requires the archive to itself.** It replaces the database and the
managed storage, and an import running at the same time would write into
storage that is being moved out from under it. There is no lock enforcing this:
the supported operating procedure is to stop the container, restore, and start
it again, which is what the documented recovery steps say.

### A restore either completed, or it did not happen

*Implemented as `gpx_view.infrastructure.archive.publication`.*

Publishing is several renames, and several renames are not one atomic act.
Between them the deployment holds a database from the archive beside a raw
storage from somewhere else, which is a state nothing can read correctly.

```
write marker → displace → publish database → publish storage → clean → clear marker
```

The marker is written and flushed *before* anything moves and cleared only after
everything has, so its presence is exactly the statement "a publication started
and did not finish". It records its directories by name relative to the data
directory: a container's data directory is a mount point whose absolute path
depends on how the container was started, and a marker of absolute paths would
stop resolving precisely when somebody moved a volume.

An unfinished publication is **undone**, never finished on the deployment's
behalf. Somebody whose restore reported an error has to be able to believe their
archive is the one they started with; a command that reports a failure and
replaced the data anyway is worse than one that simply fails. Rolling back is
available for as long as the displaced directory is intact, which is until the
moment both moves have succeeded — that is what makes the two-outcome promise
keepable rather than aspirational.

- **The same code resolves both interruptions.** An exception is handled
  in-process by `abandon`; a killed container is handled at the next start by
  the composition root, before the schema is even looked at. Two implementations
  of "what to do about an unfinished publication" would eventually disagree, and
  the day they did somebody would already be restoring from a backup.
- **`abandon` after publication has begun is not a cleanup.** The displaced
  directory then holds the *only* copy of the previous database and raw storage,
  and discarding it as staging debris would turn a failed restore into total
  data loss.
- **An unreadable marker moves nothing.** Recovery moves and deletes
  directories; a marker it cannot interpret names none, so it reports the fault
  and leaves both copies where they are rather than choosing between them.
- **`doctor` reports a pending restore as an error and does not clear it.** Not
  because data is being lost, but because nothing else on the report can be
  believed while the database and the storage may belong to different archives.

### A restored map row is not a restored map

The archive carries a package's metadata and not its bytes, so restoring onto a
machine that does not have the package produces a row without a file. That state
already has one honest answer, and no rule about restores was needed to get it:
an installation is a row **and** a file that hashes to it, so the entry reports
`INVALID` rather than offering a map that would answer every tile with nothing.

| Target holds | After the restore |
| --- | --- |
| the same package | `INSTALLED` -- the row names that exact file, nothing is downloaded |
| no package | `INVALID` -- reported by `doctor`, reinstalled by a person |
| a *different* package | its file is now claimed by no row: cleared at the next start |

The third row is a deletion, and it is the documented one: a map package is
replaceable public data, and the installation it belonged to was part of the
database the restore replaced. It happens through the ordinary start-up recovery
rather than during the restore, so `doctor` can report it first.

Attribution survives either way, because it is package metadata and the metadata
is exactly what the archive does carry. A manager entry whose bytes are gone can
still say what it was and who to credit.

## Diagnostics change nothing

*Implemented as `gpx_view.application.diagnostics`.*

> Running `doctor` must never be the thing that changes the answer.

No import, no migration, no repair, no network request. The schema version is
read through a read-only connection, because opening a missing database the
ordinary way creates it -- and the fresh deployment somebody asked about would
no longer be fresh.

Three severities, because most of what goes wrong is degraded rather than
broken: a pending migration, a missing backup and an unmounted import folder all
need somebody, and none of them needs them tonight. Every detail line is
structural -- no coordinate, no track title, no personal filename.

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
persistence_failed        track_not_found          analysis_failed
```

`analysis_failed` is the one code that leaves its subject intact: the track keeps
its geometry and whatever metrics an earlier run produced, and the next
`analyze --outdated` retries it. A metric that is simply unavailable is **not**
an error -- a planned route having no moving time is a normal state, reported as
absence rather than as a failure.

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

Which timezone instants are bucketed in -- and therefore which month a
late-evening activity counts towards -- is `GPX_VIEW_TIMEZONE`. Instants stay
stored in UTC; the setting decides boundaries and nothing else, and every
statistics response names the zone it used.
