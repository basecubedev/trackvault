"""Executable contract for the processing diagnostics view.

An operator upgrading an importer has to be able to ask two different questions
about one source and get two different answers:

```
what did the newest attempt do?      the latest processing run
what do readers currently see?       the run that owns the current generation
```

Those can disagree -- a failed attempt leaves the last good generation standing --
and a view that collapses them into one status makes a healthy archive look
broken or a broken one look healthy.

Diagnostics is a separate read boundary from the ordinary track queries, and it
is data-sparse on purpose. It reports hashes, run identities, versions,
timestamps, counts and error codes. It reports no coordinate, no file system
path, no original filename and no document content: a status page must not become
the side channel that the error contract already forbids.
"""

from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpx_view.application.import_tracks import ImportRequest
from gpx_view.application.processing_status import ProcessingStatusReport
from gpx_view.config import Settings
from gpx_view.domain import ProcessingRun, ProcessingStatus
from gpx_view.infrastructure.assembly import TrackServices, build_services

pytestmark = [pytest.mark.contract, pytest.mark.reprocessing, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

RECORDING = "recorded-measurements.gpx"


@pytest.fixture
def archive(tmp_path: Path) -> TrackServices:
    """Return a wired archive in a throwaway data directory."""
    services = build_services(Settings(data_dir=tmp_path / "data"))
    services.prepare_storage()
    return services


def _import(archive: TrackServices, name: str = RECORDING) -> str:
    """Import a synthetic fixture and return its content hash."""
    return archive.import_tracks(
        ImportRequest(content=(FIXTURES / name).read_bytes(), original_filename=name)
    ).sha256


def _status(archive: TrackServices, sha256: str) -> ProcessingStatusReport:
    """Return the diagnostics report for a source the archive holds."""
    report = archive.processing_status(sha256)
    assert report is not None
    return report


def _fail_the_latest_attempt(archive: TrackServices, sha256: str) -> None:
    """Append a failed run without touching the current generation."""
    raw = archive.store.find_raw_import(sha256)
    assert raw is not None
    archive.store.record_import(
        raw,
        ProcessingRun(
            raw_import_sha256=sha256,
            importer="gpx",
            importer_version="99",
            normalization_schema_version=99,
            processed_at=datetime(2026, 8, 8, 15, 0, tzinfo=UTC),
            status=ProcessingStatus.FAILED,
            error_code="invalid_gpx",
        ),
        [],
    )


# --- What the view answers ----------------------------------------------------


def test_a_source_the_archive_does_not_hold_has_no_status(archive: TrackServices) -> None:
    """Absence is reported, never invented."""
    assert archive.processing_status("f" * 64) is None


def test_a_freshly_imported_source_is_current(archive: TrackServices) -> None:
    """The ordinary case: one attempt, it succeeded, and it is what readers see."""
    sha256 = _import(archive)

    report = _status(archive, sha256)

    assert report.raw_import_sha256 == sha256
    assert report.current_run_id == report.latest_attempt_id
    assert report.latest_attempt_status is ProcessingStatus.SUCCEEDED
    assert report.current_profile == report.installed_profile
    assert report.is_outdated is False
    assert report.latest_error_code is None
    assert report.track_count == 1


def test_the_latest_attempt_and_the_current_generation_are_reported_separately(
    archive: TrackServices,
) -> None:
    """Both facts have to be visible at once, or one of them hides the other.

    A broken importer version appends a failed attempt and changes nothing about
    what readers see. An operator needs to know that the newest attempt failed
    *and* that the archive is still serving good data.
    """
    sha256 = _import(archive)
    current_before = _status(archive, sha256).current_run_id

    _fail_the_latest_attempt(archive, sha256)
    report = _status(archive, sha256)

    assert report.latest_attempt_status is ProcessingStatus.FAILED
    assert report.latest_error_code == "invalid_gpx"
    assert report.latest_attempt_id != report.current_run_id
    assert report.current_run_id == current_before
    assert report.track_count == 1


def test_a_source_that_never_processed_successfully_has_no_current_run(
    archive: TrackServices,
) -> None:
    """No generation at all is a state of its own, and it is not "current"."""
    sha256 = _import(archive, "not-gpx.xml")

    report = _status(archive, sha256)

    assert report.current_run_id is None
    assert report.current_profile is None
    assert report.latest_error_code == "unsupported_format"
    assert report.is_outdated is True
    assert report.track_count == 0


def test_the_view_names_both_profiles_so_an_operator_can_see_what_changed(
    archive: TrackServices,
) -> None:
    """An outdated verdict is only actionable if it says which component is behind."""
    sha256 = _import(archive)

    report = _status(archive, sha256)

    assert report.installed_profile is not None
    assert report.installed_profile.importer == "gpx"
    assert report.installed_profile.importer_version
    assert report.installed_profile.normalization_schema_version
    assert report.installed_profile.classifier_version


# --- Diagnostics stay data-sparse ---------------------------------------------


def test_the_report_carries_no_path_filename_or_document_content(
    archive: TrackServices,
) -> None:
    """A diagnostics view is not a side channel for private movement data."""
    sha256 = _import(archive)

    report = _status(archive, sha256)
    rendered = repr(report)

    assert RECORDING not in rendered
    assert str(archive.settings.data_dir) not in rendered
    assert "51.0" not in rendered
    assert "trkpt" not in rendered


def test_the_report_exposes_only_the_agreed_fields() -> None:
    """The field set is the contract: adding one is a deliberate act.

    Diagnostics is the view most likely to grow a "just this once" field, and the
    fields most useful for debugging are exactly the private ones.

    The analysis fields are counts, a profile and an error code -- the same
    shape as the processing ones. They are here rather than in a second
    diagnostics view because an operator asking what happened to a source should
    not need to know that normalization and analysis are separate lifecycles to
    notice that one of them is behind.
    """
    assert {field.name for field in fields(ProcessingStatusReport)} == {
        "raw_import_sha256",
        "current_run_id",
        "current_run_at",
        "current_profile",
        "installed_profile",
        "latest_attempt_id",
        "latest_attempt_at",
        "latest_attempt_status",
        "latest_error_code",
        "track_count",
        "is_outdated",
        "analysed_track_count",
        "outdated_analysis_count",
        "analysis_profile",
        "latest_analysis_error_code",
    }
