# ADR 0010: Offline map packages and local basemap delivery

## Status

Accepted (2026-08-09)

Supersedes the basemap paragraph of ADR 0008 and the `GPX_VIEW_MAP_STYLE_URL`
arrangement it introduced. Everything else those decisions made — the track
overlay, the sample identity, the profile — is untouched.

## Context

A track drawn on a neutral grey rectangle is a shape, not a map. Until now the
only way to get a background under it was `GPX_VIEW_MAP_STYLE_URL`: an operator
points the page at somebody else's tile service, and from then on every view of
a private recording tells that service where the owner walked, one tile request
at a time. `no-referrer` hides which archive is asking. It does not hide the
coordinates, because the coordinates *are* the request.

That is the wrong shape for a self-hosted archive whose entire premise is that
the movement data stays on the owner's machine. So:

> GPX-View stores a regional map locally and serves it itself. After a package
> is installed, viewing a track makes no external request of any kind.

The network is reached exactly three times in a package's life: refreshing the
catalog, installing, updating. Nothing else.

### What the candidates actually offer

Four routes were examined against primary sources, not summaries.

**A — Geofabrik Shortbread MBTiles, used directly.** Geofabrik publishes a
ready-made vector tile package per region, conforming to the
[Shortbread](https://shortbread-tiles.org/) schema, as a single MBTiles file. A
probe of every region in Geofabrik's own machine-readable index found **356 of
555 regions** offer one, including every German state, every Dutch province,
every Spanish autonomous community, Belgium, Austria and Switzerland. Sizes run
from 1.7 MB (Monaco) to a few gigabytes. The files are rebuilt daily.

Reading one confirms it is not a stub. `europe/monaco-shortbread-1.0.mbtiles`
carries `format=pbf`, `minzoom=0`, `maxzoom=14`, a `json` metadata value listing
26 vector layers, `bounds`, and — decisively —

```
author  = OpenStreetMap contributors, Geofabrik GmbH
license = Open Database License 1.0
```

Attribution and licence are **inside the package**. Decoding tiles by zoom shows
streets from z8, street labels from z10, buildings, water, land cover, place
labels and points of interest at z14. Tiles are gzipped MVT; `tile_row` is TMS.

**B — the same, converted to PMTiles.** Adds a conversion tool, an install-time
CPU and disk cost proportional to the package, and an HTTP Range contract, in
exchange for a delivery model that solves a problem GPX-View does not have. The
argument for PMTiles is serving tiles from object storage that cannot run code.
GPX-View is code.

**C — Protomaps, extracted regionally.** The planet build is 137 GB. Regional
extraction is a client-side operation the `pmtiles` CLI performs over thousands
of HTTP Range requests against the build server, which would mean shipping a Go
binary in the runtime image and making a stranger's bandwidth part of our
install path — against the explicit preference stated in the Protomaps
documentation.

**D — build tiles from `.osm.pbf`.** Planetiler or OpenMapTiles over a Geofabrik
extract. A JVM, gigabytes of RAM, hours of CPU, temporary disk several times the
output, and ownership of a tile schema. It is the answer when A–C have a
blocker. They do not.

### Decision matrix

| | A Geofabrik Shortbread | B A → PMTiles | C Protomaps extract | D build from PBF |
| --- | --- | --- | --- | --- |
| Licence clarity | ODbL 1.0, stated in the file | same | ODbL, stated in docs | ODbL, ours to state |
| Attribution clarity | in package metadata | preserved by hand | in docs | ours to state |
| Free download | yes | yes | yes | yes |
| Region availability | 356 regions, provider-defined | same | any bbox | any extract |
| Operational complexity | download a file | download, convert | run an extractor | run a tile builder |
| Runtime dependencies | none — stdlib `sqlite3` | PMTiles JS in the browser | Go binary | JVM + toolchain |
| Install complexity | one transfer | transfer + conversion | thousands of range requests | hours of compute |
| Update complexity | re-download | re-download + re-convert | re-extract | rebuild |
| Integrity verification | our SHA-256 | our SHA-256 | b3sum published | n/a |
| MapLibre integration | our MVT endpoint | PMTiles protocol | PMTiles protocol | our endpoint |
| Offline capability | complete | complete | complete | complete |
| Local labels/fonts | ours to ship | ours to ship | ours to ship | ours to ship |
| Docker integration | nothing added | conversion stage | binary stage | build toolchain |
| Provider independence | port; another provider is an adapter | same | same | highest |

## Decision

### 1. Geofabrik Shortbread 1.0 MBTiles, served as MVT from this process

Candidate A. One file per region, no conversion, no new runtime dependency, and
delivery from the same origin as everything else GPX-View serves.

```
Geofabrik regional Shortbread package
        │  once, at install
        ▼
/data/maps/packages/<key>/<sha256>.mbtiles
        │
        ▼
GET /api/v1/maps/tiles/<sha256>/{z}/{x}/{y}.mvt      same origin
        │
        ▼
MapLibre  ──  local style, local glyphs, GPX-View track overlay
```

No PostGIS, no TileServer GL, no Martin, no second nginx. A read-only SQLite
file answering an indexed three-column lookup is what a tile server *is*; adding
a container to perform it would be adding an operational dependency to avoid
writing thirty lines.

**The risks, stated rather than glossed.** Geofabrik marks the vector tile
package `experimental` on its own region pages. The URLs are not in
`index-v1.json` — only the `.osm.pbf` ones are — so the package URL is
constructed from the indexed region path plus the documented suffix and
confirmed with a `HEAD`, which is a convention, not a contract. No provider
checksum is published, so the content hash is ours and proves our copy is
intact, never provenance. And 199 regions have no package at all — including
`europe/germany` and `europe/netherlands` as wholes, which are only reachable
through their subregions.

Each of those is survivable because the provider sits behind a port. A second
provider is an adapter and a row in the catalog, not a change to anything above
it.

### 2. Maps are a capability of their own, not part of the track domain

`domain/maps/`, `application/maps/`, `infrastructure/maps/`. Nothing in
`NormalizedTrack`, `TrackAnalysis` or the statistics knows a map exists. The
only thing the two capabilities share is a bounding box, and it travels as
`MapBounds` rather than as a track.

### 3. A map package is a replaceable external dataset, not source evidence

This is the distinction the whole storage model turns on:

```
raw GPX import    private user source authority   irreplaceable, immutable,
                                                  never overwritten, corruption
                                                  fails closed and is preserved

map package       external reference dataset      replaceable, re-downloadable,
                                                  corruption is discarded and
                                                  the package reinstalled
```

Applying the raw-import ceremony to a map would be a category error in the
expensive direction: refusing to overwrite a corrupt basemap preserves evidence
of nothing, because the bytes came from a public server that still has them.

What *is* kept from that model is the integrity rule. An installed package is
two facts — a database row **and** a managed file that hashes to what the row
says. Either one alone is `INVALID`, never `INSTALLED`.

### 4. Attribution is package metadata and travels with the package

`MapAttribution` is a structured value read out of the package at install time
and stored beside it:

```
MapAttribution
├── data_owner          "OpenStreetMap contributors"     from mbtiles `author`
├── provider            "Geofabrik GmbH"                 from the provider adapter
├── license_identifier  "ODbL-1.0"                       from mbtiles `license`
├── license_name        "Open Database License 1.0"
├── required_text       "Map data © OpenStreetMap contributors"
└── links               structured (label, https URL) pairs — never markup
```

A package whose metadata states no licence and no author does not install. There
is no fallback string, because a hard-coded "© OpenStreetMap contributors" would
be a claim this build makes about a file it did not read.

Links are `(label, url)` pairs validated to `https://`, rendered as anchors.
Remote metadata never reaches `dangerouslySetInnerHTML`.

### 5. Region identity is provider-scoped and never a path

```
geofabrik:europe/germany/nordrhein-westfalen
```

Validated on construction: known provider, one to six segments of
`[a-z0-9][a-z0-9-]*`, no `.`, no `..`, no empty segment. The managed storage
directory is **not** derived from it by string substitution but from its SHA-256,
so even a validation defect cannot express a path. Nothing below `/data/maps`
is ever named by anything a caller sent.

### 6. The catalog is a cache, never a runtime dependency

Geofabrik's `index-v1-nogeom.json` gives region ids, display names, the parent
hierarchy and ISO codes. It is fetched on demand, validated, and written to
`/data/maps/catalog/geofabrik.json`. Reading a track never touches it. Provider
downtime shows "catalog currently unavailable" in the map manager and changes
nothing about installed maps.

### 7. Installation is atomic, and a failed update keeps the old map

```
resolve → preflight (size, free space) → stream to .part (hashing as it goes)
        → validate the container and the schema → fsync
        → publish the file → publish the row in one transaction → INSTALLED
```

The new package is downloaded and validated **beside** the installed one, never
over it. Free-space preflight therefore requires old + new simultaneously. A
failure at any step leaves the previous package current and the `.part` file
removed. A byte-identical re-download answers `ALREADY_CURRENT` and switches
nothing.

### 8. Delivery URLs carry the content hash

`/api/v1/maps/tiles/{sha256}/{z}/{x}/{y}.mvt` — immutable by construction, so
tiles are `Cache-Control: immutable` for a year and an update is a new URL
rather than a cache somebody has to clear. The hash is matched against the
database; it is never a path component.

Stored tiles are gzipped MVT and are served **as stored**, with
`Content-Encoding: gzip`. Nothing is decompressed and recompressed on the way
through.

A missing tile is `204 No Content`, which is what MapLibre expects for a hole in
coverage and is not an error.

### 9. Coverage selection is an application query, not a UI decision

`SelectMapCoverage` takes a bounding box and the installed packages:

| Situation | Answer |
| --- | --- |
| several packages fully cover it | the smallest one |
| only a parent covers it | the parent |
| no single package covers it | every intersecting package, minus any contained in another already chosen |
| nothing intersects | nothing — the track draws on a neutral background |

The containment rule is what stops Germany-and-NRW drawing the same street
twice, and the intersecting set is what lets a track that crosses the Dutch
border keep a basemap on both sides.

### 10. Style, glyphs and sprites are local, and the style is composed in the browser

The API answers **coverage**, not a style document: sources, delivery URLs,
bounds, zoom range and attribution. The browser composes the MapLibre style from
it. Selection is business and belongs to the backend; colour and line width are
presentation and belong to the page, and a style JSON assembled in Python would
put a design system in the API layer.

Two themes plus none: **Outdoor**, **Light**, **No basemap**. Outdoor
prioritises what a track needs behind it — paths, tracks, cycleways, minor
roads, water, forest, then the road hierarchy — and keeps everything muted
enough that the overlay stays the brightest thing on screen.

It is called Outdoor and not Topographic on purpose. Shortbread carries no
contours, no hillshade and no elevation model, so a topographic label would be a
promise the data cannot keep. Terrain is a possible later extension and is out
of scope here.

Glyphs are SDF font ranges generated from **Noto Sans** (SIL OFL 1.1) by
`scripts/generate_glyphs.py` and committed under `web/public/fonts/`, so they
ship in the built page and therefore in the image. The committed ranges cover
Latin-1, Latin Extended-A and -B and general punctuation, which is what Western
European place and street names need. No sprite sheet is shipped: no layer in
either theme uses `icon-image`, so there is no icon licence to audit and nothing
to load.

### 11. Regional maps never enter the image

`/data/maps` is under the existing data root, created `0700`, files `0600`, by
the same non-root runtime user. The image stays identical worldwide; what an
operator installed is in their volume.

Backups distinguish the two halves honestly: the database and the raw imports
are irreplaceable, map packages are re-downloadable. The one caveat is stated —
a package for a dataset date the provider no longer serves is not re-downloadable
in that exact version.

## Rejected alternatives

**Prefetching raster tiles from `tile.openstreetmap.org`.** Bulk download for
offline caching is exactly what the OSMF tile usage policy exists to prevent,
and the same reasoning applies to every community-run server. Not considered
further.

**A separate tile-server container.** Measured against what it would replace:
thirty lines reading an indexed SQLite table. It would add an image, a port, a
health check and a configuration surface to a self-hosted deployment whose
selling point is that it is one container.

**PostGIS.** Nothing here is a spatial query. Coverage selection compares four
floats per package against four floats, over a table with as many rows as the
user installed regions.

**An arbitrary URL downloader.** The API takes a `region_id`. The provider
adapter resolves the URL, and redirects are followed only within that provider's
declared host allowlist. A caller who can name the host has a server-side
request forgery primitive, and there is no feature here worth that.

**Automatic background updates.** Maps change slowly and a package is hundreds
of megabytes. `Update` is a button.

**HTTP Range resume.** A retry re-downloads. Resume is worth building when
somebody reports a transfer that cannot complete, and not before.

## Package format

```
Shortbread 1.0 in MBTiles
  tiles(zoom_level, tile_column, tile_row, tile_data)   tile_row is TMS
  metadata(name, value)                                 format=pbf, bounds,
                                                        minzoom, maxzoom,
                                                        json.vector_layers,
                                                        author, license
  tile_data                                             gzipped MVT
```

Validation refuses anything that is not this: the SQLite header bytes, both
tables, `format=pbf`, a parsable `bounds` inside ±180/±90, a sane zoom range, at
least one tile, `author` and `license` present, and the Shortbread layer
signature in `json.vector_layers`. A package that validates as a container but
carries an unrecognised schema fails as `map_package_schema_unsupported`, by
name, and the installed package stays current.

## Provider model

`MapCatalogProvider` is a port. Geofabrik is one adapter. It owns the index URL,
the package URL convention, the host allowlist and the display name; the
application knows none of them.

## Storage

```
<data dir>/maps/
├── packages/<sha256-of-region-id>/<content-sha256>.mbtiles
├── downloads/<job-id>.part
└── catalog/geofabrik.json
```

## Serving model

Same-origin, from this process, read-only SQLite, one connection per worker
thread, never the archive's own database file.

## Update model

Explicit. `Update` downloads beside the current package, validates, switches in
one transaction and deletes the old file afterwards. Same bytes → no switch.

## Attribution model

Four surfaces, and the metadata is the same object in all four: the map's own
attribution control, the package card in the map manager, `/credits`, and
`THIRD_PARTY_NOTICES.md` in the image.

## Offline contract

With a package installed and the network unplugged: dashboard, track list, track
detail, elevation profile, speed profile, track overlay, basemap, labels and
attribution all work. A Playwright test aborts every request that is not
same-origin and asserts exactly that.

## Security boundary

| Input | Treatment |
| --- | --- |
| region id from a caller | validated value object, never a path |
| provider URL | resolved by the adapter, never accepted from a caller |
| redirects | followed only to hosts the provider declares |
| response body | streamed to `.part`, hashed while streaming, hard byte ceiling |
| downloaded MBTiles | foreign SQLite: opened read-only, no extensions, no SQL from its content |
| package metadata | parsed and validated; links must be `https://`; rendered as text |
| tile path | `{sha256}` matched against the database, `{z}/{x}/{y}` range-checked |

## Known limitations

- 199 Geofabrik regions have no package, including several countries as wholes.
- The package URL is a documented convention rather than an index entry.
- No provider checksum, so the hash proves integrity and not authenticity.
- No download resume; an interrupted transfer is retried whole.
- Cancellation stops the transfer at the next chunk boundary; a job already in
  `publishing` runs to completion.
- No contours, hillshade or terrain.
- Glyph coverage is Latin. A package whose labels are Greek, Cyrillic or CJK
  renders those labels as blanks until the committed ranges are extended.
