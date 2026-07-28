from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
import unicodedata
from statistics import median
from typing import Iterable, Sequence


@dataclass(frozen=True)
class LayoutLine:
    x: float
    y: float
    width: float
    height: float
    text: str
    text_color: str | None = None
    text_rgb: tuple[int, int, int] | None = None
    background_color: str | None = None
    background_rgb: tuple[int, int, int] | None = None
    font_family: str | None = None
    font_size: float | None = None
    bold: bool | None = None
    bold_score: float | None = None
    ink_height: float | None = None
    ink_top: float | None = None
    baseline_offset: float | None = None

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass
class LayoutBlock:
    x: float
    y: float
    width: float
    height: float
    text: str
    lines: list[LayoutLine] = field(default_factory=list)
    role: str = "body"
    alignment: str = "left"
    # Row of a menu / navigation / option list: never a heading, never reflowed.
    list_item: bool = False
    # Which uniform run this row belongs to (0 = none). The run was proven to
    # share one glyph size, so paint must not measure its rows apart again.
    list_run: int = 0
    ink_height: float | None = None
    text_color: str | None = None
    text_rgb: tuple[int, int, int] | None = None
    background_color: str | None = None
    background_rgb: tuple[int, int, int] | None = None
    font_family: str | None = None
    font_size: float | None = None
    bold: bool | None = None
    baseline_offset: float | None = None
    region_id: int = 0
    source_id: int = 0

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def median_line_height(self) -> float:
        values = [
            _normalized_ink(line) or line.font_size or line.height
            for line in self.lines
            if (_normalized_ink(line) or line.font_size or line.height) > 0
        ]
        return float(median(values)) if values else self.height

    def as_pending_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "text": self.text,
            "role": self.role,
            "list_item": self.list_item,
            "list_run": self.list_run,
            "alignment": self.alignment,
            "text_color": self.text_color,
            "text_rgb": self.text_rgb,
            "background_color": self.background_color,
            "background_rgb": self.background_rgb,
            "font_family": self.font_family,
            "font_size": self.font_size,
            "bold": self.bold,
            "baseline_offset": self.baseline_offset,
            "ink_height": self.ink_height,
            "region_id": self.region_id,
            "source_id": self.source_id,
            "lines": [
                {
                    "x": line.x,
                    "y": line.y,
                    "width": line.width,
                    "height": line.height,
                    "text": line.text,
                    "text_color": line.text_color,
                    "text_rgb": line.text_rgb,
                    "background_color": line.background_color,
                    "background_rgb": line.background_rgb,
                    "font_family": line.font_family,
                    "font_size": line.font_size,
                    "bold": line.bold,
                    "bold_score": line.bold_score,
                    "ink_height": line.ink_height,
                    "ink_top": line.ink_top,
                    "baseline_offset": line.baseline_offset,
                }
                for line in self.lines
            ],
        }


def _as_line(value) -> LayoutLine:
    if isinstance(value, LayoutLine):
        return value
    if isinstance(value, dict):
        return LayoutLine(
            float(value["x"]),
            float(value["y"]),
            float(value.get("width", value.get("w", 0))),
            float(value.get("height", value.get("h", 0))),
            str(value.get("text", "")),
            value.get("text_color"),
            tuple(value["text_rgb"]) if value.get("text_rgb") is not None else None,
            value.get("background_color"),
            tuple(value["background_rgb"])
            if value.get("background_rgb") is not None
            else None,
            value.get("font_family"),
            float(value["font_size"]) if value.get("font_size") is not None else None,
            value.get("bold"),
            float(value["bold_score"])
            if value.get("bold_score") is not None
            else None,
            float(value["ink_height"])
            if value.get("ink_height") is not None
            else None,
            float(value["ink_top"]) if value.get("ink_top") is not None else None,
            float(value["baseline_offset"])
            if value.get("baseline_offset") is not None
            else None,
        )
    x, y, width, height, text = value[:5]
    return LayoutLine(float(x), float(y), float(width), float(height), str(text))


def _color_distance(first, second) -> float:
    if first is None or second is None:
        return 0.0
    return float(sum(abs(int(a) - int(b)) for a, b in zip(first, second)))


