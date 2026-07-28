from myutils.config import globalconfig, ocrsetting, ocrerrorfix, _TR
from myutils.commonbase import commonbase
from language import Languages
from myutils.utils import qimage2binary
import re, gobject, math, time
from qtsymbols import *


def is_garbage_block(text: str, box4: tuple) -> bool:
    t = text.strip()
    if not t:
        return True
    
    if box4:
        w = box4[2] - box4[0]
        h = box4[3] - box4[1]
        
        # Lọc nhiễu quá nhỏ (nhiễu pixel)
        if w < 4 or h < 4:
            return True
            
        # Lọc ký tự đơn lẻ khổng lồ (thường là logo/icon nhận diện nhầm thành 'O', '0', 'X', v.v.)
        if len(t) == 1 and w > 60 and h > 60:
            return True
            
        # Lọc logo/icon hình vuông hoặc tròn bị nhận diện nhầm thành 1 ký tự Latin/ASCII đơn lẻ
        # (Ví dụ: logo Google 'G' hình vuông, các icon tròn...)
        if len(t) == 1 and min(w, h) > 12:
            if not any(u'\u4e00' <= c <= u'\u9fff' or u'\u3040' <= c <= u'\u30ff' or u'\uac00' <= c <= u'\ud7af' for c in t):
                aspect_ratio = w / h if h > 0 else 0
                if 0.7 <= aspect_ratio <= 1.4:
                    return True
            
    # Lọc ký hiệu rác đứng riêng lẻ (độ dài <= 2 và không chứa bất kỳ chữ cái/chữ số/chữ CJK nào)
    if len(t) <= 2:
        pattern = r'[a-zA-Z0-9\u00c0-\u024f\u1e00-\u1eff\u0300-\u036f\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]'
        if not re.search(pattern, t):
            return True
            
    return False



