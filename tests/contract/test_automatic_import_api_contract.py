"""The contract for reporting the automatic import over HTTP.

The automatic import runs on the server with nobody watching, so the one thing
a reader can do is ask what it did. The answer is a projection of what the
worker holds, never a second opinion about a file: every count comes from the
import use case's own outcomes, in its own vocabulary.

The worker the application starts is switched off here, and an automatic import
on a test clock is put in its place. It is the real one -- the real scanner, the
real import use case, the real archive -- driven by a clock the test sets, so a
scan happens exactly when the test says so and not whenever a thread gets round
to it.
"""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trackvault.config import Settings
from trackvault.infrastructure.assembly import TrackServices
from trackvault.infrastructure.automatic_import import AutomaticImport
from trackvault.main import create_app

pytestmark = [pytest.mark.contract]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"
STATUS = "/api/v1/tracks/imports/automatic"
INTERVAL = timedelta(minutes=15)


class SettableClock:
    """A clock that says exactly what the test tells it to."""

    def __init__(self, instant: datetime) -> None:
        """Start at a chosen instant."""
        self.instant = instant

    def now(self) -> datetime:
        """Return the instant the test set."""
        return self.instant


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    """Return an import directory holding one readable and one broken file."""
    directory = tmp_path / "inbox"
    directory.mkdir()
    shutil.copy(FIXTURES / "recorded-measurements.gpx", directory / "ride.gpx")
    shutil.copy(FIXTURES / "malformed.gpx", directory / "broken.gpx")
    return directory


@dataclass(frozen=True, slots=True)
class Served:
    """A running application, the automatic import it reports on, and its clock."""

    client: TestClient
    automatic: AutomaticImport
    clock: SettableClock


@pytest.fixture
def served(tmp_path: Path, inbox: Path) -> Iterator[Served]:
    """Yield a running application whose automatic import runs on a test clock."""
    settings = Settings(data_dir=tmp_path / "data", import_dir=inbox, import_scan_enabled=False)
    with TestClient(create_app(settings)) as client:
        services: TrackServices = client.app.state.services  # type: ignore[attr-defined]
        changed = max(path.stat().st_ctime_ns for path in inbox.iterdir()) / 1e9
        clock = SettableClock(datetime.fromtimestamp(changed, UTC) + timedelta(hours=1))
        automatic = AutomaticImport(
            directory=inbox,
            enabled=True,
            import_tracks=services.import_tracks,
            interval=INTERVAL,
            clock=clock,
            settle_time=timedelta(minutes=5),
        )
        client.app.state.automatic_import = automatic  # type: ignore[attr-defined]
        yield Served(client, automatic, clock)


def test_a_deployment_without_automatic_import_says_so(client: TestClient) -> None:
    """Off is a state worth stating, not an empty response to guess from."""
    body = client.get(STATUS).json()

    assert body["enabled"] is False
    assert body["last_scan"] is None
    assert body["last_activity"] is None
    assert body["next_scan_at"] is None
    assert body["interval_minutes"] == 15
    assert body["settle_minutes"] == 5


def test_before_the_first_scan_nothing_is_claimed(served: Served, inbox: Path) -> None:
    """A folder that has not been read yet has no result, not an empty one."""
    body = served.client.get(STATUS).json()

    assert body["enabled"] is True
    assert body["directory"] == str(inbox)
    assert body["last_scan"] is None


def test_a_scan_is_reported_file_by_file_where_it_matters(served: Served) -> None:
    """Counts for what went well, names and reasons for what did not."""
    scan = served.automatic.run_due()
    assert scan is not None

    body = served.client.get(STATUS).json()

    last = body["last_scan"]
    assert (last["discovered"], last["imported"], last["failed"]) == (2, 1, 1)
    assert (last["repaired"], last["skipped"], last["waiting"]) == (0, 0, 0)
    assert last["failures"] == [{"name": "broken.gpx", "error_code": "invalid_gpx"}]
    assert last["directory_available"] is True
    assert body["last_activity"] == last
    assert datetime.fromisoformat(body["next_scan_at"]) == scan.finished_at + INTERVAL


def test_a_quiet_scan_does_not_hide_the_failure_before_it(served: Served) -> None:
    """Most scans find nothing; the one that found a problem stays readable."""
    served.automatic.run_due()
    served.clock.instant += INTERVAL

    served.automatic.run_due()
    body = served.client.get(STATUS).json()

    assert body["last_scan"]["skipped"] == 2
    assert body["last_scan"]["failures"] == []
    assert body["last_activity"]["failures"] == [
        {"name": "broken.gpx", "error_code": "invalid_gpx"}
    ]


def test_a_folder_that_cannot_be_opened_is_not_reported_as_empty(
    served: Served, inbox: Path, tmp_path: Path
) -> None:
    """A share that is not mounted says so, instead of "nothing new"."""
    inbox.rename(tmp_path / "unmounted")

    served.automatic.run_due()
    body = served.client.get(STATUS).json()

    assert body["last_scan"]["directory_available"] is False
    assert body["last_scan"]["discovered"] == 0


def test_the_status_is_part_of_the_published_schema(client: TestClient) -> None:
    """A browser generates its types from the schema, so the schema must say it."""
    schema = client.get("/openapi.json").json()

    assert "get" in schema["paths"][STATUS]
