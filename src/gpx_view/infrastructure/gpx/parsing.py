"""Safe XML access for untrusted GPX documents.

Imported files come from a phone, a download folder or a sync directory. They are
untrusted input, so the parser fails closed:

* no document type definition is processed,
* no entity is declared or expanded,
* no external reference is resolved, and therefore no network or file access
  happens while parsing,
* the input size is bounded before parsing starts, and the structural limits are
  enforced while the document is walked.

``defusedxml`` provides the hardened parser configuration. It is a single, small,
well-established dependency for exactly this problem; hand-rolling the same
handler installation against a private attribute of the standard library parser
would be clever code that breaks quietly.
"""

from collections.abc import Iterator
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import DefusedXMLParser, fromstring

from gpx_view.application import ImportErrorCode, TrackImportError

GPX_1_1_NAMESPACE = "http://www.topografix.com/GPX/1/1"
GPX_1_0_NAMESPACE = "http://www.topografix.com/GPX/1/0"

SUPPORTED_NAMESPACES: dict[str, str] = {
    GPX_1_1_NAMESPACE: "1.1",
    GPX_1_0_NAMESPACE: "1.0",
}

ROOT_TAG = "gpx"


def parse_document(content: bytes) -> Element:
    """Parse untrusted XML into an element tree, failing closed.

    Args:
        content: The raw document bytes.

    Returns:
        The root element.

    Raises:
        TrackImportError: ``unsafe_xml`` if the document uses a DTD, an entity
            declaration or an external reference; ``invalid_gpx`` if it is not
            well-formed.
    """
    try:
        return fromstring(
            content,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except DefusedXmlException as error:
        raise TrackImportError(ImportErrorCode.UNSAFE_XML, type(error).__name__) from error
    except ParseError as error:
        raise TrackImportError(
            ImportErrorCode.INVALID_GPX, "document is not well-formed"
        ) from error


class _RootFound(Exception):  # noqa: N818 - a control signal, not a failure
    """Raised as soon as the first start tag has been seen.

    Reading the root is a question about the first element, so the parse stops
    there. Feeding the rest would cost the whole document for an answer that is
    already known.
    """


class _RootElementTarget:
    """Records the qualified name of a document's first element and nothing else.

    A parser target rather than a tree: identifying the root must not cost the
    memory of building one, and everything after the first start tag is
    irrelevant to the question being asked.
    """

    def __init__(self) -> None:
        """Start with no element seen."""
        self.tag: str | None = None

    def start(self, tag: str, attrib: dict[str, str]) -> None:  # noqa: ARG002 - target protocol
        """Remember the first element's namespaced name and stop the parse."""
        self.tag = tag
        raise _RootFound

    def end(self, tag: str) -> None:
        """Ignore element ends; only the root's identity is being read."""

    def data(self, data: str) -> None:
        """Ignore character data; only the root's identity is being read."""

    def close(self) -> None:
        """Return nothing: the target's result is read from ``tag``."""


def root_element_name(content: bytes) -> str | None:
    """Return the namespaced name of the document's root element, or ``None``.

    The name is read structurally, so a namespace prefix does not change the
    answer: ``<g:gpx xmlns:g="...">`` and ``<gpx xmlns="...">`` are the same
    element and both yield ``{...}gpx``. The XML declaration and a byte order
    mark are honoured by the parser, so the document's own encoding decides how
    its bytes are read.

    Everything an XML document may put before its root -- the declaration,
    processing instructions, comments, whitespace -- is skipped by parsing rather
    than by assuming a maximum size for it. A licence header longer than some
    prefix is not a different kind of document, and refusing one for that reason
    would lose a valid file for a reason that has nothing to do with it. What
    bounds the work is the caller's input, which the import limits already bound.

    ``None`` means the root could not be identified at all -- the content is not
    XML, it breaks before its first element, or it declares entities, which are
    still refused here. Whoever asked has to decide what to do with that; this
    function does not guess.
    """
    target = _RootElementTarget()
    parser = DefusedXMLParser(
        target=target,
        # A document type declaration is tolerated only far enough to reach the
        # root element behind it. Entity declarations and external references
        # stay forbidden, which is what an expansion attack needs.
        forbid_dtd=False,
        forbid_entities=True,
        forbid_external=True,
    )
    try:
        parser.feed(content)
    except (_RootFound, ParseError, DefusedXmlException):
        return target.tag
    return target.tag


def namespace_of_tag(tag: str) -> str:
    """Return the namespace of a qualified tag, or an empty string if it has none."""
    if tag.startswith("{"):
        return tag[1:].partition("}")[0]
    return ""


def local_name_of_tag(tag: str) -> str:
    """Return a qualified tag without its namespace."""
    return tag.rpartition("}")[2]


def namespace_of(element: Element) -> str:
    """Return the XML namespace of an element, or an empty string if it has none."""
    return namespace_of_tag(element.tag)


def local_name(element: Element) -> str:
    """Return the element name without its namespace."""
    return local_name_of_tag(element.tag)


def children(element: Element, name: str, namespace: str) -> Iterator[Element]:
    """Yield the direct children with the given name in the given namespace."""
    return (child for child in element if child.tag == f"{{{namespace}}}{name}")


def first_child(element: Element, name: str, namespace: str) -> Element | None:
    """Return the first direct child with the given name, or ``None``."""
    return next(children(element, name, namespace), None)


def child_text(element: Element, name: str, namespace: str) -> str | None:
    """Return the stripped text of the first matching child, or ``None``."""
    child = first_child(element, name, namespace)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def qualified_name(element: Element) -> tuple[str, str]:
    """Return an element's namespace and local name as one comparable pair.

    Extension semantics are decided on the pair, never on the local name alone: a
    word is not a schema, and a document may use any word in a namespace of its
    own. See :mod:`gpx_view.infrastructure.gpx.extensions`.
    """
    return namespace_of_tag(element.tag), local_name_of_tag(element.tag)


def descendants_in(element: Element, names: frozenset[tuple[str, str]]) -> Iterator[Element]:
    """Yield every descendant whose namespaced name is one of the given ones."""
    return (node for node in element.iter() if qualified_name(node) in names)


def used_namespaces(root: Element) -> set[str]:
    """Return every namespace actually used by an element in the document.

    A neutral summary of what extension data was present, without copying any of
    the payload into the domain model.
    """
    return {namespace for node in root.iter() if (namespace := namespace_of(node))}
