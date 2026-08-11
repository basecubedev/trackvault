# Offline maps

TrackVault downloads a regional map **once** and stores it in your own data
directory. After that, looking at a track draws its background from your own
machine and sends nothing to anybody.

That is the whole point. A map from somebody else's tile service tells that
service where you walked, one request at a time, every time you open a track. No
amount of privacy headers changes that, because the coordinates *are* the
request.

## Why not just use a public tile server

The community-run OpenStreetMap tile servers are paid for by donations and their
usage policy exists to prevent exactly what an offline archive would need: bulk
downloading an area to keep. Commercial providers want an account and a key.
Neither belongs in a self-hosted archive of your own movements, so TrackVault
uses neither.

## Installing a region

Open **Offline maps** in the navigation. Regions come from
[Geofabrik](https://download.geofabrik.de/), which publishes a ready-made vector
map per region built from OpenStreetMap data.

Open a continent, then a country, and press **Download** on the region you want.
The size is shown before you start. Installing answers immediately and the
download runs in the background — you can keep using the dashboard, the track
list and any track while it does.

A few things worth knowing:

- **Not every region has one.** Geofabrik publishes packages for 356 of its 555
  regions. Germany, the Netherlands and Spain are only offered as their states,
  provinces and communities; the list shows "No package" for the rest, rather
  than a button that would fail.
- **Sizes are real.** Monaco is 1.7 MB, North Rhine-Westphalia about 800 MB,
  Bavaria about 1 GB. Check the number before you press the button.
- **Install more than one.** A track that crosses a border draws its basemap
  from both, if both are installed.

## Which region do I need?

You do not have to work that out yourself. A track knows where it went, and the
provider's index says where each of its 555 regions is — so a track with nothing
behind it **names the regions that would cover it**, with a Download button, on
the track's own page and in the list. There is no need to guess which province a
walk was in.

They are candidates, and the page says so. What the archive compares is
rectangles: the box around your track against the box around a region's outline.
That is enough to shortlist and not enough to decide — a box around the
Netherlands contains Aachen, which is in Germany. So several regions are
offered, each with the regions above it (`Limburg — Europe / Netherlands`), and
**you** pick the one you actually walked in.

Two honest limits:

- **A catalog has to have been read once.** Suggesting reaches no provider: it
  reads the catalog this deployment already fetched, because opening a track
  must not contact anybody. Before the first visit to **Offline maps**, the page
  says so instead of guessing.
- **Regions that cross the antimeridian get no suggestion.** New Zealand, Fiji,
  Alaska and Russia reduce to a rectangle spanning the whole globe, which would
  match every track on Earth. They are left out rather than offered everywhere,
  so a track there is offered nothing — as it was before this existed.

Nothing downloads on its own. A region is hundreds of megabytes, and which one
you want is your decision, not a page-load's.

## Where it is stored

```
<data dir>/maps/packages/…    the installed map files
<data dir>/maps/catalog/…     the last catalog that was read
```

Same directory as everything else, same permissions: `0700` directories, `0600`
files, owned by the runtime user. In Docker that is the data volume, so an
installed map survives `docker compose up --force-recreate`. Regional maps are
**never** baked into the image — the image is the same everywhere and what you
installed is yours.

## Updating and removing

Press **Update** when you want newer data. Maps change slowly; every few months
is plenty, and there is deliberately no background updater downloading hundreds
of megabytes while you are not looking.

An update downloads the new package *beside* the one you have and only switches
when the new one has been checked. If the download fails, runs out of disk or
turns out to be broken, **the map you had is still the map you have**.

**Remove** deletes the map and nothing else. Your tracks, titles, corrections
and statistics are untouched, and the region can be downloaded again later.

## What happens offline

With a region installed, unplug the network and everything still works: the
dashboard, the track list and the small maps beside its rows, a track's detail,
its elevation and speed profile, the track itself, the basemap behind it, the
place and street labels on it, and the attribution. `web/e2e/offline-maps.spec.ts`
blocks every request that is not this deployment and asserts exactly that.

The provider is contacted in exactly three situations, all of them something you
pressed: browsing the catalog, installing, updating. If it is unreachable, the
map manager says so and your installed maps carry on.

## Attribution and licence

Map data is **© OpenStreetMap contributors**, under the Open Database License
1.0, packaged by Geofabrik GmbH. That is read out of the package rather than
written into this application, it is shown beside every map that is drawn, and a
package that states no licence is refused rather than installed.

The map label glyphs are Noto Sans, under the SIL Open Font License 1.1.

Both are somebody else's work under somebody else's licence, and neither is
covered by TrackVault's own AGPL-3.0-only. See
[third-party notices](../legal/third-party-notices.md) and the `/credits` page.

## Disk space

Budget for the package plus a little room: the installer refuses to start if the
new map, the one you already have and 256 MB of head-room would not fit, and it
never frees space by deleting a working map to attempt an unproven download.

## Styling

Two themes and none: **Outdoor**, which draws paths, tracks and cycleways as
first-class features with everything else muted, **Light**, which is as plain as
possible, and **No basemap**. It is called Outdoor rather than Topographic
because the data carries no contours, no hillshade and no elevation model, and
promising those would be a lie.
