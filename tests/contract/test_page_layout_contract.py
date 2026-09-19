"""The arrangement of the track page: the user's own, and presentation only.

A customised track page is somebody's work. It is stored by the archive rather
than by one browser, so it follows its owner to every device, survives a cleared
browser and travels inside a backup. What the archive vouches for is the
*shape* of that arrangement -- a grid, tiles inside it, nothing on top of
anything else. What each widget means is the page's business, exactly as the
colour of a map line is:

```
stored layout      what the user arranged        this resource
default layout     what the page draws unasked   the page, never stored
unreadable layout  a row this build cannot read  reported, never guessed at
```

Nothing here touches a track, a metric or a classification. Arranging the page
changes where a number is drawn, never which number it is.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from trackvault.config import Settings
from trackvault.domain.page_layout import (
    MAX_GRID_COLUMNS,
    MAX_GRID_ROWS,
    MAX_TILE_HEIGHT,
    MAX_WIDGETS,
    LayoutGrid,
    LayoutTile,
    PageLayout,
)
from trackvault.infrastructure.archive import FilesystemArchiveBuilder, FilesystemArchiveExtractor
from trackvault.infrastructure.assembly import TrackServices, build_services
from trackvault.main import create_app

pytestmark = pytest.mark.contract

RESOURCE = "/api/v1/layouts/track-detail"


def _grid(*tiles: LayoutTile, columns: int = 12, hidden: tuple[str, ...] = ()) -> LayoutGrid:
    """Return a grid of the given tiles."""
    return LayoutGrid(columns=columns, tiles=tiles, hidden=hidden)


def _tile(widget: str, x: int, y: int, width: int, height: int) -> LayoutTile:
    """Return one placed widget."""
    return LayoutTile(widget=widget, x=x, y=y, width=width, height=height)


def _document(**overrides: object) -> dict[str, Any]:
    """Return a layout document as the page sends it."""
    document: dict[str, Any] = {
        "wide": {
            "columns": 12,
            "tiles": [
                {"widget": "map", "x": 0, "y": 0, "width": 6, "height": 9},
                {"widget": "profile", "x": 6, "y": 0, "width": 6, "height": 9},
                {"widget": "metric.distance", "x": 0, "y": 9, "width": 2, "height": 2},
            ],
            "hidden": ["source"],
        },
        "medium": None,
        "narrow": {
            "columns": 1,
            "tiles": [
                {"widget": "profile", "x": 0, "y": 0, "width": 1, "height": 8},
                {"widget": "map", "x": 0, "y": 8, "width": 1, "height": 8},
            ],
            "hidden": ["metric.distance", "source"],
        },
    }
    document.update(overrides)
    return document


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Yield a client over an empty archive."""
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as client:
        yield client


# --- What a well-formed arrangement is --------------------------------------


@pytest.mark.unit
def test_a_grid_of_tiles_side_by_side_is_an_arrangement() -> None:
    """Tiles that touch are not tiles that overlap."""
    grid = _grid(
        _tile("map", 0, 0, 6, 9), _tile("profile", 6, 0, 6, 9), _tile("source", 0, 9, 12, 4)
    )

    assert len(grid.tiles) == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    "tile",
    [
        ("map", 7, 0, 6, 4),
        ("map", -1, 0, 2, 2),
        ("map", 0, -1, 2, 2),
        ("map", 0, 0, 0, 2),
        ("map", 0, 0, 2, 0),
        ("map", 0, 0, 2, MAX_TILE_HEIGHT + 1),
        ("map", 0, MAX_GRID_ROWS - 1, 2, 2),
    ],
)
def test_a_tile_stays_inside_its_grid(tile: tuple[str, int, int, int, int]) -> None:
    """A tile hanging off the right edge has no column to be drawn in.

    The bounds are also what keeps an unauthenticated endpoint from storing a
    grid a million rows tall: the arrangement of one page is small by nature,
    and a limit nobody states is a storage decision nobody made.
    """
    with pytest.raises(ValueError, match="tile"):
        _grid(LayoutTile(*tile))


