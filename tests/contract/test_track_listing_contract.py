"""The HTTP contract for listing tracks at archive scale.

An archive is a few files on the day it is built and a few thousand a few years
later, and the listing endpoint is the one that notices. Returning everything is
comfortable at ten tracks, is a slow page at one thousand, and is a browser tab
running out of memory at ten thousand -- with no warning in between, because
nothing about the response shape changes.

So the listing is bounded, filtered and ordered by the archive rather than by
whoever is reading it:

```
bounded   a server maximum no query string can raise
ordered   deterministic, so page 2 is the page after page 1
filtered  in SQL, so a filtered page costs what it returns
```

The ordering matters more than it looks. Two rows that compare equal on the
sort key can come back in either order between two queries, and a row that
swaps position between page 1 and page 2 is a row the reader sees twice or not
at all. Every ordering here therefore ends in a unique tie-breaker.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.application.import_tracks import ImportRequest
from gpx_view.config import Settings
from gpx_view.main import create_app

pytestmark = [pytest.mark.contract]

_DEGREE = 111_195.0

RECORDING = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>{name}</name><type>{activity}</type><trkseg>
{points}
  </trkseg></trk>
</gpx>
"""

ROUTE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <rte><name>{name}</name>
    <rtept lat="0.0" lon="9.0"/><rtept lat="{end:.6f}" lon="9.0"/>
  </rte>
</gpx>
"""


def _recording(name: str, *, year: int, month: int, count: int, activity: str = "walking") -> bytes:
    """Build a synthetic recording of a stated month and length."""
    points = "\n".join(
        f'    <trkpt lat="{index * 1.4 / _DEGREE:.8f}" lon="8.0">'
        f"<ele>{100 + index * 0.5:.1f}</ele>"
        f"<time>{year:04d}-{month:02d}-05T09:{index // 60:02d}:{index % 60:02d}Z</time>"
        f"<hdop>1.1</hdop></trkpt>"
        for index in range(count)
    )
    return RECORDING.format(name=name, activity=activity, points=points).encode("utf-8")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client over a throwaway archive."""
    settings = Settings(data_dir=tmp_path / "data", timezone="Europe/Berlin")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _import(client: TestClient, content: bytes, name: str) -> int:
    """Import one document through the canonical use case."""
    services = client.app.state.services  # type: ignore[attr-defined]
    outcome = services.import_tracks(ImportRequest(content=content, original_filename=name))
    return outcome.track_ids[0]


@pytest.fixture
def archive(client: TestClient) -> TestClient:
    """Fill the archive with a small, deliberately varied set of tracks."""
    _import(client, _recording("jan walk", year=2025, month=1, count=60), "a.gpx")
    _import(client, _recording("feb walk", year=2025, month=2, count=120), "b.gpx")
    _import(
        client,
        _recording("mar cycle", year=2026, month=3, count=200, activity="cycling"),
        "c.gpx",
    )
    _import(client, ROUTE.format(name="planned", end=0.5).encode("utf-8"), "d.gpx")
    return client


