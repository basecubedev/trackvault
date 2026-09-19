"""Reading and keeping the owner's arrangement of a page.

The arrangement is presentation, and it is still the owner's work: it lives in
the archive rather than in one browser, so it is the same on every device and
comes back with a restore. Three answers, and they are kept apart on purpose:

```
default     nothing is stored; the page draws its own default
custom      the owner's arrangement
unreadable  something is stored and this build cannot read it
```

``unreadable`` is not folded into ``default``. The page would then draw the
default as if nobody had arranged anything, and the next save would overwrite
the owner's work without anybody having been told it existed.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from trackvault.application.ports import Clock, PageLayoutRepository
from trackvault.domain.page_layout import LayoutPage, PageLayout


class LayoutState(StrEnum):
    """What the archive holds for a page's arrangement."""

    DEFAULT = "default"
    CUSTOM = "custom"
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class LayoutReading:
    """A page's arrangement, as the archive can state it.

    Attributes:
        state: Which of the three answers this is.
        layout: The arrangement when ``state`` is ``custom``, else ``None``.
        updated_at: When it was saved, or ``None`` when nothing is stored.
    """

    state: LayoutState
    layout: PageLayout | None
    updated_at: datetime | None


DEFAULT_READING = LayoutReading(state=LayoutState.DEFAULT, layout=None, updated_at=None)


class PageLayouts:
    """The one authority for how the owner arranged a page."""

    def __init__(self, repository: PageLayoutRepository, clock: Clock) -> None:
        """Wire the use case to its storage and its source of "now"."""
        self._repository = repository
        self._clock = clock

    def read(self, page: LayoutPage) -> LayoutReading:
        """Return the page's arrangement, or say why there is none to draw."""
        stored = self._repository.read_page_layout(page)
        if stored is None:
            return DEFAULT_READING
        if stored.layout is None:
            return LayoutReading(
                state=LayoutState.UNREADABLE, layout=None, updated_at=stored.updated_at
            )
        return LayoutReading(
            state=LayoutState.CUSTOM, layout=stored.layout, updated_at=stored.updated_at
        )

    def save(self, page: LayoutPage, layout: PageLayout) -> LayoutReading:
        """Store an arrangement, replacing the previous one entirely.

        A whole document rather than a patch: a width class the owner stopped
        arranging must not survive because the new document left it out.
        """
        at = self._clock.now()
        self._repository.save_page_layout(page, layout, at)
        return LayoutReading(state=LayoutState.CUSTOM, layout=layout, updated_at=at)

    def reset(self, page: LayoutPage) -> LayoutReading:
        """Forget the arrangement, so the page draws its default again.

        The default is never stored as a copy. A copy would freeze today's
        default into the archive and hide every later improvement to it behind
        a document the owner never actually arranged.
        """
        self._repository.delete_page_layout(page)
        return DEFAULT_READING