@pytest.mark.unit
def test_two_tiles_never_occupy_one_cell() -> None:
    """A grid is an arrangement, not a stack of windows.

    The page never produces an overlap, which is exactly why one arriving here
    is refused rather than stored: it is a document nothing honest wrote.
    """
    with pytest.raises(ValueError, match="overlap"):
        _grid(_tile("map", 0, 0, 6, 9), _tile("profile", 5, 8, 6, 9))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tiles", "hidden"),
    [
        ((("map", 0, 0, 6, 9), ("map", 6, 0, 6, 9)), ()),
        ((("map", 0, 0, 6, 9),), ("map",)),
        ((), ("source", "source")),
    ],
)
def test_a_widget_is_placed_or_hidden_exactly_once(
    tiles: tuple[tuple[str, int, int, int, int], ...], hidden: tuple[str, ...]
) -> None:
    """One widget, one place. Two maps would be two answers to "where is it"."""
    with pytest.raises(ValueError, match="once"):
        _grid(*(LayoutTile(*tile) for tile in tiles), hidden=hidden)


@pytest.mark.unit
@pytest.mark.parametrize(
    "widget",
    ["", "Map", "map!", "../map", "<script>", "map..distance", "-map", "map.", "m" * 65],
)
def test_a_widget_is_named_by_a_plain_identifier(widget: str) -> None:
    """A widget name is a key the page looks up, never markup or a path."""
    with pytest.raises(ValueError, match="widget"):
        _grid(_tile(widget, 0, 0, 2, 2))


@pytest.mark.unit
@pytest.mark.parametrize("columns", [0, MAX_GRID_COLUMNS + 1])
def test_a_grid_has_a_sensible_number_of_columns(columns: int) -> None:
    """Zero columns draw nothing; a hundred are not a layout anybody arranged."""
    with pytest.raises(ValueError, match="columns"):
        _grid(columns=columns)


@pytest.mark.unit
def test_a_grid_holds_a_bounded_number_of_widgets() -> None:
    """The page offers a dozen widgets; a thousand are a document nobody arranged."""
    hidden = tuple(f"widget{index}" for index in range(MAX_WIDGETS + 1))

    with pytest.raises(ValueError, match="widgets"):
        _grid(hidden=hidden)


@pytest.mark.unit
def test_a_position_is_a_whole_number() -> None:
    """A stored document is untrusted on the way back out, so the type is checked too.

    ``True`` is an ``int`` to Python and a cell to nobody.
    """
    with pytest.raises(ValueError, match="whole number"):
        _grid(LayoutTile(widget="map", x=True, y=0, width=2, height=2))
    with pytest.raises(ValueError, match="whole number"):
        _grid(LayoutTile(widget="map", x=0, y=0, width=2.0, height=2))  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_width_class_nobody_arranged_is_left_to_the_page() -> None:
    """Every class is optional, including the widest.

    Somebody who only rearranges their phone has not arranged the wide page,
    and storing today's default for it would freeze that default into the
    archive -- the same reason a reset stores nothing at all.
    """
    phone_only = PageLayout(
        wide=None, medium=None, narrow=_grid(_tile("map", 0, 0, 1, 8), columns=1)
    )

    assert phone_only.wide is None
    assert phone_only.narrow is not None


@pytest.mark.unit
def test_an_arrangement_of_nothing_is_not_an_arrangement() -> None:
    """Nothing arranged is the absence of a row, not a row saying nothing."""
    with pytest.raises(ValueError, match="at least one"):
        PageLayout(wide=None, medium=None, narrow=None)


# --- The resource -----------------------------------------------------------


def test_an_archive_nobody_arranged_draws_the_default(client: TestClient) -> None:
    """Nothing stored is its own answer, and the page knows what it means."""
    response = client.get(RESOURCE)

    assert response.status_code == 200
    assert response.json() == {"state": "default", "layout": None, "updated_at": None}


