# ADR 0007: Temporal evidence, algorithm corrections and analysis integrity

## Status

Accepted (2026-08-09)

Extends ADR 0006 and **supersedes its decisions 6 and 7** (movement from net
displacement, ascent from a moving deadband reference) and the last of its known
limitations (statistics mixing analysis profile versions). ADR 0006 stays as the
record of what was decided and why; the algorithms it describes are `version 1`
of `windowed-displacement` and `median-deadband`, and every result they produced
still names them.

## Context

ADR 0006 built the analysis layer and made every stored number say which
algorithms produced it. Auditing that layer against adversarial fixtures and the
two real reference tracks turned up four defects, three of them silent.

**A path that bends read as standing still.** Movement was decided from the net
displacement across a thirty-second window. That is zero for every path that
returns to itself, so a walked park loop, an out-and-back and a switchback climb
reported minutes of standing still while the walker never stopped. The rule was
right about the case it was designed for -- a receiver wobbling on a table --
and wrong about a large fraction of real walking.

**Ascent depended on the direction of travel.** The deadband moved its reference
to whichever sample first left the band. Where that sample falls depends on
which way the track is walked, so the same ground reported ascents 13 % apart in
the two directions, and a there-and-back route did not gain what it lost.

**Timestamps were taken as evidence of movement.** The planned reference file
carries 391 positions with plausible timestamps and no measurement metadata at
all. The analysis derived 2.86 hours of "moving time" and a maximum speed from
them, and the API served those numbers in a flat object beside a recording's.
Nothing was wrong with the arithmetic; everything was wrong with the label. ADR
0006 had left this to the effective kind, which is a different question -- the
kind says what a track *is*, not what its clock is worth.

**Persisted derived state was trusted.** A stored analysis run was rebuilt
without checking it. A profile version of `0`, an infinite distance or a
negative duration reached callers as a raw `ValueError` or, worse, as a number
in somebody's yearly total.

Two further gaps were structural rather than defective: statistics summed
whatever metrics a track carried, so a profile bump silently mixed two algorithm
generations, and the track listing returned the whole archive.

## Decision

### 1. Movement is decided from the window's positional extent

Not from where the window ended up relative to where it started, but from the
widest straight-line spread its positions cover:

```
straight walk    spread == distance travelled     (unchanged)
park circuit     spread == the loop's diameter    (was zero)
out and back     spread == the turning point      (was zero)
receiver noise   spread == a few metres           (unchanged)
```

The ordinary case keeps the ordinary answer -- for a straight stretch the extent
*is* the distance travelled -- while the bending cases stop being wrong. Noise
rejection is untouched, because a stationary cloud is a few metres across
however long you watch it.

The extent is measured as the widest spread along four evenly spaced compass
axes, which is a lower bound on the true spread and within 8 % of it. A lower
bound is the right direction to err in: a maximum speed built on it can be
slightly conservative and can never be inflated.

The cost is stated rather than hidden. Movement confined to a circle smaller
than the window's reach is reported at the speed its *extent* implies, not its
path length. Somebody running tight laps of a small circle is moving, and this
says so, but it will not say how fast.

New profile component: `windowed-extent` version 1.

### 2. Every second of the elapsed span is attributed

```
moving + stopped + unobserved + unattributed == elapsed
```

An equality, not the inequality ADR 0006 settled for. Time that no rule can
classify -- either side of a clock that jumped backwards, an interval too fast
to be believed, a hole between positions carrying no instant -- is named
`unattributed` rather than dropped. Previously a track whose clock ran backwards
could report more moving time than it had elapsed time at all.

`unattributed_duration_s` is therefore a new metric, and the metric schema is
version 2: a version 1 set cannot say how much of its span went unclassified, so
its durations cannot be checked against each other.

A track with no derivable elapsed span now reports no durations rather than
zeroes. `0` would say it was observed and nobody moved.

### 3. Ascent is measured to the turning point

The filter confirms a climb once the profile has turned back out of the deadband
and accumulates the whole swing *up to the peak it reached*, rather than up to
whichever sample triggered the confirmation. Reversing a profile reaches the same
turning points in the opposite order, so

```
gain(reversed) == loss(original)
```

holds exactly rather than approximately. A closed loop balances, and a long ramp
no longer loses a deadband at each end: a hundred-metre hill reports as a
hundred metres instead of eighty-nine to ninety-six, whatever its sampling rate.

Noise rejection and spike rejection are unchanged -- the rolling median in front
of it does that work, and a flat walk under several metres of receiver wander
still climbs nothing.

New profile component: `median-hysteresis` version 1.

