"""The ports the application reaches the outside world through.

Each port is a small ``Protocol``. Infrastructure implements them; the use cases
never know which implementation they got, and never import one.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from gpx_view.domain import (
    Activity,
    NormalizedTrack,
    ProcessingRun,
    RawImport,
    SourceMetadata,
    TemporalEvidence,
    TrackClassification,
    TrackKind,
    TrackSegment,
    temporal_evidence_of,
)
from gpx_view.domain.analysis import (
    AnalysisProfile,
    AnalysisQuality,
    MetricName,
    MetricValue,
    TrackAnalysis,
)


class AnalysisAvailability(StrEnum):
    """What a track's stored metrics currently amount to.

    Four states rather than a boolean, because "analysed" answers a question
    nobody is really asking. Two of the four are not failures at all: a track
    that has never been analysed and a track whose algorithms moved on both have
    a perfectly good normalized generation behind them.

    Every read surface -- a listing row, the track's own analysis resource, a
    yearly total -- projects this same value, decided by
    :meth:`~gpx_view.application.analysis.InstalledAnalysis.availability`.

    Attributes:
        CURRENT: The metrics were produced by the installed algorithms from the
            geometry a reader sees. The only state whose numbers may be
            presented as what the track *is*.
        OUTDATED: Metrics exist, but the algorithms or the geometry have moved
            on since. They are the last thing that was actually derived, which
            is not the same as being right, and ``analyze --outdated`` replaces
            them.
        MISSING: There are no metrics. A normal state for a track that has not
            been analysed yet, and for one whose analysis failed.
        INVALID: The archive holds a stored analysis for this track and cannot
            interpret it -- a profile version that makes no sense, an impossible
            metric, a flag this build cannot name. Unlike the other three this
            is not a normal state; it is data damage, reported rather than
            hidden behind ``MISSING`` because "nothing has been derived yet" and
            "something is wrong with what was" call for different reactions from
            whoever reads it. Deriving the metrics again repairs it: the
            geometry they come from was never touched.
    """

    CURRENT = "current"
    OUTDATED = "outdated"
    MISSING = "missing"
    INVALID = "invalid"


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
        analysis: What the track's stored metrics currently amount to. The same
            four states the track's own analysis resource reports, decided by
            the same authority, so a row and a detail view can never disagree
            about whether a number is still the track's length.
        metrics: The metrics of the track's **current** analysis, empty unless
            ``analysis`` is ``CURRENT``. Carried on the summary so that listing a
            hundred tracks with their distances is one query rather than a
            hundred: a list view that costs a request per row is a list view
            nobody uses. Stale and damaged numbers are deliberately absent --
            they are the last thing that was derived, not what the track is, and
            a headline metric is a claim about what the track is.
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
    analysis: AnalysisAvailability = AnalysisAvailability.MISSING
    metrics: Mapping[MetricName, MetricValue] = field(default_factory=dict)

    @property
    def effective_kind(self) -> TrackKind:
        """Return the authoritative kind, honouring an explicit user override."""
        return self.classification.effective_kind

    @property
    def detected_kind(self) -> TrackKind:
        """Return what the classifier found, ignoring any user override."""
        return self.classification.detected.kind

    @property
    def temporal_evidence(self) -> TemporalEvidence:
        """Return what this track's instants have been shown to be.

        Derived from the detected evidence rather than stored beside it, so a
        classifier that learns to recognise a planner updates the answer without
        a single metric being derived again. It is deliberately read from the
        *detected* result: a user correcting the track kind says nothing about
        where its timestamps came from.
        """
        return temporal_evidence_of(self.classification.detected.evidence)


MAX_PAGE_SIZE = 200
"""The largest page a listing returns, whatever it is asked for.