def _style_compatible(
    first: LayoutLine,
    second: LayoutLine,
    *,
    same_row: bool = False,
    mixed_weight: bool = False,
) -> bool:
    if same_row:
        # Words sharing a baseline are the same line by construction; their ink
        # heights differ because of which letters they contain, not the font.
        # Only a gross mismatch means two different runs of type.
        first_size = first.ink_height or first.font_size or first.height
        second_size = second.ink_height or second.font_size or second.height
    else:
        first_size = _normalized_ink(first) or first.font_size or first.height
        second_size = _normalized_ink(second) or second.font_size or second.height
    size_ratio = max(first_size, second_size) / max(1.0, min(first_size, second_size))
    color_delta = _color_distance(first.text_rgb, second.text_rgb)
    bold_delta = (
        abs(first.bold_score - second.bold_score)
        if first.bold_score is not None and second.bold_score is not None
        else None
    )

    # UI typography often separates a heading from its body with several
    # individually subtle signals.  Looking at each signal in isolation used
    # to merge Discord's bright/bold username into the slightly dimmer message
    # and card titles into same-colour body copy.
    if size_ratio > (1.45 if same_row else 1.14):
        return False
    if color_delta > 45:
        return False
    # A bullet routinely opens bold and continues regular, so the weight
    # averaged over its first line says nothing about the lines that follow.
    if not mixed_weight:
        if bold_delta is not None and bold_delta > 0.045:
            return False
        if (
            not same_row
            and size_ratio > 1.07
            and bold_delta is not None
            and bold_delta > 0.025
        ):
            return False
    if (
        not mixed_weight
        and first.bold is not None
        and second.bold is not None
        and first.bold != second.bold
    ):
        return False
    return True


_LIST_MARKER = re.compile(
    r"^\s*(?:[•·∙◦▪▫●○◆■□*+–—-]|\.|\d{1,3}[.)]|[a-zA-Z][.)])\s+"
)


def _starts_new_list_item(text: str) -> bool:
    """A bullet or numbering opens a new item, however tight the leading is.

    OCR usually reports the bullet glyph as '.' or '*', so the marker survives
    into the text even when the dot itself was never given its own box.
    """

    return bool(_LIST_MARKER.match(text or ""))


def _luminance(rgb) -> "float | None":
    if not rgb:
        return None
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


# UI headings are set brighter than the body copy they introduce, often at the
# same size and with a weight difference too subtle to measure from pixels.
_HEADING_LUMINANCE_LEAD = 40.0
# Past this the row below is plainly secondary text — a caption, a byline, an
# issue's metadata — however short it happens to be.
_HEADING_LUMINANCE_DROP = 80.0


