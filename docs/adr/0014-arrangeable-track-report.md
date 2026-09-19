# ADR 0014: An arrangeable track report, stored by the archive

## Status

Accepted (2026-09-19).

Extends ADR 0008 and ADR 0009, which built the browser application and the
user-owned corrections. Nothing they decided changes: the report is still one
component rendered in two places, the hovered sample still lives outside React,
and browser state is still never business authority. This adds a second kind of
user-owned data — how the owner wants the report laid out — and the rule that
keeps it presentation.

## Context

The track report had one arrangement, for everybody: six metric cards, a map
beside a profile chart, then the classification, the source and the derivation
panels, in that order. It is a defensible order — it is the order somebody
reads a track in — and it is the wrong one for at least two real uses.

Somebody planning routes wants the map large and the three duration figures out
of the way. Somebody comparing efforts wants the numbers and the profile
together and does not look at the source panel from one month to the next. On a
1440-pixel screen the map was 6 of 12 columns whatever the track was, and the
only way to get a bigger one was to stop using the page.

The owner asked for widgets: free sizes, free positions.

Three things had to be decided before any of it could be built.

**How free is free.** Freely positioned, overlapping windows are how a desktop
works and not how a page works. They collide, they leave holes, they have no
answer for a narrower screen, and they cannot be operated by a keyboard.

**Where the arrangement lives.** It is presentation, so the obvious home is the
browser. It is also somebody's work: kept in `localStorage` it would differ per
device, vanish with the browser's data, and miss every backup — and this
project's whole argument about user-owned data is that a correction is not the
archive's to lose.

**What must not be arrangeable.** A report where the analysis notice can be
hidden is a report that quotes a stale number with its caveat torn off.

## Decision

### 1. A grid of whole cells, not floating windows

Twelve columns on a wide screen, rows of one fixed height, and every widget on
whole cells. Dragging snaps; the tiles it lands on move out of the way; nothing
overlaps and nothing floats over a gap.

```
no overlap    two tiles never share a cell
no gap        every tile sits as high as the tiles above it allow
one place     every widget is placed or hidden, exactly once
```

A tile is exactly the size its owner chose, and content that does not fit
scrolls inside it — "resize it and the text reflows" would be a page that
overrules the size somebody just set. The exception is a narrow screen, where
rows grow to fit instead: a scrolling box inside a scrolling phone is nobody's
choice.

Three rules the arrangement follows in one direction only. A tile dragged
*down* past a neighbour hops that neighbour into the room it left, because
otherwise the neighbour would be pushed ahead of it and everything would rise
back to where it started. A tile dropped exactly onto one of the same size
trades places with it. Everything else is pushed below.

The arithmetic is `web/src/layout/grid.ts` — pure functions over numbers,
tested as invariants (nothing overlaps, nothing is lost, nothing can still
rise) rather than as pinned coordinates, the way the movement and elevation
algorithms are. The board component draws the result and decides nothing.

### 2. No new dependency

`react-grid-layout` and `gridstack.js` both do this. The rules put a small
abstraction of our own ahead of a new dependency, and the cost here is genuinely
small: the grid arithmetic is a few hundred lines of pure functions, the drag is
pointer events, and the part that neither library gives for free — arrow-key
moving, resizing and announcements — had to be written either way.

### 3. The arrangement is stored by the archive, and it is presentation

`page_layouts` (schema 11) holds one document per page beside a format version,
and `/api/v1/layouts/track-detail` reads, replaces and forgets it. Because it is
a row in the archive's database, a backup carries it and a restore brings it
back, with no second storage mechanism and no export format to extend.

The archive vouches for the *shape* and nothing else:

```
bounded        columns, positions, sizes and how many widgets a grid may name
once           every widget placed or hidden exactly once
disjoint       no two tiles on one cell
plain keys     a widget is named `map` or `metric.distance`, never markup
```

What a widget *means* is the page's vocabulary, exactly as the colour of a map
line is. A backend that validated widget names would be a backend holding a
design system — the same mistake as assembling a MapLibre style in Python, which
ADR 0008 refused for the same reason.

A stored document is decoded through the domain types and anything that fails —
malformed text, a field of the wrong type, a grid the domain refuses, or nesting
deeper than the parser's own stack — is reported as unreadable rather than
raised. A damaged timestamp costs the reading its date and nothing else: the
arrangement is the work, when it was saved is a detail.

Three answers come back, and `unreadable` is deliberately not folded into
`default`: a stored document this build cannot read is reported as unreadable,
left in place, and the page says so. Silently drawing the default would let the
next save overwrite work nobody was told was there.

