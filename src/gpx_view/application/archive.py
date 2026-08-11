"""The whole archive as one portable object, and getting it back again.

A track export hands out one recording. This hands out the *deployment*: every
original import, the database that says what the archive made of them, and a
manifest describing both well enough that a restore can prove it got everything.

```
create    consistent snapshot + every raw artifact + a manifest that checksums them
inspect   read the manifest, decide compatibility, change nothing
restore   validate, verify, stage, publish -- in that order, or not at all
```

**A backup is not a tar of the data directory.** Copying a live SQLite file with
write-ahead logging on produces a database that may not open, and a directory
copied while an import runs can hold an artifact whose row was never committed.
So the database is captured through SQLite's own online backup, every artifact is
checked against the hash it is filed under, and the manifest records what was
taken so a restore can tell "all of it" from "most of it".

**What is deliberately not in it.** Installed map packages are public datasets
that can be fetched again, and they are by far the largest thing a deployment
holds. They are excluded -- and the manifest *says* they are excluded, in
:class:`ArchiveOmission`. An archive that quietly held part of a deployment while
calling itself a backup is the failure this whole module is arranged against.

Paths appear nowhere here. Where an archive lives and how its container is
written are infrastructure decisions reached through :class:`ArchiveBuilder` and
:class:`ArchiveExtractor`, exactly as map storage is reached through its own
ports.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from gpx_view.application.ports import Clock

ARCHIVE_FORMAT_NAME = "gpx-view-archive"
"""What this container is, written into every manifest.

A name rather than a file extension: an operator may rename the file, and a
restore has to be able to refuse a tar of holiday photos on the strength of what
is inside it.
"""

ARCHIVE_FORMAT_VERSION = 1
"""The layout of the archive itself, independent of the database schema.

Two versions that move for different reasons. The schema version changes when a
table changes; this changes when the *container* does -- another member, a
renamed directory, a manifest field a reader must understand. An archive whose
format version is newer than this build knows is refused rather than guessed at.
"""

MANIFEST_MEMBER = "manifest.json"
"""The first member of every archive, so a dry-run reads one small file."""

DATABASE_MEMBER = "database/gpx-view.sqlite3"
"""Where the captured database sits inside the container."""

RAW_MEMBER_PREFIX = "raw/"
"""Where the original imports sit, mirroring the managed storage layout."""


class ArchiveErrorCode(StrEnum):
    """Why an archive could not be written, read or restored.

    Stable, lower case and underscore separated, exactly as the import codes are:
    they reach an operator's terminal and a log, and they are not renamed once
    released.

    Attributes:
        ARCHIVE_UNREADABLE: The container could not be opened or is truncated.
        MANIFEST_INVALID: There is no manifest, or it does not describe an
            archive this build can interpret.
        FORMAT_UNSUPPORTED: The manifest names another format, or a version of
            this one that postdates this build.
        SCHEMA_UNSUPPORTED: The database inside was written by a newer build.
            The same rule the migration authority already applies: a database
            from the future is refused rather than downgraded silently.
        CHECKSUM_MISMATCH: A member's bytes are not the ones the manifest names.
        MEMBER_REFUSED: The container holds a member a restore will not extract
            -- an absolute path, a traversal, a link, a device node.
        INCOMPLETE: A member the manifest promises is not in the container.
        DATABASE_INVALID: The captured database does not open or fails its own
            integrity check.
        TARGET_OCCUPIED: Restoring here would replace data that is already
            present, and nobody said to.
        WRITE_FAILED: The archive could not be written. Nothing partial is left
            behind that could be mistaken for a backup.
    """

    ARCHIVE_UNREADABLE = "archive_unreadable"
    MANIFEST_INVALID = "archive_manifest_invalid"
    FORMAT_UNSUPPORTED = "archive_format_unsupported"
    SCHEMA_UNSUPPORTED = "archive_schema_unsupported"
    CHECKSUM_MISMATCH = "archive_checksum_mismatch"
    MEMBER_REFUSED = "archive_member_refused"
    INCOMPLETE = "archive_incomplete"
    DATABASE_INVALID = "archive_database_invalid"
    TARGET_OCCUPIED = "restore_target_occupied"
    WRITE_FAILED = "archive_write_failed"


class ArchiveError(Exception):
    """An archive operation could not be completed, for a named reason.

    Attributes:
        code: The stable error code.
        detail: A short structural hint. Never a coordinate, a payload excerpt or
            an absolute path -- a backup diagnostic must not become the side
            channel the rest of the application refuses to be.
    """

    def __init__(self, code: ArchiveErrorCode, detail: str = "") -> None:
        """Build the error from its code and an optional structural hint."""
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


class ArchiveCompatibility(StrEnum):
    """Whether this build can restore a given archive.

    Three answers rather than a boolean, because "no" and "not yet" call for
    different reactions: one is a dead end, the other is an upgrade away.

    Attributes:
        SUPPORTED: This build restores it as it is.
        MIGRATION_REQUIRED: The database inside is older than this build's
            schema. It restores, and the ordinary start-up migration brings it
            forward -- the same path an in-place upgrade takes.
        UNSUPPORTED: Another format, a newer container, or a database from a
            build that knows more than this one.
    """

    SUPPORTED = "supported"
    MIGRATION_REQUIRED = "migration_required"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ArchivedFile:
    """One member of an archive, and the proof of what it holds.

    Attributes:
        name: Where the member sits inside the container. Always relative, always
            forward-slashed, and never used as a path until a restore has decided
            it is safe.
        size_bytes: How large the member is.
        sha256: The digest its bytes must have. For a raw import this is also its
            identity, which is what lets a restore verify the archive with the
            same act that identifies its contents.
    """

    name: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveContents:
    """Everything one archive carries.

    Attributes:
        database: The captured database.
        raw_imports: Every original import, byte-identical.
    """

    database: ArchivedFile
    raw_imports: tuple[ArchivedFile, ...]

    @property
    def total_size_bytes(self) -> int:
        """Return how much the members add up to, uncompressed."""
        return self.database.size_bytes + sum(item.size_bytes for item in self.raw_imports)

    def checksums(self) -> dict[str, str]:
        """Return every member's expected digest, keyed by its name."""
        return {item.name: item.sha256 for item in (self.database, *self.raw_imports)}


