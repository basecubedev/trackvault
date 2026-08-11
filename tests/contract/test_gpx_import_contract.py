"""Executable contracts for the GPX adapter.

GPX is an adapter, not the domain. These tests therefore check what the adapter
*produces* -- normalized tracks, provenance and neutral evidence -- and never that
some XML element survived the boundary.

Every fixture under ``tests/fixtures/gpx/`` is synthetic. No coordinate here comes
from a real recording.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from gpx_view.application import ImportErrorCode, ImportLimits, TrackImportError
from gpx_view.domain import Activity, EvidenceCode, ImportedTrack
from gpx_view.infrastructure.gpx import GpxImporter

pytestmark = [pytest.mark.contract, pytest.mark.gpx]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

E = EvidenceCode

GPX_1_1_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def read(name: str) -> bytes:
    """Return the bytes of a synthetic fixture."""
    return (FIXTURES / name).read_bytes()


def import_tracks(name: str, limits: ImportLimits | None = None) -> tuple[ImportedTrack, ...]:
    """Import a fixture through the GPX adapter."""
    return GpxImporter().import_tracks(read(name), limits or ImportLimits())


def expect_failure(name: str, limits: ImportLimits | None = None) -> TrackImportError:
    """Import a fixture that must fail, and return the raised error."""
    with pytest.raises(TrackImportError) as raised:
        import_tracks(name, limits)
    return raised.value


# --- Format detection is content-based --------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "recorded-measurements.gpx",
        "route-only.gpx",
        "gpx-1.0.gpx",
        "malformed.gpx",
        "unsafe-entity.gpx",
    ],
)
def test_a_document_claiming_to_be_gpx_is_recognised(name: str) -> None:
    """Detection reads the content, so even a broken GPX is recognised as GPX."""
    assert GpxImporter().detects(read(name))


@pytest.mark.parametrize("name", ["not-gpx.xml", "not-xml.txt"])
def test_other_content_is_not_claimed(name: str) -> None:
    """Well-formed XML that is not GPX, and non-XML input, are left alone."""
    assert not GpxImporter().detects(read(name))


def test_detection_never_looks_at_a_filename() -> None:
    """The filename is display metadata; only the bytes decide."""
    assert GpxImporter().detects(read("recorded-measurements.gpx"))
    assert not GpxImporter().detects(read("not-gpx.xml"))


def test_an_empty_document_is_not_claimed() -> None:
    """Nothing at all is not a GPX file."""
    assert not GpxImporter().detects(b"")


# --- The adapter identifies itself ------------------------------------------


def test_the_adapter_states_which_format_and_version_it_is() -> None:
    """Processing provenance needs a named, versioned importer."""
    importer = GpxImporter()

    assert importer.format_id == "gpx"
    assert importer.importer_version.strip()


# --- Core GPX fields --------------------------------------------------------


def test_a_recording_normalizes_geometry_provenance_and_activity() -> None:
    """The core `<trk>`, `<trkseg>`, `<trkpt>`, `<ele>`, `<time>` fields are read."""
    (track,) = import_tracks("recorded-measurements.gpx")

    assert track.title == "Synthetic morning walk"
    assert track.activity is Activity.WALKING
    assert track.segment_count == 1
    assert track.point_count == 4

    first = track.segments[0].points[0]
    assert first.latitude == pytest.approx(51.0)
    assert first.longitude == pytest.approx(7.0)
    assert first.elevation == pytest.approx(40.0)
    assert first.time == datetime(2026, 5, 4, 8, 0, tzinfo=UTC)

    assert track.source.exchange_format == "gpx"
    assert track.source.format_version == "1.1"
    assert track.source.creator == "SyntheticRecorder 1.0"


def test_a_document_export_time_is_not_an_activity_time() -> None:
    """`<metadata><time>` is when the file was written, not when anyone moved."""
    (track,) = import_tracks("recorded-measurements.gpx")
    times = [point.time for segment in track.segments for point in segment.points]

    assert min(t for t in times if t) == datetime(2026, 5, 4, 8, 0, tzinfo=UTC)
    assert max(t for t in times if t) == datetime(2026, 5, 4, 8, 0, 24, tzinfo=UTC)
    assert datetime(2026, 5, 4, 20, 0, tzinfo=UTC) not in times


def test_elevation_and_time_stay_optional() -> None:
    """A source that records neither is imported, not rejected."""
    (without_time,) = import_tracks("missing-timestamps.gpx")
    (without_elevation,) = import_tracks("missing-elevation.gpx")

    assert all(point.time is None for point in without_time.segments[0].points)
    assert all(point.elevation is not None for point in without_time.segments[0].points)
    assert all(point.elevation is None for point in without_elevation.segments[0].points)
    assert all(point.time is not None for point in without_elevation.segments[0].points)


def test_a_document_link_is_kept_as_provenance() -> None:
    """An external link is kept as provenance, whatever it turns out to mean.

    It decides no track kind, but discarding it would throw away the one pointer
    a reader has to where the file's own application says more about it.
    """
    (track,) = import_tracks("generic-external-link.gpx")

    assert track.source.external_links == ("https://example.test/application",)


# --- One file is not one track ----------------------------------------------


def test_several_track_elements_produce_several_tracks() -> None:
    """`RawImport -> exactly one NormalizedTrack` is not a valid assumption."""
    tracks = import_tracks("multiple-tracks.gpx")

    assert len(tracks) == 2
    assert [track.point_count for track in tracks] == [2, 3]
    assert [track.title for track in tracks] == [
        "Synthetic first track",
        "Synthetic second track",
    ]


def test_several_segments_of_one_track_stay_one_track() -> None:
    """A paused recording is one track with several segments, never several tracks."""
    (track,) = import_tracks("multiple-segments.gpx")

    assert track.segment_count == 3
    assert [segment.point_count for segment in track.segments] == [2, 1, 2]
    assert track.point_count == 5


def test_a_track_without_geometry_produces_no_candidate() -> None:
    """Zero tracks is a valid import result."""
    assert import_tracks("empty-track.gpx") == ()


def test_a_route_element_produces_a_track_candidate() -> None:
    """A planned route is a track candidate, marked as the structure it came from."""
    (track,) = import_tracks("route-only.gpx")

    assert track.title == "Synthetic planned route"
    assert track.point_count == 3
    assert E.ROUTE_ELEMENT_PRESENT in track.evidence
    assert E.TRACK_ELEMENT_PRESENT not in track.evidence


# --- GPX 1.0 ----------------------------------------------------------------


def test_gpx_1_0_is_supported_with_its_own_field_placement() -> None:
    """1.0 keeps metadata in the root and `<course>` directly on a point."""
    (track,) = import_tracks("gpx-1.0.gpx")

    assert track.source.format_version == "1.0"
    assert track.source.creator == "SyntheticLegacyRecorder"
    assert track.source.external_links == ("https://legacy.example.test/track/7",)
    assert track.point_count == 2
    assert E.COURSE_MEASUREMENTS_PRESENT in track.evidence
    assert E.GPS_ACCURACY_PRESENT in track.evidence


# --- Extensions -------------------------------------------------------------


def test_an_explicit_activity_extension_is_normalized() -> None:
    """Explicit activity metadata is normalized; it is not a vendor shortcut."""
    (track,) = import_tracks("activity-extension.gpx")

    assert track.activity is Activity.HIKING
    assert E.ACTIVITY_METADATA_PRESENT in track.evidence


def test_an_unknown_extension_does_not_fail_the_parse() -> None:
    """A file must not become unimportable because it carries data we ignore."""
    (track,) = import_tracks("unknown-extension.gpx")

    assert track.point_count == 2
    assert track.activity is Activity.UNKNOWN


def test_unknown_extensions_survive_only_as_a_namespace_summary() -> None:
    """The domain records *that* extra data existed, never the XML itself."""
    (track,) = import_tracks("unknown-extension.gpx")

    assert track.source.extension_namespaces == ("http://unknown.example.test/schema/v9",)
    assert GPX_1_1_NAMESPACE not in track.source.extension_namespaces
    assert "mood" not in repr(track)
    assert "contemplative" not in repr(track)


# --- Evidence the adapter may state -----------------------------------------


def test_measurement_metadata_is_reported_as_evidence() -> None:
    """Receiver quality and heading are observations, not conclusions."""
    (track,) = import_tracks("recorded-measurements.gpx")

    assert E.GPS_ACCURACY_PRESENT in track.evidence
    assert E.COURSE_MEASUREMENTS_PRESENT in track.evidence
    assert E.TIMESTAMPS_PRESENT in track.evidence
    assert E.TRACK_ELEMENT_PRESENT in track.evidence
    assert E.MEASUREMENT_METADATA_ABSENT not in track.evidence


def test_absent_measurement_metadata_is_reported_as_evidence() -> None:
    """An absence of measurement metadata is itself an observation."""
    (track,) = import_tracks("generic-external-link.gpx")

    assert E.MEASUREMENT_METADATA_ABSENT in track.evidence
    assert E.EXTERNAL_LINK_PRESENT in track.evidence
    assert E.GPS_ACCURACY_PRESENT not in track.evidence
    assert E.COURSE_MEASUREMENTS_PRESENT not in track.evidence


def test_navigation_instructions_are_reported_as_evidence() -> None:
    """Turn instructions live on waypoints, so the observation is document-wide."""
    (track,) = import_tracks("planned-route-instructions.gpx")

    assert E.ROUTE_INSTRUCTIONS_PRESENT in track.evidence


def test_a_document_without_instructions_does_not_claim_them() -> None:
    """The observation has to be absent when there is nothing to observe."""
    (track,) = import_tracks("recorded-measurements.gpx")

    assert E.ROUTE_INSTRUCTIONS_PRESENT not in track.evidence


def test_waypoints_do_not_become_tracks() -> None:
    """Navigation waypoints are evidence about a route, not geometry of their own."""
    tracks = import_tracks("planned-route-instructions.gpx")

    assert len(tracks) == 1
    assert tracks[0].point_count == 3


def test_missing_timestamps_are_reported_as_evidence() -> None:
    """Presence and absence of time data are both observations."""
    (with_time,) = import_tracks("ambiguous-minimal.gpx")
    (without_time,) = import_tracks("missing-timestamps.gpx")

    assert E.TIMESTAMPS_PRESENT in with_time.evidence
    assert E.TIMESTAMPS_ABSENT not in with_time.evidence
    assert E.TIMESTAMPS_ABSENT in without_time.evidence
    assert E.TIMESTAMPS_PRESENT not in without_time.evidence


def test_the_adapter_states_no_track_kind() -> None:
    """Adapters observe; the classifier decides. There is no kind to set here."""
    (track,) = import_tracks("recorded-measurements.gpx")

    assert not hasattr(track, "kind")
    assert not hasattr(track, "classification")


# --- Refusing broken and hostile input --------------------------------------


def test_an_impossible_coordinate_is_refused() -> None:
    """A latitude of 97 degrees is a defect, not a position."""
    assert expect_failure("invalid-coordinate.gpx").code is ImportErrorCode.INVALID_COORDINATE


def test_an_unreadable_timestamp_is_refused() -> None:
    """A time value that cannot be parsed is not silently dropped."""
    assert expect_failure("invalid-timestamp.gpx").code is ImportErrorCode.INVALID_TIMESTAMP


def test_a_timestamp_without_a_zone_is_refused() -> None:
    """An instant that depends on the reader's timezone is not an instant."""
    assert expect_failure("naive-timestamp.gpx").code is ImportErrorCode.INVALID_TIMESTAMP


