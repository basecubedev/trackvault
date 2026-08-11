"""Archive, backup and restore contracts.

A backup is only worth what its restore is worth, so most of what is asserted
here is about refusal: an archive that is damaged, incomplete, hostile or written
by a build that knows more than this one must be rejected *before* anything in
the destination is touched.

The strongest test in this file is the last one -- back up, destroy, restore,
and find the same tracks, the same corrections and the same metrics. Everything
above it exists so that the failure modes on the way there are named rather than
discovered.
"""

import gzip
import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpx_view.application.archive import (
    ARCHIVE_FORMAT_NAME,
    ARCHIVE_FORMAT_VERSION,
    DATABASE_MEMBER,
    MANIFEST_MEMBER,
    ArchiveCompatibility,
    ArchiveContents,
    ArchiveCounts,
    ArchivedFile,
    ArchiveError,
    ArchiveErrorCode,
    ArchiveManifest,
    compatibility_of,
)
from gpx_view.application.import_tracks import ImportRequest, ImportStatus
from gpx_view.config import Settings
from gpx_view.domain import InputChannel, TrackKind, UserTrackMetadata
from gpx_view.infrastructure.archive import FilesystemArchiveBuilder, FilesystemArchiveExtractor
from gpx_view.infrastructure.archive.manifest_codec import decode_manifest, encode_manifest
from gpx_view.infrastructure.assembly import TrackServices, build_services