def _group_text_lines(boxs, texts, vertical):
    if not boxs:
        return []
    mids_idx = 1 if not vertical else 0
    other_idx = 1 - mids_idx

    mids = [((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) for box in boxs]
    ranges = [((box[0], box[2]), (box[1], box[3])) for box in boxs]

    n = len(boxs)
    passed = [False] * n
    juhe: "list[list]" = []
    for i in range(n):
        if passed[i]:
            continue
        ls = [i]
        passed[i] = True

        mi_val = mids[i][mids_idx]
        ri_min, ri_max = ranges[i][mids_idx]

        for j in range(i + 1, n):
            if passed[j]:
                continue

            mj_val = mids[j][mids_idx]
            rj_min, rj_max = ranges[j][mids_idx]

            if (
                mi_val > rj_min
                and mi_val < rj_max
                and mj_val > ri_min
                and mj_val < ri_max
            ):
                passed[j] = True
                ls.append(j)
        juhe.append(ls)

    for i in range(len(juhe)):
        juhe[i].sort(key=lambda x: mids[x][other_idx])

    juhe.sort(key=lambda x: mids[x[0]][mids_idx], reverse=vertical)

    return juhe


def _sort_text_lines(boxs, texts, vertical, space: str):
    juhe = _group_text_lines(boxs, texts, vertical)
    return [space.join([texts[idx] for idx in line]) for line in juhe]


class _OCRBlockS:
    def __init__(self, blocks: "list[OCRBlock]"):
        self.blocks = blocks

    def distance(self, blocks: "_OCRBlockS"):
        mindis = math.inf
        for block1 in self.blocks:
            for block2 in blocks.blocks:
                mindis = min(mindis, block1.distance(block2))
                if mindis == 0:
                    return 0
        return mindis

    @property
    def whmin(self):
        _ = tuple(_.whmin for _ in self.blocks)
        return sum(_) / len(_)

    def merge(self, box: "_OCRBlockS"):
        self.blocks.extend(box.blocks)

    @staticmethod
    def four_point_box_union(aabb1, aabb2):

        x_min = min(aabb1[0], aabb2[0])
        y_min = min(aabb1[1], aabb2[1])
        x_max = max(aabb1[2], aabb2[2])
        y_max = max(aabb1[3], aabb2[3])

        return [x_min, y_min, x_max, y_max]

    def asblock(self, vertical, space: str):
        texts = _sort_text_lines(
            list(_.box4 for _ in self.blocks),
            list(_.text for _ in self.blocks),
            vertical,
            space,
        )
        box0 = self.blocks[0].box4
        for i in range(1, len(self.blocks)):
            box0 = self.four_point_box_union(box0, self.blocks[i].box4)
        return OCRBlock(text=space.join(texts), box=box0)


class OCRBlock:
    def __init__(self, text: str, box: list = None):

        if box and (len(box) == 4):
            x1, y1, x2, y2 = box
            box = (x1, y1, x2, y1, x2, y2, x1, y2)

        self.box = box
        self.text = text

    @property
    def box4(self):
        box = self.box
        if not box:
            return
        if len(box) == 4:
            return box
        x_coords = box[0::2]
        y_coords = box[1::2]
        return (min(x_coords), min(y_coords), max(x_coords), max(y_coords))

    @property
    def json(self):
        _ = dict(text=self.text)
        if self.box:
            x1, y1, x2, y1, x2, y2, x1, y2 = self.box
            box = [
                dict(x=x1, y=y1),
                dict(x=x2, y=y1),
                dict(x=x2, y=y2),
                dict(x=x1, y=y2),
            ]
            _.update(box=box)
        return _

    @property
    def whmin(self):
        box = self.box4
        return min(box[2] - box[0], box[3] - box[1])

    def distance(self, box2: "OCRBlock"):
        x1_min, y1_min, x1_max, y1_max = self.box4
        x2_min, y2_min, x2_max, y2_max = box2.box4

        # 检查是否有重叠
        if (
            x1_min <= x2_max
            and x1_max >= x2_min
            and y1_min <= y2_max
            and y1_max >= y2_min
        ):
            return 0  # 有重叠，距离为0

        # 计算水平方向距离
        if x1_max < x2_min:  # box1在box2左边
            dx = x2_min - x1_max
        elif x2_max < x1_min:  # box1在box2右边
            dx = x1_min - x2_max
        else:  # x轴有重叠
            dx = 0

        # 计算垂直方向距离
        if y1_max < y2_min:  # box1在box2下面
            dy = y2_min - y1_max
        elif y2_max < y1_min:  # box1在box2上面
            dy = y1_min - y2_max
        else:  # y轴有重叠
            dy = 0

        # 计算欧几里得距离
        distance = math.sqrt(dx**2 + dy**2)
        return distance


class OCRResult:
    @property
    def texts(self):
        return (_.text for _ in self.blocks)

    def __init__(
        self, texts: "str|list[str]" = None, boxs: list = None, isocrtranslate=False
    ):
        if isinstance(texts, str):
            texts = [texts]
        elif isinstance(texts, (tuple, list)):
            pass
        else:
            texts = []
        self.hasboxs = bool(boxs)
        self.blocks: "list[OCRBlock]" = []
        for i in range(len(texts)):
            txt = texts[i]
            bx = boxs[i] if boxs else None
            temp_block = OCRBlock(txt, bx)
            if not is_garbage_block(temp_block.text, temp_block.box4):
                self.blocks.append(temp_block)
        self.isocrtranslate = isocrtranslate

        vertical = int(globalconfig.get("verticalocr", 2))
        if self.hasboxs:
            if vertical == 2:
                vertical = self.__guessvertial(self.blocks)
            else:
                vertical = vertical != 0
        self.vertical = bool(vertical)

    def parse(self, space, scale):
        if not self:
            return
        if self.blocks and scale != 1:
            for block in self.blocks:
                block.box = tuple(_ / scale for _ in block.box)
        self.raw_lines = []
        for block in self.blocks:
            if block.box4:
                self.raw_lines.append((block.box4, block.text))
        if globalconfig.get("ocrmergelines", True) and self.hasboxs:
            self.__nearmergeboxs(space)

    def __bool__(self):
        return bool(self.blocks)

    @property
    def json(self):
        _ = dict(results=tuple(_.json for _ in self.blocks))
        if self.isocrtranslate:
            _.update(isocrtranslate=self.isocrtranslate)

        if self.vertical:
            _.update(vertical=self.vertical)
        return _

    def __guessvertial(self, res: "list[OCRBlock]"):
        whs = 1
        for _ in res:
            x1, y1, x2, y2 = _.box4
            w = x2 - x1
            h = y2 - y1
            if h == 0 or w == 0:
                continue
            whs *= w / h
        return whs < 1

    def __nearmergeboxs(self, space: str):
        blocksX = list(_OCRBlockS([_]) for _ in self.blocks)
        ocrmergelines_distance = globalconfig.get("ocrmergelines_distance", 0.4)
        n = len(blocksX)

        dist_matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = blocksX[i].distance(blocksX[j])
                dist_matrix[i][j] = dist_matrix[j][i] = d

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
                threshold = ocrmergelines_distance * min(box1.whmin, box2.whmin)

                if dist_matrix[i][j] <= threshold:
                    box1.merge(blocksX.pop(j))

                    dist_matrix.pop(j)
                    for row in dist_matrix:
                        row.pop(j)

                    n_curr = len(blocksX)
                    for k in range(n_curr):
                        if i != k:
                            d = box1.distance(blocksX[k])
                            dist_matrix[i][k] = dist_matrix[k][i] = d

                    if j < i:
                        i -= 1
                        box1 = blocksX[i]
                    j = 0
                    merged_happened = True
                else:
                    j += 1

            if not merged_happened:
                i += 1

        self.blocks.clear()
        for _ in blocksX:
            self.blocks.append(_.asblock(self.vertical, space))


class OCRResultParsed:
    @property
    def json(self):
        info = {}
        if self.engine:
            info.update(
                engine=dict(
                    id=self.engine, name=_TR(globalconfig["ocr"][self.engine]["name"])
                )
            )
        if self.error:
            info.update(error=self.error)
            return info
        info.update(self.result.json)
        to = self.textonly
        if to:
            info.update(text=to)
        if self.timecost:
            info.update(timecost=self.timecost)
        return info

    def errorstring(self):
        return (
            (_TR(globalconfig["ocr"][self.engine]["name"]) + " ") if self.engine else ""
        ) + self.error

    def displayerror(self):
        gobject.base.displayinfomessage(self.errorstring(), "<msg_error_Origin>")

    def maybeerror(self):
        if self.result.isocrtranslate:
            gobject.base.displayinfomessage(self.textonly, "<notrans>")
        elif self.error:
            gobject.base.displayinfomessage(self.errorstring(), "<msg_error_Origin>")
        else:
            return self.textonly

    @property
    def space(self):
        return self.srclang_1.space

    ############################################################

    def _100_f(self, line):
        if ocrerrorfix["use"] == False:
            return line
        filters: "list[str]" = ocrerrorfix["args"]["替换内容"]
        for fil in filters:
            if fil == "":
                continue
            else:
                if fil.isascii():
                    line = re.sub(r"\b{}\b".format(re.escape(fil)), lambda m: filters[fil], line)
                else:
                    line = line.replace(fil, filters[fil])
        return line

    def __bool__(self):
        return bool(self.result)

    def __init__(
        self,
        result: "str | list[str] | OCRResult" = None,
        srclang_1: Languages = None,
        error=None,
        engine=None,
        scale=1,
        timecost=None,
        offset=(0, 0),
    ):
        self.offset = offset
        self.timecost = timecost
        self.engine = engine
        self.error = error
        if not isinstance(result, OCRResult):
            result = OCRResult(result)
        self.srclang_1 = srclang_1
        self.result = result
        if result:
            result.parse(self.space, scale)

    @property
    def textonly(self):
        cached = getattr(self, "_textonly_cache", None)
        if cached is not None:
            return cached
        if not self.result:
            return ""
        if not self.result.hasboxs:
            textonly = "\n".join((_.text for _ in self.result.blocks))
        else:
            # Use the boxes captured before OCRResult.__nearmergeboxs().  The
            # generic near-merge is useful for plain text, but it is destructive
            # for an overlay: once a large title and a smaller body are joined,
            # their individual geometry and typography cannot be recovered.
            raw_lines = getattr(self.result, "raw_lines", None) or [
                (_.box4, _.text) for _ in self.result.blocks if _.box4
            ]
            raw_atoms = [
                {
                    "x": box[0] + self.offset[0],
                    "y": box[1] + self.offset[1],
                    "width": box[2] - box[0],
                    "height": box[3] - box[1],
                    "text": text,
                }
                for box, text in raw_lines
            ]
            ovl_module = None
            try:
                import ovl as ovl_module
            except ImportError:
                try:
                    from LunaTranslator import ovl as ovl_module
                except Exception:
                    ovl_module = None

            try:
                from overlay_layout import build_ocr_layout

                if ovl_module is not None:
                    source_image = getattr(self, "overlay_source_image", None)
                    split_atoms = ovl_module.split_multiline_source_atoms(
                        raw_atoms,
                        source_image=source_image,
                        source_offset=self.offset,
                    )
                    split_atoms = ovl_module.split_horizontal_source_atoms(
                        split_atoms,
                        source_image=source_image,
                        source_offset=self.offset,
                    )
                    styled_atoms = ovl_module.analyze_source_atoms(
                        split_atoms,
                        source_image=source_image,
                        source_offset=self.offset,
                    )
                else:
                    styled_atoms = raw_atoms
                self._line_boxes = styled_atoms

                layout_blocks = build_ocr_layout(
                    styled_atoms,
                    vertical=self.result.vertical,
                    separator=self.space,
                )
                pending_blocks = [block.as_pending_dict() for block in layout_blocks]
            except Exception:
                # Overlay layout is an enhancement; OCR text must still work if
                # malformed coordinates from an engine cannot be segmented.
                pending_blocks = [
                    {
                        "x": x,
                        "y": y,
                        "width": w,
                        "height": h,
                        "text": text,
                        "source_id": index,
                        "lines": [
                            {
                                "x": x,
                                "y": y,
                                "width": w,
                                "height": h,
                                "text": text,
                            }
                        ],
                    }
                    for index, atom in enumerate(raw_atoms, 1)
                    for x, y, w, h, text in [
                        (
                            atom["x"],
                            atom["y"],
                            atom["width"],
                            atom["height"],
                            atom["text"],
                        )
                    ]
                ]

            # Only emit [#id] after successful registration. Fake sequential ids
            # that are not in _PENDING_BY_MARKER make the overlay silently vanish.
            marker_ids = None
            try:
                if ovl_module is None:
                    raise ImportError()
                marker_ids = ovl_module.set_pending_boxes(
                    pending_blocks,
                    source_image=getattr(self, "overlay_source_image", None),
                    source_offset=self.offset,
                )
            except ImportError:
                try:
                    from LunaTranslator import ovl

                    marker_ids = ovl.set_pending_boxes(
                        pending_blocks,
                        source_image=getattr(self, "overlay_source_image", None),
                        source_offset=self.offset,
                    )
                except Exception:
                    try:
                        from traceback import print_exc

                        print_exc()
                    except Exception:
                        pass
            except Exception:
                try:
                    from traceback import print_exc

                    print_exc()
                except Exception:
                    pass

            if self.result.isocrtranslate:
                lines = [
                    "[{x:.0f} {y:.0f}|{width:.0f} {height:.0f}] {text}".format(
                        **block
                    )
                    for block in pending_blocks
                    if block.get("text", "").strip()
                ]
            elif marker_ids is not None and len(marker_ids) == len(pending_blocks):
                for block, marker_id in zip(pending_blocks, marker_ids):
                    block["marker_id"] = marker_id
                lines = [
                    "[#{marker_id}] {text}".format(**block)
                    for block in pending_blocks
                    if block.get("text", "").strip()
                    and block.get("role") not in ("metadata", "protected")
                ]
            else:
                # Registration failed: plain text so translators still work;
                # overlay mapping will fall back without bogus markers.
                lines = [
                    str(block.get("text", ""))
                    for block in pending_blocks
                    if block.get("text", "").strip()
                    and block.get("role") not in ("metadata", "protected")
                ]
            textonly = "\n".join(lines)
        if self.result.isocrtranslate:
            self._textonly_cache = textonly
        else:
            self._textonly_cache = self._100_f(textonly)
        return self._textonly_cache


class baseocr(commonbase):
    def langmap(self):
        return {}

    def init(self):
        pass

    def ocr(self, imagebinary) -> "str | list[str] | OCRResult":
        raise Exception()

    required_image_format = "PNG"
    required_mini_height = 0
    required_mini_width = 0

    ############################################################

    _globalconfig_key = "ocr"
    _setting_dict = ocrsetting

    def flatten4point(self, boxs):
        return [
            [
                box[0][0],
                box[0][1],
                box[1][0],
                box[1][1],
                box[2][0],
                box[2][1],
                box[3][0],
                box[3][1],
            ]
            for box in boxs
        ]

    ########################################################
    def raise_cant_be_auto_lang(self):
        if self.is_src_auto:
            raise Exception(_TR("当前OCR引擎不支持设置语言为自动"))

    def __init__(self, typename):
        super().__init__(typename)
        self.level2init()

    def level2init(self):
        self.needinit = True
        try:
            self.init()
        except Exception as e:
            raise e
        self.needinit = False

    def _private_ocr(self, qimage: QImage, offset=None):
        if self.needinit:
            self.level2init()
        try:
            scale = 1
            if (
                qimage.height() < self.required_mini_height
                or qimage.width() < self.required_mini_width
            ):
                scaleH = self.required_mini_height / qimage.height()
                scaleW = self.required_mini_width / qimage.width()
                if scaleW > scaleH:
                    qimage = qimage.scaledToWidth(
                        self.required_mini_width,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    scale = scaleW
                else:
                    scale = scaleH
                    qimage = qimage.scaledToHeight(
                        self.required_mini_height,
                        Qt.TransformationMode.SmoothTransformation,
                    )
            required_image_format: str = self.required_image_format
            if required_image_format == QImage:
                image = qimage
            else:
                image = qimage2binary(qimage, required_image_format)
            if not image:
                return OCRResultParsed()
            t = time.time()
            self.offset = offset or (0, 0)
            result = self.multiapikeywrapper(self.ocr)(image)
            return OCRResultParsed(
                result,
                srclang_1=self.srclang_1,
                engine=self.typename,
                scale=scale,
                timecost=time.time() - t,
                offset=self.offset,
            )
        except Exception as e:
            self.needinit = True
            raise e
