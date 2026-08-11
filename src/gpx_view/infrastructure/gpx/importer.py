"""The GPX adapter: untrusted GPX bytes in, normalized track candidates out.

Supported versions are GPX 1.1 and GPX 1.0, told apart by the namespace of the
root element rather than by a filename or a version attribute. What the adapter
reads is the common core -- ``<trk>``, ``<trk><name>``, ``<trkseg>``,
``<trkpt lat lon>``, ``<ele>``, ``<time>``, links, ``creator`` and the format
version -- plus ``<rte>`` as a track candidate of its own.

Extensions are read only where there is a purpose today: an explicit activity, a
measured heading, and the receiver-quality values GPX itself defines. They are
matched on the *namespaced* name -- see
:mod:`gpx_view.infrastructure.gpx.extensions` -- because a local name on its own
is a word rather than a schema. Everything else survives as a namespace summary,
because the raw import keeps the original bytes and can be reprocessed once an
extension gains a meaning.

The adapter states **no** track kind. It observes evidence; the classifier
decides.

Each candidate carries a ``source_key`` that says which candidate of the document
it is: ``trk:0``, ``rte:0``. It is numbered per container type on purpose, so
that a document's tracks keep their identity when routes are added, removed or
start being read at all. Outside this adapter the value is opaque -- nothing
parses it, orders by it, or reads a format out of it.
"""

import math
import re
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from xml.etree.ElementTree import Element

from gpx_view.application import ImportErrorCode, ImportLimits, TrackImportError
from gpx_view.domain import (
    EvidenceCode,
    ImportedTrack,
    SourceMetadata,
    TrackPoint,
    TrackSegment,
)
from gpx_view.infrastructure.gpx.activities import normalize_activity, states_an_activity
from gpx_view.infrastructure.gpx.extensions import (
    ACTIVITY_ELEMENTS,
    CADENCE_ELEMENTS,
    COURSE_ELEMENTS,
    HEART_RATE_ELEMENTS,
    NAVIGATION_ELEMENTS,
    RECEIVER_QUALITY_ELEMENTS,
)
from gpx_view.infrastructure.gpx.parsing import (
    ROOT_TAG,
    SUPPORTED_NAMESPACES,
    child_text,
    children,
    descendants_in,
    first_child,
    local_name,
    local_name_of_tag,
    namespace_of,
    namespace_of_tag,
    parse_document,
    root_element_name,
    used_namespaces,
)

GPX_FORMAT_ID = "gpx"
GPX_MEDIA_TYPE = "application/gpx+xml"
GPX_IMPORTER_VERSION = "2"
"""What this adapter turns a document into.

Version 2 differs from version 1 in what it produces, not only in how. Since
version 1 the adapter gained the ``source_key`` candidate identity, moved
evidence onto the candidate it was observed on instead of the document, replaced
``source_link_present`` with a neutral external-link observation, started reading
its root element structurally rather than from a fixed-size prefix, and learned
to tell a known extension schema from an element that merely shares its name.

Every one of those changes the candidates, the identities or the evidence a
source yields, so a run must not be able to claim version 1 for output version 1
could not have produced. That is what `--outdated` reads.
"""

# The fallback for content whose root element cannot be read: a `gpx` element
# start, with or without a namespace prefix. It only ever runs when structural
# identification already failed, and it only decides whether this adapter is the
# one that should report the problem.
_GPX_ROOT_PATTERN = re.compile(r"<(?:[^\W\d][\w.-]*:)?gpx[\s/>]")

TRACK_ELEMENT = "trk"
ROUTE_ELEMENT = "rte"
SEGMENT_ELEMENT = "trkseg"
TRACK_POINT_ELEMENT = "trkpt"
ROUTE_POINT_ELEMENT = "rtept"