Known limitation, deliberately kept: terrain whose peak-to-peak swing exceeds
the five-metre band is counted in full even when it is fast. A perfect
sample-to-sample alternation of ±4 m survives the median (a period-two
alternation is median-invariant) and accumulates. Real receiver noise is not
periodic and is crushed by the median; a synthetic square wave is not a shape a
receiver produces.

### 4. Temporal evidence is separate from metric provenance

`MetricProvenance` says how a number was *produced*. Both of these are `DERIVED`:

```
speed from measured fixes      derived, from instants somebody observed
speed from a planner's route   derived, from instants nobody observed
```

Calling them both `DERIVED` and stopping there loses the only distinction that
matters. `TemporalEvidence` supplies the other half -- `OBSERVED`, `ESTIMATED`,
`UNKNOWN` -- and it is derived from evidence of *measurement*, never from the
presence of a timestamp, the exporting application or the detected kind:

```
receiver quality or a measured heading  ->  OBSERVED
turn-by-turn navigation instructions    ->  ESTIMATED
anything else                           ->  UNKNOWN
```

A device that reports how well it measured was measuring. A document carrying
turn-by-turn instructions had a route computed for it. Everything else fails to
`UNKNOWN`, permanently: `UNKNOWN` is not a weaker `OBSERVED` and never becomes
one by default. Measurement outranks planning, because an application that
records while it navigates produces both and really was measuring.

It is read from the *detected* evidence, so a user correcting a track's kind
moves it between the actual and planned sets without making its synthetic clock
real. It is not stored beside the metrics either: a classifier that learns to
recognise a planner updates the answer with nothing re-derived.

### 5. Analysis derives everything; eligibility is decided separately

Nothing is withheld. A planned route genuinely has a derived route speed, and
hiding it would be its own kind of lie. Two independent gates decide what those
numbers may be *presented* as:

```
actual metrics   effective kind == RECORDED
actual timing    effective kind == RECORDED  AND  evidence == OBSERVED
```

So a recording whose instants nothing vouches for still contributes its distance
-- that came from geometry, which needs no clock -- and contributes no moving
time, because nothing shows anybody moved.

### 6. The API reports geometry and timed path apart

```json
{
  "geometry":   {"distance_m": 9270.7, "elevation_gain_m": 508.4},
  "timed_path": {"basis": "unknown", "is_actual_activity_timing": false,
                 "moving_duration_s": 10251.4}
}
```

Structural rather than documentary: there is no path through the response that
reaches a duration without passing the statement that qualifies it. The listing
carries the same two fields, because a list view is exactly where the
qualification gets lost.

The maximum speed is projected as `maximum_sustained_speed_mps`. It is a speed
held across the analysis window, never an instantaneous peak, and the old name
invited exactly the comparison it cannot support. The stored metric key stays
`maximum_speed_mps`: it is the data contract, and renaming it would invalidate
every stored row to fix a presentation problem.

### 7. Default statistics never mix analysis profile versions

Only tracks whose analysis is current contribute to a total, decided by the same
`InstalledAnalysis` that `analyze --outdated` selects with. There is no
`allow_mixed` option: a total that added two algorithm generations would measure
neither, and offering it would make it the one people quote.

Refusing to answer would be worse than mixing, so the total is of the current
ones and the response says so:

```
track_count                    10
analysed_track_count            8
tracks_without_analysis         1
tracks_with_outdated_analysis   1
tracks_with_failed_analysis     0
tracks_without_observed_timing  0
```

The three shortfalls stay separate because they need different actions, and the
failed count overlaps the others rather than replacing them -- a track can hold
current metrics from an earlier run and a failed newest attempt at once.

### 8. Persisted derived state is validated before it is interpreted

SQLite is the authority, which is exactly why what comes out of it is untrusted
input: a row can come from a version that is gone, a failing disk, or a defect
since fixed. Every read fails closed.

| Surprise | Reaction |
| --- | --- |
| run row that cannot be interpreted | reads as absent |
| metric value that could not have been derived | condemns its whole run |
| quality flag this build cannot name | condemns its whole run |
| metric *name* this build does not know | skipped |

The asymmetry in the last two rows is deliberate. A value this build does not
need says nothing about the rows beside it; a caveat it cannot render is a
caveat it would be dropping silently, and presenting numbers without their
caveat is the failure that vocabulary exists against.

Currency is decided from the same validation, not only presentation, so
`analyze --outdated` offers to repair the damage rather than reporting it
forever. The repair is always available: the geometry the metrics come from was
never touched.

