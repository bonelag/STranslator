import os, importlib
from myutils.config import globalconfig, _TR
from qtsymbols import *
from myutils.commonbase import ArgsEmptyExc
from myutils.hwnd import safepixmap
from myutils.utils import stringfyerror
from traceback import print_exc
import threading, gobject, NativeUtils
from ocrengines.baseocrclass import baseocr, OCRResultParsed


def imageCut(hwnd, x1, y1, x2, y2) -> QImage:
    succ, pix = NativeUtils.GdiCropImage(x1, y1, x2, y2, hwnd)
    pix = safepixmap(pix).toImage()
    if hwnd:
        return succ, pix
    return pix


_nowuseocrx = None
_nowuseocr = None
_ocrengine: baseocr = None
_initlock = threading.Lock()


def ocr_end():
    global _ocrengine, _nowuseocr, _nowuseocrx
    with _initlock:
        _nowuseocr = None
        _nowuseocrx = None
        _ocrengine = None


def ocr_init():
    with _initlock:
        __ocr_init()


def __ocr_init():
    global _nowuseocr, _ocrengine, _nowuseocrx
    use = None
    for k in globalconfig["ocr"]:
        if globalconfig["ocr"][k]["use"] == True and os.path.exists(
            ("LunaTranslator/ocrengines/" + k + ".py")
        ):
            use = k
            break
    _nowuseocrx = use
    if use is None:
        raise Exception(_TR("未选择OCR引擎"))
    if _nowuseocr == use:
        return
    _ocrengine = None
    _nowuseocr = None
    aclass = importlib.import_module("ocrengines." + use).OCR
    _ocrengine = aclass(use)
    _nowuseocr = use


