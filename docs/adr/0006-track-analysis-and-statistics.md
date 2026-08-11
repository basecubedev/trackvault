# ADR 0006: Track analysis and statistics

## Status

Accepted (2026-08-09). **Decisions 6 and 7, and the last known limitation, are
superseded by [ADR 0007](0007-temporal-evidence-and-analysis-integrity.md)**,
which corrects the movement and elevation algorithms and stops statistics mixing
analysis profile versions. Everything else here still holds. The algorithms
described below are `windowed-displacement` version 1 and `median-deadband`
version 1, and every result they produced still names them -- which is the point
of versioning them in the first place.

Extends ADR 0002 and ADR 0005, both of which stay valid. ADR 0002 established
the source-agnostic normalized track and deferred the analysis algorithms; ADR
0005 made "was this produced by what is installed today?" answerable for
processing. This applies the same question to derived metrics, and turns them
into month and year statistics.

## Context

The archive could say what it held and prove where every byte came from. It
could not say how far anybody had walked.

Everything needed for that was in place -- normalized geometry, an effective
track kind, a metric provenance vocabulary -- and none of it was used, because
the calculations themselves had deliberately not been written. Writing them
raises four problems that are easy to get wrong quietly.

**The obvious calculations are wrong.** Elevation gain as the sum of positive
differences grows with the sampling rate rather than with the terrain: a flat
hour of walking accumulates hundreds of imaginary metres, and the same walk
logged twice as often "climbs" twice as much. Moving time from a per-point speed
threshold counts a stationary receiver's noise as movement, because a device
standing still reports positions three to five metres apart every second.
Maximum speed from the fastest interval is decided by the worst GPS fix in the
file.

**Numbers that change have to say why.** An elevation filter or a movement rule
*will* change. If the stored output does not record what produced it, the day
after the change nobody can tell which of two numbers came from which rule, and
"my ascent dropped by 200 m" has no answer.

**Silence is not a rest.** A twenty-minute gap in a recording can be a break, a
paused app, a flat battery or a lost fix. They produce identical data. Calling
it a pause is a claim the data does not support.

**A month is not a fact about an instant.** 23:30 UTC on 31 January is already
February in Berlin. Which month an evening activity counts towards depends on a
configured zone, and a system that never states which zone it used produces
monthly figures nobody can check.

## Decision

### 1. Analysis reads normalized geometry and nothing else

```python
analyze_track(segments: Sequence[TrackSegment]) -> TrackAnalysis
```

That signature is the source-agnostic guarantee, made structural rather than
promised: no parser, source application or exchange format is reachable from a
tuple of segments. A future FIT adapter that produces the same segments reaches
the same numbers without this code being told it exists.

Analysis lives in `trackvault.domain.analysis`, so the domain's stdlib-only and
format-free rules apply to it automatically.
`tests/contract/test_architecture_contract.py` checks that the package imports
no format module and no outer layer, and that no route imports a calculation.

### 2. Analysis is derived state, never source authority

```
raw import  ->  normalized track  ->  analysis
   authority        authority         rebuildable
```

Metrics can always be thrown away and derived again. Nothing in the analysis
path writes back into the geometry it read -- in particular, outlier detection
produces an analysis *decision* and never deletes a position, so a better
algorithm can revisit exactly the same data later.

### 3. `AnalysisProfile` names every algorithm and version

The same shape as `ProcessingProfile`, for the same reason. A stored result
records the distance, movement and elevation algorithms with their versions plus
a metric schema version, and one function compares that with what the build
installs. Changing an algorithm means bumping its version; an internal
refactoring with identical output does not.

### 4. Distance is horizontal, segment-aware and timestamp-free

> The sum of the horizontal geodesic distances between consecutive positions
> **within** each segment.

Segment boundaries are never bridged: a recording that was paused and resumed
did not travel between its segments. Elevation never enters the value -- a 3D
path length is a different metric and would make an alpine walk incomparable
with a flat one for no stated reason. Timestamps are not consulted, so a planned
route has a length like anything else.

The formula is haversine on a sphere of the IUGG mean earth radius, chosen for
what it does not need: no dependency, no I/O, and no loss of significance at the
short distances between consecutive positions. It costs up to roughly 0.3 %
against the WGS-84 ellipsoid, systematically rather than randomly. That is
acceptable for activity statistics and it is not *silently* acceptable: an
ellipsoidal formula would be version 2, and every stored distance says which
version produced it.

### 5. Time is four statements, not one

```
elapsed      first temporal observation -> last
moving       observed, and going somewhere
stopped      observed, and not
unobserved   nothing was recorded at all
```