def _mode(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def _median_rgb(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return tuple(round(median(channel)) for channel in zip(*values))


def _rgba(rgb):
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, 255)" if rgb else None


def _merge_line_atoms(atoms: Sequence[LayoutLine], separator: str) -> LayoutLine:
    atoms = sorted(atoms, key=lambda atom: (atom.x, atom.y))
    x1 = min(atom.x for atom in atoms)
    y1 = min(atom.y for atom in atoms)
    x2 = max(atom.right for atom in atoms)
    y2 = max(atom.bottom for atom in atoms)
    baselines = [
        atom.y + atom.baseline_offset
        for atom in atoms
        if atom.baseline_offset is not None
    ]
    text_rgb = _median_rgb(atom.text_rgb for atom in atoms)
    background_rgb = _median_rgb(atom.background_rgb for atom in atoms)
    return LayoutLine(
        x=x1,
        y=y1,
        width=max(1.0, x2 - x1),
        height=max(1.0, y2 - y1),
        text=separator.join(atom.text.strip() for atom in atoms if atom.text.strip()),
        text_color=_rgba(text_rgb),
        text_rgb=text_rgb,
        background_color=_rgba(background_rgb),
        background_rgb=background_rgb,
        font_family=_mode(atom.font_family for atom in atoms),
        font_size=(
            float(median([atom.font_size for atom in atoms if atom.font_size is not None]))
            if any(atom.font_size is not None for atom in atoms)
            else None
        ),
        bold=_mode(atom.bold for atom in atoms),
        bold_score=(
            float(median([atom.bold_score for atom in atoms if atom.bold_score is not None]))
            if any(atom.bold_score is not None for atom in atoms)
            else None
        ),
        ink_height=(
            float(median([atom.ink_height for atom in atoms if atom.ink_height is not None]))
            if any(atom.ink_height is not None for atom in atoms)
            else None
        ),
        ink_top=(
            min(atom.y + (atom.ink_top or 0.0) for atom in atoms) - y1
            if any(atom.ink_top is not None for atom in atoms)
            else None
        ),
        baseline_offset=(float(median(baselines)) - y1 if baselines else None),
    )


def merge_ocr_atoms(
    values: Iterable,
    *,
    vertical: bool = False,
    separator: str = " ",
) -> list[LayoutLine]:
    """Merge OCR words into visual lines without crossing a style boundary."""

    atoms = [atom for atom in map(_as_line, values) if atom.text.strip()]
    if not atoms or vertical:
        return sorted(atoms, key=lambda atom: (-atom.x, atom.y) if vertical else (atom.y, atom.x))

    typical_height = max(1.0, float(median([atom.height for atom in atoms])))
    row_groups: list[list[LayoutLine]] = []
    for atom in sorted(atoms, key=lambda value: (value.center_y, value.x)):
        best = None
        best_delta = None
        for row in row_groups:
            representative = row[0]
            overlap = max(0.0, min(representative.bottom, atom.bottom) - max(representative.y, atom.y))
            overlap_ratio = overlap / max(1.0, min(representative.height, atom.height))
            center_delta = abs(representative.center_y - atom.center_y)
            if overlap_ratio < 0.45 and center_delta > max(3.0, typical_height * 0.38):
                continue
            if best_delta is None or center_delta < best_delta:
                best = row
                best_delta = center_delta
        if best is None:
            row_groups.append([atom])
        else:
            best.append(atom)

    lines = []
    for row in row_groups:
        ordered = sorted(row, key=lambda atom: atom.x)
        chunks: list[list[LayoutLine]] = []
        for atom in ordered:
            if not chunks:
                chunks.append([atom])
                continue
            previous = chunks[-1][-1]
            gap = atom.x - previous.right
            # A word space is a fraction of the line height; anything wider is
            # the space between two UI elements that happen to share a row
            # (nav tabs, a label and its value), not one run of prose.
            max_gap = max(4.0, min(previous.height, atom.height) * 0.60)
            if gap <= max_gap and _style_compatible(previous, atom, same_row=True):
                chunks[-1].append(atom)
            else:
                chunks.append([atom])
        lines.extend(_merge_line_atoms(chunk, separator) for chunk in chunks)
    return sorted(lines, key=lambda line: (line.y, line.x))


# Vertical rhythm, expressed in glyph heights rather than OCR box heights.
# Body copy sets its lines at roughly 1.2-1.7 glyph heights; menu rows, option
# lists and stacked captions sit far looser than that.
_PARAGRAPH_LEADING = 1.75
_LIST_LEADING = 2.05
_CONCLUSIVE_LIST_LEADING = 2.60
# A wrapped line fills most of the measure; only the last one may be short.
_MEASURE_FILL = 0.82


_ASCENDER_LETTERS = set("bdfhijklt")
_DESCENDER_LETTERS = set("gjpqy")
_DOT_BELOW = "̣"
_BASELINE_PUNCT = set(".,_ '\"`^~·‘’“”")
# What fraction of the em box a string's ink actually covers, by the zones it
# reaches. Measured ink is not a font size until it is divided by this.
_ZONE_FULL = 1.00
_ZONE_ASCENDER = 0.78
_ZONE_DESCENDER = 0.74
_ZONE_XHEIGHT = 0.54


def _ink_zone_factor(text: str) -> float:
    """Share of the em box the glyphs of ``text`` can possibly ink.

    An ink-row scan measures the letters that happen to be in the string, so
    "Privacy" (descender) comes out a third taller than "General" in the very
    same font. Comparing raw ink heights therefore invents size differences —
    and with them phantom headings. Dividing by this factor removes the letters
    from the measurement and leaves the font.
    """

    stripped = (text or "").strip()
    if not stripped:
        return _ZONE_FULL
    for char in stripped:
        # CJK / Kana / Hangul fill the em box regardless of the characters used.
        if "぀" <= char <= "鿿" or "가" <= char <= "힯":
            return _ZONE_FULL
    above = False
    below = False
    for char in unicodedata.normalize("NFD", stripped):
        if unicodedata.combining(char):
            if char == _DOT_BELOW:
                below = True
            else:
                above = True
            continue
        if char.isspace() or char in _BASELINE_PUNCT:
            continue
        if char in _DESCENDER_LETTERS:
            below = True
        if char in _ASCENDER_LETTERS:
            above = True
        elif not char.islower():
            # Capitals, digits and most symbols all reach the ascender line.
            above = True
    if above and below:
        return _ZONE_FULL
    if above:
        return _ZONE_ASCENDER
    if below:
        return _ZONE_DESCENDER
    return _ZONE_XHEIGHT


def _normalized_ink(line: LayoutLine) -> "float | None":
    if line.ink_height and line.ink_height > 0:
        return float(line.ink_height) / _ink_zone_factor(line.text)
    return None


def _glyph_size(line: LayoutLine, typical_glyph: float) -> float:
    """Height of the actual glyphs, ignoring OCR box padding.

    Box height is not a usable unit here: engines pad it by wildly different
    amounts, so any threshold expressed in box heights silently grows with the
    padding until a menu's row pitch passes as paragraph leading.
    """

    normalized = _normalized_ink(line)
    if normalized:
        return normalized
    if line.font_size and line.font_size > 0:
        return float(line.font_size)
    return max(1.0, float(typical_glyph))


def _typical_glyph(lines: Sequence[LayoutLine]) -> float:
    """One padding-free glyph height for the whole OCR frame."""

    measured = [
        float(value)
        for value in (_normalized_ink(line) or line.font_size for line in lines)
        if value and value > 0
    ]
    if measured:
        return max(1.0, float(median(measured)))
    box_height = max(1.0, float(median([line.height for line in lines])))
    # Nothing was measured from pixels (no source image). Approximate the em
    # from the average glyph advance, which carries no box padding, and never
    # trust it beyond the box itself.
    advances = [
        line.width / len(line.text.strip()) * 2.0
        for line in lines
        if len(line.text.strip()) >= 4 and line.width > 0
    ]
    if not advances:
        return box_height
    return max(1.0, min(float(median(advances)), box_height))


def _median_pitch(lines: Sequence[LayoutLine]) -> float:
    pitches = [
        second.center_y - first.center_y for first, second in zip(lines, lines[1:])
    ]
    return float(median(pitches)) if pitches else 0.0


def _looks_like_wrapped_paragraph(
    lines: Sequence[LayoutLine], typical_glyph: float
) -> bool:
    """Is this run continuation text, or independent rows stacked apart?"""

    if len(lines) < 2:
        return True
    ordered = sorted(lines, key=lambda line: (line.y, line.x))
    glyph = max(
        1.0,
        float(median([_glyph_size(line, typical_glyph) for line in ordered])),
    )
    pitch = _median_pitch(ordered)
    if pitch <= glyph * _PARAGRAPH_LEADING:
        return True
    if pitch >= glyph * _CONCLUSIVE_LIST_LEADING:
        return False
    if pitch < glyph * _LIST_LEADING:
        return True
    # Ambiguous leading. A wrapped paragraph runs to the same measure on every
    # line but the last, so ragged non-final lines mean these rows were never
    # one flow of text.
    measure = max(line.width for line in ordered)
    ragged = [line for line in ordered[:-1] if line.width < measure * _MEASURE_FILL]
    return len(ragged) * 3 < len(ordered) - 1


def _split_loose_runs(
    lines: Sequence[LayoutLine], typical_glyph: float
) -> list[list[LayoutLine]]:
    """Cut a group at the gaps that are too loose to be paragraph leading."""

    ordered = sorted(lines, key=lambda line: (line.y, line.x))
    glyph = max(
        1.0,
        float(median([_glyph_size(line, typical_glyph) for line in ordered])),
    )
    limit = glyph * _LIST_LEADING
    parts: list[list[LayoutLine]] = [[ordered[0]]]
    for previous, line in zip(ordered, ordered[1:]):
        if line.center_y - previous.center_y > limit:
            parts.append([line])
        else:
            parts[-1].append(line)
    return parts


def _uniform_row_runs(items: Sequence, glyph_of) -> list[list]:
    """Runs of rows sharing a left edge, a glyph size and a constant loose pitch.

    This is the shape of every menu, option list and stat column: no single
    threshold separates one such row from a line of prose, but three or more of
    them in step is unmistakable.
    """

    columns: list[list] = []
    for item in sorted(items, key=lambda value: (value.x, value.y)):
        for column in columns:
            reference = column[0]
            head_glyph, item_glyph = glyph_of(reference), glyph_of(item)
            # A row's left edge wanders when the engine takes an icon or a
            # bullet into some boxes and not others, so allow roughly a glyph
            # and a half — but a list's rows are all set at the same size, and
            # that keeps titles from pooling with the captions beneath them.
            if abs(reference.x - item.x) > max(12.0, head_glyph * 1.60):
                continue
            size_ratio = max(head_glyph, item_glyph) / max(
                1.0, min(head_glyph, item_glyph)
            )
            box_ratio = max(reference.height, item.height) / max(
                1.0, min(reference.height, item.height)
            )
            # As in the run itself: a clipped or faint row measures too little
            # ink, and its box is then the honest witness.
            if size_ratio > 1.25 and box_ratio > 1.20:
                continue
            column.append(item)
            break
        else:
            columns.append([item])

    def compatible(first, second, *, ratio_limit: float = 1.25) -> bool:
        first_glyph, second_glyph = glyph_of(first), glyph_of(second)
        ratio = max(first_glyph, second_glyph) / max(
            1.0, min(first_glyph, second_glyph)
        )
        # A row clipped by the viewport, or drawn faint, measures far less ink
        # than its box can hold. When the boxes agree, the box is the honest
        # witness and the ink is not.
        box_ratio = max(first.height, second.height) / max(
            1.0, min(first.height, second.height)
        )
        if ratio > 1.15 and box_ratio <= 1.20:
            ratio = box_ratio
        return ratio <= ratio_limit

    runs: list[list] = []
    for column in columns:
        if len(column) < 3:
            continue
        # One column can hold two interleaved lists — an issue title and the
        # caption under it repeat down the same edge. Separate them by size
        # first, or every other row breaks the rhythm of the other.
        tiers: list[list] = []
        for item in sorted(column, key=glyph_of):
            if tiers and compatible(tiers[-1][-1], item, ratio_limit=1.12):
                tiers[-1].append(item)
            else:
                tiers.append([item])

        for tier in tiers:
            if len(tier) < 3:
                continue
            tier.sort(key=lambda value: value.y)
            run: list = []
            run_pitch = 0.0
            for item in tier:
                if not run:
                    run, run_pitch = [item], 0.0
                    continue
                previous = run[-1]
                glyph = max(glyph_of(previous), glyph_of(item))
                pitch = (item.y + item.height / 2) - (
                    previous.y + previous.height / 2
                )
                steady = run_pitch == 0.0 or abs(pitch - run_pitch) <= max(
                    4.0, run_pitch * 0.22
                )
                if pitch >= glyph * _LIST_LEADING and steady:
                    run.append(item)
                    run_pitch = (
                        pitch if run_pitch == 0.0 else (run_pitch + pitch) / 2.0
                    )
                    continue
                if len(run) >= 3:
                    runs.append(run)
                run, run_pitch = [item], 0.0
            if len(run) >= 3:
                runs.append(run)
    return runs


def _mark_list_runs(blocks: Sequence[LayoutBlock], typical_glyph: float) -> None:
    """Flag rows of one menu/option list so no member is promoted to a title.

    A navigation list is a run of single-line rows sharing a left edge, a glyph
    size and a constant — loose — pitch. Nothing inside such a run is a heading,
    however much shorter one row happens to be than the row below it.
    """

    # A bulleted item is a list row however many lines it wraps to.
    for block in blocks:
        if block.lines and _starts_new_list_item(block.lines[0].text):
            block.list_item = True

    candidates = [block for block in blocks if len(block.lines) == 1]
    if len(candidates) < 3:
        return
    runs = _uniform_row_runs(
        candidates, lambda block: _glyph_size(block.lines[0], typical_glyph)
    )
    for index, run in enumerate(runs, start=1):
        for member in run:
            member.list_item = True
            member.list_run = index


def _horizontal_overlap_ratio(a: LayoutLine, b: LayoutLine) -> float:
    overlap = max(0.0, min(a.right, b.right) - max(a.x, b.x))
    return overlap / max(1.0, min(a.width, b.width))


def _same_column(
    a: LayoutLine, b: LayoutLine, typical_height: float, *, hanging: bool = False
) -> bool:
    overlap = _horizontal_overlap_ratio(a, b)
    left_delta = abs(a.x - b.x)
    center_delta = abs(a.center_x - b.center_x)
    return (
        # A bullet's later lines hang past the marker, so their left edge sits
        # a marker's width in from the one that opened the item.
        left_delta <= max(4.0, typical_height * (1.40 if hanging else 0.45))
        or (
            center_delta <= max(6.0, typical_height * 0.55)
            and overlap >= 0.45
        )
    )


def _can_append(
    block_lines: Sequence[LayoutLine],
    line: LayoutLine,
    typical_height: float,
    typical_glyph: float = 0.0,
):
    previous = block_lines[-1]
    if _starts_new_list_item(line.text):
        return None
    hanging = _starts_new_list_item(block_lines[0].text)
    if not _same_column(previous, line, typical_height, hanging=hanging):
        return None
    if not _style_compatible(
        previous, line, mixed_weight=hanging
    ) or not _style_compatible(block_lines[0], line, mixed_weight=hanging):
        return None

    gap = line.y - previous.bottom
    # A small negative gap is normal for OCR boxes. A large overlap generally
    # means that the two boxes are separate columns on the same visual row.
    if gap < -0.35 * min(previous.height, line.height):
        return None

    previous_size = _glyph_size(previous, typical_glyph or previous.height)
    line_size = _glyph_size(line, typical_glyph or line.height)
    size_ratio = max(previous_size, line_size) / max(
        1.0, min(previous_size, line_size)
    )
    if size_ratio > 1.17:
        return None

    allowed_gap = max(
        typical_height * 1.30,
        min(previous.height, line.height) * 1.35,
    )
    if gap > allowed_gap:
        return None

    # The gap above is measured in OCR box heights, which include however much
    # padding the engine felt like adding, so it grows with the padding and
    # stops separating a menu's row pitch from paragraph leading. Judge the
    # rhythm in glyph heights as well: they are padding-free.
    if typical_glyph > 0:
        pitch = line.center_y - previous.center_y
        glyph = max(
            _glyph_size(previous, typical_glyph), _glyph_size(line, typical_glyph)
        )
        if pitch > glyph * _CONCLUSIVE_LIST_LEADING:
            return None

    if len(block_lines) >= 2:
        existing_gaps = [
            second.y - first.bottom
            for first, second in zip(block_lines, block_lines[1:])
            if second.y >= first.y
        ]
        if existing_gaps:
            rhythm_limit = max(
                typical_height * 0.55,
                float(median(existing_gaps)) * 2.0 + 3.0,
            )
            if gap > rhythm_limit:
                return None

    left_penalty = abs(previous.x - line.x) / max(1.0, typical_height)
    return gap / max(1.0, typical_height) + left_penalty * 0.20


def _block_alignment(lines: Sequence[LayoutLine]) -> str:
    if len(lines) < 2:
        return "left"
    height = max(1.0, float(median([line.height for line in lines])))
    lefts = [line.x for line in lines]
    rights = [line.right for line in lines]
    centers = [line.center_x for line in lines]
    left_spread = max(lefts) - min(lefts)
    right_spread = max(rights) - min(rights)
    center_spread = max(centers) - min(centers)
    tolerance = max(3.0, height * 0.35)
    if left_spread <= tolerance:
        return "left"
    if right_spread <= tolerance and right_spread < center_spread:
        return "right"
    if center_spread <= tolerance:
        return "center"
    return "left"


def _join_source_text(lines: Sequence[LayoutLine], separator: str) -> str:
    values = [line.text.strip() for line in lines if line.text.strip()]
    if not values:
        return ""
    return separator.join(values)


def _make_block(lines: Sequence[LayoutLine], separator: str) -> LayoutBlock:
    x1 = min(line.x for line in lines)
    y1 = min(line.y for line in lines)
    x2 = max(line.right for line in lines)
    y2 = max(line.bottom for line in lines)
    first = min(lines, key=lambda line: (line.y, line.x))
    font_sizes = [line.font_size for line in lines if line.font_size is not None]
    ink_heights = [
        float(line.ink_height)
        for line in lines
        if line.ink_height is not None and line.ink_height > 0
    ]
    text_rgb = _median_rgb(line.text_rgb for line in lines)
    background_rgb = _median_rgb(line.background_rgb for line in lines)
    return LayoutBlock(
        x=x1,
        y=y1,
        width=max(1.0, x2 - x1),
        height=max(1.0, y2 - y1),
        text=_join_source_text(lines, separator),
        lines=list(lines),
        alignment=_block_alignment(lines),
        text_color=_rgba(text_rgb),
        text_rgb=text_rgb,
        background_color=_rgba(background_rgb),
        background_rgb=background_rgb,
        font_family=_mode(line.font_family for line in lines),
        font_size=float(median(font_sizes)) if font_sizes else None,
        ink_height=float(median(ink_heights)) if ink_heights else None,
        bold=_mode(line.bold for line in lines),
        baseline_offset=(
            first.y - y1 + first.baseline_offset
            if first.baseline_offset is not None
            else None
        ),
    )


def _mark_roles(blocks: Sequence[LayoutBlock], typical_height: float) -> None:
    metadata_pattern = re.compile(
        r"^(?:\d{1,2}:\d{2}(?:\s+\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)?"
        r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d+)$"
    )
    for block in blocks:
        if metadata_pattern.fullmatch(block.text.strip()):
            block.role = "metadata"

    # A short run immediately to the left of a time stamp is chat identity,
    # not message content.  Keep it visible as original UI and reserve its
    # geometry, but never send it to translation.
    time_blocks = [
        block
        for block in blocks
        if block.role == "metadata" and ":" in block.text
    ]
    for block in blocks:
        if block.role != "body" or len(block.lines) != 1 or len(block.text) > 80:
            continue
        for timestamp in time_blocks:
            row_overlap = max(
                0.0,
                min(block.bottom, timestamp.bottom) - max(block.y, timestamp.y),
            )
            same_row = row_overlap >= 0.45 * min(block.height, timestamp.height)
            gap = timestamp.x - block.right
            if same_row and -2.0 <= gap <= max(24.0, typical_height * 2.5):
                block.role = "protected"
                break

    # Feature-card titles only. Chat (Discord) has timestamps → never invent
    # titles from short message openers like "Khi tôi" / "when i".
    chat_layout = any(block.role in ("metadata", "protected") for block in blocks)
    for block in blocks:
        if block.role != "body" or block.list_item:
            continue
        candidates = [
            other
            for other in blocks
            if other is not block
            and other.role == "body"
            and other.y >= block.bottom
            and min(block.right, other.right) - max(block.x, other.x)
            >= 0.25 * min(block.width, other.width)
        ]
        if not candidates:
            continue
        below = min(candidates, key=lambda other: other.y - block.bottom)
        vertical_gap = below.y - block.bottom
        is_larger = block.median_line_height >= below.median_line_height * 1.06
        word_count = len(block.text.split())
        is_short = len(block.lines) <= 2 and len(block.text) <= 100
        is_card_header = (
            not chat_layout
            and len(block.lines) == 1
            and len(block.text) <= 48
            and word_count <= 6
            and len(below.text) >= max(12, int(len(block.text) * 1.3))
        )
        is_near = vertical_gap <= max(typical_height * 2.4, block.median_line_height * 2)
        block_luminance = _luminance(block.text_rgb)
        below_luminance = _luminance(below.text_rgb)
        # A heading introduces something; a brighter row followed by a shorter,
        # dimmer one is just a selected or faded neighbour in a list.
        is_brighter = (
            block_luminance is not None
            and below_luminance is not None
            and block_luminance - below_luminance >= _HEADING_LUMINANCE_LEAD
        )
        # Whatever the visual signal, a heading introduces something. Without
        # this a clipped or faded last row turns the row above it into a title.
        # A label above a menu introduces the whole list, so its single short
        # neighbour says nothing; anywhere else the body must be the longer one.
        introduces = below.list_item or len(below.text.strip()) >= len(
            block.text.strip()
        )
        # A list row's own metadata line is shorter than its title, so length
        # says nothing there. A large brightness drop does.
        if (
            block_luminance is not None
            and below_luminance is not None
            and block_luminance - below_luminance >= _HEADING_LUMINANCE_DROP
        ):
            introduces = True
        if (
            is_short
            and is_near
            and introduces
            and (is_larger or is_card_header or is_brighter)
        ):
            block.role = "title"


