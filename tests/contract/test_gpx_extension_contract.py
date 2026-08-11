"""Executable contract for namespace-aware GPX extension semantics.

GPX lets any document carry any element from any namespace. An element name on
its own therefore says nothing: ``course`` in a track-point schema is a measured
heading, and ``course`` in a golf application's namespace is a golf course. The
adapter used to match local names in any namespace, so a foreign document could
manufacture measurement evidence -- and measurement evidence is what decides
`RECORDED`.

The rule this file pins:

```
known (namespace, element)   -> business meaning
unknown namespace            -> metadata, and nothing else
```

Every fixture here is synthetic. The namespace URIs are schema identifiers, not
personal data, and the ones this project claims to understand are only the ones
it has evidence for -- see ``docs/technical/architecture.md``.
"""

from pathlib import Path

import pytest

from gpx_view.application import ImportLimits
from gpx_view.domain import Activity, EvidenceCode, ImportedTrack
from gpx_view.infrastructure.gpx import GpxImporter

pytestmark = [pytest.mark.contract, pytest.mark.gpx]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"

E = EvidenceCode


def import_tracks(name: str) -> tuple[ImportedTrack, ...]:
    """Import a synthetic fixture through the GPX adapter."""
    return GpxImporter().import_tracks((FIXTURES / name).read_bytes(), ImportLimits())


# --- Receiver quality is a core GPX field ------------------------------------


def test_receiver_quality_is_read_from_the_documents_own_gpx_namespace() -> None:
    """`<hdop>` is defined by GPX itself, and that is where it is read."""
    (track,) = import_tracks("recorded-measurements.gpx")

    assert E.GPS_ACCURACY_PRESENT in track.evidence


def test_a_foreign_element_named_hdop_is_not_receiver_quality() -> None:
    """The GPX schema defines these names; an extension namespace does not.

    A document that happens to use the words ``hdop``, ``sat`` or ``fix`` in its
    own vocabulary has not told us how well a receiver measured anything.
    """
    (track,) = import_tracks("unknown-namespace-measurements.gpx")

    assert E.GPS_ACCURACY_PRESENT not in track.evidence


# --- A measured heading comes from a schema that defines one ------------------


def test_a_known_track_point_schema_states_a_measured_heading() -> None:
    """The track-point extension a real recorder writes still means what it meant."""
    (track,) = import_tracks("recorded-measurements.gpx")

    assert E.COURSE_MEASUREMENTS_PRESENT in track.evidence


def test_gpx_1_0_reads_its_own_course_element() -> None:
    """GPX 1.0 puts the heading directly on the point, and that is core GPX."""
    (track,) = import_tracks("gpx-1.0.gpx")

    assert E.COURSE_MEASUREMENTS_PRESENT in track.evidence


def test_a_foreign_element_named_course_is_not_a_measurement() -> None:
    """An unrelated namespace cannot manufacture the evidence that decides RECORDED."""
    (track,) = import_tracks("unknown-namespace-measurements.gpx")

    assert E.COURSE_MEASUREMENTS_PRESENT not in track.evidence
    assert E.MEASUREMENT_METADATA_ABSENT in track.evidence


def test_an_unregistered_track_point_schema_states_nothing() -> None:
    """Looking like a known vendor is not being one.

    Only the schema versions this project has evidence for are interpreted. A
    namespace that merely shares a prefix with them is unknown data.
    """
    (track,) = import_tracks("unregistered-trackpoint-schema.gpx")

    assert E.COURSE_MEASUREMENTS_PRESENT not in track.evidence
    assert E.MEASUREMENT_METADATA_ABSENT in track.evidence


# --- An explicit activity ------------------------------------------------------


def test_a_known_activity_extension_is_normalized() -> None:
    """The source stated an activity in a schema we read, so the activity is known."""
    (track,) = import_tracks("activity-extension.gpx")

    assert track.activity is Activity.HIKING
    assert E.ACTIVITY_METADATA_PRESENT in track.evidence


def test_the_core_gpx_type_element_still_states_an_activity() -> None:
    """`<trk><type>` is GPX's own field and needs no extension schema."""
    (track,) = import_tracks("recorded-measurements.gpx")

    assert track.activity is Activity.WALKING
    assert E.ACTIVITY_METADATA_PRESENT in track.evidence


def test_a_foreign_activity_element_is_not_an_activity() -> None:
    """An unknown schema's ``activity`` is a word, not a statement about movement."""
    (track,) = import_tracks("unknown-namespace-activity.gpx")

    assert track.activity is Activity.UNKNOWN
    assert E.ACTIVITY_METADATA_PRESENT not in track.evidence