def dispatch_debug_ocr(parsed: OCRResultParsed, qimage: QImage, offset=None):
    import json
    import functools
    import ovl
    import re
    from ocrengines.baseocrclass import OCRBlock, _OCRBlockS

    result = parsed.result
    if not result or not result.hasboxs:
        return

    offset_x, offset_y = offset or (0, 0)
    raw_lines = getattr(result, "raw_lines", [])
    if not raw_lines:
        return

    # 1. Thuật toán gộp các dòng sát nhau thành Đoạn văn (Paragraphs)
    # Sử dụng thuật toán tương tự __nearmergeboxs của baseocrclass nhưng với ngưỡng lớn (1.5 lần chiều cao chữ)
    blocksX = [ _OCRBlockS([OCRBlock(text, box)]) for box, text in raw_lines ]
    n = len(blocksX)
    dist_matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            dist_matrix[i][j] = dist_matrix[j][i] = blocksX[i].blocks[0].distance(blocksX[j].blocks[0])

    i = 0
    while i < len(blocksX):
        box1 = blocksX[i]
        merged_happened = False
        j = 0
        while j < len(blocksX):
            if i == j:
                j += 1
                continue
            box2 = blocksX[j]
            # Ngưỡng lớn: 1.5 lần chiều cao/rộng tối thiểu của chữ
            threshold = 1.5 * min(box1.whmin, box2.whmin)
            if dist_matrix[i][j] <= threshold:
                box1.merge(blocksX.pop(j))
                dist_matrix.pop(j)
                for row in dist_matrix:
                    row.pop(j)
                n_curr = len(blocksX)
                for k in range(n_curr):
                    if i != k:
                        dist_matrix[i][k] = dist_matrix[k][i] = box1.distance(blocksX[k])
                if j < i:
                    i -= 1
                    box1 = blocksX[i]
                j = 0
                merged_happened = True
            else:
                j += 1
        if not merged_happened:
            i += 1

    paragraphs = []
    for b_group in blocksX:
        box0 = b_group.blocks[0].box4
        for idx in range(1, len(b_group.blocks)):
            box0 = _OCRBlockS.four_point_box_union(box0, b_group.blocks[idx].box4)
        if box0:
            paragraphs.append({
                "x": box0[0] + offset_x,
                "y": box0[1] + offset_y,
                "w": box0[2] - box0[0],
                "h": box0[3] - box0[1]
            })

    # 2. Dòng (lines) và Chữ (chars)
    lines = []
    chars = []

    def split_into_words(text: str) -> list[str]:
        # Tách chữ CJK ra, còn lại gom cụm không phải khoảng trắng thành 1 từ (hỗ trợ hoàn hảo mọi dấu tiếng Việt)
        pattern = r'([^\s\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+|[\u4e00-\u9fff]|[\u3040-\u30ff]|[\uac00-\ud7af])'
        return [w for w in re.split(pattern, text) if w]

    for box, text in raw_lines:
        if not text:
            continue
        text = text.strip()
        if not text:
            continue
        lx = box[0] + offset_x
        ly = box[1] + offset_y
        lw = box[2] - box[0]
        lh = box[3] - box[1]

        lines.append({
            "x": lx,
            "y": ly,
            "w": lw,
            "h": lh,
            "font": "Arial" # Mặc định ban đầu
        })

        if text:
            words = split_into_words(text)
            total_chars = len(text)
            if total_chars > 0:
                vertical = result.vertical
                
                if not vertical:
                    font = QFont()
                    font.setPixelSize(max(10, int(lh * 0.75)))
                    metrics = QFontMetrics(font)
                    def t_width(t_str: str) -> float:
                        if not t_str: return 0.0
                        t_str = t_str.replace(' ', '\u00a0')
                        if hasattr(metrics, "horizontalAdvance"):
                            return float(metrics.horizontalAdvance(t_str))
                        return float(metrics.width(t_str))

                    words_clean = [w for w in words if w.strip()]
                    
                    if words_clean:
                        w_wished = []
                        current_idx = 0
                        for w in words_clean:
                            idx = text.find(w, current_idx)
                            if idx == -1:
                                idx = current_idx
                            left_text = text[:idx]
                            w_left = t_width(left_text)
                            w_word = t_width(w)
                            w_wished.append((w_left, w_left + w_word))
                            current_idx = idx + len(w)

                        total_measured_w = t_width(text)
                        scale = lw / total_measured_w if total_measured_w > 0 else 1.0
                        w_wished_scaled = [(left * scale, right * scale) for left, right in w_wished]

                        use_image_analysis = False
                        segments = []
                        if qimage is not None and not qimage.isNull():
                            img_x1 = max(0, int(box[0]))
                            img_y1 = max(0, int(box[1]))
                            img_x2 = min(qimage.width(), int(box[2]))
                            img_y2 = min(qimage.height(), int(box[3]))
                            
                            img_w = img_x2 - img_x1
                            img_h = img_y2 - img_y1
                            
                            if img_w > 5 and img_h > 2:
                                diff_list = []
                                for x in range(img_x1, img_x2):
                                    min_g = 255.0
                                    max_g = 0.0
                                    for y in range(img_y1, img_y2):
                                        pixel = qimage.pixel(x, y)
                                        r = (pixel >> 16) & 0xff
                                        g = (pixel >> 8) & 0xff
                                        b = pixel & 0xff
                                        gray = 0.299 * r + 0.587 * g + 0.114 * b
                                        if gray < min_g:
                                            min_g = gray
                                        if gray > max_g:
                                            max_g = gray
                                    diff_list.append(max_g - min_g)
                                
                                max_diff = max(diff_list) if diff_list else 0
                                if max_diff >= 12:
                                    use_image_analysis = True
                                    threshold = max(6, min(22, max_diff * 0.22))
                                    cols = [1 if d > threshold else 0 for d in diff_list]
                                    
                                    min_space_width = max(2, int(img_h * 0.12))
                                    spaces = []
                                    i = 0
                                    n_cols = len(cols)
                                    while i < n_cols:
                                        if cols[i] == 0:
                                            start = i
                                            while i < n_cols and cols[i] == 0:
                                                i += 1
                                            end = i
                                            if (end - start) >= min_space_width:
                                                spaces.append((start, end))
                                        else:
                                            i += 1
                                    
                                    curr_start = 0
                                    for sp_start, sp_end in spaces:
                                        if sp_start > curr_start:
                                            s_left = curr_start
                                            while s_left < sp_start and cols[s_left] == 0:
                                                s_left += 1
                                            s_right = sp_start
                                            while s_right > s_left and cols[s_right - 1] == 0:
                                                s_right -= 1
                                            if s_right > s_left:
                                                segments.append((s_left, s_right))
                                        curr_start = sp_end
                                    if curr_start < n_cols:
                                        s_left = curr_start
                                        while s_left < n_cols and cols[s_left] == 0:
                                            s_left += 1
                                        s_right = n_cols
                                        while s_right > s_left and cols[s_right - 1] == 0:
                                            s_right -= 1
                                        if s_right > s_left:
                                            segments.append((s_left, s_right))

                        final_boxes = []
                        N = len(words_clean)
                        M = len(segments)
                        
                        if use_image_analysis and M > 0:
                            word_segments = {idx: [] for idx in range(N)}
                            for seg in segments:
                                seg_center = (seg[0] + seg[1]) / 2.0
                                best_idx = 0
                                min_dist = float('inf')
                                for idx in range(N):
                                    wish_center = (w_wished_scaled[idx][0] + w_wished_scaled[idx][1]) / 2.0
                                    dist = abs(seg_center - wish_center)
                                    if dist < min_dist:
                                        min_dist = dist
                                        best_idx = idx
                                word_segments[best_idx].append(seg)
                            
                            for idx in range(N):
                                segs = word_segments[idx]
                                L_idx, R_idx = w_wished_scaled[idx]
                                if not segs:
                                    final_boxes.append((L_idx, R_idx))
                                else:
                                    s_min = min(s for s, e in segs)
                                    e_max = max(e for s, e in segs)
                                    final_boxes.append((s_min, e_max))
                            
                            for idx in range(N - 1):
                                box1 = final_boxes[idx]
                                box2 = final_boxes[idx+1]
                                if box1[1] > box2[0]:
                                    x_split = w_wished_scaled[idx][1]
                                    if x_split < box2[0]:
                                        x_split = box2[0]
                                    elif x_split > box1[1]:
                                        x_split = box1[1]
                                    final_boxes[idx] = (box1[0], x_split)
                                    final_boxes[idx+1] = (x_split, box2[1])
                        else:
                            final_boxes = w_wished_scaled

                        line_words = []
                        line_w_reals = []
                        for idx, w in enumerate(words_clean):
                            b_left, b_right = final_boxes[idx]
                            w_real = b_right - b_left
                            if len(w) >= 3 and w_real > 1.0:
                                line_words.append(w.replace(' ', '\u00a0'))
                                line_w_reals.append(w_real)
                                
                            chars.append({
                                "x": lx + b_left,
                                "y": ly,
                                "w": w_real,
                                "h": lh
                            })
                        
                        lines[-1]["words"] = line_words
                        lines[-1]["w_reals"] = line_w_reals
                        lines[-1]["lh"] = lh

                else:
                    # Chữ dọc: chia đều theo số lượng ký tự (do chữ dọc 100% là chữ CJK vuông đều nhau)
                    lines[-1]["font"] = "Microsoft YaHei"
                    lines[-1]["is_vertical"] = True
                    current_char_idx = 0
                    for w in words:
                        w_len = len(w)
                        if w.strip():
                            cy = ly + (current_char_idx / total_chars) * lh
                            ch = (w_len / total_chars) * lh
                            chars.append({
                                "x": lx,
                                "y": cy,
                                "w": lw,
                                "h": ch
                            })
                        current_char_idx += w_len

    # 3. Thuật toán nhận diện phông chữ theo Đoạn văn (Paragraph-level Fingerprinting)
    if globalconfig.get("debugocr_detect_font", False):
        FONTS_TO_TRY = ["Arial", "Segoe UI", "Calibri", "Microsoft YaHei", "Times New Roman", "Courier New", "Tahoma", "Verdana", "Georgia", "Consolas"]
        
        # 3.2. Nhận diện font cho từng đoạn văn
        for p in paragraphs:
            px_min, px_max = p["x"], p["x"] + p["w"]
            py_min, py_max = p["y"], p["y"] + p["h"]
            
            # Tìm các dòng thuộc đoạn văn này
            para_lines = []
            for l in lines:
                if l.get("is_vertical"):
                    continue
                # Tâm của dòng nằm trong box của đoạn
                cx = l["x"] + l["w"] / 2
                cy = l["y"] + l["h"] / 2
                if px_min <= cx <= px_max and py_min <= cy <= py_max:
                    para_lines.append(l)
                    
            if not para_lines:
                p["font"] = "Arial"
                continue
                
            # Gộp tất cả các từ và reals của các dòng trong đoạn
            all_words = []
            all_w_reals = []
            for l in para_lines:
                all_words.extend(l.get("words", []))
                all_w_reals.extend(l.get("w_reals", []))
                
            if not all_words:
                para_font = "Arial"
                p["font"] = para_font
                for l in para_lines:
                    l["font"] = para_font
                continue
                
            # Sắp xếp và chọn tối đa 4 từ dài nhất để tối ưu tốc độ và giữ đặc trưng tốt nhất
            if len(all_words) > 4:
                sorted_pairs = sorted(zip(all_words, all_w_reals), key=lambda x: len(x[0]), reverse=True)
                valid_words = [p[0] for p in sorted_pairs[:4]]
                w_reals = [p[1] for p in sorted_pairs[:4]]
            else:
                valid_words = all_words
                w_reals = all_w_reals
                
            if len(valid_words) >= 2:
                # Tính estimated_size (size ước lượng trung bình của đoạn văn)
                lh_avg = sum(l["lh"] for l in para_lines) / len(para_lines)
                estimated_size = max(8, int(lh_avg * 0.75))
                
                PRIOR_WEIGHTS = {
                    "Times New Roman": 0.8,
                    "Arial": 0.8,
                    "Segoe UI": 0.8,
                    "Calibri": 0.85,
                    "Tahoma": 0.85,
                    "Verdana": 0.85,
                    "Courier New": 0.8,
                    "Consolas": 0.8,
                    "Georgia": 0.85,
                    "Microsoft YaHei": 0.85
                }
                
                # Khởi tạo cache động QFontMetrics nếu chưa có
                if not hasattr(dispatch_debug_ocr, "_qfont_cache"):
                    dispatch_debug_ocr._qfont_cache = {}
                qfont_cache = dispatch_debug_ocr._qfont_cache
                
                # A. Đo và so khớp trực tiếp ở estimated_size để lấy hiệu ứng hinting làm tròn pixel thực tế
                font_rough_errs = []
                for f_name in FONTS_TO_TRY:
                    key = (f_name, estimated_size)
                    if key not in qfont_cache:
                        f_obj = QFont(f_name)
                        f_obj.setPixelSize(estimated_size)
                        qfont_cache[key] = QFontMetrics(f_obj)
                    f_metrics = qfont_cache[key]
                    
                    err_sum = 0
                    for w, r_val in zip(valid_words, w_reals):
                        if hasattr(f_metrics, "horizontalAdvance"):
                            w_meas = float(f_metrics.horizontalAdvance(w))
                        else:
                            w_meas = float(f_metrics.width(w))
                        err_sum += (r_val - w_meas) ** 2
                        
                    # Áp dụng trọng số ưu tiên
                    weight = PRIOR_WEIGHTS.get(f_name, 1.0)
                    weighted_err = err_sum * weight
                    font_rough_errs.append((weighted_err, f_name))
                    
                # Chọn top 5 font tốt nhất từ bước lọc thô
                font_rough_errs.sort(key=lambda x: x[0])
                top_fonts = [name for err, name in font_rough_errs[:5]]
                
                # B. Quét Grid-fitting chi tiết trên Top 5 font trong dải size hẹp [estimated_size - 4, estimated_size + 4]
                size_range = range(max(6, estimated_size - 4), estimated_size + 5)
                best_font = "Arial"
                min_err = float('inf')
                
                for f_name in top_fonts:
                    for sz in size_range:
                        key = (f_name, sz)
                        if key not in qfont_cache:
                            f_obj = QFont(f_name)
                            f_obj.setPixelSize(sz)
                            qfont_cache[key] = QFontMetrics(f_obj)
                        f_metrics = qfont_cache[key]
                        
                        err_sum = 0
                        for w, r_val in zip(valid_words, w_reals):
                            if hasattr(f_metrics, "horizontalAdvance"):
                                w_meas = float(f_metrics.horizontalAdvance(w))
                            else:
                                w_meas = float(f_metrics.width(w))
                            err_sum += abs(r_val - w_meas)
                            
                        # Áp dụng Prior Weight trong bước tinh chỉnh
                        weight = PRIOR_WEIGHTS.get(f_name, 1.0)
                        weighted_err = err_sum * weight
                        
                        if weighted_err < min_err:
                            min_err = weighted_err
                            best_font = f_name
                para_font = best_font
            else:
                # Nếu chỉ có 1 từ, quét đơn giản trên toàn bộ font thông dụng ở assumed_size
                w = valid_words[0]
                r_val = w_reals[0]
                lh_avg = sum(l["lh"] for l in para_lines) / len(para_lines)
                assumed_size = max(8, int(lh_avg * 0.75))
                
                # Khởi tạo cache động QFontMetrics nếu chưa có
                if not hasattr(dispatch_debug_ocr, "_qfont_cache"):
                    dispatch_debug_ocr._qfont_cache = {}
                qfont_cache = dispatch_debug_ocr._qfont_cache
                
                PRIOR_WEIGHTS = {
                    "Times New Roman": 0.8,
                    "Arial": 0.8,
                    "Segoe UI": 0.8,
                    "Calibri": 0.85,
                    "Tahoma": 0.85,
                    "Verdana": 0.85,
                    "Courier New": 0.8,
                    "Consolas": 0.8,
                    "Georgia": 0.85,
                    "Microsoft YaHei": 0.85
                }
                
                best_font = "Arial"
                min_err = float('inf')
                
                for f_name in FONTS_TO_TRY:
                    key = (f_name, assumed_size)
                    if key not in qfont_cache:
                        f_obj = QFont(f_name)
                        f_obj.setPixelSize(assumed_size)
                        qfont_cache[key] = QFontMetrics(f_obj)
                    f_metrics = qfont_cache[key]
                    
                    if hasattr(f_metrics, "horizontalAdvance"):
                        w_meas = float(f_metrics.horizontalAdvance(w))
                    else:
                        w_meas = float(f_metrics.width(w))
                    err = abs(r_val - w_meas)
                    
                    weight = PRIOR_WEIGHTS.get(f_name, 1.0)
                    weighted_err = err * weight
                    
                    if weighted_err < min_err:
                        min_err = weighted_err
                        best_font = f_name
                para_font = best_font
                
            p["font"] = para_font
            for l in para_lines:
                l["font"] = para_font
    else:
        # Nếu không bật phát hiện font, gán mặc định "Arial" cho toàn bộ
        for p in paragraphs:
            p["font"] = "Arial"
        for l in lines:
            if not l.get("is_vertical"):
                l["font"] = "Arial"

    debug_data = {
        "debugocr": True,
        "show_para": globalconfig.get("debugocr_show_para", True),
        "show_line": globalconfig.get("debugocr_show_line", True),
        "show_word": globalconfig.get("debugocr_show_word", True),
        "show_title": globalconfig.get("debugocr_show_title", True),
        "fill_para": globalconfig.get("debugocr_fill_para", False),
        "fill_line": globalconfig.get("debugocr_fill_line", False),
        "fill_word": globalconfig.get("debugocr_fill_word", False),
        "fill_text_para": globalconfig.get("debugocr_fill_text_para", False),
        "fill_text_line": globalconfig.get("debugocr_fill_text_line", False),
        "fill_text_word": globalconfig.get("debugocr_fill_text_word", False),
        "paragraphs": paragraphs,
        "lines": lines,
        "chars": chars
    }

    gobject.base.safeinvokefunction.emit(
        functools.partial(ovl.show_overlay, json.dumps(debug_data))
    )


def ocr_run(qimage: QImage, offset=None):
    gobject.base.setimage.emit(qimage)
    if (qimage is None) or qimage.isNull() or (qimage.bits() is None):
        return OCRResultParsed()
    global _nowuseocrx, _ocrengine
    thisocrtype = _nowuseocrx
    try:
        ocr_init()
        thisocrtype: str = _ocrengine.typename
        res = _ocrengine._private_ocr(qimage, offset)
        if globalconfig.get("debugocr", False):
            dispatch_debug_ocr(res, qimage, offset)
        else:
            gobject.base.setresult.emit(res)
        return res
    except Exception as e:
        if isinstance(e, ArgsEmptyExc):
            msg = str(e)
        else:
            print_exc()
            msg = stringfyerror(e)
        return OCRResultParsed(error=msg, engine=thisocrtype)
