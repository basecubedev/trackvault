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

import errno
import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from trackvault.application.archive import (
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
from trackvault.application.import_tracks import ImportRequest, ImportStatus
from trackvault.config import Settings
from trackvault.domain import InputChannel, TrackKind, UserTrackMetadata
from trackvault.infrastructure.archive import FilesystemArchiveBuilder, FilesystemArchiveExtractor
from trackvault.infrastructure.archive.manifest_codec import decode_manifest, encode_manifest
from trackvault.infrastructure.assembly import TrackServices, build_services

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
        database_path=data_dir / "trackvault.sqlite3",
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
    assert manifest.trackvault_version
    assert manifest.schema_version > 0
    assert manifest.counts.raw_imports == 1
    assert manifest.counts.tracks == 1


def test_a_manifest_states_how_much_of_the_archive_arrived_analysed(
    services: TrackServices, tmp_path: Path
) -> None:
    """What a restore is followed by, said before anybody starts it.

    A fact about the archive, not a verdict: whether those metrics are still
    current is decided by the build that reads them. What the manifest answers
    is "will this restore be followed by a long re-analysis".
    """
    _import(services, "recorded-measurements.gpx")

    manifest = services.create_archive(_builder(services, tmp_path / "backup.tar.gz"))

    assert manifest.counts.tracks == 1
    assert manifest.counts.analyzed_tracks == 1


def test_a_manifest_written_before_a_count_existed_still_restores(
    services: TrackServices, tmp_path: Path
) -> None:
    """An archive of the same container format is readable, field by field.

    The format version is the promise that this build reads an archive that
    build wrote. A count added later is absent rather than wrong, and refusing
    over it would break the promise the version number exists to make.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    document = json.loads(members[MANIFEST_MEMBER])
    del document["counts"]["analyzed_tracks"]
    members[MANIFEST_MEMBER] = json.dumps(document).encode("utf-8")
    older = _repack(tmp_path / "older.tar.gz", members)

    manifest = _extractor(older, tmp_path / "target").manifest()

    assert manifest.counts.analyzed_tracks == 0
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
        trackvault_version="9.9.9",
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
        b'{"format_name": "trackvault-archive"}',
        b'{"format_name": 1, "format_version": 1, "created_at": "2026-01-01T00:00:00+00:00",'
        b' "trackvault_version": "1", "schema_version": 1, "contents": {}, "counts": {}}',
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
            trackvault_version="1",
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


def _tampered_manifest(
    archive: Path, destination: Path, rewrite: Callable[[dict[str, Any]], None]
) -> Path:
    """Repack an archive whose manifest has been edited, leaving its members alone."""
    members = _members(archive)
    document = json.loads(members[MANIFEST_MEMBER])
    rewrite(document)
    members[MANIFEST_MEMBER] = json.dumps(document).encode("utf-8")
    return _repack(destination, members)


def test_a_manifest_that_states_one_member_twice_is_refused(
    services: TrackServices, tmp_path: Path
) -> None:
    """A repeated name claims material the container cannot be carrying.

    Every use of the member list keys it by name -- the expected checksums, the
    completeness check, the path a member is written to -- so a duplicate
    collapses to one entry wherever it is checked and stays two wherever it is
    counted. The result is an archive that verifies perfectly and describes more
    sources than it holds, which is precisely the quiet incompleteness the whole
    manifest exists to make impossible.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    def repeat_the_only_source(document: dict[str, Any]) -> None:
        sources = document["contents"]["raw_imports"]
        sources.append(dict(sources[0]))

    tampered = _tampered_manifest(archive, tmp_path / "tampered.tar.gz", repeat_the_only_source)

    with pytest.raises(ArchiveError) as raised:
        _extractor(tampered, tmp_path / "target").manifest()

    assert raised.value.code is ArchiveErrorCode.MANIFEST_INVALID


def test_a_source_that_claims_the_database_member_is_refused(
    services: TrackServices, tmp_path: Path
) -> None:
    """Two members of one name are two sets of bytes for one destination."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    def rename_the_source_onto_the_database(document: dict[str, Any]) -> None:
        document["contents"]["raw_imports"][0]["name"] = DATABASE_MEMBER

    tampered = _tampered_manifest(
        archive, tmp_path / "collided.tar.gz", rename_the_source_onto_the_database
    )

    with pytest.raises(ArchiveError) as raised:
        _extractor(tampered, tmp_path / "target").manifest()

    assert raised.value.code is ArchiveErrorCode.MANIFEST_INVALID


def test_the_manifest_is_not_one_of_the_members_it_describes(
    services: TrackServices, tmp_path: Path
) -> None:
    """An archive's account of itself cannot be an entry in that account."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    def claim_the_manifest(document: dict[str, Any]) -> None:
        document["contents"]["raw_imports"][0]["name"] = MANIFEST_MEMBER

    tampered = _tampered_manifest(archive, tmp_path / "recursive.tar.gz", claim_the_manifest)

    with pytest.raises(ArchiveError) as raised:
        _extractor(tampered, tmp_path / "target").manifest()

    assert raised.value.code is ArchiveErrorCode.MANIFEST_INVALID


def test_the_counts_are_the_databases_to_state_and_are_not_second_guessed(
    services: TrackServices, tmp_path: Path
) -> None:
    """The manifest describes integrity; the database is the authority on content.

    A count that disagrees with the member list is not refused, and that is a
    decision rather than an omission. `counts` answers "what does this archive
    hold" in an operator's terms, and only the captured database can answer it
    -- one raw import row is one source whatever the container looks like. A
    codec that reconciled the two would make the manifest a second authority for
    a number the database owns, and the day they disagreed the restore would
    have to pick a winner.

    What the archive is not allowed to do is *carry* something it did not
    describe, or describe a member twice; both of those are checked, and both
    are statements about the container alone.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    def overstate_the_sources(document: dict[str, Any]) -> None:
        document["counts"]["raw_imports"] = 100

    tampered = _tampered_manifest(archive, tmp_path / "overstated.tar.gz", overstate_the_sources)
    target = tmp_path / "target"

    extractor = _extractor(tampered, target)
    manifest = extractor.manifest()
    extractor.verify(manifest)

    assert manifest.counts.raw_imports == 100, "the manifest is read as written"
    assert len(manifest.contents.raw_imports) == 1
    assert len(_raw_members(tampered)) == 1, "and the archive carries exactly what it lists"


# --- compatibility --------------------------------------------------------


def _manifest(
    *, format_name: str = ARCHIVE_FORMAT_NAME, format_version: int = 1, schema: int = 5
) -> ArchiveManifest:
    """Return a manifest with only the fields compatibility reads."""
    return ArchiveManifest(
        format_name=format_name,
        format_version=format_version,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        trackvault_version="1",
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
        "..\\escaped.txt",
        "raw\\..\\..\\escaped.raw",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "\\\\server\\share\\escaped.raw",
        "raw//../escaped.raw",
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


@pytest.mark.parametrize(
    "hostile_name",
    ["../escaped.raw", "/etc/passwd", "raw/../../escaped.raw", "..\\escaped.raw"],
)
def test_a_manifest_that_names_an_escaping_member_is_refused_too(
    services: TrackServices, tmp_path: Path, hostile_name: str
) -> None:
    """The second of the two independent checks, exercised on its own.

    A member is extracted only when the manifest declares it *and* its name
    survives an allow-list check. Every other traversal test here is stopped by
    the first check, which means the second one -- the one that would matter if
    a manifest were ever trusted a little too far -- is never reached. So this
    archive's manifest declares the hostile member, and the name has to be
    refused on its own merits.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    payload = b"owned"
    document = json.loads(members[MANIFEST_MEMBER])
    document["contents"]["raw_imports"].append(
        {
            "name": hostile_name,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    members[MANIFEST_MEMBER] = json.dumps(document).encode("utf-8")
    members[hostile_name] = payload
    tampered = _repack(tmp_path / "tampered.tar.gz", members)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.MEMBER_REFUSED
    assert not (tmp_path / "escaped.raw").exists()
    assert not (tmp_path.parent / "escaped.raw").exists()


def test_a_hard_link_member_is_refused(services: TrackServices, tmp_path: Path) -> None:
    """A hard link writes through to a file that is already there.

    Softer-looking than a symbolic link and refused by the same rule, because
    the rule is "a member is a regular file" rather than a list of the link
    types somebody thought of.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as container:
        for name, content in _members(archive).items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            container.addfile(info, io.BytesIO(content))
        link = tarfile.TarInfo("raw/hard")
        link.type = tarfile.LNKTYPE
        link.linkname = DATABASE_MEMBER
        container.addfile(link)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.MEMBER_REFUSED


def test_a_directory_member_is_refused(services: TrackServices, tmp_path: Path) -> None:
    """Only regular files are extracted, and a directory is not one.

    The paths a restore writes to are built by this code, so it never needs a
    member to create one for it.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as container:
        for name, content in _members(archive).items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            container.addfile(info, io.BytesIO(content))
        directory = tarfile.TarInfo("raw/subdir")
        directory.type = tarfile.DIRTYPE
        container.addfile(directory)

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.MEMBER_REFUSED


def test_a_second_member_of_the_same_name_cannot_smuggle_other_bytes(
    services: TrackServices, tmp_path: Path
) -> None:
    """A duplicate entry is the oldest trick in the tar format.

    Two members of one name, the first matching the manifest and the second
    replacing it on extraction, would let an archive pass a checksum it does not
    keep. Whichever of the two the extraction ends up holding, the digests are
    verified against the manifest afterwards, so the archive is refused.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    members = _members(archive)
    raw_name = next(name for name in members if name.startswith("raw/"))
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as container:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            container.addfile(info, io.BytesIO(content))
        smuggled = tarfile.TarInfo(raw_name)
        payload = b"different bytes under a name the manifest already vouched for"
        smuggled.size = len(payload)
        container.addfile(smuggled, io.BytesIO(payload))

    extractor = _extractor(tampered, tmp_path / "target")
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code is ArchiveErrorCode.CHECKSUM_MISMATCH


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


@pytest.mark.parametrize("keep", [0.05, 0.5, 0.95])
def test_a_truncated_archive_is_refused_rather_than_half_restored(
    services: TrackServices, tmp_path: Path, keep: float
) -> None:
    """The shape a backup takes when a disk filled or a copy was interrupted.

    More likely than any hostile archive here, and more dangerous than it looks:
    the manifest is the first member, so a container cut off part-way can still
    describe an archive it no longer carries. Three cut points, because a
    truncation before the manifest, in the middle of the members and just short
    of the end fail at three different places.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    whole = archive.read_bytes()
    truncated = tmp_path / "truncated.tar.gz"
    truncated.write_bytes(whole[: int(len(whole) * keep)])
    target = tmp_path / "target"

    extractor = _extractor(truncated, target)
    with pytest.raises(ArchiveError) as raised:
        extractor.verify(extractor.manifest())

    assert raised.value.code in {
        ArchiveErrorCode.ARCHIVE_UNREADABLE,
        ArchiveErrorCode.MANIFEST_INVALID,
        ArchiveErrorCode.INCOMPLETE,
        ArchiveErrorCode.CHECKSUM_MISMATCH,
    }
    assert not (target / "trackvault.sqlite3").exists()


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
            trackvault_version=manifest.trackvault_version,
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


def test_a_dry_run_against_a_populated_archive_touches_none_of_it(
    services: TrackServices, tmp_path: Path
) -> None:
    """The dry-run that matters: the one run against something worth losing.

    A fresh directory proves little -- there is nothing there to damage. This
    one inspects an archive that already holds a database, a managed original
    and an installed map package, and asserts every byte and every timestamp
    under the data directory is the one it was.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    target_root = tmp_path / "target"
    target = build_services(Settings(data_dir=target_root))
    target.prepare_storage()
    target.import_tracks(
        ImportRequest(
            content=(FIXTURES / "planned-route-instructions.gpx").read_bytes(),
            original_filename="planned-route-instructions.gpx",
            input_channel=InputChannel.LOCAL_FILE,
        )
    )
    (target.settings.map_storage_dir / "packages" / "region").mkdir(parents=True, exist_ok=True)
    (target.settings.map_storage_dir / "packages" / "region" / "map.mbtiles").write_bytes(b"tiles")
    before = _snapshot_of(target_root)
    assert len(before) >= 3

    inspection = services.restore_archive.inspect(_extractor(archive, target_root))

    assert _snapshot_of(target_root) == before
    assert inspection.target_holds_data


def _snapshot_of(root: Path) -> dict[str, tuple[int, int, str]]:
    """Return every stored file's size, modification time and digest.

    Journal files are excluded and only those: reading a write-ahead-logging
    database creates them whoever opens it, and the dry-run does read the
    destination to answer "would this replace anything".
    """
    return {
        str(path.relative_to(root)): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.endswith(("-wal", "-shm"))
    }


def test_a_dry_run_reports_everything_a_decision_needs(
    services: TrackServices, tmp_path: Path
) -> None:
    """What an operator is asked to decide on, before anything is touched.

    Each of these answers a different question: what this file is, whether this
    build can read it, how much is in it, what it will not bring back, and
    whether saying yes would replace something.
    """
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)

    inspection = services.restore_archive.inspect(_extractor(archive, tmp_path / "target"))

    assert inspection.manifest.format_name == ARCHIVE_FORMAT_NAME
    assert inspection.manifest.format_version == ARCHIVE_FORMAT_VERSION
    assert inspection.manifest.trackvault_version
    assert inspection.manifest.schema_version > 0
    assert inspection.compatibility is ArchiveCompatibility.SUPPORTED
    assert inspection.manifest.counts.raw_imports == 1
    assert inspection.manifest.counts.tracks == 1
    assert inspection.manifest.counts.analyzed_tracks == 1
    assert [omission.kind for omission in inspection.manifest.omissions] == ["map_packages"]
    assert inspection.required_bytes > 0
    assert inspection.restorable
    assert not inspection.target_holds_data


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
    assert (target / "trackvault.sqlite3").is_file()
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

    assert not (target / "trackvault.sqlite3").exists()
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
    (target / "trackvault.sqlite3").write_bytes(b"old")
    (target / "trackvault.sqlite3-wal").write_bytes(b"stale journal")
    (target / "trackvault.sqlite3-shm").write_bytes(b"stale shm")

    services.restore_archive(_extractor(archive, target), replace=True)

    assert not (target / "trackvault.sqlite3-wal").exists()
    assert not (target / "trackvault.sqlite3-shm").exists()


def test_a_restore_leaves_no_staging_behind(services: TrackServices, tmp_path: Path) -> None:
    """A directory named after a failed restore is a directory somebody backs up."""
    _import(services, "recorded-measurements.gpx")
    archive = _write_archive(services, tmp_path)
    target = tmp_path / "fresh"

    services.restore_archive(_extractor(archive, target))

    assert not [entry for entry in target.iterdir() if entry.name.startswith(".")]


# --- atomicity and durability ---------------------------------------------
#
# A backup that is only in the page cache is a backup that a power cut takes
# away, and the moment somebody needs one is disproportionately often the moment
# after something went wrong with the machine. So the writing sequence is
# asserted end to end:
#
#     write  ->  flush  ->  fsync the file  ->  atomic rename  ->  fsync the directory
#
# The last step is the one that is easy to leave off and impossible to notice.
# It is also not observable from the file system afterwards -- a synchronised
# directory looks exactly like an unsynchronised one -- so what these tests watch
# is the system call itself, on real descriptors, during a real backup. What is
# checked is which *object* was flushed and when, never which helper was called.


@dataclass(frozen=True, slots=True)
class _Flushed:
    """One `fsync`, identified by what it was applied to.

    Attributes:
        device: The file system the descriptor belongs to.
        inode: Which object on it. Together with ``device`` this identifies the
            target without a path, which is what makes the check survive a
            rename happening in the middle of the sequence.
        is_directory: Whether the descriptor was a directory.
        archive_existed: Whether the archive was already under its own name when
            this flush happened. This is how the *ordering* is asserted.
    """

    device: int
    inode: int
    is_directory: bool
    archive_existed: bool


def _record_flushes(monkeypatch: pytest.MonkeyPatch, destination: Path) -> list[_Flushed]:
    """Record every `fsync` the process makes, and still make it."""
    flush = os.fsync
    recorded: list[_Flushed] = []

    def recording(descriptor: int) -> None:
        status = os.fstat(descriptor)
        recorded.append(
            _Flushed(
                device=status.st_dev,
                inode=status.st_ino,
                is_directory=stat.S_ISDIR(status.st_mode),
                archive_existed=destination.exists(),
            )
        )
        flush(descriptor)

    monkeypatch.setattr(os, "fsync", recording)
    return recorded


def _flushes_of(recorded: list[_Flushed], path: Path) -> list[_Flushed]:
    """Return the recorded flushes that were applied to one path."""
    status = path.stat()
    return [
        entry for entry in recorded if (entry.device, entry.inode) == (status.st_dev, status.st_ino)
    ]


def test_a_published_backup_is_on_the_disk_and_not_only_in_the_page_cache(
    services: TrackServices, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rename lives in a directory, and a directory has to be flushed too.

    Flushing the archive makes its bytes durable. The rename that gives those
    bytes the archive's *name* is an entry in the destination's directory, and
    an entry still in the page cache when the power goes leaves a deployment
    with a complete backup nothing points at.
    """
    _import(services, "recorded-measurements.gpx")
    destination = tmp_path / "backup.tar.gz"
    recorded = _record_flushes(monkeypatch, destination)

    services.create_archive(_builder(services, destination))

    directory = _flushes_of(recorded, destination.parent)
    assert directory, "the directory the archive was renamed into was never flushed"
    assert all(entry.is_directory for entry in directory)


def test_the_archive_is_flushed_before_the_rename_and_its_directory_after(
    services: TrackServices, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The order is the guarantee, not the two calls.

    A directory flushed before the rename records the absence of the archive,
    and a file flushed after it has already been published under a name whose
    bytes were never promised. This is the sequence the managed raw storage
    makes for an imported original, made here for a backup.
    """
    _import(services, "recorded-measurements.gpx")
    destination = tmp_path / "backup.tar.gz"
    recorded = _record_flushes(monkeypatch, destination)

    services.create_archive(_builder(services, destination))

    container = _flushes_of(recorded, destination)
    directory = _flushes_of(recorded, destination.parent)
    assert [entry.is_directory for entry in container] == [False], "the archive itself is flushed"
    assert [entry.is_directory for entry in directory] == [True], "and so is its directory"
    assert not container[0].archive_existed, "the file, while it was still the partial one"
    assert directory[0].archive_existed, "the directory, once the rename had happened"
    assert recorded.index(container[0]) < recorded.index(directory[0])


def test_a_backup_that_cannot_be_made_durable_is_not_reported_as_one(
    services: TrackServices, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And leaves nothing behind under the archive's own name.

    The file is complete at that point and already renamed, which is exactly why
    it has to go: a backup the command told somebody it could not write is the
    one they find later and trust.
    """
    _import(services, "recorded-measurements.gpx")
    destination = tmp_path / "backup.tar.gz"
    flush = os.fsync

    def refuse_directories(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "the directory could not be flushed")
        flush(descriptor)

    monkeypatch.setattr(os, "fsync", refuse_directories)

    with pytest.raises(ArchiveError) as refused:
        services.create_archive(_builder(services, destination))

    assert refused.value.code is ArchiveErrorCode.WRITE_FAILED
    assert not destination.exists()
    assert not list(tmp_path.glob("*.part")), "nor under the name it was written under"
    assert not [entry for entry in tmp_path.iterdir() if entry.name.startswith(".")]


# --- what a backup is allowed to call complete ----------------------------
#
# The failure this section exists for is not a damaged archive. It is a *valid*
# archive that quietly holds less than the deployment did:
#
#     database    A  B  C
#     storage     A  B
#     archive     A  B          <- and a manifest saying "complete"
#
# Nothing about that file is detectably wrong until somebody restores it and
# looks for C. So the member list is taken from the captured snapshot rather
# than from whatever the storage directory happens to contain, and a source the
# snapshot names and the disk cannot produce fails the backup.


def _raw_members(archive: Path) -> set[str]:
    """Return the names of the raw members one archive carries."""
    return {name for name in _members(archive) if name.startswith("raw/")}


def _artifact_of(services: TrackServices, sha256: str) -> Path:
    """Return where one managed original is stored."""
    return services.raw_store.path_for(sha256)


def test_a_backup_refuses_a_source_the_deployment_can_no_longer_produce(
    services: TrackServices, tmp_path: Path
) -> None:
    """A missing original must not become an archive that omits it in silence.

    This is the whole point of the section. The remaining sources are healthy,
    the database is sound, and the archive that came out of this before was
    indistinguishable from a complete one.
    """
    _import(services, "recorded-measurements.gpx")
    lost, _ = _import(services, "planned-route-instructions.gpx")
    _artifact_of(services, lost).unlink()
    destination = tmp_path / "backup.tar.gz"

    with pytest.raises(ArchiveError) as raised:
        services.create_archive(_builder(services, destination))

    assert raised.value.code is ArchiveErrorCode.SOURCE_INCOMPLETE
    assert not destination.exists()
    assert not list(tmp_path.glob("*.part"))


def test_a_backup_refuses_a_source_whose_bytes_are_not_its_own(
    services: TrackServices, tmp_path: Path
) -> None:
    """A backup of a corruption is a second copy of the damage, not a backup."""
    _import(services, "recorded-measurements.gpx")
    damaged, _ = _import(services, "planned-route-instructions.gpx")
    _artifact_of(services, damaged).write_bytes(b"not the bytes this file is named after")
    destination = tmp_path / "backup.tar.gz"

    with pytest.raises(ArchiveError) as raised:
        services.create_archive(_builder(services, destination))

    assert raised.value.code is ArchiveErrorCode.CHECKSUM_MISMATCH
    assert not destination.exists()


def test_an_archive_carries_exactly_the_sources_its_database_names(
    services: TrackServices, tmp_path: Path
) -> None:
    """The invariant a complete backup rests on, asserted directly.

    One member per raw import the captured database knows about. A count and a
    member list that can drift apart are two claims about the same archive, and
    the restore believes the one that is easier to satisfy.
    """
    _import(services, "recorded-measurements.gpx")
    _import(services, "planned-route-instructions.gpx")

    archive = _write_archive(services, tmp_path)

    manifest = _extractor(archive, tmp_path / "target").manifest()
    assert manifest.counts.raw_imports == 2
    assert len(manifest.contents.raw_imports) == manifest.counts.raw_imports
    assert len(_raw_members(archive)) == manifest.counts.raw_imports


def test_a_stored_file_no_database_row_names_is_declared_rather_than_shipped(
    services: TrackServices, tmp_path: Path
) -> None:
    """Debris is not exported, and an archive that leaves something out says so.

    A file the database cannot explain carries no import time, no filename and
    no classification -- there is nothing to restore it *as*. What must not
    happen is the silence: the manifest names it as an omission with a count, so
    "complete" keeps meaning complete.
    """
    kept, _ = _import(services, "recorded-measurements.gpx")
    orphan = _artifact_of(services, "a" * 64)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"bytes no row can account for")

    archive = _write_archive(services, tmp_path)

    manifest = _extractor(archive, tmp_path / "target").manifest()
    assert [item.sha256 for item in manifest.contents.raw_imports] == [kept]
    unreferenced = next(
        omission for omission in manifest.omissions if omission.kind == "unreferenced_raw_objects"
    )
    assert unreferenced.count == 1
    assert orphan.is_file()


def test_an_archive_that_leaves_no_debris_behind_declares_none(
    services: TrackServices, tmp_path: Path
) -> None:
    """A complete archive says so, rather than saying nothing about omissions."""
    _import(services, "recorded-measurements.gpx")

    manifest = services.create_archive(_builder(services, tmp_path / "backup.tar.gz"))

    assert not [
        omission for omission in manifest.omissions if omission.kind == "unreferenced_raw_objects"
    ]
    assert [omission.kind for omission in manifest.omissions] == ["map_packages"]


def test_a_backup_describes_the_snapshot_it_captured_and_not_the_live_archive(
    services: TrackServices, tmp_path: Path
) -> None:
    """The source list comes from the captured database, at one instant.

    A member list read from the live storage directory and counts read from the
    snapshot are two views of a deployment that is still running. Importing
    between the two would put a member in the container that the manifest's own
    counts do not cover.
    """
    _import(services, "recorded-measurements.gpx")
    builder = _builder(services, tmp_path / "backup.tar.gz")

    staged = builder.stage()
    _import(services, "planned-route-instructions.gpx")
    builder.finish(
        ArchiveManifest(
            format_name=ARCHIVE_FORMAT_NAME,
            format_version=ARCHIVE_FORMAT_VERSION,
            created_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            trackvault_version="test",
            schema_version=staged.schema_version,
            contents=staged.contents,
            counts=staged.counts,
            omissions=staged.omissions,
        )
    )

    assert staged.counts.raw_imports == 1
    assert len(staged.contents.raw_imports) == 1
    assert len(_raw_members(tmp_path / "backup.tar.gz")) == 1


def test_a_backup_taken_while_an_import_runs_is_still_a_valid_backup(
    services: TrackServices, tmp_path: Path
) -> None:
    """The operating rule a backup rests on, asserted rather than assumed.

    A backup may run while the archive is being used, and the reason it may is
    an ordering: the managed original is written *before* the row that names it.
    A snapshot taken between the two therefore sees neither -- never a row whose
    bytes are missing, which is precisely what the source-consistency rule fails
    a backup for.

    So this takes a backup from inside an import, at the one instant the two
    halves are not both present, and requires it to succeed.
    """
    _import(services, "recorded-measurements.gpx")
    archives: list[Path] = []
    store = services.raw_store.store

    def _store_then_back_up(content: bytes, sha256: str) -> None:
        """Stand where an import is between its two halves, and back up there."""
        store(content, sha256)
        destination = tmp_path / f"midway-{len(archives)}.tar.gz"
        services.create_archive(_builder(services, destination))
        archives.append(destination)

    services.raw_store.store = _store_then_back_up  # type: ignore[method-assign]
    try:
        _import(services, "planned-route-instructions.gpx")
    finally:
        services.raw_store.store = store  # type: ignore[method-assign]

    assert archives, "the import did not reach the point this test is about"
    manifest = _extractor(archives[0], tmp_path / "target").manifest()
    # The source being imported has its bytes on disk and no row yet, so the
    # snapshot does not name it -- and it is counted as debris rather than
    # silently skipped.
    assert manifest.counts.raw_imports == 1
    assert len(manifest.contents.raw_imports) == 1
    unreferenced = next(
        omission for omission in manifest.omissions if omission.kind == "unreferenced_raw_objects"
    )
    assert unreferenced.count == 1


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
    assert (fresh / "trackvault.sqlite3").is_file()

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
    (damaged / "trackvault.sqlite3").write_bytes(b"this is not a database at all")

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
    assert (fresh / "trackvault.sqlite3").is_file()
    assert (fresh / "raw").is_dir()
    assert not list((fresh / "raw").iterdir())
