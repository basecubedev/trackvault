# ADR 0008: Read authority, calendar placement and the track profile

## Status

Accepted (2026-08-09)

Extends ADR 0006 and ADR 0007. It **supersedes ADR 0007's decision 1** (movement
from a single windowed threshold), its "no filter by analysis status" limitation
and its "no elevation or speed profile resource" limitation. The algorithms ADR
0007 describes stay the record of what was decided; `windowed-extent` version 1
is what its numbers were produced by, and every result that names it still says
so.

## Context

ADR 0007 made every stored number say which algorithms produced it and made
statistics refuse to mix two generations. Auditing the *read* side against that
turned up four defects and one omission, and all four defects were silent.

**A stale metric was still a headline.** The track listing carried whatever
metrics sat under `tracks.current_analysis_run_id` without asking whether that
run applied the installed profile. An outdated 20 km presented itself as the
track's length, sorted above a current 10 km under `longest_first`, and the
track's own analysis resource called the same thing `outdated` on the next
screen.

**Damage read as absence.** A stored analysis nobody can interpret counted as
`tracks_without_analysis` in a total while the detail view reported `invalid`.
"Nothing derived yet" and "something is wrong with what was" need different
reactions and were being given the same number.

**A timestamp was taken as a date.** ADR 0007 established that a timestamp is
not evidence of *movement*. It left standing the assumption that a timestamp is
evidence of *when*. The real 391-position route carries plausible October
instants and no measurement metadata at all: it appeared under
`tracks?year=2025&month=10` and added its distance to the unknown scope's yearly
total. A user correcting its kind to `recorded` moved 8.9 km into a year nothing
in the file can support.

**`year + 1` was how a year became a window.** Two copies of a `MAX_YEAR`
constant accepted 9999 and the arithmetic behind them computed 10000, which is
not a year Python has. A route validated a value its own use case then crashed
on.

**One threshold decided what movement is.** Movement was decided by a single
windowed speed against 0.5 m/s, which makes an unstated claim -- nothing below
0.5 m/s is standing still -- and turns one centimetre per second into the
difference between ten minutes of walking and ten minutes of standing still.

The omission: the metrics are aggregates and a chart needs a series, so there
was nothing for a user interface to draw. There was also no user interface.

## Decision

### 1. One availability answer, three read surfaces

`InstalledAnalysis.availability` decides, once, which of four states a track's
stored metrics are in:

```
current    the installed algorithms, from the geometry a reader sees
outdated   metrics from rules or geometry that have moved on
missing    nothing has ever been derived
invalid    something was derived and cannot be interpreted
```

A listing row, the track's own analysis resource and a period total all project
that value. Damage is checked *before* currency: a run whose profile is also
unreadable is damage rather than an old algorithm, and "re-run the analysis" is
a different instruction from "a row is broken".

Consequences:

- A row carries a metric only while its analysis is `current`. In the other
  three states the metric is `null`, because a headline number is a claim about
  what the track *is* rather than about what was once derived from it. The last
  thing derived stays readable through the analysis resource, which says what it
  is.
- `longest_first` means longest according to a current analysis. A track with no
  current distance sorts last, ahead of the identity tie-breaker.
- Tracks are filterable by availability, and the filter is applied in the
  statement that counts and pages -- so `total` counts the filtered selection.
  Filtering after paging would page first and hide second.
- `tracks_with_invalid_analysis` joins the statistics counters, and the four
  availability counters now partition a period exactly.
  `tracks_with_failed_analysis` stays orthogonal and overlaps them all.

The repository receives the *values* of the installed profile as query
parameters and compares them. It holds no table of what is installed: an
algorithm change must not have to be made twice, because the second place is the
one nobody remembers. This is the same shape as the existing `effective_kind`
projection -- SQL applies a rule the domain owns.

### 2. Timeline time is not activity calendar time

```
timeline time            the instants the track's own positions carry
activity calendar time   the claim that this happened in this period
```

`supports_actual_calendar_placement` decides the second, from the temporal
evidence and from nothing else. A planner writes plausible instants onto
geometry nobody travelled; a recording stripped of its receiver metadata is
structurally identical. Placing either in a month puts a real distance into a
period it has nothing to do with, and nothing about the resulting total looks
wrong.

It deliberately does **not** read the track kind. A user correcting a route to
`RECORDED` is saying what the track is, not that its clock was measured, and
letting the correction reach this answer would make an override a way to
manufacture a date. The correction still moves the track between the actual,
planned and unknown sets, which is what it is for.

