"""Adapters for the offline map capability.

```
storage.py        the managed area under the data directory
mbtiles.py        reading and validating a package container
transfer.py       the one place an outbound HTTP request is made
geofabrik.py      one provider: its index, its URL convention, its hosts
catalog_cache.py  the last good catalog, so downtime is survivable
jobs.py           running an installation off the request thread
```

`geofabrik.py` is the only module in the codebase that knows a map provider
exists, exactly as `gpx/extensions.py` is the only one that knows a recording
application's schema. Adding a second provider is another module beside it and a
row in the wiring, and nothing above infrastructure changes.
"""

from gpx_view.infrastructure.maps.catalog_cache import FilesystemMapCatalogCache
from gpx_view.infrastructure.maps.geofabrik import (
    ALLOWED_HOSTS,
    PROVIDER_DISPLAY_NAME,
    PROVIDER_SLUG,
    GeofabrikMapProvider,
)
from gpx_view.infrastructure.maps.jobs import MapInstallJobs
from gpx_view.infrastructure.maps.mbtiles import (
    MbtilesPackageInspector,
    MbtilesTileReader,
    digest_of,
)
from gpx_view.infrastructure.maps.storage import FilesystemMapPackageStorage
from gpx_view.infrastructure.maps.transfer import HttpTransfer, user_agent

__all__ = [
    "ALLOWED_HOSTS",
    "PROVIDER_DISPLAY_NAME",
    "PROVIDER_SLUG",
    "FilesystemMapCatalogCache",
    "FilesystemMapPackageStorage",
    "GeofabrikMapProvider",
    "HttpTransfer",
    "MapInstallJobs",
    "MbtilesPackageInspector",
    "MbtilesTileReader",
    "digest_of",
    "user_agent",
]