An archive grows without anybody deciding to grow it, and a limit a caller can
raise without bound is a limit that exists until somebody types a large number.
Two hundred rows is more than a screen and far less than a problem.
"""


class TrackOrder(StrEnum):
    """How a listing is sorted.

    A closed set rather than a column name from the query string: an ordering is
    part of the contract, and every one of these ends in a unique tie-breaker.
    Two rows that compare equal on the sort key would otherwise come back in
    whatever order the database felt like, and a row that moves between page one
    and page two is a row the reader sees twice or not at all.

    Attributes:
        IMPORTED_NEWEST_FIRST: Newest source first. The default, because it
            answers "what did I just add".
        ACTIVITY_NEWEST_FIRST: Most recent activity first -- what a dashboard
            wants.
        ACTIVITY_OLDEST_FIRST: The same axis, read the other way.
        LONGEST_FIRST: Greatest distance first, where "distance" means the one a
            *current* analysis derived. A stale number is the length the track
            had under algorithms this build no longer runs, and ordering by it
            would answer a question nobody asked.
    """

    IMPORTED_NEWEST_FIRST = "imported_newest_first"
    ACTIVITY_NEWEST_FIRST = "activity_newest_first"
    ACTIVITY_OLDEST_FIRST = "activity_oldest_first"
    LONGEST_FIRST = "longest_first"


@dataclass(frozen=True, slots=True)
class TrackQuery:
    """Which tracks to list, in what order, and how many of them.

    The period is expressed as a half-open instant window rather than as a year
    and a month. Which local month an instant falls in depends on a configured
    zone, and that conversion has exactly one owner; handing SQL a window it can
    compare keeps it that way.

    Attributes:
        limit: How many rows to return. Bounded by the repository.
        offset: How many rows to skip.
        effective_kind: Narrow to recorded, planned or unknown, honouring an
            explicit user correction.
        activity: Narrow to one activity.
        started_at_or_after: Include tracks whose activity started at or after
            this instant.
        started_before: Include tracks whose activity started before it, or
            ``None`` for an open upper bound. The last supported year has no
            following one, and an open bound says so without inventing a date.
        installed_analysis: The algorithms this build applies, supplied by the
            application layer so that the repository can decide currency in SQL
            without *defining* it. The storage layer is allowed to compare; it
            is not allowed to hold a second table of what is installed. ``None``
            means nothing counts as current, which is the fail-closed direction.
        analysis_status: Narrow to tracks whose stored metrics are in one
            availability state. Applied in SQL beside the other restrictions, so
            ``total`` counts the filtered selection and a pager built on it is
            right.
        calendar_anchored: Narrow to tracks whose instants may say *when* the
            activity happened. Set whenever a period is asked for, because a
            period selection is a claim about when things happened, and a track
            whose clock nothing vouches for cannot answer it.
        order: How to sort what is left.
    """

    limit: int = MAX_PAGE_SIZE
    offset: int = 0
    effective_kind: TrackKind | None = None
    activity: Activity | None = None
    started_at_or_after: datetime | None = None
    started_before: datetime | None = None
    installed_analysis: AnalysisProfile | None = None
    analysis_status: AnalysisAvailability | None = None
    calendar_anchored: bool = False
    order: TrackOrder = TrackOrder.IMPORTED_NEWEST_FIRST


@dataclass(frozen=True, slots=True)
class TrackPage:
    """One page of a listing, and how much there was to page through.

    Attributes:
        tracks: The rows of this page, in the requested order.
        total: How many tracks the *filter* selected, whatever the page holds.
            Without it a client cannot tell a last page from a full one.
        limit: The page size that was applied, which may be smaller than the one
            that was asked for.
        offset: Where this page started.
    """

    tracks: tuple[TrackSummary, ...]
    total: int
    limit: int
    offset: int


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


class AnalysisStatus(StrEnum):
    """Outcome of an analysis run.

    Deliberately its own vocabulary rather than the processing one. A track can
    be perfectly normalized and still have no usable metrics, and the two
    lifecycles have to be able to say so independently.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    """One attempt to derive metrics for one track.

    The run binds two things that both decide whether its output still counts:
    the algorithms that produced it, and the normalized generation it read. Lose
    either and "are these numbers still right?" becomes unanswerable.

    Attributes:
        track_id: The track that was analysed.
        processing_run_id: The processing run whose geometry was read. A later
            successful reprocess produces different geometry, and metrics
            derived from the old one stop describing what a reader sees.
        profile: The algorithms and versions that were applied.
        analyzed_at: The timezone-aware instant the run finished.
        status: Whether the run produced metrics or failed.
        error_code: Stable error code of a failed run. Required for a failure
            and forbidden for a success.

    Raises:
        ValueError: If the timestamp is naive, or the status and the error code
            contradict each other.
    """

    track_id: int
    processing_run_id: int
    profile: AnalysisProfile
    analyzed_at: datetime
    status: AnalysisStatus
    error_code: str | None = None

    def __post_init__(self) -> None:
        """Reject runs that could not be attributed or recovered from."""
        if self.analyzed_at.tzinfo is None or self.analyzed_at.utcoffset() is None:
            raise ValueError("analyzed_at must be timezone-aware")
        if self.status is AnalysisStatus.FAILED and not self.error_code:
            raise ValueError("a failed analysis run must state a stable error_code")
        if self.status is AnalysisStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("a successful analysis run must not state an error_code")

    @property
    def succeeded(self) -> bool:
        """Report whether this run produced metrics."""
        return self.status is AnalysisStatus.SUCCEEDED


