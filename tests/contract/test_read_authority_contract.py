"""Executable contract: three read surfaces, one statement about an analysis.

A track can be read three ways -- in a listing, through its own analysis
resource, and as part of a total -- and every one of them has to say the same
thing about what its stored metrics are worth:

```
current   the installed algorithms produced them from the geometry a reader sees
outdated  metrics exist, produced by algorithms or geometry that moved on
missing   nothing has ever been derived
invalid   something was derived and cannot be interpreted
```

Four states, one authority, three projections. The failure this pins down is the
quiet one: a listing that keeps presenting a stale distance as the track's
length, a "longest first" ordering built on it, and a total that counts damage as
absence. None of those look wrong from the outside, which is exactly why they
need a test rather than a reader.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gpx_view.application.import_tracks import ImportRequest
from gpx_view.config import Settings
from gpx_view.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.analysis, pytest.mark.statistics]

_DEGREE = 111_195.0

RECORDING = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>{name}</name><type>walking</type><trkseg>
{points}
  </trkseg></trk>
</gpx>
"""


def _recording(name: str, *, metres: float, count: int = 120) -> bytes:
    """Build a synthetic recording of a stated straight-line length."""
    step = metres / (count - 1)
    points = "\n".join(
        f'    <trkpt lat="{index * step / _DEGREE:.8f}" lon="8.0">'
        f"<ele>{100 + index * 0.5:.1f}</ele>"
        f"<time>2025-10-05T09:{index // 60:02d}:{index % 60:02d}Z</time>"
        f"<hdop>1.1</hdop></trkpt>"
        for index in range(count)
    )
    return RECORDING.format(name=name, points=points).encode("utf-8")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data", timezone="UTC")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Yield a client over a throwaway archive."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _import(client: TestClient, content: bytes, name: str) -> int:
    """Import one document through the canonical use case."""
    services = client.app.state.services  # type: ignore[attr-defined]
    outcome = services.import_tracks(ImportRequest(content=content, original_filename=name))
    return int(outcome.track_ids[0])


def _write(settings: Settings, statement: str, *parameters: object) -> None:
    """Change stored state the way a bad disk or an older release would have."""
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(statement, parameters)
        connection.commit()


def _outdate(settings: Settings, track_id: int) -> None:
    """Make one track's stored analysis name algorithms this build does not run.

    A perfectly readable profile that is simply not the installed one: exactly
    what every stored result looks like the day an algorithm is corrected.
    """
    _write(
        settings,
        "UPDATE analysis_runs SET elevation_algorithm_version = 99 WHERE track_id = ?",
        track_id,
    )


@pytest.fixture
def two_tracks(client: TestClient, settings: Settings) -> tuple[int, int]:
    """Return a current 10 km track and an outdated 20 km one, in that order."""
    short = _import(client, _recording("ten", metres=10_000.0), "short.gpx")
    long = _import(client, _recording("twenty", metres=20_000.0), "long.gpx")
    _outdate(settings, long)
    return short, long