def _list(client: TestClient, **params: object) -> dict[str, object]:
    """Ask for a page of tracks."""
    response = client.get("/api/v1/tracks", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


# --- Bounded -----------------------------------------------------------------


def test_a_listing_is_bounded_even_when_nothing_asks_it_to_be(archive: TestClient) -> None:
    """The default must be a page, not the archive."""
    body = _list(archive)

    assert body["limit"] <= 200
    assert body["offset"] == 0
    assert len(body["tracks"]) <= body["limit"]  # type: ignore[arg-type,operator]


def test_the_server_keeps_the_last_word_on_the_page_size(archive: TestClient) -> None:
    """`limit=10000000` is not a request the archive has to honour."""
    response = archive.get("/api/v1/tracks", params={"limit": 10_000_000})

    assert response.status_code == 422, "an unbounded page was accepted"


@pytest.mark.parametrize("limit", [0, -1])
def test_a_nonsensical_page_size_is_refused(archive: TestClient, limit: int) -> None:
    """A page of nothing is a mistake, not a query."""
    assert archive.get("/api/v1/tracks", params={"limit": limit}).status_code == 422


def test_a_negative_offset_is_refused(archive: TestClient) -> None:
    """There is nothing before the first row."""
    assert archive.get("/api/v1/tracks", params={"offset": -1}).status_code == 422


# --- Paged and total ---------------------------------------------------------


def test_the_total_counts_the_whole_selection_not_the_page(archive: TestClient) -> None:
    """A reader needs to know how many pages there are before asking for one."""
    body = _list(archive, limit=2)

    assert body["total"] == 4
    assert len(body["tracks"]) == 2  # type: ignore[arg-type]


def test_paging_through_the_archive_sees_every_track_exactly_once(
    archive: TestClient,
) -> None:
    """The property that makes pagination pagination rather than sampling."""
    seen: list[int] = []
    offset = 0
    while True:
        body = _list(archive, limit=1, offset=offset)
        page = body["tracks"]
        assert isinstance(page, list)
        if not page:
            break
        seen.extend(track["id"] for track in page)
        offset += 1

    assert len(seen) == len(set(seen)) == 4


def test_an_offset_past_the_end_is_an_empty_page_not_an_error(archive: TestClient) -> None:
    """Running off the end of a list is an ordinary thing for a client to do."""
    body = _list(archive, offset=999)

    assert body["tracks"] == []
    assert body["total"] == 4


# --- Ordered -----------------------------------------------------------------


@pytest.mark.parametrize(
    "sort",
    ["imported_newest_first", "activity_newest_first", "activity_oldest_first", "longest_first"],
)
def test_every_ordering_is_deterministic(archive: TestClient, sort: str) -> None:
    """Asked twice, answered the same. Otherwise paging is a lottery."""
    first = _list(archive, sort=sort)["tracks"]
    second = _list(archive, sort=sort)["tracks"]

    assert first == second


def test_tracks_can_be_ordered_by_when_the_activity_happened(archive: TestClient) -> None:
    """The order a dashboard wants: what did I do, most recently first."""
    body = _list(archive, sort="activity_newest_first")
    tracks = body["tracks"]
    assert isinstance(tracks, list)

    dated = [
        track["timeline"]["started_at"]
        for track in tracks
        if track["timeline"]["started_at"] is not None
    ]
    assert dated == sorted(dated, reverse=True)


def test_a_track_without_a_date_sorts_last_rather_than_first(archive: TestClient) -> None:
    """Absent is not "the beginning of time".

    A planned route has no activity date. Sorting it as 1970 would put it at one
    end of every chronological list and make it look like the oldest thing in
    the archive.
    """
    for sort in ("activity_newest_first", "activity_oldest_first"):
        tracks = _list(archive, sort=sort)["tracks"]
        assert isinstance(tracks, list)
        dates = [track["timeline"]["started_at"] for track in tracks]
        undated = [index for index, value in enumerate(dates) if value is None]
        assert undated == list(range(len(dates) - len(undated), len(dates))), sort


def test_tracks_can_be_ordered_by_distance(archive: TestClient) -> None:
    """Longest first, and a track with no distance is not the shortest one."""
    tracks = _list(archive, sort="longest_first")["tracks"]
    assert isinstance(tracks, list)

    distances = [track["analysis"]["distance_m"] for track in tracks]
    measured = [value for value in distances if value is not None]
    assert measured == sorted(measured, reverse=True)


# --- Filtered ----------------------------------------------------------------


def test_a_listing_can_be_narrowed_to_one_kind(archive: TestClient) -> None:
    """The actual/planned separation the whole archive is built around."""
    body = _list(archive, kind="recorded")
    tracks = body["tracks"]
    assert isinstance(tracks, list)

    assert body["total"] == 3
    assert {track["classification"]["effective_kind"] for track in tracks} == {"recorded"}


def test_a_listing_can_be_narrowed_to_one_activity(archive: TestClient) -> None:
    """A cycling dashboard asks for cycling."""
    body = _list(archive, activity="cycling")
    tracks = body["tracks"]
    assert isinstance(tracks, list)

    assert body["total"] == 1
    assert tracks[0]["activity"] == "cycling"


def test_a_listing_can_be_narrowed_to_one_year(archive: TestClient) -> None:
    """The year is the configured local year, as everywhere else."""
    body = _list(archive, year=2025)

    assert body["total"] == 2


def test_a_listing_can_be_narrowed_to_one_month(archive: TestClient) -> None:
    """Clicking a bar in a monthly chart has to reach the tracks behind it."""
    body = _list(archive, year=2025, month=2)
    tracks = body["tracks"]
    assert isinstance(tracks, list)

    assert body["total"] == 1
    assert tracks[0]["title"] == "feb walk"


def test_a_month_without_a_year_is_refused(archive: TestClient) -> None:
    """February of no particular year is not a period."""
    assert archive.get("/api/v1/tracks", params={"month": 2}).status_code == 422


def test_filters_combine_rather_than_replace_each_other(archive: TestClient) -> None:
    """Each narrows the selection; none of them widens it."""
    body = _list(archive, kind="recorded", activity="walking", year=2025)

    assert body["total"] == 2


def test_a_filtered_total_counts_the_filtered_selection(archive: TestClient) -> None:
    """A total that ignored the filter would make the pager lie."""
    body = _list(archive, activity="cycling", limit=1)

    assert body["total"] == 1


def test_a_filter_matching_nothing_is_an_empty_page(archive: TestClient) -> None:
    """Not an error. "No tracks in April" is an answer a dashboard renders."""
    body = _list(archive, year=2026, month=11)

    assert body["total"] == 0
    assert body["tracks"] == []


def test_an_unknown_sort_key_is_refused(archive: TestClient) -> None:
    """The orderings are a closed set, so a typo fails rather than defaults."""
    assert archive.get("/api/v1/tracks", params={"sort": "by_vibes"}).status_code == 422


# --- Enough per row to render without a second request -----------------------


def test_a_listed_row_carries_what_an_overview_needs(archive: TestClient) -> None:
    """A list view that costs a request per row is a list view nobody uses."""
    tracks = _list(archive)["tracks"]
    assert isinstance(tracks, list)
    row = tracks[0]

    assert {"id", "title", "activity", "classification", "analysis", "timeline"} <= set(row)
    assert {"effective_kind", "detected_kind"} <= set(row["classification"])
    assert {"status", "distance_m", "elevation_gain_m"} <= set(row["analysis"])
    assert {"started_at", "basis", "is_actual_calendar_time"} <= set(row["timeline"])
    assert "segments" not in row, "a listing must never carry geometry"
