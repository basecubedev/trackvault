"""The ports the application reaches the outside world through.

Each port is a small ``Protocol``. Infrastructure implements them; the use cases
never know which implementation they got, and never import one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from gpx_view.domain import (
    Activity,
    NormalizedTrack,
    ProcessingRun,
    RawImport,
    SourceMetadata,
    TrackClassification,
    TrackKind,
    TrackSegment,
)


@dataclass(frozen=True, slots=True)
class TrackSummary:
    """A stored track without its geometry.

    Everything a caller needs to list or inspect a track, cheaply enough that a
    listing does not load hundreds of thousands of positions. The geometry is
    fetched separately.

    Attributes:
        track_id: Stable identity of the track. Survives reprocessing.
        raw_import_sha256: The raw import this track was normalized from.
        source_index: Where the track sits in its source document. Presentation
            order only -- it is **not** an identity, because a reprocess that
            reports one more candidate in front moves every later position.
        source_key: Which candidate of its source this track is. The identity a
            user correction is attached to, opaque outside the importer that
            produced it.
        title: The name the source gave the track.
        activity: The activity, from explicit source metadata or the user.
        classification: Detected result and any user override. The single
            authority for ``effective_kind`` -- no kind is stored separately.
        point_count: How many positions the geometry holds.
        segment_count: How many segments the geometry holds.
        started_at: Earliest instant the track recorded, if any.
        ended_at: Latest instant the track recorded, if any.
        source: Provenance of the document the track came from.
    """

    track_id: int
    raw_import_sha256: str
    source_index: int
    source_key: str
    title: str | None
    activity: Activity
    classification: TrackClassification
    point_count: int
    segment_count: int
    started_at: datetime | None
    ended_at: datetime | None
    source: SourceMetadata

    @property
    def effective_kind(self) -> TrackKind:
        """Return the authoritative kind, honouring an explicit user override."""
        return self.classification.effective_kind

    @property
    def detected_kind(self) -> TrackKind:
        """Return what the classifier found, ignoring any user override."""
        return self.classification.detected.kind


@dataclass(frozen=True, slots=True)
class ProcessingSnapshot:
    """What one raw import's processing history currently amounts to.

    Two facts that can disagree and both have to be readable at once: the newest
    attempt, and the run whose candidates readers actually see. A failed attempt
    leaves the last good generation standing, so collapsing them into one status
    makes a healthy archive look broken.

    Attributes:
        raw_import_sha256: Identity of the raw import.
        current_run_id: The run that owns the current generation, or ``None``
            when the source has never been processed successfully.
        current_run: That run, or ``None``.
        latest_run_id: The newest attempt, whatever it did.
        latest_run: That attempt, or ``None`` when there is none at all.
        track_count: How many tracks the current generation holds.
    """

    raw_import_sha256: str
    current_run_id: int | None
    current_run: ProcessingRun | None
    latest_run_id: int | None
    latest_run: ProcessingRun | None
    track_count: int


class Clock(Protocol):
    """The source of "now", injected so that tests stay deterministic."""

    def now(self) -> datetime:
        """Return the current instant, timezone-aware."""
        ...


class RawArtifactState(StrEnum):
    """What the managed copy of one raw import currently is.

    A database row saying the archive holds some bytes and the bytes themselves
    are two facts that can disagree, and every caller that cares -- the duplicate
    check, reprocessing, diagnostics -- has to read the same answer. One
    question, asked in one place.

    Attributes:
        HEALTHY: The artifact is there and hashes to the digest it is filed
            under.
        MISSING: There is no artifact. Recoverable if the same bytes are offered
            again.
        CORRUPT: An artifact exists and its bytes are not the ones the digest
            names. Fail closed: it is evidence of a problem, not a stale cache.
        UNREADABLE: Something else stopped the archive from reading it -- a
            permission, a device error, a symbolic link where its own file
            should be.
    """

    HEALTHY = "healthy"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNREADABLE = "unreadable"


class RawImportStore(Protocol):
    """Byte-identical storage for original import files.

    The store is content-addressed: the same bytes occupy one artifact, and a
    filename never decides where anything is written. Content-addressed means
    content-verified -- a path named after a digest is valid only while its bytes
    have that digest.
    """

    def integrity(self, sha256: str) -> RawArtifactState:
        """Report what the managed copy of one raw import currently is.

        The bytes are read and hashed, because nothing cheaper can answer the
        question: a file of the right name proves only that a file of that name
        exists.
        """
        ...

    def store(self, content: bytes, sha256: str) -> None:
        """Store the bytes under their content hash, atomically and idempotently.

        Storing content already present is a no-op, and "already present" means
        the stored bytes hash to the same digest rather than that a file of that
        name exists. An artifact whose bytes do not match is never overwritten.

        Raises:
            TrackImportError: ``raw_storage_corrupt`` if an artifact of that name
                holds different bytes; ``raw_storage_failed`` if the hash is
                unusable, the content does not match it, or the write could not
                be completed. A failed attempt leaves no partial artifact behind.
        """
        ...

    def read(self, sha256: str) -> bytes:
        """Return the stored bytes, so a raw import can be reprocessed.

        Raises:
            TrackImportError: ``raw_storage_missing`` if there is no artifact,
                ``raw_storage_corrupt`` if its bytes do not hash to the digest it
                is filed under, ``raw_storage_failed`` if it could not be read.
        """
        ...


class TrackRepository(Protocol):
    """Durable storage for raw imports, processing runs and normalized tracks."""

    def find_raw_import(self, sha256: str) -> RawImport | None:
        """Return the raw import with that content hash, if it is already known."""
        ...

    def latest_run(self, sha256: str) -> ProcessingRun | None:
        """Return the most recent processing *attempt* for a raw import, if any.

        The latest attempt and the current generation are different questions. An
        attempt may have failed while the tracks a reader sees are still the ones
        the last successful run produced, and both facts have to be expressible
        at the same time.
        """
        ...

    def processing_snapshot(self, sha256: str) -> ProcessingSnapshot | None:
        """Return what one raw import's processing amounts to, or ``None``.

        ``None`` means the archive does not hold that source at all.
        """
        ...

    def processing_snapshots(self) -> tuple[ProcessingSnapshot, ...]:
        """Return the same for every raw import, oldest received first.

        One question, one answer: selecting what to reprocess and reporting what
        an operator asked about read the same facts, so a batch run and a status
        page cannot disagree about a source. The order makes a batch run
        reproducible.
        """
        ...

    def track_ids_for(self, sha256: str) -> tuple[int, ...]:
        """Return the tracks a raw import produced, in source order."""
        ...

    def record_import(
        self,
        raw_import: RawImport,
        run: ProcessingRun,
        tracks: Sequence[NormalizedTrack],
    ) -> tuple[int, ...]:
        """Store one import attempt in a single transaction.

        The raw import is written once and never modified afterwards. The run is
        appended: processing history is never rewritten.

        Tracks are identified by their raw import and their ``source_key``, so
        reprocessing replaces the normalized data and the detected classification
        of the same logical candidate while an existing user override survives --
        whatever position that candidate now holds in the document.

        A **successful** run becomes the current normalized generation, atomically
        and in full: exactly the candidates it produced are current afterwards,
        and candidates it did not produce stop being current. Their rows and their
        user corrections are kept, so a candidate that reappears in a later run
        gets its correction back. A **failed** run is recorded as history and
        changes nothing about the current generation -- a broken importer version
        must not be able to destroy data that was already good.

        Returns:
            The identities of the stored tracks, in source order.

        Raises:
            TrackImportError: ``persistence_failed`` if the transaction could not
                be completed. Nothing partial is left behind.
        """
        ...

    def list_tracks(self) -> tuple[TrackSummary, ...]:
        """Return the current tracks without geometry, newest imported source first.

        Ordering is by the raw import's received instant, descending, then by the
        candidates' order within their source document, then by track identity so
        that two sources received in the same instant still order deterministically.

        Only the current generation appears. A candidate a later successful run
        stopped producing is history, and history is a separate question.
        """
        ...

    def get_track(self, track_id: int) -> TrackSummary | None:
        """Return one current track without its geometry, or ``None``.

        A track that is no longer part of its raw import's current generation
        reports as absent. Mixing history into the ordinary track lookup would
        make "this track exists" mean two different things.
        """
        ...

    def get_geometry(self, track_id: int) -> tuple[TrackSegment, ...] | None:
        """Return the segments of one current track, or ``None`` if it is unknown."""
        ...

    def set_override(self, track_id: int, kind: TrackKind, at: datetime) -> bool:
        """Record an explicit user correction. Returns whether the track exists."""
        ...

    def clear_override(self, track_id: int) -> bool:
        """Withdraw a user correction. Returns whether the track exists."""
        ...