# --- Navigation instructions ---------------------------------------------------


def test_a_known_navigation_extension_is_planning_evidence() -> None:
    """Turn-by-turn instructions exist because a route was computed for them."""
    (track,) = import_tracks("planned-route-instructions.gpx")

    assert E.ROUTE_INSTRUCTIONS_PRESENT in track.evidence


def test_a_foreign_element_with_the_same_name_is_not_planning_evidence() -> None:
    """Planning evidence is a strong signal, so it needs a schema behind it."""
    (track,) = import_tracks("unknown-namespace-instructions.gpx")

    assert E.ROUTE_INSTRUCTIONS_PRESENT not in track.evidence


def test_navigation_evidence_still_belongs_to_the_candidate_that_carries_it() -> None:
    """Namespace awareness must not cost the locality that was fixed before it."""
    track, route = import_tracks("track-beside-navigated-route.gpx")

    assert E.ROUTE_INSTRUCTIONS_PRESENT not in track.evidence
    assert E.ROUTE_INSTRUCTIONS_PRESENT in route.evidence


# --- Unknown extensions stay data ---------------------------------------------


def test_an_unknown_extension_still_parses() -> None:
    """A file must not become unimportable because it carries data we ignore."""
    (track,) = import_tracks("unknown-extension.gpx")

    assert track.point_count == 2


@pytest.mark.parametrize(
    ("name", "namespace"),
    [
        ("unknown-extension.gpx", "http://unknown.example.test/schema/v9"),
        ("unknown-namespace-activity.gpx", "urn:unrelated"),
        ("unknown-namespace-measurements.gpx", "urn:unrelated"),
    ],
)
def test_an_unknown_namespace_survives_as_metadata_only(name: str, namespace: str) -> None:
    """What an unknown extension leaves behind is the fact that it was there.

    The namespace summary is provenance: it lets an operator see that a document
    carried data this build ignored, without any of that data reaching the domain
    model or any business rule.
    """
    (track,) = import_tracks(name)

    assert namespace in track.source.extension_namespaces


@pytest.mark.contract
class TestSensorReadings:
    """What a body did, read only from a schema that defines it.

    `hr` and `cad` are two- and three-letter names any vocabulary may use. The
    namespace decides, exactly as it does for a heading -- reading a heart rate
    out of an element because it is spelled `hr` is the blind matching the
    extension table exists to prevent.
    """

    def test_a_recording_carries_the_readings_its_sensors_made(self) -> None:
        """The point of the whole thing."""
        (track,) = import_tracks("sensor-readings.gpx")

        readings = [point.heart_rate_bpm for point in track.segments[0].points]
        assert readings == [112, 128, None, 141]

    def test_a_missing_reading_is_absent_rather_than_zero(self) -> None:
        """A strap losing contact did not measure a heart rate of nothing."""
        (track,) = import_tracks("sensor-readings.gpx")

        assert track.segments[0].points[2].heart_rate_bpm is None

    def test_a_reading_of_zero_survives_as_a_reading(self) -> None:
        """A coasting bike reports a cadence of zero, and that is a fact about it."""
        (track,) = import_tracks("sensor-readings.gpx")

        assert track.segments[0].points[3].cadence_rpm == 0

    def test_an_element_from_an_unknown_schema_is_not_a_measurement(self) -> None:
        """`hr` in somebody else's vocabulary is not a heart rate."""
        document = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:other="https://example.invalid/hospital-rooms">
  <trk><trkseg>
    <trkpt lat="51.0" lon="7.0"><time>2026-05-04T08:00:00Z</time>
      <extensions><other:hr>7</other:hr></extensions></trkpt>
  </trkseg></trk>
</gpx>
"""
        (track,) = GpxImporter().import_tracks(document, ImportLimits())

        assert track.segments[0].points[0].heart_rate_bpm is None

    def test_an_unreadable_reading_costs_the_reading_and_nothing_else(self) -> None:
        """A sensor writing nonsense said nothing; the position is still a position."""
        document = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="synthetic" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2">
  <trk><trkseg>
    <trkpt lat="51.0" lon="7.0"><time>2026-05-04T08:00:00Z</time>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>n/a</gpxtpx:hr>
      </gpxtpx:TrackPointExtension></extensions></trkpt>
  </trkseg></trk>
</gpx>
"""
        (track,) = GpxImporter().import_tracks(document, ImportLimits())

        assert track.segments[0].points[0].heart_rate_bpm is None
        assert track.segments[0].points[0].latitude == pytest.approx(51.0)
