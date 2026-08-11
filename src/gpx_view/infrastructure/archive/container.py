"""The archive container: a plain ``tar.gz``, written and read carefully.

**Why an ordinary tar.gz.** A backup format only its own application can open is
a backup with a dependency, and the situation you need a backup in is exactly the
one where the application may not run. Any operating system can list and unpack
this, so the worst case -- GPX-View will not start at all -- still leaves somebody
holding their own GPX files and a SQLite database that any tool can read.

**Reading one is the dangerous half.** A tar member name is a string an attacker
chose, and ``tarfile.extractall`` has historically been happy to write it
wherever it points. Nothing here extracts by member name:

```
member name -> validated -> looked up in the manifest -> written to a path we built
```

A member that is not a regular file, not named in the manifest, absolute,
containing ``..``, or carrying a link target is refused rather than sanitised.
Sanitising invites the question "did we sanitise it correctly"; refusing does
not.

**Writing one is the atomic half.** The archive is built under a ``.part`` name
and renamed into place only once it is complete and flushed, so a crash leaves no
file that looks like a backup and is not one.

Member metadata is written deterministically -- no owner, no group, no host
user name -- because a tar header records the login name of whoever ran it, and
that is personal data leaving the machine inside a file people share with
support.
"""

import hashlib
import io
import logging
import os
import shutil
import tarfile
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from gpx_view.application.archive import (
    DATABASE_MEMBER,
    MANIFEST_MEMBER,
    RAW_MEMBER_PREFIX,
    ArchiveContents,
    ArchivedFile,
    ArchiveError,
    ArchiveErrorCode,
    ArchiveManifest,
    StagedArchive,
)
from gpx_view.infrastructure.archive.manifest_codec import decode_manifest, encode_manifest
from gpx_view.infrastructure.database.archive_source import read_counts
from gpx_view.infrastructure.database.inspection import counts_sources, read_schema_version
from gpx_view.infrastructure.database.snapshot import (
    DatabaseSnapshotError,
    capture_snapshot,
    digest_of,
    verify_database,
)
from gpx_view.infrastructure.private_data import (
    PRIVATE_FILE_MODE,
    create_private_directory,
    create_private_file,
)

logger = logging.getLogger(__name__)

ARCHIVE_SUFFIX = ".tar.gz"
PARTIAL_SUFFIX = ".part"
RAW_ARTIFACT_SUFFIX = ".raw"

_READ_CHUNK_BYTES = 1024 * 1024

_STAGED_DATABASE = "database.sqlite3"

# tar records an owner, a group and their names. None of that describes the
# archive, and the login name of whoever ran the backup is personal data.
_ANONYMOUS_OWNER = 0
_ANONYMOUS_NAME = ""


