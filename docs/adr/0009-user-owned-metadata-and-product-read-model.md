# ADR 0009: User-owned metadata, the archive's own calendar, and release readiness

## Status

Accepted (2026-08-09)

Extends ADR 0008. It supersedes no decision: everything ADR 0008 decided about
availability, calendar placement and the profile still holds, and this is what
had to be added around it before the result was something to hand somebody.

## Context

ADR 0008 finished the read side and the first browser slice. Using the result
turned up a different class of problem — not wrong answers, but answers with
nowhere to go.

**The interface invented a calendar.** The year selector offered "this year and
the eleven before it", generated from the reader's clock. An archive of 2019
recordings opened on an empty 2026 and looked broken; an archive with a single
2013 ride could not be reached at all. It was also a second authority on a
question the backend already owns: which local years hold tracks a period may
date.

**An empty archive looked like a defect.** A fresh installation showed a year of
zeros, twelve empty months and no explanation. "Nothing has been imported" and
"nothing matches this filter" rendered identically, and they call for opposite
reactions.

**Nothing could be corrected.** Exports arrive titled `Track`, `2024-06-11
09:14`, or nothing at all. A personal archive stops being pleasant to keep the
moment three rows read the same, and the only correction the archive offered was
of a track's *kind*.

**The map picked the wrong branch.** The chart cursor and the map marker were
coupled by searching for the nearest latitude and longitude. At a self-crossing
— a figure of eight, a lap, an out-and-back — two positions an hour apart are
equidistant from the pointer, and the marker jumped between them as the mouse
moved one pixel. ADR 0008 had already established that a sample has an identity
and that nearest-coordinate matching is a second authority on it; the browser
was still doing exactly that.

**A mouse move re-rendered the page.** The hovered sample lived in the detail
page's state, so moving the pointer along a track re-rendered the map, the
chart, six metric cards and two tables, sixty times a second, to move one dot.

**Everything shipped in one bundle.** The track list downloaded a charting
library it never uses. The dashboard's entry chunk was 791 kB.

**Three licence gaps.** The third-party notice document never reached the
runtime image, so the people it is written for never saw it. A manually reviewed
licence exception hung on a package *name*, so a relicensed package would keep
passing. And the dependency tables were written by hand against lock files
nobody compared them with.

**And nobody had measured a large track.** "About 2.4 seconds for 100 000
points" was in a report, unattributed to any stage.

## Decision

### 1. A correction is user-owned data, stored beside the evidence

```
source title       what the document said         never modified
title override     what the user said             the display authority
effective title    the override, else the source  a projection of the two
```

`track_user_metadata` (schema 6) holds a title and a note per track, keyed on
the track identity. That identity survives reprocessing, which is the whole
point: a reprocess replaces every normalized column of a track row, so a title
stored *there* would be a title an importer upgrade silently discards, and
nobody would know which of their corrections were gone.

This is deliberately the shape the classification override already has.
`display_title` is a projection exactly as `effective_kind` is — no third value
is stored — and `None` stays a real answer, because some documents genuinely
name nothing and inventing a name in the domain would put a guess where a
presentation layer can say something honest and admit that it did.

Titles and notes are plain text, trimmed, length-bounded, and **refused rather
than truncated**: somebody's sentence is not the archive's to cut. A blank
title is *absence*, not an empty name — storing `"  "` would hide the source
title behind something that renders as nothing.

`PATCH` is a real partial update. An absent field keeps its value, an explicit
`null` clears it. A rename that silently deleted a note is how a partial update
loses data, and the sentinel that prevents it is three lines.

**What the fallback is not built from.** A track its source never named is
described by its activity and its date — `Walking, 20 Oct 2025` — and never by
its filename. A filename is personal data as readily as the positions are
(`hike-with-anna-to-her-house.gpx`), it is display metadata rather than
identity, and a server path never appears anywhere. A track whose clock nothing
vouches for is *not* dated in its own title either: a title is the one place a
reader takes as fact.

### 2. The archive names its own calendar

`GET /api/v1/statistics/years` answers which local years a scope holds tracks
in, newest first, over exactly the tracks a period may date — the same
`supports_actual_calendar_placement` rule the totals and the listing's `year`
filter apply, bucketed in the same aggregation zone.

The response carries two more facts, and both exist to make an empty screen
legible:

- `unplaced`, split into `without_date` and `with_unverified_date`, because no
  instants at all and instants nothing vouches for are different facts and only
  the second looks like a date until somebody checks;
- `archive_track_count`, which is the one number that tells "nothing has been
  imported yet" apart from "nothing matches this selection".

The dashboard therefore has three distinct first-run states instead of one
ambiguous page of zeros, and the empty one names the two commands that fill it —
never "upload your first GPX", because there is no upload endpoint and saying so
would send somebody looking for a button that does not exist.

`DatedTrackRow` is a new, deliberately tiny port row: an instant, a kind and an
activity. Answering "which years exist" by loading a metric set per track would
be paying a total's price for a dropdown.

### 3. Map hover works along the structure that is drawn

```
1  candidates   the samples actually under the pointer, measured in pixels
2  continuity   of those, the ones near where the pointer already was
3  determinism  and if that settles nothing, the lowest identity wins
```

Step 2 is what makes a crossing behave: a reader tracing the first loop has a
previous sample on the first loop, and the candidate continuing it is the one
they mean. Step 3 is the honest admission that a *cold* hover exactly on a
crossing is genuinely ambiguous — no interaction context exists, so the
behaviour is **defined** rather than guessed, the same way on every machine.

The hit radius is measured in screen pixels rather than in degrees, so "under
the pointer" means the same thing at every zoom level. All of it is arithmetic
on plain numbers, so a figure-of-eight fixture tests it without a browser.

