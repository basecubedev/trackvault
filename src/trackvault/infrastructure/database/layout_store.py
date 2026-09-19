"""The SQLite implementation of the page layout repository.

An arrangement is stored as one JSON document per page, beside a format version
that says which shape the document has. It shares the archive's database file,
which is what makes a backup carry it without anybody having to remember to.

What comes back out is untrusted input. It may have been written by a build
that is gone, damaged on disk, or edited by hand, so it is decoded into the
domain types -- which validate -- and anything that fails is reported as
unreadable rather than raised as a traceback or quietly replaced.
"""

import json
import logging
from datetime import UTC, datetime

from trackvault.application.ports import StoredPageLayout
from trackvault.domain.page_layout import LayoutGrid, LayoutPage, LayoutTile, PageLayout
from trackvault.infrastructure.database.store import SqliteTrackStore

logger = logging.getLogger(__name__)

LAYOUT_FORMAT_VERSION = 1
"""The shape of the stored document. A new shape is a new version, never a reinterpretation."""


class SqlitePageLayoutStore:
    """Page arrangements, in the archive's database."""

    def __init__(self, store: SqliteTrackStore) -> None:
        """Share the archive's store, so there is one database and one migration."""
        self._store = store

    def read_page_layout(self, page: LayoutPage) -> StoredPageLayout | None:
        """Return a page's stored arrangement, validated, or ``None`` if there is none."""
        with self._store.connection() as connection:
            row = connection.execute(
                "SELECT format_version, document, updated_at FROM page_layouts WHERE page = ?",
                (page.value,),
            ).fetchone()
        if row is None:
            return None
        # The timestamp is metadata about the arrangement, not part of it: a
        # row whose clock field is damaged still holds the owner's work.
        updated_at = _instant(row["updated_at"])
        layout = _decode(row["format_version"], row["document"])
        if layout is None:
            # Left in place. A newer build may understand it, and deleting the
            # owner's work is a decision for the owner saving a new arrangement,
            # not for a read.
            logger.warning("page_layout.unreadable page=%s", page.value)
        return StoredPageLayout(layout=layout, updated_at=updated_at)

    def save_page_layout(self, page: LayoutPage, layout: PageLayout, at: datetime) -> None:
        """Store a page's arrangement, replacing the previous document entirely."""
        with self._store.connection() as connection:
            connection.execute(
                "INSERT INTO page_layouts (page, format_version, document, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (page) DO UPDATE SET "
                "format_version = excluded.format_version, document = excluded.document, "
                "updated_at = excluded.updated_at",
                (
                    page.value,
                    LAYOUT_FORMAT_VERSION,
                    _encode(layout),
                    at.astimezone(UTC).isoformat(),
                ),
            )

    def delete_page_layout(self, page: LayoutPage) -> None:
        """Remove a page's arrangement. Removing one that is not there is not an error."""
        with self._store.connection() as connection:
            connection.execute("DELETE FROM page_layouts WHERE page = ?", (page.value,))


def _instant(text: object) -> datetime | None:
    """Return a stored instant, or ``None`` if the row's own timestamp is unreadable."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _encode(layout: PageLayout) -> str:
    """Return the stored form of an arrangement."""
    return json.dumps(
        {
            "wide": None if layout.wide is None else _encode_grid(layout.wide),
            "medium": None if layout.medium is None else _encode_grid(layout.medium),
            "narrow": None if layout.narrow is None else _encode_grid(layout.narrow),
        },
        separators=(",", ":"),
    )


def _encode_grid(grid: LayoutGrid) -> dict[str, object]:
    """Return the stored form of one grid."""
    return {
        "columns": grid.columns,
        "tiles": [
            {
                "widget": tile.widget,
                "x": tile.x,
                "y": tile.y,
                "width": tile.width,
                "height": tile.height,
            }
            for tile in grid.tiles
        ],
        "hidden": list(grid.hidden),
    }


def _decode(format_version: object, document: object) -> PageLayout | None:
    """Return the arrangement a stored row holds, or ``None`` if it cannot be read.

    Every way a document can be wrong ends here as ``None``: a version this
    build does not know, text that is not JSON, a field of the wrong type, or a
    grid the domain refuses. Which one it was is not the reader's problem --
    the answer to all of them is "the page cannot draw this".
    """
    if format_version != LAYOUT_FORMAT_VERSION or not isinstance(document, str):
        return None
    try:
        parsed = json.loads(document)
        if not isinstance(parsed, dict):
            return None
        return PageLayout(
            wide=None if parsed["wide"] is None else _decode_grid(parsed["wide"]),
            medium=None if parsed["medium"] is None else _decode_grid(parsed["medium"]),
            narrow=None if parsed["narrow"] is None else _decode_grid(parsed["narrow"]),
        )
    except (ValueError, KeyError, TypeError, RecursionError):
        # ``RecursionError`` is what a document nested deeper than the parser's
        # own stack raises, and it is the same situation as malformed text: this
        # build cannot read it.
        return None


def _decode_grid(document: object) -> LayoutGrid:
    """Return one stored grid, validated by the domain.

    Raises:
        ValueError: If the grid is out of shape.
        KeyError: If a field is missing.
        TypeError: If a value is of the wrong kind.
    """
    if not isinstance(document, dict):
        raise TypeError("a grid is an object")
    tiles = document["tiles"]
    hidden = document["hidden"]
    if not isinstance(tiles, list) or not isinstance(hidden, list):
        raise TypeError("tiles and hidden widgets are lists")
    return LayoutGrid(
        columns=document["columns"],
        tiles=tuple(
            LayoutTile(
                widget=tile["widget"],
                x=tile["x"],
                y=tile["y"],
                width=tile["width"],
                height=tile["height"],
            )
            for tile in tiles
        ),
        hidden=tuple(hidden),
    )
