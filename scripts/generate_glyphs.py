r"""Generate the SDF glyph ranges the offline basemap draws its labels with.

A vector tile carries the *text* of a place name and nothing about how to draw
it. MapLibre asks for signed-distance-field glyph ranges over HTTP, and if those
come from a font service then a self-hosted archive still tells a stranger which
towns somebody walked through. So the glyphs are generated here, committed, and
served from this deployment.

```
Noto Sans (SIL OFL 1.1)
        │  this script, run by hand when a range has to change
        ▼
web/public/fonts/<stack>/<start>-<end>.pbf
        │  Vite copies public/ verbatim
        ▼
/fonts/{fontstack}/{range}.pbf        same origin, in the image
```

**Why the output is committed rather than built.** It is a few hundred
kilobytes, it changes when somebody decides it should and never otherwise, and
building it would put a font rasteriser in the runtime image to produce bytes
that are identical every time.

**Which ranges.** Latin-1, Latin Extended-A and Latin Extended-B, plus General
Punctuation. That covers Western and Central European place and street names --
umlauts, cedillas, the Nordic vowels, Polish, Czech and Hungarian diacritics,
Romanian's comma-below letters, the typographic apostrophe and the en dash.
Greek, Cyrillic, Vietnamese and CJK are deliberately absent and documented as a
limitation: a package for a region that needs them renders those labels blank
until the ranges here are extended.

**The rasteriser is not a project dependency.** Pillow and fontTools are needed
by this script and by nothing else, so they are installed for the length of one
run instead of being added to a lock file the runtime image is built from:

    uv run --with pillow --with fonttools python scripts/generate_glyphs.py \\
        --regular /usr/share/fonts/truetype/noto/NotoSans-Regular.ttf \\
        --bold    /usr/share/fonts/truetype/noto/NotoSans-Bold.ttf

On Debian and Ubuntu those paths come from `fonts-noto-core`. The upstream
release is at https://notofonts.github.io/ under the SIL Open Font License 1.1,
which is what permits redistributing the derived glyphs -- see
`docs/legal/third-party-notices.md`.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

FONT_SIZE = 24
"""The em size glyphs are rasterised at. The size every SDF renderer assumes."""

BUFFER = 3
"""Pixels of padding around a glyph's ink box, inside the stored bitmap.

MapLibre calls it `GLYPH_PBF_BORDER` and subtracts it when it places the quad,
so it is part of the format rather than a choice.
"""

RADIUS = 8
"""How far, in pixels, the distance field is meaningful."""

CUTOFF = 0.25
"""Where the glyph edge sits in the stored byte range. 0.25 puts it at ~191."""

SUPERSAMPLE = 3
"""How much finer the rasterisation is than the stored field.

A distance transform over a hard 24-pixel bitmap quantises the edge to whole
pixels, and at this size that is visible as ragged diagonals. Measuring at three
times the resolution and dividing costs a one-off script nine times nothing.
"""

FAR = 1e20
"""A distance no glyph can reach, standing in for infinity.

The envelope arithmetic subtracts two of these, and `inf - inf` is not a number
any of the comparisons can order.
"""

MARGIN = 2
"""Slack, in stored pixels, around the buffered box while rasterising.

