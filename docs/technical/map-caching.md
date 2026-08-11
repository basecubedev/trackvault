# Map caching

Three caches sit between an installed map package and a track drawn beside a
row in the list. They are separate on purpose, they hold different things, and
each one is invalidated by a different fact. Nothing here is authority: every
one of them can be thrown away, and the archive answers exactly the same.

| Layer | Holds | Where | Invalidated by |
| --- | --- | --- | --- |
| Installed packages | the map data itself | `data/maps/packages/` and a database row | installing or removing a region |
| Tiles | the bytes of one tile | the browser's HTTP cache | never; a new package is a new address |
| Rendered minimaps | the finished picture of one track | the browser's IndexedDB | the key no longer matching |

## Installed packages

The authority, and unchanged by any of this. A package is a database row **and**
a managed file that hashes to what the row says; either alone is `invalid`. See
[architecture.md](architecture.md) and
[adr/0010](../adr/0010-offline-map-packages-and-local-basemap-delivery.md).

## Tiles

`/api/v1/maps/tiles/{content-sha256}/{z}/{x}/{y}.mvt`, served
`Cache-Control: public, max-age=31536000, immutable`. The identity in the URL is
the package's content hash, so different bytes are a different address rather
than the same address with something new behind it. That is what makes a year
safe and what makes an update need no cache purge.

Map *state* — coverage, the installed list, a job's progress — stays `no-store`.
It changes while somebody is looking at it, and a cached job status is a
progress bar that never moves.

## Rendered minimaps

The track list draws one map per row with WebGL, reads the canvas back as a PNG
and destroys the map — see `web/src/map/minimap.ts` for why a row gets a picture
rather than a map component. Until this cache existed, that rendering was paid
again on every visit to the page.

The picture is kept in IndexedDB (`web/src/cache/`). Not `localStorage`: these
are images, and `localStorage` is synchronous, small, and stores text — a PNG
would be base64 inflated by a third on the way in and would block the main
thread on the way out. Bytes and a media type are stored rather than a `Blob`,
because implementations have shipped that cannot structured-clone one into a
store.

### Two geometry identities, and which is which

The archive publishes two, because there are two questions and one value cannot
answer both.

| | `TrackResponse.geometry_sha256` | `GeometryResponse.shape_sha256` |
| --- | --- | --- |
| Answers | is this the same **recording**? | is this the same **drawing**? |
| Positions | yes | yes, in order |
| Instants | yes | no — a map does not draw a clock |
| Segment boundaries | **no** | **yes** |
| Elevation, sensors | no | no |
| Of what | the track's canonical geometry, stored | *this response*, including its reduction |
| Used by | `same_recording_ids` | the minimap cache key |

They disagree exactly where a route export flattened a paused recording into one
run: **one ride, two pictures**, and both answers are right. `geometry_sha256`
must keep saying "same recording" there — that is what groups one afternoon
exported as GPX 1.1, as GPX 1.0 and as a route — so it cannot also be the
identity of a drawing. A map draws one line per segment, never one line across a
break, so the same positions split `[A,B,C][D,E]` and `[A,B][C,D,E]` are two
different pairs of lines.

`shape_sha256` identifies **the answer, not the archive**. A preview asks for at
most `MINIMAP_POINTS` positions and draws what comes back, so the identity is
computed over the reduced segments — a different `max_points`, or a changed
simplification, is a different value with nothing to remember. Both are computed
in `trackvault.domain.geometry`; nothing is hashed in the browser, because a
second answer computed there is a second authority.

### The key

A rendering may be reused when, and only when, everything that decided how it
looks is unchanged — and it must be reused when all of it is. The key names all
of it:

```
trackvault-minimap:v2|shape=<shape_sha256>|maps=<delivery ids>|theme=<t>
  |projection=<n>|style=<n>|render=<n>|width=<n>|height=<n>|points=<n>
  |maxzoom=<n>|padding=<n>|pixel=<device pixel ratio>
```

Every field is named, so a field inserted later is a miss rather than a wrong
answer. The canonical string *is* the key rather than a digest of it: hashing
needs `crypto.subtle`, which a browser withholds outside a secure context —
which is exactly a self-hosted archive on a plain-HTTP address on somebody's own
network.

**`maps`** is the ordered `delivery_id` of every package the coverage answer
reported, which is the content hash of each. A region installed again from newer
data is a new hash, so the picture drawn over the old package is simply never
found again. Nothing has to hunt for it.

**`pixel`** is the display's device pixel ratio. MapLibre sizes its canvas by it,
so the same track on a plain monitor and on a high-resolution laptop are a
132×96 image and a 264×192 one — the same box, two pictures. Without it, moving a
window between two screens shows one of them a blurred copy of the other.