@dataclass(frozen=True, slots=True)
class ArchiveCounts:
    """What the archive holds, in the terms an operator recognises.

    Not derivable from the file list, and that is the point: a restore's dry-run
    has to say "412 tracks and 37 corrections" before anybody commits to it,
    without opening the database it has not verified yet.

    Attributes:
        raw_imports: How many original sources.
        tracks: How many tracks the current generations hold.
        classification_overrides: How many kinds the user corrected.
        user_metadata: How many titles or notes the user wrote.
    """

    raw_imports: int
    tracks: int
    classification_overrides: int
    user_metadata: int


@dataclass(frozen=True, slots=True)
class StagedArchive:
    """What a builder captured, and what that material says about itself.

    Every field is read from the **staged** database rather than from the live
    deployment. That distinction is the reason this type exists: a manifest
    describes the database inside its own archive, and taking the schema version
    or the counts from a connection to something else is a second authority --
    one that disagrees the moment anybody archives a data directory that is not
    the running one.

    Attributes:
        contents: The members, with their sizes and digests.
        schema_version: The schema of the database that was captured.
        counts: What that database holds.
    """

    contents: "ArchiveContents"
    schema_version: int
    counts: "ArchiveCounts"


@dataclass(frozen=True, slots=True)
class ArchiveOmission:
    """Something a deployment holds that this archive deliberately leaves out.

    The manifest states these rather than staying silent. "Complete" and
    "complete except for the part nobody mentioned" are different promises, and
    only one of them is safe to make about a backup.

    Attributes:
        kind: What was left out, as a stable identifier.
        reason: Why it is safe to leave out, in one sentence an operator reads.
    """

    kind: str
    reason: str


MAP_PACKAGES_OMITTED = ArchiveOmission(
    kind="map_packages",
    reason="offline maps are public datasets that can be downloaded again",
)
"""The one omission this format currently makes, stated in every manifest."""


@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    """What an archive says about itself.

    The first member of the container and the authority on the rest of it: a
    restore verifies members against this, never the other way round.

    Attributes:
        format_name: Always :data:`ARCHIVE_FORMAT_NAME`.
        format_version: The container layout this archive uses.
        created_at: When the archive was written, timezone-aware.
        gpx_view_version: Which release wrote it.
        schema_version: The database schema version inside it.
        contents: Every member, with its size and digest.
        counts: What it holds, for a dry-run to report.
        omissions: What a deployment holds that this archive does not.

    Raises:
        ValueError: If the timestamp is naive. An archive that cannot say *when*
            it was taken is an archive nobody can order against another.
    """

    format_name: str
    format_version: int
    created_at: datetime
    gpx_view_version: str
    schema_version: int
    contents: ArchiveContents
    counts: ArchiveCounts
    omissions: tuple[ArchiveOmission, ...] = (MAP_PACKAGES_OMITTED,)

    def __post_init__(self) -> None:
        """Reject a manifest that could not be ordered against another."""
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")


