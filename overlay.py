"""
Windows PySide6 overlay text demo inspired by PlayTranslate's Android overlay renderer.

Features:
- borderless transparent topmost window
- real per-pixel alpha via Qt translucent background
- per-box background sampled from screenshot around source text
- original text obscuring with color-matched translucent fill
- automatic black/white text color from background luminance
- outline text for contrast
- auto-fit font size inside OCR/translation box
- vertical text example
- skeleton placeholder shimmer while "translation" is missing

Install:
    python -m pip install PySide6 pillow

Run:
    python overlay.py

Controls:
    Esc      close
    Space    toggle placeholder/translated demo text
    R        resample screen colors and rebuild overlay
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass, replace
from typing import Iterable

from PIL import Image, ImageGrab
from PySide6.QtCore import Qt, QTimer, QRect, QRectF, QPoint
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget


# ── Tuning constants ──────────────────────────────────────────────────────────

BG_ALPHA = 224                 # 224/255 ≈ 88% opaque, like Android Color.argb(224,...)
BOX_PADDING = 6                # px around OCR bounds
TEXT_MARGIN = 3                # px inside rendered box
OUTLINE_WIDTH = 1              # px text stroke
MIN_FONT_SIZE = 6              # px
MAX_FONT_SIZE = 200            # px
SAMPLE_BUFFER = 10             # px around OCR bounds
SAMPLE_STEP = 4                # read every 4th pixel, like app sampling loop
SKELETON_BAR_H = 8
SKELETON_RADIUS = 3


@dataclass(frozen=True)
class TextBox:
    translated_text: str
    bounds: tuple[int, int, int, int]  # source OCR bounds: left, top, right, bottom in virtual-screen pixels
    bg_color: tuple[int, int, int, int] = (0, 0, 0, BG_ALPHA)
    text_color: tuple[int, int, int] = (255, 255, 255)
    line_count: int = 1
    is_furigana: bool = False
    orientation: str = "horizontal"   # "horizontal" or "vertical"


# ── Color model ────────────────────────────────────────────────────────────────

def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def average_color_around_box(
    screenshot: Image.Image,
    bounds: tuple[int, int, int, int],
    origin: tuple[int, int],
    buffer_px: int = SAMPLE_BUFFER,
    step: int = SAMPLE_STEP,
) -> tuple[int, int, int, int]:
    """Sample average game background around text, excluding OCR inner rect."""
    origin_x, origin_y = origin
    left, top, right, bottom = (bounds[0] - origin_x, bounds[1] - origin_y, bounds[2] - origin_x, bounds[3] - origin_y)
    img_w, img_h = screenshot.size

    outer_l = max(0, left - buffer_px)
    outer_t = max(0, top - buffer_px)
    outer_r = min(img_w, right + buffer_px)
    outer_b = min(img_h, bottom + buffer_px)

    r_sum = g_sum = b_sum = count = 0
    px = screenshot.load()

    for y in range(outer_t, outer_b, step):
        for x in range(outer_l, outer_r, step):
            inside_inner = left <= x < right and top <= y < bottom
            if inside_inner:
                continue
            r, g, b = px[x, y][:3]
            r_sum += r
            g_sum += g
            b_sum += b
            count += 1

    if count == 0:
        return (0, 0, 0, BG_ALPHA)
    return (r_sum // count, g_sum // count, b_sum // count, BG_ALPHA)


def choose_text_color(bg_rgba: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """Same policy as app: bright background → black text, dark background → white text."""
    return (0, 0, 0) if luminance(bg_rgba[:3]) > 128 else (255, 255, 255)


def invert_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return (255 - rgb[0], 255 - rgb[1], 255 - rgb[2])


def colorize_boxes_from_screen(boxes: Iterable[TextBox], screenshot: Image.Image, origin: tuple[int, int]) -> list[TextBox]:
    colored: list[TextBox] = []
    for box in boxes:
        if box.is_furigana:
            colored.append(box)
            continue
        bg = average_color_around_box(screenshot, box.bounds, origin)
        colored.append(replace(box, bg_color=bg, text_color=choose_text_color(bg)))
    return colored


# ── Geometry + font fitting ───────────────────────────────────────────────────

def qrect_from_tuple(bounds: tuple[int, int, int, int], origin: tuple[int, int]) -> QRect:
    ox, oy = origin
    l, t, r, b = bounds
    return QRect(l - ox, t - oy, max(1, r - l), max(1, b - t))


def wrap_text(text: str, font: QFont, max_width: int) -> list[str]:
    metrics = QFontMetrics(font)
    lines: list[str] = []

    for paragraph in text.splitlines() or [text]:
        words = paragraph.split(" ")
        if len(words) == 1:
            current = ""
            for ch in words[0]:
                test = current + ch
                if metrics.horizontalAdvance(test) <= max_width or not current:
                    current = test
                else:
                    lines.append(current)
                    current = ch
            if current:
                lines.append(current)
            continue

        current = ""
        for word in words:
            test = word if not current else f"{current} {word}"
            if metrics.horizontalAdvance(test) <= max_width or not current:
                current = test
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)

    return lines


def measure_lines(lines: list[str], font: QFont) -> tuple[int, int]:
    if not lines:
        return 0, 0
    metrics = QFontMetrics(font)
    width = max(metrics.horizontalAdvance(line) for line in lines)
    height = metrics.lineSpacing() * len(lines)
    return width, height


def fit_font(text: str, box_w: int, box_h: int) -> tuple[QFont, list[str]]:
    avail_w = max(1, box_w - TEXT_MARGIN * 2)
    avail_h = max(1, box_h - TEXT_MARGIN * 2)

    best_size = MIN_FONT_SIZE
    best_lines = [text]
    lo, hi = MIN_FONT_SIZE, MAX_FONT_SIZE

    while lo <= hi:
        mid = (lo + hi) // 2
        font = QFont("Segoe UI", mid, QFont.Weight.Bold)
        font.setPixelSize(mid)
        lines = wrap_text(text, font, avail_w)
        text_w, text_h = measure_lines(lines, font)
        if text_w <= avail_w and text_h <= avail_h:
            best_size = mid
            best_lines = lines
            lo = mid + 1
        else:
            hi = mid - 1

    font = QFont("Segoe UI", best_size, QFont.Weight.Bold)
    font.setPixelSize(best_size)
    return font, best_lines


# ── Overlay widget ────────────────────────────────────────────────────────────

class OverlayWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        screen_geometry = QApplication.primaryScreen().virtualGeometry()
        self.origin = (screen_geometry.left(), screen_geometry.top())
        self.setGeometry(screen_geometry)

        self.screenshot = ImageGrab.grab(all_screens=True).convert("RGB")
        self.show_translation = True
        self.shimmer_phase = 0.0
        self.source_boxes = self.demo_boxes()
        self.boxes = colorize_boxes_from_screen(self.source_boxes, self.screenshot, self.origin)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(80)

    def demo_boxes(self) -> list[TextBox]:
        w, h = self.width(), self.height()
        ox, oy = self.origin
        cx = ox + w // 2
        cy = oy + h // 2
        return [
            TextBox(
                translated_text="Xin chào, đây là lớp phủ dịch tự khớp màu nền.",
                bounds=(cx - 360, cy - 120, cx + 360, cy - 40),
                line_count=2,
            ),
            TextBox(
                translated_text="Nền được lấy mẫu quanh hộp OCR, chữ tự chọn đen/trắng.",
                bounds=(cx - 300, cy + 20, cx + 300, cy + 92),
                line_count=2,
            ),
            TextBox(
                translated_text="縦書き",
                bounds=(cx + 420, cy - 180, cx + 500, cy + 160),
                line_count=1,
                orientation="vertical",
            ),
            TextBox(
                translated_text="ふりがな",
                bounds=(cx - 140, cy - 170, cx + 120, cy - 135),
                line_count=1,
                is_furigana=True,
                text_color=(255, 255, 255),
            ),
        ]

    def tick(self) -> None:
        self.shimmer_phase = (math.sin(time.time() * 5.0) + 1.0) / 2.0
        self.raise_()
        if not self.show_translation:
            self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Space:
            self.show_translation = not self.show_translation
            self.update()
        elif key == Qt.Key.Key_R:
            self.resample()

    def resample(self) -> None:
        self.screenshot = ImageGrab.grab(all_screens=True).convert("RGB")
        self.boxes = colorize_boxes_from_screen(self.source_boxes, self.screenshot, self.origin)
        self.update()

    def padded_rect(self, box: TextBox) -> QRect:
        rect = qrect_from_tuple(box.bounds, self.origin)
        if box.is_furigana:
            return rect
        return rect.adjusted(-BOX_PADDING, -BOX_PADDING, BOX_PADDING, BOX_PADDING).intersected(self.rect())

    def resolve_overlaps(self, boxes: list[TextBox]) -> list[tuple[TextBox, QRect]]:
        rects = [self.padded_rect(box) for box in boxes]

        horizontal = [i for i, b in enumerate(boxes) if not b.is_furigana and b.orientation != "vertical"]
        horizontal.sort(key=lambda i: rects[i].top())
        for a_pos, i in enumerate(horizontal):
            for j in horizontal[a_pos + 1:]:
                ri, rj = rects[i], rects[j]
                if ri.bottom() > rj.top() and ri.left() < rj.right() and ri.right() > rj.left():
                    mid = (ri.bottom() + rj.top()) // 2
                    rects[i] = QRect(ri.left(), ri.top(), ri.width(), max(1, mid - ri.top()))
                    rects[j] = QRect(rj.left(), mid, rj.width(), max(1, rj.bottom() - mid))

        vertical = [i for i, b in enumerate(boxes) if not b.is_furigana and b.orientation == "vertical"]
        vertical.sort(key=lambda i: rects[i].right(), reverse=True)
        for a_pos, i in enumerate(vertical):
            for j in vertical[a_pos + 1:]:
                ri, rj = rects[i], rects[j]
                if ri.left() < rj.right() and ri.top() < rj.bottom() and ri.bottom() > rj.top():
                    mid = (ri.left() + rj.right()) // 2
                    rects[i] = QRect(mid, ri.top(), max(1, ri.right() - mid), ri.height())
                    rects[j] = QRect(rj.left(), rj.top(), max(1, mid - rj.left()), rj.height())

        return list(zip(boxes, rects))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        for box, rect in self.resolve_overlaps(self.boxes):
            if box.is_furigana:
                self.draw_furigana(painter, box, rect)
            elif self.show_translation and box.translated_text:
                self.draw_text_box(painter, box, rect)
            else:
                self.draw_skeleton(painter, box, rect)

    def draw_text_box(self, painter: QPainter, box: TextBox, rect: QRect) -> None:
        r, g, b, a = box.bg_color
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(r, g, b, a))
        painter.drawRect(rect)

        if box.orientation == "vertical":
            self.draw_vertical_text(painter, box, rect)
            return

        inner = rect.adjusted(TEXT_MARGIN, TEXT_MARGIN, -TEXT_MARGIN, -TEXT_MARGIN)
        font, lines = fit_font(box.translated_text, inner.width(), inner.height())
        metrics = QFontMetrics(font)
        total_h = metrics.lineSpacing() * len(lines)
        y = inner.top() + max(0, (inner.height() - total_h) // 2) + metrics.ascent()

        for line in lines:
            x = inner.left() + max(0, (inner.width() - metrics.horizontalAdvance(line)) // 2)
            self.draw_outlined_text(painter, QPoint(x, y), line, font, box.text_color, invert_rgb(box.text_color))
            y += metrics.lineSpacing()

    def draw_vertical_text(self, painter: QPainter, box: TextBox, rect: QRect) -> None:
        chars = list(box.translated_text)
        if not chars:
            return
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(rect.width() * 0.7), int(rect.height() / max(1, len(chars)))))
        font = QFont("Segoe UI", size, QFont.Weight.Bold)
        font.setPixelSize(size)
        metrics = QFontMetrics(font)
        x = rect.center().x()
        total_h = metrics.lineSpacing() * len(chars)
        y = rect.top() + max(0, (rect.height() - total_h) // 2) + metrics.ascent()
        outline = invert_rgb(box.text_color)
        for ch in chars:
            ch_w = metrics.horizontalAdvance(ch)
            self.draw_outlined_text(painter, QPoint(x - ch_w // 2, y), ch, font, box.text_color, outline)
            y += metrics.lineSpacing()

    def draw_furigana(self, painter: QPainter, box: TextBox, rect: QRect) -> None:
        is_vertical = box.orientation == "vertical"
        size = max(4, int((rect.width() if is_vertical else rect.height()) * 0.7))
        font = QFont("Segoe UI", size, QFont.Weight.Bold)
        font.setPixelSize(size)
        metrics = QFontMetrics(font)
        if is_vertical:
            x = rect.center().x()
            y = rect.top() + metrics.ascent()
            for ch in box.translated_text:
                self.draw_outlined_text(painter, QPoint(x - metrics.horizontalAdvance(ch) // 2, y), ch, font, (255, 255, 255), (0, 0, 0), width=3)
                y += int(metrics.lineSpacing() * 0.8)
        else:
            self.draw_outlined_text(painter, QPoint(rect.left(), rect.top() + metrics.ascent()), box.translated_text, font, (255, 255, 255), (0, 0, 0), width=3)

    def draw_skeleton(self, painter: QPainter, box: TextBox, rect: QRect) -> None:
        r, g, b, a = box.bg_color
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(r, g, b, a))
        painter.drawRect(rect)

        alpha = int(255 * (0.3 + 0.5 * self.shimmer_phase))
        tr, tg, tb = box.text_color
        painter.setBrush(QColor(tr, tg, tb, alpha))

        side = TEXT_MARGIN * 2
        avail_w = max(1, rect.width() - side * 2)
        count = max(1, box.line_count)
        for line_idx in range(count):
            width_fraction = 0.6 if line_idx == count - 1 and count > 1 else 0.85
            bar_w = max(1, int(avail_w * width_fraction))
            center_y = rect.top() + rect.height() * (line_idx + 1) // (count + 1)
            bar = QRectF(rect.left() + side, center_y - SKELETON_BAR_H / 2, bar_w, SKELETON_BAR_H)
            painter.drawRoundedRect(bar, SKELETON_RADIUS, SKELETON_RADIUS)

    def draw_outlined_text(
        self,
        painter: QPainter,
        pos: QPoint,
        text: str,
        font: QFont,
        fill_rgb: tuple[int, int, int],
        outline_rgb: tuple[int, int, int],
        width: int = OUTLINE_WIDTH,
    ) -> None:
        path = QPainterPath()
        path.addText(pos, font, text)

        painter.setPen(QPen(QColor(*outline_rgb), width * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor(*fill_rgb))
        painter.drawPath(path)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = OverlayWidget()
    overlay.show()
    overlay.activateWindow()
    sys.exit(app.exec())
