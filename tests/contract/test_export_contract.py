"""Export contracts: the original and a generated representation are not the same thing.

Two exports exist and they answer different questions:

```
raw export        the bytes that arrived        byte-identical, always
document export   a canonical exchange copy     generated, and says so
```

Confusing the two is the failure this whole module exists to prevent. A raw
export is evidence and must survive an importer upgrade unchanged; a document
export is what this build currently believes the track is, written into an
exchange format so another application can read it.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trackvault.application.export import (
    ExchangeDocument,
    ExportRawSource,
    ExportTrackDocument,
)
from trackvault.application.import_tracks import ImportRequest, ImportStatus
from trackvault.application.importing import ImportLimits
from trackvault.config import Settings
from trackvault.domain import Activity, InputChannel, TrackPoint, TrackSegment, UserTrackMetadata
from trackvault.infrastructure.assembly import TrackServices, build_services
from trackvault.infrastructure.gpx import GPX_MEDIA_TYPE, GpxDocumentWriter, GpxImporter

pytestmark = [pytest.mark.contract, pytest.mark.gpx]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"


@pytest.fixture
def services(settings: Settings) -> TrackServices:
    """Return a wired archive over a throwaway data directory."""
    built = build_services(settings)
    built.prepare_storage()
    return built


def _import(services: TrackServices, name: str) -> tuple[str, tuple[int, ...]]:
    """Import one fixture and return its content hash and track identities."""
    content = (FIXTURES / name).read_bytes()
    outcome = services.import_tracks(
        ImportRequest(
            content=content,
            original_filename=name,
            input_channel=InputChannel.LOCAL_FILE,
        )
    )
    assert outcome.status is ImportStatus.IMPORTED, outcome.error_code
    return outcome.sha256, outcome.track_ids


def _writer() -> GpxDocumentWriter:
    """Return the exchange writer under test."""
    return GpxDocumentWriter(generator="TrackVault test")


def _reimport(document: bytes) -> tuple[TrackSegment, ...]:
    """Parse an exported document back through the ordinary importer."""
    candidates = GpxImporter().import_tracks(document, ImportLimits())
    assert len(candidates) == 1
    return candidates[0].segments


# --- Type A: the original bytes -------------------------------------------


def test_a_raw_export_returns_the_bytes_that_arrived(services: TrackServices) -> None:
    """The contract: an exported raw source is byte-identical to what was imported."""
    original = (FIXTURES / "recorded-measurements.gpx").read_bytes()
    sha256, _ = _import(services, "recorded-measurements.gpx")

    export = ExportRawSource(repository=services.store, raw_store=services.raw_store)(sha256)

    assert export is not None
    assert export.content == original
    assert hashlib.sha256(export.content).hexdigest() == sha256


def test_a_raw_export_carries_the_filename_as_display_metadata(
    services: TrackServices,
) -> None:
    """The original name is offered for saving, and never used as a location."""
    sha256, _ = _import(services, "multiple-segments.gpx")

    export = ExportRawSource(repository=services.store, raw_store=services.raw_store)(sha256)

    assert export is not None
    assert export.original_filename == "multiple-segments.gpx"
    assert export.suggested_filename == "multiple-segments.gpx"


def test_a_raw_export_of_an_unknown_source_is_absent_rather_than_an_error(
    services: TrackServices,
) -> None:
    """A hash the archive never held is not a failure, it is nothing."""
    export = ExportRawSource(repository=services.store, raw_store=services.raw_store)("0" * 64)

    assert export is None


def test_a_raw_export_names_a_file_even_when_the_source_did_not(
    services: TrackServices,
) -> None:
    """A source that arrived unnamed still exports, under its content hash."""
    content = (FIXTURES / "ambiguous-minimal.gpx").read_bytes()
    outcome = services.import_tracks(
        ImportRequest(
            content=content, original_filename=None, input_channel=InputChannel.WEB_UPLOAD
        )
    )
    assert outcome.status is ImportStatus.IMPORTED

    export = ExportRawSource(repository=services.store, raw_store=services.raw_store)(
        outcome.sha256
    )

    assert export is not None
    assert export.original_filename is None
    assert export.suggested_filename.startswith(outcome.sha256[:12])


# --- Type B: a generated exchange document --------------------------------


def test_an_exported_document_is_valid_gpx_the_importer_reads_back() -> None:
    """The strongest available check: this build's own adapter accepts the output."""
    segments = (
        TrackSegment(
            points=(
                TrackPoint(latitude=52.5, longitude=13.4, elevation=34.0),
                TrackPoint(latitude=52.6, longitude=13.5, elevation=36.5),
            )
        ),
    )

    document = _writer().write(
        ExchangeDocument(title="A ride", activity=Activity.UNKNOWN, segments=segments)
    )

    assert _reimport(document) == segments


