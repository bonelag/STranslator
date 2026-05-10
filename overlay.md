# Windows Python overlay text renderer

Tài liệu này trích cơ chế overlay text của app Android PlayTranslate thành mẫu chạy trên Windows bằng Python.

File mẫu: [overlay.py](overlay.py)

## Mục tiêu

Overlay cần làm các việc sau:

1. Vẽ cửa sổ trong suốt, luôn nằm trên cùng màn hình.
2. Đặt từng hộp dịch đúng vị trí OCR text gốc.
3. Che text gốc bằng nền gần giống màu game phía sau.
4. Tự chọn màu chữ đen/trắng theo độ sáng nền.
5. Tự fit cỡ chữ vào box.
6. Vẽ outline quanh chữ để tăng contrast.
7. Hỗ trợ placeholder skeleton khi chưa có bản dịch.
8. Hỗ trợ text dọc và furigana demo.

## Cài đặt

```powershell
python -m pip install PySide6 pillow
```

Chạy:

```powershell
python overlay.py
```

Phím:

- `Esc`: đóng overlay
- `Space`: đổi giữa skeleton placeholder và text đã dịch
- `R`: chụp lại màn hình, sample lại màu nền/màu chữ

## Kiến trúc trong overlay.py

### 1. TextBox

`TextBox` là đơn vị render chính:

```python
@dataclass(frozen=True)
class TextBox:
    translated_text: str
    bounds: tuple[int, int, int, int]
    bg_color: tuple[int, int, int, int]
    text_color: tuple[int, int, int]
    line_count: int
    is_furigana: bool
    orientation: str
```

`bounds` là tọa độ screen pixel: `(left, top, right, bottom)`. Trong app thật, bounds đến từ OCR. Trong demo, bounds hard-code ở giữa màn hình.

## Cửa sổ overlay Windows

Overlay dùng PySide6 `QWidget` trong suốt thật:

- `Qt.FramelessWindowHint`: bỏ title bar/window border
- `Qt.WindowStaysOnTopHint`: luôn nổi trên cùng
- `Qt.Tool`: không hiện như app window bình thường
- `WA_TranslucentBackground`: bật per-pixel alpha thật
- `WA_NoSystemBackground`: không vẽ nền hệ thống
- `QApplication.primaryScreen().virtualGeometry()`: phủ toàn virtual screen

Code chính:

```python
self.setWindowFlags(
    Qt.FramelessWindowHint
    | Qt.WindowStaysOnTopHint
    | Qt.Tool
)
self.setAttribute(Qt.WA_TranslucentBackground, True)
self.setAttribute(Qt.WA_NoSystemBackground, True)
self.setGeometry(QApplication.primaryScreen().virtualGeometry())
```

Khác Tkinter color-key: PySide6 có alpha thật cho từng pixel, nên nền box `QColor(r, g, b, 224)` render đúng trong suốt 88%.

## Nhận biết màu nền

Mục tiêu: lấy màu game phía sau text, không lấy màu chữ gốc.

Hàm chính:

```python
average_color_around_box(screenshot, bounds)
```

Thuật toán:

1. Chụp screenshot bằng `ImageGrab.grab(all_screens=True)`.
2. Lấy vùng ngoài OCR bounds, mở rộng thêm `SAMPLE_BUFFER = 10px`.
3. Bỏ qua vùng bên trong bounds để tránh sample chữ gốc.
4. Đọc pixel cách nhau `SAMPLE_STEP = 4` để giảm chi phí.
5. Trung bình RGB.
6. Gán alpha cố định `BG_ALPHA = 224`.

Pseudo:

```python
outer = bounds.expand(10px)
for pixel in outer step 4:
    if pixel inside bounds:
        continue
    accumulate RGB
bg = average RGB + alpha 224
```

Lý do bỏ inner rect: vùng trong OCR box thường chứa chữ gốc. Nếu sample cả vùng trong, màu nền sẽ bị nhiễm bởi màu chữ, làm overlay không khớp nền game.

## Làm mờ / che text gốc

Không dùng blur. Không dùng image inpainting. Cơ chế là vẽ rectangle bán đục lên đúng vùng text gốc.

Alpha:

```python
BG_ALPHA = 224
```