class GpxImporter:
    """Normalizes GPX 1.1 and GPX 1.0 documents into track candidates."""

    @property
    def format_id(self) -> str:
        """Return the format identifier recorded on every imported track."""
        return GPX_FORMAT_ID

    @property
    def importer_version(self) -> str:
        """Return the adapter version recorded with every processing run."""
        return GPX_IMPORTER_VERSION

    @property
    def media_type(self) -> str:
        """Return the media type hint recorded on an accepted raw import."""
        return GPX_MEDIA_TYPE

    def detects(self, content: bytes) -> bool:
        """Report whether the content is a GPX document.

        The decision is made from the content and never from a filename. What
        identifies a GPX document is the namespaced name of its root element,
        ``{http://www.topografix.com/GPX/1/1}gpx`` or its 1.0 equivalent.
        Reading it structurally means a namespace prefix changes nothing --
        ``<g:gpx xmlns:g="...">`` is the same element as ``<gpx xmlns="...">`` --
        and that the document's own declaration decides how its bytes are read.

        The root is parsed for rather than searched for in a fixed-size prefix.
        An XML document may put a declaration, processing instructions, comments
        and whitespace of any length in front of its root, and a licence header
        four kilobytes long does not make a file a different format. Parsing
        stops at the first start tag, and the input is already bounded by the
        import byte limit, so no size assumption of its own is needed.

        A document whose root cannot be identified at all falls back to a
        deliberately lenient look at the bytes. That keeps the useful distinction
        between "not GPX" and "broken GPX": a file that breaks before its root
        element, or that declares entities, is still claimed here so the parser
        can say *what* is wrong with it. Deciding that is the parser's job, not
        the detector's -- see ``ImportErrorCode``.
        """
        root = root_element_name(content)
        if root is None:
            return _claims_to_be_gpx(content)
        return (
            local_name_of_tag(root) == ROOT_TAG and namespace_of_tag(root) in SUPPORTED_NAMESPACES
        )

    def import_tracks(self, content: bytes, limits: ImportLimits) -> tuple[ImportedTrack, ...]:
        """Normalize a GPX document into track candidates.

        Args:
            content: The raw document bytes.
            limits: The safety limits this import runs under.

        Returns:
            The track candidates in document order. Possibly none: a document
            whose tracks carry no geometry yields nothing.

        Raises:
            TrackImportError: If the document is too large, unsafe, not readable
                as GPX, or exceeds a structural limit.
        """
        if len(content) > limits.max_bytes:
            raise TrackImportError(ImportErrorCode.IMPORT_TOO_LARGE, "input exceeds the byte limit")

        root = parse_document(content)
        namespace = namespace_of(root)
        if local_name(root) != ROOT_TAG or namespace not in SUPPORTED_NAMESPACES:
            raise TrackImportError(
                ImportErrorCode.INVALID_GPX, "root element is not a supported gpx element"
            )

        return _DocumentReader(root, namespace, limits).read()


