"""Reprocessing a source the archive already holds, end to end.

This is the path the raw import layer exists for: original bytes in, a newly
normalized and classified generation out, with the source untouched and the
user's corrections intact.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpx_view.application import ImportErrorCode
from gpx_view.application.import_tracks import ImportRequest
from gpx_view.application.ports import TrackQuery
from gpx_view.application.reprocess import ReprocessStatus
from gpx_view.cli import EXIT_FAILED, EXIT_OK, main
from gpx_view.config import Settings
from gpx_view.domain import (
    ClassificationResult,
    NormalizedTrack,
    ProcessingRun,
    ProcessingStatus,
    SourceMetadata,
    TrackClassification,
    TrackKind,
    TrackPoint,
    TrackSegment,
)
from gpx_view.infrastructure.assembly import TrackServices, build_services

pytestmark = [pytest.mark.integration, pytest.mark.reprocessing, pytest.mark.persistence]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

THEN = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def services(tmp_path: Path) -> TrackServices:
    """Return a wired archive in a throwaway data directory."""
    wired = build_services(Settings(data_dir=tmp_path / "data"))
    wired.prepare_storage()
    return wired


def _content(name: str) -> bytes:
    """Return the bytes of a synthetic fixture."""
    return (FIXTURES / name).read_bytes()


def _import(services: TrackServices, name: str) -> str:
    """Import a fixture and return its content hash."""
    outcome = services.import_tracks(ImportRequest(content=_content(name), original_filename=name))
    return outcome.sha256


def test_reprocessing_produces_a_new_run_and_a_new_current_generation(
    services: TrackServices,
) -> None:
    """The whole point: normalize the same source again, from the source."""
    sha256 = _import(services, "recorded-measurements.gpx")

    outcome = services.reprocess(sha256)

    assert outcome.status is ReprocessStatus.REPROCESSED
    assert len(outcome.track_ids) == 1
    assert services.store.run_count(sha256) == 2
    latest = services.store.latest_run(sha256)
    assert latest is not None
    assert latest.status is ProcessingStatus.SUCCEEDED


def test_reprocessing_keeps_the_source_byte_identical(services: TrackServices) -> None:
    """Reprocessing reads the source. It never writes to it."""
    original = _content("recorded-measurements.gpx")
    sha256 = _import(services, "recorded-measurements.gpx")
    before = services.store.find_raw_import(sha256)

    services.reprocess(sha256)

    assert services.raw_store.read(sha256) == original
    assert hashlib.sha256(services.raw_store.read(sha256)).hexdigest() == sha256
    assert services.store.find_raw_import(sha256) == before


def test_reprocessing_keeps_the_candidate_identity(services: TrackServices) -> None:
    """The same source describes the same candidates, whoever reads it."""
    sha256 = _import(services, "multiple-tracks.gpx")
    before = [track.source_key for track in services.store.list_tracks(TrackQuery()).tracks]

    services.reprocess(sha256)

    assert [track.source_key for track in services.store.list_tracks(TrackQuery()).tracks] == before


def test_a_user_correction_survives_reprocessing(services: TrackServices) -> None:
    """A classifier upgrade replaces its own verdict, never the user's."""
    sha256 = _import(services, "recorded-measurements.gpx")
    (track_id,) = services.store.track_ids_for(sha256)
    services.store.set_override(track_id, TrackKind.PLANNED, datetime(2026, 8, 8, tzinfo=UTC))

    services.reprocess(sha256)

    (summary,) = services.store.list_tracks(TrackQuery()).tracks
    assert summary.detected_kind is TrackKind.RECORDED
    assert summary.effective_kind is TrackKind.PLANNED
    assert summary.classification.is_overridden


def test_reprocessing_an_unknown_source_is_reported_not_invented(
    services: TrackServices,
) -> None:
    """Asking about bytes the archive never saw is answered, not guessed at."""
    outcome = services.reprocess("f" * 64)

    assert outcome.status is ReprocessStatus.UNKNOWN_SOURCE
    assert outcome.track_ids == ()


def test_a_corrupt_raw_artifact_never_becomes_a_new_generation(
    services: TrackServices,
) -> None:
    """Regenerating from bytes that no longer match their hash is refused.

    The previous generation stays current. Silently normalizing whatever is on
    disk would let a damaged artifact quietly replace good data with something
    derived from it.
    """
    sha256 = _import(services, "recorded-measurements.gpx")
    before = services.store.list_tracks(TrackQuery()).tracks
    services.raw_store.path_for(sha256).write_bytes(b"<gpx>not what this hash names</gpx>")

    outcome = services.reprocess(sha256)

    assert outcome.status is ReprocessStatus.FAILED
    assert outcome.error_code is ImportErrorCode.RAW_STORAGE_CORRUPT
    assert services.store.list_tracks(TrackQuery()).tracks == before