### 4. The hovered sample lives outside React

A tiny store with a subscription. The map moves its marker through MapLibre, the
chart moves its cursor through ECharts, and one paragraph of text re-renders.
This is not a state library and nothing else may read it: browser state is never
business authority, and a hover is the clearest case of state that belongs to a
gesture rather than to the data.

### 5. ECharts is a lazily loaded chunk

The track list draws no chart and now downloads no charting library. The
dashboard's initial JavaScript fell from 791 kB (261 kB gzipped) to 255 kB
(81 kB gzipped); the chart arrives in its own chunk, after first paint.

MapLibre stays on the detail route, where it is always needed, and is not
replaced by a smaller library — a mapping library that is 40% the size and 60%
of the behaviour is not a saving.

### 6. Measured before optimised, and no cache

`scripts/benchmark_projection.py` measures each stage separately, because "the
profile takes 2.4 seconds" is the absence of a finding rather than one. On a
250 000-position synthetic recording:

```
elevation filter      235 ms
movement window      3381 ms
profile series       4753 ms
profile decimation   5078 ms
map simplification   1189 ms
bounded payload      5133 ms
```

The movement window is roughly 70% of it. Profiling it shows no redundancy to
remove: the sliding spread already runs as a monotonic queue, both window edges
only advance, and the remaining time is per-position CPython work spread evenly
across the projection, the queue and the haversine. So **nothing was optimised
on a guess, and no cache was introduced.** A cache here would need binding to
the processing generation, the analysis profile, the projection version and the
sample budget — a fourth derived-state lifecycle bought to avoid an honest
linear cost that the bounded response already keeps off the network. Serialising
the response is 55 ms of the 5133, which is exactly the point.

Measuring it did find one real defect. Decimation kept *local* turning points
and, when a segment had more of them than its budget, thinned them evenly —
which can drop the global maximum. A chart whose peak sat two centimetres below
the summit quoted beside it. The global extremes of the filtered profile and of
the sustained speed are now kept unconditionally, before anything optional is
considered.

### 7. A deployment can say what it is

`GET /api/v1/system/info` reports the release, the schema version, the
aggregation zone and both currency profiles. "Why did my elevation gain change?"
is answerable from the deployment rather than from a changelog somebody has to
find. It describes the **build**, never the machine: no path, no data directory,
no configured provider.

The version has one authority, `pyproject.toml`. The package reads it from
distribution metadata, the OpenAPI document carries it, and the image label
takes it as a build argument whose default a contract test compares with
`pyproject.toml`. The image deliberately carries no `revision` or `source`
label: an ordinary `docker compose build` knows neither, and an empty label
looks answered.

### 8. Four response headers, and no more

`X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `X-Frame-Options`, and
a `Content-Security-Policy` naming only what the page never does —
`frame-ancestors`, `base-uri`, `form-action`, `object-src`.

The policy deliberately sets **no** `script-src`, `connect-src` or `img-src`. A
basemap is operator configuration: a style document lives on one host and
routinely references tiles, sprites and glyphs on others, none of them knowable
when the image is built. A policy naming them would break a provider somebody
configured, and a half-working CSP is one people switch off.

`Referrer-Policy` earns its place rather than being copied from a checklist. A
basemap request would otherwise tell a third-party tile service the URL of a
private, self-hosted movement archive.

### 9. Licence statements that cannot quietly rot

- The notice document **ships in the runtime image** at
  `/app/THIRD_PARTY_NOTICES.md`. Whoever pulls a container and never sees this
  repository is exactly who those statements are for.
- A reviewed exception **names the licence it was granted for**. What somebody
  read was a document, so an exception recorded against a package name would
  outlive it. Both audits now report a third outcome, `relicensed`, distinct
  from "never reviewed" because it needs a different conversation.
- A contract test checks every **direct** dependency in the document against
  `pyproject.toml`, `uv.lock`, `package.json` and `package-lock.json` for
  existence, version, licence and a stated purpose. Transitive dependencies stay
  out of the document — several hundred rows nobody reads is a worse statement
  than none — and stay covered by the two audits, which read the whole resolved
  trees.

**No project licence was chosen.** That decision belongs to the project owner,
and the README says so plainly rather than leaving it unmentioned.

## Consequences

- A user's correction survives an importer upgrade, a reprocess, a container
  replacement and a candidate temporarily disappearing from its source document.
  The raw import stays byte-identical throughout, which the opt-in local
  regression checks by hashing both real files before and after a rename.
- The interface can no longer show a year the archive has nothing for, and can
  no longer fail to show a year it has.
- A self-crossing hover is deterministic. Where it is mathematically ambiguous,
  the behaviour is documented rather than emergent.
- A large track is bounded on the wire and measured on the server, and the
  measurement is reproducible from a script rather than remembered from a
  report.
- There is no cache, and the reason there is no cache is written down with the
  numbers that justify it — so the next person to consider one starts from
  evidence rather than from an impression.

## Deliberately still open

- **Authentication**, and with it any upload endpoint. Everything above assumes
  a trusted network and the README says so in its own section.
- **A re-analysis button in the browser.** `analyze --outdated` is an operator
  action on the machine that holds the data; the interface explains what to run
  behind a disclosure rather than putting a command line in front of a reader
  who cannot use it.
- **Automatic backup.** The requirement is documented — both halves of the data
  directory, and never a copy of a live WAL database — and no scheduler was
  added. An archive that silently stopped backing itself up would be worse than
  one that never claimed to.
- **Full-text search.** The metadata schema does not stand in its way; nothing
  was built for it either.
