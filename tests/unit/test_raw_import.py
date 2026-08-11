"""Unit tests for the raw import and processing provenance value objects."""

from datetime import UTC, datetime

import pytest

from gpx_view.domain import (
    NORMALIZATION_SCHEMA_VERSION,
    InputChannel,
    ProcessingRun,
    ProcessingStatus,
    RawImport,
)

SHA256 = "a" * 64
RECEIVED_AT = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


def _raw_import(**overrides: object) -> RawImport:
    """Build a raw import, overriding single fields for a scenario."""
    values: dict[str, object] = {
        "sha256": SHA256,
        "size_bytes": 1024,
        "original_filename": "track.gpx",
        "received_at": RECEIVED_AT,
        "media_type": "application/gpx+xml",
        "input_channel": InputChannel.LOCAL_FILE,
    }
    values.update(overrides)
    return RawImport(**values)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    "digest",
    ["", "abc", "A" * 64, "g" * 64, "a" * 63, "a" * 65, f"{'a' * 63} "],
)
def test_a_content_hash_that_is_not_a_sha256_digest_is_rejected(digest: str) -> None:
    """The content hash is the identity of a raw import, so its shape is fixed."""
    with pytest.raises(ValueError, match="sha256"):
        _raw_import(sha256=digest)


@pytest.mark.unit
def test_a_negative_size_is_rejected() -> None:
    """A stored artifact cannot have fewer than zero bytes."""
    with pytest.raises(ValueError, match="size_bytes"):
        _raw_import(size_bytes=-1)


@pytest.mark.unit
@pytest.mark.parametrize(
    "filename",
    ["../../etc/passwd", "sub/dir/track.gpx", "sub\\dir\\track.gpx", ".", ".."],
)
def test_a_filename_that_looks_like_a_path_is_rejected(filename: str) -> None:
    """The original filename is display metadata and never authorises a path."""
    with pytest.raises(ValueError, match="original_filename"):
        _raw_import(original_filename=filename)


@pytest.mark.unit
def test_a_missing_filename_is_allowed() -> None:
    """Not every input channel knows a filename, and none is required."""
    assert _raw_import(original_filename=None).original_filename is None


@pytest.mark.unit
def test_a_naive_received_timestamp_is_rejected() -> None:
    """Every stored instant is unambiguous; local wall-clock time is not."""
    with pytest.raises(ValueError, match="received_at"):
        _raw_import(received_at=datetime(2026, 3, 1, 9, 30))


@pytest.mark.unit
def test_the_short_identity_is_a_log_safe_hash_prefix() -> None:
    """Logs identify a raw import without repeating the whole digest."""
    short = _raw_import().short_sha256

    assert SHA256.startswith(short)
    assert len(short) == 12


@pytest.mark.unit
@pytest.mark.parametrize(
    ("importer", "version"), [("", "1"), ("  ", "1"), ("gpx", ""), ("gpx", " ")]
)
def test_an_unidentified_importer_is_rejected(importer: str, version: str) -> None:
    """A run that cannot be attributed cannot be compared against a later one."""
    with pytest.raises(ValueError, match="importer"):
        ProcessingRun(
            raw_import_sha256=SHA256,
            importer=importer,
            importer_version=version,
            normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
            processed_at=RECEIVED_AT,
            status=ProcessingStatus.SUCCEEDED,
        )


@pytest.mark.unit
def test_a_naive_processing_timestamp_is_rejected() -> None:
    """Processing instants are unambiguous too."""
    with pytest.raises(ValueError, match="processed_at"):
        ProcessingRun(
            raw_import_sha256=SHA256,
            importer="gpx",
            importer_version="1",
            normalization_schema_version=NORMALIZATION_SCHEMA_VERSION,
            processed_at=datetime(2026, 3, 1, 9, 30),
            status=ProcessingStatus.SUCCEEDED,
        )


@pytest.mark.unit
def test_processing_status_values_are_stable_wire_strings() -> None:
    """The status strings are part of the contract with storage and HTTP."""
    assert [status.value for status in ProcessingStatus] == ["succeeded", "failed"]


@pytest.mark.unit
def test_input_channel_values_are_stable_wire_strings() -> None:
    """Input channels are recorded metadata, so their names are part of the schema."""
    assert [channel.value for channel in InputChannel] == ["local_file", "import_directory"]
