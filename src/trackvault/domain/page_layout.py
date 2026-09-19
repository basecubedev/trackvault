"""How the owner arranged a page: which widget sits where, and how large.

An arrangement is user-owned data -- somebody's work, stored so it follows them
to every device and into every backup -- and it is presentation only. It decides
where a number is drawn and never which number it is, so nothing here knows a
track, a metric or a widget's meaning. What each widget *is* belongs to the page
that draws it, exactly as the colour of a map line does.

What the archive vouches for is the shape:

```
grid      a number of columns and rows of one fixed height
tile      one widget, placed on whole cells, inside the columns
hidden    widgets the owner took off the page, kept so they can come back
```

Two tiles never share a cell. The page never produces an overlap, so a document
holding one is not something the page wrote, and storing it would hand every
later reader a layout it has to repair.

A page distinguishes a few width classes, and each can carry an arrangement of
its own -- or none, in which case the page draws its own: its default, or a
derivation of a class that *was* arranged. Storing that instead would freeze
today's default into the archive, which is why a document may leave every class
but one empty, and why a document that arranges nothing is refused rather than
stored.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class LayoutPage(StrEnum):
    """A page whose arrangement the owner can change."""

    TRACK_DETAIL = "track-detail"


MAX_GRID_COLUMNS = 24
"""Most columns a grid may have. The page uses twelve; this is the ceiling."""

MAX_GRID_ROWS = 400
"""Rows a tile may reach down to. Far below it is a page nobody scrolls to."""

MAX_TILE_HEIGHT = 48
"""Tallest single tile, in rows."""

MAX_WIDGETS = 64
"""Most widgets one grid may name, placed and hidden together.

The page offers about a dozen. An unauthenticated endpoint that accepts a
document without a bound is a storage-growth decision nobody made.
"""

MAX_WIDGET_ID_LENGTH = 64
"""Longest widget identifier."""

_WIDGET_ID = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")
"""A widget is named by a plain key -- never markup, never a path."""


def _whole(value: object, name: str, minimum: int, maximum: int) -> int:
    """Return ``value`` if it is a whole number inside the bounds.

    ``bool`` is an ``int`` to Python and a cell to nobody, and a float that
    happens to be whole is still a document of another shape. What comes back
    out of the database is validated through here too, so the check is on the
    type as well as the range.

    Raises:
        ValueError: If the value is not a whole number within the bounds.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must lie between {minimum} and {maximum}")
    return value


def _widget_id(value: object) -> str:
    """Return ``value`` if it is a valid widget identifier.

    Raises:
        ValueError: If it is not a plain lower-case key.
    """
    if (
        not isinstance(value, str)
        or len(value) > MAX_WIDGET_ID_LENGTH
        or _WIDGET_ID.fullmatch(value) is None
    ):
        raise ValueError("a widget is named by a lower-case key such as 'map' or 'metric.distance'")
    return value


@dataclass(frozen=True, slots=True)
class LayoutTile:
    """One widget placed on the grid, in whole cells.

    Attributes:
        widget: Which widget, by the key the page gives it.
        x: First column, counted from zero.
        y: First row, counted from zero.
        width: Columns it spans.
        height: Rows it spans.
    """

    widget: str
    x: int
    y: int
    width: int
    height: int

    def overlaps(self, other: "LayoutTile") -> bool:
        """Report whether two tiles claim at least one common cell."""
        return (
            self.x < other.x + other.width
            and other.x < self.x + self.width
            and self.y < other.y + other.height
            and other.y < self.y + self.height
        )


@dataclass(frozen=True, slots=True)
class LayoutGrid:
    """The arrangement for one width class of a page.

    Attributes:
        columns: How many columns the grid is divided into.
        tiles: The widgets on the page, where they are and how large.
        hidden: Widgets the owner took off the page. Kept rather than forgotten,
            so the page can offer them back -- and so a widget a later release
            adds can be told apart from one the owner removed on purpose.
    """

    columns: int
    tiles: tuple[LayoutTile, ...]
    hidden: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse an arrangement the page could not draw as a grid.

        Raises:
            ValueError: If the grid, a tile or a widget name is out of shape, a
                widget appears twice, or two tiles overlap.
        """
        _whole(self.columns, "columns", 1, MAX_GRID_COLUMNS)
        if len(self.tiles) + len(self.hidden) > MAX_WIDGETS:
            raise ValueError(f"a grid names at most {MAX_WIDGETS} widgets")
        for tile in self.tiles:
            _validate_tile(tile, self.columns)
        names = [tile.widget for tile in self.tiles] + [
            _widget_id(widget) for widget in self.hidden
        ]
        if len(names) != len(set(names)):
            raise ValueError("every widget is placed or hidden exactly once")
        for index, tile in enumerate(self.tiles):
            for other in self.tiles[index + 1 :]:
                if tile.overlaps(other):
                    raise ValueError(f"tiles '{tile.widget}' and '{other.widget}' overlap")


def _validate_tile(tile: LayoutTile, columns: int) -> None:
    """Refuse a tile that is out of shape or does not fit its grid.

    Raises:
        ValueError: Naming the tile and what is wrong with it.
    """
    try:
        _widget_id(tile.widget)
        _whole(tile.x, "x", 0, columns - 1)
        _whole(tile.y, "y", 0, MAX_GRID_ROWS - 1)
        _whole(tile.width, "width", 1, columns - tile.x)
        _whole(tile.height, "height", 1, min(MAX_TILE_HEIGHT, MAX_GRID_ROWS - tile.y))
    except ValueError as error:
        raise ValueError(f"tile '{tile.widget}': {error}") from None


@dataclass(frozen=True, slots=True)
class PageLayout:
    """Everything the owner arranged on one page.

    Every width class is optional, the widest included. A class nobody arranged
    is drawn by the page -- from its own default, or derived from a class that
    *was* arranged -- and storing that default instead would freeze today's
    version of it into the archive, which is the same reason forgetting an
    arrangement stores nothing at all.

    Attributes:
        wide: The arrangement for the widest class, or ``None`` for the page's
            own.
        medium: A middle-width arrangement, or ``None``.
        narrow: A narrow arrangement, or ``None``.
    """

    wide: LayoutGrid | None = None
    medium: LayoutGrid | None = None
    narrow: LayoutGrid | None = None

    def __post_init__(self) -> None:
        """Refuse a document that arranges nothing.

        Three empty classes are the absence of an arrangement, and the archive
        already has a way to say that: no row at all.

        Raises:
            ValueError: If no width class is arranged.
        """
        if self.wide is None and self.medium is None and self.narrow is None:
            raise ValueError("a stored layout arranges at least one width class")


__all__ = [
    "MAX_GRID_COLUMNS",
    "MAX_GRID_ROWS",
    "MAX_TILE_HEIGHT",
    "MAX_WIDGETS",
    "MAX_WIDGET_ID_LENGTH",
    "LayoutGrid",
    "LayoutPage",
    "LayoutTile",
    "PageLayout",
]