def test_a_saved_arrangement_reads_back_exactly(client: TestClient) -> None:
    """What the user arranged is what comes back, down to a hidden widget."""
    saved = client.put(RESOURCE, json=_document())

    assert saved.status_code == 200
    assert saved.json()["state"] == "custom"
    assert saved.json()["updated_at"] is not None
    read = client.get(RESOURCE).json()
    assert read["state"] == "custom"
    assert read["layout"] == _document()


def test_an_arrangement_of_the_narrow_page_alone_leaves_the_wide_one_to_the_page(
    client: TestClient,
) -> None:
    """A phone arrangement does not drag a copy of today's wide default with it."""
    document = _document(wide=None, medium=None)

    saved = client.put(RESOURCE, json=document)

    assert saved.status_code == 200
    assert client.get(RESOURCE).json()["layout"] == document


def test_a_document_that_arranges_nothing_is_refused(client: TestClient) -> None:
    """Three nulls say nothing; forgetting the arrangement is what DELETE is for."""
    response = client.put(RESOURCE, json=_document(wide=None, medium=None, narrow=None))

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "layout_invalid"
    assert client.get(RESOURCE).json()["state"] == "default"


def test_saving_replaces_the_whole_arrangement(client: TestClient) -> None:
    """A layout is one document. A width class left out is no longer arranged.

    Merging would keep a narrow arrangement the user just discarded, and the
    page would draw something nobody can see how to change.
    """
    client.put(RESOURCE, json=_document())
    client.put(RESOURCE, json=_document(narrow=None))

    assert client.get(RESOURCE).json()["layout"]["narrow"] is None


def test_an_overlapping_arrangement_is_refused_and_nothing_is_stored(client: TestClient) -> None:
    """A refusal names the problem in the archive's own envelope."""
    document = _document()
    document["wide"]["tiles"][1]["x"] = 5

    response = client.put(RESOURCE, json=document)

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "layout_invalid"
    assert "overlap" in response.json()["detail"]["error"]["message"]
    assert client.get(RESOURCE).json()["state"] == "default"


@pytest.mark.parametrize(
    "document",
    [
        _document(extra="field"),
        _document(
            wide={
                "columns": 12,
                "tiles": [{"widget": "map", "x": "0", "y": 0, "width": 2, "height": 2}],
                "hidden": [],
            }
        ),
        _document(wide={"columns": 12, "tiles": [], "hidden": [], "gutter": 3}),
    ],
)
def test_a_document_of_another_shape_is_refused(
    client: TestClient, document: dict[str, Any]
) -> None:
    """Stored verbatim, so nothing the archive would silently drop is accepted."""
    assert client.put(RESOURCE, json=document).status_code == 422
    assert client.get(RESOURCE).json()["state"] == "default"


def test_resetting_hands_the_page_back_its_default(client: TestClient) -> None:
    """Reset is the absence of a layout, not a stored copy of the default.

    A copy would freeze today's default into the archive and hide every later
    improvement to it behind a document the user never actually arranged.
    """
    client.put(RESOURCE, json=_document())

    reset = client.delete(RESOURCE)

    assert reset.status_code == 200
    assert reset.json() == {"state": "default", "layout": None, "updated_at": None}
    assert client.get(RESOURCE).json()["state"] == "default"
    assert client.delete(RESOURCE).status_code == 200


# --- What comes back out of the database ------------------------------------


def _write_row(settings: Settings, format_version: int, document: str) -> None:
    """Store a layout row directly, as a damaged disk or a defect would."""
    connection = sqlite3.connect(settings.database_path, isolation_level=None)
    try:
        connection.execute(
            "INSERT INTO page_layouts (page, format_version, document, updated_at) "
            "VALUES ('track-detail', ?, ?, '2026-09-19T10:00:00+00:00') "
            "ON CONFLICT (page) DO UPDATE SET format_version = excluded.format_version, "
            "document = excluded.document",
            (format_version, document),
        )
    finally:
        connection.close()