Tracks with no calendar placement are reported beside the period rather than
inside it, and split, because the two halves are different facts:

| Reported as | Means |
| --- | --- |
| `unplaced.without_date` | no instants at all |
| `unplaced.with_unverified_date` | instants nothing showed to be measured |

`undated` is renamed to `unplaced` rather than quietly widened. The field's
meaning changed, and a field that means something new under an old name is worse
than a rename before there is a released contract to keep.

The listing's `year` and `month` filters apply the same rule, so clicking a bar
in a monthly chart reaches exactly the tracks that bar counted. The timeline
itself is never withheld: every track reports `started_at`, `ended_at` and the
`basis` that qualifies them, in one `timeline` object rather than as two
unqualified root fields.

### 3. One calendar range, and an open upper bound

`trackvault.application.calendar` is the one statement of what years a query may
name and how a period becomes an instant window. The last supported year yields
an **open** upper bound rather than the first instant of a next year that does
not exist. Stopping at 9998 to keep `year + 1` working would have hidden an
arithmetic limitation inside a business constant.

### 4. Movement is read at two scales, net of the receiver's own noise

One scale cannot separate slow progress from receiver noise: over thirty seconds
they cover a similar spread. What separates them is what the spread does when
you watch longer -- a still receiver's cloud is bounded, a walker's grows at the
rate they advance. So the positional extent is measured at two scales, with the
wander a still receiver produces anyway subtracted before a rate is taken:

```
thirty seconds already show 0.5 m/s              -> moving
three minutes show 0.1 m/s of net progress       -> moving
three minutes stay inside the receiver envelope  -> stopped
neither                                          -> unattributed
```

The long window is measured only across consecutive intervals the short one
could not settle, and clipped to them. That is what keeps a rest inside a walk a
rest: the slow stretch a rest forms is bounded by the moving intervals either
side, so the longer window never reaches them.

The fourth answer is used rather than avoided. A ten-metre circle walked slowly
produces a bounded cloud exactly as a receiver on a table does; calling it a stop
would be a claim the positions do not support.

New profile component: `windowed-extent` version 2. Distance, elevation and the
metric schema are unchanged and are not bumped.

### 5. Series come from the same authorities as the aggregates

`derive_profile` computes nothing of its own. It asks the elevation filter and
the movement window for their own series -- the same ones `elevation_gain_m` and
`maximum_speed_mps` are read from -- and lays them out against the positions.
A chart drawn from raw altitude under a filtered figure is two pictures of two
tracks sharing an axis.

A sample is addressed by `(segment_index, point_index)`: the position's own
address in the geometry. A map marker and a chart cursor mean the same sample
without the browser matching a coordinate, which is a second authority on
identity and picks the wrong position exactly where a track crosses itself.

Cumulative distance is summed within segments and carried across them, so the
ground between two segments never enters the axis and the axis never jumps.

### 6. Two reductions, because a map and a chart are wrong in different ways

| Projection | Keeps | Because |
| --- | --- | --- |
| map simplification | the positions carrying the shape (Ramer--Douglas--Peucker, as a heap) | a corner matters, a straight kilometre does not |
| profile decimation | ends, segment boundaries, elevation and speed turning points | a summit sits on nearly straight ground and is the first thing a shape simplifier drops |

Both run per segment and keep each segment's ends, so neither ever joins two.
Nothing is written back: this is presentation, and the canonical geometry keeps
every position it was imported with. `/profile` is bounded by default;
`/geometry` stays the canonical normalized track unless a caller asks for
`max_points`, and says which of the two it returned.

Nothing is persisted. A third derived-state lifecycle -- written, versioned,
invalidated, repaired -- is three more ways to hold something stale, and no
benchmark has shown it is needed: deriving the profile of a hundred thousand
positions takes about two seconds and simplifying it takes fifty milliseconds.

### 7. A browser application, served from the same origin

TypeScript, React, Vite, MapLibre GL JS and Apache ECharts, under `web/`, built
into static assets the Python process serves. One origin, so there is no CORS
configuration to get wrong, and one route serves the build: it resolves inside
the build directory, never answers `/api` with HTML, and hands the entry page to
anything else so a deep link to `/tracks/123` survives a refresh.

The browser's types are **generated from the backend's OpenAPI document**. A
hand-written `TrackResponse` that drifts from the server is a bug that
type-checks. `trackvault openapi` writes that document deterministically and CI
regenerates and diffs it.

