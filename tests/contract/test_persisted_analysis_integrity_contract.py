"""Executable contract: stored derived state is read as untrusted input.

SQLite is the archive's authority, and that is exactly why what comes back out
of it cannot be assumed well formed. A row can have been written by a version
that is gone, edited by hand at three in the morning, damaged by a failing disk,
or produced by a defect that has since been fixed. None of that is exotic; all
of it is silent until something reads the row.

The rule:

```
a stored analysis that cannot be interpreted
  is not analysis
  is never current
  and never reaches a caller as a traceback
```

Failing closed is the whole point. A profile version this build cannot make
sense of must not be mistaken for the installed one, an impossible metric must
not be summed into somebody's yearly distance, and neither may take down the
diagnostics view that exists to *show an operator the damage*.

The geometry behind such a track is untouched, so the repair is always the same
and always available: derive the metrics again.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trackvault.application.analysis import AnalysisAvailability, GetTrackAnalysis
from trackvault.application.import_tracks import ImportRequest
from trackvault.application.ports import TrackQuery
from trackvault.application.statistics import AggregationScope, GetYearStatistics
from trackvault.config import Settings
from trackvault.infrastructure.assembly import TrackServices, build_services
from trackvault.main import create_app

pytestmark = [pytest.mark.contract, pytest.mark.persistence, pytest.mark.analysis]

NOW = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
_DEGREE = 111_195.0

RECORDING = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Synthetic walk</name><type>walking</type><trkseg>
{points}
  </trkseg></trk>
</gpx>
"""


def _recording(count: int = 90) -> bytes:
    """Build a synthetic recording that analyses to real numbers."""
    points = "\n".join(
        f'    <trkpt lat="{index * 1.4 / _DEGREE:.8f}" lon="8.0">'
        f"<ele>{100 + index * 0.5:.1f}</ele>"
        f"<time>2026-05-04T09:{index // 60:02d}:{index % 60:02d}Z</time>"
        f"<hdop>1.1</hdop></trkpt>"
        for index in range(count)
    )
    return RECORDING.format(points=points).encode("utf-8")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings pointing at a throwaway data directory."""
    return Settings(data_dir=tmp_path / "data", timezone="UTC")


@pytest.fixture
def archive(settings: Settings) -> TrackServices:
    """Return a prepared archive holding one analysed recording."""
    services = build_services(settings)
    services.prepare_storage()
    services.import_tracks(ImportRequest(content=_recording(), original_filename="walk.gpx"))
    return services


@pytest.fixture
def track_id(archive: TrackServices) -> int:
    """Return the identity of the archive's one track."""
    return archive.store.list_tracks(TrackQuery()).tracks[0].track_id


def _damage(settings: Settings, statement: str, *parameters: object) -> None:
    """Corrupt the stored analysis the way a bad disk or a stray UPDATE would."""
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(statement, parameters)
        connection.commit()


@pytest.fixture
def client(settings: Settings, archive: TrackServices) -> Iterator[TestClient]:
    """Yield a client over the same archive the damage was applied to."""
    assert archive is not None
    with TestClient(create_app(settings)) as test_client:
        yield test_client


DAMAGE = {
    "impossible_profile_version": ("UPDATE analysis_runs SET distance_algorithm_version = 0", ()),
    "unnamed_algorithm": ("UPDATE analysis_runs SET movement_algorithm = ''", ()),
    "unreadable_status": ("UPDATE analysis_runs SET status = 'half-way'", ()),
    "missing_timestamp": ("UPDATE analysis_runs SET analyzed_at = ''", ()),
    "infinite_metric": ("UPDATE track_metrics SET value = 1e999 WHERE metric = 'distance_m'", ()),
    "negative_distance": ("UPDATE track_metrics SET value = -5 WHERE metric = 'distance_m'", ()),
    "negative_duration": (
        "UPDATE track_metrics SET value = -60 WHERE metric = 'moving_duration_s'",
        (),
    ),
    "unknown_unit": ("UPDATE track_metrics SET unit = 'furlongs' WHERE metric = 'distance_m'", ()),
    "unknown_provenance": ("UPDATE track_metrics SET provenance = 'guessed'", ()),
    "unreadable_quality_flag": (
        "INSERT INTO analysis_quality_flags (analysis_run_id, position, flag) "
        "SELECT id, 99, 'who_knows' FROM analysis_runs",
        (),
    ),
}


@pytest.fixture(params=sorted(DAMAGE))
def damaged(request: pytest.FixtureRequest, settings: Settings, archive: TrackServices) -> str:
    """Apply one shape of corruption to the stored analysis."""
    assert archive is not None
    statement, parameters = DAMAGE[request.param]
    _damage(settings, statement, *parameters)
    return str(request.param)


# --- Nothing raises ----------------------------------------------------------


def test_reading_a_damaged_analysis_never_raises(
    damaged: str, archive: TrackServices, track_id: int
) -> None:
    """Every repository read survives it. The archive is still an archive."""
    assert damaged
    store = archive.store

    assert store.analysis_snapshot(track_id) is not None
    assert store.analysis_snapshots()
    assert store.current_analysis(track_id) is None, "damaged metrics were handed out as good"
    assert store.list_tracks(TrackQuery()).tracks
    assert (
        store.placed_aggregation_rows(
            datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)
        )
        is not None
    )


def test_a_damaged_analysis_is_never_current(
    damaged: str, archive: TrackServices, track_id: int
) -> None:
    """Fail closed: what cannot be read has not shown itself to be right."""
    assert damaged
    snapshot = archive.store.analysis_snapshot(track_id)
    assert snapshot is not None

    assert not archive.analyze.installed.is_current(snapshot)
    assert track_id in archive.analyze.outdated_tracks()