class FilesystemArchiveBuilder:
    """Assembles one archive file out of a live data directory."""

    def __init__(self, destination: Path, database_path: Path, raw_root: Path) -> None:
        """Point the builder at where the archive goes and what goes into it."""
        self._destination = destination
        self._database_path = database_path
        self._raw_root = raw_root
        self._staging = destination.parent / f".{destination.name}.staging-{uuid4().hex}"
        self._partial = destination.with_name(destination.name + PARTIAL_SUFFIX)

    def stage(self) -> StagedArchive:
        """Capture the database consistently and describe what was captured.

        The artifacts are hashed rather than trusted. Their filename already
        states a digest, and checking it here is what stops a backup from
        becoming a second copy of a corruption nobody noticed.

        The schema version and the counts are read from the **snapshot**, not
        from the live database this builder was pointed at. They describe what
        goes into the archive, and reading them from anywhere else would let a
        manifest describe a different deployment.

        Raises:
            ArchiveError: If the database could not be captured consistently, or
                an artifact does not match the hash it is filed under.
        """
        try:
            create_private_directory(self._staging)
        except OSError as error:
            # Almost always a backup directory the container user cannot write
            # to. A traceback here would send an operator looking for a bug in
            # the archive format instead of at the ownership of one directory.
            raise ArchiveError(
                ArchiveErrorCode.WRITE_FAILED,
                "the backup directory is not writable by this user",
            ) from error
        try:
            size, digest = capture_snapshot(self._database_path, self._staging / _STAGED_DATABASE)
        except DatabaseSnapshotError as error:
            raise ArchiveError(
                ArchiveErrorCode.WRITE_FAILED, "the database could not be copied consistently"
            ) from error
        except OSError as error:
            raise ArchiveError(
                ArchiveErrorCode.WRITE_FAILED, "the database copy could not be written"
            ) from error
        snapshot = self._staging / _STAGED_DATABASE
        return StagedArchive(
            contents=ArchiveContents(
                database=ArchivedFile(name=DATABASE_MEMBER, size_bytes=size, sha256=digest),
                raw_imports=tuple(self._raw_artifacts()),
            ),
            schema_version=read_schema_version(snapshot) or 0,
            counts=read_counts(snapshot),
        )

    def _raw_artifacts(self) -> Iterator[ArchivedFile]:
        """Describe every managed raw artifact, in a stable order.

        Sorted, so two archives of the same archive list their members the same
        way and a difference between them is a difference in the data.
        """
        for path in sorted(self._raw_root.rglob(f"*{RAW_ARTIFACT_SUFFIX}")):
            if not path.is_file() or path.is_symlink():
                continue
            digest = digest_of(path)
            if digest != path.stem:
                raise ArchiveError(
                    ArchiveErrorCode.CHECKSUM_MISMATCH,
                    "a stored artifact does not match the hash it is filed under",
                )
            relative = path.relative_to(self._raw_root).as_posix()
            yield ArchivedFile(
                name=f"{RAW_MEMBER_PREFIX}{relative}",
                size_bytes=path.stat().st_size,
                sha256=digest,
            )

    def finish(self, manifest: ArchiveManifest) -> None:
        """Write the container and move it into place as one act.

        Raises:
            ArchiveError: If the archive could not be written. The partial file
                is removed, so nothing survives that could be mistaken for a
                complete backup.
        """
        create_private_file(self._partial)
        try:
            with tarfile.open(self._partial, "w:gz") as container:
                self._add_bytes(container, manifest, MANIFEST_MEMBER, encode_manifest(manifest))
                self._add_file(
                    container,
                    manifest,
                    manifest.contents.database,
                    self._staging / _STAGED_DATABASE,
                )
                for item in manifest.contents.raw_imports:
                    self._add_file(container, manifest, item, self._source_path_of(item))
            _flush(self._partial)
            self._partial.replace(self._destination)
        except OSError as error:
            self._discard_partial()
            raise ArchiveError(
                ArchiveErrorCode.WRITE_FAILED, "the archive could not be written"
            ) from error
        except BaseException:
            self._discard_partial()
            raise
        self._remove_staging()

    def _source_path_of(self, item: ArchivedFile) -> Path:
        """Return where a described raw member is read from."""
        return self._raw_root / item.name.removeprefix(RAW_MEMBER_PREFIX)

    def _add_bytes(
        self, container: tarfile.TarFile, manifest: ArchiveManifest, name: str, content: bytes
    ) -> None:
        """Add a member held in memory."""
        info = self._member(manifest, name, len(content))
        container.addfile(info, io.BytesIO(content))

    def _add_file(
        self,
        container: tarfile.TarFile,
        manifest: ArchiveManifest,
        item: ArchivedFile,
        source: Path,
    ) -> None:
        """Add a member read from disk, proving it is still what was described.

        The digest was taken during staging and the bytes are read again here.
        Checking them against the manifest closes the window between the two: a
        file that changed in between would otherwise be archived under a checksum
        it no longer has, and the mismatch would surface at restore time.

        Raises:
            ArchiveError: If the bytes no longer match what the manifest states.
        """
        info = self._member(manifest, item.name, item.size_bytes)
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            container.addfile(info, _HashingReader(handle, digest))
        if digest.hexdigest() != item.sha256:
            raise ArchiveError(
                ArchiveErrorCode.CHECKSUM_MISMATCH,
                "a file changed while the archive was being written",
            )

    @staticmethod
    def _member(manifest: ArchiveManifest, name: str, size: int) -> tarfile.TarInfo:
        """Return a member header that describes the archive and not the host."""
        info = tarfile.TarInfo(name)
        info.size = size
        info.mtime = int(manifest.created_at.timestamp())
        info.mode = PRIVATE_FILE_MODE
        info.uid = info.gid = _ANONYMOUS_OWNER
        info.uname = info.gname = _ANONYMOUS_NAME
        return info

    def abandon(self) -> None:
        """Discard everything staged, without masking the failure that caused it."""
        self._discard_partial()
        self._remove_staging()

    def _discard_partial(self) -> None:
        """Remove the partial archive, if one was started."""
        with suppress(OSError):
            self._partial.unlink(missing_ok=True)

    def _remove_staging(self) -> None:
        """Remove the staging directory, if one was created."""
        with suppress(OSError):
            shutil.rmtree(self._staging, ignore_errors=True)


