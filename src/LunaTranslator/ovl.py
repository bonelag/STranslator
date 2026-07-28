from __future__ import annotations

import ctypes
import itertools
import json
import re
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import List, Optional, Sequence

from overlay_layout import parse_indexed_segments

from qtsymbols import (
    QApplication,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QRect,
    Qt,
    QTimer,
    QWidget,
    QLabel,
)

_overlays = []

from myutils.config import globalconfig

# Cấu hình tuỳ chỉnh vẽ Debug Overlay (Fill nền, fill text và độ mở rộng các hướng)
DEBUG_FILL_BG = True        # Cho phép fill đè nền (nền đã dịch)
DEBUG_FILL_TEXT = True      # Cho phép fill đè màu chữ

# Độ mở rộng dành riêng cho việc FILL NỀN (Background)
DEBUG_BG_PADDING = {
    "left_percent": 0.0,     # Mở rộng về bên trái theo % chiều rộng
    "right_percent": 0.0,   # Mở rộng về bên phải theo % chiều rộng
    "top_percent": 0.05,      # Mở rộng lên phía trên theo % chiều cao
    "bottom_percent": 0.05,   # Mở rộng xuống phía dưới theo % chiều cao
    
    "left_px": 0.0,          # Mở rộng về bên trái cố định (pixel)
    "right_px": 0.0,         # Mở rộng về bên phải cố định (pixel)
    "top_px": 0.0,           # Mở rộng lên phía trên cố định (pixel)
    "bottom_px": 0.0,        # Mở rộng xuống phía dưới cố định (pixel)
}

# Độ mở rộng dành riêng cho việc FILL TEXT (Chữ)
DEBUG_TEXT_PADDING = {
    "left_percent": 0.0,     # Mở rộng về bên trái theo % chiều rộng
    "right_percent": 0.0,   # Mở rộng về bên phải theo % chiều rộng
    "top_percent": 0.0,      # Mở rộng lên phía trên theo % chiều cao
    "bottom_percent": 0.0,   # Mở rộng xuống phía dưới theo % chiều cao
    
    "left_px": 0.0,          # Mở rộng về bên trái cố định (pixel)
    "right_px": 0.0,         # Mở rộng về bên phải cố định (pixel)
    "top_px": 0.0,           # Mở rộng lên phía trên cố định (pixel)
    "bottom_px": 0.0,        # Mở rộng xuống phía dưới cố định (pixel)
}

BG_ALPHA = 224
SAMPLE_STEP = 4
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_capture_affinity(widget: QWidget, exclude: bool):
    if not CONFIG.get("screen_capture_protection", 0):
        return
    try:
        affinity = WDA_EXCLUDEFROMCAPTURE if exclude else 0
        ctypes.windll.user32.SetWindowDisplayAffinity(
            int(widget.winId()), affinity
        )
    except Exception:
        pass


CONFIG = {
    "enable": 1,
    "show_in_main": 1,
    "text_color": "white",
    "stroke_color": "black",
    "stroke_width": 0,
    "min_font_size": 8,
    "max_font_size": 30,
    "font_family": "Segoe UI",
    "background_color": "rgba(0, 0, 0, 180)",
    "timeout_ms": 6000,
    "horizontal_padding": 4,
    "vertical_padding": 4,
    "screen_capture_protection": 0,
    "auto_background": 0,
    "auto_text_color": 0,
    "auto_font_weight": 1,
    "auto_font_family": 0,
    "adaptive_font_size": 1,
    # Write what the OCR layer actually handed the renderer to
    # userconfig/overlay_dump.json. Diagnostics only; off by default.
    "debug_dump": 0,
}

# Bề dày nét chữ / chiều cao dòng vượt ngưỡng này thì coi là chữ đậm.
# Dùng tỉ lệ (bất biến với cỡ chữ & độ dài dòng) thay vì tỉ lệ phủ mực,
# để các dòng cùng độ đậm cho ra kết quả đồng nhất.
BOLD_REL_THICKNESS = 0.12

# Two sampled ink colours are the same colour, measured twice, below this L1
# distance. Above it the page really does use two colours and both must stay.
# Sized from real captures: antialiasing spreads one grey over ~50 units,
# while a link, a dimmed caption or a dark-on-light label sits 160+ away.
_PALETTE_MERGE_DISTANCE = 60.0


def load_config():
    config_path = Path("userconfig/overlay.json")
    if not config_path.exists():
        return
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            CONFIG.update(data)
    except Exception:
        pass


def save_config():
    config_path = Path("userconfig/overlay.json")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=4)


def parse_color(color_str: str) -> QColor:
    color_str = str(color_str).strip().lower()
    if color_str.startswith("rgba"):
        try:
            content = color_str[color_str.find("(") + 1 : color_str.rfind(")")]
            parts = [x.strip() for x in content.split(",")]
            if len(parts) == 4:
                r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
                a_str = parts[3]
                a = int(float(a_str) * 255) if "." in a_str else int(a_str)
                return QColor(r, g, b, a)
        except Exception:
            pass
    return QColor(color_str)


SAMPLE_BUFFER = 10


def sample_background(image, box: "TextBox") -> QColor:
    if image is None or image.isNull():
        return QColor(0, 0, 0, BG_ALPHA)
    left = max(0, int(box.x - SAMPLE_BUFFER))
    top = max(0, int(box.y - SAMPLE_BUFFER))
    right = min(image.width(), int(box.x + box.width + SAMPLE_BUFFER))
    bottom = min(image.height(), int(box.y + box.height + SAMPLE_BUFFER))
    inner_left = int(box.x)
    inner_top = int(box.y)
    inner_right = int(box.x + box.width)
    inner_bottom = int(box.y + box.height)
    # Gom pixel rồi lấy cụm màu chiếm đa số (dominant) thay vì trung bình.
    # Khi box OCR không bao trọn dòng, vùng lấy mẫu dính chữ ngoài box;
    # dùng trung bình sẽ lem màu chữ vào nền, dùng dominant thì loại được.
    colors = []
    inside = []
    for y in range(top, bottom, SAMPLE_STEP):
        for x in range(left, right, SAMPLE_STEP):
            if inner_left <= x < inner_right and inner_top <= y < inner_bottom:
                inside.append(QColor(image.pixel(x, y)))
                continue
            colors.append(QColor(image.pixel(x, y)))
    if not colors:
        return QColor(0, 0, 0, BG_ALPHA)
    dom = find_dominant_color(colors)
    if inside:
        # A badge, pill or button is barely wider than its label, so the ring
        # around the box lands on the page instead of on the surface the text
        # actually sits on. Behind the glyphs is the honest answer.
        inner_dom = find_dominant_color(inside)
        if color_distance(inner_dom, dom) > 60:
            # Unless the glyphs themselves are what fills the box, in which
            # case the majority colour inside is the ink, not any surface.
            ink = sample_text_color(image, box, dom)
            if color_distance(inner_dom, ink) > 60:
                dom = inner_dom
    dom.setAlpha(BG_ALPHA)
    return dom


_SURFACE_TOLERANCE = 46
_SURFACE_SHARE = 0.5
_PAGE_SHARE = 0.95
# A rounded cap plus a 1px border is about this thick; more than that and
# whatever we are crossing is drawn content, not the element's own edge.
_SURFACE_EDGE = 3


def surface_pad(image, box: "TextBox") -> Optional[tuple]:
    """How far the empty part of this text's own surface reaches past its box.

    A badge, pill or button is a rounded rectangle a few pixels larger than
    the label inside it, so a cover cut to the label repaints the text and
    leaves the pill's border drawn around the translation. Growth crosses
    surface and the page showing through a rounded corner, and stops dead at
    anything else — an icon sharing the button keeps its pixels. Nothing grows
    at all unless the surface differs from the page around it, so ordinary
    text on the page background is never touched.
    """

    if image is None or image.isNull():
        return None
    x0 = max(0, int(box.x))
    y0 = max(0, int(box.y))
    x1 = min(image.width(), int(box.x + box.width))
    y1 = min(image.height(), int(box.y + box.height))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    # A pill's side padding runs to roughly its own text height; anything
    # beyond that is a different element, and growth stops on colour anyway.
    limit = max(4, int(round((y1 - y0) * 0.9)))

    inside = [
        QColor(image.pixel(x, y))
        for y in range(y0, y1, 2)
        for x in range(x0, x1, 2)
    ]
    if not inside:
        return None
    surface = find_dominant_color(inside)

    # The page is whatever lies beyond the furthest we could ever grow.
    beyond = limit + SAMPLE_BUFFER
    ring = []
    for y in (y0 - beyond, y1 - 1 + beyond):
        if 0 <= y < image.height():
            ring.extend(QColor(image.pixel(x, y)) for x in range(x0, x1, 2))
    for x in (x0 - beyond, x1 - 1 + beyond):
        if 0 <= x < image.width():
            ring.extend(QColor(image.pixel(x, y)) for y in range(y0, y1, 2))
    if not ring:
        return None
    outside = [
        pixel
        for pixel in ring
        if color_distance(pixel, surface) > _SURFACE_TOLERANCE
    ]
    # An element we may grow into has an edge: probe far enough out and you
    # are off it on nearly every side. A box sitting on the page itself — a
    # chat message, a paragraph — is still surrounded by that same colour, and
    # the stray glyphs and avatars the probe catches must not be read as one.
    if len(outside) < len(ring) * 0.6:
        return None
    page = find_dominant_color(outside)
    if color_distance(page, surface) <= 60:
        return None

    def shares(pixels) -> tuple:
        on_surface = on_page = 0
        for pixel in pixels:
            if color_distance(pixel, surface) <= _SURFACE_TOLERANCE:
                on_surface += 1
            elif color_distance(pixel, page) <= _SURFACE_TOLERANCE:
                on_page += 1
        total = max(1, len(pixels))
        return on_surface / total, on_page / total

    def grow(step_x: int, step_y: int) -> int:
        scan = []
        # Probe well past what may be covered: deciding whether a stretch of
        # non-surface is this element's border or an icon inside it needs to
        # see whether the surface comes back, and an icon is wider than the
        # padding we would ever grow by.
        for step in range(1, limit * 2 + 5):
            if step_x:
                x = x0 - step if step_x < 0 else x1 - 1 + step
                if not 0 <= x < image.width():
                    break
                pixels = [QColor(image.pixel(x, y)) for y in range(y0, y1, 2)]
            else:
                y = y0 - step if step_y < 0 else y1 - 1 + step
                if not 0 <= y < image.height():
                    break
                pixels = [QColor(image.pixel(x, y)) for x in range(x0, x1, 2)]
            scan.append(shares(pixels))

        reach = 0
        edge = 0
        for index, (surface_share, page_share) in enumerate(scan[:limit]):
            if surface_share >= _SURFACE_SHARE:
                reach = index + 1
                edge = 0
                continue
            if page_share >= _PAGE_SHARE:
                break  # clean page: the element ended here
            # Neither: a rounded cap, or the element's own border — both are
            # ours to cover. Unless the surface picks up again before the page
            # does, in which case this is something drawn *on* the surface,
            # and an icon sharing a button must keep its pixels.
            resumes = False
            for ahead_surface, ahead_page in scan[index + 1 :]:
                if ahead_page >= _PAGE_SHARE:
                    break
                if ahead_surface >= _SURFACE_SHARE:
                    resumes = True
                    break
            if resumes:
                break
            edge += 1
            if edge > _SURFACE_EDGE:
                break
            reach = index + 1
        return reach

    pad = (grow(-1, 0), grow(0, -1), grow(1, 0), grow(0, 1))
    return pad if any(pad) else None


