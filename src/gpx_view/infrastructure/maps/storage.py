"""The managed area under the data directory that holds map packages.

```
<data dir>/maps/
├── packages/<region-key>/<content-sha256>.mbtiles
├── downloads/<job-id>.part
└── catalog/<provider>.json
```

Two names decide where anything lands, and **neither of them comes from a
caller**. The directory is `MapRegionId.storage_key`, a SHA-256 of the validated
identity; the file is the content hash of the bytes themselves, checked against
64 hexadecimal digits before it is used. There is no code path from a string a
provider or an HTTP client sent to a path component.

Permissions follow the archive's own rule: directories `0700`, files `0600`,
stated to the call that creates the object. A regional basemap is not private
movement data, but it sits inside a directory that holds nothing else, and one
rule for the whole data directory is a rule people can check.

Unlike the raw import store this is **not** an evidence boundary. A corrupt map
package is deleted and downloaded again; the bytes are still on a public server.
Applying immutable-evidence semantics here would mean refusing to repair a
basemap in order to preserve proof of a disk error nobody will read.
"""

import hashlib
import logging
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

from gpx_view.application.maps import MapDownloadSlot, MapErrorCode, MapOperationError
from gpx_view.domain.maps import MapRegionId
from gpx_view.infrastructure.private_data import (
    PRIVATE_FILE_MODE,
    create_private_directory,
)

logger = logging.getLogger(__name__)

PACKAGES_DIRECTORY = "packages"
DOWNLOADS_DIRECTORY = "downloads"
CATALOG_DIRECTORY = "catalog"
PACKAGE_SUFFIX = ".mbtiles"
PARTIAL_SUFFIX = ".part"

HASH_LENGTH = 64
HEX_DIGITS = frozenset("0123456789abcdef")

READ_CHUNK_BYTES = 1024 * 1024


def _require_hash(value: str) -> str:
    """Return a content hash, or refuse it before it becomes a file name."""
    if len(value) != HASH_LENGTH or not set(value) <= HEX_DIGITS:
        raise MapOperationError(MapErrorCode.MAP_STORAGE_FAILED, "malformed content hash")
    return value


def _require_job_id(value: str) -> str:
    """Return a job identity, or refuse it before it becomes a file name."""
    if not value or len(value) > 64 or not set(value) <= HEX_DIGITS:
        raise MapOperationError(MapErrorCode.MAP_STORAGE_FAILED, "malformed job identity")
    return value


