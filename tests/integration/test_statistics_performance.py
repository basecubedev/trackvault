"""What a statistics query is allowed to cost.

Two properties, both of which a convenient implementation quietly loses.

**No aggregate reads a position.** Per-track metrics exist precisely so that a
yearly total is a sum over one row per track. The moment a total touches
``track_points``, the archive's cost stops depending on how many tracks it holds
and starts depending on how long they are -- and a single long recording holds
hundreds of thousands of positions.

**No listing costs a query per row.** An N+1 appears exactly where the obvious
code would loop, so it is measured rather than assumed.

This is a shape check, not a benchmark. It counts the SQL a query issues and
what that SQL touches, which stays meaningful on any machine.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trackvault.application.analysis import InstalledAnalysis
from trackvault.application.analyze import AnalyzeTrack
from trackvault.application.ports import AnalysisAvailability, TrackOrder, TrackQuery
from trackvault.application.statistics import AggregationScope, GetYearStatistics
from trackvault.domain import (
    NORMALIZATION_SCHEMA_VERSION,
    Activity,
    ClassificationResult,
    EvidenceCode,
    InputChannel,
    NormalizedTrack,
    ProcessingRun,
    ProcessingStatus,
    RawImport,
    SourceMetadata,
    TrackClassification,
    TrackKind,
    TrackPoint,
    TrackSegment,
)
from trackvault.domain.analysis import ANALYSIS_PROFILE
from trackvault.infrastructure.database import SqliteTrackStore

pytestmark = [pytest.mark.integration, pytest.mark.statistics]

NOW = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)
START = datetime(2026, 4, 1, 8, 0, tzinfo=UTC)

TRACKS = 40
POINTS_PER_TRACK = 200


class _RecordingStore(SqliteTrackStore):
    """A store that remembers every statement it issued."""

    def __init__(self, path: Path) -> None:
        """Point the store at a database and start with an empty log."""
        super().__init__(path)
        self.statements: list[str] = []
        self.recording = False

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection that reports what it runs, while recording."""
        with super().connection() as connection:
            if self.recording:
                connection.set_trace_callback(self.statements.append)
            yield connection

    @contextmanager
    def watching(self) -> Iterator[None]:
        """Record the statements issued inside this block."""
        self.statements.clear()
        self.recording = True
        try:
            yield
        finally:
            self.recording = False


class _FixedClock:
    """A clock that answers one instant."""

    def now(self) -> datetime:
        """Return the configured instant."""
        return NOW


def _fill(store: SqliteTrackStore, tracks: int) -> None:
    """Store and analyse a number of recordings of realistic length."""
    analyze = AnalyzeTrack(repository=store, clock=_FixedClock(), analysis=InstalledAnalysis())
    for index in range(tracks):
        sha = f"{index:064x}"
        started = START + timedelta(days=index)
        raw = RawImport(
            sha256=sha,
            size_bytes=4096,
            original_filename=None,
            received_at=NOW,
            input_channel=InputChannel.LOCAL_FILE,
        )
        run = ProcessingRun(
            raw_import_sha256=sha,
            importer="gpx",
            importer_version="1",
            normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
            processed_at=NOW,
            status=ProcessingStatus.SUCCEEDED,
            classifier="evidence-weights",
            classifier_version="2",
        )
        track = NormalizedTrack(
            segments=(
                TrackSegment(
                    points=tuple(
                        TrackPoint(
                            latitude=point * 1.4 / 111_195.0,
                            longitude=8.0,
                            elevation=100.0 + point,
                            time=started + timedelta(seconds=point),
                        )
                        for point in range(POINTS_PER_TRACK)
                    )
                ),
            ),
            source=SourceMetadata(exchange_format="gpx"),
            source_key="trk:0",
            classification=TrackClassification(
                detected=ClassificationResult(
                    kind=TrackKind.RECORDED,
                    confidence=0.9,
                    method="evidence-weights",
                    method_version="2",
                    evidence=(
                        EvidenceCode.TIMESTAMPS_PRESENT.value,
                        EvidenceCode.GPS_ACCURACY_PRESENT.value,
                    ),
                )
            ),
            title=f"Walk {index}",
            activity=Activity.WALKING,
        )
        (track_id,) = store.record_import(raw, run, [track])
        analyze(track_id)


@pytest.fixture
def archive(tmp_path: Path) -> _RecordingStore:
    """Return a store holding enough analysed tracks to see a shape."""
    store = _RecordingStore(tmp_path / "trackvault.sqlite3")
    store.migrate()
    _fill(store, TRACKS)
    return store


def _reads(store: _RecordingStore) -> list[str]:
    """Return the recorded statements that actually query something."""
    return [
        statement
        for statement in store.statements
        if statement.lstrip().upper().startswith("SELECT")
    ]