def test_an_exported_document_declares_gpx_1_1() -> None:
    """GPX 1.1 is what this writer produces, and it says so in the namespace."""
    document = _writer().write(
        ExchangeDocument(
            title=None,
            activity=Activity.UNKNOWN,
            segments=(TrackSegment(points=(TrackPoint(latitude=1.0, longitude=2.0),)),),
        )
    )

    assert b'version="1.1"' in document
    assert b"http://www.topografix.com/GPX/1/1" in document
    assert _writer().media_type == GPX_MEDIA_TYPE


def test_segment_boundaries_survive_an_export() -> None:
    """A paused recording is several segments, and joining them invents continuity."""
    segments = (
        TrackSegment(points=(TrackPoint(latitude=1.0, longitude=2.0),)),
        TrackSegment(
            points=(
                TrackPoint(latitude=3.0, longitude=4.0),
                TrackPoint(latitude=5.0, longitude=6.0),
            )
        ),
    )

    document = _writer().write(
        ExchangeDocument(title=None, activity=Activity.UNKNOWN, segments=segments)
    )

    assert document.count(b"<trkseg>") == 2
    assert _reimport(document) == segments


def test_timestamps_survive_an_export_exactly() -> None:
    """An instant is exported as the instant it is, in UTC, and reads back equal."""
    segments = (
        TrackSegment(
            points=(
                TrackPoint(
                    latitude=1.0,
                    longitude=2.0,
                    time=datetime(2024, 6, 11, 9, 14, 5, tzinfo=UTC),
                ),
            )
        ),
    )

    document = _writer().write(
        ExchangeDocument(title=None, activity=Activity.UNKNOWN, segments=segments)
    )

    assert _reimport(document) == segments


def test_a_missing_elevation_is_omitted_rather_than_invented() -> None:
    """No `<ele>0</ele>`: a position without an altitude did not record one."""
    segments = (
        TrackSegment(
            points=(
                TrackPoint(latitude=1.0, longitude=2.0),
                TrackPoint(latitude=3.0, longitude=4.0, elevation=12.5),
            )
        ),
    )

    document = _writer().write(
        ExchangeDocument(title=None, activity=Activity.UNKNOWN, segments=segments)
    )

    assert document.count(b"<ele>") == 1
    assert _reimport(document) == segments


def test_a_missing_timestamp_is_omitted_rather_than_invented() -> None:
    """A planned route carries no clock, and an export must not give it one."""
    segments = (TrackSegment(points=(TrackPoint(latitude=1.0, longitude=2.0),)),)

    document = _writer().write(
        ExchangeDocument(title=None, activity=Activity.UNKNOWN, segments=segments)
    )

    assert b"<time>" not in document
    assert _reimport(document) == segments


def test_sensor_readings_survive_an_export() -> None:
    """A measurement the archive holds is exported, in the schema it was read from.

    Not an invention: it is written into the same namespaced vocabulary the
    importer reads, so what goes out is what came in rather than a new claim.
    """
    segments = (
        TrackSegment(
            points=(TrackPoint(latitude=1.0, longitude=2.0, heart_rate_bpm=142, cadence_rpm=0),),
        ),
    )

    document = _writer().write(
        ExchangeDocument(title=None, activity=Activity.UNKNOWN, segments=segments)
    )

    assert _reimport(document) == segments


