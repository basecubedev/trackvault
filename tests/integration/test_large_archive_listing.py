"""A page must cost the page, whatever is behind it.

`test_statistics_performance.py` establishes the *shape*: no query per track,
no position ever read, `LIMIT` reaching the database. This asks the question
that shape exists to answer, and asks it the way this project prefers -- as a
relation that has to hold when the input is transformed, rather than as a
duration somebody pinned on one machine:

```
an archive ten times larger costs the same number of statements per page
```

A wall-clock threshold would be a red cross on whichever runner was busy. A
statement count is the property that actually decides whether an archive stays
usable as it grows, and it is the same on every machine.

The tracks here carry two positions each. Geometry is not what a listing costs
-- proving that it is not is the other module's job -- and building a thousand
long recordings to demonstrate paging would be paying for the wrong thing.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gpx_view.application.ports import TrackOrder, TrackQuery
from gpx_view.domain import (
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
from gpx_view.infrastructure.database import SqliteTrackStore

pytestmark = [pytest.mark.integration, pytest.mark.persistence]

NOW = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)
START = datetime(2020, 1, 1, 8, 0, tzinfo=UTC)

SMALL = 100
LARGE = 1_000
"""Two archive sizes an order of magnitude apart.

Large enough that a per-track query would show up as a factor of ten, small
enough that the fixture builds in seconds. The property being checked does not
get truer at five thousand; it gets slower to check.
"""

PAGE = 25


class _CountingStore(SqliteTrackStore):
    """A store that counts the statements it issues."""

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

    def reads(self) -> list[str]:
        """Return the recorded statements that actually query something."""
        return [
            statement
            for statement in self.statements
            if statement.lstrip().upper().startswith("SELECT")
        ]


def _archive(path: Path, tracks: int) -> _CountingStore:
    """Return a store holding ``tracks`` short recordings, unanalysed.

    Unanalysed on purpose: what a listing costs must not depend on how much of
    the archive has metrics, and a track with none is the cheaper half of the
    question rather than the whole of it.
    """
    store = _CountingStore(path)
    store.migrate()
    for index in range(tracks):
        sha = f"{index:064x}"
        started = START + timedelta(hours=index)
        store.record_import(
            RawImport(
                sha256=sha,
                size_bytes=1024,
                original_filename=None,
                received_at=NOW,
                input_channel=InputChannel.LOCAL_FILE,
            ),
            ProcessingRun(
                raw_import_sha256=sha,
                importer="gpx",
                importer_version="1",
                normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
                processed_at=NOW,
                status=ProcessingStatus.SUCCEEDED,
                classifier="evidence-weights",
                classifier_version="2",
            ),
            [
                NormalizedTrack(
                    segments=(
                        TrackSegment(
                            points=(
                                TrackPoint(
                                    latitude=48.0, longitude=11.0, elevation=500.0, time=started
                                ),
                                TrackPoint(
                                    latitude=48.001,
                                    longitude=11.001,
                                    elevation=510.0,
                                    time=started + timedelta(minutes=20),
                                ),
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
            ],
        )
    return store


@pytest.fixture(scope="module")
def small(tmp_path_factory: pytest.TempPathFactory) -> _CountingStore:
    """A hundred tracks."""
    return _archive(tmp_path_factory.mktemp("small") / "gpx-view.sqlite3", SMALL)


@pytest.fixture(scope="module")
def large(tmp_path_factory: pytest.TempPathFactory) -> _CountingStore:
    """A thousand tracks."""
    return _archive(tmp_path_factory.mktemp("large") / "gpx-view.sqlite3", LARGE)


def _page_cost(store: _CountingStore, query: TrackQuery) -> int:
    """Return how many read statements one page costs."""
    with store.watching():
        store.list_tracks(query)
    return len(store.reads())


def test_a_page_costs_the_same_in_an_archive_ten_times_larger(
    small: _CountingStore, large: _CountingStore
) -> None:
    """The relation the whole paging design exists for."""
    query = TrackQuery(limit=PAGE)

    assert _page_cost(small, query) == _page_cost(large, query)


def test_the_last_page_costs_what_the_first_one_did(large: _CountingStore) -> None:
    """Paging deep into an archive is not more expensive than starting it.

    An implementation that read everything and sliced afterwards would be
    indistinguishable from this one on page one.
    """
    first = _page_cost(large, TrackQuery(limit=PAGE))
    last = _page_cost(large, TrackQuery(limit=PAGE, offset=LARGE - PAGE))

    assert first == last


def test_a_filtered_page_counts_the_filtered_selection(large: _CountingStore) -> None:
    """`total` describes the filter, not the archive, and still costs a page."""
    with large.watching():
        page = large.list_tracks(TrackQuery(limit=PAGE, effective_kind=TrackKind.RECORDED))
    recorded = page.total

    with large.watching():
        empty = large.list_tracks(TrackQuery(limit=PAGE, effective_kind=TrackKind.PLANNED))

    assert recorded == LARGE
    assert empty.total == 0
    assert empty.tracks == ()


@pytest.mark.parametrize("order", list(TrackOrder))
def test_every_ordering_returns_a_bounded_page_from_a_large_archive(
    large: _CountingStore, order: TrackOrder
) -> None:
    """Including the ones that sort on a metric no track here has.

    A thousand tracks with no analysis is exactly the archive in which
    ``longest_first`` has nothing to sort by, and it still has to answer a page
    rather than everything.
    """
    page = large.list_tracks(TrackQuery(limit=PAGE, order=order))

    assert len(page.tracks) == PAGE
    assert page.total == LARGE


def test_a_page_never_reads_a_position_however_large_the_archive(
    large: _CountingStore,
) -> None:
    """A listing row shows no position, so a listing must not load one."""
    with large.watching():
        large.list_tracks(TrackQuery(limit=PAGE))

    assert not [statement for statement in large.reads() if "track_points" in statement.lower()]


def test_the_calendar_the_interface_offers_costs_one_pass(large: _CountingStore) -> None:
    """Which years exist is a dropdown, and must not cost what a total costs.

    One statement over one short row per dated track, and never a metric or a
    position -- the reason `dated_track_rows` is its own narrow port row rather
    than a reuse of the aggregation query.
    """
    with large.watching():
        rows = large.dated_track_rows()

    assert len(rows) == LARGE
    assert len(large.reads()) == 1
    assert not [statement for statement in large.reads() if "track_metrics" in statement.lower()]
