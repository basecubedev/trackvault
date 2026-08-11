"""GPX adapter: the first concrete exchange format.

Everything GPX-specific lives in this package. XML elements, namespaces and
version quirks stop here; what leaves is the normalized track model plus neutral
evidence.
"""

from gpx_view.infrastructure.gpx.exporter import (
    GPX_EXPORT_VERSION,
    GPX_FILE_EXTENSION,
    GpxDocumentWriter,
)
from gpx_view.infrastructure.gpx.importer import GPX_MEDIA_TYPE, GpxImporter

__all__ = [
    "GPX_EXPORT_VERSION",
    "GPX_FILE_EXTENSION",
    "GPX_MEDIA_TYPE",
    "GpxDocumentWriter",
    "GpxImporter",
]