def test_a_classification_is_not_exported_as_an_invented_extension() -> None:
    """`RECORDED` has no representation in GPX, so nothing claims it does."""
    document = _writer().write(
        ExchangeDocument(
            title=None,
            activity=Activity.UNKNOWN,
            segments=(TrackSegment(points=(TrackPoint(latitude=1.0, longitude=2.0),)),),
        )
    )

    lowered = document.lower()
    assert b"recorded" not in lowered
    assert b"planned" not in lowered
    assert b"trackvault.org" not in lowered


def test_a_known_activity_is_exported_as_the_gpx_element_that_means_it() -> None:
    """`<trk><type>` is core GPX and is exactly what the importer reads it from."""
    document = _writer().write(
        ExchangeDocument(
            title=None,
            activity=Activity.CYCLING,
            segments=(TrackSegment(points=(TrackPoint(latitude=1.0, longitude=2.0),)),),
        )
    )

    assert b"<type>cycling</type>" in document


def test_an_unknown_activity_states_nothing_rather_than_the_word_unknown() -> None:
    """An absence, not a value another reader should adopt as an activity."""
    document = _writer().write(
        ExchangeDocument(
            title=None,
            activity=Activity.UNKNOWN,
            segments=(TrackSegment(points=(TrackPoint(latitude=1.0, longitude=2.0),)),),
        )
    )

    assert b"<type>" not in document


def test_a_title_with_markup_characters_is_escaped_not_injected() -> None:
    """A title is text. Writing it unescaped would let a name break the document."""
    segments = (TrackSegment(points=(TrackPoint(latitude=1.0, longitude=2.0),)),)

    document = _writer().write(
        ExchangeDocument(
            title="</name><trk>evil & co", activity=Activity.UNKNOWN, segments=segments
        )
    )

    candidates = GpxImporter().import_tracks(document, ImportLimits())
    assert len(candidates) == 1
    assert candidates[0].title == "</name><trk>evil & co"


def test_the_writer_is_deterministic() -> None:
    """The same track exports to the same bytes, so a diff means a real change."""
    document = ExchangeDocument(
        title="Ride",
        activity=Activity.CYCLING,
        segments=(
            TrackSegment(
                points=(
                    TrackPoint(
                        latitude=1.5,
                        longitude=2.5,
                        elevation=3.5,
                        time=datetime(2024, 1, 1, tzinfo=UTC),
                    ),
                )
            ),
        ),
    )

    assert _writer().write(document) == _writer().write(document)


# --- The use case that joins them -----------------------------------------


def test_exporting_a_track_uses_the_title_the_user_corrected(
    services: TrackServices,
) -> None:
    """Display authority is the user's title, and an export is a display."""
    _, track_ids = _import(services, "multiple-segments.gpx")
    services.store.set_user_metadata(
        track_ids[0], UserTrackMetadata(title="My own name"), datetime.now(UTC)
    )

    export = ExportTrackDocument(repository=services.store, writer=_writer())(track_ids[0])

    assert export is not None
    assert b"<name>My own name</name>" in export.content
    assert export.suggested_filename.endswith(".gpx")


def test_exporting_an_unknown_track_is_absent_rather_than_an_error(
    services: TrackServices,
) -> None:
    """A track that is not current has no document, and that is a normal answer."""
    export = ExportTrackDocument(repository=services.store, writer=_writer())(9999)

    assert export is None


def test_an_exported_track_round_trips_through_the_archive(
    services: TrackServices,
) -> None:
    """Export, re-import, and the archive holds the same geometry twice.

    The end-to-end statement the two halves above only imply: what leaves this
    archive can come back into it, unchanged, through the ordinary import path.
    """
    _, track_ids = _import(services, "recorded-measurements.gpx")
    original = services.store.get_geometry(track_ids[0])

    export = ExportTrackDocument(repository=services.store, writer=_writer())(track_ids[0])
    assert export is not None
    outcome = services.import_tracks(
        ImportRequest(
            content=export.content,
            original_filename=export.suggested_filename,
            input_channel=InputChannel.LOCAL_FILE,
        )
    )

    assert outcome.status is ImportStatus.IMPORTED
    assert services.store.get_geometry(outcome.track_ids[0]) == original
