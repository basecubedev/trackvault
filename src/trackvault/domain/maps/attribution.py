"""Who made the map, under what licence, and how that survives every projection.

> Attribution metadata travels with the installed map package.

A hard-coded footer string is the failure this type exists to prevent. It reads
correctly for exactly one provider, and the moment a second one is added it is a
statement the build makes about a file it never read. So attribution is data,
extracted from the package at install time and stored beside it, and every
surface -- the map control, the package card, the credits page -- renders the
same object.

Links are structured `(label, url)` pairs rather than markup. Provider metadata
is remote input, and the shortest path from remote input to a cross-site
scripting hole is a footer that renders somebody else's HTML.
"""

from dataclasses import dataclass

HTTPS_PREFIX = "https://"

MAX_TEXT_LENGTH = 500
MAX_LABEL_LENGTH = 120
MAX_URL_LENGTH = 500
MAX_LINKS = 8


def _plain(value: str, *, limit: int, what: str) -> str:
    """Return a trimmed single-line string, or refuse it.

    Control characters are refused rather than stripped. A provider that sends
    a newline inside a licence name is a provider whose metadata this build does
    not understand, and quietly repairing it would hide that.
    """
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{what} must not be blank")
    if len(trimmed) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    if any(character < " " or character == "\x7f" for character in trimmed):
        raise ValueError(f"{what} must not contain control characters")
    return trimmed


@dataclass(frozen=True, slots=True)
class AttributionLink:
    """One credit link: what it says, and where it points.

    Attributes:
        label: The text a reader sees.
        url: An ``https://`` address. Nothing else is accepted -- not ``http``,
            which would downgrade a page that is otherwise same-origin, and
            certainly not ``javascript:``.
    """

    label: str
    url: str

    def __post_init__(self) -> None:
        """Refuse a link that is blank, over-long or not plain HTTPS."""
        object.__setattr__(
            self, "label", _plain(self.label, limit=MAX_LABEL_LENGTH, what="a link label")
        )
        url = _plain(self.url, limit=MAX_URL_LENGTH, what="a link url")
        if not url.startswith(HTTPS_PREFIX) or len(url) <= len(HTTPS_PREFIX):
            raise ValueError("an attribution link must be an https:// url")
        object.__setattr__(self, "url", url)


@dataclass(frozen=True, slots=True)
class MapAttribution:
    """What has to be said whenever a package's data is drawn.

    Attributes:
        data_owner: Who the underlying data belongs to, as the package states
            it -- for OpenStreetMap-derived data, the contributors.
        provider: Who assembled and published the package.
        license_identifier: A short, stable code for the licence, suitable for
            comparison and for an audit table.
        license_name: The licence's own name, as a reader would recognise it.
        required_text: The one line that must be visible while the map renders.
        links: Credit links, in the order they should be offered.
    """

    data_owner: str
    provider: str
    license_identifier: str
    license_name: str
    required_text: str
    links: tuple[AttributionLink, ...] = ()

    def __post_init__(self) -> None:
        """Refuse an attribution that would render as nothing."""
        object.__setattr__(
            self, "data_owner", _plain(self.data_owner, limit=MAX_TEXT_LENGTH, what="a data owner")
        )
        object.__setattr__(
            self, "provider", _plain(self.provider, limit=MAX_TEXT_LENGTH, what="a provider")
        )
        object.__setattr__(
            self,
            "license_identifier",
            _plain(self.license_identifier, limit=MAX_LABEL_LENGTH, what="a licence identifier"),
        )
        object.__setattr__(
            self,
            "license_name",
            _plain(self.license_name, limit=MAX_TEXT_LENGTH, what="a licence name"),
        )
        object.__setattr__(
            self,
            "required_text",
            _plain(self.required_text, limit=MAX_TEXT_LENGTH, what="an attribution text"),
        )
        if len(self.links) > MAX_LINKS:
            raise ValueError(f"an attribution carries at most {MAX_LINKS} links")
