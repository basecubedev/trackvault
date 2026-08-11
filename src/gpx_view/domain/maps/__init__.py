"""Offline map packages: a capability of its own, beside the track model.

A map is not part of a track. `NormalizedTrack`, `TrackAnalysis` and the
statistics know nothing about this package, and nothing here reads a track. The
one thing the two capabilities share is a rectangle, and it travels as
`MapBounds`.

The vocabulary:

```
MapRegionId     which region, scoped to the catalog that offers it
MapBounds       the rectangle a region or a track occupies
MapAttribution  who made the data, under what licence, in what words
MapTileSchema   which vector tile vocabulary the tiles speak
MapPackage      one installed regional map and everything explaining it
MapInstallState what a region amounts to right now
MapJobState     where an installation has got to
select_coverage which installed packages belong behind a rectangle
```
"""

from gpx_view.domain.maps.attribution import AttributionLink, MapAttribution
from gpx_view.domain.maps.bounds import MapBounds
from gpx_view.domain.maps.coverage import select_coverage
from gpx_view.domain.maps.identity import MapRegionId
from gpx_view.domain.maps.package import (
    MapInstallState,
    MapJobState,
    MapPackage,
    MapPackageFormat,
    MapTileSchema,
)

__all__ = [
    "AttributionLink",
    "MapAttribution",
    "MapBounds",
    "MapInstallState",
    "MapJobState",
    "MapPackage",
    "MapPackageFormat",
    "MapRegionId",
    "MapTileSchema",
    "select_coverage",
]