def compatibility_of(
    manifest: ArchiveManifest, installed_schema_version: int
) -> ArchiveCompatibility:
    """Decide whether this build can restore that archive.

    One rule, in one place, so a dry-run and a real restore cannot disagree about
    what they are looking at.

    A *newer* schema is refused for the same reason the database itself refuses
    one: this build does not know what the columns mean, and opening it anyway
    would be a downgrade nobody asked for. An *older* schema restores and is
    migrated at the next start-up -- exactly the path an in-place upgrade takes,
    which is what makes a restore from an old backup a supported act rather than
    a hopeful one.
    """
    if manifest.format_name != ARCHIVE_FORMAT_NAME:
        return ArchiveCompatibility.UNSUPPORTED
    if manifest.format_version > ARCHIVE_FORMAT_VERSION:
        return ArchiveCompatibility.UNSUPPORTED
    if manifest.schema_version > installed_schema_version:
        return ArchiveCompatibility.UNSUPPORTED
    if manifest.schema_version < installed_schema_version:
        return ArchiveCompatibility.MIGRATION_REQUIRED
    return ArchiveCompatibility.SUPPORTED


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    """What a dry-run reports, having changed nothing.

    Attributes:
        manifest: What the archive says about itself.
        compatibility: Whether this build can restore it.
        target_holds_data: Whether restoring would replace data already present.
        required_bytes: How much space the extracted material needs.
    """

    manifest: ArchiveManifest
    compatibility: ArchiveCompatibility
    target_holds_data: bool
    required_bytes: int

    @property
    def restorable(self) -> bool:
        """Report whether a restore would be attempted at all."""
        return self.compatibility is not ArchiveCompatibility.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    """What a completed restore did.

    Attributes:
        manifest: The archive that was restored.
        compatibility: What it was judged to be before it was published.
        replaced_existing_data: Whether data that was already there was replaced.
    """

    manifest: ArchiveManifest
    compatibility: ArchiveCompatibility
    replaced_existing_data: bool


class ArchiveBuilder(Protocol):
    """Assembles one archive. Infrastructure decides where and how.

    The steps are separate on purpose. Staging is what can fail slowly -- a disk
    fills, an artifact is corrupt -- and it happens before a manifest claims
    anything. ``finish`` is the act that makes an archive exist, and it is the
    one that has to be atomic.
    """

    def stage(self) -> StagedArchive:
        """Capture a consistent copy of everything an archive holds, and describe it.

        What is returned describes the staged material, read from it. A builder
        that reported the *live* deployment's schema and counts instead would
        produce a manifest about a different database.

        Raises:
            ArchiveError: If the database could not be captured or an artifact
                does not match the hash it is filed under. A corrupt source is
                not quietly copied into a backup: it would make the backup a
                second copy of the damage.
        """
        ...

    def finish(self, manifest: ArchiveManifest) -> None:
        """Write the manifest and the staged material as one atomic act.

        Nothing partial may survive a failure here. A half-written file with a
        backup's name is worse than no backup, because it is the one somebody
        will find and trust.
        """
        ...

    def abandon(self) -> None:
        """Discard everything staged, after a failure or a change of mind."""
        ...


class ArchiveExtractor(Protocol):
    """Reads one archive and, only when told to, publishes it."""

    def manifest(self) -> ArchiveManifest:
        """Read the manifest alone, extracting nothing else.

        Raises:
            ArchiveError: ``archive_unreadable`` or ``archive_manifest_invalid``.
        """
        ...

    def verify(self, manifest: ArchiveManifest) -> None:
        """Extract into staging and check every member against the manifest.

        Extraction is where an archive stops being data and starts being a file
        system operation, so it is also where a hostile one would act. Members
        are refused rather than sanitised.

        Raises:
            ArchiveError: ``archive_member_refused``, ``archive_checksum_mismatch``,
                ``archive_incomplete`` or ``archive_database_invalid``.
        """
        ...

    def target_holds_data(self) -> bool:
        """Report whether publishing would replace data that is already there."""
        ...

    def free_bytes(self) -> int:
        """Return how much room the destination has, for a dry-run to report."""
        ...

    def publish(self) -> None:
        """Move the verified material into place, keeping the old data until it is."""
        ...

    def abandon(self) -> None:
        """Discard the staged material without touching what is in place."""
        ...