Reset stores nothing rather than storing a copy of the default — a copy would
freeze today's default into the archive and hide every later improvement behind
a layout the owner never made.

### 4. The report says what it says, wherever the widgets are

The title, the state badges, the analysis notice and the timing caveat are above
the board and cannot be moved or hidden. Map attribution is inside the map
widget: the map can go anywhere, its credit goes with it. Arranging the page
changes where a number is drawn, never which number it is.

### 5. Reconciled with the widgets this build has

A stored arrangement is untrusted input on the way out, the same way a stored
analysis is:

```
a widget this page no longer has     forgotten
a widget a later release added       shown, below what the owner arranged
a size a widget cannot be read at    brought back inside its limits
a grid with another column count     re-flowed, keeping its order
```

A new widget is *shown*, never silently hidden: the arrangement records what the
owner hid on purpose, and a widget they have never seen is not one of those.
Normalising is idempotent, so reading, drawing and saving again never drifts.

### 6. One arrangement per width class, derived until it is made

The width that decides is the width of the area the report is drawn in, not the
window's: the same report is a page of its own and a row in the track list.

```
wide     >= 960px   12 columns
medium   >= 600px    8 columns
narrow    < 600px    2 columns -- every panel full width, small cards in pairs
```

Every class is optional, the widest included. A class nobody arranged is drawn
by the page: its own default, or — for a narrower class — a derivation of the
wide arrangement, same order with each widget at its own size for that width.
Shrinking the wide sizes proportionally would give a phone a map one twelfth of
a screen wide.

A class is stored only once the owner arranges *that* class. Somebody who only
rearranges their phone therefore stores a narrow arrangement and no wide one,
and a later improvement to the wide default still reaches them — the same
argument as reset storing nothing, applied per width class rather than per
document. A document that arranges nothing at all is refused: that is what
forgetting the arrangement is for.

### 7. Three ways to arrange, and undo for all of them

Pointer: drag by a widget's bar, resize by its corner, with the target cell
shown under a dashed outline. Keyboard: focus a bar and use the arrow keys —
Shift to resize — with the new position announced through a live region. Menu:
Small, Medium, Large, Full width and Hide, one click each, for anybody who does
not want to drag anything precisely.

Reading is the default and nothing moves by accident: arranging starts with a
button and ends with Done or Cancel. While arranging, widgets stay visible but
inert, so dragging a map's tile cannot pan the map. Every step is undoable,
Cancel discards, and a click on a link with unsaved changes asks first.

The document order while arranging is the order the tiles had when it began, so
a keyboard move never pulls the focused bar out from under the reader and a drag
never re-inserts a map's canvas somewhere else in the page. Reading order is
restored when arranging ends.

### 8. Charts follow their box

`Chart` observed the *window* for resizes, which is exactly wrong for a widget:
the window never changes and the box does. It observes its own container now.
The map already did.

## Consequences

- The report keeps its old arrangement as the default, so nothing changes for
  anybody who never opens the editor, and the existing browser tests still
  describe the page they were written for.
- One arrangement per deployment. There is no authentication (ADR 0001), so
  several people sharing an installation share the arrangement — the same
  assumption the title corrections already make. Two tabs arranging at once
  overwrite each other, last save wins; there is no version check, for the
  same reason there is none on a title.
- One arrangement for every track, rather than per track or per kind. A planned
  route shows a dash where a recording shows a duration, which is what "missing
  is not zero" already means; whether planned routes deserve their own
  arrangement is a question worth real use before it is answered.
- A widget added in a later release lands at the bottom of an arranged page.
  That is deliberate and slightly untidy: the alternative is a widget nobody
  discovers.
- The board is generic. Nothing in `web/src/layout` knows what a track is, so a
  second arrangeable page would be a second catalog — but no second page has
  asked for one, and none is built.

## Alternatives considered

**Floating, freely positioned windows.** Rejected: overlapping panels, no
answer for a narrower screen, and no keyboard story.

**`localStorage`.** Rejected: per device, lost with the browser's data, absent
from every backup. It is the owner's work, and the archive is where this
project puts the owner's work.

**A widget catalog in the backend.** Rejected: it would put the page's design
vocabulary into the API, and every new widget would become a migration.

**Per-track arrangements.** Deferred: it multiplies the thing to keep and makes
"why does this track look different?" a question with an invisible answer.

**Templates ("map first", "analysis", "compact").** Deferred until there is
evidence that arranging once is too much work; the default and the per-widget
sizes cover most of it.
