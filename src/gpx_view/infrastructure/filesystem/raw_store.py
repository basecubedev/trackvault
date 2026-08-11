"""Content-addressed storage for the original import files.

The archive keeps every accepted import byte-identically, under its own data
directory, so that a parser or classifier upgrade can always start from the
source again.

The canonical path is derived from the content hash and from nothing else:

```
<root>/sha256/ab/abcdef....raw
```

Two invariants make this an authority rather than a naming convention.

**Content-addressed means content-verified.** A path named after a digest is only
valid while its bytes actually have that digest. The store therefore hashes what
it is given before writing it, hashes what it finds before returning it, and
refuses a mismatch instead of overwriting or quietly repairing one. Corrupted
source evidence must never become the input of a reprocessing.

**Nothing below the root escapes the root.** Every path component under the
storage root is opened with ``O_NOFOLLOW``, and every subsequent operation works
on the resulting directory descriptor rather than on the path. Checking a path
and then writing to it leaves a window in which the path can be swapped;
operating on a descriptor closes it.

### The threat boundary this guarantees

The configured root -- ``GPX_VIEW_DATA_DIR`` and below -- is operator
configuration and is trusted: it may itself be reached through a symbolic link,
because an operator who points the data directory somewhere means it. What is
*not* trusted is anything inside the root, since that is where the archive's own
artifacts and any accident or tampering below them live. A symbolic link planted
on the fan-out directory, on the artifact itself, or anywhere between, is refused.

This is a local integrity boundary, not a sandbox. A process running as the same
user can still replace the whole data directory, and no file system trick can
prevent that; what it cannot do is make the archive write outside its own root or
hand out bytes that do not match the hash they are filed under.
"""

import hashlib
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

from gpx_view.application import ImportErrorCode, TrackImportError
from gpx_view.application.ports import RawArtifactState
from gpx_view.infrastructure.private_data import (
    PRIVATE_DIRECTORY_MODE,
    PRIVATE_FILE_MODE,
    create_private_directory,
)

logger = logging.getLogger(__name__)

SHA256_HEX_LENGTH = 64
FANOUT_LENGTH = 2
ARTIFACT_SUFFIX = ".raw"
TEMPORARY_SUFFIX = ".part"

_HEX_DIGITS = frozenset("0123456789abcdef")

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY


class FilesystemRawImportStore:
    """Keeps original import bytes under a content-addressed directory tree."""

    def __init__(self, root: Path) -> None:
        """Point the store at its root directory. Nothing is created until a write."""
        self._root = root

    @property
    def root(self) -> Path:
        """Return the directory this store owns."""
        return self._root

    def path_for(self, sha256: str) -> Path:
        """Return the canonical path of an artifact.

        This is the artifact's *name*. Containment is enforced when the store
        opens it, because a path is only a description of where something should
        be and says nothing about what is there now.

        Raises:
            TrackImportError: ``raw_storage_failed`` if the hash is not a SHA-256
                digest. The hash is the only caller-supplied part of the path, so
                it is checked before it is used.
        """
        _require_digest(sha256)
        return self._root / "sha256" / sha256[:FANOUT_LENGTH] / f"{sha256}{ARTIFACT_SUFFIX}"

    def integrity(self, sha256: str) -> RawArtifactState:
        """Report what the managed copy of one raw import currently is.

        There is no cheaper honest answer. A file of the right name proves that a
        file of that name exists, which is exactly the assumption that let a
        database row stand in for the bytes it describes.
        """
        try:
            content = self._stored_content(sha256)
        except FileNotFoundError:
            # The fan-out directory does not exist yet, so nothing is filed
            # under this hash. That is absence, not damage.
            return RawArtifactState.MISSING
        except OSError:
            return RawArtifactState.UNREADABLE
        except TrackImportError:
            # An unusable hash names nothing, so there is nothing filed under it.
            return RawArtifactState.MISSING
        if content is None:
            return RawArtifactState.MISSING
        if hashlib.sha256(content).hexdigest() != sha256:
            return RawArtifactState.CORRUPT
        return RawArtifactState.HEALTHY

    def read(self, sha256: str) -> bytes:
        """Return the stored bytes, so a raw import can be reprocessed.

        The three ways this can go wrong are three different problems for whoever
        has to fix them, so they answer three different codes: bytes that were
        never there, bytes that are not what they claim to be, and bytes that
        could not be read at all.

        Raises:
            TrackImportError: ``raw_storage_missing``, ``raw_storage_corrupt`` or
                ``raw_storage_failed``.
        """
        _require_digest(sha256)
        try:
            content = self._stored_content(sha256)
        except FileNotFoundError as error:
            raise TrackImportError(
                ImportErrorCode.RAW_STORAGE_MISSING, "no artifact is stored under that hash"
            ) from error
        except OSError as error:
            raise TrackImportError(
                ImportErrorCode.RAW_STORAGE_FAILED, "stored artifact is unreadable"
            ) from error
        if content is None:
            raise TrackImportError(
                ImportErrorCode.RAW_STORAGE_MISSING, "no artifact is stored under that hash"
            )
        if hashlib.sha256(content).hexdigest() != sha256:
            raise TrackImportError(
                ImportErrorCode.RAW_STORAGE_CORRUPT,
                "stored artifact does not match its content hash",
            )
        return content

    def _stored_content(self, sha256: str) -> bytes | None:
        """Return the bytes filed under a hash, or ``None`` if there are none."""
        _require_digest(sha256)
        with self._managed_directory(sha256, create=False) as directory:
            return _read_artifact(directory, _artifact_name(sha256))

    def store(self, content: bytes, sha256: str) -> None:
        """Put the bytes in place atomically, or leave nothing behind.

        Storing content that is already present is a no-op: exact duplicates
        share one artifact. "Already present" means the stored bytes hash to the
        same digest, not merely that a file of that name exists.

        The hash is verified against the content even though the import use case
        computes it correctly. An invariant that holds only because of who
        happens to call the port is not an invariant.

        An artifact that is already there and already correct is adopted rather
        than written again. That is what makes an orphaned artifact -- bytes
        installed by an import that crashed before its database transaction
        committed -- recoverable: the content is proven identical, so there is
        nothing to decide and nothing to duplicate.

        Raises:
            TrackImportError: ``raw_storage_corrupt`` if an artifact of that name
                holds different bytes; ``raw_storage_failed`` if the hash is
                unusable, the content does not match it, the managed path leads
                outside the root, or the write could not be completed.
        """
        _require_digest(sha256)
        _require_content_matches(content, sha256, "content does not match the stated hash")
        name = _artifact_name(sha256)

        try:
            with self._managed_directory(sha256, create=True) as directory:
                existing = _read_artifact(directory, name)
                if existing is not None:
                    _require_stored_artifact_matches(existing, sha256)
                    return
                _install(directory, name, content)
        except OSError as error:
            raise TrackImportError(
                ImportErrorCode.RAW_STORAGE_FAILED, "artifact could not be stored"
            ) from error

    @contextmanager
    def _managed_directory(self, sha256: str, *, create: bool) -> Iterator[int]:
        """Yield a descriptor for the directory an artifact belongs in.

        The root is opened by path, because where the data directory lives is
        operator configuration. Every component below it is opened with
        ``O_NOFOLLOW`` relative to its parent's descriptor, so a symbolic link
        anywhere on the managed path fails the open instead of redirecting it.
        """
        descriptors: list[int] = []
        try:
            descriptors.append(self._open_root(create=create))
            for component in ("sha256", sha256[:FANOUT_LENGTH]):
                descriptors.append(_open_subdirectory(component, descriptors[-1], create=create))
            yield descriptors[-1]
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

    def _open_root(self, *, create: bool) -> int:
        """Open the configured storage root, creating it on first write."""
        if create:
            create_private_directory(self._root)
        return os.open(self._root, _DIRECTORY_FLAGS)


