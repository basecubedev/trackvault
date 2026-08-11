"""Where a track came from, kept as evidence rather than as authority.

Source metadata is preserved so a decision can be explained and revisited later.
It never decides business meaning: no source application implies a track kind, and
no exchange format implies one either.

The record stays format-independent on purpose. A FIT importer fills the same
fields; it does not get fields of its own.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Provenance of one normalized track.

    Attributes:
        exchange_format: Lower-case identifier of the format the track arrived in,
            for example ``gpx``.
        format_version: Version of that format, if the document states one.
        creator: The application that wrote the document, as it identified itself.
            A label, never a business rule.
        external_links: Related web resources the document declares, in document
            order. What they point at, and whether the geometry has anything to
            do with them, is not observable: an application writing its own home
            page and a planner writing a permalink produce the same value. The
            field is named for what it is rather than for what it might mean --
            calling it a *source* link claimed a provenance the exchange format
            never states.
        extension_namespaces: The vendor extension namespaces observed in the
            document, sorted. A neutral summary that records *that* extra data was
            present without copying arbitrary payloads into the domain model.

    Raises:
        ValueError: If the exchange format is blank or not lower case.
    """

    exchange_format: str
    format_version: str | None = None
    creator: str | None = None
    external_links: tuple[str, ...] = ()
    extension_namespaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Normalize the extension summary and reject an unusable format name."""
        if not self.exchange_format.strip():
            raise ValueError("exchange_format must name the format the track arrived in")
        if self.exchange_format != self.exchange_format.lower():
            raise ValueError("exchange_format must be lower case")
        object.__setattr__(
            self, "extension_namespaces", tuple(sorted(set(self.extension_namespaces)))
        )
