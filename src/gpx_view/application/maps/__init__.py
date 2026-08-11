"""Use cases for offline map packages.

```
GetMapCatalog        what a provider offers, cached so downtime is survivable
InstallMapPackage    the one authority for putting a package on this machine
RemoveMapPackage     deleting one, and nothing else
RecoverMapStorage    what has to be true again after a crash
ListInstalledMaps    what is installed, and whether it can still be proved
SelectMapCoverage    which installed maps belong behind a rectangle
SuggestMapRegions    which regions could be installed for one nothing covers
GetMapInstallJob     where an installation has got to
```

Everything reaches the outside world through the ports in `ports.py`. No module
here knows a URL scheme, a file format or a directory.
"""

from gpx_view.application.maps.catalog import CatalogEntry, CatalogPage, GetMapCatalog
from gpx_view.application.maps.errors import MapErrorCode, MapOperationError
from gpx_view.application.maps.install import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    InstallMapPackage,
    InstallOutcome,
    InstallResult,
    RecoverMapStorage,
    RemoveMapPackage,
    queued_job,
)
from gpx_view.application.maps.ports import (
    CachedCatalog,
    CatalogRegion,
    InspectedPackage,
    InstalledMap,
    MapCatalogCache,
    MapDownloadSlot,
    MapInstallationService,
    MapInstallJob,
    MapPackageInspector,
    MapPackageProvider,
    MapPackageRepository,
    MapPackageStorage,
    MapTileSource,
    RemotePackage,
    TransferOutcome,
)
from gpx_view.application.maps.queries import (
    COVERAGE_PADDING_DEGREES,
    MAX_SUGGESTIONS,
    ApproximateLocation,
    GetMapInstallJob,
    ListInstalledMaps,
    LocatedCountry,
    LocateTracks,
    MapCoverage,
    MapSource,
    SelectMapCoverage,
    SuggestedRegion,
    SuggestMapRegions,
)

__all__ = [
    "COVERAGE_PADDING_DEGREES",
    "DEFAULT_MAX_DOWNLOAD_BYTES",
    "MAX_SUGGESTIONS",
    "ApproximateLocation",
    "CachedCatalog",
    "CatalogEntry",
    "CatalogPage",
    "CatalogRegion",
    "GetMapCatalog",
    "GetMapInstallJob",
    "InspectedPackage",
    "InstallMapPackage",
    "InstallOutcome",
    "InstallResult",
    "InstalledMap",
    "ListInstalledMaps",
    "LocateTracks",
    "LocatedCountry",
    "MapCatalogCache",
    "MapCoverage",
    "MapDownloadSlot",
    "MapErrorCode",
    "MapInstallJob",
    "MapInstallationService",
    "MapOperationError",
    "MapPackageInspector",
    "MapPackageProvider",
    "MapPackageRepository",
    "MapPackageStorage",
    "MapSource",
    "MapTileSource",
    "RecoverMapStorage",
    "RemotePackage",
    "RemoveMapPackage",
    "SelectMapCoverage",
    "SuggestMapRegions",
    "SuggestedRegion",
    "TransferOutcome",
    "queued_job",
]