The 72-pixel render's ink box is only approximately three times the 24-pixel
one; rounding can put a stem a fraction outside. The margin absorbs it and is
cropped away.
"""

RANGES: tuple[tuple[int, int], ...] = (
    (0, 255),  # Basic Latin and Latin-1 Supplement: ä ö ü ß é à ñ å ø
    (256, 511),  # Latin Extended-A: ł č ő ū ș ż
    (512, 767),  # Latin Extended-B: Ș Ț ǎ ǧ
    (8192, 8447),  # General Punctuation: en dash, em dash, curly quotes, ellipsis
)

STACKS: tuple[tuple[str, str], ...] = (
    ("Noto Sans Regular", "regular"),
    ("Noto Sans Bold", "bold"),
)

SKIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
"""Categories that are not glyphs: controls, formatting, surrogates, unassigned."""


@dataclass(frozen=True, slots=True)
class Glyph:
    """One rasterised glyph, in the shape the protocol buffer stores.

    Attributes:
        code: The Unicode code point.
        width: Ink box width, without the buffer.
        height: Ink box height, without the buffer.
        left: Horizontal bearing, in pixels from the pen position.
        top: Vertical bearing, in pixels above the baseline.
        advance: How far the pen moves afterwards.
        bitmap: The distance field, `(width + 2 * BUFFER) * (height + 2 * BUFFER)`
            bytes, row major. Empty for a glyph with no ink, such as a space.
    """

    code: int
    width: int
    height: int
    left: int
    top: int
    advance: int
    bitmap: bytes


def main(argv: list[str] | None = None) -> int:
    """Generate every range of every stack into the output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regular", type=Path, required=True, help="Noto Sans Regular TTF")
    parser.add_argument("--bold", type=Path, required=True, help="Noto Sans Bold TTF")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "web" / "public" / "fonts",
        help="Where the font stacks are written",
    )
    arguments = parser.parse_args(argv)

    sources = {"regular": arguments.regular, "bold": arguments.bold}
    total = 0
    for stack, key in STACKS:
        source = sources[key]
        if not source.is_file():
            parser.error(f"no such font file: {key}")
        directory = arguments.output / stack
        directory.mkdir(parents=True, exist_ok=True)
        covered = set(TTFont(source, fontNumber=0).getBestCmap())
        font = ImageFont.truetype(str(source), FONT_SIZE)
        large = ImageFont.truetype(str(source), FONT_SIZE * SUPERSAMPLE)
        for start, end in RANGES:
            glyphs = [
                glyph
                for code in range(start, end + 1)
                if code in covered and _is_drawable(code)
                if (glyph := _render(font, large, code)) is not None
            ]
            payload = _encode(stack, f"{start}-{end}", glyphs)
            (directory / f"{start}-{end}.pbf").write_bytes(payload)
            total += len(payload)
            sys.stdout.write(
                f"{stack}/{start}-{end}.pbf  {len(glyphs):4d} glyphs  {len(payload):7d} bytes\n"
            )
    sys.stdout.write(f"total {total} bytes\n")
    return 0


def _is_drawable(code: int) -> bool:
    """Report whether a code point is something a label can contain."""
    if code == 0x20:
        return True
    return unicodedata.category(chr(code)) not in SKIPPED_CATEGORIES


def _render(font: ImageFont.FreeTypeFont, large: ImageFont.FreeTypeFont, code: int) -> Glyph | None:
    """Rasterise one glyph and turn it into a distance field."""
    character = chr(code)
    # `ls` anchors at the baseline origin, so the box is measured the way a
    # font does it: y negative above the baseline.
    left, top, right, bottom = font.getbbox(character, anchor="ls")
    advance = round(font.getlength(character))
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        if advance <= 0:
            return None
        # A space has an advance and no ink. It still has to be in the range:
        # without it, MapLibre has no width for the gaps between words.
        return Glyph(code, 0, 0, 0, 0, advance, b"")

    field = _distance_field(large, character, left, top, width, height)
    return Glyph(code, width, height, left, -top, advance, field)


def _distance_field(
    large: ImageFont.FreeTypeFont, character: str, left: int, top: int, width: int, height: int
) -> bytes:
    """Return the signed distance field of one glyph, at stored resolution."""
    box_width, box_height = width + 2 * BUFFER, height + 2 * BUFFER
    canvas_width = (box_width + 2 * MARGIN) * SUPERSAMPLE
    canvas_height = (box_height + 2 * MARGIN) * SUPERSAMPLE
    image = Image.new("L", (canvas_width, canvas_height), 0)
    ImageDraw.Draw(image).text(
        ((MARGIN + BUFFER - left) * SUPERSAMPLE, (MARGIN + BUFFER - top) * SUPERSAMPLE),
        character,
        font=large,
        fill=255,
        anchor="ls",
    )
    inside = [pixel >= 128 for pixel in image.getdata()]
    # Positive outside the glyph, negative within it -- the sign convention
    # every SDF shader expects, and the one the byte encoding below assumes.
    to_ink = _euclidean_distance(inside, canvas_width, canvas_height, target=True)
    to_gap = _euclidean_distance(inside, canvas_width, canvas_height, target=False)

    field = bytearray(box_width * box_height)
    half = SUPERSAMPLE // 2
    for row in range(box_height):
        sample_row = (MARGIN + row) * SUPERSAMPLE + half
        for column in range(box_width):
            index = sample_row * canvas_width + (MARGIN + column) * SUPERSAMPLE + half
            distance = (to_ink[index] - to_gap[index]) / SUPERSAMPLE
            value = round(255 - 255 * (distance / RADIUS + CUTOFF))
            field[row * box_width + column] = max(0, min(255, value))
    return bytes(field)