A damaged analysis reports as `invalid` rather than as `missing`. "Nothing has
been derived yet" and "something is wrong with what was" call for different
reactions. `/healthz` stays healthy: one damaged row is a data problem, and
wiring it into a readiness probe would restart a container that has nothing
wrong with it while hiding the row that does.

### 9. The track listing is a page

Bounded by a server maximum no query string can raise, filtered by effective
kind, activity, year and month, ordered by import date, activity date or
distance -- all in SQL, so a page costs the page rather than the archive.
`total` counts the filtered selection, so a client can tell a last page from a
full one.

Every ordering ends in the track identity. Two rows equal on the sort key would
otherwise come back in an arbitrary order, and a row that moves between page one
and page two is a row the reader sees twice or not at all. Undated tracks sort
last in *both* chronological directions rather than wherever `NULL` falls: a
planned route is not the oldest thing in the archive.

The month filter draws its boundary in the configured zone, through the same
conversion the monthly statistics use, so clicking a bar in a chart reaches the
tracks that bar counted.

## Consequences

- Every stored analysis is outdated. Two algorithms and the metric schema all
  changed, and `analyze --outdated` is the migration -- which is the mechanism
  ADR 0006 built for exactly this day.
- Moving time rises and stopped time falls for any track that bends inside
  thirty seconds. On the recorded reference: 8875 s → 8723 s moving, 6670 s →
  6822 s stopped. The direction differs per track; what changed is that a loop
  is no longer a pause.
- Reported ascent rises slightly and becomes direction-independent. On the
  recorded reference: 483.4 m → 508.4 m gain, 481.9 m → 508.6 m loss, and the
  two now agree to 0.05 % on a round tour instead of 0.3 %.
- A recording without receiver metadata loses its contribution to actual
  *timing* totals while keeping its distance. That is a deliberate reduction in
  what the archive claims, not a loss of data.
- The analysis resource changed shape. It is a projection and no UI consumes it
  yet, so the break is paid now rather than after a dashboard is built on it.
- The listing endpoint changed shape and is no longer exhaustive. A client that
  wants everything pages for it.
- Reading a track's analysis now costs one more query -- the summary, for its
  evidence -- and validating a snapshot costs two batched queries whatever the
  archive's size.

## Known limitations

- Movement inside a circle smaller than the window's reach is reported at its
  extent's speed rather than its path length's. Understated, never overstated.
- Terrain whose swing exceeds the deadband is counted whatever its wavelength.
- No filter *by* analysis status on the listing. Deciding currency needs the
  installed profile and the stored metrics' readability, and expressing that in
  SQL would put a second copy of the authority there; doing it in Python after
  the fact would break pagination. The statistics counters answer "how many" and
  `analyze --outdated` fixes them, which is what the question is usually for.
- `ESTIMATED` temporal evidence is derivable but nothing yet presents a planned
  duration as a planned duration. The route's numbers are reported as a timed
  path with an estimated basis, which is honest and not yet useful.
- No elevation or speed profile resource. The metrics are aggregates; a chart
  needs a series, and deriving one on demand from stored geometry is a separate
  contract.
- No integrity command. The pieces exist -- raw hash verification, foreign-key
  checks, analysis validation -- and centralising them is worth doing when
  something needs them together.

## Alternatives considered

**Withhold timed metrics for non-observed tracks.** Rejected. It conflates the
calculation with the permission, and a planned route's derived duration is a
real property of the data somebody may legitimately want. Reporting it *with its
basis* is strictly more informative than reporting nothing.

**Infer temporal evidence from the source application or the track kind.**
Rejected outright: `if source == "komoot"` is the exact shortcut the classifier
is forbidden to take, and inferring from the detected kind would make two
derived facts depend on each other in a circle.

**Put the temporal evidence on the analysis run.** Rejected. It is a property of
the normalized track's evidence, not of the algorithms, and storing it would
mean re-analysing every track whenever the classifier improved.

**A `MetricValue.basis` field per metric.** Rejected as redundant: every
clock-dependent metric of one run shares one basis, and repeating it per row
would create a second place for it to disagree with itself.

**Bounding-box diagonal for the movement extent.** Rejected. It is exact for a
straight stretch and up to 41 % *over* for a curved one, and overstating a
maximum speed is the one direction this must not err in. Four projection axes
under-report by at most 8 % and never over-report.

**An `allow_mixed=true` statistics option.** Rejected. See decision 7.

**Cursor pagination.** Deferred. Offset pagination over a deterministic order is
correct and enough for an archive of this size, and a cursor scheme is a
contract to keep once published.