def _order_by_regions(
    blocks: Sequence[LayoutBlock], typical_height: float
) -> list[LayoutBlock]:
    """Keep blocks from the same visual card/region adjacent in reading order."""

    count = len(blocks)
    parents = list(range(count))

    def find(value):
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parents[b] = a

    for i, first in enumerate(blocks):
        for j in range(i + 1, count):
            second = blocks[j]
            overlap = max(
                0.0, min(first.right, second.right) - max(first.x, second.x)
            )
            overlap_ratio = overlap / max(1.0, min(first.width, second.width))
            if overlap_ratio < 0.25:
                continue
            if first.bottom <= second.y:
                gap = second.y - first.bottom
            elif second.bottom <= first.y:
                gap = first.y - second.bottom
            else:
                gap = 0.0
            if gap <= max(typical_height * 1.8, 10.0):
                union(i, j)

    regions: dict[int, list[LayoutBlock]] = {}
    for index, block in enumerate(blocks):
        regions.setdefault(find(index), []).append(block)
    ordered_regions = sorted(
        regions.values(),
        key=lambda region: (
            min(block.y for block in region),
            min(block.x for block in region),
        ),
    )
    result = []
    for region_id, region in enumerate(ordered_regions, 1):
        region.sort(key=lambda block: (block.y, block.x))
        for block in region:
            block.region_id = region_id
            result.append(block)
    return result


