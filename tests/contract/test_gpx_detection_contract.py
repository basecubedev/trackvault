"""Executable contract for recognising a GPX document.

Detection answers one question -- "is this adapter the one that should read these
bytes?" -- and it answers it from the content. What identifies a GPX document is
the namespaced name of its root element, so the reliable way to find out is to
parse until the first start tag and then stop.

The mistake this file pins is the shortcut: looking only at a fixed-size prefix.
XML allows a declaration, processing instructions, comments and whitespace before
the root, in any amount, and a document that uses more of them than the prefix is
still a perfectly ordinary document. Refusing it as an unknown format loses a
valid file for a reason that has nothing to do with it.
"""

from pathlib import Path

import pytest

from trackvault.application import ImportErrorCode, ImportLimits, TrackImportError
from trackvault.infrastructure.gpx import GpxImporter

pytestmark = [pytest.mark.contract, pytest.mark.gpx]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gpx"


def read(name: str) -> bytes:
    """Return the bytes of a synthetic fixture."""
    return (FIXTURES / name).read_bytes()


# --- The root element decides, wherever it starts -----------------------------


def test_a_long_prolog_does_not_hide_the_root_element() -> None:
    """A valid document with kilobytes of comments in front is still valid.

    Licence headers, provenance comments and stylesheet instructions are ordinary
    things to find before a root element, and nothing bounds how much of them a
    writer may emit.
    """
    assert GpxImporter().detects(read("long-prolog.gpx"))


def test_a_document_behind_a_long_prolog_normalizes_like_any_other() -> None:
    """Recognising the root is worth nothing if the content then stays unread."""
    (track,) = GpxImporter().import_tracks(read("long-prolog.gpx"), ImportLimits())

    assert track.point_count == 2


def test_a_namespace_prefixed_root_is_recognised() -> None:
    """``<g:gpx xmlns:g="...">`` is the same element as ``<gpx xmlns="...">``."""
    assert GpxImporter().detects(read("prefixed-root.gpx"))


def test_the_gpx_namespace_alone_does_not_make_a_document_gpx() -> None:
    """The root element decides, not the namespaces a document happens to declare."""
    assert not GpxImporter().detects(read("wrong-root-element.gpx"))


# --- Encodings ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["recorded-measurements.gpx", "utf-8-bom.gpx", "utf-16.gpx"])
def test_the_documents_own_declaration_decides_how_its_bytes_are_read(name: str) -> None:
    """Detection must not assume UTF-8; the XML declaration and BOM say what to do.

    Only the encodings tested here are claimed. A byte order mark and a declared
    UTF-16 document are both things a real writer produces, and both are read by
    the same parser that reads the document afterwards -- so detection and
    parsing can never disagree about what the bytes say.
    """
    assert GpxImporter().detects(read(name))


@pytest.mark.parametrize("name", ["utf-8-bom.gpx", "utf-16.gpx"])
def test_a_document_in_a_supported_encoding_normalizes(name: str) -> None:
    """A claimed encoding has to survive past detection to mean anything."""
    (track,) = GpxImporter().import_tracks(read(name), ImportLimits())

    assert track.point_count == 2


# --- Detection stays bounded --------------------------------------------------


def test_content_without_a_root_element_is_not_claimed() -> None:
    """A prolog that never reaches a root is not a document this adapter reads.

    Reading is bounded by the import byte limit, which the input paths already
    apply, so detection needs no size assumption of its own -- but it must still
    answer rather than search forever.
    """
    endless_prolog = b'<?xml version="1.0"?>\n' + b"<!-- nothing here -->\n" * 20_000

    assert not GpxImporter().detects(endless_prolog)


def test_an_empty_document_is_not_claimed() -> None:
    """Nothing at all is not a GPX file."""
    assert not GpxImporter().detects(b"")


# --- Broken is still claimed --------------------------------------------------


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("malformed.gpx", ImportErrorCode.INVALID_GPX),
        ("unsafe-entity.gpx", ImportErrorCode.UNSAFE_XML),
        ("unsafe-external-entity.gpx", ImportErrorCode.UNSAFE_XML),
    ],
)
def test_a_document_that_claims_to_be_gpx_is_claimed_so_it_can_be_called_broken(
    name: str, code: ImportErrorCode
) -> None:
    """Not-GPX-at-all and broken-GPX are different problems for whoever fixes the file."""
    assert GpxImporter().detects(read(name))
    with pytest.raises(TrackImportError) as raised:
        GpxImporter().import_tracks(read(name), ImportLimits())
    assert raised.value.code is code


@pytest.mark.parametrize("name", ["not-gpx.xml", "not-xml.txt"])
def test_other_content_is_left_alone(name: str) -> None:
    """Well-formed XML that is not GPX, and non-XML input, belong to no adapter here."""
    assert not GpxImporter().detects(read(name))