class FilesystemMapPackageStorage:
    """Managed map package storage rooted at one directory."""

    def __init__(self, root: Path) -> None:
        """Bind the storage to its root. Nothing is created until it is used.

        The root is made absolute here, once. `GPX_VIEW_DATA_DIR` defaults to a
        *relative* path, and a relative path is resolved against whatever the
        working directory happens to be at the moment it is used -- which for a
        server that runs for weeks is not a property worth relying on. It is
        also not expressible as the file URI the package reader needs, so a
        relative root made every install fail after the whole file had already
        been downloaded.

        `resolve` rather than `absolute`: the configured root may legitimately
        be a symbolic link an operator pointed somewhere, exactly as the managed
        raw storage allows, and following it once at construction is what makes
        every path below it stable afterwards.
        """
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        """Return the managed root, for the composition root to report."""
        return self._root

    def prepare(self) -> None:
        """Create the managed directories, private from creation."""
        create_private_directory(self._root)
        for name in (PACKAGES_DIRECTORY, DOWNLOADS_DIRECTORY, CATALOG_DIRECTORY):
            create_private_directory(self._root / name)

    def catalog_directory(self) -> Path:
        """Return where cached provider catalogs live."""
        return self._root / CATALOG_DIRECTORY

    def package_path(self, region_id: MapRegionId, content_sha256: str) -> Path:
        """Return the managed file one package's bytes live in.

        Not part of the port. The tile reader and the inspector are the only
        callers, and they are in this package -- nothing above infrastructure
        ever learns a path.
        """
        return (
            self._root
            / PACKAGES_DIRECTORY
            / region_id.storage_key
            / f"{_require_hash(content_sha256)}{PACKAGE_SUFFIX}"
        )

    def open_download_slot(self, job_id: str) -> MapDownloadSlot:
        """Create a private temporary file for one transfer."""
        self.prepare()
        path = self._root / DOWNLOADS_DIRECTORY / f"{_require_job_id(job_id)}{PARTIAL_SUFFIX}"
        # `O_EXCL` rather than truncation: a `.part` that already exists belongs
        # to a job that is either running or was interrupted, and quietly
        # writing over it would make two transfers share a file.
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            PRIVATE_FILE_MODE,
        )
        return _DownloadSlot(path, os.fdopen(descriptor, "wb"))

    def publish(self, region_id: MapRegionId, content_sha256: str, slot: MapDownloadSlot) -> None:
        """Move a finished download into the managed area atomically."""
        if not isinstance(slot, _DownloadSlot):  # pragma: no cover - wiring guard
            raise MapOperationError(MapErrorCode.MAP_STORAGE_FAILED, "unknown download slot")
        destination = self.package_path(region_id, content_sha256)
        try:
            create_private_directory(destination.parent)
            # Same file system by construction -- both are under the managed
            # root -- so the rename is atomic and a crash leaves either the old
            # file or the new one, never half of either.
            slot.path.replace(destination)
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as error:
            slot.discard()
            raise MapOperationError(MapErrorCode.MAP_STORAGE_FAILED) from error

    def remove(self, region_id: MapRegionId, content_sha256: str) -> None:
        """Delete one managed package file. A missing file is not an error."""
        path = self.package_path(region_id, content_sha256)
        path.unlink(missing_ok=True)
        self._prune(path.parent)

    def remove_region(self, region_id: MapRegionId) -> None:
        """Delete everything managed for a region."""
        directory = self._root / PACKAGES_DIRECTORY / region_id.storage_key
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)

    def exists(self, region_id: MapRegionId, content_sha256: str) -> bool:
        """Report whether the managed file exists, without reading it."""
        return self.package_path(region_id, content_sha256).is_file()

    def is_intact(self, region_id: MapRegionId, content_sha256: str) -> bool:
        """Report whether the managed file exists and hashes to its name.

        The expensive check. It is what publication is followed by and what a
        suspected-damage path asks; a listing asks :meth:`exists` instead,
        because re-hashing a gigabyte to draw a table is a cost with no reader.
        """
        path = self.package_path(region_id, content_sha256)
        if not path.is_file():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(READ_CHUNK_BYTES):
                digest.update(chunk)
        return digest.hexdigest() == content_sha256

    def free_bytes(self) -> int:
        """Return the space available where packages are stored."""
        self.prepare()
        return shutil.disk_usage(self._root).free

    def discard_partial_downloads(self) -> int:
        """Remove leftover temporary files. Returns how many were removed."""
        directory = self._root / DOWNLOADS_DIRECTORY
        if not directory.is_dir():
            return 0
        removed = 0
        for candidate in directory.glob(f"*{PARTIAL_SUFFIX}"):
            candidate.unlink(missing_ok=True)
            removed += 1
        return removed

    def discard_orphans(self, keep: Sequence[tuple[MapRegionId, str]]) -> int:
        """Remove managed files no installed package claims."""
        directory = self._root / PACKAGES_DIRECTORY
        if not directory.is_dir():
            return 0
        wanted = {self.package_path(region_id, digest) for region_id, digest in keep}
        removed = 0
        for region_directory in sorted(directory.iterdir()):
            if not region_directory.is_dir():
                continue
            for candidate in sorted(region_directory.iterdir()):
                if candidate.is_file() and candidate not in wanted:
                    candidate.unlink(missing_ok=True)
                    removed += 1
            self._prune(region_directory)
        return removed

    def _prune(self, directory: Path) -> None:
        """Remove a package directory once nothing is left in it."""
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            logger.warning("map_storage.prune_failed")


class _DownloadSlot:
    """One `.part` file, open for writing, until it is published or discarded."""

    def __init__(self, path: Path, handle: BinaryIO) -> None:
        """Hold the open temporary file."""
        self.path = path
        self._handle = handle

    @property
    def sink(self) -> BinaryIO:
        """Return the open, writable file."""
        return self._handle

    def finish(self) -> None:
        """Flush and fsync, so what is published survives a power cut."""
        if self._handle.closed:
            return
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()

    def discard(self) -> None:
        """Remove the temporary file. Safe to call more than once."""
        if not self._handle.closed:
            self._handle.close()
        self.path.unlink(missing_ok=True)
