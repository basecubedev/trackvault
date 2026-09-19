"""The owner's arrangement of the track page, over HTTP.

```
GET     /api/v1/layouts/track-detail   default, custom or unreadable
PUT     /api/v1/layouts/track-detail   store a whole arrangement
DELETE  /api/v1/layouts/track-detail   forget it; the page draws its default
```

The document is stored as sent, so nothing the archive would silently drop is
accepted: an unknown field is refused rather than ignored. What a widget *means*
is not checked here and cannot be -- that is the page's vocabulary, exactly as
the colour of a map line is. What is checked is that the arrangement is a grid:
bounded, every widget once, no two tiles on one cell.
"""

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from trackvault.application.page_layout import LayoutReading, LayoutState, PageLayouts
from trackvault.domain.page_layout import (
    MAX_GRID_COLUMNS,
    MAX_GRID_ROWS,
    MAX_TILE_HEIGHT,
    MAX_WIDGET_ID_LENGTH,
    MAX_WIDGETS,
    LayoutGrid,
    LayoutPage,
    LayoutTile,
    PageLayout,
)

router = APIRouter(prefix="/api/v1/layouts", tags=["layouts"])

WidgetId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=MAX_WIDGET_ID_LENGTH,
        pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$",
    ),
]


class LayoutTileDocument(BaseModel):
    """One widget on the grid, in whole cells."""

    model_config = ConfigDict(extra="forbid")

    widget: WidgetId = Field(description="Which widget, by the key the page gives it")
    x: StrictInt = Field(ge=0, lt=MAX_GRID_COLUMNS, description="First column, from zero")
    y: StrictInt = Field(ge=0, lt=MAX_GRID_ROWS, description="First row, from zero")
    width: StrictInt = Field(ge=1, le=MAX_GRID_COLUMNS, description="Columns it spans")
    height: StrictInt = Field(ge=1, le=MAX_TILE_HEIGHT, description="Rows it spans")


class LayoutGridDocument(BaseModel):
    """The arrangement for one width class."""

    model_config = ConfigDict(extra="forbid")

    columns: StrictInt = Field(ge=1, le=MAX_GRID_COLUMNS)
    tiles: list[LayoutTileDocument] = Field(max_length=MAX_WIDGETS)
    hidden: list[WidgetId] = Field(
        max_length=MAX_WIDGETS,
        description="Widgets the owner took off the page, so the page can offer them back",
    )


class PageLayoutDocument(BaseModel):
    """Everything the owner arranged on the page.

    A class is ``null`` when the owner arranged none of it and the page draws
    its own -- including ``wide``, so that arranging a phone does not store a
    copy of today's wide default. All three fields are required rather than
    optional so that "left out" and "not arranged" cannot mean two different
    things, and all three ``null`` is refused: that is what forgetting the
    arrangement is for.
    """

    model_config = ConfigDict(extra="forbid")

    wide: LayoutGridDocument | None
    medium: LayoutGridDocument | None
    narrow: LayoutGridDocument | None


class PageLayoutResponse(BaseModel):
    """What the archive holds for the page's arrangement."""

    state: LayoutState = Field(
        description="default: nothing stored, draw the default; custom: the owner's "
        "arrangement; unreadable: something is stored that this build cannot read"
    )
    layout: PageLayoutDocument | None = Field(description="The arrangement, when custom")
    updated_at: datetime | None = Field(description="When it was saved")


class LayoutErrorBody(BaseModel):
    """Why an arrangement was refused."""

    code: str
    message: str


class LayoutErrorResponse(BaseModel):
    """A refused arrangement, in the archive's own envelope."""

    error: LayoutErrorBody


def _layouts(request: Request) -> PageLayouts:
    """Return the layout use case the composition root wired into the app."""
    return cast(PageLayouts, request.app.state.page_layouts)


def _grid_document(grid: LayoutGrid) -> LayoutGridDocument:
    """Shape one grid for HTTP."""
    return LayoutGridDocument(
        columns=grid.columns,
        tiles=[
            LayoutTileDocument(
                widget=tile.widget, x=tile.x, y=tile.y, width=tile.width, height=tile.height
            )
            for tile in grid.tiles
        ],
        hidden=list(grid.hidden),
    )


def _project(reading: LayoutReading) -> PageLayoutResponse:
    """Shape a reading for HTTP."""
    layout = reading.layout
    return PageLayoutResponse(
        state=reading.state,
        layout=None
        if layout is None
        else PageLayoutDocument(
            wide=None if layout.wide is None else _grid_document(layout.wide),
            medium=None if layout.medium is None else _grid_document(layout.medium),
            narrow=None if layout.narrow is None else _grid_document(layout.narrow),
        ),
        updated_at=reading.updated_at,
    )


def _grid(document: LayoutGridDocument) -> LayoutGrid:
    """Return the domain grid a document describes, validated by the domain."""
    return LayoutGrid(
        columns=document.columns,
        tiles=tuple(
            LayoutTile(widget=tile.widget, x=tile.x, y=tile.y, width=tile.width, height=tile.height)
            for tile in document.tiles
        ),
        hidden=tuple(document.hidden),
    )


@router.get("/track-detail", summary="How the owner arranged the track page")
def read_track_layout(request: Request) -> PageLayoutResponse:
    """Return the stored arrangement, or say why the page should draw its default.

    Always ``200``: no arrangement and an unreadable one are answers about the
    archive, not failed requests.
    """
    return _project(_layouts(request).read(LayoutPage.TRACK_DETAIL))


@router.put(
    "/track-detail",
    summary="Store the owner's arrangement of the track page",
    responses={422: {"model": LayoutErrorResponse, "description": "Not a drawable grid"}},
)
def save_track_layout(request: Request, document: PageLayoutDocument) -> PageLayoutResponse:
    """Store a whole arrangement, replacing the previous one.

    A tile that overlaps another, a widget named twice, a tile hanging off the
    grid or a document that arranges no width class at all is refused with
    ``layout_invalid`` and nothing is stored.
    """
    try:
        layout = PageLayout(
            wide=None if document.wide is None else _grid(document.wide),
            medium=None if document.medium is None else _grid(document.medium),
            narrow=None if document.narrow is None else _grid(document.narrow),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": {"code": "layout_invalid", "message": str(error)}},
        ) from error
    return _project(_layouts(request).save(LayoutPage.TRACK_DETAIL, layout))


@router.delete("/track-detail", summary="Forget the arrangement of the track page")
def reset_track_layout(request: Request) -> PageLayoutResponse:
    """Remove the stored arrangement. The page draws its default again."""
    return _project(_layouts(request).reset(LayoutPage.TRACK_DETAIL))