`moving + stopped + unobserved <= elapsed`, with the inequality accounting for
positions that carry no instant: that time is real and unattributable, and
inventing an attribution for it would be the same mistake in a smaller place.

A stop is claimed only where positions kept arriving and showed no movement.
Silence is `unobserved`, because a paused recording, a flat battery and a rest
are indistinguishable in the data. The threshold between a sampling interval and
a gap is scaled to the track's own median interval, with a floor: a fixed
threshold would call a deliberately five-minute-sampled track one long gap, and
would miss a thirty-second dropout in one-second data.

The activity's start is the first instant its own positions carry, in source
traversal order rather than `min()` -- a track whose timestamps jump around has
a defect, and taking the extremes would hide it behind a plausible-looking span.
An interval with `dt <= 0` produces no speed and is excluded; nothing takes an
absolute value or divides by zero.

### 6. Movement is decided from net displacement over a window

A stationary receiver's positions wander but end up where they started, so the
net displacement across a thirty-second window stays a few metres while a
walker's is forty. Each interval is classified by the speed implied by the net
displacement across a window centred on it, against one threshold of 0.5 m/s.

One threshold, for every activity. A per-activity table would be a hidden second
authority on what a track *is*, which is the classification's job; the generic
rule handles walking and cycling without knowing which it has.

The same window is what makes maximum speed survivable: an isolated bad fix that
jumps a kilometre and returns nets out to nothing. An interval whose own speed
exceeds a ceiling scaled to the track's median interval speed is excluded
outright, which rejects a teleport in a walk without rejecting a fast descent on
a bicycle, and an excluded interval ends the window stretch so the bad fix never
enters its neighbours' windows either.

Maximum speed is therefore the highest speed *sustained across the analysis
window*. That is a deliberate trade: a genuine three-second sprint is averaged
down. Reporting the fastest single interval instead would mean reporting the
worst GPS fix in the file.

### 7. Elevation extremes are raw, ascent is filtered

| Metric | Read from |
| --- | --- |
| minimum, maximum | the raw observations |
| ascent, descent | the filtered profile |

The extremes are single observations, and a filter could only move them away
from what the source recorded. Ascent is a sum over the whole track, so every
sample's error is added into it -- which is exactly the quantity a filter has to
protect.

Two filters, for two different defects: a rolling median over five samples
removes isolated impossible spikes, and a five-metre deadband removes the drift
the median leaves behind. The deadband's cost is stated rather than hidden:
undulation whose amplitude stays inside the band is not counted, because at that
amplitude it cannot be told apart from receiver noise. Each segment accumulates
on its own, so a recording that resumed higher up did not climb in between.

No terrain model is consulted. Correcting elevation against a digital elevation
model is a network dependency and a separate feature; this analyses the data the
track carries.

### 8. Missing is never zero

An underivable metric is absent from the stored set and `null` in the API. A
planned route has no moving time, and reporting `0` would claim it was travelled
and nobody moved. The three durations, the three speeds and the four elevation
values are each present only when the data supported them, and a small set of
quality flags says what was wrong when something is absent.

### 9. Analysis is bound to its generation as well as its profile

Stored metrics are current only when a successful run applied the installed
algorithms **and** read the geometry a reader currently sees. Three ways to fall
behind, one authority to answer all of them:

```
never analysed        -> outdated
older algorithms      -> outdated
newer geometry        -> outdated
```

A classification override deliberately does **not** outdate anything: it changes
which totals a track reaches, not how long it is. A failed reprocess does not
either -- the generation a reader sees did not change, so its metrics did not.

Publication is one transaction, conditional on the generation: a reprocess that
commits while an analysis is running has already moved the track on, the update
matches nothing, and metrics describing geometry nobody can see never become
current. The run is still recorded. That is also the concurrency contract, and
it needs no lock.

### 10. Analysis runs automatically, best effort

A successful import or reprocess derives the metrics of what it produced, so a
scanned sync folder yields statistics without a second command. The step cannot
fail the thing it follows: losing metrics costs one `analyze --outdated`, while
letting an analysis defect abort an import would lose source evidence, which is
the one thing the archive cannot reconstruct. Analysis is therefore its own
lifecycle with its own status, and "track available, analysis absent" is a normal
simultaneous state.

### 11. Actual, planned and unknown are three sets

Selected by the **effective** kind, at query time:

```
Recorded  10 km
Planned  100 km
Unknown   50 km

actual total = 10 km
```