def segment_ocr_lines(
    values: Iterable,
    *,
    vertical: bool = False,
    separator: str = " ",
) -> list[LayoutBlock]:
    """Build conservative semantic blocks from OCR visual lines.

    Lines are only joined when they occupy the same column, have similar
    heights, and are close vertically. This intentionally prefers two small
    blocks over one incorrect large block: an overlay can safely render two
    blocks independently, but it cannot recover title/body structure after a
    destructive merge.
    """

    lines = [line for line in map(_as_line, values) if line.text.strip()]
    if not lines:
        return []
    if vertical:
        # Vertical OCR has different reading-order and line-flow semantics.
        # Keeping each OCR column independent is safer than a horizontal merge.
        ordered = sorted(lines, key=lambda line: (-line.x, line.y))
        blocks = [_make_block([line], separator) for line in ordered]
        for index, block in enumerate(blocks, 1):
            block.source_id = index
        return blocks

    typical_height = max(1.0, float(median([line.height for line in lines])))
    typical_glyph = _typical_glyph(lines)
    ordered = sorted(lines, key=lambda line: (line.y, line.x))
    # Find the menu rhythm before grouping. Judged one pair at a time a menu row
    # is indistinguishable from a loosely-led line of prose; judged as a run it
    # is obvious, and the rows must then never be joined to anything.
    # The last line of each item in a bulleted list is itself evenly spaced, so
    # a line that sits tight under another one is a continuation and must not
    # be read as a standalone row.
    standalone = [
        line
        for line in ordered
        if not any(
            other is not line
            and _horizontal_overlap_ratio(other, line) > 0.15
            and 0.0 <= line.y - other.bottom
            < _glyph_size(line, typical_glyph) * 0.80
            for other in ordered
        )
    ]
    row_run_lines = {
        id(member)
        for run in _uniform_row_runs(
            standalone, lambda line: _glyph_size(line, typical_glyph)
        )
        for member in run
    }
    groups: list[list[LayoutLine]] = []

    for line in ordered:
        if id(line) in row_run_lines:
            groups.append([line])
            continue
        best_group = None
        best_score = None
        for group in groups:
            score = _can_append(group, line, typical_height, typical_glyph)
            if score is None:
                continue
            if best_score is None or score < best_score:
                best_group = group
                best_score = score
        if best_group is None:
            groups.append([line])
        else:
            best_group.append(line)

    # Grouping is greedy, so a run only reveals itself as a list once every row
    # is in: the widths turn out ragged and the pitch never tightens. Undo those
    # merges before anything downstream treats the run as one flow of text.
    refined: list[list[LayoutLine]] = []
    for group in groups:
        if len(group) > 1 and not _looks_like_wrapped_paragraph(group, typical_glyph):
            refined.extend(_split_loose_runs(group, typical_glyph))
        else:
            refined.append(group)

    blocks = [_make_block(group, separator) for group in refined]
    blocks.sort(key=lambda block: (block.y, block.x))
    _mark_list_runs(blocks, typical_glyph)
    _mark_roles(blocks, typical_height)
    blocks = _order_by_regions(blocks, typical_height)
    for index, block in enumerate(blocks, 1):
        block.source_id = index
    return blocks


def build_ocr_layout(
    values: Iterable,
    *,
    vertical: bool = False,
    separator: str = " ",
) -> list[LayoutBlock]:
    lines = merge_ocr_atoms(values, vertical=vertical, separator=separator)
    return segment_ocr_lines(lines, vertical=vertical, separator=separator)


def parse_indexed_segments(text: str) -> list[tuple[int, str]]:
    """Parse ``[#id] value`` chunks without merging missing IDs."""

    import re

    matches = list(re.finditer(r"\[#(\d+)\][:\s-]*", text))
    result = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        value = text[match.end() : end].strip()
        if value:
            result.append((int(match.group(1)), value))
    return result
