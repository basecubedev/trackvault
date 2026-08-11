"""Unit tests for source metadata and the evidence vocabulary."""

import pytest

from trackvault.domain import EvidenceCode, SourceMetadata, in_canonical_order


@pytest.mark.unit
def test_the_exchange_format_is_required() -> None:
    """Every normalized track knows which format it was read from."""
    with pytest.raises(ValueError, match="exchange_format"):
        SourceMetadata(exchange_format="  ")


@pytest.mark.unit
def test_the_exchange_format_is_lower_case() -> None:
    """A format identifier is a stable wire value, not a display label."""
    with pytest.raises(ValueError, match="lower case"):
        SourceMetadata(exchange_format="GPX")


@pytest.mark.unit
def test_optional_provenance_may_be_missing() -> None:
    """Not every document names a creator, a version or a source link."""
    metadata = SourceMetadata(exchange_format="gpx")

    assert metadata.format_version is None
    assert metadata.creator is None
    assert metadata.external_links == ()
    assert metadata.extension_namespaces == ()


@pytest.mark.unit
def test_extension_namespaces_are_a_deduplicated_sorted_summary() -> None:
    """The summary records that extra data was present, not the payload itself."""
    metadata = SourceMetadata(
        exchange_format="gpx",
        extension_namespaces=(
            "http://example.test/b",
            "http://example.test/a",
            "http://example.test/b",
        ),
    )

    assert metadata.extension_namespaces == ("http://example.test/a", "http://example.test/b")


@pytest.mark.unit
def test_external_links_keep_their_document_order() -> None:
    """Link order is source data; only the extension summary gets normalized."""
    links = ("https://example.test/second", "https://example.test/first")

    assert SourceMetadata(exchange_format="gpx", external_links=links).external_links == links


@pytest.mark.unit
def test_evidence_codes_are_stable_lower_case_identifiers() -> None:
    """Codes are stored data; renaming one invalidates every stored explanation."""
    for code in EvidenceCode:
        assert code.value == code.value.lower()
        assert code.value.replace("_", "").isalnum()
        assert " " not in code.value


@pytest.mark.unit
def test_the_evidence_vocabulary_is_the_documented_set() -> None:
    """Adding or removing a code is a deliberate, visible contract change."""
    assert [code.value for code in EvidenceCode] == [
        "track_element_present",
        "route_element_present",
        "route_instructions_present",
        "timestamps_present",
        "timestamps_absent",
        "gps_accuracy_present",
        "course_measurements_present",
        "measurement_metadata_absent",
        "source_link_present",
        "external_link_present",
        "activity_metadata_present",
    ]


@pytest.mark.unit
def test_evidence_is_deduplicated_and_ordered_independently_of_discovery_order() -> None:
    """Stored evidence must not depend on what a parser happened to notice first."""
    observed = in_canonical_order(
        [
            EvidenceCode.GPS_ACCURACY_PRESENT,
            EvidenceCode.TIMESTAMPS_PRESENT,
            EvidenceCode.GPS_ACCURACY_PRESENT,
        ]
    )

    assert observed == (EvidenceCode.TIMESTAMPS_PRESENT, EvidenceCode.GPS_ACCURACY_PRESENT)