@dataclass(frozen=True, slots=True)
class StoredAnalysis:
    """The metrics a track currently has, and what produced them.

    Attributes:
        run_id: Identity of the run that produced these metrics.
        run: That run, including its profile and the generation it read.
        metrics: The derived values. A metric that could not be derived is
            absent -- readers must not fill the hole with a zero.
        quality: What was wrong with the data the metrics came from.
    """

    run_id: int
    run: AnalysisRun
    metrics: Mapping[MetricName, MetricValue]
    quality: tuple[AnalysisQuality, ...]


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """What one track's analysis currently amounts to.

    The same two-facts-at-once shape as
    :class:`ProcessingSnapshot`, for the same reason: a failed attempt must not
    make a healthy set of current metrics look absent.

    Attributes:
        track_id: The track this describes.
        processing_run_id: The generation a reader currently sees. An analysis
            bound to a different one is stale however good it was.
        current_run_id: The run whose metrics are current, or ``None``.
        current_run: That run, or ``None``.
        latest_run_id: The newest attempt, whatever it did.
        latest_run: That attempt, or ``None`` when there is none at all.
    """

    track_id: int
    processing_run_id: int
    current_run_id: int | None
    current_run: AnalysisRun | None
    latest_run_id: int | None
    latest_run: AnalysisRun | None


