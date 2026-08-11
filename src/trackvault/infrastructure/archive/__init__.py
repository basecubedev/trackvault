"""The archive container adapter: a portable ``tar.gz`` and its manifest.

Everything about *how* an archive is stored lives here -- the container, the safe
extraction, the staging and the atomic publish. What an archive *means* --
its manifest, its versioning and the rule about what may be restored -- belongs
to :mod:`trackvault.application.archive`, and nothing in this package decides it.
"""

from trackvault.infrastructure.archive.container import (
    ARCHIVE_SUFFIX,
    FilesystemArchiveBuilder,
    FilesystemArchiveExtractor,
)
from trackvault.infrastructure.archive.manifest_codec import decode_manifest, encode_manifest
from trackvault.infrastructure.archive.publication import (
    RESTORE_MARKER_NAME,
    RestoreRecovery,
    recover_interrupted_restore,
    restore_is_pending,
)

__all__ = [
    "ARCHIVE_SUFFIX",
    "RESTORE_MARKER_NAME",
    "FilesystemArchiveBuilder",
    "FilesystemArchiveExtractor",
    "RestoreRecovery",
    "decode_manifest",
    "encode_manifest",
    "recover_interrupted_restore",
    "restore_is_pending",
]