There is no combined scope. A number that adds routes somebody planned to
distances somebody travelled is about neither, and offering it as the default
would make it the one people quote. Because the effective kind is read live, a
user correction moves a track between the sets immediately, with nothing
recalculated.

### 12. Buckets are local, and every response says so

`TRACKVAULT_TIMEZONE` is an IANA zone, defaulting to `UTC`. Not the host zone: a
container inherits whatever its image carries, so a local-time default would
make the same archive report different monthly totals on two machines. UTC is
wrong for most people and *visibly* wrong, which is what gets it configured. An
unknown zone fails at start-up rather than falling back silently.

The year window is computed in that zone and the archive is queried in UTC;
bucketing happens in Python. An offset is not a constant, and doing that
arithmetic in SQL with a fixed one is how a daylight-saving transition moves a
track into the wrong month.

A track's period comes from its own positions. An import instant is when the
archive learned about a track, which is not a fact about the activity, and an
export timestamp is when a file was written. A recorded track with no timestamps
at all has a length and belongs to no month; it is reported beside the year
rather than assigned to 1970 or to the import date.

### 13. Metrics are typed rows, and no aggregate reads a position

`analysis_runs` carries the profile and the generation; `track_metrics` holds one
typed row per derived value with its unit and provenance. Not a JSON blob:
summing a month would become a scan and a parse, and a value whose unit nothing
declares is a value whose meaning drifts.

A year is one indexed range over `tracks.started_at` plus one batched metric
lookup. `tests/integration/test_statistics_performance.py` asserts that neither
a yearly total nor a track listing touches `track_points`, and that neither
issues a query per track.

## Consequences

- Every stored number can name the algorithm version that produced it, so an
  algorithm change is explainable rather than mysterious. It also means an
  algorithm change makes every stored result outdated, and `analyze --outdated`
  is the migration.
- Distances carry a systematic spherical-approximation error of up to ~0.3 %.
  Stated, versioned, and improvable without reinterpreting old data.
- Elevation gain under-reports terrain whose undulation stays within five
  metres. That is the price of not over-reporting receiver noise, and the two
  cannot be separated at that amplitude.
- Maximum speed under-reports short bursts. It is a sustained speed, named as
  such.
- Moving time depends on a 0.5 m/s threshold and a 30-second window. Both are in
  the profile, so a future change is a version bump rather than a silent shift.
- A track's month depends on configuration. Changing `TRACKVAULT_TIMEZONE` changes
  monthly figures without any data changing, which is why every response names
  the zone it used.
- Analysis at import time makes an import slower by roughly the cost of one pass
  over the geometry it just parsed.

## Known limitations

- No 3D path length, no grade, no per-segment or per-kilometre splits.
- No personal records, streaks or lifetime totals. The aggregation base makes
  them cheap to add; none is implemented.
- No planned duration as an estimated metric. A planner's synthetic timestamps
  are analysed like any others, and the separation from actual activity is made
  by the effective kind rather than by withholding the numbers.
- No sensor metrics -- heart rate, cadence, power, temperature. Nothing in the
  model carries them yet.
- No digital elevation model correction.
- No week-based or custom periods, and no timezone override per request.
- Statistics mix analysis profile versions if some tracks were analysed under an
  older one. Every total reports how many of its tracks contributed nothing, and
  `analyze --outdated` is the fix; nothing refuses to answer in the meantime.

## Alternatives considered

**Compute statistics on the fly from the geometry.** Correct, always current,
and unusable: a yearly total would load every position in the archive. Persisted
per-track metrics are a cache, and ADR 0005's currency machinery is what makes a
cache safe.

**One analysis version number instead of per-algorithm versions.** Simpler to
compare and much less informative. Naming which algorithm moved is what makes an
outdated verdict actionable, and it is the same argument that split
`ProcessingProfile` into its components.

**Activity-specific movement thresholds.** Rejected for V1. A per-activity table
would decide what a track is inside the analysis layer, and the activity is
often `unknown` anyway. The generic windowed rule handles walking and cycling
today; a tuned table needs evidence that it beats it, and that evidence does not
exist yet.

**A fixed maximum plausible speed as the outlier rule.** Rejected as the primary
mechanism. A cap high enough for a motorcycle does nothing for a walk, and a cap
low enough for a walk breaks every other activity. Scaling to the track's own
median is activity-agnostic by construction; an absolute ceiling remains only as
a backstop.

**Storing a month or year column per track.** Rejected. It would freeze the
aggregation timezone into the data, and changing the setting would then require
rewriting every row instead of asking a different question.

**A combined "all tracks" statistics scope.** Rejected. See decision 11.