class _DocumentReader:
    """Walks one GPX document once, enforcing the limits as it goes."""

    def __init__(self, root: Element, namespace: str, limits: ImportLimits) -> None:
        """Prepare the reader for one document."""
        self._root = root
        self._namespace = namespace
        self._limits = limits
        self._points_seen = 0
        self._version = SUPPORTED_NAMESPACES[namespace]
        self._legacy = self._version == "1.0"
        self._document_links = tuple(_links(self._document_metadata(), namespace, self._legacy))
        self._extension_namespaces = tuple(used_namespaces(root) - {namespace})

    def _document_metadata(self) -> Element:
        """Return the element that carries the document-level metadata.

        GPX 1.1 groups it into ``<metadata>``; GPX 1.0 places the same fields
        directly below the root.
        """
        if self._legacy:
            return self._root
        metadata = first_child(self._root, "metadata", self._namespace)
        return self._root if metadata is None else metadata

    def read(self) -> tuple[ImportedTrack, ...]:
        """Return every track candidate in the document, in document order."""
        containers = [
            element
            for element in self._root
            if local_name(element) in (TRACK_ELEMENT, ROUTE_ELEMENT)
            and namespace_of(element) == self._namespace
        ]
        if len(containers) > self._limits.max_tracks:
            raise TrackImportError(
                ImportErrorCode.TOO_MANY_TRACKS, f"limit is {self._limits.max_tracks}"
            )

        inherits = self._document_instructions_belong_to(containers)
        seen = Counter[str]()
        candidates = []
        for element in containers:
            container = local_name(element)
            # Numbered per container type, so a document's `<trk>` candidates keep
            # their identity when `<rte>` elements are added, removed, or start
            # being read at all. A single running index would renumber every
            # candidate after the first change.
            source_key = f"{container}:{seen[container]}"
            seen[container] += 1
            candidate = self._read_container(
                element,
                source_key=source_key,
                is_route=container == ROUTE_ELEMENT,
                inherits_document_instructions=inherits,
            )
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)

    def _document_instructions_belong_to(self, containers: list[Element]) -> bool:
        """Report whether instructions outside every candidate describe the only one.

        GPX writers put turn-by-turn instructions on waypoints, which sit beside
        the candidates rather than inside them. With exactly one candidate in the
        document there is nothing else they could describe, so attributing them is
        justified. With several, the document never says which one they belong to,
        and guessing would make one candidate's evidence into another's.
        """
        return len(containers) == 1 and self._has_instructions_outside(containers)

    def _has_instructions_outside(self, containers: list[Element]) -> bool:
        """Report whether navigation instructions sit outside every candidate."""
        return any(
            _states(element, NAVIGATION_ELEMENTS)
            for element in self._root
            if element not in containers
        )

    def _read_container(
        self,
        element: Element,
        *,
        source_key: str,
        is_route: bool,
        inherits_document_instructions: bool,
    ) -> ImportedTrack | None:
        """Return the candidate a ``<trk>`` or ``<rte>`` yields, if it has geometry."""
        segments, measured = self._read_segments(element, is_route=is_route)
        if not segments:
            return None

        activity_candidates = self._activity_candidates(element)
        links = self._document_links + tuple(_links(element, self._namespace, self._legacy))
        has_instructions = inherits_document_instructions or _states(element, NAVIGATION_ELEMENTS)

        return ImportedTrack(
            segments=segments,
            source_key=source_key,
            source=SourceMetadata(
                exchange_format=GPX_FORMAT_ID,
                format_version=self._version,
                creator=self._root.get("creator"),
                external_links=_deduplicated(links),
                extension_namespaces=self._extension_namespaces,
            ),
            evidence=_evidence(
                segments=segments,
                measured=measured,
                is_route=is_route,
                has_links=bool(links),
                has_instructions=has_instructions,
                states_activity=states_an_activity(*activity_candidates),
            ),
            title=child_text(element, "name", self._namespace),
            activity=normalize_activity(*activity_candidates),
        )

    def _activity_candidates(self, element: Element) -> tuple[str | None, ...]:
        """Return the activity strings the source stated, most trusted first.

        GPX's own ``<trk><type>`` first, then an ``activity`` element from an
        extension schema this adapter reads. An ``activity`` element from an
        unknown namespace is a word in someone else's vocabulary and is left
        alone.
        """
        extensions = first_child(element, "extensions", self._namespace)
        from_extension = None
        if extensions is not None:
            node = next(descendants_in(extensions, ACTIVITY_ELEMENTS), None)
            from_extension = node.text.strip() if node is not None and node.text else None
        return (child_text(element, "type", self._namespace), from_extension)

    def _read_segments(
        self, element: Element, *, is_route: bool
    ) -> tuple[tuple[TrackSegment, ...], "_Measurements"]:
        """Return the non-empty segments of a container and what was measured."""
        measured = _Measurements(self._namespace)
        if is_route:
            points = self._read_points(element, ROUTE_POINT_ELEMENT, measured)
            return ((TrackSegment(points=points),) if points else ()), measured

        raw_segments = list(children(element, SEGMENT_ELEMENT, self._namespace))
        if len(raw_segments) > self._limits.max_segments_per_track:
            raise TrackImportError(
                ImportErrorCode.TOO_MANY_TRACK_SEGMENTS,
                f"limit is {self._limits.max_segments_per_track}",
            )

        segments = []
        for raw_segment in raw_segments:
            points = self._read_points(raw_segment, TRACK_POINT_ELEMENT, measured)
            if points:
                segments.append(TrackSegment(points=points))
        return tuple(segments), measured

    def _read_points(
        self, parent: Element, element_name: str, measured: "_Measurements"
    ) -> tuple[TrackPoint, ...]:
        """Return the positions below a parent element, counting them against the limit."""
        points = []
        for node in children(parent, element_name, self._namespace):
            self._points_seen += 1
            if self._points_seen > self._limits.max_points:
                raise TrackImportError(
                    ImportErrorCode.TOO_MANY_TRACK_POINTS, f"limit is {self._limits.max_points}"
                )
            points.append(self._read_point(node))
            measured.observe(node)
        return tuple(points)

    def _read_point(self, node: Element) -> TrackPoint:
        """Return one normalized position, refusing anything unusable."""
        elevation = _parse_elevation(child_text(node, "ele", self._namespace))
        time = _parse_time(child_text(node, "time", self._namespace))
        try:
            return TrackPoint(
                latitude=_parse_coordinate(node.get("lat"), "lat"),
                longitude=_parse_coordinate(node.get("lon"), "lon"),
                elevation=elevation,
                time=time,
                heart_rate_bpm=_reading(node, HEART_RATE_ELEMENTS),
                cadence_rpm=_reading(node, CADENCE_ELEMENTS),
            )
        except ValueError as error:
            raise TrackImportError(
                ImportErrorCode.INVALID_COORDINATE, "position outside the valid range"
            ) from error