class _HashingReader:
    """A read-only file wrapper that hashes what passes through it.

    ``tarfile`` copies from a file object, so hashing there costs one pass rather
    than a second read of the whole artifact.
    """

    def __init__(self, handle: BinaryIO, digest: "hashlib._Hash") -> None:
        """Wrap a binary handle and the digest to feed."""
        self._handle = handle
        self._digest = digest

    def read(self, size: int = -1) -> bytes:
        """Return the next bytes, updating the digest with them."""
        chunk = self._handle.read(size)
        self._digest.update(chunk)
        return chunk


def _flush(path: Path) -> None:
    """Force a written file to disk before it is renamed into place."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class FilesystemArchiveExtractor:
    """Reads one archive and, once it is proved, publishes it into a data directory."""

    def __init__(self, source: Path, data_dir: Path, database_path: Path, raw_root: Path) -> None:
        """Point the extractor at the archive and at the deployment it would become."""
        self._source = source
        self._data_dir = data_dir
        self._database_path = database_path
        self._raw_root = raw_root
        self._staging = data_dir / f".restore-{uuid4().hex}"
        self._displaced = data_dir / f".replaced-{uuid4().hex}"

    def manifest(self) -> ArchiveManifest:
        """Read the manifest alone, extracting nothing.

        Raises:
            ArchiveError: ``archive_unreadable`` if the container will not open,
                ``archive_manifest_invalid`` if it holds no readable manifest.
        """
        with self._open() as container:
            try:
                member = container.getmember(MANIFEST_MEMBER)
            except KeyError as error:
                raise ArchiveError(
                    ArchiveErrorCode.MANIFEST_INVALID, "the archive holds no manifest"
                ) from error
            if not member.isfile():
                raise ArchiveError(
                    ArchiveErrorCode.MANIFEST_INVALID, "the manifest is not a regular file"
                )
            handle = container.extractfile(member)
            if handle is None:
                raise ArchiveError(
                    ArchiveErrorCode.MANIFEST_INVALID, "the manifest could not be read"
                )
            with handle:
                return decode_manifest(handle.read())

    def verify(self, manifest: ArchiveManifest) -> None:
        """Extract into staging and prove every member, before anything is published.

        Raises:
            ArchiveError: If a member is refused, missing, or not the bytes the
                manifest names, or if the database inside is not sound.
        """
        expected = manifest.contents.checksums()
        create_private_directory(self._staging)
        extracted: set[str] = set()
        with self._open() as container:
            for member in container:
                if member.name == MANIFEST_MEMBER:
                    continue
                name = self._accepted_name(member, expected)
                self._extract_member(container, member, name)
                extracted.add(name)
        self._require_complete(expected, extracted)
        self._verify_checksums(manifest)
        self._verify_database()
        # An archive may legitimately carry no originals at all -- a deployment
        # that has a database and has imported nothing yet, which is exactly the
        # backup somebody takes to check the command works. Nothing then created
        # the raw directory during extraction, and publishing has to move
        # *something* into place, so the empty storage the archive describes is
        # made explicit here rather than left as a missing path.
        create_private_directory(self._staged_raw_root())

    def _accepted_name(self, member: tarfile.TarInfo, expected: dict[str, str]) -> str:
        """Return the member's name, or refuse it.

        Two independent reasons a member may be extracted, and it needs both:
        the manifest names it, *and* the name cannot escape the staging
        directory. Either check alone would be enough today; keeping both means
        a mistake in one is not a vulnerability.

        Raises:
            ArchiveError: ``archive_member_refused``.
        """
        if not member.isfile():
            raise ArchiveError(
                ArchiveErrorCode.MEMBER_REFUSED,
                "the archive holds a member that is not a regular file",
            )
        name = member.name
        if name not in expected:
            raise ArchiveError(
                ArchiveErrorCode.MEMBER_REFUSED, "the archive holds a member its manifest omits"
            )
        _require_contained(name)
        return name

    def _extract_member(
        self, container: tarfile.TarFile, member: tarfile.TarInfo, name: str
    ) -> None:
        """Write one member into staging, at a path this code built.

        The member's own name is never handed to the file system. It is split
        into components that have already been proved harmless, and rejoined
        under the staging root.
        """
        destination = self._staging.joinpath(*name.split("/"))
        create_private_directory(destination.parent)
        handle = container.extractfile(member)
        if handle is None:
            raise ArchiveError(
                ArchiveErrorCode.ARCHIVE_UNREADABLE, "an archive member could not be read"
            )
        create_private_file(destination)
        with handle, destination.open("wb") as sink:
            while chunk := handle.read(_READ_CHUNK_BYTES):
                sink.write(chunk)

    @staticmethod
    def _require_complete(expected: dict[str, str], extracted: set[str]) -> None:
        """Refuse an archive that promised more than it carried."""
        if missing := sorted(set(expected) - extracted):
            raise ArchiveError(
                ArchiveErrorCode.INCOMPLETE,
                f"the archive is missing {len(missing)} member(s) its manifest names",
            )

    def _verify_checksums(self, manifest: ArchiveManifest) -> None:
        """Prove every extracted member is the bytes the manifest describes."""
        for name, digest in manifest.contents.checksums().items():
            path = self._staging.joinpath(*name.split("/"))
            if digest_of(path) != digest:
                raise ArchiveError(
                    ArchiveErrorCode.CHECKSUM_MISMATCH,
                    "an archive member does not match its stated digest",
                )

    def _verify_database(self) -> None:
        """Prove the captured database is a sound database, not just intact bytes."""
        try:
            verify_database(self._staged_database())
        except DatabaseSnapshotError as error:
            raise ArchiveError(
                ArchiveErrorCode.DATABASE_INVALID, "the archived database is not sound"
            ) from error

    def _staged_database(self) -> Path:
        """Return where the archived database sits in staging."""
        return self._staging.joinpath(*DATABASE_MEMBER.split("/"))

    def _staged_raw_root(self) -> Path:
        """Return where the archived raw storage sits in staging."""
        return self._staging / RAW_MEMBER_PREFIX.rstrip("/")

    def target_holds_data(self) -> bool:
        """Report whether publishing would replace something somebody could lose.

        Deliberately "holds data" and not "holds files". Starting the server
        creates and migrates an empty database, so the obvious check answers yes
        for a deployment that holds nothing at all -- and the ordinary
        disaster-recovery path would then be ``restore --replace``. Making
        ``--replace`` the normal thing to type is how somebody eventually types
        it at an archive that mattered.

        A source that cannot be accounted for counts as data. An unreadable
        database is not "nothing is there"; it is something this build cannot
        explain, and replacing it is a decision for a person.
        """
        if self._raw_root.is_dir() and any(self._raw_root.rglob(f"*{RAW_ARTIFACT_SUFFIX}")):
            return True
        return counts_sources(self._database_path) != 0

    def free_bytes(self) -> int:
        """Return how much room the destination has."""
        create_private_directory(self._data_dir)
        usage = shutil.disk_usage(self._data_dir)
        return usage.free

    def publish(self) -> None:
        """Move the verified material into place, keeping the old data until it is.

        The data directory is **not** replaced as a whole. In a container it is a
        mount point, and a mount point cannot be renamed -- a restore that tried
        would work on a developer's laptop and fail on every real deployment.
        What is replaced is what the archive actually carries: the database, its
        journal files, and the managed raw storage. Installed offline maps are
        left exactly where they are, which is the same decision that keeps them
        out of the archive.

        The old material is moved aside rather than deleted, and removed only
        once the new material is in place. Two renames are not one atomic act;
        what this guarantees is that no moment exists in which neither copy is
        there.
        """
        create_private_directory(self._data_dir)
        create_private_directory(self._displaced)
        self._displace_existing()
        self._staged_database().replace(self._database_path)
        self._staged_raw_root().replace(self._raw_root)
        self._cleanup()

    def _displace_existing(self) -> None:
        """Move what is currently in place out of the way.

        The journal files go with the database they belong to. A write-ahead log
        left beside a restored database describes transactions from a different
        database, and SQLite would apply it.
        """
        for path in (
            self._database_path,
            _journal(self._database_path, "-wal"),
            _journal(self._database_path, "-shm"),
        ):
            if path.exists():
                path.replace(self._displaced / path.name)
        if self._raw_root.exists():
            self._raw_root.replace(self._displaced / self._raw_root.name)

    def _cleanup(self) -> None:
        """Remove the staging and displaced directories, once nothing needs them."""
        for directory in (self._staging, self._displaced):
            with suppress(OSError):
                shutil.rmtree(directory, ignore_errors=True)

    def abandon(self) -> None:
        """Discard the staged material without touching what is in place."""
        self._cleanup()

    def _open(self) -> tarfile.TarFile:
        """Open the container, or refuse it.

        Raises:
            ArchiveError: ``archive_unreadable``.
        """
        try:
            return tarfile.open(self._source, "r:*")
        except (OSError, tarfile.TarError) as error:
            raise ArchiveError(
                ArchiveErrorCode.ARCHIVE_UNREADABLE, "the archive could not be opened"
            ) from error


def _journal(database: Path, suffix: str) -> Path:
    """Return the path of one of a database's journal files."""
    return database.with_name(database.name + suffix)


def _require_contained(name: str) -> None:
    """Refuse a member name that could name anything outside the staging root.

    An allow list of what a component may be, rather than a deny list of what it
    may not: ``..`` and a leading ``/`` are the two everybody remembers, and a
    drive letter, a backslash and an empty component are the ones they do not.

    Raises:
        ArchiveError: ``archive_member_refused``.
    """
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        raise ArchiveError(
            ArchiveErrorCode.MEMBER_REFUSED, "the archive holds a member with an unusable name"
        )
    components = name.split("/")
    if any(component in ("", ".", "..") for component in components):
        raise ArchiveError(
            ArchiveErrorCode.MEMBER_REFUSED,
            "the archive holds a member whose name leaves the archive",
        )
    if Path(name).is_absolute():
        raise ArchiveError(
            ArchiveErrorCode.MEMBER_REFUSED, "the archive holds a member with an absolute path"
        )
