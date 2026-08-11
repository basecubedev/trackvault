"""Executable contracts for raw imports and processing provenance.

Two authorities are deliberately separated here:

* ``RawImport`` answers "what was offered for import" -- the original bytes and
  the circumstances they arrived under. It is immutable source evidence.
* ``ProcessingRun`` answers "what did we make of it, with which importer, when".

The importer and its version describe the *processing*, never the source file. A
raw import that is reprocessed by a newer importer must come out unchanged.
"""

import dataclasses
from datetime import UTC, datetime

import pytest

from trackvault.domain import (
    NORMALIZATION_SCHEMA_VERSION,
    InputChannel,
    ProcessingRun,
    ProcessingStatus,
    RawImport,
)

SHA256 = "b" * 64
RECEIVED_AT = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


def _raw_import() -> RawImport:
    """Build a raw import for a test scenario."""
    return RawImport(
        sha256=SHA256,
        size_bytes=2048,
        original_filename="track.gpx",
        received_at=RECEIVED_AT,
        media_type="application/gpx+xml",
        input_channel=InputChannel.LOCAL_FILE,
    )


def _run(version: str, *, processed_at: datetime) -> ProcessingRun:
    """Build a successful processing run of the given importer version."""
    return ProcessingRun(
        raw_import_sha256=SHA256,
        importer="gpx",
        importer_version=version,
        normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
        processed_at=processed_at,
        status=ProcessingStatus.SUCCEEDED,
    )


# --- Raw imports are immutable source evidence ------------------------------


@pytest.mark.contract
def test_reprocessing_with_a_newer_importer_does_not_mutate_the_raw_import() -> None:
    """A parser upgrade produces another run; the source evidence is untouched."""
    raw = _raw_import()
    before = dataclasses.asdict(raw)

    first = _run("1", processed_at=datetime(2026, 3, 1, 9, 31, tzinfo=UTC))
    second = _run("2", processed_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC))

    assert dataclasses.asdict(raw) == before
    assert first.raw_import_sha256 == second.raw_import_sha256 == raw.sha256
    assert first != second
    assert (first.importer_version, second.importer_version) == ("1", "2")


@pytest.mark.contract
def test_a_raw_import_carries_no_processing_provenance() -> None:
    """Importer, parser and schema versions are properties of a run, not of a file."""
    field_names = {field.name for field in dataclasses.fields(RawImport)}

    leaked = {
        name for name in field_names if any(term in name for term in ("importer", "parser"))
    } | {name for name in field_names if name.endswith("version") or name.endswith("_version")}

    assert not leaked, f"RawImport must not own processing provenance: {sorted(leaked)}"


@pytest.mark.contract
def test_a_raw_import_cannot_be_edited_in_place() -> None:
    """The record of what arrived is a historical fact."""
    raw = _raw_import()

    with pytest.raises(dataclasses.FrozenInstanceError):
        raw.size_bytes = 1  # type: ignore[misc]


@pytest.mark.contract
def test_identical_bytes_are_the_same_raw_import_identity() -> None:
    """The content hash, not the filename or the channel, identifies a raw import."""
    first = _raw_import()
    second = dataclasses.replace(
        first,
        original_filename="a-completely-different-name.gpx",
        input_channel=InputChannel.IMPORT_DIRECTORY,
    )

    assert first.sha256 == second.sha256


# --- Processing provenance is explicit and re-runnable ----------------------


@pytest.mark.contract
def test_a_processing_run_states_who_produced_it_and_against_which_schema() -> None:
    """A stored normalization stays explainable and re-evaluable."""
    run = _run("1", processed_at=RECEIVED_AT)

    assert run.importer == "gpx"
    assert run.importer_version == "1"
    assert run.normalization_schema_version == NORMALIZATION_SCHEMA_VERSION
    assert run.status is ProcessingStatus.SUCCEEDED
    assert run.error_code is None


@pytest.mark.contract
def test_a_failed_processing_run_names_a_stable_error_code() -> None:
    """A failure that cannot be named cannot be recovered from later."""
    run = ProcessingRun(
        raw_import_sha256=SHA256,
        importer="gpx",
        importer_version="1",
        normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
        processed_at=RECEIVED_AT,
        status=ProcessingStatus.FAILED,
        error_code="invalid_gpx",
    )

    assert run.status is ProcessingStatus.FAILED
    assert run.error_code == "invalid_gpx"


@pytest.mark.contract
def test_a_failed_run_without_an_error_code_is_rejected() -> None:
    """A failure status alone is not a recoverable state."""
    with pytest.raises(ValueError, match="error_code"):
        ProcessingRun(
            raw_import_sha256=SHA256,
            importer="gpx",
            importer_version="1",
            normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
            processed_at=RECEIVED_AT,
            status=ProcessingStatus.FAILED,
        )


@pytest.mark.contract
def test_a_successful_run_may_not_also_report_an_error() -> None:
    """Success and failure are exclusive; a half state would not be recoverable."""
    with pytest.raises(ValueError, match="error_code"):
        ProcessingRun(
            raw_import_sha256=SHA256,
            importer="gpx",
            importer_version="1",
            normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
            processed_at=RECEIVED_AT,
            status=ProcessingStatus.SUCCEEDED,
            error_code="invalid_gpx",
        )