pytestmark = [pytest.mark.contract, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"


@pytest.fixture
def services(settings: Settings) -> TrackServices:
    """Return a wired archive over a throwaway data directory."""
    built = build_services(settings)
    built.prepare_storage()
    return built


def _import(services: TrackServices, name: str) -> tuple[str, tuple[int, ...]]:
    """Import one synthetic fixture."""
    outcome = services.import_tracks(
        ImportRequest(
            content=(FIXTURES / name).read_bytes(),
            original_filename=name,
            input_channel=InputChannel.LOCAL_FILE,
        )
    )
    assert outcome.status is ImportStatus.IMPORTED, outcome.error_code
    return outcome.sha256, outcome.track_ids


def _builder(services: TrackServices, destination: Path) -> FilesystemArchiveBuilder:
    """Return a builder writing one archive out of the wired deployment."""
    return FilesystemArchiveBuilder(
        destination=destination,
        database_path=services.settings.database_path,
        raw_root=services.settings.raw_storage_dir,
    )


def _extractor(archive: Path, data_dir: Path) -> FilesystemArchiveExtractor:
    """Return an extractor that would publish into ``data_dir``."""
    return FilesystemArchiveExtractor(
        source=archive,
        data_dir=data_dir,
        database_path=data_dir / "gpx-view.sqlite3",
        raw_root=data_dir / "raw",
    )


def _write_archive(services: TrackServices, tmp_path: Path, name: str = "backup.tar.gz") -> Path:
    """Create one archive and return where it was written."""
    destination = tmp_path / name
    services.create_archive(_builder(services, destination))
    return destination


def _members(archive: Path) -> dict[str, bytes]:
    """Return every member of an archive, by name."""
    with tarfile.open(archive, "r:gz") as container:
        return {
            member.name: (handle.read() if (handle := container.extractfile(member)) else b"")
            for member in container
            if member.isfile()
        }


def _repack(destination: Path, members: dict[str, bytes]) -> Path:
    """Write a new archive holding exactly the given members.

    The suite builds its own hostile archives rather than committing one: a
    committed malicious tar is a file that has to be explained to every scanner
    that ever looks at this repository.
    """
    with tarfile.open(destination, "w:gz") as container:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            container.addfile(info, io.BytesIO(content))
    return destination


# --- the manifest ---------------------------------------------------------


def test_an_archive_carries_a_manifest_that_describes_it(
    services: TrackServices, tmp_path: Path
) -> None:
    """Every archive states what it is, what wrote it and what it holds."""
    _import(services, "recorded-measurements.gpx")

    manifest = services.create_archive(_builder(services, tmp_path / "backup.tar.gz"))

    assert manifest.format_name == ARCHIVE_FORMAT_NAME
    assert manifest.format_version == ARCHIVE_FORMAT_VERSION
    assert manifest.gpx_view_version
    assert manifest.schema_version > 0
    assert manifest.counts.raw_imports == 1
    assert manifest.counts.tracks == 1


def test_the_manifest_is_the_first_member(services: TrackServices, tmp_path: Path) -> None:
    """A dry-run reads one small file rather than scanning the whole container."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    with tarfile.open(archive, "r:gz") as container:
        assert container.next().name == MANIFEST_MEMBER  # type: ignore[union-attr]


def test_an_archive_states_what_it_leaves_out(services: TrackServices, tmp_path: Path) -> None:
    """Complete and "complete except for the unmentioned part" are different promises."""
    manifest = services.create_archive(_builder(services, tmp_path / "backup.tar.gz"))

    assert [omission.kind for omission in manifest.omissions] == ["map_packages"]
    assert all(omission.reason for omission in manifest.omissions)


def test_every_member_is_checksummed(services: TrackServices, tmp_path: Path) -> None:
    """A restore verifies bytes, so the manifest has to name what they must be."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    manifest = _extractor(archive, tmp_path / "target").manifest()

    checksums = manifest.contents.checksums()
    assert set(checksums) == set(_members(archive)) - {MANIFEST_MEMBER}
    assert all(len(digest) == 64 for digest in checksums.values())


def test_a_manifest_survives_being_written_and_read() -> None:
    """Encoding and decoding are inverse, or a restore reads something else."""
    manifest = ArchiveManifest(
        format_name=ARCHIVE_FORMAT_NAME,
        format_version=ARCHIVE_FORMAT_VERSION,
        created_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
        gpx_view_version="9.9.9",
        schema_version=7,
        contents=ArchiveContents(
            database=ArchivedFile(name=DATABASE_MEMBER, size_bytes=3, sha256="a" * 64),
            raw_imports=(ArchivedFile(name="raw/x.raw", size_bytes=1, sha256="b" * 64),),
        ),
        counts=ArchiveCounts(raw_imports=1, tracks=2, classification_overrides=3, user_metadata=4),
    )

    assert decode_manifest(encode_manifest(manifest)) == manifest


@pytest.mark.parametrize(
    "document",
    [
        b"not json at all",
        b"[]",
        b'{"format_name": "gpx-view-archive"}',
        b'{"format_name": 1, "format_version": 1, "created_at": "2026-01-01T00:00:00+00:00",'
        b' "gpx_view_version": "1", "schema_version": 1, "contents": {}, "counts": {}}',
    ],
)
def test_a_malformed_manifest_is_refused_rather_than_half_read(document: bytes) -> None:
    """Decoding fails closed: a default would verify half an archive."""
    with pytest.raises(ArchiveError) as raised:
        decode_manifest(document)

    assert raised.value.code is ArchiveErrorCode.MANIFEST_INVALID


def test_a_manifest_version_that_is_a_boolean_is_refused() -> None:
    """`True` is an `int` in Python, and it would compare as version 1."""
    document = encode_manifest(
        ArchiveManifest(
            format_name=ARCHIVE_FORMAT_NAME,
            format_version=1,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            gpx_view_version="1",
            schema_version=1,
            contents=ArchiveContents(
                database=ArchivedFile(name=DATABASE_MEMBER, size_bytes=1, sha256="a" * 64),
                raw_imports=(),
            ),
            counts=ArchiveCounts(
                raw_imports=0, tracks=0, classification_overrides=0, user_metadata=0
            ),
        )
    ).replace(b'"schema_version": 1', b'"schema_version": true')

    with pytest.raises(ArchiveError) as raised:
        decode_manifest(document)

    assert raised.value.code is ArchiveErrorCode.MANIFEST_INVALID


# --- compatibility --------------------------------------------------------


def _manifest(
    *, format_name: str = ARCHIVE_FORMAT_NAME, format_version: int = 1, schema: int = 5
) -> ArchiveManifest:
    """Return a manifest with only the fields compatibility reads."""
    return ArchiveManifest(
        format_name=format_name,
        format_version=format_version,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        gpx_view_version="1",
        schema_version=schema,
        contents=ArchiveContents(
            database=ArchivedFile(name=DATABASE_MEMBER, size_bytes=1, sha256="a" * 64),
            raw_imports=(),
        ),
        counts=ArchiveCounts(raw_imports=0, tracks=0, classification_overrides=0, user_metadata=0),
    )


@pytest.mark.parametrize(
    ("manifest", "installed", "expected"),
    [
        (_manifest(schema=5), 5, ArchiveCompatibility.SUPPORTED),
        (_manifest(schema=3), 5, ArchiveCompatibility.MIGRATION_REQUIRED),
        (_manifest(schema=9), 5, ArchiveCompatibility.UNSUPPORTED),
        (_manifest(format_version=99), 5, ArchiveCompatibility.UNSUPPORTED),
        (_manifest(format_name="something-else"), 5, ArchiveCompatibility.UNSUPPORTED),
    ],
)
def test_compatibility_is_decided_by_one_rule(
    manifest: ArchiveManifest, installed: int, expected: ArchiveCompatibility
) -> None:
    """A dry-run and a real restore must not disagree about what they are looking at.

    A newer schema is refused for the reason the database itself refuses one: this
    build does not know what the columns mean. An older one restores and migrates,
    which is what makes an old backup usable rather than merely kept.
    """
    assert compatibility_of(manifest, installed) is expected


# --- refusing damaged and hostile archives --------------------------------


def test_an_archive_that_is_not_an_archive_is_refused(tmp_path: Path) -> None:
    """A file somebody renamed is not a backup, and saying so is not optional."""
    fake = tmp_path / "backup.tar.gz"
    fake.write_bytes(b"this is not a tar file")

    with pytest.raises(ArchiveError) as raised:
        _extractor(fake, tmp_path / "target").manifest()

    assert raised.value.code is ArchiveErrorCode.ARCHIVE_UNREADABLE


def test_a_tar_without_a_manifest_is_refused(tmp_path: Path) -> None:
    """The manifest is the authority; without one there is nothing to verify against."""
    archive = _repack(tmp_path / "backup.tar.gz", {"some/file": b"hello"})

    with pytest.raises(ArchiveError) as raised:
        _extractor(archive, tmp_path / "target").manifest()

    assert raised.value.code is ArchiveErrorCode.MANIFEST_INVALID


def test_a_member_the_manifest_does_not_name_is_refused(
    services: TrackServices, tmp_path: Path
) -> None:
    """An archive carries what it declares. A smuggled extra member is a refusal."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    members["stowaway.txt"] = b"surprise"
    tampered = _repack(tmp_path / "tampered.tar.gz", members)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.MEMBER_REFUSED


@pytest.mark.parametrize(
    "hostile_name",
    [
        "../escaped.txt",
        "../../etc/passwd",
        "/etc/passwd",
        "raw/../../escaped.raw",
        "raw/./../../escaped.raw",
    ],
)
def test_a_member_whose_name_leaves_the_archive_is_refused(
    services: TrackServices, tmp_path: Path, hostile_name: str
) -> None:
    """Path traversal is refused rather than sanitised.

    Sanitising invites "did we sanitise correctly". Refusing does not, and the
    member is refused twice over: the manifest does not name it, and the name
    itself cannot be built into a path.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    members[hostile_name] = b"owned"
    tampered = _repack(tmp_path / "tampered.tar.gz", members)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.MEMBER_REFUSED
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_a_symbolic_link_member_is_refused(services: TrackServices, tmp_path: Path) -> None:
    """A link in an archive is a way to write through it into somewhere else."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as container:
        for name, content in _members(archive).items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            container.addfile(info, io.BytesIO(content))
        link = tarfile.TarInfo("raw/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        container.addfile(link)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.MEMBER_REFUSED


def test_a_device_node_member_is_refused(services: TrackServices, tmp_path: Path) -> None:
    """Only regular files are extracted. A character device is not data."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as container:
        for name, content in _members(archive).items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            container.addfile(info, io.BytesIO(content))
        node = tarfile.TarInfo("raw/null")
        node.type = tarfile.CHRTYPE
        node.devmajor, node.devminor = 1, 3
        container.addfile(node)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.MEMBER_REFUSED


def test_a_member_whose_bytes_changed_is_refused(services: TrackServices, tmp_path: Path) -> None:
    """The checksum is what makes an archive evidence rather than a hope."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    raw_member = next(name for name in members if name.startswith("raw/"))
    members[raw_member] = b"different bytes entirely"
    tampered = _repack(tmp_path / "tampered.tar.gz", members)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.CHECKSUM_MISMATCH


def test_an_archive_missing_a_promised_member_is_refused(
    services: TrackServices, tmp_path: Path
) -> None:
    """A partial restore is not a restore, and it must not silently be one."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    del members[next(name for name in members if name.startswith("raw/"))]
    truncated = _repack(tmp_path / "truncated.tar.gz", members)

    extractor = _extractor(truncated, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.INCOMPLETE


def test_an_archive_whose_database_is_not_a_database_is_refused(
    services: TrackServices, tmp_path: Path
) -> None:
    """A checksum proves the bytes arrived. It does not prove they were a database.

    So the archive is checked twice over: intact bytes, and a database that
    passes its own integrity check. Verifying only the first would mean a restore
    that succeeded and left an archive that will not open.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    broken = b"not a database" * 100
    members[DATABASE_MEMBER] = broken
    manifest = decode_manifest(members[MANIFEST_MEMBER])
    # Restate the checksum so the *database* check is what fails, not the digest.
    import hashlib

    members[MANIFEST_MEMBER] = encode_manifest(
        ArchiveManifest(
            format_name=manifest.format_name,
            format_version=manifest.format_version,
            created_at=manifest.created_at,
            gpx_view_version=manifest.gpx_view_version,
            schema_version=manifest.schema_version,
            contents=ArchiveContents(
                database=ArchivedFile(
                    name=DATABASE_MEMBER,
                    size_bytes=len(broken),
                    sha256=hashlib.sha256(broken).hexdigest(),
                ),
                raw_imports=manifest.contents.raw_imports,
            ),
            counts=manifest.counts,
            omissions=manifest.omissions,
        )
    )
    tampered = _repack(tmp_path / "tampered.tar.gz", members)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.DATABASE_INVALID


# --- restore behaviour ----------------------------------------------------


def test_a_dry_run_changes_nothing(services: TrackServices, tmp_path: Path) -> None:
    """A dry-run that extracted the archive would be a restore promising not to be."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    target = tmp_path / "target"

    inspection = services.restore_archive.inspect(_extractor(archive, target))

    assert inspection.compatibility is ArchiveCompatibility.SUPPORTED
    assert inspection.manifest.counts.tracks == 1
    assert not inspection.target_holds_data
    assert not target.exists()


def test_a_restore_refuses_to_replace_data_nobody_said_to_replace(
    services: TrackServices, tmp_path: Path
) -> None:
    """The protection that makes trying a restore safe."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    with pytest.raises(ArchiveError) as raised:
        services.restore_archive(_extractor(archive, services.settings.data_dir))

    assert raised.value.code is ArchiveErrorCode.TARGET_OCCUPIED


def test_a_restore_into_an_empty_directory_needs_no_permission(
    services: TrackServices, tmp_path: Path
) -> None:
    """Nothing is being replaced, so nothing has to be authorised."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    target = tmp_path / "fresh"

    outcome = services.restore_archive(_extractor(archive, target))

    assert not outcome.replaced_existing_data
    assert (target / "gpx-view.sqlite3").is_file()
    assert any((target / "raw").rglob("*.raw"))


def test_a_refused_restore_leaves_the_destination_untouched(
    services: TrackServices, tmp_path: Path
) -> None:
    """The property that makes a restore attemptable: failing costs nothing.

    A damaged archive is discovered during verification, which happens in a
    staging directory. Nothing in the destination has been moved by then.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    members[next(name for name in members if name.startswith("raw/"))] = b"corrupted"
    tampered = _repack(tmp_path / "tampered.tar.gz", members)
    target = tmp_path / "fresh"

    with pytest.raises(ArchiveError):
        services.restore_archive(_extractor(tampered, target))

    assert not (target / "gpx-view.sqlite3").exists()
    assert list(target.iterdir()) == []


def test_a_restore_does_not_remove_installed_maps(services: TrackServices, tmp_path: Path) -> None:
    """Maps are excluded from the archive, so a restore must not delete them either.

    The data directory is not replaced wholesale. What is replaced is what the
    archive actually carries, which is also what makes a restore work in a
    container where the data directory is a mount point that cannot be renamed.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    target = tmp_path / "fresh"
    maps = target / "maps" / "packages"
    maps.mkdir(parents=True)
    (maps / "keep-me").write_bytes(b"an installed map")

    services.restore_archive(_extractor(archive, target))

    assert (maps / "keep-me").read_bytes() == b"an installed map"


def test_a_restore_takes_the_old_journal_files_with_the_old_database(
    services: TrackServices, tmp_path: Path
) -> None:
    """A write-ahead log left beside a restored database describes another database.

    SQLite would apply it, and the restored archive would silently become a
    mixture of two.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    target = tmp_path / "fresh"
    target.mkdir()
    (target / "gpx-view.sqlite3").write_bytes(b"old")
    (target / "gpx-view.sqlite3-wal").write_bytes(b"stale journal")
    (target / "gpx-view.sqlite3-shm").write_bytes(b"stale shm")

    services.restore_archive(_extractor(archive, target), replace=True)

    assert not (target / "gpx-view.sqlite3-wal").exists()
    assert not (target / "gpx-view.sqlite3-shm").exists()


def test_a_restore_leaves_no_staging_behind(services: TrackServices, tmp_path: Path) -> None:
    """A directory named after a failed restore is a directory somebody backs up."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    target = tmp_path / "fresh"

    services.restore_archive(_extractor(archive, target))

    assert not [entry for entry in target.iterdir() if entry.name.startswith(".")]


# --- atomicity ------------------------------------------------------------


def test_a_failed_backup_leaves_nothing_that_looks_like_one(
    services: TrackServices, tmp_path: Path
) -> None:
    """A half-written file with a backup's name is worse than no backup at all."""
    _import(services, "recorded-measurements.gpx")
    destination = tmp_path / "backup.tar.gz"
    builder = _builder(services, destination)

    class _FailingBuilder:
        """A builder that stages successfully and then fails to write."""

        def stage(self) -> ArchiveContents:
            return builder.stage()

        def finish(self, manifest: ArchiveManifest) -> None:
            builder.finish(manifest)
            raise OSError("the disk filled up between the rename and the report")

        def abandon(self) -> None:
            builder.abandon()
            destination.unlink(missing_ok=True)

    with pytest.raises(OSError, match="disk filled up"):
        services.create_archive(_FailingBuilder())

    assert not destination.exists()
    assert not list(tmp_path.glob("*.part"))


def test_creating_a_backup_leaves_no_staging_directory(
    services: TrackServices, tmp_path: Path
) -> None:
    """The staging copy of the database is large; leaving it would double the cost."""
    _import(services, "recorded-measurements.gpx")

    _write_archive(services, tmp_path)

    assert not [entry for entry in tmp_path.iterdir() if entry.name.startswith(".")]
    assert not list(tmp_path.glob("*.part"))


def test_an_archive_records_no_host_user_name(services: TrackServices, tmp_path: Path) -> None:
    """A tar header carries the login name of whoever ran it. That is personal data."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    with tarfile.open(archive, "r:gz") as container:
        assert all(member.uname == "" and member.gname == "" for member in container)
        assert all(member.uid == 0 and member.gid == 0 for member in container)


def test_an_archive_is_private_at_rest(services: TrackServices, tmp_path: Path) -> None:
    """It holds every recording the deployment does, so it is as private as they are."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    assert archive.stat().st_mode & 0o077 == 0


def test_an_archive_is_an_ordinary_tar_anything_can_open(
    services: TrackServices, tmp_path: Path
) -> None:
    """The situation you need a backup in is the one where the application will not run.

    So the container is a plain gzipped tar: the worst case still leaves somebody
    holding their own files and a database any tool can read.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    with gzip.open(archive, "rb") as handle:
        assert handle.read(8)


# --- the whole point ------------------------------------------------------


def test_backup_destroy_restore_returns_the_archive_intact(
    services: TrackServices, tmp_path: Path
) -> None:
    """The acceptance test the rest of this file supports.

    Everything a user cannot recreate has to survive: the original bytes, the
    tracks derived from them, the kind they corrected and the title they wrote.
    """
    sha256, track_ids = _import(services, "recorded-measurements.gpx")
    services.store.set_override(track_ids[0], TrackKind.PLANNED, datetime.now(UTC))
    services.store.set_user_metadata(
        track_ids[0], UserTrackMetadata(title="My own name", note="a note"), datetime.now(UTC)
    )
    before = services.store.get_geometry(track_ids[0])
    archive = _write_archive(services, tmp_path)

    restored_root = tmp_path / "restored"
    services.restore_archive(_extractor(archive, restored_root))
    restored = build_services(Settings(data_dir=restored_root))

    summary = restored.store.get_track(track_ids[0])
    assert summary is not None
    assert summary.effective_kind is TrackKind.PLANNED
    assert summary.display_title == "My own name"
    assert summary.user_metadata.note == "a note"
    assert restored.store.get_geometry(track_ids[0]) == before
    assert restored.raw_store.read(sha256) == (FIXTURES / "recorded-measurements.gpx").read_bytes()


def test_an_empty_archive_is_not_data_worth_protecting(
    services: TrackServices, tmp_path: Path
) -> None:
    """Holding data means data, not files.

    Starting the server creates and migrates a database, so a destination that
    holds nothing at all still has a file in it. If that counted as data, the
    ordinary disaster-recovery path would be `restore --replace` -- and making
    `--replace` the normal thing to type is how somebody eventually types it at
    an archive that mattered.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    fresh = tmp_path / "fresh"
    started = build_services(Settings(data_dir=fresh))
    started.prepare_storage()
    assert (fresh / "gpx-view.sqlite3").is_file()

    outcome = services.restore_archive(_extractor(archive, fresh))

    assert not outcome.replaced_existing_data


def test_an_archive_holding_one_source_is_protected(
    services: TrackServices, tmp_path: Path
) -> None:
    """One imported track is data, and replacing it takes an explicit decision."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    extractor = _extractor(archive, services.settings.data_dir)

    assert extractor.target_holds_data()


def test_a_database_that_cannot_be_accounted_for_counts_as_data(
    services: TrackServices, tmp_path: Path
) -> None:
    """Fail safe: unreadable is not "nothing is there".

    A file this build cannot explain is something a person should look at before
    it is replaced, not an absence to restore over.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    damaged = tmp_path / "damaged"
    damaged.mkdir()
    (damaged / "gpx-view.sqlite3").write_bytes(b"this is not a database at all")

    extractor = _extractor(archive, damaged)

    assert extractor.target_holds_data()
    with pytest.raises(ArchiveError) as raised:
        services.restore_archive(extractor)
    assert raised.value.code is ArchiveErrorCode.TARGET_OCCUPIED


def test_a_raw_artifact_without_a_database_counts_as_data(
    services: TrackServices, tmp_path: Path
) -> None:
    """The other half of an archive. Losing it is losing the irreplaceable half."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    orphaned = tmp_path / "orphaned"
    (orphaned / "raw" / "sha256" / "ab").mkdir(parents=True)
    (orphaned / "raw" / "sha256" / "ab" / f"{'ab' * 32}.raw").write_bytes(b"someone's afternoon")

    assert _extractor(archive, orphaned).target_holds_data()


def test_an_archive_holding_no_originals_still_restores(
    services: TrackServices, tmp_path: Path
) -> None:
    """A deployment that has imported nothing is a valid thing to back up.

    It is also the first backup most people take -- the one that checks the
    command works before they need it. Nothing creates a raw directory during
    extraction in that case, and publishing still has to put the empty storage
    the archive describes into place.
    """
    archive = _write_archive(services, tmp_path)
    fresh = tmp_path / "fresh"

    outcome = services.restore_archive(_extractor(archive, fresh))

    assert outcome.manifest.counts.raw_imports == 0
    assert (fresh / "gpx-view.sqlite3").is_file()
    assert (fresh / "raw").is_dir()
    assert not list((fresh / "raw").iterdir())