def test_a_damaged_analysis_is_reported_as_such(
    damaged: str, archive: TrackServices, track_id: int
) -> None:
    """An operator has to be able to tell this apart from "not analysed yet"."""
    assert damaged
    report = GetTrackAnalysis(repository=archive.store, analysis=archive.analyze.installed)(
        track_id
    )

    assert report is not None
    assert report.status is AnalysisAvailability.INVALID
    assert not report.metrics, "no metric from an uninterpretable run reaches a caller"


def test_a_damaged_analysis_never_reaches_a_total(damaged: str, archive: TrackServices) -> None:
    """An impossible distance must not become part of somebody's year."""
    assert damaged
    totals = GetYearStatistics(
        repository=archive.store, timezone="UTC", analysis=archive.analyze.installed
    )(2026, scope=AggregationScope.RECORDED).totals

    assert totals.track_count == 1
    assert totals.analysed_track_count == 0
    assert totals.distance_m is None


def test_the_diagnostics_view_still_answers(damaged: str, archive: TrackServices) -> None:
    """The view whose job is showing the damage must survive the damage."""
    assert damaged
    sha = archive.store.list_tracks(TrackQuery()).tracks[0].raw_import_sha256

    report = archive.processing_status(sha)

    assert report is not None
    assert report.outdated_analysis_count == 1
    assert report.analysed_track_count == 0


# --- Nothing leaks over HTTP -------------------------------------------------


def test_the_api_answers_without_a_stack_trace(
    damaged: str, client: TestClient, track_id: int
) -> None:
    """A damaged row is not a server defect the caller has to read about."""
    assert damaged

    for path in (
        "/api/v1/tracks",
        f"/api/v1/tracks/{track_id}",
        f"/api/v1/tracks/{track_id}/analysis",
        "/api/v1/statistics/year/2026",
        "/api/v1/statistics/year/2026/monthly",
    ):
        response = client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        assert "Traceback" not in response.text
        assert "sqlite3" not in response.text


def test_the_service_is_still_healthy(damaged: str, client: TestClient) -> None:
    """One damaged track is a data problem, not an unhealthy service.

    Health is about whether this process can serve requests. Wiring data
    integrity into it would make a readiness probe restart a container that has
    nothing wrong with it, and would hide the one row that does.
    """
    assert damaged

    assert client.get("/healthz").status_code == 200


# --- The repair is the ordinary one ------------------------------------------


def test_reanalysing_repairs_a_damaged_analysis(
    damaged: str, archive: TrackServices, track_id: int
) -> None:
    """Metrics are derived state. The geometry they came from was never touched."""
    assert damaged

    for outdated in archive.analyze.outdated_tracks():
        archive.analyze(outdated)

    snapshot = archive.store.analysis_snapshot(track_id)
    assert snapshot is not None
    assert archive.analyze.installed.is_current(snapshot)
    stored = archive.store.current_analysis(track_id)
    assert stored is not None
    assert stored.metrics


# --- A profile from the future is not the installed one ----------------------


def test_an_analysis_from_a_later_release_is_not_current(
    settings: Settings, archive: TrackServices, track_id: int
) -> None:
    """A version this build has never heard of cannot be the version it runs.

    Reporting it as current would mean never regenerating it after a downgrade,
    which is how a rolled-back deployment keeps serving numbers its own code
    cannot produce.
    """
    _damage(settings, "UPDATE analysis_runs SET metric_schema_version = 9999")

    snapshot = archive.store.analysis_snapshot(track_id)
    assert snapshot is not None
    assert not archive.analyze.installed.is_current(snapshot)

    report = GetTrackAnalysis(repository=archive.store, analysis=archive.analyze.installed)(
        track_id
    )
    assert report is not None
    assert report.status is AnalysisAvailability.OUTDATED
    assert report.profile is not None
    assert report.profile.metric_schema_version == 9999, (
        "the stored profile is reported as it is, so an operator can see what happened"
    )


def test_an_unknown_metric_name_is_skipped_rather_than_fatal(
    settings: Settings, archive: TrackServices, track_id: int
) -> None:
    """A metric a later release added is history this build cannot use.

    Skipping it keeps the metrics that are still meaningful readable, which is
    the opposite trade from an unreadable *value*: a name this build does not
    know says nothing about the rows it does know.
    """
    _damage(
        settings,
        "UPDATE track_metrics SET metric = 'vertical_speed_mps' WHERE metric = 'elevation_loss_m'",
    )

    stored = archive.store.current_analysis(track_id)

    assert stored is not None
    assert stored.metrics, "known metrics survived an unknown sibling"


# --- A track that was never analysed is still distinguishable ----------------


def test_damage_is_not_confused_with_never_having_been_analysed(
    settings: Settings, archive: TrackServices, track_id: int
) -> None:
    """Two different states, two different answers, two different actions.

    Both need `analyze --outdated`, so both are outdated for selection. What
    they are *not* is the same thing to look at: one archive has not got round
    to a track, the other is holding a row it cannot read.
    """
    report = GetTrackAnalysis(repository=archive.store, analysis=archive.analyze.installed)

    _damage(settings, "UPDATE tracks SET current_analysis_run_id = NULL")
    _damage(settings, "DELETE FROM track_metrics")
    _damage(settings, "DELETE FROM analysis_quality_flags")
    _damage(settings, "DELETE FROM analysis_runs")
    missing = report(track_id)
    assert missing is not None
    assert missing.status is AnalysisAvailability.MISSING

    archive.analyze(track_id)
    _damage(settings, "UPDATE analysis_runs SET distance_algorithm_version = 0")
    damaged = report(track_id)
    assert damaged is not None
    assert damaged.status is AnalysisAvailability.INVALID