def test_a_year_total_never_reads_a_position(archive: _RecordingStore) -> None:
    """The whole reason per-track metrics are persisted.

    Forty tracks of two hundred positions is eight thousand rows an aggregate
    must not look at, and the number that matters is that it looks at none.
    """
    statistics = GetYearStatistics(repository=archive, timezone="UTC", analysis=InstalledAnalysis())

    with archive.watching():
        totals = statistics(2026, scope=AggregationScope.RECORDED)

    assert totals.totals.track_count == TRACKS
    assert not [statement for statement in _reads(archive) if "track_points" in statement.lower()]
    assert not [statement for statement in _reads(archive) if "track_segments" in statement.lower()]


def test_a_year_total_costs_a_constant_number_of_queries(archive: _RecordingStore) -> None:
    """A total is bounded work, not work per track.

    The bound is generous on purpose -- it exists to catch a query issued inside
    a loop, not to pin an implementation to a particular number of statements.
    """
    statistics = GetYearStatistics(repository=archive, timezone="UTC", analysis=InstalledAnalysis())

    with archive.watching():
        statistics(2026, scope=AggregationScope.RECORDED)

    assert len(_reads(archive)) < TRACKS


def test_listing_tracks_does_not_cost_a_query_per_track(archive: _RecordingStore) -> None:
    """The N+1 that a per-row analysis lookup would introduce."""
    with archive.watching():
        summaries = archive.list_tracks(TrackQuery(installed_analysis=ANALYSIS_PROFILE)).tracks

    assert len(summaries) == TRACKS
    assert all(summary.analysis is AnalysisAvailability.CURRENT for summary in summaries)
    assert len(_reads(archive)) < TRACKS


def test_listing_tracks_never_reads_a_position(archive: _RecordingStore) -> None:
    """A listing states a point count; it does not fetch the points."""
    with archive.watching():
        assert archive.list_tracks(TrackQuery()).tracks is not None

    assert not [statement for statement in _reads(archive) if "track_points" in statement.lower()]


def _plan(store: SqliteTrackStore, statement: str, parameters: tuple[object, ...] = ()) -> str:
    """Return SQLite's query plan for one statement, as one lowercase blob."""
    with store.connection() as connection:
        rows = connection.execute(f"EXPLAIN QUERY PLAN {statement}", parameters).fetchall()
    return " | ".join(str(row["detail"]) for row in rows).lower()


def test_a_year_window_is_served_by_an_index_rather_than_a_scan(
    archive: _RecordingStore,
) -> None:
    """The window over the activity date is the query a growing archive repeats.

    A scan is fine at forty tracks and is the whole cost at forty thousand, and
    nothing in the result would look different in between. The plan is checked
    instead, because it says which of the two is happening.
    """
    plan = _plan(
        archive,
        "SELECT t.id FROM tracks t WHERE t.started_at >= ? AND t.started_at < ?",
        ("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
    )

    assert "ix_tracks_started_at" in plan, plan
    assert "scan t" not in plan, plan


def test_no_aggregate_or_listing_plan_touches_the_positions(
    archive: _RecordingStore,
) -> None:
    """Stated as a plan, not only as a statement count.

    A join added later could pull `track_points` in without issuing a statement
    that mentions it separately.
    """
    for statement in (
        "SELECT count(*) FROM tracks t "
        "JOIN raw_imports i ON i.sha256 = t.raw_import_sha256 "
        "AND i.active_processing_run_id = t.processing_run_id",
        "SELECT t.id FROM tracks t WHERE t.started_at >= ?",
    ):
        plan = _plan(archive, statement, ("2026-01-01T00:00:00+00:00",)[: statement.count("?")])
        assert "track_points" not in plan, plan
        assert "track_segments" not in plan, plan


def test_a_page_costs_the_page_rather_than_the_archive(archive: _RecordingStore) -> None:
    """Paging must not load everything and slice it afterwards.

    The metric lookup is batched over the page, so a page of five reads five
    tracks' metrics however large the archive behind it is.
    """
    with archive.watching():
        page = archive.list_tracks(TrackQuery(limit=5))

    assert len(page.tracks) == 5
    assert page.total == TRACKS
    assert len(_reads(archive)) < TRACKS
    assert not [statement for statement in _reads(archive) if "track_points" in statement.lower()]


def test_every_listing_order_is_bounded_in_sql(archive: _RecordingStore) -> None:
    """`LIMIT` reaches the database, whichever ordering was asked for."""
    for order in TrackOrder:
        with archive.watching():
            page = archive.list_tracks(TrackQuery(limit=3, order=order))

        assert len(page.tracks) == 3, order
        assert [statement for statement in _reads(archive) if "limit" in statement.lower()], order