def _tracks(client: TestClient, **params: object) -> dict[str, object]:
    """Ask for a page of tracks."""
    response = client.get("/api/v1/tracks", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _analysis(client: TestClient, track_id: int) -> dict[str, object]:
    """Ask for one track's own analysis resource."""
    response = client.get(f"/api/v1/tracks/{track_id}/analysis")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _row(body: dict[str, object], track_id: int) -> dict[str, object]:
    """Return one track's row out of a listing page."""
    rows = [row for row in body["tracks"] if row["id"] == track_id]  # type: ignore[attr-defined,index]
    assert rows, f"track {track_id} missing from the page"
    return dict(rows[0])


# --- A stale metric is never a headline --------------------------------------


def test_an_outdated_analysis_is_not_presented_as_current(
    client: TestClient, two_tracks: tuple[int, int]
) -> None:
    """The listing says `outdated`, and shows no number under that heading."""
    _, long = two_tracks

    row = _row(_tracks(client), long)

    assert row["analysis"]["status"] == "outdated"  # type: ignore[index]
    assert row["analysis"]["distance_m"] is None, "a stale distance was served as current"  # type: ignore[index]
    assert row["analysis"]["elevation_gain_m"] is None  # type: ignore[index]


def test_a_current_analysis_still_carries_its_metrics(
    client: TestClient, two_tracks: tuple[int, int]
) -> None:
    """Gating the stale ones must not cost the good ones their numbers."""
    short, _ = two_tracks

    row = _row(_tracks(client), short)

    assert row["analysis"]["status"] == "current"  # type: ignore[index]
    assert row["analysis"]["distance_m"] == pytest.approx(10_000.0, rel=0.01)  # type: ignore[index]


def test_longest_first_orders_by_the_current_distance_only(
    client: TestClient, two_tracks: tuple[int, int]
) -> None:
    """`longest_first` means longest *according to a current analysis*."""
    short, long = two_tracks

    body = _tracks(client, sort="longest_first")
    order = [row["id"] for row in body["tracks"]]  # type: ignore[attr-defined,index]

    assert order.index(short) < order.index(long), "an outdated 20 km sorted above a current 10 km"


def test_tracks_without_a_current_distance_sort_last(
    client: TestClient, settings: Settings, two_tracks: tuple[int, int]
) -> None:
    """Nothing to compare sorts after everything there is something to compare."""
    short, long = two_tracks
    unanalysed = _import(client, _recording("none", metres=30_000.0), "none.gpx")
    _write(settings, "UPDATE tracks SET current_analysis_run_id = NULL WHERE id = ?", unanalysed)

    body = _tracks(client, sort="longest_first")
    order = [row["id"] for row in body["tracks"]]  # type: ignore[attr-defined,index]

    assert order[0] == short
    assert order.index(unanalysed) > order.index(short)
    assert order.index(long) > order.index(short)


# --- Damage says the same thing everywhere -----------------------------------


DAMAGE = {
    "unreadable_quality_flag": (
        "INSERT INTO analysis_quality_flags (analysis_run_id, position, flag) "
        "SELECT id, 99, 'who_knows' FROM analysis_runs WHERE track_id = ?"
    ),
    "impossible_metric": (
        "UPDATE track_metrics SET value = -5 WHERE metric = 'distance_m' AND analysis_run_id IN "
        "(SELECT id FROM analysis_runs WHERE track_id = ?)"
    ),
    "unknown_unit": (
        "UPDATE track_metrics SET unit = 'furlongs' WHERE metric = 'distance_m' "
        "AND analysis_run_id IN (SELECT id FROM analysis_runs WHERE track_id = ?)"
    ),
    "unreadable_status": ("UPDATE analysis_runs SET status = 'half-way' WHERE track_id = ?"),
    "missing_timestamp": ("UPDATE analysis_runs SET analyzed_at = '' WHERE track_id = ?"),
    "unparseable_timestamp": (
        "UPDATE analysis_runs SET analyzed_at = 'the other day' WHERE track_id = ?"
    ),
    "naive_timestamp": (
        "UPDATE analysis_runs SET analyzed_at = '2026-05-04T12:00:00' WHERE track_id = ?"
    ),
    "impossible_profile_version": (
        "UPDATE analysis_runs SET distance_algorithm_version = 0 WHERE track_id = ?"
    ),
    "unnamed_algorithm": ("UPDATE analysis_runs SET movement_algorithm = '' WHERE track_id = ?"),
}


@pytest.fixture(params=sorted(DAMAGE))
def damaged_track(
    request: pytest.FixtureRequest, client: TestClient, settings: Settings
) -> tuple[int, int]:
    """Return a healthy track and a damaged one, in that order."""
    healthy = _import(client, _recording("healthy", metres=5_000.0), "healthy.gpx")
    broken = _import(client, _recording("broken", metres=6_000.0), "broken.gpx")
    _write(settings, DAMAGE[request.param], broken)
    return healthy, broken


def test_detail_listing_and_statistics_agree_that_an_analysis_is_invalid(
    client: TestClient, damaged_track: tuple[int, int]
) -> None:
    """One damaged analysis, three surfaces, one word for it."""
    healthy, broken = damaged_track

    detail = _analysis(client, broken)
    row = _row(_tracks(client), broken)
    year = client.get("/api/v1/statistics/year/2025").json()

    assert detail["status"] == "invalid"
    assert row["analysis"]["status"] == "invalid", "the listing hid the damage"  # type: ignore[index]
    assert row["analysis"]["distance_m"] is None  # type: ignore[index]
    assert year["totals"]["tracks_with_invalid_analysis"] == 1
    assert year["totals"]["tracks_without_analysis"] == 0, "damage was counted as absence"
    assert year["totals"]["analysed_track_count"] == 1
    assert _row(_tracks(client), healthy)["analysis"]["status"] == "current"  # type: ignore[index]


def test_statistics_separate_missing_from_invalid(
    client: TestClient, settings: Settings, damaged_track: tuple[int, int]
) -> None:
    """Nothing-derived-yet and something-is-wrong are different shortfalls."""
    _, _ = damaged_track
    never = _import(client, _recording("never", metres=7_000.0), "never.gpx")
    _write(settings, "UPDATE tracks SET current_analysis_run_id = NULL WHERE id = ?", never)

    totals = client.get("/api/v1/statistics/year/2025").json()["totals"]

    assert totals["track_count"] == 3
    assert totals["tracks_without_analysis"] == 1
    assert totals["tracks_with_invalid_analysis"] == 1
    assert totals["analysed_track_count"] == 1


def test_the_four_availability_counters_cover_every_track(
    client: TestClient, settings: Settings, damaged_track: tuple[int, int]
) -> None:
    """Current + missing + outdated + invalid == track_count. An equality."""
    _, _ = damaged_track
    never = _import(client, _recording("never", metres=7_000.0), "never.gpx")
    _write(settings, "UPDATE tracks SET current_analysis_run_id = NULL WHERE id = ?", never)
    stale = _import(client, _recording("stale", metres=8_000.0), "stale.gpx")
    _outdate(settings, stale)

    totals = client.get("/api/v1/statistics/year/2025").json()["totals"]

    assert (
        totals["analysed_track_count"]
        + totals["tracks_without_analysis"]
        + totals["tracks_with_outdated_analysis"]
        + totals["tracks_with_invalid_analysis"]
        == totals["track_count"]
    )


# --- Filtering by analysis status --------------------------------------------


@pytest.fixture
def four_states(client: TestClient, settings: Settings) -> dict[str, int]:
    """Return one track in each of the four availability states."""
    current = _import(client, _recording("current", metres=1_000.0), "current.gpx")
    outdated = _import(client, _recording("outdated", metres=2_000.0), "outdated.gpx")
    missing = _import(client, _recording("missing", metres=3_000.0), "missing.gpx")
    invalid = _import(client, _recording("invalid", metres=4_000.0), "invalid.gpx")
    _outdate(settings, outdated)
    _write(settings, "UPDATE tracks SET current_analysis_run_id = NULL WHERE id = ?", missing)
    _write(
        settings,
        "INSERT INTO analysis_quality_flags (analysis_run_id, position, flag) "
        "SELECT id, 99, 'who_knows' FROM analysis_runs WHERE track_id = ?",
        invalid,
    )
    return {"current": current, "outdated": outdated, "missing": missing, "invalid": invalid}


@pytest.mark.parametrize("state", ["current", "outdated", "missing", "invalid"])
def test_tracks_can_be_filtered_by_analysis_status(
    client: TestClient, four_states: dict[str, int], state: str
) -> None:
    """Each state selects exactly its own track."""
    body = _tracks(client, analysis_status=state)

    assert body["total"] == 1, body
    assert [row["id"] for row in body["tracks"]] == [four_states[state]]  # type: ignore[attr-defined,index]


def test_filtering_by_analysis_status_keeps_pagination_honest(
    client: TestClient, four_states: dict[str, int], settings: Settings
) -> None:
    """`total` counts the filtered selection, not the page and not the archive."""
    assert four_states
    for name in ("second", "third"):
        _outdate(settings, _import(client, _recording(name, metres=900.0), f"{name}.gpx"))

    first = _tracks(client, analysis_status="outdated", limit=2, offset=0)
    second = _tracks(client, analysis_status="outdated", limit=2, offset=2)

    assert first["total"] == 3
    assert second["total"] == 3
    assert len(first["tracks"]) == 2  # type: ignore[arg-type]
    assert len(second["tracks"]) == 1  # type: ignore[arg-type]
    seen = [row["id"] for row in first["tracks"]] + [row["id"] for row in second["tracks"]]  # type: ignore[attr-defined,index,operator]
    assert len(set(seen)) == 3, "a row appeared twice or not at all across pages"


def test_an_unknown_analysis_status_is_refused(client: TestClient) -> None:
    """The filter is a closed vocabulary, not a free-text column name."""
    assert client.get("/api/v1/tracks", params={"analysis_status": "probably"}).status_code == 422


# --- The upper end of the calendar -------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/statistics/year/9999",
        "/api/v1/statistics/year/9999/monthly",
    ],
)
def test_the_last_supported_year_is_answerable(client: TestClient, path: str) -> None:
    """`year + 1` must not be how a supported year is turned into a window."""
    response = client.get(path)

    assert response.status_code == 200, response.text


@pytest.mark.parametrize("params", [{"year": 9999}, {"year": 9999, "month": 12}])
def test_the_last_supported_year_lists_tracks(client: TestClient, params: dict[str, int]) -> None:
    """The same year, through the listing's own calendar filter."""
    response = client.get("/api/v1/tracks", params=params)

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


def test_a_year_beyond_the_supported_range_is_refused(client: TestClient) -> None:
    """A route must never validate a year the use case cannot then answer."""
    assert client.get("/api/v1/statistics/year/10000").status_code == 422
    assert client.get("/api/v1/tracks", params={"year": 10_000}).status_code == 422