def test_a_source_that_could_not_be_read_can_be_reprocessed_later(
    services: TrackServices,
) -> None:
    """A failed import is kept precisely so that this is possible.

    The ordinary directory scan still recognises the bytes as a duplicate and
    skips them. "Already known" and "up to date" are different statements, and
    reprocessing is where the second one is acted on.
    """
    sha256 = _import(services, "not-gpx.xml")
    assert services.store.list_tracks(TrackQuery()).tracks == ()

    duplicate = services.import_tracks(ImportRequest(content=_content("not-gpx.xml")))
    outcome = services.reprocess(sha256)

    assert duplicate.status.value == "duplicate"
    assert outcome.status is ReprocessStatus.FAILED
    assert outcome.error_code is ImportErrorCode.UNSUPPORTED_FORMAT
    # The import and the reprocess each recorded an attempt. The duplicate did
    # not: recognising known bytes has to stay cheap, which is the whole reason
    # reprocessing had to become a separate, explicit action.
    assert services.store.run_count(sha256) == 2


def test_reprocessing_keeps_every_earlier_attempt(services: TrackServices) -> None:
    """History is append-only, so a verdict stays explainable after it changed."""
    sha256 = _import(services, "recorded-measurements.gpx")

    services.reprocess(sha256)
    services.reprocess(sha256)

    assert services.store.run_count(sha256) == 3


# --- Which sources a batch covers -------------------------------------------


def _older_generation(services: TrackServices, sha256: str) -> None:
    """Make the current generation look like one an earlier build produced.

    A run is appended that claims the processing of the first productive
    build -- importer 1, normalization schema 1, classifier 1 -- and becomes the
    current generation. That is the shape a real archive is in after an upgrade,
    and it needs no checkout of the old implementation to reproduce: what makes a
    generation outdated is what the run *recorded*, not how it was computed.
    """
    raw = services.store.find_raw_import(sha256)
    assert raw is not None
    services.store.record_import(
        raw,
        ProcessingRun(
            raw_import_sha256=sha256,
            importer="gpx",
            importer_version="1",
            normalization_schema_version=1,
            processed_at=THEN,
            status=ProcessingStatus.SUCCEEDED,
            classifier="evidence-weights",
            classifier_version="1",
        ),
        [
            NormalizedTrack(
                source_key="trk:0",
                segments=(TrackSegment(points=(TrackPoint(latitude=51.0, longitude=7.0),)),),
                source=SourceMetadata(exchange_format="gpx", format_version="1.1"),
                classification=TrackClassification(
                    detected=ClassificationResult(
                        kind=TrackKind.UNKNOWN,
                        confidence=0.0,
                        method="evidence-weights",
                        method_version="1",
                    )
                ),
                title="Normalized by an older build",
            )
        ],
    )


def _fail_the_latest_attempt(services: TrackServices, sha256: str) -> None:
    """Append a failed attempt without disturbing the current generation."""
    raw = services.store.find_raw_import(sha256)
    assert raw is not None
    services.store.record_import(
        raw,
        ProcessingRun(
            raw_import_sha256=sha256,
            importer="gpx",
            importer_version="2",
            normalization_schema_version=2,
            processed_at=THEN,
            status=ProcessingStatus.FAILED,
            error_code="invalid_gpx",
            classifier="evidence-weights",
            classifier_version="2",
        ),
        [],
    )


def test_a_freshly_imported_source_is_in_neither_batch(services: TrackServices) -> None:
    """Nothing to do is the ordinary state, and both selections have to say so."""
    _import(services, "recorded-measurements.gpx")

    assert services.reprocess.outdated_sources() == ()
    assert services.reprocess.failed_sources() == ()


def test_a_generation_from_an_older_profile_is_outdated(services: TrackServices) -> None:
    """The archive can name data an upgrade would change, which is the whole point."""
    sha256 = _import(services, "recorded-measurements.gpx")
    _older_generation(services, sha256)

    assert services.reprocess.outdated_sources() == (sha256,)
    assert services.reprocess.failed_sources() == ()


def test_a_failed_attempt_beside_a_current_generation_is_failed_but_not_outdated(
    services: TrackServices,
) -> None:
    """The two selections answer different questions and must not merge.

    Readers are still served by a generation the installed processing produced,
    so regenerating it would change nothing. The newest attempt failed anyway,
    and that is worth retrying once the cause is fixed.
    """
    sha256 = _import(services, "recorded-measurements.gpx")
    _fail_the_latest_attempt(services, sha256)

    assert services.reprocess.outdated_sources() == ()
    assert services.reprocess.failed_sources() == (sha256,)


def test_a_source_that_never_processed_successfully_is_in_both_batches(
    services: TrackServices,
) -> None:
    """No generation at all is both a failure and the strongest form of outdated.

    Excluding it from `--outdated` would leave the archive's least healthy data
    out of the very upgrade meant to reach it.
    """
    sha256 = _import(services, "not-gpx.xml")

    assert services.reprocess.outdated_sources() == (sha256,)
    assert services.reprocess.failed_sources() == (sha256,)