@pytest.mark.parametrize(
    "name", ["missing-coordinate.gpx", "non-numeric-coordinate.gpx", "invalid-coordinate.gpx"]
)
def test_an_unusable_position_is_refused(name: str) -> None:
    """A missing, unreadable or impossible position is a defect, not data."""
    assert expect_failure(name).code is ImportErrorCode.INVALID_COORDINATE


@pytest.mark.parametrize("name", ["non-numeric-elevation.gpx", "non-finite-elevation.gpx"])
def test_an_unusable_elevation_is_refused(name: str) -> None:
    """`NaN` stored as a number would poison every later calculation silently."""
    assert expect_failure(name).code is ImportErrorCode.INVALID_GPX


def test_a_namespace_prefixed_root_is_recognised() -> None:
    """``<g:gpx xmlns:g="...">`` is the same element as ``<gpx xmlns="...">``.

    Namespace prefixes are a serialisation choice, not a different format. A
    detector that searches for the literal ``<gpx`` reads the markup instead of
    the document, and refuses a perfectly ordinary file as an unknown format.
    """
    assert GpxImporter().detects(read("prefixed-root.gpx"))


def test_a_namespace_prefixed_document_normalizes_like_any_other() -> None:
    """Recognising the root is worth nothing if the content then stays unread."""
    (candidate,) = import_tracks("prefixed-root.gpx")

    assert candidate.point_count == 2
    assert candidate.segment_count == 1
    assert candidate.title == "Synthetic track written with a namespace prefix"
    assert candidate.source.format_version == "1.1"


