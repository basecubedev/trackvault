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

### The key

A rendering may be reused when, and only when, everything that decided how it
looks is unchanged. The key names all of it:

```
trackvault-minimap:v1|geometry=<sha256>|segments=<n>|maps=<delivery ids>
  |theme=<t>|style=<n>|render=<n>|width=<n>|height=<n>|points=<n>
  |maxzoom=<n>|padding=<n>
```

Every field is named, so a field inserted later is a miss rather than a wrong
answer. The canonical string *is* the key rather than a digest of it: hashing
needs `crypto.subtle`, which a browser withholds outside a secure context —
which is exactly a self-hosted archive on a plain-HTTP address on somebody's own
network.

**`geometry`** is `TrackResponse.geometry_sha256`, the archive's own identity for
the geometry a track currently has. Reprocessing that moves a position changes
it; a title, a note or a kind correction does not. Inventing a second hash in
the browser would be a second answer to a question the archive already answers.

**`segments`** is beside it, not instead of it. `geometry_sha256` names the
*recording* — positions and instants — and deliberately leaves out where the
source put its breaks, because a route export of a paused ride is the same
afternoon. A minimap draws one line per segment, so the same recording flattened
into one run and split into three are two different pictures. What is left is
narrow and cosmetic: two exports of one recording split differently into the
*same* number of runs.

**`maps`** is the ordered `delivery_id` of every package the coverage answer
reported, which is the content hash of each. A region installed again from newer
data is a new hash, so the picture drawn over the old package is simply never
found again. Nothing has to hunt for it.

**`style`** and **`render`** are `BASEMAP_STYLE_VERSION` in `web/src/map/style.ts`
and `MINIMAP_RENDER_VERSION` in `web/src/map/minimap.ts`. They stand for the code
that decides what a map looks like: a palette, a layer, a filter, a line width,
a font. **Change any of those and bump the constant in the same commit** —
nothing else can tell that a kept picture is no longer one this build would
produce. They are deliberately constants rather than a digest of the source,
which would change for a renamed local and stay the same for a colour moved
between the two files.

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

### What is deliberately not cached

The interactive map on a track's own page. It stays a live MapLibre map because
zoom, pan, hover and the chart coupling are what it is for, and the immutable
tile cache already covers what it costs to draw. There is no service worker and
no offline synchronisation: this caches one derived picture, and nothing else.
