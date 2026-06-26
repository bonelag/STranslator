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
    "stroke_width": 3,
    "min_font_size": 8,
    "max_font_size": 48,
    "font_family": "",
    "background_color": "rgba(0, 0, 0, 180)",
    "timeout_ms": 6000,
    "horizontal_padding": 4,
    "vertical_padding": 4,
    "screen_capture_protection": 0,
    "auto_background": 0,
    "auto_text_color": 0,
    "auto_font_weight": 0,
}

# Bề dày nét chữ / chiều cao dòng vượt ngưỡng này thì coi là chữ đậm.
# Dùng tỉ lệ (bất biến với cỡ chữ & độ dài dòng) thay vì tỉ lệ phủ mực,
# để các dòng cùng độ đậm cho ra kết quả đồng nhất.
BOLD_REL_THICKNESS = 0.12


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
    for y in range(top, bottom, SAMPLE_STEP):
        for x in range(left, right, SAMPLE_STEP):
            if inner_left <= x < inner_right and inner_top <= y < inner_bottom:
                continue
            colors.append(QColor(image.pixel(x, y)))
    if not colors:
        return QColor(0, 0, 0, BG_ALPHA)
    dom = find_dominant_color(colors)
    dom.setAlpha(BG_ALPHA)
    return dom


def color_to_rgba(color: QColor) -> str:
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


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
    
    # 1. Ép về Trắng nếu rất gần trắng
    if r > 215 and g > 215 and b > 215:
        return QColor(255, 255, 255)
    # 2. Ép về Đen nếu rất gần đen
    if r < 40 and g < 40 and b < 40:
        return QColor(0, 0, 0)
    # 3. Ép về Xám trung tính nếu các kênh gần nhau
    max_val = max(r, g, b)
    min_val = min(r, g, b)
    if max_val - min_val < 15:
        gray_val = (r + g + b) // 3
        if gray_val > 200:
            return QColor(255, 255, 255)
        elif gray_val < 50:
            return QColor(0, 0, 0)
        return QColor(gray_val, gray_val, gray_val)
        
    return color


