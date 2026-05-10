from __future__ import annotations

import ctypes
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from qtsymbols import (
    QApplication,
    QColor,
    QFont,
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

BG_ALPHA = 224
TEXT_MARGIN = 2
OUTLINE_WIDTH = 0.6
AUTO_MIN_FONT_SIZE = 10
AUTO_MAX_FONT_SIZE = 200
SAMPLE_BUFFER = 10
SAMPLE_STEP = 4
BACKGROUND_CLUSTER_DISTANCE = 28

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_from_capture(widget: QWidget):
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(
            int(widget.winId()), WDA_EXCLUDEFROMCAPTURE
        )
    except Exception:
        pass


CONFIG = {
    "enable": 1,
    "auto_mode": 0,
    "text_color": "white",
    "stroke_color": "black",
    "stroke_width": 3,
    "min_font_size": 8,
    "max_font_size": 48,
    "font_family": "",
    "background_color": "rgba(0, 0, 0, 180)",
    "box_expansion": 6,
    "timeout_ms": 6000,
    "horizontal_padding": 4,
    "vertical_padding": 4,
}


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


def color_to_rgba(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


def luminance(color: QColor) -> float:
    return 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()


def choose_text_color(background: QColor) -> QColor:
    return QColor(0, 0, 0) if luminance(background) > 128 else QColor(255, 255, 255)


def invert_color(color: QColor) -> QColor:
    return QColor(255 - color.red(), 255 - color.green(), 255 - color.blue())


def color_distance(a: QColor, b: QColor) -> int:
    return max(abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue()))


def average_color(colors: List[QColor]) -> QColor:
    count = len(colors)
    return QColor(
        sum(color.red() for color in colors) // count,
        sum(color.green() for color in colors) // count,
        sum(color.blue() for color in colors) // count,
        BG_ALPHA,
    )


def normalize_background_colors(colors: List[QColor]) -> List[QColor]:
    normalized = list(colors)
    used = [False] * len(colors)
    for i, color in enumerate(colors):
        if used[i]:
            continue
        group = [j for j, other in enumerate(colors) if not used[j] and color_distance(color, other) <= BACKGROUND_CLUSTER_DISTANCE]
        merged = average_color([colors[j] for j in group])
        for j in group:
            normalized[j] = merged
            used[j] = True
    return normalized


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
    r_sum = g_sum = b_sum = count = 0
    for y in range(top, bottom, SAMPLE_STEP):
        for x in range(left, right, SAMPLE_STEP):
            if inner_left <= x < inner_right and inner_top <= y < inner_bottom:
                continue
            color = QColor(image.pixel(x, y))
            r_sum += color.red()
            g_sum += color.green()
            b_sum += color.blue()
            count += 1
    if count == 0:
        return QColor(0, 0, 0, BG_ALPHA)
    return QColor(r_sum // count, g_sum // count, b_sum // count, BG_ALPHA)


def text_width(metrics: QFontMetrics, text: str) -> int:
    return metrics.horizontalAdvance(text) if hasattr(metrics, "horizontalAdvance") else metrics.width(text)


def wrap_text(text: str, font: QFont, max_width: int) -> List[str]:
    metrics = QFontMetrics(font)
    lines = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split(" ")
        if len(words) == 1:
            current = ""
            for ch in words[0]:
                candidate = current + ch
                if text_width(metrics, candidate) <= max_width or not current:
                    current = candidate
                else:
                    lines.append(current)
                    current = ch
            if current:
                lines.append(current)
            continue
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if text_width(metrics, candidate) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines or [text]


def fit_font(text: str, font: QFont, width: int, height: int, min_px: int, max_px: int):
    best_size = min_px
    best_lines = [text]
    low, high = min_px, max_px
    while low <= high:
        mid = (low + high) // 2
        font.setPixelSize(mid)
        metrics = QFontMetrics(font)
        lines = wrap_text(text, font, width)
        text_h = metrics.lineSpacing() * len(lines)
        text_w = max(text_width(metrics, line) for line in lines)
        if text_w <= width and text_h <= height:
            best_size = mid
            best_lines = lines
            low = mid + 1
        else:
            high = mid - 1
    font.setPixelSize(best_size)
    return font, best_lines


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


def parse_boxes(text: str) -> List[TextBox]:
    boxes: List[TextBox] = []
    text = re.sub(r"^\[Engine\].*?(\n|$)", "", text.strip())
    for match in BOX_PATTERN.finditer(text):
        value = match.group("text").strip()
        if not value:
            continue
        boxes.append(
            TextBox(
                x=int(match.group("x")),
                y=int(match.group("y")),
                width=int(match.group("w")),
                height=int(match.group("h")),
                text=value,
            )
        )
    return boxes


class StrokedLabel(QLabel):
    def paintEvent(self, event):
        auto = bool(CONFIG.get("auto_mode", 0)) and self.property("auto_background_color")
        background_color = self.property("auto_background_color") if auto else CONFIG["background_color"]
        text_color = self.property("auto_text_color") if auto else CONFIG["text_color"]
        stroke_color = self.property("auto_stroke_color") if auto else CONFIG["stroke_color"]
        stroke_width = OUTLINE_WIDTH if auto else float(CONFIG["stroke_width"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(parse_color(background_color))
        painter.drawRoundedRect(self.rect(), 4, 4)

        text = self.text()
        if not text:
            return
        font = self.font()
        if (not auto) and CONFIG["font_family"]:
            font.setFamily(CONFIG["font_family"])
        painter.setFont(font)
        metrics = QFontMetrics(font)
        lines = self.property("auto_lines") if auto else None
        lines = lines if lines else [text]
        total_h = metrics.lineSpacing() * len(lines)
        y = self.rect().top() + max(0, (self.rect().height() - total_h) // 2) + metrics.ascent()
        left = self.rect().left() + int((TEXT_MARGIN if auto else CONFIG["horizontal_padding"] / 2))
        right = self.rect().right() - int((TEXT_MARGIN if auto else CONFIG["horizontal_padding"] / 2))

        path = QPainterPath()
        for line in lines:
            width = text_width(metrics, line)
            x = left + max(0, (right - left - width) // 2) if auto else left
            path.addText(x, y, font, line)
            y += metrics.lineSpacing()
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

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(parse_color(text_color))
        painter.drawPath(path)


class Overlay(QWidget):
    def __init__(self, boxes: Sequence[TextBox]):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.Tool, True)
        if hasattr(Qt.WindowType, "WindowTransparentForInput"):
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        exclude_from_capture(self)

        screen = QApplication.primaryScreen()
        rect = screen.geometry()
        dpr = max(1.0, screen.devicePixelRatio())
        self.setGeometry(rect)
        self.screen_origin = rect.topLeft()
        self.labels = []
        self.boxes = list(boxes)
        self._render_boxes(self.boxes, dpr)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.close)
        self.timer.start(int(CONFIG["timeout_ms"]))
        _overlays.append(self)

    def _render_boxes(self, boxes: Sequence[TextBox], dpr: float):
        box_expansion = float(CONFIG["box_expansion"])
        auto = bool(CONFIG.get("auto_mode", 0))
        screenshot = None
        backgrounds = []
        if auto:
            screen = QApplication.primaryScreen()
            screenshot = screen.grabWindow(0).toImage() if screen else None
            for box in boxes:
                sample_box = TextBox(
                    x=box.x - self.screen_origin.x(),
                    y=box.y - self.screen_origin.y(),
                    width=box.width,
                    height=box.height,
                    text=box.text,
                )
                backgrounds.append(sample_background(screenshot, sample_box))
            backgrounds = normalize_background_colors(backgrounds)
        for idx, box in enumerate(boxes):
            background_color = box.background_color
            text_color = box.text_color
            stroke_color = box.stroke_color
            if auto:
                background = backgrounds[idx]
                foreground = choose_text_color(background)
                background_color = color_to_rgba(background)
                text_color = color_to_rgba(foreground)
                stroke_color = color_to_rgba(invert_color(foreground))
            scaled_width = int(box.width / dpr) + box_expansion
            scaled_height = int(box.height / dpr) + box_expansion
            scaled = TextBox(
                x=int((box.x - self.screen_origin.x()) / dpr) - box_expansion / 2,
                y=int((box.y - self.screen_origin.y()) / dpr) - box_expansion / 2,
                width=scaled_width,
                height=scaled_height,
                text=box.text,
                background_color=background_color,
                text_color=text_color,
                stroke_color=stroke_color,
            )
            self.labels.append(self._create_label(scaled))

    def update_content(self, boxes: Sequence[TextBox]):
        self.timer.start(int(CONFIG["timeout_ms"]))
        self.boxes = list(boxes)
        for label in self.labels:
            label.deleteLater()
        self.labels.clear()
        screen = QApplication.primaryScreen()
        rect = screen.geometry()
        dpr = max(1.0, screen.devicePixelRatio())
        self.screen_origin = rect.topLeft()
        self._render_boxes(self.boxes, dpr)
        self.show()

    def closeEvent(self, event):
        if self in _overlays:
            _overlays.remove(self)
        super().closeEvent(event)

    def _create_label(self, box: TextBox):
        label = StrokedLabel(box.text, self)
        label.setWordWrap(False)
        label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if box.background_color:
            label.setProperty("auto_background_color", box.background_color)
            label.setProperty("auto_text_color", box.text_color)
            label.setProperty("auto_stroke_color", box.stroke_color)

        auto = bool(CONFIG.get("auto_mode", 0))
        font = QFont(label.font())
        if CONFIG["font_family"]:
            font.setFamily(CONFIG["font_family"])
        min_px = AUTO_MIN_FONT_SIZE if auto else int(CONFIG["min_font_size"])
        max_px = AUTO_MAX_FONT_SIZE if auto else int(CONFIG["max_font_size"])
        vpad = TEXT_MARGIN * 2 if auto else int(CONFIG["vertical_padding"])
        hpad = TEXT_MARGIN * 2 if auto else int(CONFIG["horizontal_padding"])
        target_w = max(1, int(box.width - hpad))
        target_h = max(1, int(box.height - vpad))
        if auto:
            font, lines = fit_font(box.text, font, target_w, target_h, min_px, max_px)
            label.setProperty("auto_lines", lines)
        else:
            px = int(min(max_px, max(min_px, box.height - vpad)))
            font.setPixelSize(px)
            metrics = QFontMetrics(font)
            while px > min_px:
                width = text_width(metrics, box.text)
                if width <= target_w and metrics.height() <= target_h:
                    break
                px -= 1
                font.setPixelSize(px)
                metrics = QFontMetrics(font)
        label.setFont(font)
        label.setGeometry(QRect(int(box.x), int(box.y), int(box.width), int(box.height)))
        label.show()
        return label


def close_all():
    for item in _overlays[:]:
        item.close()


def show_overlay(data: str) -> int:
    load_config()
    if not CONFIG.get("enable", 1):
        return 0
    boxes = parse_boxes(data)
    if not boxes:
        return 1
    app = QApplication.instance()
    run_loop = False
    if app is None:
        app = QApplication([])
        run_loop = True
    if _overlays:
        _overlays[0].update_content(boxes)
    else:
        overlay = Overlay(boxes)
        overlay.show()
    if run_loop:
        return app.exec()
    return 0
