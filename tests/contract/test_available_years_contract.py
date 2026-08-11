"""Which years an archive actually holds, and how an empty one says so.

The interface used to offer "this year and the eleven before it", computed in
the browser from the machine's clock. Two things were wrong with that, and the
second one is the reason this endpoint exists:

- an archive of 2019 recordings opened on an empty 2026 and looked broken;
- a year list computed from a clock is a second authority on the calendar, and
  the archive already owns which instants fall in which local year.

So the archive answers it, in the aggregation timezone it draws every other
boundary in, over exactly the tracks a period may date -- the same rule the
totals and the listing's `year` filter apply.

The response also carries the two counts a first screen needs to tell "nothing
imported yet" apart from "nothing in this selection", because that distinction
is the whole difference between a setup instruction and a wrong filter.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trackvault.application.import_tracks import ImportRequest
from trackvault.config import Settings
from trackvault.infrastructure.assembly import build_services
from trackvault.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.statistics]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"
YEARS_URL = "/api/v1/statistics/years"


def _recording(started: datetime, *, points: int = 4) -> bytes:
    """Return a synthetic recorded GPX document starting at one instant.

    Receiver quality is present on every position, which is what makes the
    instants *observed* -- and therefore what lets the track be placed in a
    calendar period at all.
    """
    positions = "".join(
        f'<trkpt lat="52.5{index}" lon="13.4{index}">'
        f"<ele>{34 + index}</ele>"
        f"<time>{(started + timedelta(minutes=index)).isoformat().replace('+00:00', 'Z')}</time>"
        f"<hdop>1.1</hdop>"
        f"</trkpt>"
        for index in range(points)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><name>Walk {started.year}</name><trkseg>{positions}</trkseg></trk>"
        "</gpx>"
    ).encode()


def _archive(tmp_path: Path, *instants: datetime, timezone: str = "UTC") -> Iterator[TestClient]:
    """Yield a client over an archive holding one recording per instant."""
    settings = Settings(data_dir=tmp_path / "data", timezone=timezone)
    services = build_services(settings)
    services.prepare_storage()
    for instant in instants:
        services.import_tracks(
            ImportRequest(
                content=_recording(instant), original_filename=f"{instant.year}-{instant.month}.gpx"
            )
        )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def empty(client: TestClient) -> TestClient:
    """An archive that holds nothing at all."""
    return client


@pytest.fixture
def three_years(tmp_path: Path) -> Iterator[TestClient]:
    """An archive holding recordings from 2019, 2021 and 2021 again."""
    yield from _archive(
        tmp_path,
        datetime(2019, 5, 4, 8, 0, tzinfo=UTC),
        datetime(2021, 7, 1, 8, 0, tzinfo=UTC),
        datetime(2021, 9, 2, 8, 0, tzinfo=UTC),
    )


def test_an_empty_archive_offers_no_years_and_says_it_is_empty(empty: TestClient) -> None:
    """The first-run answer, and it is not an error.

    ``archive_track_count`` is what makes an empty archive legible as one: a
    caller seeing no years otherwise cannot tell "nothing imported" from "you
    are looking at the wrong scope".
    """
    response = empty.get(YEARS_URL, params={"scope": "recorded"})

    assert response.status_code == 200
    assert response.json() == {
        "scope": "recorded",
        "activity": None,
        "timezone": "UTC",
        "years": [],
        "unplaced": {"without_date": 0, "with_unverified_date": 0},
        "archive_track_count": 0,
    }


def test_the_years_offered_are_the_years_the_archive_holds(three_years: TestClient) -> None:
    """Not a range from a clock. The years there is something to show for."""
    payload = three_years.get(YEARS_URL, params={"scope": "recorded"}).json()

    assert payload["years"] == [2021, 2019]
    assert payload["archive_track_count"] == 3


def test_the_newest_year_comes_first(three_years: TestClient) -> None:
    """A default is the first element, and the useful default is the newest.

    An archive somebody has not touched since 2021 should open on 2021 rather
    than on whatever year the reader's computer thinks it is.
    """
    years = three_years.get(YEARS_URL, params={"scope": "recorded"}).json()["years"]

    assert years == sorted(years, reverse=True)


def test_a_scope_only_offers_its_own_years(three_years: TestClient) -> None:
    """Recorded, planned and unknown stay three separate sets here too."""
    planned = three_years.get(YEARS_URL, params={"scope": "planned"}).json()

    assert planned["years"] == []
    assert planned["scope"] == "planned"
    assert planned["archive_track_count"] == 3


def test_boundaries_are_drawn_in_the_configured_zone(tmp_path: Path) -> None:
    """A local year, exactly as every other period in this archive.

    23:30 UTC on 31 December is already January in Berlin. Bucketing the
    instant in UTC would offer a year the totals and the listing filter do not
    agree with, and clicking it would show nothing.
    """
    instant = datetime(2020, 12, 31, 23, 30, tzinfo=UTC)

    for client in _archive(tmp_path, instant, timezone="Europe/Berlin"):
        payload = client.get(YEARS_URL, params={"scope": "recorded"}).json()

        assert payload["timezone"] == "Europe/Berlin"
        assert payload["years"] == [2021]
        assert client.get("/api/v1/tracks", params={"year": 2021}).json()["total"] == 1


def test_a_track_no_period_can_date_is_counted_beside_the_years(tmp_path: Path) -> None:
    """The unplaced state is visible rather than silently missing.

    The two halves are counted apart because they are different facts: the
    planned route here carries no instants at all, while a recording stripped
    of its receiver metadata would carry instants nothing vouches for. Both have
    a length and belong to no year, and a dashboard that only showed the years
    would lose either of them entirely.
    """
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    services.import_tracks(
        ImportRequest(
            content=(FIXTURES / "recorded-measurements.gpx").read_bytes(),
            original_filename="recorded-measurements.gpx",
        )
    )
    services.import_tracks(
        ImportRequest(
            content=(FIXTURES / "route-only.gpx").read_bytes(), original_filename="route-only.gpx"
        )
    )

    with TestClient(create_app(settings)) as client:
        recorded = client.get(YEARS_URL, params={"scope": "recorded"}).json()
        planned = client.get(YEARS_URL, params={"scope": "planned"}).json()

    assert recorded["years"] == [2026]
    assert recorded["archive_track_count"] == 2
    assert planned["unplaced"] == {"without_date": 1, "with_unverified_date": 0}


def test_narrowing_to_an_activity_narrows_the_years(three_years: TestClient) -> None:
    """The filter a caller will apply to the totals applies here too.

    Offering a year that the very next request reports as empty would be the
    same bug the endpoint exists to fix, one level down.
    """
    payload = three_years.get(YEARS_URL, params={"scope": "recorded", "activity": "cycling"}).json()

    assert payload["activity"] == "cycling"
    assert payload["years"] == []


def test_the_years_endpoint_never_answers_a_year_outside_the_calendar(
    three_years: TestClient,
) -> None:
    """Whatever is offered can be asked for."""
    payload = three_years.get(YEARS_URL, params={"scope": "recorded"}).json()

    for year in payload["years"]:
        assert three_years.get(f"/api/v1/statistics/year/{year}").status_code == 200