class _Measurements:
    """Records which measurement metadata a document actually carried.

    Both observations are read from a schema that defines them: receiver quality
    from GPX's own vocabulary on the position itself, a heading from GPX 1.0 or
    from a track-point extension this adapter knows. An element that merely
    shares one of those names is data the adapter does not understand.
    """

    def __init__(self, namespace: str) -> None:
        """Start with nothing observed, reading core fields in one namespace."""
        self._namespace = namespace
        self.receiver_quality = False
        self.course = False

    def observe(self, node: Element) -> None:
        """Note the measurement metadata attached to one position."""
        if not self.receiver_quality:
            self.receiver_quality = any(
                child_text(node, name, self._namespace) for name in RECEIVER_QUALITY_ELEMENTS
            )
        if not self.course:
            self.course = _states(node, COURSE_ELEMENTS)


def _reading(node: Element, names: frozenset[tuple[str, str]]) -> int | None:
    """Return one whole-number sensor reading from a schema that defines it.

    Unreadable is absent. A sensor writing something this adapter cannot parse
    has said nothing about the body it was strapped to, and inventing a zero
    would put a measurement in the archive that nobody made -- the one thing an
    absent value must never become. The position itself is unaffected: a
    coordinate is what makes a point, and a heart rate is not.
    """
    for found in descendants_in(node, names):
        text = (found.text or "").strip()
        if not text:
            continue
        try:
            value = int(float(text))
        except ValueError:
            return None
        return value if value >= 0 else None
    return None


def _claims_to_be_gpx(content: bytes) -> bool:
    """Report whether unparseable content still presents itself as GPX.

    The lenient half of detection, and lenient on purpose: it decides only which
    adapter gets to explain what is wrong with a file, never what the file means.
    Content this rough is read as UTF-8, which every GPX writer in practice
    produces; a document that is both unparseable *and* in another encoding is
    reported as an unknown format rather than guessed at.
    """
    text = content.decode("utf-8", errors="ignore")
    return bool(_GPX_ROOT_PATTERN.search(text)) and any(
        namespace in text for namespace in SUPPORTED_NAMESPACES
    )