`224 / 255 ≈ 88% opaque`. Nghĩa là nền overlay che gần hết text gốc, nhưng vẫn còn 12% trong suốt để nhìn thấy context game.

Trong Android app, TextView có alpha thật. PySide6 demo cũng có alpha thật nhờ `WA_TranslucentBackground`, nên có thể vẽ trực tiếp:

```python
painter.setBrush(QColor(r, g, b, 224))
painter.drawRect(rect)
```

Windows compositor tự blend overlay với game/app bên dưới.

## Tự chọn màu chữ

Hàm:

```python
choose_text_color(bg_rgba)
```

Dùng luminance chuẩn:

```python
luma = 0.299 * R + 0.587 * G + 0.114 * B
```

Quy tắc:

```python
if luma > 128:
    text = black
else:
    text = white
```

Chỉ chọn đen hoặc trắng. Cách này ổn định, rẻ, tránh chọn màu chữ kỳ dị.

## Outline chữ

Text thường dùng outline màu đảo RGB của màu chữ:

```python
outline = invert_rgb(text_color)
```

- Chữ trắng → outline đen
- Chữ đen → outline trắng

PySide6 dùng `QPainterPath.addText()` rồi vẽ path với `QPen` dày để tạo stroke:

```python
path = QPainterPath()
path.addText(pos, font, text)
painter.setPen(QPen(outline_color, width * 2))
painter.setBrush(fill_color)
painter.drawPath(path)
```

Cơ chế giống Android app: stroke quanh glyph + fill text.

## Tự khớp cỡ chữ

Hàm:

```python
fit_font_size(text, box_w, box_h)
```

Cơ chế:

1. Trừ padding trong box: `TEXT_MARGIN * 2`.
2. Binary search font size từ `MIN_FONT_SIZE = 6` đến `MAX_FONT_SIZE = 200`.
3. Với mỗi size:
   - wrap text theo width
   - đo text bằng Pillow `ImageDraw.textbbox`
   - nếu width/height vừa box → thử size lớn hơn
   - nếu không vừa → giảm size
4. Trả về size lớn nhất vừa box.

Pseudo:

```python
lo, hi = 6, 200
while lo <= hi:
    mid = (lo + hi) // 2
    lines = wrap(text, mid, max_width)
    measured = measure(lines)
    if measured fits:
        best = mid
        lo = mid + 1
    else:
        hi = mid - 1
```

Đây là tương đương Android `TextViewCompat.setAutoSizeTextTypeUniformWithConfiguration(min=6sp, max=200sp, step=1sp)`.

## Wrap text

`wrapped_lines()` xử lý 2 trường hợp:

- Có khoảng trắng: wrap theo word.
- Không có khoảng trắng (CJK/Japanese/Chinese hoặc text dài): wrap từng ký tự.

Điểm này quan trọng cho ngôn ngữ không dùng space giữa từ.

## Skeleton placeholder

Khi `translated_text` rỗng hoặc bấm `Space` chuyển sang placeholder, overlay vẽ skeleton bars:

- Nền vẫn là màu sample của box.
- Có `line_count` bars.
- Bar cuối ngắn hơn nếu nhiều dòng.
- Alpha shimmer dao động bằng sine theo thời gian.

```python
alphaish = 0.3 + 0.5 * shimmer_phase
```

Android app dùng `ValueAnimator` 0.8 → 0.3. Python demo dùng timer `root.after(80, tick)`.

## Text dọc

Demo hỗ trợ `orientation="vertical"`.

Cách vẽ đơn giản:

1. Tách text thành từng ký tự.
2. Font size = min(70% width box, height / số ký tự).
3. Vẽ từng ký tự từ trên xuống.

```python
chars = list(text)
size = min(width * 0.7, height / len(chars))
for ch in chars:
    draw ch
    y += size
```

Android app làm khác: tạo TextView với width/height đảo nhau rồi xoay 90°. Python demo chọn cách stack ký tự để dễ đọc và dễ port.

## Furigana

Furigana demo dùng `is_furigana=True`.

Khác text thường:

- Không vẽ nền rectangle.
- Luôn chữ trắng.
- Luôn outline đen.
- Font size = 70% chiều cao box.

Đây bắt chước cơ chế app: furigana là nhãn nhỏ nằm trên chữ gốc hoặc cạnh cột text dọc.