def _euclidean_distance(mask: list[bool], width: int, height: int, *, target: bool) -> list[float]:
    """Return every pixel's distance to the nearest pixel matching ``target``.

    Felzenszwalb and Huttenlocher's exact transform: one pass down the columns,
    one across the rows, each the lower envelope of a set of parabolas. Exact
    and linear, where a naive nearest-neighbour search is quadratic in the pixel
    count and would make this script take minutes per font.

    ``FAR`` stands in for infinity because the envelope arithmetic subtracts two
    of these, and infinity minus infinity is not a number the comparisons below
    can order.
    """
    grid = [0.0 if pixel is target else FAR for pixel in mask]
    for column in range(width):
        _transform_line(grid, start=column, step=width, count=height)
    for row in range(height):
        _transform_line(grid, start=row * width, step=1, count=width)
    return [value**0.5 for value in grid]


def _transform_line(grid: list[float], *, start: int, step: int, count: int) -> None:
    """Run the one-dimensional squared-distance transform over one line, in place."""
    values = [grid[start + index * step] for index in range(count)]
    vertices = [0] * count
    boundaries = [0.0] * (count + 1)
    boundaries[0] = -FAR
    boundaries[1] = FAR
    rightmost = 0
    for index in range(1, count):
        crossing = _crossing(values, index, vertices[rightmost])
        while crossing <= boundaries[rightmost]:
            rightmost -= 1
            crossing = _crossing(values, index, vertices[rightmost])
        rightmost += 1
        vertices[rightmost] = index
        boundaries[rightmost] = crossing
        boundaries[rightmost + 1] = FAR
    rightmost = 0
    for index in range(count):
        while boundaries[rightmost + 1] < index:
            rightmost += 1
        offset = index - vertices[rightmost]
        grid[start + index * step] = offset * offset + values[vertices[rightmost]]


def _crossing(values: list[float], here: int, there: int) -> float:
    """Return where two parabolas of the lower envelope intersect."""
    return ((values[here] + here * here) - (values[there] + there * there)) / (2 * here - 2 * there)


def _encode(stack: str, span: str, glyphs: list[Glyph]) -> bytes:
    """Return the `glyphs` protocol buffer for one range of one stack.

    ```
    glyphs    { fontstack stacks = 1 }
    fontstack { string name = 1; string range = 2; glyph glyphs = 3 }
    glyph     { uint32 id = 1; bytes bitmap = 2; uint32 width = 3;
                uint32 height = 4; sint32 left = 5; sint32 top = 6;
                uint32 advance = 7 }
    ```

    Written by hand. It is forty lines of varints against a schema that has not
    changed since 2014, and the alternative is a protocol buffer runtime in the
    dependency tree of a script that runs once a year.
    """
    fontstack = _string(1, stack) + _string(2, span)
    for glyph in glyphs:
        body = _varint_field(1, glyph.code)
        if glyph.bitmap:
            body += _bytes(2, glyph.bitmap)
        body += _varint_field(3, glyph.width)
        body += _varint_field(4, glyph.height)
        body += _zigzag(5, glyph.left)
        body += _zigzag(6, glyph.top)
        body += _varint_field(7, glyph.advance)
        fontstack += _bytes(3, body)
    return _bytes(1, fontstack)


def _varint(value: int) -> bytes:
    """Return one unsigned varint."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    """Return a field tag."""
    return _varint((field << 3) | wire)


def _varint_field(field: int, value: int) -> bytes:
    """Return a varint field."""
    return _key(field, 0) + _varint(value)


def _zigzag(field: int, value: int) -> bytes:
    """Return a signed varint field, zigzag encoded as the schema declares."""
    return _key(field, 0) + _varint((value << 1) ^ (value >> 31))


def _bytes(field: int, value: bytes) -> bytes:
    """Return a length-delimited field."""
    return _key(field, 2) + _varint(len(value)) + value


def _string(field: int, value: str) -> bytes:
    """Return a length-delimited string field."""
    return _bytes(field, value.encode("utf-8"))


if __name__ == "__main__":
    sys.exit(main())
