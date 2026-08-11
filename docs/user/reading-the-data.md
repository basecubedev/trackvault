# Reading the data

What the interface shows, what each number means, and how to ask for the same
things over HTTP.

## The web interface

Three pages, served from the same origin as the data.

**Dashboard.** A year at a time: distance, track count, moving time and ascent,
with a monthly chart and the same numbers in a table beside it. The year is the
newest one *the archive* has something to show for, not the one your computer
thinks it is. Recorded, planned and undecided tracks are three separate scopes
and are never added together.

Each month is drawn as **one bar per activity**, with a legend above the chart
and a column per activity in the table below it — so a month of cycling and a
month of walking are two answers rather than one sum. An activity keeps its
colour whatever else the year holds, so narrowing the filter never repaints what
is left. A month an activity was not out in shows a dash, not a zero: nobody
walked no kilometres.

The year selector also offers **All years**, which is a different question and
says so in its shape: the totals cover everything the scope holds, and the chart
draws one bar per year instead of per month — one bucket for each year the
archive has something for, not a padded calendar. Opening one lists that year's
tracks. The archive answers it in one request; the browser never adds years up
itself, because summing periods correctly means knowing which analyses may be
totalled at all.

**Tracks.** The archive, one page at a time, with every filter in the address
bar — so a filtered view is a link you can send and a refresh does not lose it.

It opens on **Recorded**, because opening the archive is nearly always a question
about what you actually did. That is a starting point and not a restriction: the
*Kind* filter still offers Planned, Unknown and *All kinds*, and a link that
names a kind — `?kind=planned`, `?kind=unknown`, `?kind=all` — always wins over
the default.

*Import files* at the top offers files to the archive from the browser. Each row
carries a small map of where that track went, drawn over the same offline
basemap the track's own page uses. A row draws nothing until you have scrolled
to it, and it asks for a reduced shape rather than the whole recording, so a
page of rows costs a page of rows.

A row also says **roughly where the track was** — `≈ Zeeland · Netherlands`. The
tilde is not decoration: the archive compares the rectangle around your track
with the rectangle around each region the map provider publishes, which is right
well inside a country and wrong at a border. A walk in Aachen is named for the
Dutch province next door, and the mark is what stops that reading as a fact. The
region and the country come from one selection — the country is the named
region's own ancestor — so they can be wrong together but never contradict each
other. It needs a region catalog, so **Offline maps → refresh** once; without
one the archive says nothing rather than guessing from a coordinate.

A row that says **"3 imports"** means the same recording arrived three times, in
three different files — the same ride exported as GPX 1.1, as GPX 1.0 and as a
route, say. All three are kept: the bytes are different evidence, and one format
carries readings another cannot. What the badge stops is the list presenting one
afternoon as three. It is an *equality* — identical positions at identical
instants — so it never guesses that two rides are one, and it does not find the
same loop ridden on two different days.

The arrow at the right of a row opens **the track's whole page inside the list**
— the same component, not a summary of it — so looking for the right track is
scrolling and reading rather than a page load and a back button each time. One
row at a time, and which one is in the address too. Corrections made in there
are corrections: the row above updates rather than keeping the answer it had.

**One track.** What it is, what it did, where it went, and how the archive
decided all of that. You can correct its kind, give it a title of your own and
keep a note; the file keeps saying what it said, and resetting hands the display
back to it.

If a recording carries **heart rate or cadence**, they get a chart of their own
below the elevation one — measurements passed through exactly as the sensors
reported them. A gap is a reading the sensor missed; a cadence of zero is a
reading, not a gap. Tracks without sensors show no such chart rather than an
empty one.

If a track's metrics were derived by algorithms this build no longer runs, the
headline figures are left blank and the page says why — while the map and the
chart, which are derived now, stay exactly right.

## What the numbers mean

All stored values are SI; the interface converts for reading and never back.

| Metric | Meaning |
| --- | --- |
| `distance_m` | horizontal distance between consecutive positions, summed **within** segments |
| `elevation_min_m`, `elevation_max_m` | extremes of the raw observations |
| `elevation_gain_m`, `elevation_loss_m` | ascent and descent of the *filtered* profile |
| `elapsed_duration_s` | first position timestamp to last |
| `moving_duration_s` | observed time actually going somewhere |
| `stopped_duration_s` | observed time standing still |
| `unobserved_gap_duration_s` | time nothing was recorded at all |
| `unattributed_duration_s` | observed time no rule could classify |
| `average_speed_mps` | distance over elapsed duration |
| `moving_average_speed_mps` | distance covered while moving, over moving time |
| `maximum_speed_mps` | highest speed **sustained** across the analysis window |

Four of those are deliberately not what the obvious formula would give:

- **A twenty-minute silence is not a rest.** A paused app, a flat battery and a
  break look identical in the data, so that time is an *unobserved gap*. A stop
  is only claimed where positions kept arriving and showed no movement.
- **A stationary phone is not walking.** A receiver standing still reports
  positions metres apart every second, so movement is decided from how far the
  track got across a window, at two scales — and the same window keeps one bad
  fix from becoming your maximum speed.
- **Elevation noise is not climbing.** Summing every upward step turns a flat
  hour into hundreds of imaginary metres and grows with the sampling rate, so
  ascent comes from a filtered profile. Minimum and maximum stay raw.
- **A timestamp is not a measurement.** A route planner writes plausible times
  onto geometry nobody travelled. Durations from such a track are reported as
  the path's own timeline, never as time somebody spent, and the track belongs
  to no month.

A metric that cannot be derived is *absent*, never zero. Everything states the
algorithm version that produced it, so when an algorithm changes,
`analyze --outdated` regenerates and the old numbers were never mistaken for the
new ones.

## The HTTP API

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` | Liveness probe |
| `GET /api/v1/system/info` | Release, schema version and the installed algorithms |
| `GET /api/v1/tracks` | List stored tracks, without geometry |
| `GET /api/v1/tracks/{id}` | Read one track |
| `GET /api/v1/tracks/{id}/geometry` | Its segments and positions; `?max_points=` for a simplified shape |
| `GET /api/v1/tracks/{id}/profile` | Its elevation and speed against distance; `?max_samples=` |
| `GET /api/v1/tracks/{id}/analysis` | Its derived metrics and their status |
| `PUT /api/v1/tracks/{id}/classification` | Correct the kind: `{"kind": "recorded"}` |
| `DELETE /api/v1/tracks/{id}/classification` | Withdraw the correction |
| `PATCH /api/v1/tracks/{id}/metadata` | Your own title and note: `{"title": "…"}` |
| `GET /api/v1/statistics/years` | Which years this archive holds tracks in |
| `GET /api/v1/statistics/year/{year}` | Total one year |
| `GET /api/v1/statistics/year/{year}/monthly` | Total each of its twelve months |

Statistics take `?scope=recorded|planned|unknown` (default `recorded`) and an
optional `?activity=walking`. Actual and planned are never mixed, and every
response names the timezone its buckets were drawn in.

```bash
curl 'http://127.0.0.1:8081/api/v1/statistics/year/2025?scope=recorded'
```

Every unit is in its field name, and a metric that could not be derived is
`null` rather than `0`. Geometry is a separate request on purpose: a long
recording holds tens of thousands of positions, and `point_count` in the listing
tells a client what it would be asking for.

Interactive documentation is at `/docs`.
