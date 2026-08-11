"""The operator's view of analysis: the command line and the automatic step.

Two promises are checked here.

*A file that arrives is usable.* Importing a track derives its metrics as part
of the same operator action, so a scanned sync folder produces statistics
without a second command. That step is best effort: analysis is derived state,
and failing to derive it must never cost the archive the import it just made.

*Nothing is stuck.* Whatever the automatic step missed -- because it failed,
because the algorithms changed, or because a reprocess produced new geometry --
`analyze --outdated` reaches, and one unanalysable track does not stop the rest.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from trackvault.application.analyze import AnalyzeTrack
from trackvault.application.ports import TrackQuery
from trackvault.cli import EXIT_FAILED, EXIT_OK, main
from trackvault.config import Settings
from trackvault.domain.analysis import MetricName
from trackvault.infrastructure.assembly import build_services

pytestmark = [pytest.mark.integration, pytest.mark.analysis]

RECORDING = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Synthetic walk</name><trkseg>
{points}
  </trkseg></trk>
</gpx>
"""


def _document(count: int = 120, *, start_second: int = 0) -> str:
    """Build a small synthetic recording that analyses to real numbers."""
    points = "\n".join(
        f'    <trkpt lat="{index * 1.4 / 111195.0:.8f}" lon="8.0">'
        f"<ele>{100 + index}</ele>"
        f"<time>2026-05-04T09:{(start_second + index) // 60:02d}:"
        f"{(start_second + index) % 60:02d}Z</time>"
        f"<hdop>1.2</hdop></trkpt>"
        for index in range(count)
    )
    return RECORDING.format(points=points)


@pytest.fixture
def archive(tmp_path: Path) -> Settings:
    """Return settings for a throwaway archive with a prepared database."""
    settings = Settings(data_dir=tmp_path / "data")
    build_services(settings).prepare_storage()
    return settings


def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write a source document to import."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _run(settings: Settings, argv: Sequence[str]) -> int:
    """Run one command against the throwaway archive."""
    return main(list(argv), settings=settings)


# --- Automatic analysis after import ----------------------------------------


