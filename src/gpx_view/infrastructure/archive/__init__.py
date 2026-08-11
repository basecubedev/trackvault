"""The archive container adapter: a portable ``tar.gz`` and its manifest.

Everything about *how* an archive is stored lives here -- the container, the safe
extraction, the staging and the atomic publish. What an archive *means* --
its manifest, its versioning and the rule about what may be restored -- belongs
to :mod:`gpx_view.application.archive`, and nothing in this package decides it.
"""

from gpx_view.infrastructure.archive.container import (
    ARCHIVE_SUFFIX,
    FilesystemArchiveBuilder,
    FilesystemArchiveExtractor,
)
from gpx_view.infrastructure.archive.manifest_codec import decode_manifest, encode_manifest

__all__ = [
    "ARCHIVE_SUFFIX",
    "FilesystemArchiveBuilder",
    "FilesystemArchiveExtractor",
    "decode_manifest",
    "encode_manifest",
]
