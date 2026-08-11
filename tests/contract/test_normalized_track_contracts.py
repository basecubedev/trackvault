"""Executable contracts for the canonical normalized track.

Two properties matter more than the field list itself:

* the model is source-agnostic -- no exchange format and no source application
  becomes a field of it, so a future FIT importer terminates at the same boundary;
* the effective track kind has exactly one authority, the classification.
"""

import dataclasses
from dataclasses import MISSING
from datetime import UTC, datetime, timedelta

import pytest

from gpx_view.domain import (
    Activity,
    ClassificationResult,
    EvidenceCode,
    ImportedTrack,
    NormalizedTrack,
    SourceMetadata,
    TrackClassification,
    TrackKind,
    TrackPoint,
    TrackSegment,
)

START = datetime(2026, 5, 4, 8, 0, tzinfo=UTC)

# Neither an exchange format nor a source application may become a field name of
# the normalized model. `trk`/`trkpt` catch a GPX element name sneaking in.
FORMAT_AND_VENDOR_TERMS = (
    "gpx",
    "fit",
    "tcx",
    "kml",
    "geojson",
    "xml",
    "trkpt",
    "trkseg",
    "locus",
    "komoot",
    "garmin",
    "wahoo",
    "osmand",
    "gpslogger",
    "strava",
)

SOURCE_AGNOSTIC_TYPES = (
    TrackPoint,
    TrackSegment,
    SourceMetadata,
    ImportedTrack,
    NormalizedTrack,
)


def _segment(*offsets_in_minutes: int, latitude: float = 51.0) -> TrackSegment:
    """Build a segment whose points are timestamped at the given offsets."""
    return TrackSegment(
        points=tuple(
            TrackPoint(
                latitude=latitude + index / 1000,
                longitude=6.8,
                time=START + timedelta(minutes=offset),
            )
            for index, offset in enumerate(offsets_in_minutes)
        )
    )


def _source() -> SourceMetadata:
    """Build source metadata for a test scenario."""
    return SourceMetadata(exchange_format="gpx", format_version="1.1", creator="test-writer")


def _classification(kind: TrackKind, *evidence: EvidenceCode) -> TrackClassification:
    """Build a detected classification for a test scenario."""
    return TrackClassification(
        detected=ClassificationResult(
            kind=kind,
            confidence=0.9 if kind is not TrackKind.UNKNOWN else 0.0,
            method="test-classifier",
            method_version="1",
            evidence=tuple(code.value for code in evidence),
        )
    )


def _track(kind: TrackKind = TrackKind.RECORDED, **overrides: object) -> NormalizedTrack:
    """Build a normalized track for a test scenario."""
    values: dict[str, object] = {
        "segments": (_segment(0, 1, 2),),
        "source": _source(),
        "source_key": "container:0",
        "classification": _classification(kind, EvidenceCode.GPS_ACCURACY_PRESENT),
        "title": "Test track",
        "activity": Activity.WALKING,
    }
    values.update(overrides)
    return NormalizedTrack(**values)  # type: ignore[arg-type]


# --- Source-agnostic model --------------------------------------------------


@pytest.mark.contract
@pytest.mark.parametrize("model", SOURCE_AGNOSTIC_TYPES, ids=lambda model: model.__name__)
def test_the_normalized_model_has_no_format_or_vendor_fields(model: type) -> None:
    """`GPX != Track`: a format or source application never becomes a business field."""
    offenders = [
        field.name
        for field in dataclasses.fields(model)
        if any(term in field.name.lower() for term in FORMAT_AND_VENDOR_TERMS)
    ]

    assert not offenders, f"{model.__name__} exposes format-specific fields: {offenders}"


@pytest.mark.contract
def test_a_normalized_track_needs_nothing_but_geometry_provenance_and_a_verdict() -> None:
    """The mandatory field set stays format-independent, so FIT can reuse it."""
    required = {
        field.name
        for field in dataclasses.fields(NormalizedTrack)
        if field.default is MISSING and field.default_factory is MISSING
    }

    assert required == {"segments", "source", "source_key", "classification"}


@pytest.mark.contract
def test_source_metadata_is_evidence_and_carries_no_business_verdict() -> None:
    """Provenance is preserved for explanation, never as an authority field."""
    field_names = {field.name for field in dataclasses.fields(SourceMetadata)}

    assert "kind" not in field_names
    assert not {name for name in field_names if name.endswith("kind")}