def color_to_rgba(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def text_width(metrics: QFontMetrics, text: str) -> int:
    return metrics.horizontalAdvance(text) if hasattr(metrics, "horizontalAdvance") else metrics.width(text)


_LEADING_PUNCT = set(",.;:!?)]}…、，。；：！？")


def _tokenize_for_wrap(text: str) -> list[str]:
    """Split into wrappable tokens; never leave bare punctuation as its own token."""

    raw: list[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for token in paragraph.split(" "):
            if token:
                raw.append(token)
    if not raw:
        return []
    # Glue ", word" / lone commas onto the previous token so wrap cannot
    # start a line with punctuation (common after LLM spacing).
    words: list[str] = []
    for token in raw:
        if words and (
            token in _LEADING_PUNCT
            or (token[:1] in _LEADING_PUNCT and len(token) <= 2)
        ):
            words[-1] = words[-1] + token
        elif words and token[:1] in _LEADING_PUNCT:
            words[-1] = words[-1] + token[0]
            rest = token[1:].lstrip()
            if rest:
                words.append(rest)
        else:
            words.append(token)
    return words


def wrap_text(text: str, font: QFont, max_width: int) -> List[str]:
    """Greedy word-wrap (minimum line count for the given width)."""

    metrics = QFontMetrics(font)
    lines = []

    def split_token(token: str) -> List[str]:
        chunks = []
        current = ""
        for char in token:
            candidate = current + char
            if current and text_width(metrics, candidate) > max_width:
                chunks.append(current)
                current = char
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks or [token]

    words = _tokenize_for_wrap(text)
    if not words:
        return [text] if text else [""]

    current = ""
    for word in words:
        if text_width(metrics, word) > max_width:
            if current:
                lines.append(current)
                current = ""
            pieces = split_token(word)
            lines.extend(pieces[:-1])
            current = pieces[-1]
            continue
        candidate = word if not current else f"{current} {word}"
        if text_width(metrics, candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def wrap_text_balanced(
    text: str,
    font: QFont,
    max_width: int,
    target_lines: Optional[int] = None,
) -> List[str]:
    """Even out greedy wrap lines — never invent *extra* sparse lines.

    Forcing more lines than the greedy minimum (e.g. to match OCR line count)
    produced the "one short phrase floating per row" look. We only rebalance
    the minimum wrap; ``target_lines`` may *reduce* a request but cannot
    expand past greedy.
    """

    greedy = wrap_text(text, font, max_width)
    words = _tokenize_for_wrap(text)
    if len(words) <= 1 or len(greedy) <= 1:
        return greedy
    metrics = QFontMetrics(font)
    # Only the greedy line count. Ignoring larger target_lines is intentional.
    n = len(greedy)
    if target_lines is not None and int(target_lines) > 0:
        n = min(n, max(1, int(target_lines)))
        n = max(n, 1)
        # If caller asks for fewer lines than greedy, that would overflow —
        # keep greedy count.
        if n < len(greedy):
            n = len(greedy)

    space = max(1, text_width(metrics, " "))
    widths = [text_width(metrics, word) for word in words]
    count = len(words)

    def span_width(start: int, end: int) -> int:
        if end <= start:
            return 0
        return sum(widths[start:end]) + space * (end - start - 1)

    total = span_width(0, count)
    ideal = total / float(n)
    inf = 1e18
    dp = [[inf] * (count + 1) for _ in range(n + 1)]
    prev = [[-1] * (count + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for k in range(1, n + 1):
        for i in range(k, count + 1):
            max_j = i - (n - k) if k < n else i - 1
            min_j = k - 1
            for j in range(min_j, max_j + 1):
                if dp[k - 1][j] >= inf:
                    continue
                width = span_width(j, i)
                if width > max_width and (i - j) > 1:
                    continue
                cost = dp[k - 1][j] + (width - ideal) ** 2
                if cost < dp[k][i]:
                    dp[k][i] = cost
                    prev[k][i] = j

    if dp[n][count] >= inf:
        return greedy

    cuts: list[tuple[int, int]] = []
    k, i = n, count
    while k > 0:
        j = prev[k][i]
        if j < 0:
            return greedy
        cuts.append((j, i))
        i = j
        k -= 1
    cuts.reverse()
    parts = [" ".join(words[start:end]) for start, end in cuts]
    # Never leave a line that is only punctuation.
    cleaned = []
    for part in parts:
        if cleaned and part and all(ch in _LEADING_PUNCT or ch.isspace() for ch in part):
            cleaned[-1] = cleaned[-1] + part
        else:
            cleaned.append(part)
    return cleaned or greedy


def _stable_line_spacing(role: str, source_spacing: Optional[float] = None) -> float:
    """Tight, UI-like leading. Ignore stretched OCR rhythm entirely."""

    if role == "title":
        return 1.05
    if role == "chat":
        # Slightly denser than body so long VI lines stay in the message band.
        return 1.08
    return 1.15


def _estimate_start_font_px(box: "TextBox", min_px: int, max_px: int) -> int:
    ink_hints = [
        float(line["ink_height"])
        for line in box.source_lines
        if line.get("ink_height")
    ]
    size_hints = [
        float(line["font_size"])
        for line in box.source_lines
        if line.get("font_size")
    ]
    # ink ≈ cap height; Qt pixel size is a bit larger than ink.
    factor = 1.22 if box.role == "title" else 1.15
    own_ink = float(median(ink_hints)) if ink_hints else 0.0
    # A clipped or faded menu row reports ink far too small and would drop
    # several sizes below the rows it plainly lines up with. Its run measured
    # the same glyphs many times over, so take whichever saw more of them.
    ink = max(own_ink, float(box.run_ink or 0.0))
    if box.font_size:
        source_px = float(box.font_size)
    elif size_hints:
        source_px = float(median(size_hints))
    elif ink:
        source_px = ink * factor
    else:
        source_px = min(20.0, max(float(min_px), box.height * 0.72))
    if ink:
        source_px = min(source_px, ink * factor)
    return max(min_px, min(max_px, round(source_px)))


def _preferred_wrap_width(
    text: str,
    font: QFont,
    target_w: int,
    target_h: float,
    source_lines: Sequence,
    role: str,
) -> int:
    """Choose wrap width from source line geometry (cards) or full box (chat).

    Chat/Discord messages are already wide; forcing narrow wrap invents wrong
    line breaks and looks like huge reflowed blobs. Cards keep the denser wrap.
    """

    metrics = QFontMetrics(font)
    target_w = max(40, int(target_w))
    source_ws = [
        float(line.get("width", 0))
        for line in (source_lines or [])
        if line.get("width")
    ]
    # Single source line / wide chat bubble → use full box width.
    if len(source_ws) <= 1:
        return target_w
    # Many similar-width source lines that nearly fill the box → chat/paragraph.
    med_w = float(median(source_ws))
    if med_w >= target_w * 0.82:
        return target_w

    if role != "body":
        return target_w

    pref = med_w * 1.06
    wrap_w = int(min(target_w, max(pref, target_w * 0.70)))
    line_h = max(1.0, float(metrics.lineSpacing()) * 1.15)
    max_lines = max(1, int(target_h / line_h))
    words = _tokenize_for_wrap(text)
    if len(words) >= 8 and max_lines >= 3 and len(source_ws) >= 3:
        floor_w = max(40, int(target_w * 0.62))
        want = min(max_lines, max(len(source_ws), (len(words) + 2) // 4))
        lo, hi = floor_w, wrap_w
        best = wrap_w
        for _ in range(10):
            mid = (lo + hi) // 2
            n = len(wrap_text(text, font, mid))
            if n < want and mid > floor_w:
                hi = mid - 1
                best = mid
            else:
                lo = mid + 1
                if n <= max_lines:
                    best = mid
        wrap_w = max(floor_w, min(wrap_w, best))
    return max(40, min(target_w, wrap_w))


def _layout_text_in_box(
    text: str,
    font: QFont,
    *,
    target_w: int,
    target_h: float,
    box_height: float,
    role: str,
    source_line_count: int,
    source_spacing: Optional[float],
    vpad: float,
    prefer_baseline: Optional[float],
    source_lines: Optional[Sequence] = None,
    spacing_scale: float = 1.0,
    allow_overflow: bool = False,
):
    """Return (lines, spacing, baseline, line_advance, metrics, tight_rects) or None."""

    metrics = QFontMetrics(font)
    wrap_w = _preferred_wrap_width(
        text, font, target_w, target_h, source_lines or [], role
    )
    # Dense greedy wrap at preferred width, then rebalance those lines only.
    lines = wrap_text_balanced(text, font, wrap_w)
    # Safety: if a line still exceeds the real box width, re-wrap at full width.
    if any(text_width(metrics, line) > target_w + 1 for line in lines):
        lines = wrap_text_balanced(text, font, target_w)
    spacing_role = "chat" if role == "chat" else role
    spacing = _stable_line_spacing(spacing_role, source_spacing) * float(spacing_scale)
    line_advance = max(1, round(metrics.lineSpacing() * spacing))
    text_w = max((text_width(metrics, line) for line in lines), default=0)
    if text_w > target_w + 1:
        return None
    tight_rects = [metrics.tightBoundingRect(line or " ") for line in lines]
    relative_top = min(rect.top() for rect in tight_rects) if tight_rects else 0
    relative_bottom = (
        line_advance * max(0, len(lines) - 1)
        + max((rect.bottom() for rect in tight_rects), default=0)
    )
    ink_height = relative_bottom - relative_top + 1
    min_y = vpad / 2.0
    max_y = box_height - vpad / 2.0
    # Top-align like real UI copy.
    if prefer_baseline is not None and len(lines) == 1:
        candidate_baseline = float(prefer_baseline)
    else:
        candidate_baseline = min_y - relative_top
    ink_top = candidate_baseline + relative_top
    ink_bottom = candidate_baseline + relative_bottom
    if ink_top < min_y:
        candidate_baseline += min_y - ink_top
        ink_bottom += min_y - ink_top
        ink_top = min_y
    if ink_bottom > max_y:
        candidate_baseline -= ink_bottom - max_y
        ink_top -= ink_bottom - max_y
        ink_bottom = max_y
    if not (ink_top >= min_y - 0.5 and ink_bottom <= max_y + 0.5):
        if not allow_overflow:
            return None
        # Emergency: top-align and accept clipping rather than drop the label.
        candidate_baseline = min_y - relative_top
    return lines, spacing, candidate_baseline, line_advance, metrics, tight_rects


def _max_fitting_font_px(box: "TextBox", min_px: int, max_px: int) -> Optional[int]:
    """Largest pixel size at which ``box.text`` still fits the box geometry."""

    if not (box.text or "").strip():
        return None
    hpad = int(CONFIG["horizontal_padding"])
    vpad = int(CONFIG["vertical_padding"])
    target_w = max(1, int(box.width - hpad))
    target_h = max(1, int(box.height - vpad))
    family = (
        box.font_family
        if CONFIG.get("auto_font_family", 0) and box.font_family
        else (CONFIG.get("font_family") or "Segoe UI")
    )
    font = QFont(family)
    if box.bold is not None:
        font.setBold(bool(box.bold))
    start = _estimate_start_font_px(box, min_px, max_px)
    source_line_count = max(
        1, len([line for line in box.source_lines if line]) or 1
    )
    for px in range(start, min_px - 1, -1):
        font.setPixelSize(px)
        laid = _layout_text_in_box(
            box.text,
            font,
            target_w=target_w,
            target_h=target_h,
            box_height=box.height,
            role=box.role or "body",
            source_line_count=source_line_count,
            source_spacing=box.line_spacing,
            vpad=vpad,
            prefer_baseline=None,
            source_lines=box.source_lines,
        )
        if laid is not None:
            return px
    return None


def _shared_font_px_by_role(boxes: Sequence["TextBox"]) -> dict[str, int]:
    """Harmonize peer sizes without crushing everything to the hungriest outlier.

    Uses the lower quartile of per-block fit sizes so one pathological narrow
    box cannot make the whole grid unreadably small, while still keeping sizes
    close across cards.
    """

    min_px = int(CONFIG["min_font_size"])
    max_px = int(CONFIG["max_font_size"])
    by_role: dict[str, list[TextBox]] = {}
    for box in boxes:
        if box.role in ("metadata", "protected") or not (box.text or "").strip():
            continue
        by_role.setdefault(box.role or "body", []).append(box)

    shared: dict[str, int] = {}
    for role, members in by_role.items():
        fits = []
        starts = []
        for box in members:
            starts.append(_estimate_start_font_px(box, min_px, max_px))
            fit = _max_fitting_font_px(box, min_px, max_px)
            if fit is not None:
                fits.append(fit)
        if not fits:
            continue
        fits_sorted = sorted(fits)
        # Lower quartile (or min for tiny sets) — balanced vs uniform min.
        q_index = max(0, (len(fits_sorted) - 1) // 4)
        common = fits_sorted[q_index]
        if starts:
            # Never above typical source size for this role.
            common = min(common, max(min_px, int(median(starts))))
        # Keep within a band of the median fit so grid still looks uniform.
        med = float(median(fits_sorted))
        common = int(min(common, med))
        common = max(min_px, min(common, int(med)))
        # Soft floor: if quartile is far below median, prefer a bit larger and
        # let the few hard boxes shrink independently in _create_label.
        if med > 0 and common < med * 0.82:
            common = max(common, int(med * 0.82))
        shared[role] = max(min_px, min(max_px, common))
    if "title" in shared and "body" in shared:
        if shared["title"] < shared["body"]:
            shared["title"] = shared["body"]
        elif shared["title"] > shared["body"] * 1.35:
            shared["title"] = max(shared["body"], int(shared["body"] * 1.18))
    return shared


try:
    from PyQt5.QtGui import QLinearGradient, QBrush
except ImportError:
    from PyQt6.QtGui import QLinearGradient, QBrush


def average_color_in_rect(image, rx, ry, rw, rh) -> Optional[QColor]:
    if image is None or image.isNull():
        return None
    r_sum = g_sum = b_sum = count = 0
    for y in range(max(0, int(ry)), min(image.height(), int(ry + rh)), SAMPLE_STEP):
        for x in range(max(0, int(rx)), min(image.width(), int(rx + rw)), SAMPLE_STEP):
            color = QColor(image.pixel(x, y))
            r_sum += color.red()
            g_sum += color.green()
            b_sum += color.blue()
            count += 1
    if count == 0:
        return None
    return QColor(r_sum // count, g_sum // count, b_sum // count, 255)


def dominant_color_in_rect(image, rx, ry, rw, rh) -> Optional[QColor]:
    # Như average_color_in_rect nhưng trả màu cụm đa số, loại pixel chữ ngoài box.
    if image is None or image.isNull():
        return None
    colors = []
    for y in range(max(0, int(ry)), min(image.height(), int(ry + rh)), SAMPLE_STEP):
        for x in range(max(0, int(rx)), min(image.width(), int(rx + rw)), SAMPLE_STEP):
            colors.append(QColor(image.pixel(x, y)))
    if not colors:
        return None
    return find_dominant_color(colors)


def sample_gradient_colors(image, box: "TextBox", default_color: QColor):
    if image is None or image.isNull():
        return (default_color, default_color, default_color, default_color, default_color)
        
    color_left = dominant_color_in_rect(image, box.x - SAMPLE_BUFFER, box.y, SAMPLE_BUFFER, box.height)
    color_right = dominant_color_in_rect(image, box.x + box.width, box.y, SAMPLE_BUFFER, box.height)
    color_top = dominant_color_in_rect(image, box.x, box.y - SAMPLE_BUFFER, box.width, SAMPLE_BUFFER)
    color_bottom = dominant_color_in_rect(image, box.x, box.y + box.height, box.width, SAMPLE_BUFFER)
    
    color_left = color_left or default_color
    color_right = color_right or default_color
    color_top = color_top or default_color
    color_bottom = color_bottom or default_color
    
    return (color_left, color_right, color_top, color_bottom, default_color)


def create_gradient_brush(rect: QRect, colors) -> QBrush:
    color_left, color_right, color_top, color_bottom, default_color = colors
    
    def color_diff(c1: QColor, c2: QColor) -> int:
        return abs(c1.red() - c2.red()) + abs(c1.green() - c2.green()) + abs(c1.blue() - c2.blue())
    
    dist_h = color_diff(color_left, color_right)
    dist_v = color_diff(color_top, color_bottom)
    
    THRESHOLD = 15
    
    if dist_h > dist_v and dist_h > THRESHOLD:
        grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        grad.setColorAt(0.0, color_left)
        grad.setColorAt(1.0, color_right)
        return QBrush(grad)
    elif dist_v >= dist_h and dist_v > THRESHOLD:
        grad = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        grad.setColorAt(0.0, color_top)
        grad.setColorAt(1.0, color_bottom)
        return QBrush(grad)
    else:
        return QBrush(default_color)


def color_distance(c1: QColor, c2: QColor) -> int:
    return abs(c1.red() - c2.red()) + abs(c1.green() - c2.green()) + abs(c1.blue() - c2.blue())


def clean_text_color(color: QColor) -> QColor:
    r, g, b = color.red(), color.green(), color.blue()
    
    # Only collapse values that are effectively at the gamut boundary. Broad
    # snapping (for example 220 -> 255) makes warm white and muted UI text
    # visibly different from the source.
    if r > 248 and g > 248 and b > 248:
        return QColor(255, 255, 255)
    if r < 7 and g < 7 and b < 7:
        return QColor(0, 0, 0)
    # Neutral colors keep their measured luminance.
    max_val = max(r, g, b)
    min_val = min(r, g, b)
    if max_val - min_val < 15:
        gray_val = (r + g + b) // 3
        return QColor(gray_val, gray_val, gray_val)
        
    return color


def sample_text_color(image, box: "TextBox", bg_color: QColor) -> QColor:
    if image is None or image.isNull():
        return QColor(255, 255, 255)
        
    pixels_with_diff = []
    max_diff = 0
    bg_r, bg_g, bg_b = bg_color.red(), bg_color.green(), bg_color.blue()
    if box.width >= 640:
        step = 6
    elif box.width >= 320:
        step = 4
    elif box.height >= 18 or box.width >= 80:
        step = 2
    else:
        step = 1
    
    # Bước 1: Quét tất cả các pixel trong box chữ để tính độ lệch màu và tìm max_diff
    for y in range(int(box.y), int(box.y + box.height), step):
        for x in range(int(box.x), int(box.x + box.width), step):
            if 0 <= x < image.width() and 0 <= y < image.height():
                pixel_color = QColor(image.pixel(x, y))
                r, g, b = pixel_color.red(), pixel_color.green(), pixel_color.blue()
                diff = abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b)
                pixels_with_diff.append((r, g, b, diff))
                if diff > max_diff:
                    max_diff = diff
                    
    # Nếu chênh lệch màu cực đại quá nhỏ, trả về màu tương phản
    if max_diff < 35:
        return QColor(255, 255, 255) if bg_color.lightness() < 128 else QColor(0, 0, 0)
        
    # Bước 2: Chỉ lấy trung bình cộng của các pixel có độ chênh lệch màu thuộc nhóm cao nhất
    # (chênh lệch so với max_diff không quá 60 đơn vị RGB) để lấy đúng phần ruột chữ sáng nhất
    r_sum = g_sum = b_sum = count = 0
    threshold = max(35, max_diff - 60)
    for r, g, b, diff in pixels_with_diff:
        if diff >= threshold:
            r_sum += r
            g_sum += g
            b_sum += b
            count += 1
            
    if count == 0:
        return QColor(255, 255, 255) if bg_color.lightness() < 128 else QColor(0, 0, 0)
        
    avg_color = QColor(r_sum // count, g_sum // count, b_sum // count)
    return clean_text_color(avg_color)


def measure_ink_geometry(
    image, box: "TextBox", bg_color: QColor, text_color: Optional[QColor] = None
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if image is None or image.isNull():
        return None, None, None

    x0 = max(0, int(box.x))
    y0 = max(0, int(box.y))
    x1 = min(image.width(), int(box.x + box.width))
    y1 = min(image.height(), int(box.y + box.height))
    h = y1 - y0
    if h <= 0 or x1 - x0 <= 0:
        return None, None, None

    # Ngưỡng "mực" thích nghi theo độ tương phản chữ/nền thực tế.
    text_color = text_color or sample_text_color(image, box, bg_color)
    contrast = color_distance(text_color, bg_color)
    if contrast < 40:
        return None, None, None
    ink_thresh = max(30.0, contrast * 0.42)

    run_lengths = []
    ink_top = y1
    ink_bottom = y0 - 1
    width = x1 - x0
    # Coarse probe to find ink; measure each hit's true pixel span so x_step
    # does not inflate stroke width (and therefore bold_score) on wide boxes.
    x_step = 8 if width >= 640 else (4 if width >= 320 else (2 if width >= 80 else 1))

    def is_ink(px: int, py: int) -> bool:
        return color_distance(QColor(image.pixel(px, py)), bg_color) > ink_thresh

    for y in range(y0, y1):
        x = x0
        while x < x1:
            if not is_ink(x, y):
                x += x_step
                continue
            left = x
            while left > x0 and is_ink(left - 1, y):
                left -= 1
            right = x
            while right + 1 < x1 and is_ink(right + 1, y):
                right += 1
            ink_top = min(ink_top, y)
            ink_bottom = max(ink_bottom, y)
            run_lengths.append(right - left + 1)
            x = right + 1

    if not run_lengths:
        return None, None, None
    ink_height = max(1.0, float(ink_bottom - ink_top + 1))
    # Loại đoạn quá dài (nét ngang / gạch chân) để median phản ánh bề dày nét dọc.
    max_keep = max(2, ink_height * 0.6)
    filtered = [r for r in run_lengths if r <= max_keep]
    if len(filtered) < max(3, len(run_lengths) // 10):
        return float(ink_top - y0), ink_height, None
    filtered.sort()
    median_run = filtered[len(filtered) // 2]
    return float(ink_top - y0), ink_height, float(median_run / ink_height)


def estimate_text_boldness(
    image, box: "TextBox", bg_color: QColor, text_color: Optional[QColor] = None
) -> bool:
    _, _, score = measure_ink_geometry(image, box, bg_color, text_color)
    return bool(score is not None and score > BOLD_REL_THICKNESS)


def find_dominant_color(colors: List[QColor]) -> QColor:
    if not colors:
        return QColor(255, 255, 255)

    # Quantized histogram is deterministic; the previous greedy clustering
    # changed its result with pixel traversal order near color boundaries.
    groups = {}
    for c in colors:
        key = (c.red() // 24, c.green() // 24, c.blue() // 24)
        groups.setdefault(key, []).append(c)
    longest_group = max(groups.values(), key=len)
    
    r_sum = sum(c.red() for c in longest_group)
    g_sum = sum(c.green() for c in longest_group)
    b_sum = sum(c.blue() for c in longest_group)
    count = len(longest_group)
    
    return QColor(r_sum // count, g_sum // count, b_sum // count)


BOX_PATTERN = re.compile(
    r"\[(?P<x>-?\d+)\s+(?P<y>-?\d+)\|(?P<w>\d+)\s+(?P<h>\d+)\]\s*(?P<text>.*?)(?=\[-?\d+\s+-?\d+\|\d+\s+\d+\]|$)",
    re.DOTALL,
)


@dataclass
class TextBox:
    x: float
    y: float
    width: float
    height: float
    text: str
    background_color: Optional[str] = None
    text_color: Optional[str] = None
    stroke_color: Optional[str] = None
    bold: Optional[bool] = None
    stroke_width: Optional[float] = None
    font_family: Optional[str] = None
    font_size: Optional[float] = None
    line_spacing: Optional[float] = None
    baseline_offset: Optional[float] = None
    alignment: str = "left"
    role: str = "body"
    # Row of a menu / option list, decided during OCR segmentation with the
    # whole frame in view. Paint must not re-merge these into a paragraph.
    list_item: bool = False
    # Which uniform row run it belongs to (0 = none). Rows of one run were
    # proven to share a glyph size, whatever each one's own ink measured.
    list_run: int = 0
    # Typical ink height across that run. One row's own ink is unreliable —
    # clipped, faded or short-lettered rows measure far too small — so a run
    # member is sized against what the run as a whole measured.
    run_ink: Optional[float] = None
    region_id: int = 0
    layout_id: int = 0
    source_id: int = 0
    marker_id: int = 0
    source_lines: list[dict] = field(default_factory=list)
    ink_height: Optional[float] = None
    ink_top: Optional[float] = None
    bold_score: Optional[float] = None
    # Colour as measured from the source pixels. Kept even when the user has
    # auto_text_color off: that switch governs painting, not structure.
    text_rgb: Optional[tuple] = None
    # Re-classified from title to body inside its card; its measured style is
    # known to be unreliable, so it may rejoin the body paragraph.
    demoted: bool = False


PENDING_BOXES: List[TextBox] = []
_PENDING_BY_MARKER: "OrderedDict[int, TextBox]" = OrderedDict()
_MARKER_IDS = itertools.count(1)
_LAYOUT_IDS = itertools.count(1)
_LAYOUT_MARKERS: "OrderedDict[int, list[int]]" = OrderedDict()
_TRANSLATED_BY_STREAM: dict[tuple[str, int], str] = {}
_MAX_PENDING_MARKERS = 1024
# Layout IDs detected as Discord/chat for this paint cycle (height caps, fit).
_CHAT_LAYOUT_IDS: set[int] = set()
# OCR workers register markers; the UI thread paints from the same maps.
_PENDING_LOCK = threading.RLock()


def _as_text_box(value) -> TextBox:
    if isinstance(value, TextBox):
        return replace(value, source_lines=list(value.source_lines))
    if isinstance(value, dict):
        return TextBox(
            x=float(value["x"]),
            y=float(value["y"]),
            width=float(value.get("width", value.get("w", 0))),
            height=float(value.get("height", value.get("h", 0))),
            text=str(value.get("text", "")),
            background_color=value.get("background_color"),
            text_color=value.get("text_color"),
            stroke_color=value.get("stroke_color"),
            bold=value.get("bold"),
            stroke_width=value.get("stroke_width"),
            font_family=value.get("font_family"),
            font_size=value.get("font_size"),
            line_spacing=value.get("line_spacing"),
            baseline_offset=value.get("baseline_offset"),
            alignment=value.get("alignment", "left"),
            role=value.get("role", "body"),
            list_item=bool(value.get("list_item", False)),
            list_run=int(value.get("list_run", 0)),
            region_id=int(value.get("region_id", 0)),
            layout_id=int(value.get("layout_id", 0)),
            source_id=int(value.get("source_id", 0)),
            source_lines=list(value.get("lines", value.get("source_lines", []))),
            ink_height=(
                float(value["ink_height"])
                if value.get("ink_height") is not None
                else None
            ),
            ink_top=(
                float(value["ink_top"])
                if value.get("ink_top") is not None
                else None
            ),
            bold_score=(
                float(value["bold_score"])
                if value.get("bold_score") is not None
                else None
            ),
            text_rgb=(
                tuple(value["text_rgb"]) if value.get("text_rgb") is not None else None
            ),
        )
    x, y, width, height, text = value[:5]
    return TextBox(float(x), float(y), float(width), float(height), str(text))


def _local_box(box: TextBox, offset) -> TextBox:
    ox, oy = offset or (0, 0)
    return replace(box, x=box.x - ox, y=box.y - oy)


def _source_line_boxes(box: TextBox) -> list[TextBox]:
    values = []
    for line in box.source_lines:
        try:
            values.append(_as_text_box(line))
        except Exception:
            continue
    return values or [replace(box)]


_FONT_CANDIDATES = (
    "Segoe UI",
    "Arial",
    "Tahoma",
    "Verdana",
    "Calibri",
    "Times New Roman",
    "Georgia",
    "Trebuchet MS",
    "Microsoft YaHei",
    "Yu Gothic UI",
    "Meiryo",
    "Malgun Gothic",
    "Noto Sans",
)
_FONT_METRICS_CACHE = {}
_FONT_CANDIDATE_CACHE = {}


def _font_metrics(family: str, pixel_size: int, bold: bool) -> QFontMetrics:
    key = (family, int(pixel_size), bool(bold))
    metrics = _FONT_METRICS_CACHE.get(key)
    if metrics is None:
        font = QFont(family)
        font.setPixelSize(int(pixel_size))
        font.setBold(bool(bold))
        metrics = QFontMetrics(font)
        _FONT_METRICS_CACHE[key] = metrics
    return metrics


def _installed_font_candidates() -> list[str]:
    configured = str(CONFIG.get("font_family", "")).strip()
    cached = _FONT_CANDIDATE_CACHE.get(configured)
    if cached is not None:
        return cached
    try:
        installed = set(QFontDatabase().families())
    except Exception:
        try:
            installed = set(QFontDatabase.families())
        except Exception:
            installed = set()
    candidates = [family for family in _FONT_CANDIDATES if not installed or family in installed]
    try:
        system_family = QFontDatabase.systemFont(
            QFontDatabase.SystemFont.GeneralFont
        ).family()
        if system_family and system_family not in candidates:
            candidates.insert(0, system_family)
    except Exception:
        pass
    if configured and configured not in candidates:
        candidates.insert(0, configured)
    result = candidates or [configured or "Arial"]
    _FONT_CANDIDATE_CACHE[configured] = result
    return result


def _font_score(family: str, lines: Sequence[TextBox], bold: bool):
    errors = []
    sizes = []
    for line in lines:
        text = line.text.strip()
        target_height = line.ink_height or line.height
        if not text or line.width <= 0 or target_height <= 0:
            continue
        best = None
        # OCR rectangles describe glyph ink, while a Qt pixel size describes
        # the em square. A small coarse-to-fine set around the ink height is
        # sufficient and avoids constructing dozens of fonts per line.
        candidates = {
            max(4, min(180, round(target_height * factor)))
            for factor in (0.72, 0.86, 1.0, 1.14, 1.28, 1.42, 1.58)
        }
        for pixel_size in sorted(candidates):
            metrics = _font_metrics(family, pixel_size, bold)
            rect = metrics.tightBoundingRect(text)
            measured_w = max(1, rect.width())
            measured_h = max(1, rect.height())
            height_error = abs(measured_h - target_height) / max(1.0, target_height)
            width_error = abs(measured_w - line.width) / max(1.0, line.width)
            score = height_error * 1.35 + width_error
            if best is None or score < best[0]:
                best = (score, pixel_size)
        if best:
            errors.append(best[0])
            sizes.append(best[1])
    if not errors:
        return float("inf"), None
    return sum(errors) / len(errors), float(median(sizes))


def _infer_font(lines: Sequence[TextBox], bold: bool):
    best = None
    for family in _installed_font_candidates():
        score, size = _font_score(family, lines, bold)
        if size is not None and (best is None or score < best[0]):
            best = (score, family, size)
    if best is None:
        heights = [
            line.ink_height or line.height
            for line in lines
            if (line.ink_height or line.height) > 0
        ]
        return None, (float(median(heights)) if heights else None)
    return best[1], best[2]


def _split_text_for_bands(text: str, widths: Sequence[float]) -> list[str]:
    explicit = [line.strip() for line in text.splitlines() if line.strip()]
    if len(explicit) == len(widths):
        return explicit
    use_words = len(text.split()) >= len(widths)
    units = text.split() if use_words else list(text.strip())
    if len(units) < len(widths):
        return []
    weights = [max(1.0, float(width)) for width in widths]
    total_weight = sum(weights)
    total_text = sum(max(1, len(unit)) for unit in units)
    result = []
    start = 0
    cumulative_weight = 0.0
    for index, weight in enumerate(weights[:-1], 1):
        cumulative_weight += weight
        target = total_text * cumulative_weight / total_weight
        best_end = start + 1
        best_error = float("inf")
        running = 0
        max_end = len(units) - (len(widths) - index)
        for end in range(start + 1, max_end + 1):
            running += max(1, len(units[end - 1]))
            error = abs(running - target + sum(max(1, len(unit)) for unit in units[:start]))
            if error <= best_error:
                best_error = error
                best_end = end
            else:
                break
        result.append((" " if use_words else "").join(units[start:best_end]))
        start = best_end
    result.append((" " if use_words else "").join(units[start:]))
    return result if all(value.strip() for value in result) else []


def _plain_atom(value, box: TextBox) -> dict:
    if isinstance(value, dict):
        return dict(value)
    return {
        "x": box.x,
        "y": box.y,
        "width": box.width,
        "height": box.height,
        "text": box.text,
    }


def _scan_ink_row_bands(image, box: TextBox, background: QColor, x_step: int):
    x0 = max(0, int(box.x))
    y0 = max(0, int(box.y))
    x1 = min(image.width(), int(box.x + box.width))
    y1 = min(image.height(), int(box.y + box.height))
    if x1 <= x0 or y1 <= y0:
        return []

    # A fixed background-distance floor keeps dim body copy visible even when
    # a bright title shares the same paragraph rectangle.  The previous global
    # max-contrast threshold erased that body entirely.
    threshold = 32
    min_ink = max(1, round(((x1 - x0) / max(1, x_step)) * 0.006))
    active = []
    for y in range(y0, y1):
        count = 0
        left = x1
        right = x0
        for x in range(x0, x1, x_step):
            if color_distance(QColor(image.pixel(x, y)), background) > threshold:
                count += 1
                left = min(left, x)
                right = max(right, x + x_step)
        if count >= min_ink:
            active.append((y, left, min(x1, right)))

    bridge = max(4, min(7, round(box.height * 0.08)))
    bands = []
    for y, left, right in active:
        if not bands or y - bands[-1][1] > bridge:
            bands.append([y, y, left, right])
        else:
            bands[-1][1] = y
            bands[-1][2] = min(bands[-1][2], left)
            bands[-1][3] = max(bands[-1][3], right)
    return bands


def _main_ink_bands(bands):
    """Keep real text rows and fold accent/diacritic islands into them."""

    if not bands:
        return []
    heights = [band[1] - band[0] + 1 for band in bands]
    major_floor = max(3.0, max(heights) * 0.42)
    major = [list(band) for band, height in zip(bands, heights) if height >= major_floor]
    if len(major) < 2:
        return major
    for band, height in zip(bands, heights):
        if height >= major_floor:
            continue
        target = min(
            major,
            key=lambda item: min(abs(band[1] - item[0]), abs(item[1] - band[0])),
        )
        distance = min(abs(band[1] - target[0]), abs(target[1] - band[0]))
        if distance <= max(5.0, (target[1] - target[0] + 1) * 0.5):
            target[0] = min(target[0], band[0])
            target[1] = max(target[1], band[1])
            target[2] = min(target[2], band[2])
            target[3] = max(target[3], band[3])
    return sorted(major, key=lambda item: item[0])


def split_multiline_source_atoms(
    values, source_image=None, source_offset=(0, 0)
) -> list[dict]:
    """Split paragraph-level OCR rectangles into their visible ink rows."""

    values = list(values)
    if source_image is None or source_image.isNull():
        return [_plain_atom(value, _as_text_box(value)) for value in values]
    result = []
    ox, oy = source_offset or (0, 0)
    for value in values:
        box = _as_text_box(value)
        if len(box.text.strip()) < 4 or box.height < 24:
            result.append(_plain_atom(value, box))
            continue
        local = _local_box(box, source_offset)
        background = sample_background(source_image, local)

        # Cheap projection (at most about 96 samples per row) rejects ordinary
        # one-line chat boxes before the expensive pixel pass.  This removes a
        # full-screen-sized scan from every Discord OCR frame.
        probe_step = max(3, round(max(1.0, local.width) / 96.0))
        probe_bands = _main_ink_bands(
            _scan_ink_row_bands(source_image, local, background, probe_step)
        )
        if len(probe_bands) < 2:
            result.append(_plain_atom(value, box))
            continue

        full_step = 2 if local.width >= 120 else 1
        bands = _main_ink_bands(
            _scan_ink_row_bands(source_image, local, background, full_step)
        )
        if len(bands) < 2:
            result.append(_plain_atom(value, box))
            continue
        band_heights = [band[1] - band[0] + 1 for band in bands]
        if box.height < float(median(band_heights)) * 1.65:
            result.append(_plain_atom(value, box))
            continue
        texts = _split_text_for_bands(
            box.text, [band[3] - band[2] for band in bands]
        )
        if len(texts) != len(bands):
            result.append(_plain_atom(value, box))
            continue
        for band, band_text in zip(bands, texts):
            bx1, bx2 = band[2], band[3]
            by1, by2 = band[0], band[1] + 1
            result.append(
                {
                    "x": bx1 + ox,
                    "y": by1 + oy,
                    "width": max(1, bx2 - bx1),
                    "height": max(1, by2 - by1),
                    "text": band_text,
                }
            )
    return result


def split_horizontal_source_atoms(
    values, source_image=None, source_offset=(0, 0)
) -> list[dict]:
    """Expose same-line style runs such as Discord username and timestamp."""

    if source_image is None or source_image.isNull():
        return list(values)
    result = []
    ox, oy = source_offset or (0, 0)
    for value in values:
        box = _as_text_box(value)
        words = box.text.split()
        has_metadata_boundary = bool(
            re.search(
                r"\b\d{1,2}:\d{2}\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
                box.text,
            )
        )
        if (
            len(words) < 2
            or box.width < max(24, box.height * 2)
            or not has_metadata_boundary
        ):
            result.append(value)
            continue
        local = _local_box(box, source_offset)
        background = sample_background(source_image, local)
        text_color = sample_text_color(source_image, local, background)
        contrast = color_distance(text_color, background)
        if contrast < 40:
            result.append(value)
            continue
        threshold = max(28.0, min(68.0, contrast * 0.18))
        x0 = max(0, int(local.x))
        y0 = max(0, int(local.y))
        x1 = min(source_image.width(), int(local.x + local.width))
        y1 = min(source_image.height(), int(local.y + local.height))
        active_columns = []
        for x in range(x0, x1):
            if any(
                color_distance(QColor(source_image.pixel(x, y)), background)
                > threshold
                for y in range(y0, y1)
            ):
                active_columns.append(x)
        segments = []
        bridge = max(2, round(box.height * 0.18))
        for x in active_columns:
            if not segments or x - segments[-1][1] > bridge:
                segments.append([x, x])
            else:
                segments[-1][1] = x
        segments = [segment for segment in segments if segment[1] >= segment[0]]
        if len(segments) < 2:
            result.append(value)
            continue
        while len(segments) > len(words):
            merge_at = min(
                range(len(segments) - 1),
                key=lambda index: segments[index + 1][0] - segments[index][1],
            )
            segments[merge_at][1] = segments[merge_at + 1][1]
            segments.pop(merge_at + 1)
        pieces = _split_text_for_bands(
            box.text, [segment[1] - segment[0] + 1 for segment in segments]
        )
        if len(pieces) != len(segments):
            result.append(value)
            continue
        segment_colors = []
        for segment in segments:
            segment_box = TextBox(
                segment[0],
                local.y,
                segment[1] - segment[0] + 1,
                local.height,
                "",
            )
            segment_bg = sample_background(source_image, segment_box)
            segment_colors.append(
                sample_text_color(source_image, segment_box, segment_bg)
            )
        if segment_colors and max(
            color_distance(first, second)
            for first in segment_colors
            for second in segment_colors
        ) <= 54:
            result.append(value)
            continue
        for segment, piece in zip(segments, pieces):
            result.append(
                {
                    "x": segment[0] + ox,
                    "y": box.y,
                    "width": segment[1] - segment[0] + 1,
                    "height": box.height,
                    "text": piece,
                }
            )
    return result


def analyze_source_atoms(values, source_image=None, source_offset=(0, 0)) -> list[dict]:
    """Attach visual style to raw OCR atoms before any destructive merge."""

    load_config()
    valid_image = source_image is not None and not source_image.isNull()
    result = []
    for value in values:
        box = _as_text_box(value)
        local = _local_box(box, source_offset)
        background = None
        text_color = None
        detected_bold = None
        bold_score = None
        ink_top = None
        ink_height = None
        if valid_image:
            background = sample_background(source_image, local)
            background.setAlpha(255)
            text_color = sample_text_color(source_image, local, background)
            ink_top, ink_height, bold_score = measure_ink_geometry(
                source_image, local, background, text_color=text_color
            )
            detected_bold = bool(
                bold_score is not None and bold_score > BOLD_REL_THICKNESS
            )

        result.append(
            {
                "x": box.x,
                "y": box.y,
                "width": box.width,
                "height": box.height,
                "text": box.text,
                "text_color": color_to_rgba(text_color) if text_color else None,
                "text_rgb": (
                    (text_color.red(), text_color.green(), text_color.blue())
                    if text_color
                    else None
                ),
                "background_color": (
                    color_to_rgba(background) if background else None
                ),
                "background_rgb": (
                    (background.red(), background.green(), background.blue())
                    if background
                    else None
                ),
                "font_family": None,
                "font_size": None,
                "bold": detected_bold,
                "bold_score": bold_score,
                "ink_height": ink_height,
                "ink_top": ink_top,
                "baseline_offset": None,
            }
        )
    return result


def _apply_source_style(box: TextBox, source_image, source_offset) -> TextBox:
    lines = _source_line_boxes(box)
    local_lines = [_local_box(line, source_offset) for line in lines]
    valid_image = source_image is not None and not source_image.isNull()
    precomputed = any(
        value is not None
        for value in (box.font_size, box.text_color, box.background_color, box.bold)
    ) or any(
        any(
            value is not None
            for value in (
                line.font_size,
                line.text_color,
                line.background_color,
                line.bold,
            )
        )
        for line in lines
    )
    detected_bold = box.bold

    if not precomputed:
        background_colors = []
        text_colors = []
        bold_votes = []
        if valid_image:
            for line in local_lines:
                background = sample_background(source_image, line)
                background.setAlpha(255)
                background_colors.append(background)
                text_color = sample_text_color(source_image, line, background)
                text_colors.append(text_color)
                bold_votes.append(
                    estimate_text_boldness(
                        source_image, line, background, text_color=text_color
                    )
                )
        detected_bold = (
            sum(1 for value in bold_votes if value) * 2 >= len(bold_votes)
            if bold_votes
            else False
        )
        if background_colors:
            box.background_color = color_to_rgba(
                find_dominant_color(background_colors)
            )
        if text_colors:
            box.text_color = color_to_rgba(find_dominant_color(text_colors))

    if CONFIG.get("auto_font_weight", 0):
        if box.role == "title":
            box.bold = True
        elif box.role == "body":
            box.bold = False
        else:
            box.bold = bool(detected_bold)
    else:
        box.bold = None

    # Default: one configured UI family for every block (stable). When the user
    # opts into auto_font_family, geometric matching may override per block.
    configured_family = CONFIG.get("font_family") or "Segoe UI"
    box.font_family = configured_family
    if CONFIG.get("auto_font_family", 0):
        inferred_family, inferred_size = _infer_font(
            lines, bool(box.bold) if box.bold is not None else False
        )
        if inferred_family:
            box.font_family = inferred_family
        if inferred_size is not None and box.font_size is None:
            box.font_size = float(inferred_size)
    if box.font_size is None:
        ink_heights = [
            line.ink_height
            for line in lines
            if line.ink_height is not None and line.ink_height > 0
        ]
        box.font_size = float(median(ink_heights)) if ink_heights else None

    if box.font_size is None:
        sizes = [line.font_size for line in lines if line.font_size is not None]
        box.font_size = float(median(sizes)) if sizes else None
    if len(lines) > 1 and box.font_size:
        ordered = sorted(lines, key=lambda line: (line.y, line.x))
        center_distances = [
            (second.y + second.height / 2) - (first.y + first.height / 2)
            for first, second in zip(ordered, ordered[1:])
            if second.y >= first.y
        ]
        if center_distances:
            font = QFont(box.font_family or CONFIG.get("font_family", ""))
            font.setPixelSize(max(1, round(box.font_size)))
            font.setBold(bool(box.bold))
            native_spacing = max(1, QFontMetrics(font).lineSpacing())
            # Cap spacing: loose source leading makes multi-line EN fail to fit
            # and leaves uncovered source between painted lines.
            box.line_spacing = max(
                0.85,
                min(1.25, float(median(center_distances)) / native_spacing),
            )
    if lines and box.font_size and box.baseline_offset is None:
        first = min(lines, key=lambda line: (line.y, line.x))
        font = QFont(box.font_family or CONFIG.get("font_family", ""))
        font.setPixelSize(max(1, round(box.font_size)))
        font.setBold(bool(box.bold))
        tight = QFontMetrics(font).tightBoundingRect(first.text or "Hg")
        first_source = min(
            box.source_lines or [{}],
            key=lambda line: (line.get("y", box.y), line.get("x", box.x)),
        )
        ink_top = first_source.get("ink_top")
        source_top = float(first_source.get("y", first.y)) - box.y
        if ink_top is not None:
            source_top += float(ink_top)
        box.baseline_offset = max(0.0, source_top - tight.top())

    if not CONFIG.get("auto_background", 0):
        box.background_color = None
    if not CONFIG.get("auto_text_color", 0):
        box.text_color = None
    if not CONFIG.get("adaptive_font_size", 1):
        box.font_size = None
        box.baseline_offset = None
        box.line_spacing = None
    return box


def _color_luminance(color_str: Optional[str]) -> float:
    if not color_str:
        return 0.0
    try:
        c = parse_color(color_str)
        return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    except Exception:
        return 0.0


def _default_title_color() -> str:
    """Bright UI title when sampling is missing or inherited body gray."""

    configured = CONFIG.get("text_color")
    if configured and _color_luminance(str(configured)) >= 180:
        return str(configured)
    return "rgba(245, 245, 247, 255)"


def _measured_rgb(box: TextBox):
    return box.text_rgb or _rgb_of(box.text_color)


def _ink_weight(box: TextBox) -> float:
    """How much ink this block owns — its vote when colours are grouped."""

    glyphs = len((box.text or "").strip())
    if not glyphs:
        return 0.0
    size = box.font_size or box.ink_height or box.height or 1.0
    return float(glyphs) * max(1.0, float(size))


def _cluster_measured_colors(boxes: Sequence[TextBox]) -> dict[int, str]:
    """Fold sampling jitter out of measured ink colours, and nothing else.

    Two blocks share one painted colour only when their samples already agreed
    to within `_PALETTE_MERGE_DISTANCE`; a colour the page genuinely reserves
    for a minority of its text — a link, a warning, a dimmed caption, a dark
    label on a light button — keeps its own group instead of being outvoted.
    """

    weighted = []
    for box in boxes:
        rgb = _measured_rgb(box)
        weight = _ink_weight(box)
        if rgb is None or weight <= 0:
            continue
        weighted.append((weight, rgb, box))
    if not weighted:
        return {}
    # Heaviest first, so a group is seeded by the colour most of the ink uses
    # and faint outliers attach to it rather than defining their own centre.
    weighted.sort(key=lambda item: -item[0])

    groups: list[list] = []
    seeds: list[QColor] = []
    for entry in weighted:
        color = QColor(*entry[1])
        for index, seed in enumerate(seeds):
            if color_distance(seed, color) <= _PALETTE_MERGE_DISTANCE:
                groups[index].append(entry)
                break
        else:
            seeds.append(color)
            groups.append([entry])

    resolved: dict[int, str] = {}
    for group in groups:
        total = sum(weight for weight, _rgb, _box in group)
        mean = QColor(
            *(
                round(sum(weight * rgb[channel] for weight, rgb, _box in group) / total)
                for channel in range(3)
            )
        )
        painted = color_to_rgba(clean_text_color(mean))
        for _weight, _rgb, box in group:
            resolved[id(box)] = painted
    return resolved


def _harmonize_role_palette(boxes: Sequence[TextBox]) -> None:
    """Settle text colour across a frame — by measurement, not by role.

    Roles decide weight and leading. They must not decide colour: collapsing
    every body onto one representative is what turned a page of white text
    blue, and forcing every title bright is what erased dark titles on light
    surfaces. Colour comes from the pixels; grouping only removes the noise.
    """

    auto_weight = bool(CONFIG.get("auto_font_weight", 0))
    chat = _is_chat_layout(boxes)

    if chat:
        # Chat invents no titles; a leftover tag is just message text.
        for box in boxes:
            if box.role == "title" and (box.text or "").strip():
                box.role = "body"

    palette = _cluster_measured_colors(boxes)
    for box in boxes:
        painted = palette.get(id(box))
        if painted:
            box.text_color = painted

    for box in boxes:
        if not (box.text or "").strip():
            continue
        if box.role == "title":
            if not box.text_color:
                box.text_color = _default_title_color()
            if auto_weight:
                box.bold = True
            box.line_spacing = _stable_line_spacing("title", box.line_spacing)
        elif box.role == "body":
            if auto_weight:
                box.bold = False
            box.line_spacing = _stable_line_spacing("body", box.line_spacing)
            if chat and (
                not box.text_color or _color_luminance(box.text_color) < 40
            ):
                # Visible text on dark Discord chrome when sampling found none.
                box.text_color = "rgba(220, 221, 222, 255)"


def _source_ink_height(box: TextBox) -> Optional[float]:
    values = [
        float(line["ink_height"])
        for line in box.source_lines or ()
        if line.get("ink_height")
    ]
    if values:
        return float(median(values))
    return float(box.ink_height) if box.ink_height else None


def _normalize_layout_styles(pending: list[TextBox]) -> None:
    """Keep title/body typography consistent across cards in one OCR layout."""

    groups: dict[tuple[int, str], list[TextBox]] = {}
    for box in pending:
        if box.role in ("metadata", "protected"):
            continue
        groups.setdefault((box.layout_id, box.role), []).append(box)

    for role, members in ((key[1], group) for key, group in groups.items()):
        for box in members:
            box.line_spacing = _stable_line_spacing(role, box.line_spacing)
        if not members:
            continue
        sizes = [
            float(box.font_size)
            for box in members
            if box.font_size is not None and box.font_size > 0
        ]
        if sizes:
            common_size = float(median(sizes))
            for box in members:
                box.font_size = common_size
        # A uniform row run is the one grouping the frame actually proved: its
        # rows share a left edge, a glyph size and a pitch. Give every member
        # the run's ink so none of them is sized off its own bad measurement.
        runs: dict[int, list[float]] = {}
        for box in members:
            if box.list_run > 0:
                ink = _source_ink_height(box)
                if ink:
                    runs.setdefault(box.list_run, []).append(ink)
        for box in members:
            run_inks = runs.get(box.list_run)
            if run_inks:
                box.run_ink = float(median(run_inks))
        if role == "title":
            for box in members:
                if CONFIG.get("auto_font_weight", 0):
                    box.bold = True
        elif role == "body":
            for box in members:
                if CONFIG.get("auto_font_weight", 0):
                    box.bold = False
    _harmonize_role_palette(pending)


def _dump_pending_boxes(boxes: Sequence[TextBox], source_image) -> None:
    """Record what OCR produced, so a bad overlay can be diagnosed from data."""

    if not CONFIG.get("debug_dump", 0):
        return
    try:
        path = Path("userconfig/overlay_dump.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "has_source_image": bool(
                source_image is not None and not source_image.isNull()
            ),
            "block_count": len(boxes),
            "blocks": [
                {
                    "x": box.x,
                    "y": box.y,
                    "width": box.width,
                    "height": box.height,
                    "text": box.text,
                    "role": box.role,
                    "list_item": box.list_item,
                    "font_size": box.font_size,
                    "ink_height": box.ink_height,
                    "text_color": box.text_color,
                    "bold": box.bold,
                    "source_lines": [
                        {
                            key: line.get(key)
                            for key in ("x", "y", "width", "height", "text", "ink_height")
                        }
                        for line in box.source_lines
                    ],
                }
                for box in boxes
            ],
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_pending_boxes(boxes, source_image=None, source_offset=(0, 0)) -> list[int]:
    """Register immutable block geometry and return stable translation markers."""

    global PENDING_BOXES
    load_config()
    # Style sampling can be slow; do it outside the lock. Only the map
    # mutations below must be atomic vs UI-thread parse/render.
    prepared = [
        _apply_source_style(_as_text_box(value), source_image, source_offset)
        for value in boxes
    ]
    with _PENDING_LOCK:
        pending = []
        marker_ids = []
        layout_id = next(_LAYOUT_IDS)
        for box in prepared:
            marker_id = next(_MARKER_IDS)
            box.layout_id = layout_id
            box.marker_id = marker_id
            pending.append(box)
            marker_ids.append(marker_id)
        _normalize_layout_styles(pending)
        for box in pending:
            _PENDING_BY_MARKER[box.marker_id] = replace(
                box, source_lines=list(box.source_lines)
            )
        _LAYOUT_MARKERS[layout_id] = marker_ids
        while len(_PENDING_BY_MARKER) > _MAX_PENDING_MARKERS:
            old_marker, old_box = _PENDING_BY_MARKER.popitem(last=False)
            for key in [key for key in _TRANSLATED_BY_STREAM if key[1] == old_marker]:
                _TRANSLATED_BY_STREAM.pop(key, None)
            if old_box.layout_id in _LAYOUT_MARKERS:
                remaining = [
                    marker
                    for marker in _LAYOUT_MARKERS[old_box.layout_id]
                    if marker in _PENDING_BY_MARKER
                ]
                if remaining:
                    _LAYOUT_MARKERS[old_box.layout_id] = remaining
                else:
                    _LAYOUT_MARKERS.pop(old_box.layout_id, None)
        PENDING_BOXES = pending
    _dump_pending_boxes(pending, source_image)
    return list(marker_ids)


def distribute_lines(orig_boxes, trans_lines: List[str]) -> List[TextBox]:
    """Conservative fallback for translators that remove every block marker.

    Geometry is never unioned and text is never pushed outside its source box.
    Ambiguous cardinalities are rejected because showing no overlay is safer
    than painting a title translation across several unrelated cards.
    """

    sources = [_as_text_box(box) for box in orig_boxes]
    values = [value.strip() for value in trans_lines if value.strip()]
    if not sources or not values:
        return []
    if len(sources) == 1:
        return [replace(sources[0], text="\n".join(values))]
    if len(sources) != len(values):
        return []
    return [replace(source, text=value) for source, value in zip(sources, values)]


def is_effectively_empty(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if not re.search(r"[\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t):
        return True
    return False


def parse_indexed_boxes(text: str, orig_boxes=None, stream_id=None) -> List[TextBox]:
    segments = parse_indexed_segments(text)
    if not segments:
        return []

    with _PENDING_LOCK:
        resolved = [
            (marker_id, value, _PENDING_BY_MARKER.get(marker_id))
            for marker_id, value in segments
        ]
        resolved = [item for item in resolved if item[2] is not None]

        if not resolved:
            return []

        stream_key = str(stream_id or "default")
        layout_ids = []
        for marker_id, value, source in resolved:
            if source.layout_id not in layout_ids:
                layout_ids.append(source.layout_id)
            if source.role not in ("metadata", "protected") and value.strip():
                _TRANSLATED_BY_STREAM[(stream_key, marker_id)] = value.strip()

        result = []
        for layout_id in layout_ids:
            for marker_id in _LAYOUT_MARKERS.get(layout_id, []):
                source = _PENDING_BY_MARKER.get(marker_id)
                if source is None:
                    continue
                value = _TRANSLATED_BY_STREAM.get((stream_key, marker_id))
                if value:
                    result.append(
                        replace(
                            source,
                            text=value,
                            source_lines=list(source.source_lines),
                        )
                    )
                else:
                    # Untranslated title/body still need cover geometry so the
                    # original line does not peek between translated neighbours
                    # (the leftover tiny lines on HOOK/OCR cards).
                    result.append(
                        replace(
                            source,
                            text="",
                            source_lines=list(source.source_lines),
                        )
                    )
        return result


def _close_small_mask_gaps(masks: list) -> list:
    """Expand neighbouring line masks so tight body lines fully hide source."""

    if len(masks) < 2:
        return masks
    ordered = sorted(
        [(rect, brush) for rect, brush in masks if rect is not None],
        key=lambda item: (item[0].top(), item[0].left()),
    )
    if len(ordered) < 2:
        return masks
    closed = [ordered[0]]
    for rect, brush in ordered[1:]:
        prev_rect, prev_brush = closed[-1]
        gap = rect.top() - prev_rect.bottom()
        threshold = max(4, int(max(prev_rect.height(), rect.height()) * 0.85))
        horizontal_overlap = min(prev_rect.right(), rect.right()) - max(
            prev_rect.left(), rect.left()
        )
        if (
            0 < gap <= threshold
            and horizontal_overlap >= 0.35 * min(prev_rect.width(), rect.width())
        ):
            mid = (prev_rect.bottom() + rect.top()) // 2
            closed[-1] = (
                QRect(
                    prev_rect.left(),
                    prev_rect.top(),
                    prev_rect.width(),
                    max(1, mid - prev_rect.top() + 1),
                ),
                prev_brush,
            )
            rect = QRect(
                rect.left(),
                mid,
                rect.width(),
                max(1, rect.bottom() - mid + 1),
            )
        closed.append((rect, brush))
    return closed


def _significant_paint_overlap(
    first: QRect, second: QRect, *, threshold: float = 0.18, min_area: int = 24
) -> bool:
    """Ignore tiny edge touches so title/body of the same card both render."""

    inter = first.intersected(second)
    if inter.isEmpty():
        return False
    area = max(1, inter.width() * inter.height())
    smaller = max(1, min(first.width() * first.height(), second.width() * second.height()))
    return area >= max(min_area, int(smaller * threshold))


def _label_cover_rect(label) -> QRect:
    """Opaque paint footprint of a label (background), in overlay coords."""

    geo = label.geometry()
    bg = getattr(label, "_background_rect", None)
    if bg is None or bg.isEmpty():
        return QRect(geo)
    return QRect(
        geo.x() + bg.x(),
        geo.y() + bg.y(),
        max(1, bg.width()),
        max(1, bg.height()),
    )


def _intersection_over_union(first: TextBox, second: TextBox) -> float:
    x1 = max(first.x, second.x)
    y1 = max(first.y, second.y)
    x2 = min(first.x + first.width, second.x + second.width)
    y2 = min(first.y + first.height, second.y + second.height)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = first.width * first.height + second.width * second.height - intersection
    return intersection / max(1.0, union)


def _copy_matching_source_style(box: TextBox) -> TextBox:
    candidates = list(_PENDING_BY_MARKER.values())[-256:] or PENDING_BOXES
    if not candidates:
        return box
    source = max(candidates, key=lambda item: _intersection_over_union(box, item))
    if _intersection_over_union(box, source) < 0.35:
        return box
    return replace(
        source,
        x=box.x,
        y=box.y,
        width=box.width,
        height=box.height,
        text=box.text,
    )



def _join_fragment_text(parts: Sequence[str]) -> str:
    """Join translated fragments without leaving ', foo' or lone '.' rows."""

    out = ""
    for raw in parts:
        part = (raw or "").strip()
        if not part:
            continue
        if not out:
            out = part
            continue
        if part[0] in _LEADING_PUNCT or part in _LEADING_PUNCT:
            out = out.rstrip() + part
        else:
            out = out.rstrip() + " " + part
    return out


def _looks_like_card_header(text: str) -> bool:
    """Short header only — not mid-paragraph phrases like 'nhúng trực tiếp'."""

    text = (text or "").strip()
    if not text or text[0] in _LEADING_PUNCT:
        return False
    if text.endswith((",", ";", ":")):
        return False
    words = text.split()
    # Real card titles: HOOK, OCR, Học ngôn ngữ, API dịch thuật phong phú
    # Mid-body chips are often 2–4 words too — require header-ish shape:
    # short, no sentence punctuation in the middle.
    if len(text) > 42 or len(words) > 5:
        return False
    if any(ch in text for ch in ".!?"):
        return False
    return True


def _region_key(box: TextBox):
    if box.region_id > 0:
        return (box.layout_id, box.region_id)
    # Fallback: cluster by layout + rough column so card bodies still merge.
    return (box.layout_id, "col", int(box.x // 80), int(box.y // 220))


def _is_chat_layout(boxes: Sequence[TextBox]) -> bool:
    """Discord/chat: usernames + timestamps, or multi-message left column."""

    if any(box.role in ("metadata", "protected") for box in boxes):
        return True
    # Explicit card titles → not chat.
    if any(box.role == "title" and (box.text or "").strip() for box in boxes):
        return False
    bodies = [
        box for box in boxes if box.role == "body" and (box.text or "").strip()
    ]
    if len(bodies) < 3:
        return False
    regions = {box.region_id for box in bodies if box.region_id > 0}
    # Several distinct message regions stacked on the left.
    if len(regions) >= 3:
        xs = [box.x for box in bodies]
        if max(xs) - min(xs) <= 120:
            return True
    return False


def _is_card_layout(boxes: Sequence[TextBox]) -> bool:
    """Feature cards: title+body pairs and/or a multi-column grid."""

    if _is_chat_layout(boxes):
        return False
    titles = [box for box in boxes if box.role == "title" and (box.text or "").strip()]
    bodies = [box for box in boxes if box.role == "body" and (box.text or "").strip()]
    if titles and bodies:
        return True
    regions = {
        (box.layout_id, box.region_id)
        for box in boxes
        if box.region_id > 0 and box.role in ("body", "title")
    }
    if len(regions) >= 2:
        xs = [box.x for box in boxes if box.role in ("body", "title")]
        if xs and max(xs) - min(xs) > 160:
            return True
    # Single-region HOOK-style: short first body + longer body below.
    if len(bodies) >= 2:
        ordered = sorted(bodies, key=lambda box: (box.y, box.x))
        if _looks_like_card_header(ordered[0].text) and len(ordered[1].text) > len(
            ordered[0].text
        ):
            return True
    return False


def _dominant_ink_rgb(boxes: Sequence[TextBox]):
    """The colour most of these blocks' ink is drawn in, weighted by ink."""

    weights: dict[tuple, float] = {}
    for box in boxes:
        rgb = _measured_rgb(box)
        weight = _ink_weight(box)
        if rgb is None or weight <= 0:
            continue
        key = tuple(rgb)
        weights[key] = weights.get(key, 0.0) + weight
    if not weights:
        return None
    return max(weights.items(), key=lambda item: item[1])[0]


def _merge_body_run(run: Sequence[TextBox]) -> TextBox:
    ordered = sorted(run, key=lambda box: (box.y, box.x, box.marker_id))
    first = ordered[0]
    x1 = min(box.x for box in ordered)
    y1 = min(box.y for box in ordered)
    x2 = max(box.x + box.width for box in ordered)
    y2 = max(box.y + box.height for box in ordered)
    source_lines: list[dict] = []
    for box in ordered:
        if box.source_lines:
            source_lines.extend(dict(line) for line in box.source_lines)
        else:
            source_lines.append(
                {
                    "x": box.x,
                    "y": box.y,
                    "width": box.width,
                    "height": box.height,
                    "text": box.text,
                    "font_size": box.font_size,
                    "ink_height": box.ink_height,
                }
            )
    styled = [box for box in ordered if (box.text or "").strip()] or list(ordered)
    sizes = [box.font_size for box in styled if box.font_size]
    backgrounds = [box.background_color for box in styled if box.background_color]
    # Longest line in the run decides the colour, not the most numerous: a
    # wrapped paragraph is one colour, and two short stragglers must not
    # outvote the line that carries most of its ink.
    merged_rgb = _dominant_ink_rgb(styled)
    return replace(
        first,
        x=x1,
        y=y1,
        width=max(1.0, x2 - x1),
        height=max(1.0, y2 - y1),
        text=_join_fragment_text([box.text for box in ordered]),
        role="body",
        source_lines=source_lines,
        font_size=float(median(sizes)) if sizes else first.font_size,
        text_color=(
            color_to_rgba(QColor(*merged_rgb)) if merged_rgb else first.text_color
        ),
        text_rgb=merged_rgb or first.text_rgb,
        bold=False,
        background_color=(
            Counter(backgrounds).most_common(1)[0][0]
            if backgrounds
            else first.background_color
        ),
        baseline_offset=None,
        line_spacing=_stable_line_spacing("body", first.line_spacing),
        alignment=first.alignment or "left",
    )


def _title_style_from_source_line(line: Optional[dict], fallback: TextBox) -> dict:
    """Style for a peeled/promoted title — never inherit body gray/weight."""

    auto_weight = bool(CONFIG.get("auto_font_weight", 0))
    color = None
    # Only a colour read off this title's own pixels counts as measured; an
    # invented one must stay out of the palette grouping downstream.
    rgb = None
    font_size = None
    if line:
        color = line.get("text_color")
        rgb = line.get("text_rgb") or _rgb_of(color)
        if line.get("font_size"):
            font_size = float(line["font_size"])
        elif line.get("ink_height"):
            font_size = float(line["ink_height"]) * 1.22
    if rgb is None:
        # Nothing sampled for this line — inherit the block's own measurement
        # before inventing a colour for it.
        color = fallback.text_color
        rgb = fallback.text_rgb or _rgb_of(fallback.text_color)
    if rgb is None:
        # Never measured anywhere: a bright default beats inherited body grey.
        color = _default_title_color()
    if font_size is None and fallback.font_size:
        # Titles slightly larger than the body they were peeled from.
        font_size = float(fallback.font_size) * 1.12
    return {
        "text_color": color,
        "text_rgb": tuple(rgb) if rgb else None,
        "font_size": font_size,
        "bold": True if auto_weight else fallback.bold,
    }


def _promote_title_box(box: TextBox, source_line: Optional[dict] = None) -> TextBox:
    style = _title_style_from_source_line(source_line, box)
    return replace(
        box,
        role="title",
        bold=style["bold"],
        text_color=style["text_color"],
        text_rgb=style["text_rgb"],
        font_size=style["font_size"] if style["font_size"] else box.font_size,
        line_spacing=_stable_line_spacing("title", box.line_spacing),
        baseline_offset=None,
    )


def _peel_merged_title_from_body(box: TextBox) -> Optional[tuple[TextBox, TextBox]]:
    """Split card title glued into body — only with strong OCR geometry evidence.

    Never invent titles from sentence openers ('Bạn có', 'Khi tôi', 'You can').
    Requires a distinct first source line that looks like a card header AND is
    separated from the body band (not just a wrapped paragraph line).
    """

    text = (box.text or "").strip()
    if not text or box.role == "title":
        return None
    words = text.split()
    if len(words) < 5:
        return None
    if not box.source_lines or len(box.source_lines) < 2:
        # No geometric title band → never peel (prevents Discord false titles).
        return None

    ordered_src = sorted(
        box.source_lines,
        key=lambda line: (line.get("y", 0), line.get("x", 0)),
    )
    first_src = ordered_src[0]
    rest_src = list(ordered_src[1:])
    first_src_text = str(first_src.get("text") or "").strip()
    if not _looks_like_card_header(first_src_text):
        return None

    first_h = float(first_src.get("height") or first_src.get("ink_height") or 0) or 20.0
    second = rest_src[0]
    second_h = float(second.get("height") or second.get("ink_height") or 0) or first_h
    gap = float(second.get("y", 0)) - (
        float(first_src.get("y", 0)) + float(first_src.get("height", first_h))
    )
    # Wrapped body lines sit close with similar height; card title sits above
    # with a clearer gap and/or larger ink.
    first_ink = float(first_src.get("ink_height") or first_h)
    second_ink = float(second.get("ink_height") or second_h)
    larger_title = first_ink >= second_ink * 1.06
    clear_gap = gap >= max(6.0, first_h * 0.45)
    if not larger_title and not clear_gap:
        return None
    if gap < first_h * 0.35 and abs(first_h - second_h) <= first_h * 0.22:
        # Same-paragraph wrap — not a title.
        return None

    hint_n = max(1, len(first_src_text.split()))
    # Only peel near the source title word count (no free 2-word guessing).
    candidates = list(range(max(2, hint_n - 1), min(len(words), hint_n + 2)))
    best = None
    for n in candidates:
        if n >= len(words):
            continue
        head = " ".join(words[:n])
        tail = " ".join(words[n:]).strip()
        if not _looks_like_card_header(head) or head.endswith(","):
            continue
        if len(tail) < max(16, len(head) + 8):
            continue
        score = abs(n - hint_n)
        tx = float(first_src.get("x", box.x))
        ty = float(first_src.get("y", box.y))
        tw = float(first_src.get("width", box.width))
        th = float(first_src.get("height", max(18.0, box.height * 0.22)))
        bx1 = min(float(line.get("x", box.x)) for line in rest_src)
        by1 = min(float(line.get("y", box.y)) for line in rest_src)
        bx2 = max(
            float(line.get("x", box.x)) + float(line.get("width", box.width))
            for line in rest_src
        )
        by2 = max(
            float(line.get("y", box.y)) + float(line.get("height", 18))
            for line in rest_src
        )
        title = _promote_title_box(
            replace(
                box,
                x=tx,
                y=ty,
                width=max(1.0, tw),
                height=max(1.0, th),
                text=head,
                role="title",
                text_color=None,
                bold=None,
                source_lines=[dict(first_src)],
                baseline_offset=None,
            ),
            source_line=first_src,
        )
        body = replace(
            box,
            x=bx1,
            y=by1,
            width=max(1.0, bx2 - bx1),
            height=max(1.0, by2 - by1),
            text=tail,
            role="body",
            bold=False,
            source_lines=[dict(line) for line in rest_src],
            baseline_offset=None,
            line_spacing=_stable_line_spacing("body", box.line_spacing),
        )
        cand = (score, title, body)
        if best is None or cand[0] < best[0]:
            best = cand
    return best


def _boxes_share_visual_line(a: TextBox, b: TextBox) -> bool:
    """True only when two OCR crumbs sit on the same horizontal text line."""

    y_overlap = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    if y_overlap < 0.55 * min(a.height, b.height):
        return False
    # Prefer horizontal neighbours (same line continuation), not stacked rows.
    if b.y >= a.y + a.height * 0.65:
        return False
    x_gap = max(0.0, b.x - (a.x + a.width), a.x - (b.x + b.width))
    return x_gap <= max(24.0, min(a.height, b.height) * 1.2)


def _boxes_same_message_stack(a: TextBox, b: TextBox) -> bool:
    """Stacked OCR lines of one chat bubble (not the next message).

    Same-message lines sit almost edge-to-edge. A new Discord message has a
    username/timestamp row between bodies, so body-to-body gap is much larger.
    """

    if a.list_item or b.list_item:
        return False
    line_h = max(1.0, min(a.height, b.height))
    if abs(a.x - b.x) > max(48.0, line_h * 2.5):
        return False
    # Box heights carry the engine's padding, so a gap limit derived from them
    # grows with that padding until list rows read as one tight stack. Glyph
    # heights do not, so require plausible leading in those units too.
    glyph = min(
        a.ink_height or a.font_size or a.height,
        b.ink_height or b.font_size or b.height,
    )
    if glyph > 0:
        pitch = (b.y + b.height / 2.0) - (a.y + a.height / 2.0)
        if pitch > glyph * 2.05:
            return False
    # b must be below a (small tolerance for OCR jitter).
    if b.y + line_h * 0.35 < a.y + a.height * 0.35:
        return False
    gap = b.y - (a.y + a.height)
    if gap < -0.4 * line_h:
        return False
    # Tight stack only — larger gaps are inter-message padding.
    if gap > max(10.0, line_h * 0.65):
        return False
    wa, wb = max(1.0, a.width), max(1.0, b.width)
    if min(wa, wb) / max(wa, wb) < 0.42:
        return False
    return True


def _apply_chat_height_caps(boxes: Sequence[TextBox]) -> list[TextBox]:
    """Limit growth of each chat body to free space above the next OCR box.

    Uncapped downward growth was the main Discord failure mode: long Vietnamese
    wraps expanded into the next message, collision dropped that message
    (missing text), and the tall cover looked like a solid clump.
    """

    if not boxes:
        return []
    ordered = sorted(
        enumerate(boxes),
        key=lambda item: (item[1].y, item[1].x, item[0]),
    )
    items = [box for _, box in ordered]
    result = list(boxes)
    for pos, box in enumerate(items):
        if (box.role or "body") not in ("body", "title"):
            continue
        if not (box.text or "").strip():
            continue
        limit_bottom = None
        for other in items[pos + 1 :]:
            overlap = min(box.x + box.width, other.x + other.width) - max(
                box.x, other.x
            )
            if overlap < 0.15 * min(box.width, max(1.0, other.width)):
                continue
            if other.y < box.y + 1.0:
                continue
            # Leave a 3px gutter so covers/ink never seal over the next row.
            candidate = other.y - 3.0
            if limit_bottom is None or candidate < limit_bottom:
                limit_bottom = candidate
        if limit_bottom is None:
            max_h = float(box.height) + 160.0
        elif limit_bottom <= box.y + box.height:
            # Already tight against the next line — no growth allowed.
            max_h = float(box.height)
        else:
            max_h = float(limit_bottom - box.y)
        # Attach without disturbing dataclass equality helpers.
        capped = replace(box)
        capped._chat_max_height = max(float(box.height), max_h)
        # Put back at original index.
        orig_index = ordered[pos][0]
        result[orig_index] = capped
    return result


def _coalesce_chat_boxes(boxes: Sequence[TextBox]) -> list[TextBox]:
    """Discord/chat: one paint box per message (or per visual line crumb).

    - Glue horizontal crumbs on the same visual line.
    - Glue tightly stacked lines of the *same* bubble so Vietnamese can reflow
      inside the real message band (avoids per-line growth wars).
    - Never cross username/timestamp chrome or large vertical gaps (next message).
    """

    result: list[TextBox] = []
    ordered = sorted(
        boxes, key=lambda box: (box.layout_id, box.region_id, box.y, box.x, box.marker_id)
    )
    i = 0
    while i < len(ordered):
        cur = ordered[i]
        if cur.role in ("metadata", "protected"):
            result.append(replace(cur, text=""))
            i += 1
            continue
        if cur.role == "title":
            cur = replace(cur, role="body", bold=False, text_color=cur.text_color)
        if cur.role != "body":
            result.append(cur)
            i += 1
            continue
        if not (cur.text or "").strip():
            result.append(replace(cur, role="body", bold=False))
            i += 1
            continue

        run = [cur]
        j = i + 1
        while j < len(ordered):
            nxt = ordered[j]
            if nxt.role in ("metadata", "protected"):
                break
            if nxt.role == "title":
                nxt = replace(nxt, role="body", bold=False)
            if nxt.role != "body" or not (nxt.text or "").strip():
                break
            if cur.layout_id != nxt.layout_id:
                break
            prev = run[-1]
            if _boxes_share_visual_line(prev, nxt) or _boxes_same_message_stack(
                prev, nxt
            ):
                run.append(nxt)
                j += 1
                continue
            break

        if len(run) == 1:
            result.append(replace(run[0], role="body", bold=False))
        else:
            result.append(_merge_body_run(run))
        i = j

    result.sort(key=lambda box: (box.layout_id, box.region_id, box.y, box.x))
    return _apply_chat_height_caps(result)


def _rgb_of(color_str: Optional[str]):
    if not color_str:
        return None
    color = parse_color(color_str)
    return (color.red(), color.green(), color.blue())


def _paragraph_continuation(previous: TextBox, nxt: TextBox) -> bool:
    """True only when two paint blocks are one paragraph the OCR split up.

    Everything else in a region — a console line under a prompt, the next
    option under a description — was separated deliberately upstream, where the
    whole frame was visible. Rejoining it here rebuilds the blob.
    """

    if previous.list_item or nxt.list_item:
        return False
    # A line the card demoted from title was mis-measured, not differently
    # styled: it belongs back in the paragraph whatever it looks like — but
    # only if it is the next line, not the next item further down the page.
    if previous.demoted or nxt.demoted:
        room = 1.5 * max(1.0, min(previous.height, nxt.height))
        return nxt.y - (previous.y + previous.height) <= room
    first_rgb = previous.text_rgb or _rgb_of(previous.text_color)
    second_rgb = nxt.text_rgb or _rgb_of(nxt.text_color)
    if first_rgb and second_rgb:
        if sum(abs(int(a) - int(b)) for a, b in zip(first_rgb, second_rgb)) > 45:
            return False
    glyph = min(
        previous.font_size or previous.ink_height or previous.height,
        nxt.font_size or nxt.ink_height or nxt.height,
    )
    if glyph <= 0:
        return False
    if nxt.y - (previous.y + previous.height) > glyph * 1.15:
        return False
    return abs(previous.x - nxt.x) <= max(24.0, glyph * 2.0)


def _split_paragraph_runs(
    body_text: Sequence[TextBox], body_empty: Sequence[TextBox]
) -> list[list[TextBox]]:
    """Cut a region's bodies into runs that each really are one paragraph."""

    runs: list[list[TextBox]] = []
    for box in sorted(body_text, key=lambda item: (item.y, item.x, item.marker_id)):
        if runs and _paragraph_continuation(runs[-1][-1], box):
            runs[-1].append(box)
        else:
            runs.append([box])
    # Cover-only boxes keep their own geometry; folding them into a neighbour
    # would stretch that neighbour over the row it is meant to hide.
    runs.extend([box] for box in body_empty)
    return runs


def _coalesce_card_boxes(boxes: Sequence[TextBox]) -> list[TextBox]:
    """Feature-card grid: one title + one body paragraph per card."""

    if len(boxes) <= 1:
        if boxes and boxes[0].role == "body" and (boxes[0].text or "").strip():
            peeled = _peel_merged_title_from_body(boxes[0])
            if peeled is not None:
                _score, title, body = peeled
                return [title, body]
        return list(boxes)

    groups: dict = {}
    passthrough: list[TextBox] = []
    for box in boxes:
        if box.role in ("metadata", "protected"):
            passthrough.append(box)
            continue
        if box.list_item:
            # Menu rows share a region with the card around them; merging them
            # into that card's paragraph is what produced the sidebar blob.
            passthrough.append(replace(box, role="body", bold=box.bold))
            continue
        groups.setdefault(_region_key(box), []).append(box)

    result: list[TextBox] = list(passthrough)
    for members in groups.values():
        members = sorted(members, key=lambda box: (box.y, box.x, box.marker_id))
        titles = [
            box
            for box in members
            if box.role == "title" and (box.text or "").strip()
        ]
        bodies = [
            box
            for box in members
            if box.role != "title" or not (box.text or "").strip()
        ]
        if titles:
            titles = sorted(titles, key=lambda box: (box.y, box.x))
            header = _promote_title_box(titles[0])
            result.append(header)
            for extra in titles[1:]:
                bodies.append(replace(extra, role="body", bold=False, demoted=True))
        else:
            text_bodies = [box for box in bodies if (box.text or "").strip()]
            text_bodies.sort(key=lambda box: (box.y, box.x))
            if (
                len(text_bodies) >= 2
                and _looks_like_card_header(text_bodies[0].text)
                and len(text_bodies[1].text.strip())
                > len(text_bodies[0].text.strip())
            ):
                header = _promote_title_box(text_bodies[0])
                result.append(header)
                bodies = [box for box in bodies if box is not text_bodies[0]]

        body_text = [box for box in bodies if (box.text or "").strip()]
        body_empty = [box for box in bodies if not (box.text or "").strip()]
        for run in _split_paragraph_runs(body_text, body_empty):
            if len(run) == 1 and (run[0].text or "").strip():
                single = replace(run[0], role="body", bold=False)
                has_title = any(
                    box.role == "title" and (box.text or "").strip()
                    for box in result
                    if _region_key(box) == _region_key(single)
                )
                if not has_title:
                    peeled = _peel_merged_title_from_body(single)
                    if peeled is not None:
                        _score, title, body = peeled
                        result.append(title)
                        result.append(body)
                    else:
                        result.append(single)
                else:
                    result.append(single)
            elif run:
                merged_body = _merge_body_run(run)
                has_title = any(
                    box.role == "title" and (box.text or "").strip()
                    for box in result
                    if _region_key(box) == _region_key(merged_body)
                )
                if not has_title:
                    peeled = _peel_merged_title_from_body(merged_body)
                    if peeled is not None:
                        _score, title, body = peeled
                        result.append(title)
                        result.append(body)
                    else:
                        result.append(merged_body)
                else:
                    result.append(merged_body)

    result.sort(key=lambda box: (box.layout_id, box.region_id, box.y, box.x))
    return result


def _coalesce_paragraph_boxes(boxes: Sequence[TextBox]) -> list[TextBox]:
    """Generic document text: no titles, fuse only near-duplicate OCR crumbs."""

    # Reuse chat's conservative stitch (no title peel / invent).
    cleaned = [
        replace(box, role="body", bold=False)
        if box.role == "title" and (box.text or "").strip()
        else box
        for box in boxes
    ]
    return _coalesce_chat_boxes(cleaned)


def _coalesce_list_boxes(boxes: Sequence[TextBox]) -> list[TextBox]:
    """Menu/option rows: keep every row exactly where the source put it."""

    result = []
    for box in boxes:
        if box.role in ("metadata", "protected"):
            result.append(replace(box, text=""))
        else:
            result.append(box)
    result.sort(key=lambda box: (box.layout_id, box.y, box.x))
    # Rows sit close together, so a long translation must not grow down into
    # the next row the way a free-standing paragraph may.
    return _apply_chat_height_caps(result)


def _coalesce_flowing_boxes(boxes: Sequence[TextBox]) -> list[TextBox]:
    """Everything that is not a menu row: chat, cards, or plain document text."""

    if _is_chat_layout(boxes):
        for box in boxes:
            if box.layout_id > 0:
                _CHAT_LAYOUT_IDS.add(box.layout_id)
        return _coalesce_chat_boxes(boxes)
    if _is_card_layout(boxes):
        return _coalesce_card_boxes(boxes)
    return _coalesce_paragraph_boxes(boxes)


def coalesce_paint_boxes(boxes: Sequence[TextBox]) -> list[TextBox]:
    """Pick display strategy from layout type (list / chat / card / paragraph)."""

    if not boxes:
        return []
    # Which boxes are menu rows was decided during segmentation, row by row,
    # with the whole frame in view — including rows that were never
    # translated. A window that is half sidebar and half article is both, so
    # one verdict for all of it either rebuilds the sidebar blob or leaves the
    # article's paragraphs in the pieces the OCR happened to cut.
    listed = [box for box in boxes if box.list_item]
    if not listed:
        return _coalesce_flowing_boxes(boxes)
    flowing = [box for box in boxes if not box.list_item]
    if not flowing:
        return _coalesce_list_boxes(boxes)
    merged = _coalesce_list_boxes(listed) + _coalesce_flowing_boxes(flowing)
    merged.sort(key=lambda box: (box.layout_id, box.region_id, box.y, box.x))
    return merged


def parse_boxes(text: str, stream_id=None) -> List[TextBox]:
    text = re.sub(r"^\[Engine\].*?(\n|$)", "", text.strip())
    # Kiểm tra xem có chứa tọa độ [x y|w h] hay không
    matches = list(BOX_PATTERN.finditer(text))
    if matches:
        boxes: List[TextBox] = []
        for match in matches:
            value = match.group("text").strip()
            if not value:
                continue
            boxes.append(
                _copy_matching_source_style(TextBox(
                    x=int(match.group("x")),
                    y=int(match.group("y")),
                    width=int(match.group("w")),
                    height=int(match.group("h")),
                    text=value,
                ))
            )
        merged = coalesce_paint_boxes(boxes)
        _harmonize_role_palette(merged)
        return merged
    else:
        # Nếu không chứa tọa độ, lấy từ PENDING_BOXES
        global PENDING_BOXES
        if not PENDING_BOXES:
            return []

        indexed_boxes = parse_indexed_boxes(text, PENDING_BOXES, stream_id=stream_id)
        if indexed_boxes:
            merged = coalesce_paint_boxes(indexed_boxes)
            _harmonize_role_palette(merged)
            return merged

        # Without coordinates or stable markers there is no safe way to know
        # whether this translation belongs to the current OCR frame. Reusing
        # the last global boxes here used to paint hook/clipboard translations
        # over stale OCR geometry and to merge unrelated blocks.
        return []


class StrokedLabel(QLabel):
    def __init__(
        self,
        *args,
        skip_background: bool = False,
        background_color: Optional[str] = None,
        text_color: Optional[str] = None,
        stroke_color: Optional[str] = None,
        stroke_width: Optional[float] = None,
        alignment: str = "left",
        line_spacing: Optional[float] = None,
        baseline_offset: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._skip_background = skip_background
        self._custom_background_color = background_color
        self._custom_text_color = text_color
        self._custom_stroke_color = stroke_color
        self._custom_stroke_width = stroke_width
        self._text_alignment = alignment
        self._line_spacing = line_spacing
        self._baseline_offset = baseline_offset

    def paintEvent(self, event):
        background_color = self._custom_background_color or CONFIG["background_color"]
        text_color = self._custom_text_color or CONFIG["text_color"]
        stroke_color = self._custom_stroke_color or CONFIG["stroke_color"]
        stroke_width = float(
            CONFIG["stroke_width"]
            if self._custom_stroke_width is None
            else self._custom_stroke_width
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self._skip_background:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(parse_color(background_color))
            painter.drawRoundedRect(
                getattr(self, "_background_rect", self.rect()), 4, 4
            )

        text = self.text()
        if not text:
            return
        font = self.font()
        painter.setFont(font)
        metrics = QFontMetrics(font)
        paint_margin = int(getattr(self, "_paint_margin", 0))
        left = (
            self.rect().left()
            + int(CONFIG["horizontal_padding"] / 2)
            + paint_margin
        )
        target_w = max(
            1,
            self.rect().width()
            - int(CONFIG["horizontal_padding"])
            - paint_margin * 2,
        )
        right = left + target_w
        lines = getattr(self, "_layout_lines", None) or wrap_text(
            text, font, target_w
        )
        line_advance = max(
            1,
            round(metrics.lineSpacing() * float(self._line_spacing or 1.0)),
        )
        total_h = metrics.height() + line_advance * max(0, len(lines) - 1)
        if self._baseline_offset is not None:
            y = self.rect().top() + self._baseline_offset
        else:
            y = self.rect().top() + max(0, (self.rect().height() - total_h) // 2) + metrics.ascent()

        # Tính baseline mỗi dòng. Stroke (viền) chỉ vẽ khi width > 0 — nếu
        # width = 0 mà vẫn drawPath thì QPen width 0 = pen cosmetic 1px,
        # tạo ra viền mảnh thừa + làm chữ trông răng cưa.
        # Fill (ruột chữ): luôn vẽ bằng native drawText (có hinting) cho mượt.
        line_positions = []
        for line in lines:
            line_w = text_width(metrics, line)
            if self._text_alignment == "center":
                line_x = left + max(0, (target_w - line_w) // 2)
            elif self._text_alignment == "right":
                line_x = right - line_w
            else:
                line_x = left
            line_positions.append((line, line_x, y))
            y += line_advance

        if stroke_width > 0:
            path = QPainterPath()
            for line, lx, ly in line_positions:
                path.addText(lx, ly, font, line)
            painter.setPen(
                QPen(
                    parse_color(stroke_color),
                    stroke_width,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        painter.setPen(parse_color(text_color))
        for line, lx, ly in line_positions:
            painter.drawText(int(lx), int(ly), line)


def _box_in_chat_layout(box: TextBox) -> bool:
    """Whether this box belongs to a chat/Discord OCR layout snapshot."""

    if box.role in ("metadata", "protected"):
        return True
    if box.layout_id > 0 and box.layout_id in _CHAT_LAYOUT_IDS:
        return True
    if getattr(box, "_chat_max_height", None) is not None:
        return True
    if box.layout_id <= 0:
        return False
    with _PENDING_LOCK:
        for marker_id in _LAYOUT_MARKERS.get(box.layout_id, []):
            source = _PENDING_BY_MARKER.get(marker_id)
            if source is not None and source.role in ("metadata", "protected"):
                return True
    return False


def _widen_list_rows(
    boxes: Sequence[TextBox], screen_right: float
) -> list[TextBox]:
    """Give a menu row the empty space around it instead of shrinking its text.

    An OCR box is a tight crop of the source ink, so it is both too narrow for
    a longer translation and too short for Vietnamese diacritics, which stack
    above the cap line. Widen into the empty column to its right, and take a
    little of the gap to the next row — never more than that gap.
    """

    result = list(boxes)
    rows = [
        (index, box)
        for index, box in enumerate(boxes)
        if box.list_item and (box.text or "").strip()
    ]
    if not rows:
        return result

    columns: dict = {}
    for index, box in rows:
        columns.setdefault(round(box.x / 24.0), []).append((index, box))

    for members in columns.values():
        if len(members) < 2:
            continue
        top = min(box.y for _index, box in members)
        bottom = max(box.y + box.height for _index, box in members)
        right = max(box.x + box.width for _index, box in members)
        limit = screen_right
        for other in boxes:
            if other.x < right or not (other.text or "").strip():
                continue
            if min(bottom, other.y + other.height) - max(top, other.y) <= 0:
                continue
            limit = min(limit, other.x)
        limit -= 8.0
        ordered = sorted(members, key=lambda item: item[1].y)
        for position, (index, box) in enumerate(ordered):
            width = box.width
            if limit > box.x + box.width:
                width = limit - box.x
            height = box.height
            if position + 1 < len(ordered):
                gap = ordered[position + 1][1].y - (box.y + box.height)
                if gap > 4.0:
                    height = box.height + min(gap - 3.0, box.height * 0.45)
            if width != box.width or height != box.height:
                result[index] = replace(box, width=width, height=height)
    return result


def _region_flow_boxes(
    boxes: Sequence[TextBox], screen_right: float, screen_bottom: float
) -> list[TextBox]:
    """Give translations unused room inside their visual card/message region."""

    sources = list(boxes)
    if not sources:
        return []
    # Chat: never expand/reflow geometry — keep OCR boxes so lines stay put.
    if _is_chat_layout(sources) or any(_box_in_chat_layout(box) for box in sources):
        return list(sources)
    groups = {}
    for index, box in enumerate(sources):
        key = (
            (box.layout_id, box.region_id)
            if box.region_id > 0
            else ("box", index)
        )
        groups.setdefault(key, []).append(index)

    regions = []
    for key, indices in groups.items():
        members = [sources[index] for index in indices]
        ordered_members = sorted(members, key=lambda item: (item.y, item.x))
        roles = {box.role for box in members}
        first = ordered_members[0]
        has_header_geometry = len(ordered_members) >= 2 and any(
            other.y >= first.y + first.height + 4.0
            for other in ordered_members[1:]
        )
        card_like = (
            "metadata" not in roles
            and "protected" not in roles
            and ("title" in roles or has_header_geometry)
        )
        regions.append(
            {
                "key": key,
                "indices": indices,
                "left": min(box.x for box in members),
                "top": min(box.y for box in members),
                "right": max(box.x + box.width for box in members),
                "bottom": max(box.y + box.height for box in members),
                "card_like": card_like,
                "layout_id": members[0].layout_id,
            }
        )

    def overlap(start1, end1, start2, end2):
        return max(0.0, min(end1, end2) - max(start1, start2))

    def same_row(first, second):
        amount = overlap(
            first["top"], first["bottom"], second["top"], second["bottom"]
        )
        return amount >= 0.35 * min(
            first["bottom"] - first["top"],
            second["bottom"] - second["top"],
        )

    def same_column(first, second):
        amount = overlap(
            first["left"], first["right"], second["left"], second["right"]
        )
        return amount >= 0.25 * min(
            first["right"] - first["left"],
            second["right"] - second["left"],
        )

    grid_keys = set()
    for layout_id in {region["layout_id"] for region in regions}:
        candidates = [
            region
            for region in regions
            if region["layout_id"] == layout_id and region["card_like"]
        ]
        rows = []
        for region in sorted(candidates, key=lambda item: (item["top"], item["left"])):
            row = next(
                (values for values in rows if same_row(values[0], region)), None
            )
            if row is None:
                rows.append([region])
            else:
                row.append(region)
        rows = [sorted(row, key=lambda item: item["left"]) for row in rows]
        if len(rows) < 2 or min((len(row) for row in rows), default=0) < 2:
            continue
        column_count = len(rows[0])
        if any(len(row) != column_count for row in rows):
            continue
        pitches = [
            second["left"] - first["left"]
            for first, second in zip(rows[0], rows[0][1:])
        ]
        if not pitches or min(pitches) <= 0:
            continue
        tolerance = max(20.0, float(median(pitches)) * 0.20)
        anchors = [region["left"] for region in rows[0]]
        stable = all(
            abs(region["left"] - anchors[index]) <= tolerance
            for row in rows[1:]
            for index, region in enumerate(row)
        )
        separated = all(
            second["left"] > first["right"]
            for row in rows
            for first, second in zip(row, row[1:])
        )
        if stable and separated:
            grid_keys.update(region["key"] for row in rows for region in row)

    for region in regions:
        region["expandable"] = region["key"] in grid_keys

    bounds = {}
    for region in regions:
        if not region["expandable"]:
            bounds[region["key"]] = (region["right"], region["bottom"], False)
            continue
        right_peers = [
            other
            for other in regions
            if other is not region
            and other["expandable"]
            and other["layout_id"] == region["layout_id"]
            and other["left"] > region["left"]
            and same_row(region, other)
        ]
        left_peers = [
            other
            for other in regions
            if other is not region
            and other["expandable"]
            and other["layout_id"] == region["layout_id"]
            and other["left"] < region["left"]
            and same_row(region, other)
        ]
        if right_peers:
            neighbour = min(right_peers, key=lambda item: item["left"])
            right = (
                (region["right"] + neighbour["left"]) / 2.0 - 6.0
                if neighbour["left"] > region["right"]
                else region["right"]
            )
        elif left_peers:
            neighbour = max(left_peers, key=lambda item: item["left"])
            pitch = region["left"] - neighbour["left"]
            left_boundary = (neighbour["right"] + region["left"]) / 2.0
            right = left_boundary + pitch - 6.0
        else:
            right = region["right"]
        right = max(region["right"], min(screen_right - 8.0, right))

        below = [
            other
            for other in regions
            if other is not region
            and other["expandable"]
            and other["layout_id"] == region["layout_id"]
            and other["top"] >= region["bottom"] + 4.0
            and same_column(region, other)
        ]
        above = [
            other
            for other in regions
            if other is not region
            and other["expandable"]
            and other["layout_id"] == region["layout_id"]
            and region["top"] >= other["bottom"] + 4.0
            and same_column(region, other)
        ]
        if below:
            neighbour = min(below, key=lambda item: item["top"])
            bottom = (region["bottom"] + neighbour["top"]) / 2.0 - 6.0
        elif above:
            neighbour = max(above, key=lambda item: item["top"])
            pitch = region["top"] - neighbour["top"]
            top_boundary = (neighbour["bottom"] + region["top"]) / 2.0
            bottom = top_boundary + pitch - 6.0
        else:
            bottom = region["bottom"]
        bottom = max(region["bottom"], min(screen_bottom - 8.0, bottom))
        bounds[region["key"]] = (right, bottom, True)

    result = []
    for index, box in enumerate(sources):
        key = (
            (box.layout_id, box.region_id)
            if box.region_id > 0
            else ("box", index)
        )
        right, bottom, card_like = bounds[key]
        if (
            box.region_id <= 0
            or not card_like
            or box.role in ("metadata", "protected")
        ):
            region_roles = {sources[i].role for i in groups[key]}
            # Chat-style rows (username/timestamp) must keep tight geometry so
            # body text never expands into the next message's header gap.
            chat_like = bool(region_roles & {"metadata", "protected"})
            if box.role == "body" and box.text.strip() and not chat_like:
                candidates = []
                for other_index, other in enumerate(sources):
                    if other_index == index or other.y < box.y + box.height + 4.0:
                        continue
                    horizontal_overlap = overlap(
                        box.x,
                        box.x + box.width,
                        other.x,
                        other.x + other.width,
                    )
                    if horizontal_overlap >= 0.20 * min(box.width, other.width):
                        candidates.append(other.y)
                if candidates:
                    safe_bottom = min(candidates) - 6.0
                    result.append(
                        replace(
                            box,
                            height=max(box.height, safe_bottom - box.y),
                        )
                    )
                    continue
            result.append(box)
            continue
        for other_index in groups[key]:
            if other_index == index:
                continue
            other = sources[other_index]
            horizontal_overlap = overlap(
                box.x, box.x + box.width, other.x, other.x + other.width
            )
            if (
                other.y >= box.y + box.height + 2.0
                and horizontal_overlap >= 0.20 * min(box.width, other.width)
            ):
                bottom = min(bottom, other.y - 6.0)
        result.append(
            replace(
                box,
                width=max(box.width, right - box.x),
                height=max(box.height, bottom - box.y),
            )
        )
    return result


def _native_screen_geometry(screen):
    """Return Win32 physical monitor bounds for a QScreen when available."""

    try:
        class NativeRect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", NativeRect),
                ("rcWork", NativeRect),
                ("dwFlags", ctypes.c_ulong),
                ("szDevice", ctypes.c_wchar * 32),
            ]

        found = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(NativeRect),
            ctypes.c_longlong,
        )

        def collect(handle, _dc, _rect, _data):
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                rect = info.rcMonitor
                found.append(
                    (info.szDevice.casefold(), rect.left, rect.top, rect.right, rect.bottom)
                )
            return True

        callback = callback_type(collect)
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback, 0)
        name = str(screen.name()).casefold()
        for device, left, top, right, bottom in found:
            if name and (name == device or name.endswith(device) or device.endswith(name)):
                return left, top, right, bottom
    except Exception:
        pass
    geometry = screen.geometry()
    dpr = max(1.0, screen.devicePixelRatio())
    return (
        geometry.x(),
        geometry.y(),
        geometry.x() + geometry.width() * dpr,
        geometry.y() + geometry.height() * dpr,
    )


def _screen_for_boxes(boxes: Sequence[TextBox]):
    screens = QApplication.screens()
    if not screens or not boxes:
        return QApplication.primaryScreen()
    x1 = min(box.x for box in boxes)
    y1 = min(box.y for box in boxes)
    x2 = max(box.x + box.width for box in boxes)
    y2 = max(box.y + box.height for box in boxes)
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2

    for screen in screens:
        sx1, sy1, sx2, sy2 = _native_screen_geometry(screen)
        if sx1 <= center_x < sx2 and sy1 <= center_y < sy2:
            return screen

    def screen_distance(screen):
        sx1, sy1, sx2, sy2 = _native_screen_geometry(screen)
        return (center_x - (sx1 + sx2) / 2) ** 2 + (center_y - (sy1 + sy2) / 2) ** 2

    return min(screens, key=screen_distance)


class Overlay(QWidget):
    def _configure_screen(self, screen) -> float:
        rect = screen.geometry()
        self.setGeometry(rect)
        dpr = max(1.0, float(screen.devicePixelRatio()))
        self.screen_origin = rect.topLeft()
        self.screen_origin_physical_x = float(rect.x())
        self.screen_origin_physical_y = float(rect.y())
        try:
            class NativeRect(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            native = NativeRect()
            if ctypes.windll.user32.GetWindowRect(int(self.winId()), ctypes.byref(native)):
                self.screen_origin_physical_x = float(native.left)
                self.screen_origin_physical_y = float(native.top)
                native_width = native.right - native.left
                if rect.width() > 0 and native_width > 0:
                    measured_dpr = native_width / rect.width()
                    if 0.75 <= measured_dpr <= 4.0:
                        dpr = measured_dpr
        except Exception:
            pass
        return max(1.0, dpr)

    def __init__(self, boxes: Sequence[TextBox]):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.debug_mode = False
        self.debug_paragraphs = []
        self.debug_lines = []
        self.debug_chars = []
        
        self.show_para = True
        self.show_line = True
        self.show_word = True
        self.fill_para = False
        self.fill_line = False
        self.fill_word = False
        self.fill_text_para = False
        self.fill_text_line = False
        self.fill_text_word = False
        self.actual_fill_level = None
        self.actual_fill_text_level = None
        self.debug_screenshot = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.Tool, True)
        if hasattr(Qt.WindowType, "WindowTransparentForInput"):
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        set_capture_affinity(self, not self.debug_mode)

        screen = _screen_for_boxes(boxes)
        dpr = self._configure_screen(screen)
        self.labels = []
        self.boxes = list(boxes)
        self.auto_bg_rects = []

        screenshot = None
        if CONFIG.get("auto_background", 0) or CONFIG.get("auto_text_color", 0) or CONFIG.get("auto_font_weight", 0):
            screenshot = screen.grabWindow(0).toImage()
        self._render_boxes(self.boxes, dpr, screenshot)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.close)
        self.timer.start(int(CONFIG["timeout_ms"]))
        _overlays.append(self)

    def _render_boxes(self, boxes: Sequence[TextBox], dpr: float, screenshot=None):
        self.auto_bg_rects: "list[tuple]" = []
        source_boxes = list(boxes)
        planning_boxes = list(source_boxes)
        known_markers = {box.marker_id for box in source_boxes if box.marker_id > 0}
        with _PENDING_LOCK:
            for layout_id in {box.layout_id for box in source_boxes if box.layout_id > 0}:
                for marker_id in _LAYOUT_MARKERS.get(layout_id, []):
                    source = _PENDING_BY_MARKER.get(marker_id)
                    if source is not None and marker_id not in known_markers:
                        planning_boxes.append(
                            replace(source, source_lines=list(source.source_lines))
                        )
        screen_right = self.screen_origin_physical_x + self.width() * dpr
        # Widen last: the region pass reasons about cards and would clamp a
        # menu row back to the card's own bounds.
        planned = _widen_list_rows(
            _region_flow_boxes(
                planning_boxes,
                screen_right,
                self.screen_origin_physical_y + self.height() * dpr,
            ),
            screen_right,
        )
        flow_boxes = [
            replace(source, width=planned[index].width, height=planned[index].height)
            for index, source in enumerate(source_boxes)
        ]

        def scaled_source_line(value, fallback):
            line = dict(value)
            line["x"] = (
                float(line.get("x", fallback.x)) - self.screen_origin_physical_x
            ) / dpr
            line["y"] = (
                float(line.get("y", fallback.y)) - self.screen_origin_physical_y
            ) / dpr
            line["width"] = float(line.get("width", fallback.width)) / dpr
            line["height"] = float(line.get("height", fallback.height)) / dpr
            for key in ("font_size", "ink_height", "ink_top", "baseline_offset"):
                if line.get(key) is not None:
                    line[key] = float(line[key]) / dpr
            return line

        def logical_source_rect(value, fallback):
            line_x = (
                float(value.get("x", fallback.x)) - self.screen_origin_physical_x
            ) / dpr
            line_y = (
                float(value.get("y", fallback.y)) - self.screen_origin_physical_y
            ) / dpr
            line_w = float(value.get("width", fallback.width)) / dpr
            line_h = float(value.get("height", fallback.height)) / dpr
            pad_x = max(1.0, line_h * 0.08)
            pad_y = max(1.0, line_h * 0.10)
            return QRect(
                round(line_x - pad_x),
                round(line_y - pad_y),
                max(1, round(line_w + pad_x * 2)),
                max(1, round(line_h + pad_y * 2)),
            )

        obstacle_rects = [
            QRect(
                round((obstacle.x - self.screen_origin_physical_x) / dpr),
                round((obstacle.y - self.screen_origin_physical_y) / dpr),
                max(1, round(obstacle.width / dpr)),
                max(1, round(obstacle.height / dpr)),
            )
            for obstacle in source_boxes
        ]

        # Phase 1: scale geometry + sample style for every paintable block.
        render_jobs: list[tuple[int, TextBox, TextBox]] = []
        for box_index, (source_box, box) in enumerate(zip(source_boxes, flow_boxes)):
            scaled_width = max(1, round(box.width / dpr))
            scaled_height = max(1, round(box.height / dpr))
            x = round((box.x - self.screen_origin_physical_x) / dpr)
            y = round((box.y - self.screen_origin_physical_y) / dpr)
            scaled = TextBox(
                x=x,
                y=y,
                width=scaled_width,
                height=scaled_height,
                text=box.text,
                background_color=box.background_color,
                text_color=box.text_color,
                stroke_color=box.stroke_color,
                bold=box.bold,
                stroke_width=box.stroke_width,
                font_family=box.font_family,
                font_size=(box.font_size / dpr if box.font_size else None),
                line_spacing=_stable_line_spacing(
                    box.role or "body", box.line_spacing
                ),
                baseline_offset=(
                    box.baseline_offset / dpr if box.baseline_offset is not None else None
                ),
                alignment=box.alignment,
                role=box.role,
                region_id=box.region_id,
                layout_id=box.layout_id,
                source_id=box.source_id,
                marker_id=box.marker_id,
                source_lines=[
                    scaled_source_line(line, source_box)
                    for line in source_box.source_lines
                ],
                ink_height=(box.ink_height / dpr if box.ink_height else None),
                ink_top=(box.ink_top / dpr if box.ink_top is not None else None),
                bold_score=box.bold_score,
                # Not geometry — carry through unscaled. text_rgb in particular
                # is what tells paint the colour was measured, not invented.
                list_item=box.list_item,
                list_run=box.list_run,
                run_ink=(box.run_ink / dpr if box.run_ink else None),
                text_rgb=box.text_rgb,
                demoted=box.demoted,
            )
            scaled._source_width = source_box.width / dpr
            scaled._source_height = source_box.height / dpr
            # Chat height caps are physical-space → convert to logical like height.
            chat_cap = getattr(source_box, "_chat_max_height", None)
            if chat_cap is not None:
                scaled._chat_max_height = float(chat_cap) / dpr
            elif box.layout_id > 0 and box.layout_id in _CHAT_LAYOUT_IDS:
                # No free-space growth when cap missing: stay in OCR band.
                scaled._chat_max_height = float(scaled_height)

            has_text = bool(scaled.text.strip())
            if scaled.role in ("metadata", "protected"):
                # Usernames and timestamps are deliberately left untranslated;
                # covering them would blank out chrome the user still reads.
                continue
            # An untranslated title/body still needs its cover, or the source
            # line shows through between the translated ones.
            if not has_text:
                if CONFIG.get("auto_background", 0):
                    # Defer to phase 3 as mask-only jobs.
                    render_jobs.append((box_index, source_box, scaled, True))
                continue

            auto_bg = CONFIG.get("auto_background", 0)
            auto_text = CONFIG.get("auto_text_color", 0)
            auto_weight = CONFIG.get("auto_font_weight", 0)
            has_screenshot = screenshot is not None and not screenshot.isNull()
            if (auto_bg or auto_text or auto_weight) and has_screenshot:
                x_pixel = source_box.x - self.screen_origin_physical_x
                y_pixel = source_box.y - self.screen_origin_physical_y
                w_pixel = source_box.width
                h_pixel = source_box.height
                sample_box = TextBox(x_pixel, y_pixel, w_pixel, h_pixel, "")
                bg_color = sample_background(screenshot, sample_box)
                bg_color.setAlpha(255)

                if auto_text and not scaled.text_color:
                    text_color = sample_text_color(screenshot, sample_box, bg_color)
                    scaled.text_color = color_to_rgba(text_color)

                if auto_weight and scaled.bold is None:
                    if scaled.role == "title":
                        scaled.bold = True
                    elif scaled.role == "body":
                        scaled.bold = False
                    else:
                        scaled.bold = estimate_text_boldness(
                            screenshot, sample_box, bg_color
                        )
            elif CONFIG.get("auto_font_weight", 0):
                if scaled.role == "title":
                    scaled.bold = True
                elif scaled.role == "body":
                    scaled.bold = False

            render_jobs.append((box_index, source_box, scaled, False))

        # Phase 2: shared size + one title/body color for the whole frame.
        paintable = [
            job[2] for job in render_jobs if not job[3] and job[2].text.strip()
        ]
        _harmonize_role_palette(paintable)
        chat_frame = _is_chat_layout(paintable) or any(
            _box_in_chat_layout(box) for box in paintable
        )
        # Chat keeps per-message font sizes (shared min was crushing long posts).
        shared_px = {} if chat_frame else _shared_font_px_by_role(paintable)
        if not chat_frame and "title" in shared_px and "body" in shared_px:
            shared_px["title"] = max(
                shared_px["title"],
                min(
                    int(CONFIG["max_font_size"]),
                    int(shared_px["body"] * 1.15 + 0.5),
                ),
            )

        # Phase 3: masks + labels at the harmonized size.
        for box_index, source_box, scaled, mask_only in render_jobs:
            auto_bg = CONFIG.get("auto_background", 0)
            has_screenshot = screenshot is not None and not screenshot.isNull()
            background_brush_created = False
            self.auto_bg_rects.append((None, None))

            if auto_bg:
                mask_lines = source_box.source_lines or [
                    {
                        "x": source_box.x,
                        "y": source_box.y,
                        "width": source_box.width,
                        "height": source_box.height,
                    }
                ]
                masks = []
                for source_line in mask_lines:
                    source_rect = logical_source_rect(source_line, source_box)
                    line_sample = TextBox(
                        float(source_line.get("x", source_box.x))
                        - self.screen_origin_physical_x,
                        float(source_line.get("y", source_box.y))
                        - self.screen_origin_physical_y,
                        float(source_line.get("width", source_box.width)),
                        float(source_line.get("height", source_box.height)),
                        "",
                    )
                    if has_screenshot:
                        # A badge or button is wider than the label the OCR
                        # reported; cover the whole surface or its border is
                        # left drawn around the translation.
                        pad = surface_pad(screenshot, line_sample)
                        if pad:
                            source_rect = source_rect.adjusted(
                                -round(pad[0] / dpr),
                                -round(pad[1] / dpr),
                                round(pad[2] / dpr),
                                round(pad[3] / dpr),
                            )
                    if scaled.background_color:
                        brush = QBrush(parse_color(scaled.background_color))
                    elif has_screenshot:
                        line_bg = sample_background(screenshot, line_sample)
                        line_bg.setAlpha(255)
                        brush = create_gradient_brush(
                            source_rect,
                            sample_gradient_colors(
                                screenshot, line_sample, line_bg
                            ),
                        )
                    else:
                        brush = QBrush(parse_color(CONFIG["background_color"]))
                    masks.append((source_rect, brush))
                masks = _close_small_mask_gaps(masks)
                self.auto_bg_rects[-1:] = masks
                background_brush_created = bool(masks)

            if mask_only or not scaled.text.strip():
                # Cover-only: masks already applied; no text label.
                continue

            skip_bg = False
            if auto_bg and background_brush_created and scaled.background_color:
                pass
            label = self._create_label(
                scaled,
                skip_background=skip_bg,
                forced_px=shared_px.get(scaled.role or "body"),
            )
            collision = False
            chat_label = _box_in_chat_layout(scaled)
            if label is not None:
                candidate_cover = _label_cover_rect(label)
                candidate_ink = getattr(label, "_paint_bounds", [label.geometry()])
                # Chat: only drop on heavy ink overlap — light cover touches are
                # normal between consecutive message lines.
                for existing in self.labels:
                    existing_cover = _label_cover_rect(existing)
                    existing_ink = getattr(
                        existing, "_paint_bounds", [existing.geometry()]
                    )
                    if chat_label:
                        # Only near-total ink cover counts. Light edge touches
                        # between consecutive messages are normal; dropping them
                        # caused the "missing messages" holes.
                        if any(
                            _significant_paint_overlap(
                                candidate, current, threshold=0.52, min_area=48
                            )
                            for candidate in candidate_ink
                            for current in existing_ink
                        ):
                            collision = True
                            break
                    elif _significant_paint_overlap(
                        candidate_cover, existing_cover
                    ) or any(
                        _significant_paint_overlap(candidate, current)
                        for candidate in candidate_ink
                        for current in existing_ink
                    ):
                        collision = True
                        break
                if not collision:
                    for obstacle_index, obstacle_rect in enumerate(obstacle_rects):
                        if (
                            obstacle_index == box_index
                            or source_boxes[obstacle_index].role
                            not in ("metadata", "protected")
                        ):
                            continue
                        if chat_label:
                            # Username/time chrome: only block if we really paint on it.
                            if _significant_paint_overlap(
                                candidate_cover,
                                obstacle_rect,
                                threshold=0.45,
                                min_area=36,
                            ):
                                collision = True
                                break
                        elif _significant_paint_overlap(candidate_cover, obstacle_rect):
                            collision = True
                            break
            if label is not None and not collision:
                self.labels.append(label)
            elif label is not None and chat_label:
                # Prefer showing chat text over silence.
                self.labels.append(label)
            else:
                if label is not None:
                    label.deleteLater()
                # Keep auto-background masks even when text cannot fit or
                # collides: dropping the cover re-exposes the original source.

    def update_content(self, boxes: Sequence[TextBox]):
        # Coalesce nested calls (hide+processEvents can re-enter via queued
        # show_overlay) so we never paint with half-cleared labels.
        if getattr(self, "_updating_content", False):
            self._pending_update_boxes = list(boxes)
            return
        self._updating_content = True
        self._pending_update_boxes = None
        try:
            self.timer.start(int(CONFIG["timeout_ms"]))
            self.debug_mode = False
            set_capture_affinity(self, True)
            self.boxes = list(boxes)
            for label in self.labels:
                label.deleteLater()
            self.labels.clear()
            self.auto_bg_rects = []
            screen = _screen_for_boxes(boxes)
            dpr = self._configure_screen(screen)
            self.actual_fill_level = None
            self.debug_screenshot = None

            screenshot = None
            if CONFIG.get("auto_background", 0) or CONFIG.get("auto_text_color", 0) or CONFIG.get("auto_font_weight", 0):
                was_visible = self.isVisible()
                if was_visible:
                    self.hide()
                    QApplication.processEvents()
                screenshot = screen.grabWindow(0).toImage()
                if was_visible:
                    self.show()

            self._render_boxes(self.boxes, dpr, screenshot)
            self.update()
            self.show()
        finally:
            self._updating_content = False
            pending = getattr(self, "_pending_update_boxes", None)
            self._pending_update_boxes = None
            if pending is not None:
                self.update_content(pending)

    def update_debug_content(self, debug_data: dict):
        from qtsymbols import QRect
        self.timer.start(int(CONFIG["timeout_ms"]))
        self.debug_mode = True
        set_capture_affinity(self, False)
        for label in self.labels:
            label.deleteLater()
        self.labels.clear()
        self.auto_bg_rects = []
        screen = _screen_for_boxes(self.boxes)
        dpr = self._configure_screen(screen)

        self.show_para = debug_data.get("show_para", True)
        self.show_line = debug_data.get("show_line", True)
        self.show_word = debug_data.get("show_word", True)
        self.show_title = debug_data.get("show_title", True)
        self.fill_para = debug_data.get("fill_para", False)
        self.fill_line = debug_data.get("fill_line", False)
        self.fill_word = debug_data.get("fill_word", False)
        self.fill_text_para = debug_data.get("fill_text_para", False)
        self.fill_text_line = debug_data.get("fill_text_line", False)
        self.fill_text_word = debug_data.get("fill_text_word", False)

        was_visible = self.isVisible()
        if was_visible:
            self.hide()
            QApplication.processEvents()

        self.debug_screenshot = screen.grabWindow(0).toImage() if screen else None

        if was_visible:
            self.show()

        self.debug_chars = []
        raw_chars = debug_data.get("chars", [])
        char_data_list = []
        
        for c in raw_chars:
            rx = int((c["x"] - self.screen_origin_physical_x) / dpr)
            ry = int((c["y"] - self.screen_origin_physical_y) / dpr)
            rw = int(c["w"] / dpr)
            rh = int(c["h"] / dpr)
            
            x_pixel = c["x"] - self.screen_origin_physical_x
            y_pixel = c["y"] - self.screen_origin_physical_y
            w_pixel = c["w"]
            h_pixel = c["h"]
            
            sample_box = TextBox(x_pixel, y_pixel, w_pixel, h_pixel, "")
            bg_color = sample_background(self.debug_screenshot, sample_box)
            bg_color.setAlpha(255)
            grad_colors = sample_gradient_colors(self.debug_screenshot, sample_box, bg_color)
            
            text_color = sample_text_color(self.debug_screenshot, sample_box, bg_color)
            
            # Tính bg_rect (cho nền)
            bg_left = rw * DEBUG_BG_PADDING["left_percent"] + DEBUG_BG_PADDING["left_px"]
            bg_right = rw * DEBUG_BG_PADDING["right_percent"] + DEBUG_BG_PADDING["right_px"]
            bg_top = rh * DEBUG_BG_PADDING["top_percent"] + DEBUG_BG_PADDING["top_px"]
            bg_bottom = rh * DEBUG_BG_PADDING["bottom_percent"] + DEBUG_BG_PADDING["bottom_px"]
            bg_rx = round(rx - bg_left)
            bg_ry = round(ry - bg_top)
            bg_rw = max(1, round(rw + bg_left + bg_right))
            bg_rh = max(1, round(rh + bg_top + bg_bottom))
            bg_rect = QRect(bg_rx, bg_ry, bg_rw, bg_rh)

            # Tính text_rect (cho chữ)
            text_left = rw * DEBUG_TEXT_PADDING["left_percent"] + DEBUG_TEXT_PADDING["left_px"]
            text_right = rw * DEBUG_TEXT_PADDING["right_percent"] + DEBUG_TEXT_PADDING["right_px"]
            text_top = rh * DEBUG_TEXT_PADDING["top_percent"] + DEBUG_TEXT_PADDING["top_px"]
            text_bottom = rh * DEBUG_TEXT_PADDING["bottom_percent"] + DEBUG_TEXT_PADDING["bottom_px"]
            text_rx = round(rx - text_left)
            text_ry = round(ry - text_top)
            text_rw = max(1, round(rw + text_left + text_right))
            text_rh = max(1, round(rh + text_top + text_bottom))
            text_rect = QRect(text_rx, text_ry, text_rw, text_rh)
            
            self.debug_chars.append((bg_rect, text_rect, grad_colors, text_color))
            
            char_data_list.append({
                "x": c["x"], "y": c["y"], "w": c["w"], "h": c["h"],
                "text_color": text_color
            })

        self.debug_lines = []
        raw_lines = debug_data.get("lines", [])
        line_data_list = []
        
        for l in raw_lines:
            rx = int((l["x"] - self.screen_origin_physical_x) / dpr)
            ry = int((l["y"] - self.screen_origin_physical_y) / dpr)
            rw = int(l["w"] / dpr)
            rh = int(l["h"] / dpr)
            
            # Tính bg_rect (cho nền)
            bg_left = rw * DEBUG_BG_PADDING["left_percent"] + DEBUG_BG_PADDING["left_px"]
            bg_right = rw * DEBUG_BG_PADDING["right_percent"] + DEBUG_BG_PADDING["right_px"]
            bg_top = rh * DEBUG_BG_PADDING["top_percent"] + DEBUG_BG_PADDING["top_px"]
            bg_bottom = rh * DEBUG_BG_PADDING["bottom_percent"] + DEBUG_BG_PADDING["bottom_px"]
            bg_rx = round(rx - bg_left)
            bg_ry = round(ry - bg_top)
            bg_rw = max(1, round(rw + bg_left + bg_right))
            bg_rh = max(1, round(rh + bg_top + bg_bottom))
            bg_rect = QRect(bg_rx, bg_ry, bg_rw, bg_rh)

            # Tính text_rect (cho chữ)
            text_left = rw * DEBUG_TEXT_PADDING["left_percent"] + DEBUG_TEXT_PADDING["left_px"]
            text_right = rw * DEBUG_TEXT_PADDING["right_percent"] + DEBUG_TEXT_PADDING["right_px"]
            text_top = rh * DEBUG_TEXT_PADDING["top_percent"] + DEBUG_TEXT_PADDING["top_px"]
            text_bottom = rh * DEBUG_TEXT_PADDING["bottom_percent"] + DEBUG_TEXT_PADDING["bottom_px"]
            text_rx = round(rx - text_left)
            text_ry = round(ry - text_top)
            text_rw = max(1, round(rw + text_left + text_right))
            text_rh = max(1, round(rh + text_top + text_bottom))
            text_rect = QRect(text_rx, text_ry, text_rw, text_rh)
            
            x_pixel = l["x"] - self.screen_origin_physical_x
            y_pixel = l["y"] - self.screen_origin_physical_y
            w_pixel = l["w"]
            h_pixel = l["h"]
            
            sample_box = TextBox(x_pixel, y_pixel, w_pixel, h_pixel, "")
            bg_color = sample_background(self.debug_screenshot, sample_box)
            bg_color.setAlpha(255)
            grad_colors = sample_gradient_colors(self.debug_screenshot, sample_box, bg_color)
            
            line_char_colors = []
            for cd in char_data_list:
                cx_center = cd["x"] + cd["w"] / 2
                cy_center = cd["y"] + cd["h"] / 2
                if l["x"] <= cx_center <= l["x"] + l["w"] and l["y"] <= cy_center <= l["y"] + l["h"]:
                    line_char_colors.append(cd["text_color"])
            
            dominant_text_color = find_dominant_color(line_char_colors) if line_char_colors else QColor(255, 255, 255)
            
            self.debug_lines.append((bg_rect, text_rect, grad_colors, dominant_text_color, l.get("font", "Arial")))
            
            line_data_list.append({
                "x": l["x"], "y": l["y"], "w": l["w"], "h": l["h"],
                "char_colors": line_char_colors,
                "font": l.get("font", "Arial")
            })

        self.debug_paragraphs = []
        raw_paras = debug_data.get("paragraphs", [])
        
        for p in raw_paras:
            rx = int((p["x"] - self.screen_origin_physical_x) / dpr)
            ry = int((p["y"] - self.screen_origin_physical_y) / dpr)
            rw = int(p["w"] / dpr)
            rh = int(p["h"] / dpr)
            
            # Tính bg_rect (cho nền)
            bg_left = rw * DEBUG_BG_PADDING["left_percent"] + DEBUG_BG_PADDING["left_px"]
            bg_right = rw * DEBUG_BG_PADDING["right_percent"] + DEBUG_BG_PADDING["right_px"]
            bg_top = rh * DEBUG_BG_PADDING["top_percent"] + DEBUG_BG_PADDING["top_px"]
            bg_bottom = rh * DEBUG_BG_PADDING["bottom_percent"] + DEBUG_BG_PADDING["bottom_px"]
            bg_rx = round(rx - bg_left)
            bg_ry = round(ry - bg_top)
            bg_rw = max(1, round(rw + bg_left + bg_right))
            bg_rh = max(1, round(rh + bg_top + bg_bottom))
            bg_rect = QRect(bg_rx, bg_ry, bg_rw, bg_rh)

            # Tính text_rect (cho chữ)
            text_left = rw * DEBUG_TEXT_PADDING["left_percent"] + DEBUG_TEXT_PADDING["left_px"]
            text_right = rw * DEBUG_TEXT_PADDING["right_percent"] + DEBUG_TEXT_PADDING["right_px"]
            text_top = rh * DEBUG_TEXT_PADDING["top_percent"] + DEBUG_TEXT_PADDING["top_px"]
            text_bottom = rh * DEBUG_TEXT_PADDING["bottom_percent"] + DEBUG_TEXT_PADDING["bottom_px"]
            text_rx = round(rx - text_left)
            text_ry = round(ry - text_top)
            text_rw = max(1, round(rw + text_left + text_right))
            text_rh = max(1, round(rh + text_top + text_bottom))
            text_rect = QRect(text_rx, text_ry, text_rw, text_rh)
            
            x_pixel = p["x"] - self.screen_origin_physical_x
            y_pixel = p["y"] - self.screen_origin_physical_y
            w_pixel = p["w"]
            h_pixel = p["h"]
            
            sample_box = TextBox(x_pixel, y_pixel, w_pixel, h_pixel, "")
            bg_color = sample_background(self.debug_screenshot, sample_box)
            bg_color.setAlpha(255)
            grad_colors = sample_gradient_colors(self.debug_screenshot, sample_box, bg_color)
            
            para_char_colors = []
            para_line_fonts = []
            for ld in line_data_list:
                lx_center = ld["x"] + ld["w"] / 2
                ly_center = ld["y"] + ld["h"] / 2
                if p["x"] <= lx_center <= p["x"] + p["w"] and p["y"] <= ly_center <= p["y"] + p["h"]:
                    para_char_colors.extend(ld["char_colors"])
                    para_line_fonts.append(ld.get("font", "Arial"))
                    
            dominant_text_color = find_dominant_color(para_char_colors) if para_char_colors else QColor(255, 255, 255)
            dominant_para_font = max(set(para_line_fonts), key=para_line_fonts.count) if para_line_fonts else "Arial"
            
            self.debug_paragraphs.append((bg_rect, text_rect, grad_colors, dominant_text_color, dominant_para_font))

        self.debug_title_indices = set()
        if getattr(self, "show_title", True):
            for p_bg_rect, p_text_rect, *rest in self.debug_paragraphs:
                para_lines_with_indices = []
                for line_idx, (bg_rect, text_rect, *line_rest) in enumerate(self.debug_lines):
                    if p_text_rect.contains(text_rect.center()):
                        para_lines_with_indices.append((line_idx, bg_rect, text_rect, *line_rest))
                
                if len(para_lines_with_indices) >= 2:
                    sorted_para_lines = sorted(para_lines_with_indices, key=lambda x: x[2].top())
                    last_line = sorted_para_lines[-1]
                    content_h = last_line[2].height()
                    content_color = last_line[4]
                    
                    if len(sorted_para_lines) > 2:
                        content_h = sum(x[2].height() for x in sorted_para_lines[1:]) / (len(sorted_para_lines) - 1)
                        
                    line_idx_0, bg_rect_0, text_rect_0, grad_colors_0, color_0, font_0 = sorted_para_lines[0]
                    h_0 = text_rect_0.height()
                    
                    is_title_0 = False
                    if h_0 >= 1.15 * content_h:
                        is_title_0 = True
                    elif color_distance(color_0, content_color) > 50:
                        is_title_0 = True
                        
                    if is_title_0:
                        self.debug_title_indices.add(line_idx_0)
                        max_title_lines = max(1, len(sorted_para_lines) // 2)
                        for i in range(1, max_title_lines):
                            line_idx_i, bg_rect_i, text_rect_i, grad_colors_i, color_i, font_i = sorted_para_lines[i]
                            h_i = text_rect_i.height()
                            
                            height_similar = (0.85 * h_0 <= h_i <= 1.15 * h_0)
                            color_similar = (color_distance(color_i, color_0) < 30)
                            
                            if height_similar and color_similar:
                                self.debug_title_indices.add(line_idx_i)
                            else:
                                break

        self.actual_fill_level = None
        if self.fill_para and self.show_para and self.debug_paragraphs:
            self.actual_fill_level = "para"
        elif self.fill_line and self.show_line and self.debug_lines:
            self.actual_fill_level = "line"
        elif self.fill_word and self.show_word and self.debug_chars:
            self.actual_fill_level = "word"

        self.actual_fill_text_level = None
        if self.fill_text_para and self.show_para and self.debug_paragraphs:
            self.actual_fill_text_level = "para"
        elif self.fill_text_line and self.show_line and self.debug_lines:
            self.actual_fill_text_level = "line"
        elif self.fill_text_word and self.show_word and self.debug_chars:
            self.actual_fill_text_level = "word"

        self.update()
        self.show()

    def paintEvent(self, event):
        if not hasattr(self, "debug_mode") or not self.debug_mode:
            if (
                hasattr(self, "auto_bg_rects")
                and self.auto_bg_rects
                and any(r[0] is not None for r in self.auto_bg_rects)
            ):
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                for r_bg, brush in self.auto_bg_rects:
                    if r_bg is not None and brush is not None:
                        painter.setBrush(brush)
                        painter.drawRect(r_bg)
            else:
                super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Vẽ Fill (nếu có cấp độ fill nào hoạt động và cấu hình bật)
        actual_fill = getattr(self, "actual_fill_level", None)
        if DEBUG_FILL_BG and actual_fill:
            if actual_fill == "para":
                painter.setPen(Qt.PenStyle.NoPen)
                for r_bg, r_text, *rest in self.debug_paragraphs:
                    col = rest[0]
                    brush = create_gradient_brush(r_bg, col)
                    painter.setBrush(brush)
                    painter.drawRect(r_bg)
                    
            elif actual_fill == "line":
                painter.setPen(Qt.PenStyle.NoPen)
                for r_bg, r_text, *rest in self.debug_lines:
                    col = rest[0]
                    brush = create_gradient_brush(r_bg, col)
                    painter.setBrush(brush)
                    painter.drawRect(r_bg)
                    
            elif actual_fill == "word":
                painter.setPen(Qt.PenStyle.NoPen)
                for r_bg, r_text, *rest in self.debug_chars:
                    col = rest[0]
                    brush = create_gradient_brush(r_bg, col)
                    painter.setBrush(brush)
                    painter.drawRect(r_bg)

        # 1b. Vẽ Fill Text (Chữ) - kích thước đúng bằng box gốc của chữ/dòng/đoạn (nếu cấu hình bật)
        actual_fill_text = getattr(self, "actual_fill_text_level", None)
        if DEBUG_FILL_TEXT and actual_fill_text:
            if actual_fill_text == "para":
                painter.setPen(Qt.PenStyle.NoPen)
                for r_bg, r_text, *rest in self.debug_paragraphs:
                    text_col = rest[1]
                    painter.setBrush(text_col)
                    painter.drawRect(r_text)
                    
            elif actual_fill_text == "line":
                painter.setPen(Qt.PenStyle.NoPen)
                for r_bg, r_text, *rest in self.debug_lines:
                    text_col = rest[1]
                    painter.setBrush(text_col)
                    painter.drawRect(r_text)
                    
            elif actual_fill_text == "word":
                painter.setPen(Qt.PenStyle.NoPen)
                for r_bg, r_text, *rest in self.debug_chars:
                    text_col = rest[1]
                    painter.setBrush(text_col)
                    painter.drawRect(r_text)

        # 2. Vẽ viền (Stroke) cho những cấp độ không bị fill che khuất
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Viền xanh lá (từng chữ) - Chỉ vẽ nếu show_word bật và actual_fill != "word"
        if getattr(self, "show_word", True) and actual_fill != "word":
            pen_char = QPen(QColor(0, 255, 0), 1)
            painter.setPen(pen_char)
            for r_bg, r_text, *rest in self.debug_chars:
                painter.drawRect(r_text)

        # Viền xanh dương (từng dòng) - Chỉ vẽ nếu show_line bật and actual_fill != "line"
        if getattr(self, "show_line", True) and actual_fill != "line":
            pen_line = QPen(QColor(0, 0, 255), 2)
            pen_title = QPen(QColor(255, 0, 127), 2)
            for line_idx, (r_bg, r_text, *rest) in enumerate(self.debug_lines):
                if getattr(self, "show_title", True) and line_idx in getattr(self, "debug_title_indices", set()):
                    painter.setPen(pen_title)
                else:
                    painter.setPen(pen_line)
                painter.drawRect(r_text)

        # Viền đỏ (từng đoạn) - Chỉ vẽ nếu show_para bật and actual_fill != "para"
        if getattr(self, "show_para", True) and actual_fill != "para":
            pen_para = QPen(QColor(255, 0, 0), 3)
            painter.setPen(pen_para)
            for r_bg, r_text, *rest in self.debug_paragraphs:
                painter.drawRect(r_text)

        # 3. Vẽ tên phông chữ (nếu chế độ phát hiện font bật)
        if globalconfig.get("debugocr_detect_font", False):
            painter.setPen(QColor(255, 255, 0))  # Vẽ chữ màu vàng cho nổi bật
            
            if getattr(self, "show_line", True):
                for r_bg, r_text, *rest in self.debug_lines:
                    font_name = rest[2]
                    draw_font = QFont("Segoe UI")
                    draw_font.setPixelSize(max(8, min(14, int(r_text.height() * 0.6))))
                    painter.setFont(draw_font)
                    # Vẽ dòng ở góc trên bên trái của box dòng (lùi vào 4px, 2px)
                    draw_rect = r_text.adjusted(4, 2, -4, -2)
                    painter.drawText(draw_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, font_name)
                    
            if getattr(self, "show_para", True):
                for r_bg, r_text, *rest in self.debug_paragraphs:
                    font_name = rest[2]
                    draw_font = QFont("Segoe UI")
                    draw_font.setPixelSize(max(8, min(16, int(r_text.height() * 0.4))))
                    painter.setFont(draw_font)
                    # Vẽ đoạn ở góc dưới bên phải của box đoạn (lùi vào 4px, 2px)
                    draw_rect = r_text.adjusted(4, 2, -4, -2)
                    painter.drawText(draw_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, font_name)

    def closeEvent(self, event):
        if self in _overlays:
            _overlays.remove(self)
        super().closeEvent(event)

    def _create_label(
        self,
        box: TextBox,
        skip_background: bool = False,
        forced_px: Optional[int] = None,
    ):
        label = StrokedLabel(
            box.text,
            self,
            skip_background=skip_background,
            background_color=box.background_color,
            text_color=box.text_color,
            stroke_color=box.stroke_color,
            stroke_width=box.stroke_width,
            alignment=box.alignment,
            line_spacing=box.line_spacing,
            baseline_offset=box.baseline_offset,
        )
        label.setWordWrap(True)
        qt_alignment = {
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight,
        }.get(box.alignment, Qt.AlignmentFlag.AlignLeft)
        label.setAlignment(qt_alignment | Qt.AlignmentFlag.AlignVCenter)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        font = QFont(label.font())
        # Configured UI face unless auto_font_family chose a per-block family.
        if CONFIG.get("auto_font_family", 0) and box.font_family:
            family = box.font_family
        else:
            family = CONFIG.get("font_family") or "Segoe UI"
        if family:
            font.setFamily(family)
        # Role weight wins over whatever the box carried (peeled titles often
        # still had body bold=False before harmonize).
        if CONFIG.get("auto_font_weight", 0):
            if (box.role or "body") == "title":
                font.setBold(True)
            elif (box.role or "body") == "body":
                font.setBold(False)
            elif box.bold is not None:
                font.setBold(box.bold)
        elif box.bold is not None:
            font.setBold(box.bold)
        # Peeled titles that never got sampled fall back to a bright default.
        # A title whose own pixels were measured keeps them, however dim —
        # otherwise a dark heading on a light page is painted white onto white.
        if (box.role or "") == "title":
            if not box.text_color or (
                box.text_rgb is None and _color_luminance(box.text_color) < 180
            ):
                box.text_color = _default_title_color()
            label._custom_text_color = box.text_color
        min_px = int(CONFIG["min_font_size"])
        max_px = int(CONFIG["max_font_size"])
        vpad = int(CONFIG["vertical_padding"])
        hpad = int(CONFIG["horizontal_padding"])
        
        stroke_width = float(
            CONFIG["stroke_width"] if box.stroke_width is None else box.stroke_width
        )
        paint_margin = max(1, int(stroke_width + 1.5))
        target_w = max(1, int(box.width - hpad))
        chat = _box_in_chat_layout(box)
        base_height = float(box.height)
        # Chat may grow only into free space below (see _apply_chat_height_caps).
        # Uncapped +12..+140 growth invaded the next message → drop + clumps.
        height_budget = [base_height]
        chat_cap = float(getattr(box, "_chat_max_height", 0) or 0)
        if chat and (box.role or "body") == "body":
            max_h = max(base_height, chat_cap) if chat_cap > 0 else base_height
            steps = (6, 12, 20, 32, 48, 72, 100, 140)
        else:
            # An OCR box is a tight crop of the source ink, so it is shorter
            # than the line it came from — and a translation's accents stack
            # higher than the original's. Take the free space below when it is
            # known, otherwise a sliver, before touching the font size.
            ceiling = chat_cap if chat_cap > 0 else base_height * 1.18
            max_h = max(base_height, min(ceiling, base_height * 1.35))
            steps = (4, 8, 14, 22)
        if max_h > base_height + 0.5:
            for extra in steps:
                candidate = base_height + extra
                if candidate <= max_h + 0.5:
                    height_budget.append(candidate)
            if all(abs(h - max_h) > 0.5 for h in height_budget):
                height_budget.append(max_h)
        chat_vpad = max(2, vpad - 2) if chat else vpad

        start_px = _estimate_start_font_px(box, min_px, max_px)
        if forced_px is not None and not chat:
            px0 = max(min_px, min(max_px, int(forced_px)))
            px0 = min(px0, start_px)
        else:
            # Chat keeps per-message size; shared min would crush long messages.
            px0 = start_px
        source_line_count = max(
            1, len([line for line in box.source_lines if line]) or 1
        )
        fitted = False
        fitted_baseline = box.baseline_offset
        lines: list[str] = []
        tight_rects = []
        line_advance = 1
        spacing = _stable_line_spacing(
            "chat" if chat else (box.role or "body"), box.line_spacing
        )
        metrics = QFontMetrics(font)
        used_height = base_height
        layout_role = "chat" if chat else (box.role or "body")
        spacing_tries = (1.0, 0.94, 0.88) if chat else (1.0,)
        # Size first, box second: try every height the box may legally take at
        # this font size before giving up a pixel. The other order shrank the
        # text to the minimum inside the tight crop and never grew the box.
        px = px0
        while px >= min_px and not fitted:
            for try_h in height_budget:
                target_h = max(1, int(try_h - chat_vpad))
                font.setPixelSize(px)
                # Chat uses full box width (source_lines often one long line).
                layout_source_lines = [] if chat else box.source_lines
                for scale in spacing_tries:
                    laid = _layout_text_in_box(
                        box.text,
                        font,
                        target_w=target_w,
                        target_h=target_h,
                        box_height=try_h,
                        role=layout_role,
                        source_line_count=source_line_count,
                        source_spacing=box.line_spacing,
                        vpad=chat_vpad,
                        prefer_baseline=None if chat else box.baseline_offset,
                        source_lines=layout_source_lines,
                        spacing_scale=scale,
                    )
                    if laid is not None:
                        # A label that was one line in the source should stay
                        # one line: a nav tab broken across two rows reads as
                        # a layout bug, while a slightly smaller one does not.
                        if (
                            source_line_count == 1
                            and len(laid[0]) > 1
                            and px > max(min_px, round(px0 * 0.78))
                        ):
                            continue
                        (
                            lines,
                            spacing,
                            fitted_baseline,
                            line_advance,
                            metrics,
                            tight_rects,
                        ) = laid
                        fitted = True
                        used_height = try_h
                        label._line_spacing = spacing
                        break
                if fitted:
                    break
            px -= 1
        if not fitted and chat and (box.text or "").strip():
            # Last resort: paint at min size inside the cap — never drop chat text.
            try_h = max(
                base_height,
                float(getattr(box, "_chat_max_height", base_height) or base_height),
            )
            font.setPixelSize(min_px)
            laid = _layout_text_in_box(
                box.text,
                font,
                target_w=target_w,
                target_h=max(1, int(try_h - chat_vpad)),
                box_height=try_h,
                role="chat",
                source_line_count=source_line_count,
                source_spacing=box.line_spacing,
                vpad=chat_vpad,
                prefer_baseline=None,
                source_lines=[],
                spacing_scale=0.86,
                allow_overflow=True,
            )
            if laid is not None:
                (
                    lines,
                    spacing,
                    fitted_baseline,
                    line_advance,
                    metrics,
                    tight_rects,
                ) = laid
                fitted = True
                used_height = try_h
                label._line_spacing = spacing
        if not fitted:
            label.deleteLater()
            return None
        # Final weight lock — body never inherits a bold fragment style.
        if CONFIG.get("auto_font_weight", 0):
            if (box.role or "body") == "title":
                font.setBold(True)
            elif (box.role or "body") == "body":
                font.setBold(False)
        label._baseline_offset = fitted_baseline + paint_margin
        label._layout_lines = lines
        label._paint_margin = paint_margin
        label.setFont(font)
        # Content-tight height: region-flow may give a tall budget for wrapping,
        # but keeping the label that tall after top-align left huge empty voids.
        relative_bottom = (
            line_advance * max(0, len(lines) - 1)
            + max((rect.bottom() for rect in tight_rects), default=0)
        )
        content_h = max(
            1.0,
            float(fitted_baseline) + float(relative_bottom) + max(chat_vpad / 2.0, 2.0),
        )
        source_h = float(getattr(box, "_source_height", 0) or 0)
        # Cover source ink; for chat, never cover past the height cap.
        cover_h = max(content_h, source_h)
        if chat:
            cap_h = float(getattr(box, "_chat_max_height", used_height) or used_height)
            cover_h = max(content_h, min(max(content_h, source_h), min(used_height, cap_h)))
            cover_h = min(cover_h, cap_h)
            cover_h = max(cover_h, min(content_h, cap_h))
        else:
            cover_h = min(float(box.height), cover_h)
            cover_h = max(cover_h, min(float(box.height), content_h))
            cover_h = min(max(cover_h, content_h), max(used_height, base_height))
        label_x = round(box.x - paint_margin)
        label_y = round(box.y - paint_margin)
        label_w = max(1, round(box.width + paint_margin * 2))
        label_h = max(1, round(cover_h + paint_margin * 2))
        label.setGeometry(QRect(label_x, label_y, label_w, label_h))
        # Cover the source ink and the text actually painted — not the whole
        # box. A row widened into free space would otherwise drag its
        # background across the scrollbar and the panel beside it.
        painted_w = max((text_width(metrics, line) for line in lines), default=0)
        bg_w = max(
            1,
            round(
                max(
                    float(getattr(box, "_source_width", box.width) or 0),
                    min(float(box.width), painted_w + hpad),
                )
            ),
        )
        label._background_rect = QRect(
            paint_margin, paint_margin, bg_w, max(1, round(cover_h))
        )

        left = int(hpad / 2) + paint_margin
        right = left + target_w
        baseline = fitted_baseline + paint_margin
        paint_pad = max(1, round(stroke_width / 2.0 + 1.0))
        label._paint_bounds = []
        for line, tight in zip(lines, tight_rects):
            line_w = text_width(metrics, line)
            if box.alignment == "center":
                line_x = left + max(0, (target_w - line_w) // 2)
            elif box.alignment == "right":
                line_x = right - line_w
            else:
                line_x = left
            local_rect = tight.translated(round(line_x), round(baseline))
            label._paint_bounds.append(
                local_rect.translated(label_x, label_y).adjusted(
                    -paint_pad, -paint_pad, paint_pad, paint_pad
                )
            )
            baseline += line_advance
        label.show()
        return label


def close_all():
    for item in _overlays[:]:
        item.close()


def show_overlay(data: str, stream_id=None) -> int:
    load_config()
    if not CONFIG.get("enable", 1):
        return 0

    debug_mode = False
    debug_data = {}
    if data.strip().startswith("{") and "debugocr" in data:
        try:
            debug_data = json.loads(data)
            if debug_data.get("debugocr"):
                debug_mode = True
        except Exception:
            pass

    app = QApplication.instance()
    run_loop = False
    if app is None:
        app = QApplication([])
        run_loop = True

    if debug_mode:
        if _overlays:
            _overlays[0].update_debug_content(debug_data)
        else:
            overlay = Overlay([])
            overlay.debug_mode = True
            overlay.update_debug_content(debug_data)
            overlay.show()
    else:
        boxes = parse_boxes(data, stream_id=stream_id)
        if not boxes:
            if run_loop:
                return 0
            return 1
        if _overlays:
            _overlays[0].debug_mode = False
            _overlays[0].update_content(boxes)
        else:
            overlay = Overlay(boxes)
            overlay.show()

    if run_loop:
        return app.exec()
    return 0