def sample_text_color(image, box: "TextBox", bg_color: QColor) -> QColor:
    if image is None or image.isNull():
        return QColor(255, 255, 255)
        
    pixels_with_diff = []
    max_diff = 0
    
    # Bước 1: Quét tất cả các pixel trong box chữ để tính độ lệch màu và tìm max_diff
    for y in range(int(box.y), int(box.y + box.height)):
        for x in range(int(box.x), int(box.x + box.width)):
            if 0 <= x < image.width() and 0 <= y < image.height():
                pixel_color = QColor(image.pixel(x, y))
                diff = color_distance(pixel_color, bg_color)
                pixels_with_diff.append((pixel_color, diff))
                if diff > max_diff:
                    max_diff = diff
                    
    # Nếu chênh lệch màu cực đại quá nhỏ, trả về màu tương phản
    if max_diff < 35:
        return QColor(255, 255, 255) if bg_color.lightness() < 128 else QColor(0, 0, 0)
        
    # Bước 2: Chỉ lấy trung bình cộng của các pixel có độ chênh lệch màu thuộc nhóm cao nhất
    # (chênh lệch so với max_diff không quá 60 đơn vị RGB) để lấy đúng phần ruột chữ sáng nhất
    r_sum = g_sum = b_sum = count = 0
    threshold = max(35, max_diff - 60)
    for pixel_color, diff in pixels_with_diff:
        if diff >= threshold:
            r_sum += pixel_color.red()
            g_sum += pixel_color.green()
            b_sum += pixel_color.blue()
            count += 1
            
    if count == 0:
        return QColor(255, 255, 255) if bg_color.lightness() < 128 else QColor(0, 0, 0)
        
    avg_color = QColor(r_sum // count, g_sum // count, b_sum // count)
    return clean_text_color(avg_color)


def estimate_text_boldness(image, box: "TextBox", bg_color: QColor) -> bool:
    # Đo bề dày nét chữ gốc một cách bất biến với cỡ chữ & độ dài dòng:
    # quét từng hàng ngang, lấy độ dài các đoạn "mực" liên tiếp (= bề dày nét
    # dọc), lấy trung vị rồi chia cho chiều cao dòng. Nét dày tương đối -> đậm.
    if image is None or image.isNull():
        return False

    x0 = max(0, int(box.x))
    y0 = max(0, int(box.y))
    x1 = min(image.width(), int(box.x + box.width))
    y1 = min(image.height(), int(box.y + box.height))
    h = y1 - y0
    if h <= 0 or x1 - x0 <= 0:
        return False

    # Ngưỡng "mực" thích nghi theo độ tương phản chữ/nền thực tế.
    text_color = sample_text_color(image, box, bg_color)
    contrast = color_distance(text_color, bg_color)
    if contrast < 40:
        return False  # tương phản quá thấp, không tin cậy
    ink_thresh = contrast * 0.5

    run_lengths = []
    for y in range(y0, y1, 2):
        run = 0
        for x in range(x0, x1):
            if color_distance(QColor(image.pixel(x, y)), bg_color) > ink_thresh:
                run += 1
            else:
                if run > 0:
                    run_lengths.append(run)
                run = 0
        if run > 0:
            run_lengths.append(run)

    if not run_lengths:
        return False
    # Loại đoạn quá dài (nét ngang / gạch chân) để median phản ánh bề dày nét dọc.
    max_keep = max(2, h * 0.6)
    filtered = [r for r in run_lengths if r <= max_keep] or run_lengths
    filtered.sort()
    median = filtered[len(filtered) // 2]
    return (median / h) > BOLD_REL_THICKNESS


def find_dominant_color(colors: List[QColor]) -> QColor:
    if not colors:
        return QColor(255, 255, 255)
        
    groups = []
    for c in colors:
        added = False
        for g in groups:
            if color_distance(c, g[0]) < 45:
                g.append(c)
                added = True
                break
        if not added:
            groups.append([c])
            
    longest_group = max(groups, key=len)
    
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


PENDING_BOXES: List[tuple] = []


def set_pending_boxes(boxes: List[tuple]):
    global PENDING_BOXES
    PENDING_BOXES = list(boxes)


def distribute_lines(orig_boxes: List[tuple], trans_lines: List[str]) -> List[TextBox]:
    N = len(orig_boxes)
    M = len(trans_lines)
    if N == 0 or M == 0:
        return []

    # Nếu số dòng dịch nhiều hơn hoặc bằng dòng gốc
    if M >= N:
        boxes = []
        for i in range(M):
            idx = min(i, N - 1)
            x, y, w, h, _ = orig_boxes[idx]
            # Nếu dư dòng dịch so với dòng gốc, ta dịch chuyển y xuống dưới một chút để không đè chữ
            if i >= N:
                y += (i - N + 1) * (h + 5)
            boxes.append(
                TextBox(
                    x=float(x),
                    y=float(y),
                    width=float(w),
                    height=float(h),
                    text=trans_lines[i]
                )
            )
        return boxes

    # Nếu số dòng dịch ít hơn (M < N), gộp nhóm
    total_orig_len = sum(len(box[4]) for box in orig_boxes) or 1
    orig_ratios = []
    curr_sum = 0
    for box in orig_boxes:
        curr_sum += len(box[4])
        orig_ratios.append(curr_sum / total_orig_len)

    total_trans_len = sum(len(line) for line in trans_lines) or 1
    trans_ratios = []
    curr_sum = 0
    for line in trans_lines:
        curr_sum += len(line)
        trans_ratios.append(curr_sum / total_trans_len)

    groups = [[] for _ in range(M)]
    curr_group = 0
    for i in range(N):
        while curr_group < M - 1 and orig_ratios[i] > (trans_ratios[curr_group] + trans_ratios[curr_group + 1]) / 2:
            curr_group += 1
        groups[curr_group].append(i)

    # Đảm bảo không nhóm nào rỗng
    for j in range(M):
        if not groups[j]:
            for k in range(M):
                if len(groups[k]) > 1:
                    if k < j:
                        val = groups[k].pop(-1)
                        groups[j].append(val)
                    else:
                        val = groups[k].pop(0)
                        groups[j].append(val)
                    groups[j].sort()
                    break

    for j in range(M):
        if not groups[j]:
            groups[j] = [min(j, N - 1)]

    boxes = []
    for j, indices in enumerate(groups):
        if not indices:
            continue
        first_idx = indices[0]
        x_min, y_min, w_first, h_first, _ = orig_boxes[first_idx]
        x_max = x_min + w_first
        y_max = y_min + h_first

        for idx in indices[1:]:
            x, y, w, h, _ = orig_boxes[idx]
            x_min = min(x_min, x)
            y_min = min(y_min, y)
            x_max = max(x_max, x + w)
            y_max = max(y_max, y + h)

        boxes.append(
            TextBox(
                x=float(x_min),
                y=float(y_min),
                width=float(x_max - x_min),
                height=float(y_max - y_min),
                text=trans_lines[j]
            )
        )
    return boxes


def is_effectively_empty(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if not re.search(r"[\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t):
        return True
    return False


def parse_indexed_boxes(text: str, orig_boxes: List[tuple]) -> List[TextBox]:
    matches = list(re.finditer(r"\[#?(\d+)\][:\s-]*", text))
    if not matches:
        return []
    
    N = len(orig_boxes)
    parsed_indices = []
    parsed_values = []
    for idx, match in enumerate(matches):
        idx_val = int(match.group(1))
        start_pos = match.end()
        end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        val = text[start_pos:end_pos].strip()
        parsed_indices.append(idx_val)
        parsed_values.append(val)
        
    line_texts = [""] * N
    for match_idx, idx_val in enumerate(parsed_indices):
        if 0 <= idx_val - 1 < N:
            line_texts[idx_val - 1] = parsed_values[match_idx]
            
    groups = []
    group_texts = []
    pending_empty_indices = []
    
    for i in range(N):
        if not is_effectively_empty(line_texts[i]):
            new_group = [i]
            if pending_empty_indices:
                new_group.extend(pending_empty_indices)
                pending_empty_indices = []
            groups.append(new_group)
            group_texts.append(line_texts[i])
        else:
            if groups:
                groups[-1].append(i)
            else:
                pending_empty_indices.append(i)
                
    if pending_empty_indices and groups:
        groups[-1].extend(pending_empty_indices)
        
    boxes = []
    for g_idx, indices in enumerate(groups):
        if not indices:
            continue
        first_idx = indices[0]
        x_min, y_min, w_first, h_first, _ = orig_boxes[first_idx]
        x_max = x_min + w_first
        y_max = y_min + h_first
        
        for idx in indices[1:]:
            x, y, w, h, _ = orig_boxes[idx]
            x_min = min(x_min, x)
            y_min = min(y_min, y)
            x_max = max(x_max, x + w)
            y_max = max(y_max, y + h)
            
        boxes.append(
            TextBox(
                x=float(x_min),
                y=float(y_min),
                width=float(x_max - x_min),
                height=float(y_max - y_min),
                text=group_texts[g_idx]
            )
        )
    return boxes


def parse_boxes(text: str) -> List[TextBox]:
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
                TextBox(
                    x=int(match.group("x")),
                    y=int(match.group("y")),
                    width=int(match.group("w")),
                    height=int(match.group("h")),
                    text=value,
                )
            )
        return boxes
    else:
        # Nếu không chứa tọa độ, lấy từ PENDING_BOXES
        global PENDING_BOXES
        if not PENDING_BOXES:
            return []

        indexed_boxes = parse_indexed_boxes(text, PENDING_BOXES)
        if indexed_boxes:
            return indexed_boxes

        translated_lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned_lines = []
        for line in translated_lines:
            cleaned = re.sub(r"^\[#\d+\][:\s-]*", "", line).strip()
            if cleaned:
                cleaned_lines.append(cleaned)
        if not cleaned_lines:
            return []

        return distribute_lines(PENDING_BOXES, cleaned_lines)


class StrokedLabel(QLabel):
    def __init__(self, *args, skip_background: bool = False, text_color: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._skip_background = skip_background
        self._custom_text_color = text_color

    def paintEvent(self, event):
        background_color = CONFIG["background_color"]
        text_color = self._custom_text_color or CONFIG["text_color"]
        stroke_color = CONFIG["stroke_color"]
        stroke_width = float(CONFIG["stroke_width"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not self._skip_background:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(parse_color(background_color))
            painter.drawRoundedRect(self.rect(), 4, 4)

        text = self.text()
        if not text:
            return
        font = self.font()
        if CONFIG["font_family"]:
            font.setFamily(CONFIG["font_family"])
        painter.setFont(font)
        metrics = QFontMetrics(font)
        left = self.rect().left() + int(CONFIG["horizontal_padding"] / 2)
        right = self.rect().right() - int(CONFIG["horizontal_padding"] / 2)
        target_w = max(1, right - left)
        lines = wrap_text(text, font, target_w)
        total_h = metrics.lineSpacing() * len(lines)
        y = self.rect().top() + max(0, (self.rect().height() - total_h) // 2) + metrics.ascent()

        # Tính baseline mỗi dòng. Stroke (viền) chỉ vẽ khi width > 0 — nếu
        # width = 0 mà vẫn drawPath thì QPen width 0 = pen cosmetic 1px,
        # tạo ra viền mảnh thừa + làm chữ trông răng cưa.
        # Fill (ruột chữ): luôn vẽ bằng native drawText (có hinting) cho mượt.
        line_positions = []
        for line in lines:
            line_positions.append((line, y))
            y += metrics.lineSpacing()

        if stroke_width > 0:
            path = QPainterPath()
            for line, ly in line_positions:
                path.addText(left, ly, font, line)
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
        for line, ly in line_positions:
            painter.drawText(int(left), int(ly), line)


class Overlay(QWidget):
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

        screen = QApplication.primaryScreen()
        rect = screen.geometry()
        dpr = max(1.0, screen.devicePixelRatio())
        self.setGeometry(rect)
        self.screen_origin = rect.topLeft()
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

        for box in boxes:
            scaled_width = int(box.width / dpr)
            scaled_height = int(box.height / dpr)
            x = int((box.x - self.screen_origin.x()) / dpr)
            y = int((box.y - self.screen_origin.y()) / dpr)
            scaled = TextBox(
                x=x,
                y=y,
                width=scaled_width,
                height=scaled_height,
                text=box.text,
                background_color=box.background_color,
                text_color=box.text_color,
                stroke_color=box.stroke_color,
            )

            auto_bg = CONFIG.get("auto_background", 0)
            auto_text = CONFIG.get("auto_text_color", 0)
            auto_weight = CONFIG.get("auto_font_weight", 0)
            if (auto_bg or auto_text or auto_weight) and screenshot and not screenshot.isNull():
                # 1. Lấy mẫu màu nền dùng tọa độ vật lý (chưa chia dpr) để trích xuất chính xác trên ảnh screenshot
                x_pixel = box.x - self.screen_origin.x()
                y_pixel = box.y - self.screen_origin.y()
                w_pixel = box.width
                h_pixel = box.height
                sample_box = TextBox(x_pixel, y_pixel, w_pixel, h_pixel, "")
                bg_color = sample_background(screenshot, sample_box)
                bg_color.setAlpha(255)  # 100% opaque, giống debug

                if auto_bg:
                    grad_colors = sample_gradient_colors(screenshot, sample_box, bg_color)

                    # 2. Tính r_bg (vùng vẽ logic) tuân theo 100% logic mở rộng của DEBUG_BG_PADDING giống debug
                    bg_left = scaled_width * DEBUG_BG_PADDING["left_percent"] + DEBUG_BG_PADDING["left_px"]
                    bg_right = scaled_width * DEBUG_BG_PADDING["right_percent"] + DEBUG_BG_PADDING["right_px"]
                    bg_top = scaled_height * DEBUG_BG_PADDING["top_percent"] + DEBUG_BG_PADDING["top_px"]
                    bg_bottom = scaled_height * DEBUG_BG_PADDING["bottom_percent"] + DEBUG_BG_PADDING["bottom_px"]
                    bg_rx = round(x - bg_left)
                    bg_ry = round(y - bg_top)
                    bg_rw = max(1, round(scaled_width + bg_left + bg_right))
                    bg_rh = max(1, round(scaled_height + bg_top + bg_bottom))
                    r_bg = QRect(bg_rx, bg_ry, bg_rw, bg_rh)

                    brush = create_gradient_brush(r_bg, grad_colors)
                    self.auto_bg_rects.append((r_bg, brush))
                else:
                    self.auto_bg_rects.append((None, None))

                if auto_text:
                    text_color = sample_text_color(screenshot, sample_box, bg_color)
                    scaled.text_color = color_to_rgba(text_color)

                if auto_weight:
                    scaled.bold = estimate_text_boldness(screenshot, sample_box, bg_color)
            else:
                self.auto_bg_rects.append((None, None))

            skip_bg = bool(auto_bg and screenshot and not screenshot.isNull())
            self.labels.append(self._create_label(scaled, skip_background=skip_bg))

    def update_content(self, boxes: Sequence[TextBox]):
        self.timer.start(int(CONFIG["timeout_ms"]))
        self.debug_mode = False
        set_capture_affinity(self, True)
        self.boxes = list(boxes)
        for label in self.labels:
            label.deleteLater()
        self.labels.clear()
        self.auto_bg_rects = []
        screen = QApplication.primaryScreen()
        rect = screen.geometry()
        dpr = max(1.0, screen.devicePixelRatio())
        self.screen_origin = rect.topLeft()
        self.actual_fill_level = None
        self.debug_screenshot = None

        screenshot = None
        if CONFIG.get("auto_background", 0) or CONFIG.get("auto_text_color", 0) or CONFIG.get("auto_font_weight", 0):
            screenshot = screen.grabWindow(0).toImage()

        self._render_boxes(self.boxes, dpr, screenshot)
        self.update()
        self.show()

    def update_debug_content(self, debug_data: dict):
        from qtsymbols import QRect
        self.timer.start(int(CONFIG["timeout_ms"]))
        self.debug_mode = True
        set_capture_affinity(self, False)
        for label in self.labels:
            label.deleteLater()
        self.labels.clear()
        self.auto_bg_rects = []
        screen = QApplication.primaryScreen()
        rect = screen.geometry()
        dpr = max(1.0, screen.devicePixelRatio())
        self.screen_origin = rect.topLeft()

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
            rx = int((c["x"] - self.screen_origin.x()) / dpr)
            ry = int((c["y"] - self.screen_origin.y()) / dpr)
            rw = int(c["w"] / dpr)
            rh = int(c["h"] / dpr)
            
            x_pixel = c["x"] - self.screen_origin.x()
            y_pixel = c["y"] - self.screen_origin.y()
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
            rx = int((l["x"] - self.screen_origin.x()) / dpr)
            ry = int((l["y"] - self.screen_origin.y()) / dpr)
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
            
            x_pixel = l["x"] - self.screen_origin.x()
            y_pixel = l["y"] - self.screen_origin.y()
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
            rx = int((p["x"] - self.screen_origin.x()) / dpr)
            ry = int((p["y"] - self.screen_origin.y()) / dpr)
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
            
            x_pixel = p["x"] - self.screen_origin.x()
            y_pixel = p["y"] - self.screen_origin.y()
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

    def _create_label(self, box: TextBox, skip_background: bool = False):
        label = StrokedLabel(box.text, self, skip_background=skip_background, text_color=box.text_color)
        label.setWordWrap(True)
        label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        font = QFont(label.font())
        if CONFIG["font_family"]:
            font.setFamily(CONFIG["font_family"])
        if box.bold is not None:
            font.setBold(box.bold)
        min_px = int(CONFIG["min_font_size"])
        max_px = int(CONFIG["max_font_size"])
        vpad = int(CONFIG["vertical_padding"])
        hpad = int(CONFIG["horizontal_padding"])
        
        target_w = max(1, int(box.width - hpad))
        target_h = max(1, int(box.height - vpad))
        
        px = max_px
        font.setPixelSize(px)
        metrics = QFontMetrics(font)
        while px > min_px:
            lines = wrap_text(box.text, font, target_w)
            text_h = metrics.lineSpacing() * len(lines)
            text_w = max(text_width(metrics, line) for line in lines) if lines else 0
            if text_w <= target_w and text_h <= target_h:
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
        boxes = parse_boxes(data)
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