def test_a_generic_external_link_is_not_planning_evidence() -> None:
    """A ``<link>`` says where to read more, not that the geometry was computed.

    GPX defines ``<link>`` as an external reference. Applications write their own
    home page, a help page or a permalink into it, and a recorder does that just
    as readily as a planner. Treating any link as a planning signal turns "this
    file mentions a website" into a business verdict.
    """
    (candidate,) = import_tracks("generic-external-link.gpx")

    assert E.ROUTE_INSTRUCTIONS_PRESENT not in candidate.evidence
    assert E.ROUTE_ELEMENT_PRESENT not in candidate.evidence


def test_navigation_instructions_belong_only_to_the_candidate_that_carries_them() -> None:
    """Document evidence is not automatically candidate evidence.

    One file may hold a plain track and a computed route side by side. Searching
    the whole document once and handing the result to every candidate makes the
    route's turn instructions an observation about the track -- a statement the
    document never made.
    """
    track, route = import_tracks("track-beside-navigated-route.gpx")

    assert track.title == "Plain track"
    assert E.ROUTE_INSTRUCTIONS_PRESENT not in track.evidence, (
        "the route's instructions leaked into the track candidate"
    )
    assert route.title == "Computed route"
    assert E.ROUTE_INSTRUCTIONS_PRESENT in route.evidence


