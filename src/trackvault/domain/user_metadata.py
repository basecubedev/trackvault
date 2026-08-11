"""What the *user* says about a track, beside what its source said.

An archive of somebody's own movements is only pleasant to keep once they can
correct it. Exports arrive titled `2024-06-11 09:14`, `Track`, `Morning Ride`
three times over, or with no title at all, and none of that is something the
archive may fix by guessing.

The correction is therefore user-owned data with its own authority, and it sits
*beside* the source rather than on top of it:

```
source title       what the document said        never modified
title override     what the user said            authoritative for display
effective title    the override, else the source  a projection of the two
```

That is deliberately the same shape as the classification override, and for the
same reason: two independently writable titles would be two authorities that can
disagree, and reprocessing would silently pick one. The raw import stays
byte-identical, the normalized projection keeps stating what the file said, and
the user's correction survives both because it was never part of either.

Everything here is **plain text**. The archive stores what was typed and renders
it as text; nothing interprets it as markup, and no field is an HTML authority.
Control characters are refused rather than stripped -- a title that silently
loses a character is a title the user did not write.
"""

from dataclasses import dataclass

MAX_TITLE_LENGTH = 200
"""Longest accepted track title.

Long enough for a sentence about a day out, short enough that a listing row
stays a row. The limit applies to what was sent, whitespace included.
"""

MAX_NOTE_LENGTH = 4000
"""Longest accepted note.

A few paragraphs. This is a place for "the GPS lost the fix in the tunnel", not
a journal, and an unbounded text field on an unauthenticated endpoint is a
storage-growth decision nobody made.
"""


def normalize_title(value: str | None) -> str | None:
    """Return a title as it will be stored, or ``None`` for no title at all.

    Surrounding whitespace is removed and a value that is empty afterwards is
    ``None``: typing spaces into the field is how somebody clears it, and
    storing `"  "` would be storing a title that renders as nothing and hides
    the source title behind it.

    Args:
        value: What the user typed, or ``None``.

    Returns:
        The stored form, or ``None`` when there is no title.

    Raises:
        ValueError: If the title is too long, or carries a control character. A
            title is one line of text; a newline in it is a shape the caller did
            not mean and every listing would have to cope with.
    """
    if value is None:
        return None
    if len(value) > MAX_TITLE_LENGTH:
        raise ValueError(f"a title may hold at most {MAX_TITLE_LENGTH} characters")
    stripped = value.strip()
    if not stripped:
        return None
    if any(character.isprintable() is False for character in stripped):
        raise ValueError("a title is a single line of printable text")
    return stripped


def normalize_note(value: str | None) -> str | None:
    """Return a note as it will be stored, or ``None`` for no note.

    Line breaks are kept -- a note is prose and paragraphs are part of it -- and
    every other control character is refused for the same reason a title refuses
    them.

    Args:
        value: What the user typed, or ``None``.

    Returns:
        The stored form, or ``None`` when there is no note.

    Raises:
        ValueError: If the note is too long or carries a control character other
            than a line break.
    """
    if value is None:
        return None
    if len(value) > MAX_NOTE_LENGTH:
        raise ValueError(f"a note may hold at most {MAX_NOTE_LENGTH} characters")
    stripped = value.strip()
    if not stripped:
        return None
    if any(not character.isprintable() and character != "\n" for character in stripped):
        raise ValueError("a note is printable text and line breaks")
    return stripped


@dataclass(frozen=True, slots=True)
class UserTrackMetadata:
    """The corrections and notes one track carries from its owner.

    Both fields are optional and both are independently clearable. A value that
    is ``None`` is *absent*, not empty: absent means the source keeps speaking
    for the track, which is what makes resetting a title a real operation rather
    than an override that says nothing.

    Attributes:
        title: What the user calls this track, or ``None`` to let the source
            title stand.
        note: A free-text note, or ``None``.
    """

    title: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        """Refuse a value that was not normalized on the way in.

        Validation lives in :func:`normalize_title` and :func:`normalize_note`,
        and this is the check that no path skipped them. A stored value that
        never passed them would be a second, laxer contract reachable from
        whichever caller forgot.

        Raises:
            ValueError: If either field is not in its stored form.
        """
        if self.title != normalize_title(self.title):
            raise ValueError("a stored title must already be normalized")
        if self.note != normalize_note(self.note):
            raise ValueError("a stored note must already be normalized")

    @property
    def is_empty(self) -> bool:
        """Report whether the user has said nothing about this track.

        Empty metadata is not stored. A row holding two nulls would be a
        correction that corrects nothing, and it would make "has the user
        touched this track?" answerable two different ways.
        """
        return self.title is None and self.note is None


EMPTY_USER_METADATA = UserTrackMetadata()
"""What a track carries before anybody has said anything about it."""


def effective_title(source_title: str | None, metadata: UserTrackMetadata) -> str | None:
    """Return the title to display, honouring an explicit user correction.

    The one authority for "what is this track called". A projection of two
    stored values and never a third stored value of its own -- exactly as
    ``TrackClassification.effective_kind`` projects the detected kind and the
    override.

    ``None`` is a real answer: some documents genuinely name nothing, and
    inventing a name here would put a guess where a presentation layer can put
    something honest and say that it did.
    """
    return metadata.title if metadata.title is not None else source_title


__all__ = [
    "EMPTY_USER_METADATA",
    "MAX_NOTE_LENGTH",
    "MAX_TITLE_LENGTH",
    "UserTrackMetadata",
    "effective_title",
    "normalize_note",
    "normalize_title",
]