## Xử lý chồng lấn box

Hàm:

```python
resolve_overlaps(boxes)
```

Text ngang:

- Sort theo `top`.
- Nếu 2 box chồng nhau theo chiều dọc và giao nhau theo chiều ngang, cắt tại midpoint.

```python
mid = (bottom_a + top_b) // 2
bottom_a = mid
top_b = mid
```

Text dọc:

- Sort theo `right` giảm dần.
- Nếu 2 box dọc chồng theo chiều ngang và giao nhau theo chiều dọc, cắt tại midpoint.

```python
mid = (left_a + right_b) // 2
left_a = mid
right_b = mid
```

Furigana không resolve overlap.

## Pinhole mode: không nằm trong mẫu Tkinter

Android app có pinhole mode: nền box 100% đục, sau đó đục các điểm 50% alpha theo grid 3px để phát hiện game text thay đổi bên dưới overlay.

Tkinter color-key window không phù hợp để demo pinhole đúng vì:

1. Tkinter Canvas không có alpha thật cho từng pixel.
2. Pinhole detection cần screenshot raw, clean ref, overlay rendered bitmap cùng resolution.
3. Windows compositor + color-key transparency không bảo đảm blend toán học `raw = 0.5*game + 0.5*overlay`.

Nếu muốn port pinhole thật sang Windows, nên dùng:

- PyQt/PySide với per-pixel alpha ARGB window, hoặc
- DirectComposition/Direct2D, hoặc
- Win32 layered window với `UpdateLayeredWindow` và bitmap ARGB.

Core math cần giữ:

```text
predicted = (clean_ref + overlay_rendered) / 2
changed = abs(raw - predicted) > threshold
```

Và pinhole mask:

- spacing = 3px
- alpha = 128/255 tại pinhole
- pixel khác = opaque overlay

## Dữ liệu đầu vào thực tế

Trong demo, `demo_boxes()` hard-code box giữa màn hình. Trong app thật, cần thay bằng OCR output:

```python
TextBox(
    translated_text=translated_text,
    bounds=(ocr_left, ocr_top, ocr_right, ocr_bottom),
    line_count=ocr_line_count,
    orientation="horizontal" or "vertical",
)
```

Sau đó:

```python
screenshot = ImageGrab.grab(all_screens=True).convert("RGB")
boxes = colorize_boxes_from_screen(raw_boxes, screenshot)
render(boxes)
```

## Các điểm khác Android app

| Tính năng | Android app | Python demo |
|---|---|---|
| Window overlay | Accessibility overlay | PySide6 translucent topmost widget |
| Alpha nền | Alpha thật trong TextView | Alpha thật qua `WA_TranslucentBackground` |
| Font | `Typeface.DEFAULT_BOLD` | Segoe UI Bold |
| Auto-size | Android TextViewCompat | Binary search + `QFontMetrics` |
| Outline | Paint stroke/fill 2 pass | `QPainterPath` + stroked `QPen` |
| Pinhole | Có, chính xác theo pixel | Không demo chính xác |
| OCR | ML Kit | Không có; box hard-code |
| Translation | Backend app | Không có; text hard-code |

## Nâng cấp đề xuất

Nếu muốn biến demo này thành overlay production trên Windows:

1. Thêm OCR bằng `PaddleOCR`, `EasyOCR`, Windows OCR API, hoặc Tesseract.
2. Capture bằng `mss` thay `ImageGrab` để nhanh hơn.
3. Cache font measurement theo `(text, box_w, box_h)` để giảm CPU.
4. Tách pipeline: capture → OCR → color sample → translate → render.
5. Nếu cần phát hiện text thay đổi dưới overlay, port pinhole mode sang ARGB/per-pixel alpha rendering.

## Tóm tắt thuật toán render

```text
for each OCR group:
    bounds = OCR bounding box
    bg = average_color(outside bounds, exclude inside bounds)
    text_color = black if luminance(bg) > 128 else white
    overlay_rect = bounds expanded by 6px
    draw bg with alpha 224/255
    font_size = largest size 6..200 that fits text in rect
    outline = invert(text_color)
    draw outline
    draw text
```

Kết quả: text gốc bị che bởi màu gần giống nền game, bản dịch tự co giãn và tự tương phản với nền.
