"""Writing a normalized track back out as GPX 1.1.

The other half of the adapter boundary. The importer turns GPX into normalized
tracks; this turns a normalized track into GPX, and neither lets an XML type
travel inwards.

What is written is deliberately a subset:

```
<trk><name>          the displayed title, when there is one
<trk><type>          the activity, when it is one we know
<trkseg>             one per segment, boundaries intact
<trkpt lat lon>      every position, in source order
<ele>                only where an altitude was recorded
<time>                only where an instant was recorded
TrackPointExtension  only where a sensor reading was recorded
```

**Nothing absent is invented.** A position without an altitude gets no ``<ele>``;
a planned route gets no ``<time>``. A zero would be a measurement claim, and this
project refuses that conflation everywhere else.

**No verdict is exported.** ``RECORDED`` and ``PLANNED`` are conclusions this
project reaches from its own evidence rules, at a confidence, with a classifier
version. GPX has no element that means any of that, and inventing a private one
would publish a claim no reader could evaluate. A track's kind therefore does not
appear in an exported document at all -- the archive keeps it, the exchange
format does not carry it.

Sensor readings are the deliberate exception, and they are not an exception to
the rule above: heart rate and cadence are *measurements* that arrived in a
defined, namespaced vocabulary, and they are written back into that same
vocabulary rather than a new one. What goes out is what came in. The namespace
comes from :mod:`gpx_view.infrastructure.gpx.extensions`, which stays the one
place that says what it means.
"""

from collections.abc import Iterable
from xml.etree.ElementTree import Element, SubElement, tostring

from gpx_view.application.export import ExchangeDocument
from gpx_view.domain import Activity, TrackPoint, TrackSegment
from gpx_view.infrastructure.gpx.extensions import GARMIN_TRACK_POINT_EXTENSION_V2
from gpx_view.infrastructure.gpx.importer import GPX_MEDIA_TYPE
from gpx_view.infrastructure.gpx.parsing import GPX_1_1_NAMESPACE

GPX_FILE_EXTENSION = ".gpx"
GPX_EXPORT_VERSION = "1"
"""What this writer produces, so a changed output is a stated change.

Bumped when the *document* changes -- another element written, a value spelled
differently. Not bumped for an internal tidy-up that produces identical bytes.
"""

_SCHEMA_INSTANCE_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
_GPX_SCHEMA_LOCATION = "http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd"

_TRACK_POINT_EXTENSION_PREFIX = "gpxtpx"
_TRACK_POINT_EXTENSION_ELEMENT = "TrackPointExtension"
_HEART_RATE_ELEMENT = "hr"
_CADENCE_ELEMENT = "cad"

_XML_DECLARATION = b"<?xml version='1.0' encoding='UTF-8'?>\n"


class GpxDocumentWriter:
    """Renders a normalized track as a GPX 1.1 document.

    Deterministic by construction: the same track produces the same bytes, so a
    difference between two exports is a difference in the track rather than in
    the day it was exported. Nothing here reads a clock -- a ``<metadata><time>``
    would be the export's own timestamp, which says nothing about the activity
    and would make every export differ from every other.
    """

    def __init__(self, generator: str) -> None:
        """Name what produced the document, for the ``creator`` attribute.

        GPX requires ``creator``, and it means "the application that wrote this
        file". Stating the release is what makes an exported document traceable
        back to the rules that produced it.
        """
        self._generator = generator

    @property
    def media_type(self) -> str:
        """Return the media type of a GPX document."""
        return GPX_MEDIA_TYPE

    @property
    def file_extension(self) -> str:
        """Return the filename suffix a GPX document carries."""
        return GPX_FILE_EXTENSION

    @property
    def format_version(self) -> str:
        """Return the exchange format version this writer produces."""
        return "1.1"

    def write(self, document: ExchangeDocument) -> bytes:
        """Render the document as GPX 1.1 bytes."""
        root = Element(
            "gpx",
            {
                "version": "1.1",
                "creator": self._generator,
                "xmlns": GPX_1_1_NAMESPACE,
                "xmlns:xsi": _SCHEMA_INSTANCE_NAMESPACE,
                "xsi:schemaLocation": _GPX_SCHEMA_LOCATION,
                f"xmlns:{_TRACK_POINT_EXTENSION_PREFIX}": GARMIN_TRACK_POINT_EXTENSION_V2,
            },
        )
        track = SubElement(root, "trk")
        if document.title:
            SubElement(track, "name").text = document.title
        # `UNKNOWN` is written as nothing. It is this project's honest answer to a
        # question the source never answered, and writing the word would hand
        # another application a value to act on.
        if document.activity is not Activity.UNKNOWN:
            SubElement(track, "type").text = document.activity.value
        for segment in document.segments:
            _write_segment(SubElement(track, "trkseg"), segment)
        rendered: bytes = tostring(root, encoding="utf-8", xml_declaration=False)
        return _XML_DECLARATION + rendered


def _write_segment(element: Element, segment: TrackSegment) -> None:
    """Write one segment's positions in the order the source listed them."""
    for point in segment.points:
        _write_point(SubElement(element, "trkpt", _coordinates(point)), point)


def _coordinates(point: TrackPoint) -> dict[str, str]:
    """Return the position attributes, in the shortest form that round-trips.

    ``repr`` of a float is the shortest string that reads back as the same
    double, which is what keeps an export followed by an import an identity
    rather than a slow drift towards a different coordinate.
    """
    return {"lat": repr(point.latitude), "lon": repr(point.longitude)}


def _write_point(element: Element, point: TrackPoint) -> None:
    """Write the optional values a position actually carries, and no others."""
    if point.elevation is not None:
        SubElement(element, "ele").text = repr(point.elevation)
    if point.time is not None:
        # Stored instants are UTC, and `Z` is how GPX spells that. The importer
        # reads it back through `datetime.fromisoformat`, which accepts it.
        SubElement(element, "time").text = point.time.isoformat().replace("+00:00", "Z")
    _write_sensor_readings(element, point)


def _write_sensor_readings(element: Element, point: TrackPoint) -> None:
    """Write the measurements a sensor reported here, if any did.

    A reading of zero is a reading -- a cadence sensor on a coasting bike reports
    one -- so the test is against ``None`` and never against falsiness.
    """
    readings = tuple(
        (name, value)
        for name, value in (
            (_HEART_RATE_ELEMENT, point.heart_rate_bpm),
            (_CADENCE_ELEMENT, point.cadence_rpm),
        )
        if value is not None
    )
    if not readings:
        return
    extension = SubElement(
        SubElement(element, "extensions"),
        f"{_TRACK_POINT_EXTENSION_PREFIX}:{_TRACK_POINT_EXTENSION_ELEMENT}",
    )
    _write_readings(extension, readings)


def _write_readings(extension: Element, readings: Iterable[tuple[str, int]]) -> None:
    """Write each reading as its own namespaced element."""
    for name, value in readings:
        SubElement(extension, f"{_TRACK_POINT_EXTENSION_PREFIX}:{name}").text = str(value)