def test_the_gpx_namespace_alone_does_not_make_a_document_gpx() -> None:
    """The root element decides, not the namespaces the document happens to use.

    A document rooted in ``<trackList>`` is not a GPX document, however many GPX
    namespaces it declares, so this adapter does not claim it. Asked to parse it
    anyway -- the port is callable directly -- it still refuses with
    ``invalid_gpx`` rather than trusting that detection ran first.
    """
    assert not GpxImporter().detects(read("wrong-root-element.gpx"))
    assert expect_failure("wrong-root-element.gpx").code is ImportErrorCode.INVALID_GPX


def test_a_broken_document_is_still_claimed_so_it_can_be_called_broken() -> None:
    """Not-GPX-at-all and broken-GPX are different problems for whoever fixes the file.

    Detection stays lenient where it cannot read a root: a file that claims to be
    GPX and then breaks is reported as broken GPX, not filed away as an unknown
    format nobody will look at again.
    """
    assert GpxImporter().detects(read("malformed.gpx"))
    assert expect_failure("malformed.gpx").code is ImportErrorCode.INVALID_GPX
    assert GpxImporter().detects(read("unsafe-entity.gpx"))
    assert expect_failure("unsafe-entity.gpx").code is ImportErrorCode.UNSAFE_XML


def test_an_oversized_document_is_refused_before_it_is_parsed() -> None:
    """The byte limit is the adapter's first check, not an afterthought."""
    error = expect_failure("recorded-measurements.gpx", ImportLimits(max_bytes=32))

    assert error.code is ImportErrorCode.IMPORT_TOO_LARGE


