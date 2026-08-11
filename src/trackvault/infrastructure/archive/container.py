"""The archive container: a plain ``tar.gz``, written and read carefully.

**Why an ordinary tar.gz.** A backup format only its own application can open is
a backup with a dependency, and the situation you need a backup in is exactly the
one where the application may not run. Any operating system can list and unpack
this, so the worst case -- TrackVault will not start at all -- still leaves somebody
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

```
write  ->  flush  ->  fsync the file  ->  atomic rename  ->  fsync the directory
```

The last arrow is the one that is easy to leave off, and without it the
guarantee is half a guarantee. Flushing the file makes its *bytes* durable; the
rename that gives those bytes the archive's name is an entry in the destination's
directory, and an unflushed directory entry does not survive the power cut this
sequence exists for. So a backup whose command returned successfully has been
synchronised after its publish -- the same promise, in the same order, that the
managed raw storage makes about an imported original.

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
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from trackvault.application.archive import (
    DATABASE_MEMBER,
    MANIFEST_MEMBER,
    MAP_PACKAGES_OMITTED,
    RAW_MEMBER_PREFIX,
    ArchiveContents,
    ArchivedFile,
    ArchiveError,
    ArchiveErrorCode,
    ArchiveManifest,
    ArchiveOmission,
    StagedArchive,
    unreferenced_raw_objects,
)
from trackvault.infrastructure.archive.manifest_codec import decode_manifest, encode_manifest
from trackvault.infrastructure.archive.publication import (
    begin_publication,
    finish_publication,
    recover_interrupted_restore,
    restore_is_pending,
    sync_directory,
)
from trackvault.infrastructure.database.archive_source import read_counts, read_raw_import_digests
from trackvault.infrastructure.database.inspection import counts_sources, read_schema_version
from trackvault.infrastructure.database.snapshot import (
    DatabaseSnapshotError,
    capture_snapshot,
    digest_of,
    verify_database,
)
from trackvault.infrastructure.filesystem.raw_store import FilesystemRawImportStore
from trackvault.infrastructure.private_data import (
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
        """Point the builder at where the archive goes and what goes into it.

        The managed storage is reached through its own store rather than by
        rebuilding its layout here. Where a content hash lives on disk has one
        owner, and a backup that computed the path itself would be the second
        place that has to be changed if it ever moves.
        """
        self._destination = destination
        self._database_path = database_path
        self._raw_root = raw_root
        self._raw_store = FilesystemRawImportStore(raw_root)
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
        referenced = read_raw_import_digests(snapshot)
        return StagedArchive(
            contents=ArchiveContents(
                database=ArchivedFile(name=DATABASE_MEMBER, size_bytes=size, sha256=digest),
                raw_imports=tuple(self._raw_artifacts(referenced)),
            ),
            schema_version=read_schema_version(snapshot) or 0,
            counts=read_counts(snapshot),
            omissions=self._omissions(referenced),
        )

    def _raw_artifacts(self, referenced: Sequence[str]) -> Iterator[ArchivedFile]:
        """Describe the managed original of every source the snapshot names.

        Driven by the database rather than by the directory. A source the
        snapshot names and the storage cannot produce is the failure this whole
        arrangement exists to catch: walking the directory instead would simply
        not find it, and the archive would be complete-looking and short by one
        recording nobody could get back.

        Raises:
            ArchiveError: ``archive_source_incomplete`` if an original is
                missing or unreadable, ``archive_checksum_mismatch`` if its
                bytes are not the ones it is filed under.
        """
        for sha256 in referenced:
            path = self._raw_store.path_for(sha256)
            if not path.is_file() or path.is_symlink():
                raise ArchiveError(
                    ArchiveErrorCode.SOURCE_INCOMPLETE,
                    "the archive holds no stored original for a source its database names",
                )
            try:
                digest = digest_of(path)
            except OSError as error:
                raise ArchiveError(
                    ArchiveErrorCode.SOURCE_INCOMPLETE,
                    "a stored original could not be read",
                ) from error
            if digest != sha256:
                raise ArchiveError(
                    ArchiveErrorCode.CHECKSUM_MISMATCH,
                    "a stored original does not match the hash it is filed under",
                )
            relative = path.relative_to(self._raw_root).as_posix()
            yield ArchivedFile(
                name=f"{RAW_MEMBER_PREFIX}{relative}",
                size_bytes=path.stat().st_size,
                sha256=digest,
            )

    def _omissions(self, referenced: Sequence[str]) -> tuple[ArchiveOmission, ...]:
        """Return what this archive leaves out, discovered rather than assumed.

        The map packages are categorical. The unreferenced originals are counted
        here and now, because whether a deployment has any is a fact about that
        deployment at that instant -- and an archive that passed over stored
        bytes without saying so is exactly the quiet incompleteness this module
        is arranged against.
        """
        wanted = {self._raw_store.path_for(sha256) for sha256 in referenced}
        unreferenced = sum(
            1
            for path in self._raw_root.rglob(f"*{RAW_ARTIFACT_SUFFIX}")
            if path.is_file() and not path.is_symlink() and path not in wanted
        )
        if not unreferenced:
            return (MAP_PACKAGES_OMITTED,)
        return (MAP_PACKAGES_OMITTED, unreferenced_raw_objects(unreferenced))

    def finish(self, manifest: ArchiveManifest) -> None:
        """Write the container, move it into place, and make that durable.

        Returning from here means the archive exists on the disk and not merely
        in the page cache: the bytes are flushed before the rename and the
        destination's directory is flushed after it. Somebody who runs a backup
        and pulls the plug has a backup.

        Raises:
            ArchiveError: If the archive could not be written or could not be
                made durable. Nothing survives under either name that could be
                mistaken for a complete backup.
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
            self._make_durable()
        except OSError as error:
            self._discard_partial()
            raise ArchiveError(
                ArchiveErrorCode.WRITE_FAILED, "the archive could not be written"
            ) from error
        except BaseException:
            self._discard_partial()
            raise
        self._remove_staging()

    def _make_durable(self) -> None:
        """Flush the directory the rename was recorded in, or leave no archive.

        The same helper the restore publication uses, deliberately. Both end a
        multi-step write with a rename, and both are only as durable as the
        directory entry that rename produced -- one implementation, because a
        second one would be the one that quietly stopped at the rename.

        A failure here is a failure of the backup. The file is complete and
        already carries the archive's own name, and leaving it would hand
        somebody a backup that the command told them it could not write.

        Raises:
            OSError: If the directory could not be flushed.
        """
        try:
            sync_directory(self._destination.parent)
        except OSError:
            with suppress(OSError):
                self._destination.unlink(missing_ok=True)
            raise

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
        with self._open() as container, _readable_container():
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
        with self._open() as container, _readable_container():
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
        once the new material is in place. Several renames are not one atomic
        act; what this guarantees is that no moment exists in which neither copy
        is there, and that any moment in between is *recoverable*.

        Recoverable rather than atomic, because atomic is not available here. A
        marker recording what is being moved is written and flushed before the
        first rename and cleared after the last, so an interruption anywhere in
        between leaves a deployment that can be resolved -- see
        `trackvault.infrastructure.archive.publication`.
        """
        create_private_directory(self._data_dir)
        create_private_directory(self._displaced)
        begin_publication(
            self._data_dir,
            staging=self._staging,
            displaced=self._displaced,
            database=self._database_path,
            raw_root=self._raw_root,
        )
        self._displace_existing()
        self._staged_database().replace(self._database_path)
        self._staged_raw_root().replace(self._raw_root)
        self._cleanup()
        finish_publication(self._data_dir)

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
        """Give up on this restore, leaving one readable deployment behind.

        Before publication began this is simply "throw the staged material
        away". Once it has begun it is not, and the difference is the whole
        point: the displaced directory then holds the *only* copy of the
        previous database and raw storage, and discarding it as staging debris
        would turn a failed restore into total data loss.

        So an interrupted publication is resolved rather than deleted, through
        exactly the code the next start-up would use. Two implementations of
        "what to do about an unfinished publication" would eventually disagree,
        and the day they did, somebody would already be restoring from a backup.
        """
        if restore_is_pending(self._data_dir):
            recover_interrupted_restore(self._data_dir)
            return
        self._cleanup()

    def _open(self) -> tarfile.TarFile:
        """Open the container, or refuse it.

        Raises:
            ArchiveError: ``archive_unreadable``.
        """
        try:
            return tarfile.open(self._source, "r:*")
        except _CONTAINER_FAILURES as error:
            raise ArchiveError(
                ArchiveErrorCode.ARCHIVE_UNREADABLE, "the archive could not be opened"
            ) from error


_CONTAINER_FAILURES = (OSError, tarfile.TarError, EOFError, zlib.error)
"""Every way a damaged container fails while it is being read.

`EOFError` is the one that matters and the one that is easy to miss. A backup
cut short -- a disk that filled, a copy somebody interrupted, a download that
stopped -- opens perfectly: the gzip header is intact and the manifest is the
first member, so the archive can still *describe* material it no longer
carries. The failure arrives later, from the decompressor, as a bare `EOFError`
that is neither an `OSError` nor a `TarError`.

Truncation is also far more likely than any hostile archive this module guards
against, which makes a traceback the most probable way somebody meets this code.
"""


@contextmanager
def _readable_container() -> Iterator[None]:
    """Translate a damaged container into the archive's own vocabulary.

    Wraps the *reading*, not the opening. What is refused here is a container
    this build cannot finish reading, which is a different statement from a file
    that is not an archive -- and both are answers rather than tracebacks.
    """
    try:
        yield
    except ArchiveError:
        raise
    except _CONTAINER_FAILURES as error:
        raise ArchiveError(
            ArchiveErrorCode.ARCHIVE_UNREADABLE, "the archive could not be read to the end"
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