The page holds no state the backend cannot recompute, so there is no state
library: a cache of the truth beside the truth is the browser's version of a
second authority.

Two rules the interface exists to keep:

- **Absent is not zero.** A metric the archive could not derive renders as a
  dash and is announced as unavailable.
- **Nothing is distinguished by colour alone.** Every state carries a word and a
  glyph, because `current` and `invalid` are exactly the two a reader has to act
  on differently.

The basemap style is handed to the page at request time. A tile provider is an
operator's account and terms, not a build constant, and without one the map
draws the track over a neutral background and stays usable.

### 8. Dependencies are audited, once, for the whole repository

Two audits read the resolved trees -- `package-lock.json` and the installed
distributions -- against one `license-policy.json`. They are an engineering gate
and say so: allowed, review required, unknown. "Review required" means somebody
has to read a licence, not that a package is forbidden.
`docs/legal/third-party-notices.md` is the canonical list.

## Consequences

- Every stored analysis is outdated again. The movement algorithm changed, and
  `analyze --outdated` is the migration.
- Moving time rises and stopped time falls for tracks with slow sections. On the
  recorded reference: 8723 s → 10015 s moving, 6822 s → 3612 s stopped, and
  1918 s newly `unattributed` -- time the archive now admits it cannot
  attribute rather than calling it a stop.
- A track whose instants nothing vouches for leaves every period total and every
  period listing. That is a deliberate reduction in what the archive claims. The
  distance is not lost; it moves to `unplaced.with_unverified_date`.
- The listing and statistics responses changed shape: `analysed` became
  `status`, `undated` became `unplaced`, and the track's instants moved into
  `timeline`. Nothing external consumes them yet, so the break is paid now.
- The image gained a build stage and about 250 kB of gzipped browser assets. It
  gained no runtime dependency: there is still no Node in it.
- CI gained three jobs and roughly doubled in wall-clock time.

## Known limitations

- Movement below `SLOW_MOVEMENT_SPEED_MPS` of *net* progress is not claimed, and
  neither is any path staying inside the receiver's own envelope. A ten-metre
  circle walked slowly reports as `unattributed`, which is honest and not
  useful. The bound is stated and tested rather than discovered.
- The noise envelope is a constant, not an estimate from the track's own data. A
  receiver having a very bad day is treated like one having an ordinary one.
- Profile series are derived on every request. That is measured and fine at a
  hundred thousand positions; an archive of much longer recordings would want a
  cache, and a cache is a lifecycle.
- The map hover resolves a pointer to the nearest sample by coordinate, because
  a pointer on a map *is* a coordinate. What it returns is a sample index, so
  everything downstream still addresses identity -- but the nearest-sample step
  itself can pick the wrong branch where a track crosses itself.
- No authentication, so no upload endpoint and no mutation beyond the
  classification override. The deployment assumption is a trusted network.
- `ESTIMATED` temporal evidence is still derivable and still not presented as a
  planned duration.
- The interface offers no way to run an analysis. Re-analysing is a command-line
  operator action, and a button that shells out over an unauthenticated API
  would be a worse thing than the inconvenience it removes.

## Alternatives considered

**Decide analysis availability in Python after the page.** Rejected. It reports
a correct verdict under a wrong `total`: the filter would page first and hide
second, and every pager built on the response would be wrong by the number of
hidden rows.

**Keep `analysed: bool` for compatibility.** Rejected. It was the wrong question
with a reassuring answer -- true for stale metrics and true for unreadable ones
-- and nothing external consumes the API yet. A compatibility layer for an
unreleased contract is a cost with no beneficiary.

**Let a kind override make a track's timestamps trustworthy.** Rejected
outright. It would make the one user-facing correction a way to manufacture
dates, and the classifier is forbidden from reasoning that way for exactly the
same reason.

**Place planned routes by their route timeline.** Rejected as the default. A
routing engine's clock is a plan; a period total built on it measures when
somebody pressed a button. If planned scheduling evidence ever exists it can
have its own authority.

**Lower the movement threshold instead of adding a scale.** Rejected. It moves
the cliff rather than removing it, and at any threshold low enough to catch
0.3 m/s a stationary receiver becomes a walker.

**Persist the profile series.** Rejected for now. It is a third derived-state
lifecycle bought against an optimisation nothing has yet shown is needed.

**A component framework and a state library for the browser.** Rejected. Three
pages, one server, and every value recomputable from it.

**A browser matrix in CI.** Rejected. Chromium only: a matrix multiplies the
runtime and tests the same code four times.