@dataclass(frozen=True, slots=True)
class TrackAggregationRow:
    """One current track, reduced to what a statistic needs from it.

    Everything here is per track and already derived, which is the point: a
    yearly total must never load a position. A long recording holds hundreds of
    thousands of them, and there is nothing in one that an aggregate wants.

    Attributes:
        track_id: The track this describes.
        effective_kind: What the track counts as *now*, honouring a user
            correction. Read at query time rather than stored, so a correction
            moves a track between the actual and planned sets without anything
            being derived again.
        activity: What the track was, for narrowing a total to one of them.
        started_at: The first temporal observation the track's own positions
            carry, in UTC. ``None`` for a track that carries no time at all --
            which has a length and belongs to no month, both at once.
        temporal_evidence: What the instants behind the clock-dependent metrics
            were shown to be. A total of moving time may only add the ones
            somebody measured.
        analysis: What the track's analysis amounts to. Carried whole rather
            than reduced to a flag here, because deciding whether it is current
            belongs to the application's one currency authority -- the same one
            ``analyze --outdated`` asks -- and not to the storage that fetched
            the row.
        metrics: The metrics of the track's current analysis. Empty when it has
            none, which is a track counted as unanalysed rather than as zero.
    """

    track_id: int
    effective_kind: TrackKind
    activity: Activity
    started_at: datetime | None
    temporal_evidence: TemporalEvidence
    analysis: AnalysisSnapshot
    metrics: Mapping[MetricName, MetricValue]


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

    @property
    def max_page_size(self) -> int:
        """Return the largest page this repository will return.

        The server keeps the last word on it. A limit a caller can raise without
        bound is a limit that exists only until somebody types a large number.
        """
        ...

    def list_tracks(self, query: TrackQuery) -> TrackPage:
        """Return one page of the current tracks, without geometry.

        Filtering, ordering and paging all happen in the query. Loading an
        archive into memory to sort and slice it there costs what the archive
        costs rather than what the page does, and it is the version that works
        fine until the day it does not.

        Only the current generation appears. A candidate a later successful run
        stopped producing is history, and history is a separate question.
        """
        ...

    def get_track(
        self, track_id: int, installed_analysis: AnalysisProfile | None = None
    ) -> TrackSummary | None:
        """Return one current track without its geometry, or ``None``.

        A track that is no longer part of its raw import's current generation
        reports as absent. Mixing history into the ordinary track lookup would
        make "this track exists" mean two different things.

        ``installed_analysis`` is what this build derives with, so the summary
        can state what the track's stored metrics amount to. Omitting it means
        nothing counts as current -- the fail-closed answer, and the same one
        the domain's own currency check gives for an unknown profile.
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

    def record_analysis(self, run: AnalysisRun, analysis: TrackAnalysis | None) -> int:
        """Store one analysis attempt in a single transaction.

        A **successful** run becomes the track's current analysis atomically and
        in full: its metrics and quality flags are written and published as one
        act, so no reader ever sees a new distance beside an old duration.

        Publication is conditional on the run still describing what a reader
        sees. If the track was reprocessed while this analysis was running, its
        generation moved on, and metrics derived from the old geometry must not
        become current for the new one. The run is still recorded -- history is
        never discarded -- it simply does not win.

        A **failed** run is recorded and publishes nothing, so a broken
        algorithm cannot cost a track the metrics it already had.

        Returns:
            The identity of the stored run.

        Raises:
            TrackImportError: ``persistence_failed`` if the transaction could
                not be completed. Nothing partial is left behind.
        """
        ...

    def current_analysis(self, track_id: int) -> StoredAnalysis | None:
        """Return the metrics a track currently has, or ``None`` if it has none.

        ``None`` is a normal answer: a track that has never been analysed, or
        whose only attempts failed, has no metrics. It is not an empty set of
        metrics, because an empty set would read as "everything is zero".
        """
        ...

    def analysis_snapshot(self, track_id: int) -> AnalysisSnapshot | None:
        """Return what one track's analysis amounts to, or ``None``.

        ``None`` means the archive holds no such current track.
        """
        ...

    def analysis_snapshots(self) -> tuple[AnalysisSnapshot, ...]:
        """Return the same for every current track, oldest imported source first.

        One question, one answer: selecting what to analyse and reporting what an
        operator asked about read the same facts. The order makes a batch run
        reproducible.
        """
        ...

    def analysis_run_count(self, track_id: int) -> int:
        """Return how many analysis runs a track has accumulated."""
        ...

    def placed_aggregation_rows(
        self, since: datetime, until: datetime | None
    ) -> tuple[TrackAggregationRow, ...]:
        """Return the current tracks a window can vouch for as having happened in it.

        Two conditions, not one. The activity date falls in the window, *and*
        the instants it was read from were shown to have been measured. A
        plausible clock written onto geometry nobody travelled is not an
        activity date, and a total that adds its distance is about a month it
        never touched.

        Half open: ``since`` is included and ``until`` is not, so consecutive
        windows tile without a track landing in two of them. ``until`` is
        ``None`` for a window that runs to the end of the supported calendar --
        there is no instant after the last one, and inventing a date for it is
        how ``year + 1`` became a crash.

        The window is computed from the aggregation timezone by the caller and
        handed over in UTC, because that is what the archive stores. Bucketing
        the results into local months is the caller's job too: an offset is not
        a constant, and doing that arithmetic in SQL is where a daylight-saving
        transition quietly moves a track into the wrong month.
        """
        ...

    def unplaced_aggregation_rows(self) -> tuple[TrackAggregationRow, ...]:
        """Return the current tracks that belong to no calendar period at all.

        They are a separate question because they have no answer to the first
        one. A track with a length and no trustworthy date belongs to no month
        and no year, and putting it in one -- the import date, 1970, or whatever
        its own file happens to claim -- would put a real distance into a period
        it has nothing to do with.

        Two populations under one heading: no instants at all, and instants
        nothing showed to be measured. A caller tells them apart by whether the
        row carries a ``started_at``, and reports them separately, because
        "undated" and "dated by something unverifiable" call for different
        reactions.
        """
        ...
