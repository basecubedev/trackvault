"""Which catalog regions could hold a track, when nothing is installed for it.

```
walk on Mallorca, nothing installed   ->  Islas Baleares, then Spain, then Europe
walk in Aachen                        ->  Limburg, Köln, Nordrhein-Westfalen, ...
walk over a border                    ->  both sides, because neither holds it
nothing in the catalog reaches it     ->  nothing
```

**This is the weaker sibling of `select_coverage`, and the difference matters.**
Coverage selection decides what to *draw*, and it is allowed to decide: an
installed package's extent is the extent of data this archive actually holds.
A catalog extent is not that. It is a rectangle around a region's outline, and
a rectangle around the Netherlands contains Aachen.

So this does not decide. It names the regions whose declared extent reaches the
track, most specific first, and the person who knows where they walked picks
one. Answering with a single confident region would be the archive guessing --
and a wrong guess here costs somebody a gigabyte and leaves them with a map
their track is not on.

The track's own rectangle is used, unpadded. Coverage pads its query because a
reader pans around a track and the basemap has to keep going; that is a question
about a viewport. This is a question about which region a track is *in*, and
growing the rectangle first would push a track near an edge out of the small
region that holds it and into the continent that contains them both.

Ordering is by extent, smallest first, with the region identity breaking ties --
the same rule coverage selection uses, for the same reason: two suggestions that
swap places between two page loads are a defect nobody can reproduce.

**An extent that wraps the globe is not an extent.** `MapBounds` deliberately
does not model the antimeridian, so a region whose outline crosses it -- New
Zealand, Fiji, Alaska, Russia, and every continent containing one of them --
reduces to a rectangle spanning all 360 degrees of longitude. Fourteen of the
555 regions Geofabrik publishes are like that. Such a rectangle contains every
track on Earth, so left in, those fourteen would be offered for a walk in
Bavaria. They are dropped instead: a suggestion that cannot be wrong about
anything cannot be right about anything either. The cost is stated rather than
hidden -- a track in New Zealand is offered nothing, which is what it was
offered before this existed.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from gpx_view.domain.maps.bounds import MapBounds
from gpx_view.domain.maps.identity import MapRegionId

MAX_USEFUL_SPAN_DEGREES = 180.0
"""How wide an extent may be and still single anything out.

Half the globe. Above it the rectangle is not describing a region any more, it
is describing an outline that crossed the antimeridian, and it would match every
track everywhere. The real catalog has a clean gap here: the widest honest
extent is well under this, and everything above it spans the full 360.
"""


@dataclass(frozen=True, slots=True)
class RegionExtent:
    """One catalog region's identity and the rectangle its provider declares.

    Attributes:
        region_id: The provider-scoped identity, which is all an install needs.
        bounds: What the provider says the region occupies. Evidence about where
            a download would be useful, never a statement about a border.
    """

    region_id: MapRegionId
    bounds: MapBounds


def suggest_regions(track: MapBounds, extents: Iterable[RegionExtent]) -> tuple[MapRegionId, ...]:
    """Return the regions that could hold ``track``, most specific first.

    Args:
        track: The rectangle the track occupies.
        extents: Every region a catalog offers, in any order.

    Returns:
        The candidates, most specific first. A region whose extent contains the
        whole track is preferred over one that merely reaches into it -- half a
        map is worse than a bigger whole one -- and when nothing contains the
        track, everything it crosses is named, because a track over a border
        needs both sides. Empty is a normal answer.
    """
    usable = [extent for extent in extents if _distinguishes_anything(extent.bounds)]
    reaching = [extent for extent in usable if extent.bounds.intersects(track)]
    containing = [extent for extent in reaching if extent.bounds.covers(track)]
    return _by_specificity(containing or reaching)


def _distinguishes_anything(bounds: MapBounds) -> bool:
    """Report whether an extent narrows anything down at all."""
    return bounds.max_longitude - bounds.min_longitude <= MAX_USEFUL_SPAN_DEGREES


def _by_specificity(extents: Sequence[RegionExtent]) -> tuple[MapRegionId, ...]:
    """Return the identities ordered smallest-extent first, then by identity."""
    return tuple(
        extent.region_id
        for extent in sorted(extents, key=lambda entry: (entry.bounds.area, str(entry.region_id)))
    )