class CreateArchive:
    """Write everything this deployment holds into one portable archive.

    The use case owns the manifest -- what an archive claims about itself is a
    contract, not a detail of whichever writer produced it -- and the builder
    owns the container and reports what it captured.
    """

    def __init__(self, clock: Clock, version: str) -> None:
        """Wire the use case to the clock and the release that will stamp it."""
        self._clock = clock
        self._version = version

    def __call__(self, builder: ArchiveBuilder) -> ArchiveManifest:
        """Stage, describe and write one archive, or leave nothing behind.

        Raises:
            ArchiveError: If the material could not be captured or written.
        """
        try:
            staged = builder.stage()
            manifest = ArchiveManifest(
                format_name=ARCHIVE_FORMAT_NAME,
                format_version=ARCHIVE_FORMAT_VERSION,
                created_at=self._clock.now(),
                gpx_view_version=self._version,
                # From the staged database, not from a live one. See StagedArchive.
                schema_version=staged.schema_version,
                contents=staged.contents,
                counts=staged.counts,
            )
            builder.finish(manifest)
        except BaseException:
            builder.abandon()
            raise
        return manifest


class RestoreArchive:
    """Put an archive back, having proved first that it can be put back.

    The order is the contract:

    ```
    manifest -> compatibility -> checksums -> database integrity -> publish
    ```

    Nothing in the destination is touched until every step before ``publish``
    has passed. A restore that fails leaves the deployment exactly as it was,
    which is the property that makes trying one safe.
    """

    def __init__(self, installed_schema_version: int) -> None:
        """Wire the use case to the schema version this build installs."""
        self._installed_schema_version = installed_schema_version

    def inspect(self, extractor: ArchiveExtractor) -> ArchiveInspection:
        """Report what an archive is and what restoring it would do.

        Reads the manifest and nothing else. A dry-run that extracted the
        archive to describe it would be a restore that promised not to be.
        """
        manifest = extractor.manifest()
        return ArchiveInspection(
            manifest=manifest,
            compatibility=compatibility_of(manifest, self._installed_schema_version),
            target_holds_data=extractor.target_holds_data(),
            required_bytes=manifest.contents.total_size_bytes,
        )

    def __call__(self, extractor: ArchiveExtractor, *, replace: bool = False) -> RestoreOutcome:
        """Restore the archive, refusing rather than overwriting by surprise.

        Args:
            extractor: The archive, and the destination it would be published to.
            replace: Whether data already in the destination may be replaced.
                Default ``False``: a restore that silently overwrote an archive
                would destroy the very thing somebody was trying to protect.

        Raises:
            ArchiveError: ``archive_format_unsupported`` or
                ``archive_schema_unsupported`` if this build cannot restore it,
                ``restore_target_occupied`` if data is present and ``replace`` is
                not set, and the verification codes if the archive is damaged.
        """
        manifest = extractor.manifest()
        compatibility = compatibility_of(manifest, self._installed_schema_version)
        try:
            _require_restorable(manifest, compatibility)
            occupied = extractor.target_holds_data()
            if occupied and not replace:
                raise ArchiveError(
                    ArchiveErrorCode.TARGET_OCCUPIED,
                    "the destination already holds an archive; pass --replace to replace it",
                )
            extractor.verify(manifest)
            extractor.publish()
        except BaseException:
            extractor.abandon()
            raise
        return RestoreOutcome(
            manifest=manifest,
            compatibility=compatibility,
            replaced_existing_data=occupied,
        )


def _require_restorable(manifest: ArchiveManifest, compatibility: ArchiveCompatibility) -> None:
    """Refuse an archive this build cannot interpret, naming which half is wrong."""
    if compatibility is not ArchiveCompatibility.UNSUPPORTED:
        return
    if manifest.format_name != ARCHIVE_FORMAT_NAME:
        raise ArchiveError(ArchiveErrorCode.FORMAT_UNSUPPORTED, "not a GPX-View archive")
    if manifest.format_version > ARCHIVE_FORMAT_VERSION:
        raise ArchiveError(
            ArchiveErrorCode.FORMAT_UNSUPPORTED,
            f"archive format {manifest.format_version} is newer than this build understands",
        )
    raise ArchiveError(
        ArchiveErrorCode.SCHEMA_UNSUPPORTED,
        f"database schema {manifest.schema_version} is newer than this build understands",
    )


def describe_omissions(omissions: Sequence[ArchiveOmission]) -> str:
    """Render what an archive leaves out, for an operator to read.

    Rendered rather than assumed: an archive that omits nothing says so, because
    "no omissions listed" and "omissions not reported" look identical otherwise.
    """
    if not omissions:
        return "nothing"
    return ", ".join(f"{omission.kind} ({omission.reason})" for omission in omissions)