@pytest.mark.persistence
@pytest.mark.parametrize(
    ("format_version", "document"),
    [
        (1, "{not json"),
        (1, '{"wide": {"columns": 12, "tiles": [{"widget": "map"}], "hidden": []}}'),
        (1, '{"wide": {"columns": 12, "tiles": [], "hidden": [7]}}'),
        (1, "[1, 2, 3]"),
        (99, '{"wide": {"columns": 12, "tiles": [], "hidden": []}}'),
    ],
)
def test_a_stored_layout_this_build_cannot_read_fails_closed(
    tmp_path: Path, format_version: int, document: str
) -> None:
    """Unreadable is reported as unreadable -- never a traceback, never a guess.

    The row is left where it is. Reading is not the place to delete somebody's
    work; saving a new arrangement is what replaces it, and the page says so.
    """
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        _write_row(settings, format_version, document)

        first = client.get(RESOURCE)
        second = client.get(RESOURCE)

        assert first.status_code == 200
        assert first.json()["state"] == "unreadable"
        assert first.json()["layout"] is None
        assert second.json()["state"] == "unreadable"

        assert client.put(RESOURCE, json=_document()).json()["state"] == "custom"
        assert client.get(RESOURCE).json()["layout"] == _document()


@pytest.mark.persistence
def test_a_document_nested_deeper_than_the_parser_can_go_is_unreadable(tmp_path: Path) -> None:
    """A damaged file is an answer about the archive, never a traceback.

    Deep nesting exhausts the parser's own stack rather than failing its
    grammar, which is a different exception and the same situation.
    """
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        _write_row(settings, 1, "[" * 100_000 + "]" * 100_000)

        response = client.get(RESOURCE)

        assert response.status_code == 200
        assert response.json()["state"] == "unreadable"


@pytest.mark.persistence
def test_a_damaged_timestamp_costs_the_reading_its_date_and_nothing_else(tmp_path: Path) -> None:
    """The arrangement is the owner's work; when it was saved is a detail.

    Throwing away a layout that reads perfectly because the row's own clock
    field does not parse would lose the work to protect the metadata.
    """
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        client.put(RESOURCE, json=_document())
        _break_timestamp(settings)

        reading = client.get(RESOURCE).json()

        assert reading["state"] == "custom"
        assert reading["layout"] == _document()
        assert reading["updated_at"] is None


def _break_timestamp(settings: Settings) -> None:
    """Leave the stored document intact and damage only its timestamp."""
    connection = sqlite3.connect(settings.database_path, isolation_level=None)
    try:
        connection.execute("UPDATE page_layouts SET updated_at = 'not a time'")
    finally:
        connection.close()


@pytest.mark.persistence
def test_an_arrangement_travels_inside_a_backup(tmp_path: Path) -> None:
    """The layout lives in the archive's database, so a restore brings it back."""
    origin = build_services(Settings(data_dir=tmp_path / "origin"))
    origin.prepare_storage()
    with TestClient(create_app(origin.settings)) as client:
        client.put(RESOURCE, json=_document())
    archive = tmp_path / "backup.tar.gz"
    origin.create_archive(
        FilesystemArchiveBuilder(
            destination=archive,
            database_path=origin.settings.database_path,
            raw_root=origin.settings.raw_storage_dir,
        )
    )

    restored = _restore(origin, archive, tmp_path / "restored")

    with TestClient(create_app(restored)) as client:
        assert client.get(RESOURCE).json()["layout"] == _document()


def _restore(services: TrackServices, archive: Path, data_dir: Path) -> Settings:
    """Restore an archive into a fresh data directory and return its settings."""
    services.restore_archive(
        FilesystemArchiveExtractor(
            source=archive,
            data_dir=data_dir,
            database_path=data_dir / "trackvault.sqlite3",
            raw_root=data_dir / "raw",
        )
    )
    return Settings(data_dir=data_dir)