def test_reprocessing_an_outdated_source_makes_it_current(services: TrackServices) -> None:
    """After the upgrade the source is current, and the old run still says otherwise.

    The history is not rewritten to match: the run that produced the old
    generation keeps the versions it recorded, which is what let `--outdated`
    find it in the first place.
    """
    sha256 = _import(services, "recorded-measurements.gpx")
    _older_generation(services, sha256)

    outcome = services.reprocess(sha256)

    assert outcome.status is ReprocessStatus.REPROCESSED
    assert services.reprocess.outdated_sources() == ()
    superseded = services.store.processing_snapshot(sha256)
    assert superseded is not None
    assert superseded.current_run is not None
    assert superseded.current_run.importer_version == "2"
    with services.store.connection() as connection:
        recorded = [
            (str(row["importer_version"]), row["classifier_version"])
            for row in connection.execute(
                "SELECT importer_version, classifier_version FROM processing_runs "
                "WHERE raw_import_sha256 = ? ORDER BY id",
                (sha256,),
            )
        ]
    assert recorded[1] == ("1", "1"), "the superseded run was rewritten to look current"


# --- The command line -------------------------------------------------------


def test_the_command_line_reprocesses_one_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator can name a source and have it normalized again."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    sha256 = _import(services, "recorded-measurements.gpx")

    exit_code = main(["reprocess", sha256], settings=settings)

    assert exit_code == EXIT_OK
    assert "reprocessed" in capsys.readouterr().out
    assert services.store.run_count(sha256) == 2


def test_the_command_line_reprocesses_every_failed_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After an importer learns a format, the failures are worth retrying at once."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    broken = _import(services, "malformed.gpx")
    good = _import(services, "recorded-measurements.gpx")

    exit_code = main(["reprocess", "--failed"], settings=settings)
    output = capsys.readouterr().out

    assert exit_code == EXIT_FAILED
    assert broken[:12] in output
    assert good[:12] not in output, "a source that processed cleanly was reprocessed anyway"


def test_the_command_line_reprocesses_every_outdated_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator action an importer upgrade needs, and nothing beyond it."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    stale = _import(services, "recorded-measurements.gpx")
    current = _import(services, "ambiguous-minimal.gpx")
    _older_generation(services, stale)

    exit_code = main(["reprocess", "--outdated"], settings=settings)
    output = capsys.readouterr().out

    assert exit_code == EXIT_OK
    assert stale[:12] in output
    assert current[:12] not in output, "a source the installed processing produced was redone"
    assert services.reprocess.outdated_sources() == ()


def test_an_outdated_batch_continues_past_a_source_it_cannot_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One damaged source must not cost the upgrade of every other one.

    The run still ends non-zero: continuing past a failure is not the same as
    forgiving it, and a script has to be able to tell.
    """
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    damaged = _import(services, "recorded-measurements.gpx")
    healthy = _import(services, "ambiguous-minimal.gpx")
    _older_generation(services, damaged)
    _older_generation(services, healthy)
    services.raw_store.path_for(damaged).write_bytes(b"<gpx>not these bytes</gpx>")

    exit_code = main(["reprocess", "--outdated"], settings=settings)
    output = capsys.readouterr().out

    assert exit_code == EXIT_FAILED
    assert damaged[:12] in output
    assert healthy[:12] in output
    assert services.reprocess.outdated_sources() == (damaged,)


@pytest.mark.parametrize("selection", ["--failed", "--outdated"])
def test_the_command_line_says_so_when_there_is_nothing_to_reprocess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], selection: str
) -> None:
    """An empty batch is a normal answer and says which one it was."""
    settings = Settings(data_dir=tmp_path / "data")
    build_services(settings).prepare_storage()

    exit_code = main(["reprocess", selection], settings=settings)

    assert exit_code == EXIT_OK
    assert "nothing to reprocess" in capsys.readouterr().out


def test_the_command_line_reports_what_produced_a_generation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator can see the latest attempt, the current run and each version."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    sha256 = _import(services, "recorded-measurements.gpx")
    _older_generation(services, sha256)

    exit_code = main(["processing-status", sha256], settings=settings)
    output = capsys.readouterr().out

    assert exit_code == EXIT_OK
    assert sha256 in output
    assert "importer:       1 -> installed 2" in output
    assert "normalization:  1 -> installed 3" in output
    assert "classifier:     1 -> installed 2" in output
    assert "outdated:       yes" in output


def test_the_reported_status_never_names_a_file_or_a_position(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Diagnostics must not become a side channel for private movement data."""
    settings = Settings(data_dir=tmp_path / "data")
    services = build_services(settings)
    services.prepare_storage()
    sha256 = _import(services, "recorded-measurements.gpx")

    main(["processing-status", sha256], settings=settings)
    output = capsys.readouterr().out

    assert "recorded-measurements.gpx" not in output
    assert str(settings.data_dir) not in output
    assert "51.0" not in output
    assert "outdated:       no" in output


def test_asking_about_a_source_the_archive_does_not_hold_is_a_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A status query about nothing answers, and says the run was not clean."""
    settings = Settings(data_dir=tmp_path / "data")
    build_services(settings).prepare_storage()

    exit_code = main(["processing-status", "f" * 64], settings=settings)

    assert exit_code == EXIT_FAILED
    assert "unknown source" in capsys.readouterr().out