def _artifact_name(sha256: str) -> str:
    """Return the file name an artifact carries inside its fan-out directory."""
    return f"{sha256}{ARTIFACT_SUFFIX}"


def _require_digest(sha256: str) -> None:
    """Refuse a hash that could not be a SHA-256 digest before it becomes a path."""
    if len(sha256) != SHA256_HEX_LENGTH or not _HEX_DIGITS.issuperset(sha256):
        raise TrackImportError(
            ImportErrorCode.RAW_STORAGE_FAILED, "content hash is not a sha256 digest"
        )


def _require_content_matches(content: bytes, sha256: str, detail: str) -> None:
    """Refuse content that does not hash to the digest a caller stated.

    The detail stays structural. A mismatch must not tempt anyone into logging
    the bytes that caused it.
    """
    if hashlib.sha256(content).hexdigest() != sha256:
        raise TrackImportError(ImportErrorCode.RAW_STORAGE_FAILED, detail)


def _require_stored_artifact_matches(content: bytes, sha256: str) -> None:
    """Fail closed on an artifact whose bytes are not the ones its name claims.

    This is a different failure from a caller stating a wrong hash. Disk
    corruption, manual tampering and a broken file system all look like this, and
    the artifact is the only trace of any of them -- so it is neither overwritten
    nor repaired, and it gets a code of its own so an operator can tell what they
    are looking at.
    """
    if hashlib.sha256(content).hexdigest() != sha256:
        raise TrackImportError(
            ImportErrorCode.RAW_STORAGE_CORRUPT, "stored artifact does not match its content hash"
        )


def _open_subdirectory(name: str, parent: int, *, create: bool) -> int:
    """Open a directory below the root, refusing to follow a symbolic link."""
    flags = _DIRECTORY_FLAGS | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
    # `mkdir` either creates the directory or loses a race with something that
    # created it first. Either way the open below decides, and it still refuses a
    # symbolic link -- including one planted between these two calls.
    with suppress(FileExistsError):
        os.mkdir(name, PRIVATE_DIRECTORY_MODE, dir_fd=parent)
    return os.open(name, flags, dir_fd=parent)


def _read_artifact(directory: int, name: str) -> bytes | None:
    """Return an existing artifact's bytes, or ``None`` if there is none.

    ``O_NOFOLLOW`` covers the artifact itself: a symbolic link where the archive
    expects its own file is refused rather than followed.
    """
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def _install(directory: int, name: str, content: bytes) -> None:
    """Write the bytes and move them into place atomically.

    The temporary artifact is created in the directory it will be renamed within,
    so the rename cannot cross a file system boundary and stops being atomic. The
    directory is flushed afterwards: the rename decides which bytes an artifact
    has, and a crash must not be able to undo it while keeping the file.

    The temporary file is private from its first byte. It holds the whole
    recording, so a permissive mode on it is the same exposure as a permissive
    mode on the artifact -- for a shorter time, which is not a defence.
    """
    temporary = f"{uuid4().hex}{TEMPORARY_SUFFIX}"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            PRIVATE_FILE_MODE,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        _discard(temporary, directory)
        raise
    os.fsync(directory)


def _discard(name: str, directory: int) -> None:
    """Remove a temporary artifact, without ever masking the failure that caused it.

    Cleanup runs while an import is already failing. If the data directory itself
    is unusable, removing the temporary file fails too -- and that second failure
    must not replace the named error the caller is about to receive.
    """
    try:
        os.unlink(name, dir_fd=directory)
    except OSError:
        logger.warning("raw_storage.cleanup_failed")