def test_a_document_that_is_not_well_formed_is_refused() -> None:
    """Broken markup is reported as broken GPX, not guessed at."""
    assert expect_failure("malformed.gpx").code is ImportErrorCode.INVALID_GPX


@pytest.mark.parametrize("name", ["unsafe-entity.gpx", "unsafe-external-entity.gpx"])
def test_entity_and_dtd_tricks_are_refused_rather_than_expanded(name: str) -> None:
    """Imported XML is untrusted input, so the parser fails closed."""
    assert expect_failure(name).code is ImportErrorCode.UNSAFE_XML


def test_an_error_never_carries_coordinates_or_raw_markup() -> None:
    """Errors are diagnostics, not a channel for personal movement data."""
    for name in ("invalid-coordinate.gpx", "invalid-timestamp.gpx", "malformed.gpx"):
        message = str(expect_failure(name))

        assert "97.5" not in message
        assert "51.0000" not in message
        assert "<trkpt" not in message
        assert str(FIXTURES) not in message


# --- Import limits ----------------------------------------------------------


def test_too_many_tracks_is_refused() -> None:
    """A hostile file must not be able to allocate without bound."""
    error = expect_failure("multiple-tracks.gpx", ImportLimits(max_tracks=1))

    assert error.code is ImportErrorCode.TOO_MANY_TRACKS


def test_too_many_segments_in_one_track_is_refused() -> None:
    """Segment count is bounded too, not only the track count."""
    error = expect_failure("multiple-segments.gpx", ImportLimits(max_segments_per_track=2))

    assert error.code is ImportErrorCode.TOO_MANY_TRACK_SEGMENTS


def test_too_many_points_is_refused() -> None:
    """The point budget spans the whole file, not one track."""
    error = expect_failure("multiple-tracks.gpx", ImportLimits(max_points=3))

    assert error.code is ImportErrorCode.TOO_MANY_TRACK_POINTS


def test_the_default_limits_allow_a_long_recording() -> None:
    """Defaults must not get in the way of an ordinary multi-hour GPS log."""
    limits = ImportLimits()

    assert limits.max_points >= 100_000
    assert limits.max_bytes >= 8 * 1024 * 1024
    assert limits.max_segments_per_track >= 100
    assert limits.max_tracks >= 10


def test_limits_must_be_positive() -> None:
    """A zero limit would disable imports rather than protect them."""
    with pytest.raises(ValueError, match="max_points"):
        ImportLimits(max_points=0)


# --- Candidate identity -----------------------------------------------------


def test_candidates_are_numbered_within_their_container_type() -> None:
    """A `<trk>` keeps its identity when routes are added, removed or read.

    A single running index over all candidates would renumber every track after
    the first route the adapter learns to read, and a user correction follows the
    identity. Numbering per container type is what makes "the same candidate"
    survive a change to what else the document contains.
    """
    tracks = import_tracks("multiple-tracks.gpx")

    assert [track.source_key for track in tracks] == ["trk:0", "trk:1"]


def test_a_route_does_not_renumber_the_tracks_beside_it() -> None:
    """Adding route support must not rename an existing track candidate."""
    track, route = import_tracks("track-beside-navigated-route.gpx")

    assert track.source_key == "trk:0"
    assert route.source_key == "rte:0"


def test_the_same_document_always_yields_the_same_identities() -> None:
    """Identity is a property of the source, not of when it was read."""
    first = [track.source_key for track in import_tracks("multiple-tracks.gpx")]
    second = [track.source_key for track in import_tracks("multiple-tracks.gpx")]

    assert first == second


def test_a_candidate_without_geometry_does_not_consume_an_identity() -> None:
    """Identities come from the source structure, not from what survived it.

    A `<trk>` that yields no candidate still occupies its position in the
    document, so the track after it keeps the key it had before the empty one was
    written -- or before a later parser started skipping it.
    """
    (track,) = import_tracks("empty-track-before-a-real-one.gpx")

    assert track.source_key == "trk:1"