**`projection`**, **`style`** and **`render`** are `SHAPE_PROJECTION_VERSION` in
`web/src/map/geojson.ts`, `BASEMAP_STYLE_VERSION` in `web/src/map/style.ts` and
`MINIMAP_RENDER_VERSION` in `web/src/map/minimap.ts`. They stand for the code
that decides what a map looks like: how a shape becomes features and a frame,
what a basemap's palettes, layers, filters, line widths and glyphs are, and how
the camera and the track overlay draw. **Change any of that and bump the
constant in the same file, in the same commit** — nothing else can tell that a
kept picture is no longer one this build would produce. Three constants rather
than one because each sits where its edits happen; a version in another module
is a version nobody remembers. They are deliberately constants rather than a
digest of the source, which would change for a renamed local and stay the same
for a colour moved between two files.

### What was audited and left out

Every input `renderTrackMinimap` receives, and where each is accounted for:

| Input | Accounted for by |
| --- | --- |
| the line, its order, its segment breaks, its reduction | `shape` |
| antimeridian unwrapping, the framing rectangle, single-position padding | `projection` |
| tile template, min/max zoom, package bounds | `maps` — all derived from the content hash |
| palettes, layers, filters, line widths, opacity, label fonts and sizes | `style` |
| glyph ranges under `web/public/fonts/` | `style` — committed assets, changed deliberately |
| sprite sheet | none exists; no layer uses `icon-image` |
| track casing and line colour and width, fade, camera options | `render` |
| theme, width, height, zoom ceiling, padding, position budget | their own fields |
| canvas resolution | `pixel` |
| attribution text | not in the picture — see below |

Deliberately **not** in the key: the track's title, note, classification,
activity, statistics and instants. None of them is drawn, and a renamed track
must keep its picture. `points` is kept although `shape` already reflects it: the
shape identity names the answer and this names the request, and a key that says
which budget produced a shape is one somebody can read.

### Attribution

Coverage is read on a reuse exactly as on a first draw, and what it answers is
what gets credited. Attribution is a fact about the archive now; it is never
read back out of a stored picture. The delivery identities being in the key is
what makes the two agree — a picture is only ever reused when the packages
behind it are the ones still installed.

### Limits and failure

64 MiB and two thousand entries, least recently used evicted first; a minimap is
a few kilobytes, so the entry ceiling is the one that usually binds. A storage
refusal makes room and retries once, then gives up. The schema version is the
database's own version, so an upgrade discards the old store rather than
orphaning a database nobody opens again and nobody reclaims.

Version 2 of that schema does not change what a record looks like; it changes
what one *means*. Every key version 1 wrote was built from the recording
identity, which cannot tell two drawings of one recording apart, so nothing
written under it can be trusted to be a picture of what its key claims. The new
keys would never find those entries anyway — dropping them is what the version
is for, and a derived picture is never worth migrating.

Nothing the cache does can fail a caller. Storage switched off, a quota that is
full, a record this build cannot vouch for, a database somebody cleared
mid-session: all of them end at a track drawn the ordinary way. A cache that can
break a page is a defect, not an optimisation.

### Why it is not in a backup

It holds a derived presentation artifact and nothing else — no track data, no
API responses, nothing that could not be produced again by drawing the map. It
is browser-local, under the same access control as the browser itself, and the
[archive format](../user/backup-and-restore.md) is unchanged by it. A rendered
picture of somebody's afternoon is also exactly the thing that should not become
a shared server-side artifact, which is the second reason it is drawn in the
browser that shows it.

If the cache and the archive ever disagree, the archive wins: the picture is
found by an identity the archive publishes, so a stale one is not found at all.

### What causes a miss, and what deliberately does not

| Change | |
| --- | --- |
| a position moved, added or removed | miss |
| the same positions split into different runs | miss |
| a different position budget that changes the drawn line | miss |
| a map package installed again from newer data | miss |
| a package added to or removed from the coverage | miss |
| a different theme | miss |
| `SHAPE_PROJECTION_VERSION`, `BASEMAP_STYLE_VERSION` or `MINIMAP_RENDER_VERSION` bumped | miss |
| a different width, height, zoom ceiling or padding | miss |
| the page moved to a screen with another pixel ratio | miss |
| the track renamed, re-noted, reclassified, re-analysed | **hit** |
| the same track drawn again after a reload or in a new tab | **hit** |
| one recording imported twice, drawn identically | **hit**, and they share one entry |

### What is deliberately not cached

The interactive map on a track's own page. It stays a live MapLibre map because
zoom, pan, hover and the chart coupling are what it is for, and the immutable
tile cache already covers what it costs to draw. There is no service worker and
no offline synchronisation: this caches one derived picture, and nothing else.