def test_importing_a_track_derives_its_metrics(
    archive: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A file that arrives is immediately worth a statistic.

    Without this, every sync would need a second command before the numbers it
    produced meant anything.
    """
    source = _write(tmp_path, "walk.gpx", _document())

    assert _run(archive, ["import", str(source)]) == EXIT_OK

    capsys.readouterr()
    services = build_services(archive)
    (summary,) = services.store.list_tracks(TrackQuery()).tracks
    stored = services.store.current_analysis(summary.track_id)
    assert stored is not None
    assert stored.metrics[MetricName.DISTANCE].value > 100.0
    assert MetricName.MOVING_DURATION in stored.metrics


def test_an_imported_track_needs_no_further_analysis(
    archive: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The automatic step leaves nothing for the batch selection to find."""
    source = _write(tmp_path, "walk.gpx", _document())
    _run(archive, ["import", str(source)])
    capsys.readouterr()

    services = build_services(archive)

    assert services.analyze.outdated_tracks() == ()


def test_a_failing_analysis_never_costs_the_import(
    archive: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import is the authority; the metrics are a reading of it.

    Losing the reading is recoverable -- `analyze --outdated` derives it again.
    Losing the import would mean losing the only copy of the source evidence.
    """
    source = _write(tmp_path, "walk.gpx", _document())

    def explode(self: AnalyzeTrack, track_id: int) -> None:  # noqa: ARG001
        raise RuntimeError("analysis is broken")

    monkeypatch.setattr(AnalyzeTrack, "__call__", explode)

    assert _run(archive, ["import", str(source)]) == EXIT_OK

    services = build_services(archive)
    (summary,) = services.store.list_tracks(TrackQuery()).tracks
    assert summary.point_count == 120
    assert services.store.current_analysis(summary.track_id) is None


# --- The analyze command ----------------------------------------------------


def test_analyze_outdated_reaches_a_track_the_automatic_step_missed(
    archive: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The recovery path for everything the import-time step did not manage."""
    source = _write(tmp_path, "walk.gpx", _document())

    def explode(self: AnalyzeTrack, track_id: int) -> None:  # noqa: ARG001
        raise RuntimeError("analysis is broken")

    monkeypatch.setattr(AnalyzeTrack, "__call__", explode)
    _run(archive, ["import", str(source)])
    monkeypatch.undo()
    capsys.readouterr()

    assert _run(archive, ["analyze", "--outdated"]) == EXIT_OK

    output = capsys.readouterr().out
    assert "analyzed" in output
    services = build_services(archive)
    (summary,) = services.store.list_tracks(TrackQuery()).tracks
    assert services.store.current_analysis(summary.track_id) is not None


def test_analyze_outdated_says_so_when_there_is_nothing_to_do(
    archive: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A no-op run is reported as one, and is not a failure."""
    _run(archive, ["import", str(_write(tmp_path, "walk.gpx", _document()))])
    capsys.readouterr()

    assert _run(archive, ["analyze", "--outdated"]) == EXIT_OK

    assert "nothing to analyze" in capsys.readouterr().out


def test_analyze_all_reanalyses_current_tracks(
    archive: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator's override for "do it again whatever you think"."""
    _run(archive, ["import", str(_write(tmp_path, "walk.gpx", _document()))])
    capsys.readouterr()

    assert _run(archive, ["analyze", "--all"]) == EXIT_OK

    assert "analyzed" in capsys.readouterr().out
    services = build_services(archive)
    (summary,) = services.store.list_tracks(TrackQuery()).tracks
    assert services.store.analysis_run_count(summary.track_id) == 2


def test_analyzing_an_unknown_track_reports_a_failure(
    archive: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """An identity that does not exist ends with a non-zero exit code."""
    assert _run(archive, ["analyze", "4242"]) == EXIT_FAILED

    assert "unknown_track" in capsys.readouterr().out


def test_reprocessing_brings_the_analysis_along(
    archive: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """New geometry gets new metrics in the same operator action.

    Leaving them behind would mean a reprocess quietly made every statistic
    describe geometry that no longer exists.
    """
    source = _write(tmp_path, "walk.gpx", _document())
    _run(archive, ["import", str(source)])
    services = build_services(archive)
    (summary,) = services.store.list_tracks(TrackQuery()).tracks
    sha = summary.raw_import_sha256
    capsys.readouterr()

    assert _run(archive, ["reprocess", sha]) == EXIT_OK

    assert build_services(archive).analyze.outdated_tracks() == ()


# --- Diagnostics ------------------------------------------------------------


def test_processing_status_reports_the_analysis_too(
    archive: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One diagnostics view, not two.

    An operator asking what happened to a source should not have to know that
    normalization and analysis are separate lifecycles to find out that one of
    them is behind.
    """
    source = _write(tmp_path, "walk.gpx", _document())
    _run(archive, ["import", str(source)])
    services = build_services(archive)
    (summary,) = services.store.list_tracks(TrackQuery()).tracks
    capsys.readouterr()

    assert _run(archive, ["processing-status", summary.raw_import_sha256]) == EXIT_OK

    output = capsys.readouterr().out
    assert "analysis:" in output
    assert "analysis outdated: no" in output


def test_processing_status_never_prints_a_coordinate(
    archive: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Diagnostics must not become a side channel for movement data."""
    source = _write(tmp_path, "walk.gpx", _document())
    _run(archive, ["import", str(source)])
    services = build_services(archive)
    (summary,) = services.store.list_tracks(TrackQuery()).tracks
    capsys.readouterr()

    _run(archive, ["processing-status", summary.raw_import_sha256])

    output = capsys.readouterr().out
    assert "8.0" not in output
    assert "walk.gpx" not in output
    assert str(tmp_path) not in output