def _states(node: Element, names: frozenset[tuple[str, str]]) -> bool:
    """Report whether a descendant from a known schema carries a value."""
    return any(bool(found.text and found.text.strip()) for found in descendants_in(node, names))


def _links(element: Element, namespace: str, legacy: bool) -> Iterator[str]:
    """Yield the external links an element declares.

    GPX 1.1 uses ``<link href="...">``; GPX 1.0 uses a ``<url>`` element.
    """
    if legacy:
        url = child_text(element, "url", namespace)
        if url:
            yield url
        return
    for link in children(element, "link", namespace):
        href = link.get("href")
        if href and href.strip():
            yield href.strip()


def _deduplicated(values: tuple[str, ...]) -> tuple[str, ...]:
    """Return the values without repetitions, in their original order."""
    return tuple(dict.fromkeys(values))


def _evidence(
    *,
    segments: tuple[TrackSegment, ...],
    measured: _Measurements,
    is_route: bool,
    has_links: bool,
    has_instructions: bool,
    states_activity: bool,
) -> tuple[EvidenceCode, ...]:
    """Return what was observed about one candidate, as neutral evidence."""
    observed = [
        EvidenceCode.ROUTE_ELEMENT_PRESENT if is_route else EvidenceCode.TRACK_ELEMENT_PRESENT
    ]

    has_time = any(point.time is not None for segment in segments for point in segment.points)
    observed.append(EvidenceCode.TIMESTAMPS_PRESENT if has_time else EvidenceCode.TIMESTAMPS_ABSENT)

    if measured.receiver_quality:
        observed.append(EvidenceCode.GPS_ACCURACY_PRESENT)
    if measured.course:
        observed.append(EvidenceCode.COURSE_MEASUREMENTS_PRESENT)
    if not measured.receiver_quality and not measured.course:
        observed.append(EvidenceCode.MEASUREMENT_METADATA_ABSENT)

    if has_links:
        observed.append(EvidenceCode.EXTERNAL_LINK_PRESENT)
    if has_instructions:
        observed.append(EvidenceCode.ROUTE_INSTRUCTIONS_PRESENT)
    if states_activity:
        observed.append(EvidenceCode.ACTIVITY_METADATA_PRESENT)

    return tuple(observed)


def _parse_coordinate(text: str | None, attribute: str) -> float:
    """Return a coordinate attribute as a number, refusing anything unusable."""
    if text is None:
        raise TrackImportError(ImportErrorCode.INVALID_COORDINATE, f"missing {attribute}")
    try:
        return float(text)
    except ValueError as error:
        raise TrackImportError(
            ImportErrorCode.INVALID_COORDINATE, f"{attribute} is not a number"
        ) from error


def _parse_elevation(text: str | None) -> float | None:
    """Return an elevation as a number, or ``None`` when the source states none."""
    if text is None:
        return None
    try:
        elevation = float(text)
    except ValueError as error:
        raise TrackImportError(ImportErrorCode.INVALID_GPX, "elevation is not a number") from error
    if not math.isfinite(elevation):
        raise TrackImportError(ImportErrorCode.INVALID_GPX, "elevation is not a finite number")
    return elevation


def _parse_time(text: str | None) -> datetime | None:
    """Return an instant, or ``None`` when the source states none.

    A value that cannot be read, or that carries no timezone, is refused: an
    instant whose meaning depends on the reader's timezone is not an instant.
    """
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise TrackImportError(
            ImportErrorCode.INVALID_TIMESTAMP, "time value cannot be read"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TrackImportError(ImportErrorCode.INVALID_TIMESTAMP, "time value has no timezone")
    return parsed