# --- One authority for the effective kind -----------------------------------


@pytest.mark.contract
def test_a_normalized_track_holds_no_second_kind_value() -> None:
    """A separately storable kind would be a second authority that can disagree."""
    field_names = {field.name for field in dataclasses.fields(NormalizedTrack)}

    assert not {name for name in field_names if name == "kind" or name.endswith("_kind")}


@pytest.mark.contract
def test_the_effective_kind_is_a_projection_of_the_classification() -> None:
    """The track answers with what the classification says, never with its own value."""
    track = _track(TrackKind.PLANNED)

    assert track.effective_kind is TrackKind.PLANNED
    assert track.effective_kind is track.classification.effective_kind


@pytest.mark.contract
def test_a_user_override_changes_the_tracks_effective_kind() -> None:
    """Correcting the classification is the only way to change the effective kind."""
    track = _track(TrackKind.PLANNED)

    corrected = track.with_classification(track.classification.overridden_with(TrackKind.RECORDED))

    assert corrected.detected_kind is TrackKind.PLANNED
    assert corrected.effective_kind is TrackKind.RECORDED
    assert corrected.contributes_to_actual_totals


@pytest.mark.contract
def test_the_effective_kind_cannot_be_assigned_directly() -> None:
    """There is no write path that bypasses the classification."""
    track = _track(TrackKind.RECORDED)

    with pytest.raises((AttributeError, TypeError)):
        track.effective_kind = TrackKind.PLANNED  # type: ignore[misc]

    assert track.effective_kind is TrackKind.RECORDED


@pytest.mark.contract
def test_a_normalized_track_cannot_be_edited_in_place() -> None:
    """Updating a track means deriving a new value, never mutating the old one."""
    track = _track()

    with pytest.raises(dataclasses.FrozenInstanceError):
        track.title = "renamed"  # type: ignore[misc]


# --- Segments are not flattened ---------------------------------------------


@pytest.mark.contract
def test_segment_boundaries_survive_normalization() -> None:
    """Several segments of one track stay several segments."""
    first = _segment(0, 1, 2)
    second = _segment(30, 31, latitude=51.5)

    track = _track(segments=(first, second))

    assert track.segment_count == 2
    assert track.point_count == 5
    assert track.segments == (first, second)


@pytest.mark.contract
def test_a_track_reports_the_extent_of_the_instants_across_all_segments() -> None:
    """A pause between segments belongs to the track's temporal extent."""
    track = _track(segments=(_segment(0, 1), _segment(30, 45, latitude=51.5)))

    assert track.started_at == START
    assert track.ended_at == START + timedelta(minutes=45)


@pytest.mark.contract
def test_a_track_without_any_segment_is_rejected() -> None:
    """A track with no geometry is not a track; an importer yields nothing instead."""
    with pytest.raises(ValueError, match="at least one segment"):
        _track(segments=())


# --- Importer output boundary -----------------------------------------------


@pytest.mark.contract
def test_an_importer_produces_candidates_without_deciding_a_kind() -> None:
    """Adapters normalize and observe evidence; the classifier decides."""
    field_names = {field.name for field in dataclasses.fields(ImportedTrack)}

    assert "classification" not in field_names
    assert not {name for name in field_names if name == "kind" or name.endswith("_kind")}
    assert "evidence" in field_names


@pytest.mark.contract
def test_classifying_a_candidate_yields_the_canonical_track_unchanged() -> None:
    """Classification attaches a verdict; it does not reshape the geometry."""
    candidate = ImportedTrack(
        segments=(_segment(0, 1, 2), _segment(9, 10, latitude=51.5)),
        source=_source(),
        source_key="container:0",
        evidence=(EvidenceCode.TIMESTAMPS_PRESENT, EvidenceCode.GPS_ACCURACY_PRESENT),
        title="Candidate",
        activity=Activity.HIKING,
    )

    track = candidate.classified_as(
        _classification(TrackKind.RECORDED, EvidenceCode.TIMESTAMPS_PRESENT)
    )

    assert track.segments == candidate.segments
    assert track.source == candidate.source
    assert track.source_key == candidate.source_key
    assert track.title == "Candidate"
    assert track.activity is Activity.HIKING
    assert track.effective_kind is TrackKind.RECORDED
