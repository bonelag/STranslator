import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src" / "LunaTranslator"))

_ORIGINAL_CWD = os.getcwd()
try:
    os.chdir(PROJECT_ROOT / "src")
    from qtsymbols import QApplication, QColor, QFont, QFontMetrics, QImage, QPainter

    _APP = QApplication.instance() or QApplication([])
    import ovl
except Exception as exc:  # pragma: no cover - optional on non-Qt dev machines
    ovl = None
    QT_IMPORT_ERROR = exc
else:
    QT_IMPORT_ERROR = None
finally:
    os.chdir(_ORIGINAL_CWD)


@unittest.skipIf(ovl is None, f"Qt overlay unavailable: {QT_IMPORT_ERROR}")
class OverlayMappingTests(unittest.TestCase):
    def setUp(self):
        self.old_family = ovl.CONFIG.get("auto_font_family")
        self.old_size = ovl.CONFIG.get("adaptive_font_size")
        ovl.CONFIG["auto_font_family"] = 0
        ovl.CONFIG["adaptive_font_size"] = 0

    def tearDown(self):
        ovl.CONFIG["auto_font_family"] = self.old_family
        ovl.CONFIG["adaptive_font_size"] = self.old_size

    def register_two_blocks(self, x=10):
        return ovl.set_pending_boxes(
            [
                (x, 10, 100, 25, "title"),
                (x, 55, 300, 90, "body"),
            ]
        )

    def test_one_merged_ocr_rectangle_is_split_back_into_menu_rows(self):
        # Paragraph-level engines return the whole sidebar as one rectangle.
        # Solid bars stand in for glyph rows: this environment's offscreen Qt
        # rasterises no text, but fillRect gives the scanner the same ink.
        from overlay_layout import build_ocr_layout
        from test_overlay_layout import measured_ink

        labels = [
            ("General", 106), ("Account", 112), ("Privacy", 100),
            ("Billing", 90), ("Usage", 90), ("Capabilities", 160),
            ("Reflect", 90), ("Time and focus", 165), ("Claude Code", 145),
            ("Cowork", 100), ("Claude in Ch...", 160),
        ]
        x, y0, pitch, ink = 118, 225, 66, 24

        image = QImage(420, 960, QImage.Format.Format_RGB32)
        image.fill(QColor(38, 38, 36))
        painter = QPainter(image)
        for index, (text, width) in enumerate(labels):
            # Bar height follows the zones the label's letters occupy, the way
            # a scan of real glyphs would.
            bar = measured_ink(text, ink)
            painter.fillRect(
                x, y0 + index * pitch + 6, width, bar, QColor(236, 236, 233)
            )
        painter.end()

        merged = {
            "x": x,
            "y": y0,
            "width": 190,
            "height": (len(labels) - 1) * pitch + 30,
            "text": " ".join(text for text, _width in labels),
        }

        atoms = ovl.split_multiline_source_atoms(
            [merged], source_image=image, source_offset=(0, 0)
        )
        self.assertEqual(len(labels), len(atoms))

        blocks = build_ocr_layout(
            ovl.analyze_source_atoms(atoms, source_image=image, source_offset=(0, 0))
        )
        self.assertEqual(len(labels), len(blocks))
        self.assertTrue(all(block.list_item for block in blocks))
        self.assertEqual({"body"}, {block.role for block in blocks})

    def test_menu_rows_stay_one_paint_box_each(self):
        # Whole path: OCR atoms -> layout blocks -> markers -> paint boxes.
        # Regression: the sidebar used to arrive as one 700px-tall paragraph.
        from overlay_layout import build_ocr_layout
        from test_overlay_layout import measured_ink

        labels = [
            ("General", 106), ("Account", 112), ("Privacy", 100),
            ("Billing", 90), ("Usage", 90), ("Capabilities", 160),
            ("Reflect", 90), ("Time and focus", 165), ("Claude Code", 145),
            ("Cowork", 100), ("Claude in Ch...", 160),
        ]
        atoms = [
            {
                "x": 118,
                "y": 121 + index * 66 + 13 - 23,
                "width": width,
                "height": 46,
                "text": text,
                "ink_height": measured_ink(text, 24),
                "text_rgb": (236, 236, 233),
                "bold_score": 0.10,
            }
            for index, (text, width) in enumerate(labels)
        ]
        blocks = build_ocr_layout(atoms)
        self.assertEqual(len(labels), len(blocks))

        markers = ovl.set_pending_boxes([b.as_pending_dict() for b in blocks])
        translated = "\n".join(
            f"[#{marker}] vi {block.text}"
            for marker, block in zip(markers, blocks)
        )

        painted = ovl.parse_boxes(translated, stream_id="menu")

        self.assertEqual(len(labels), len(painted))
        self.assertEqual(
            [121 + index * 66 + 13 - 23 for index in range(len(labels))],
            [box.y for box in painted],
        )
        self.assertEqual({46.0}, {float(box.height) for box in painted})
        self.assertEqual({"body"}, {box.role for box in painted})

    def test_one_line_label_shrinks_rather_than_wrapping(self):
        old_min = ovl.CONFIG["min_font_size"]
        old_adaptive = ovl.CONFIG["adaptive_font_size"]
        ovl.CONFIG["min_font_size"] = 7
        ovl.CONFIG["adaptive_font_size"] = 1
        widget = ovl.Overlay(
            [
                ovl.TextBox(
                    30, 20, 90, 30, "Mã nguồn", font_size=24,
                    source_lines=[{"x": 30, "y": 20, "width": 90, "height": 30,
                                   "text": "Code"}],
                )
            ]
        )
        try:
            label = widget.labels[0]
            # A nav tab broken across two rows reads as a layout bug; a
            # slightly smaller one does not.
            self.assertEqual(1, len(getattr(label, "_layout_lines", ["", ""])))
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG["min_font_size"] = old_min
            ovl.CONFIG["adaptive_font_size"] = old_adaptive

    def test_menu_row_takes_free_space_instead_of_shrinking(self):
        rows = [
            ovl.TextBox(
                98, 209 + index * 66, width, 28, text,
                list_item=True, layout_id=1, region_id=1, font_size=24,
            )
            for index, (text, width) in enumerate(
                [("General", 106), ("Account", 113), ("Privacy", 101)]
            )
        ]
        # A panel sits to the right of the whole column.
        panel = ovl.TextBox(
            504, 204, 420, 32, "Claude understands your codebase",
            layout_id=1, region_id=2, font_size=24,
        )

        widened = ovl._widen_list_rows(rows + [panel], 1910.0)

        for before, after in zip(rows, widened[:3]):
            self.assertGreater(after.width, before.width)
            self.assertLessEqual(after.x + after.width, panel.x)
        # Vietnamese stacks diacritics above the cap line, so the tight OCR
        # crop needs a little of the gap below it — never more than the gap.
        self.assertGreater(widened[0].height, rows[0].height)
        self.assertLessEqual(
            widened[0].y + widened[0].height, rows[1].y
        )
        self.assertEqual(panel.width, widened[3].width)

    def test_badge_background_comes_from_behind_the_glyphs(self):
        image_format = (
            QImage.Format.Format_RGB32
            if hasattr(QImage, "Format")
            else QImage.Format_RGB32
        )
        image = QImage(300, 80, image_format)
        image.fill(QColor("#0d1117"))          # page
        painter = QPainter(image)
        painter.fillRect(40, 20, 120, 32, QColor("#21262d"))   # the pill
        painter.fillRect(56, 30, 60, 12, QColor("#e6edf3"))    # its label
        painter.end()

        # The OCR box covers the pill, so the ring around it is all page.
        background = ovl.sample_background(image, ovl.TextBox(44, 24, 112, 24, ""))

        self.assertLess(
            ovl.color_distance(background, QColor("#21262d")),
            ovl.color_distance(background, QColor("#0d1117")),
        )

    def test_streaming_partial_never_unions_missing_geometry(self):
        title_marker, _ = self.register_two_blocks()
        boxes = ovl.parse_indexed_boxes(
            f"[#{title_marker}] translated title", stream_id="request-a"
        )

        # Translated title + empty cover for the untranslated body sibling.
        painted = [box for box in boxes if box.text.strip()]
        self.assertEqual(1, len(painted))
        self.assertEqual(100, painted[0].width)
        self.assertEqual(25, painted[0].height)
        self.assertTrue(any(not box.text.strip() for box in boxes))

    def test_old_translation_uses_its_own_snapshot_after_new_ocr(self):
        old_title, old_body = self.register_two_blocks(x=10)
        self.register_two_blocks(x=700)

        boxes = ovl.parse_indexed_boxes(
            f"[#{old_title}] old title\n[#{old_body}] old body",
            stream_id="old-request",
        )

        self.assertEqual([10, 10], [box.x for box in boxes])

    def test_translation_streams_do_not_mix_partial_blocks(self):
        title_marker, body_marker = self.register_two_blocks()
        first = ovl.parse_indexed_boxes(
            f"[#{title_marker}] title from A", stream_id="engine-a"
        )
        second = ovl.parse_indexed_boxes(
            f"[#{body_marker}] body from B", stream_id="engine-b"
        )

        self.assertEqual(
            ["title from A"],
            [box.text for box in first if box.text.strip()],
        )
        self.assertEqual(
            ["body from B"],
            [box.text for box in second if box.text.strip()],
        )

    def test_short_translation_never_grows_above_source_font_size(self):
        old_min = ovl.CONFIG["min_font_size"]
        old_max = ovl.CONFIG["max_font_size"]
        old_background = ovl.CONFIG["auto_background"]
        old_text = ovl.CONFIG["auto_text_color"]
        old_weight = ovl.CONFIG["auto_font_weight"]
        ovl.CONFIG.update(
            {
                "min_font_size": 6,
                "max_font_size": 48,
                "auto_background": 0,
                "auto_text_color": 0,
                "auto_font_weight": 0,
            }
        )
        widget = ovl.Overlay(
            [ovl.TextBox(10, 10, 300, 40, "Short", font_size=18)]
        )
        try:
            self.assertLessEqual(widget.labels[0].font().pixelSize(), 18)
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG.update(
                {
                    "min_font_size": old_min,
                    "max_font_size": old_max,
                    "auto_background": old_background,
                    "auto_text_color": old_text,
                    "auto_font_weight": old_weight,
                }
            )

    def test_unfit_translation_is_skipped_instead_of_clipped(self):
        old_min = ovl.CONFIG["min_font_size"]
        old_bg = ovl.CONFIG["auto_background"]
        ovl.CONFIG["min_font_size"] = 8
        ovl.CONFIG["auto_background"] = 1
        widget = ovl.Overlay(
            [
                ovl.TextBox(
                    10,
                    10,
                    80,
                    20,
                    "A translation that cannot possibly fit this tiny rectangle",
                    font_size=16,
                    background_color="rgba(32, 33, 36, 255)",
                    source_lines=[
                        {"x": 10, "y": 10, "width": 80, "height": 20, "text": "src"}
                    ],
                )
            ]
        )
        try:
            self.assertEqual([], widget.labels)
            # Source cover masks must remain even when the text label is dropped.
            masks = [rect for rect, _ in widget.auto_bg_rects if rect is not None]
            self.assertGreaterEqual(len(masks), 1)
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG["min_font_size"] = old_min
            ovl.CONFIG["auto_background"] = old_bg

    def test_balanced_wrap_evens_line_lengths(self):
        font = QFont("Segoe UI")
        font.setPixelSize(16)
        text = (
            "Supports almost all translation engines including large "
            "language model translation offline and more tools"
        )
        metrics = QFontMetrics(font)
        # Wide enough that greedy uses a few lines; balance should match count
        # and avoid a stub last line much shorter than the others.
        greedy = ovl.wrap_text(text, font, 320)
        lines = ovl.wrap_text_balanced(text, font, 320, target_lines=len(greedy))
        self.assertEqual(len(greedy), len(lines))
        self.assertGreaterEqual(len(lines), 2)
        widths = [ovl.text_width(metrics, line) for line in lines]
        greedy_widths = [ovl.text_width(metrics, line) for line in greedy]
        self.assertLessEqual(max(widths) - min(widths), max(greedy_widths) - min(greedy_widths) + 8)
        for line in lines:
            self.assertLessEqual(ovl.text_width(metrics, line), 320)

    def test_balanced_wrap_never_adds_sparse_extra_lines(self):
        font = QFont("Segoe UI")
        font.setPixelSize(16)
        text = "Short phrase that fits on one or two lines"
        greedy = ovl.wrap_text(text, font, 400)
        # Asking for more lines than greedy must NOT create empty-looking rows.
        lines = ovl.wrap_text_balanced(text, font, 400, target_lines=len(greedy) + 3)
        self.assertEqual(len(greedy), len(lines))

    def test_wrap_does_not_start_line_with_comma(self):
        font = QFont("Segoe UI")
        font.setPixelSize(16)
        text = "Hỗ trợ hầu hết các công cụ dịch thuật , bao gồm dịch thuật mô hình ngôn ngữ lớn"
        lines = ovl.wrap_text_balanced(text, font, 220)
        for line in lines:
            self.assertFalse(
                line.lstrip().startswith(","),
                msg=f"line started with comma: {line!r}",
            )

    def test_coalesce_merges_fragmented_body_lines(self):
        boxes = [
            ovl.TextBox(
                20, 40, 120, 24, "Title", role="title",
                layout_id=1, region_id=1, font_size=18,
            ),
            ovl.TextBox(
                20, 80, 280, 20, "Hỗ trợ hầu hết các công cụ dịch",
                role="body", layout_id=1, region_id=1, font_size=14,
            ),
            ovl.TextBox(
                20, 110, 280, 20, ", bao gồm dịch mô hình ngôn ngữ lớn,",
                role="body", layout_id=1, region_id=1, font_size=14,
            ),
            ovl.TextBox(
                20, 140, 280, 20, "dịch ngoại tuyến, và nhiều hơn nữa.",
                role="body", layout_id=1, region_id=1, font_size=14,
            ),
            ovl.TextBox(
                20, 175, 10, 12, ".",
                role="body", layout_id=1, region_id=1, font_size=14,
            ),
        ]
        merged = ovl.coalesce_paint_boxes(boxes)
        bodies = [box for box in merged if box.role == "body"]
        titles = [box for box in merged if box.role == "title"]
        self.assertEqual(1, len(titles))
        self.assertEqual(1, len(bodies))
        self.assertIn("Hỗ trợ hầu hết", bodies[0].text)
        self.assertIn("bao gồm", bodies[0].text)
        self.assertFalse(bodies[0].text.lstrip().startswith(","))
        self.assertTrue(bodies[0].text.rstrip().endswith("."))
        # One dense paragraph box, not four sparse bands.
        self.assertGreater(bodies[0].height, 50)

    def test_coalesce_recovers_card_title_from_short_first_body(self):
        """HOOK / OCR style: short first line + long body → title + body."""
        boxes = [
            ovl.TextBox(
                20, 40, 80, 24, "HOOK", role="body",
                layout_id=2, region_id=1, font_size=16,
            ),
            ovl.TextBox(
                20, 80, 280, 20, "Chủ yếu sử dụng HOOK để trích xuất",
                role="body", layout_id=2, region_id=1, font_size=14,
            ),
            ovl.TextBox(
                20, 110, 280, 20, "văn bản trong game, tương thích với",
                role="body", layout_id=2, region_id=1, font_size=14,
            ),
        ]
        merged = ovl.coalesce_paint_boxes(boxes)
        titles = [box for box in merged if box.role == "title"]
        bodies = [box for box in merged if box.role == "body"]
        self.assertEqual(1, len(titles))
        self.assertEqual(1, len(bodies))
        self.assertEqual("HOOK", titles[0].text)
        self.assertTrue(titles[0].bold)
        self.assertIn("Chủ yếu", bodies[0].text)
        self.assertNotIn("HOOK Chủ yếu", bodies[0].text)
        self.assertFalse(bodies[0].bold)

    def test_chat_layout_never_invents_titles_or_blanks_messages(self):
        """Discord: keep each message body; do not peel 'Khi tôi' into a title."""
        boxes = [
            ovl.TextBox(
                150, 20, 90, 18, "MoSta", role="protected",
                layout_id=11, region_id=1,
            ),
            ovl.TextBox(
                250, 20, 50, 16, "20:14", role="metadata",
                layout_id=11, region_id=1,
            ),
            ovl.TextBox(
                150, 48, 400, 22,
                "Khi tôi dịch, nó dẫn tôi đến trình dịch bên trong ứng dụng",
                role="body", layout_id=11, region_id=1, font_size=15,
                text_color="rgba(220,221,222,255)",
            ),
            ovl.TextBox(
                150, 90, 90, 18, "roll", role="protected",
                layout_id=11, region_id=2,
            ),
            ovl.TextBox(
                250, 90, 50, 16, "18:18", role="metadata",
                layout_id=11, region_id=2,
            ),
            ovl.TextBox(
                150, 118, 420, 40,
                "I guess my question now is about Thor holding back development",
                role="body", layout_id=11, region_id=2, font_size=15,
                text_color="rgba(220,221,222,255)",
            ),
        ]
        merged = ovl.coalesce_paint_boxes(boxes)
        titles = [box for box in merged if box.role == "title"]
        bodies = [box for box in merged if box.role == "body" and box.text.strip()]
        self.assertEqual([], titles)
        self.assertEqual(2, len(bodies))
        self.assertTrue(bodies[0].text.startswith("Khi tôi"))
        self.assertIn("guess my question", bodies[1].text)
        self.assertFalse(any(box.bold for box in bodies))

    def test_chat_does_not_peel_ban_co_as_title(self):
        """'Bạn có thể chọn…' must stay one body — not title 'Bạn có'."""
        box = ovl.TextBox(
            150, 200, 500, 60,
            "Bạn có thể chọn dịch vụ dịch thuật trong phần cài đặt. "
            "Tôi nghĩ hiện nay tất cả đều dựa trên máy học.",
            role="body",
            layout_id=12,
            region_id=3,
            font_size=15,
            source_lines=[
                {"x": 150, "y": 200, "width": 480, "height": 18,
                 "text": "You can choose the translation service", "ink_height": 15},
                {"x": 150, "y": 222, "width": 460, "height": 18,
                 "text": "in settings. i think they're all based", "ink_height": 15},
                {"x": 150, "y": 244, "width": 400, "height": 18,
                 "text": "on machine learning nowadays", "ink_height": 15},
            ],
        )
        # Chat chrome present so card peel is disabled at layout level.
        chrome = [
            ovl.TextBox(150, 170, 80, 16, "user", role="protected",
                        layout_id=12, region_id=3),
            ovl.TextBox(240, 170, 40, 14, "19:49", role="metadata",
                        layout_id=12, region_id=3),
            box,
        ]
        merged = ovl.coalesce_paint_boxes(chrome)
        titles = [b for b in merged if b.role == "title"]
        bodies = [b for b in merged if b.role == "body" and b.text.strip()]
        self.assertEqual([], titles)
        self.assertEqual(1, len(bodies))
        self.assertTrue(bodies[0].text.startswith("Bạn có thể"))

    def test_chat_keeps_separate_messages_unmerged(self):
        boxes = [
            ovl.TextBox(150, 20, 60, 16, "A", role="protected",
                        layout_id=13, region_id=1),
            ovl.TextBox(220, 20, 40, 14, "17:04", role="metadata",
                        layout_id=13, region_id=1),
            ovl.TextBox(150, 48, 400, 20, "Message one line",
                        role="body", layout_id=13, region_id=1, font_size=14),
            ovl.TextBox(150, 100, 60, 16, "B", role="protected",
                        layout_id=13, region_id=2),
            ovl.TextBox(220, 100, 40, 14, "17:29", role="metadata",
                        layout_id=13, region_id=2),
            ovl.TextBox(150, 128, 400, 20, "Message two line",
                        role="body", layout_id=13, region_id=2, font_size=14),
        ]
        merged = ovl.coalesce_paint_boxes(boxes)
        bodies = [b for b in merged if b.role == "body" and b.text.strip()]
        self.assertEqual(2, len(bodies))
        self.assertEqual("Message one line", bodies[0].text)
        self.assertEqual("Message two line", bodies[1].text)
        self.assertLess(bodies[0].y + bodies[0].height, bodies[1].y)

    def test_chat_multiline_message_reflows_in_union_box(self):
        """Tightly stacked lines of one bubble reflow inside their union band."""
        boxes = [
            ovl.TextBox(150, 20, 80, 16, "user", role="protected",
                        layout_id=14, region_id=1),
            ovl.TextBox(240, 20, 40, 14, "19:49", role="metadata",
                        layout_id=14, region_id=1),
            ovl.TextBox(
                150, 48, 480, 20,
                "You can choose the translation service in settings.",
                role="body", layout_id=14, region_id=1, font_size=14,
            ),
            ovl.TextBox(
                150, 72, 480, 20,
                "It should be able to translate your entire screen.",
                role="body", layout_id=14, region_id=1, font_size=14,
            ),
            ovl.TextBox(
                150, 96, 460, 20,
                "You need to enable screen capture for it to work.",
                role="body", layout_id=14, region_id=1, font_size=14,
            ),
        ]
        merged = ovl.coalesce_paint_boxes(boxes)
        bodies = [b for b in merged if b.role == "body" and b.text.strip()]
        self.assertEqual(1, len(bodies))
        self.assertEqual(48, bodies[0].y)
        # Union of y=48..116
        self.assertGreaterEqual(bodies[0].height, 60)
        self.assertIn("choose", bodies[0].text)
        self.assertIn("entire screen", bodies[0].text)
        self.assertIn("screen capture", bodies[0].text)
        # Single bubble occupies its full OCR union band.
        self.assertAlmostEqual(bodies[0].height, 68.0, delta=1.0)

    def test_chat_height_cap_stops_at_next_message(self):
        """Long VI must not claim the next message's vertical band."""
        boxes = [
            ovl.TextBox(150, 20, 80, 16, "A", role="protected",
                        layout_id=15, region_id=1),
            ovl.TextBox(240, 20, 40, 14, "17:04", role="metadata",
                        layout_id=15, region_id=1),
            ovl.TextBox(
                150, 48, 400, 22,
                "Message one that becomes much longer when translated to Vietnamese",
                role="body", layout_id=15, region_id=1, font_size=14,
            ),
            ovl.TextBox(150, 100, 80, 16, "B", role="protected",
                        layout_id=15, region_id=2),
            ovl.TextBox(240, 100, 40, 14, "17:29", role="metadata",
                        layout_id=15, region_id=2),
            ovl.TextBox(
                150, 128, 400, 22, "Message two line",
                role="body", layout_id=15, region_id=2, font_size=14,
            ),
        ]
        merged = ovl.coalesce_paint_boxes(boxes)
        bodies = [b for b in merged if b.role == "body" and b.text.strip()]
        self.assertEqual(2, len(bodies))
        first, second = bodies
        cap = getattr(first, "_chat_max_height", first.height)
        # Cap ends just above the next username row (y=100).
        self.assertLessEqual(first.y + cap, 100)
        self.assertLess(first.y + first.height, second.y)

    def test_peel_glued_title_from_single_body_box(self):
        """'Dịch thuật nhúng' + body glued into one string must split again."""
        box = ovl.TextBox(
            20,
            40,
            300,
            120,
            "Dịch thuật nhúng Một số trò chơi cũng hỗ trợ bản dịch vào trò chơi "
            "để có trải nghiệm sống động hơn.",
            role="body",
            layout_id=9,
            region_id=4,
            font_size=14,
            source_lines=[
                {
                    "x": 20, "y": 40, "width": 160, "height": 26,
                    "text": "Dịch thuật nhúng", "ink_height": 20,
                },
                # Clear gap under title band (not paragraph wrap).
                {
                    "x": 20, "y": 80, "width": 280, "height": 20,
                    "text": "Một số trò chơi cũng hỗ trợ", "ink_height": 15,
                },
                {
                    "x": 20, "y": 112, "width": 260, "height": 20,
                    "text": "bản dịch vào trò chơi để", "ink_height": 15,
                },
            ],
        )
        # Need a second region so classifier treats this as a feature card,
        # not a lone paragraph.
        sibling = ovl.TextBox(
            400, 40, 80, 24, "HOOK", role="title",
            layout_id=9, region_id=5, font_size=18,
            text_color="rgba(245,245,247,255)",
        )
        sibling_body = ovl.TextBox(
            400, 80, 280, 60, "Body of other card",
            role="body", layout_id=9, region_id=5, font_size=14,
        )
        merged = ovl.coalesce_paint_boxes([box, sibling, sibling_body])
        titles = [b for b in merged if b.role == "title"]
        bodies = [b for b in merged if b.role == "body"]
        # HOOK sibling title + peeled "Dịch thuật nhúng".
        self.assertEqual(2, len(titles))
        self.assertEqual(2, len(bodies))
        glued_titles = [t for t in titles if t.text == "Dịch thuật nhúng"]
        glued_bodies = [b for b in bodies if "Một số" in b.text]
        self.assertEqual(1, len(glued_titles))
        self.assertEqual(1, len(glued_bodies))
        self.assertTrue(glued_titles[0].bold)
        self.assertIsNotNone(glued_titles[0].text_color)
        self.assertGreaterEqual(
            ovl._color_luminance(glued_titles[0].text_color), 180
        )
        self.assertTrue(glued_bodies[0].text.startswith("Một số"))
        self.assertNotIn("Dịch thuật nhúng Một", glued_bodies[0].text)
        self.assertFalse(glued_bodies[0].bold)

    def test_mid_body_short_lines_never_become_fake_titles(self):
        """Regression: 'nhúng trực tiếp' mid-card was bold/white as a title."""
        boxes = [
            ovl.TextBox(
                20, 30, 180, 24, "Dịch thuật nhúng", role="title",
                layout_id=3, region_id=2, font_size=18, bold=True,
                text_color="rgba(245,245,245,255)",
            ),
            ovl.TextBox(
                20, 70, 280, 18, "Một số trò chơi cũng hỗ trợ",
                role="body", layout_id=3, region_id=2, font_size=14,
                text_color="rgba(170,172,178,255)",
            ),
            ovl.TextBox(
                20, 110, 200, 18, "nhúng trực tiếp",
                role="title", layout_id=3, region_id=2, font_size=16,
                bold=True, text_color="rgba(250,250,250,255)",
            ),
            ovl.TextBox(
                20, 145, 220, 18, "bản dịch vào trò chơi để",
                role="title", layout_id=3, region_id=2, font_size=16,
                bold=True, text_color="rgba(250,250,250,255)",
            ),
            ovl.TextBox(
                20, 180, 260, 18, "mang lại trải nghiệm sống động hơn.",
                role="body", layout_id=3, region_id=2, font_size=14,
                text_color="rgba(170,172,178,255)",
            ),
        ]
        merged = ovl.coalesce_paint_boxes(boxes)
        titles = [box for box in merged if box.role == "title"]
        bodies = [box for box in merged if box.role == "body"]
        self.assertEqual(1, len(titles))
        self.assertEqual("Dịch thuật nhúng", titles[0].text)
        self.assertEqual(1, len(bodies))
        self.assertIn("nhúng trực tiếp", bodies[0].text)
        self.assertIn("bản dịch vào trò chơi", bodies[0].text)
        self.assertIn("sống động", bodies[0].text)
        self.assertFalse(bodies[0].bold)

    def test_role_palette_folds_jitter_but_keeps_distinct_colors(self):
        # Sampling the same grey four times never returns the same number
        # twice; those must converge. Colours the page really does use for a
        # minority of its text must survive, and role still drives weight.
        boxes = [
            ovl.TextBox(
                20, 20, 100, 24, "HOOK", role="title",
                text_rgb=(245, 245, 245), bold=False, font_size=18,
            ),
            ovl.TextBox(
                200, 20, 160, 24, "Dịch thuật nhúng", role="title",
                text_rgb=(242, 243, 243), bold=False, font_size=16,
            ),
            ovl.TextBox(
                20, 60, 280, 60, "Body one", role="body",
                text_rgb=(170, 172, 178), bold=True, font_size=14,
            ),
            ovl.TextBox(
                200, 60, 280, 60, "Body two", role="body",
                text_rgb=(172, 174, 179), bold=True, font_size=14,
            ),
        ]
        ovl.CONFIG["auto_font_weight"] = 1
        ovl._harmonize_role_palette(boxes)
        self.assertEqual(boxes[0].text_color, boxes[1].text_color)
        self.assertEqual(boxes[2].text_color, boxes[3].text_color)
        self.assertGreater(
            ovl._color_luminance(boxes[0].text_color),
            ovl._color_luminance(boxes[2].text_color),
        )
        self.assertTrue(boxes[0].bold and boxes[1].bold)
        self.assertFalse(boxes[2].bold or boxes[3].bold)

    def test_majority_white_body_is_not_repainted_in_a_minority_color(self):
        # A page of white text with a few coloured links used to be collapsed
        # onto whichever colour sat at the median luminance — so every line
        # came out cyan. Each colour keeps the pixels it was measured from.
        white = [
            ovl.TextBox(
                20, 40 + index * 30, 260, 22,
                f"Dòng văn bản trắng số {index}", role="body",
                text_rgb=(240 + index % 4, 240, 241), font_size=15,
            )
            for index in range(8)
        ]
        accents = [
            ovl.TextBox(
                320, 40 + index * 30, 90, 22, "Xem", role="body",
                text_rgb=(160, 234, 235), font_size=15,
            )
            for index in range(3)
        ]
        boxes = white + accents
        ovl._harmonize_role_palette(boxes)
        for box in white:
            self.assertGreater(ovl._color_luminance(box.text_color), 200)
        self.assertEqual(1, len({box.text_color for box in white}))
        for box in accents:
            self.assertGreater(
                ovl.color_distance(
                    ovl.parse_color(box.text_color),
                    ovl.parse_color(white[0].text_color),
                ),
                60,
            )

    def test_dark_title_on_a_light_page_is_not_forced_white(self):
        box = ovl.TextBox(
            20, 20, 200, 26, "Tiêu đề", role="title",
            text_rgb=(28, 30, 34), font_size=18,
            background_color="rgba(255, 255, 255, 255)",
        )
        ovl._harmonize_role_palette([box])
        self.assertLess(ovl._color_luminance(box.text_color), 80)
        widget = ovl.Overlay([box])
        try:
            self.assertEqual(1, len(widget.labels))
            self.assertLess(
                ovl._color_luminance(widget.labels[0]._custom_text_color), 80
            )
        finally:
            widget.close()
            _APP.processEvents()

    def test_body_wrap_avoids_single_ultrawide_line(self):
        font = QFont("Segoe UI")
        font.setPixelSize(15)
        text = (
            "Hỗ trợ trình giả lập HOOK để trực tiếp trích xuất văn bản "
            "từ hầu hết các game trên NS/PSP/PSV/PS2."
        )
        # Wide flow budget but source lines were ~260px — wrap should still
        # produce multiple lines, not one stretched line.
        wrap_w = ovl._preferred_wrap_width(
            text,
            font,
            target_w=480,
            target_h=90,
            source_lines=[{"width": 260}, {"width": 250}, {"width": 240}],
            role="body",
        )
        self.assertLess(wrap_w, 400)
        lines = ovl.wrap_text_balanced(text, font, wrap_w)
        self.assertGreaterEqual(len(lines), 3)

    def test_peer_body_cards_share_one_font_size(self):
        short = ovl.TextBox(
            20, 20, 300, 100, "Short body", role="body", font_size=18,
            source_lines=[{"ink_height": 16, "height": 20}],
        )
        long = ovl.TextBox(
            340, 20, 300, 100,
            "Supports almost all translation engines, including large "
            "language model translation, offline translation, and more.",
            role="body", font_size=18,
            source_lines=[
                {"ink_height": 16, "height": 20},
                {"ink_height": 16, "height": 20},
                {"ink_height": 16, "height": 20},
            ],
        )
        widget = ovl.Overlay([short, long])
        try:
            self.assertEqual(2, len(widget.labels))
            sizes = [label.font().pixelSize() for label in widget.labels]
            self.assertEqual(1, len(set(sizes)))
            self.assertLessEqual(sizes[0], 18)
        finally:
            widget.close()
            _APP.processEvents()

    def test_long_translation_shrinks_instead_of_vanishing(self):
        """EN often longer than VI: must still paint, not drop the label."""
        old_min = ovl.CONFIG["min_font_size"]
        old_max = ovl.CONFIG["max_font_size"]
        ovl.CONFIG["min_font_size"] = 8
        ovl.CONFIG["max_font_size"] = 30
        text = (
            "Supports almost all translation engines, including large "
            "language model translation, offline translation, and more."
        )
        widget = ovl.Overlay(
            [
                ovl.TextBox(
                    20,
                    20,
                    320,
                    110,
                    text,
                    font_size=18,
                    role="body",
                    baseline_offset=4.0,
                    line_spacing=1.35,
                    source_lines=[
                        {
                            "x": 20, "y": 20, "width": 300, "height": 20,
                            "ink_height": 16, "text": "line1",
                        },
                        {
                            "x": 20, "y": 52, "width": 280, "height": 20,
                            "ink_height": 16, "text": "line2",
                        },
                        {
                            "x": 20, "y": 84, "width": 260, "height": 20,
                            "ink_height": 16, "text": "line3",
                        },
                    ],
                )
            ]
        )
        try:
            self.assertEqual(1, len(widget.labels))
            self.assertIn("translation engines", widget.labels[0].text())
            self.assertLessEqual(widget.labels[0].font().pixelSize(), 18)
            self.assertGreaterEqual(widget.labels[0].font().pixelSize(), 8)
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG["min_font_size"] = old_min
            ovl.CONFIG["max_font_size"] = old_max

    def test_layout_font_sizes_are_normalized_per_role(self):
        old_adaptive = ovl.CONFIG.get("adaptive_font_size")
        ovl.CONFIG["adaptive_font_size"] = 1
        try:
            markers = ovl.set_pending_boxes(
                [
                    {
                        "x": 10, "y": 10, "width": 100, "height": 24,
                        "text": "A", "role": "body", "font_size": 14,
                        "ink_height": 14,
                    },
                    {
                        "x": 200, "y": 10, "width": 100, "height": 24,
                        "text": "B", "role": "body", "font_size": 20,
                        "ink_height": 20,
                    },
                ]
            )
            a = ovl._PENDING_BY_MARKER[markers[0]]
            b = ovl._PENDING_BY_MARKER[markers[1]]
            self.assertEqual(a.font_size, b.font_size)
            self.assertEqual(17.0, a.font_size)
        finally:
            ovl.CONFIG["adaptive_font_size"] = old_adaptive

    def test_missing_source_size_does_not_fall_back_to_global_max(self):
        old_max = ovl.CONFIG["max_font_size"]
        ovl.CONFIG["max_font_size"] = 48
        widget = ovl.Overlay([ovl.TextBox(10, 10, 300, 100, "OK")])
        try:
            self.assertLess(widget.labels[0].font().pixelSize(), 48)
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG["max_font_size"] = old_max

    def test_multiline_paragraph_atom_is_split_by_ink_bands(self):
        image_format = (
            QImage.Format.Format_RGB32
            if hasattr(QImage, "Format")
            else QImage.Format_RGB32
        )
        image = QImage(420, 140, image_format)
        image.fill(QColor("#202124"))
        painter = QPainter(image)
        painter.fillRect(20, 22, 150, 16, QColor("#f1f3f4"))
        painter.fillRect(20, 72, 180, 16, QColor("#f1f3f4"))
        painter.end()

        atoms = ovl.split_multiline_source_atoms(
            [(15, 10, 300, 95, "First source line Second source line")],
            source_image=image,
        )
        self.assertEqual(2, len(atoms))
        self.assertLess(atoms[0]["y"], atoms[1]["y"])

    def test_low_contrast_body_still_splits_from_bright_title(self):
        image_format = (
            QImage.Format.Format_RGB32
            if hasattr(QImage, "Format")
            else QImage.Format_RGB32
        )
        image = QImage(420, 140, image_format)
        image.fill(QColor("#202124"))
        painter = QPainter(image)
        painter.fillRect(20, 15, 120, 22, QColor("#f1f3f4"))
        painter.fillRect(20, 60, 300, 14, QColor("#656a70"))
        painter.fillRect(20, 88, 260, 14, QColor("#656a70"))
        painter.end()

        atoms = ovl.split_multiline_source_atoms(
            [(10, 8, 360, 105, "HOOK body first line body second line")], image
        )
        self.assertEqual(3, len(atoms))
        self.assertEqual("HOOK", atoms[0]["text"])

    def test_diacritic_band_does_not_become_a_fake_line(self):
        image_format = (
            QImage.Format.Format_RGB32
            if hasattr(QImage, "Format")
            else QImage.Format_RGB32
        )
        image = QImage(320, 70, image_format)
        image.fill(QColor("#202124"))
        painter = QPainter(image)
        painter.fillRect(20, 20, 120, 3, QColor("#f1f3f4"))
        painter.fillRect(20, 27, 220, 13, QColor("#f1f3f4"))
        painter.end()

        atoms = ovl.split_multiline_source_atoms(
            [(10, 15, 260, 30, "Tiếng Việt overlay")], image
        )
        self.assertEqual(1, len(atoms))

    def test_same_line_discord_metadata_is_split_by_color_runs(self):
        from overlay_layout import build_ocr_layout

        image_format = (
            QImage.Format.Format_RGB32
            if hasattr(QImage, "Format")
            else QImage.Format_RGB32
        )
        image = QImage(420, 80, image_format)
        image.fill(QColor("#202124"))
        painter = QPainter(image)
        for x, width in ((20, 54), (80, 38)):
            painter.fillRect(x, 20, width, 16, QColor("#f1f3f4"))
        for x, width in ((128, 48), (182, 64)):
            painter.fillRect(x, 20, width, 16, QColor("#aeb1b8"))
        painter.end()

        raw = [(15, 16, 250, 24, "Bone Lag 20:14 8/7/26")]
        split = ovl.split_horizontal_source_atoms(raw, image)
        styled = ovl.analyze_source_atoms(split, image)
        blocks = build_ocr_layout(styled, separator=" ")

        self.assertEqual(2, len(blocks))
        self.assertEqual(["Bone Lag", "20:14 8/7/26"], [b.text for b in blocks])

    def test_mention_message_is_not_split_into_word_boxes(self):
        image_format = (
            QImage.Format.Format_RGB32
            if hasattr(QImage, "Format")
            else QImage.Format_RGB32
        )
        image = QImage(900, 70, image_format)
        image.fill(QColor("#202124"))
        painter = QPainter(image)
        painter.fillRect(20, 20, 110, 18, QColor("#b5bac1"))
        painter.fillRect(140, 20, 700, 18, QColor("#f2f3f5"))
        painter.end()
        text = (
            "@embrace21 Were you able to find any clues? I don't mind trying "
            "to delve into android development"
        )
        atoms = ovl.split_horizontal_source_atoms(
            [(15, 16, 840, 26, text)], image
        )
        self.assertEqual(1, len(atoms))
        self.assertEqual(text, ovl._as_text_box(atoms[0]).text)
        from overlay_layout import build_ocr_layout

        blocks = build_ocr_layout(ovl.analyze_source_atoms(atoms, image))
        self.assertEqual(1, len(blocks))

    def test_background_mask_uses_source_lines_not_union_gap(self):
        old_background = ovl.CONFIG["auto_background"]
        ovl.CONFIG["auto_background"] = 1
        box = ovl.TextBox(
            20,
            20,
            240,
            80,
            "translated body",
            background_color="rgba(32, 33, 36, 255)",
            font_size=14,
            source_lines=[
                {"x": 20, "y": 20, "width": 180, "height": 18, "text": "line one"},
                {"x": 20, "y": 70, "width": 220, "height": 18, "text": "line two"},
            ],
        )
        widget = ovl.Overlay([box])
        try:
            rects = [rect for rect, _ in widget.auto_bg_rects if rect is not None]
            self.assertEqual(2, len(rects))
            self.assertLess(rects[0].bottom(), rects[1].top())
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG["auto_background"] = old_background

    def test_metadata_stays_as_non_rendered_obstacle(self):
        body_marker, time_marker = ovl.set_pending_boxes(
            [
                {
                    "x": 20, "y": 50, "width": 300, "height": 22,
                    "text": "message", "role": "body",
                },
                {
                    "x": 330, "y": 50, "width": 90, "height": 22,
                    "text": "20:14", "role": "metadata",
                },
            ]
        )
        boxes = ovl.parse_indexed_boxes(
            f"[#{body_marker}] translated message", stream_id="metadata-obstacle"
        )
        self.assertEqual(["body", "metadata"], [box.role for box in boxes])
        self.assertEqual("", boxes[1].text)
        self.assertGreater(time_marker, body_marker)

    def test_font_inference_uses_ink_height_not_padded_ocr_height(self):
        sizes = []
        for height in (24, 40, 60):
            _, size = ovl._infer_font(
                [ovl.TextBox(0, 0, 160, height, "Overlay text", ink_height=18)],
                False,
            )
            sizes.append(size)
        self.assertEqual(1, len(set(sizes)))

    def test_stroke_margin_does_not_force_large_font_shrink(self):
        widget = ovl.Overlay(
            [ovl.TextBox(10, 10, 180, 20, "Short", font_size=18)]
        )
        try:
            self.assertTrue(widget.labels)
            self.assertGreaterEqual(widget.labels[0].font().pixelSize(), 17)
        finally:
            widget.close()
            _APP.processEvents()

    def test_live_background_sampling_still_masks_each_source_line(self):
        old_background = ovl.CONFIG["auto_background"]
        ovl.CONFIG["auto_background"] = 1
        image_format = (
            QImage.Format.Format_RGB32
            if hasattr(QImage, "Format")
            else QImage.Format_RGB32
        )
        screenshot = QImage(500, 200, image_format)
        screenshot.fill(QColor("#202124"))
        widget = ovl.Overlay([])
        try:
            widget._render_boxes(
                [
                    ovl.TextBox(
                        20, 20, 240, 80, "translated body", font_size=14,
                        source_lines=[
                            {"x": 20, "y": 20, "width": 180, "height": 18},
                            {"x": 20, "y": 70, "width": 220, "height": 18},
                        ],
                    )
                ],
                1.0,
                screenshot,
            )
            rects = [rect for rect, _ in widget.auto_bg_rects if rect is not None]
            self.assertEqual(2, len(rects))
            self.assertLess(rects[0].bottom(), rects[1].top())
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG["auto_background"] = old_background

    def test_source_font_metadata_is_scaled_with_dpr(self):
        widget = ovl.Overlay([])
        try:
            widget._render_boxes(
                [
                    ovl.TextBox(
                        20, 20, 360, 40, "Short", font_size=32,
                        source_lines=[
                            {
                                "x": 20, "y": 20, "width": 360, "height": 40,
                                "font_size": 32, "ink_height": 28,
                            }
                        ],
                    )
                ],
                2.0,
                None,
            )
            # Physical font_size 32 / dpr 2 = 16; ink 28/2 * 1.15 ≈ 16
            self.assertEqual(16, widget.labels[0].font().pixelSize())
        finally:
            widget.close()
            _APP.processEvents()

    def test_configured_ui_font_wins_when_auto_family_off(self):
        old_font = ovl.CONFIG["font_family"]
        old_auto_family = ovl.CONFIG["auto_font_family"]
        ovl.CONFIG["font_family"] = "Segoe UI"
        ovl.CONFIG["auto_font_family"] = 0
        widget = ovl.Overlay(
            [
                ovl.TextBox(
                    20, 20, 240, 32, "Stable font", font_family="Georgia",
                    font_size=18, source_lines=[{"ink_height": 18, "height": 22}],
                )
            ]
        )
        try:
            self.assertEqual("Segoe UI", widget.labels[0].font().family())
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG["font_family"] = old_font
            ovl.CONFIG["auto_font_family"] = old_auto_family

    def test_auto_font_family_uses_box_family_when_enabled(self):
        old_font = ovl.CONFIG["font_family"]
        old_auto_family = ovl.CONFIG["auto_font_family"]
        ovl.CONFIG["font_family"] = "Segoe UI"
        ovl.CONFIG["auto_font_family"] = 1
        widget = ovl.Overlay(
            [
                ovl.TextBox(
                    20, 20, 240, 32, "Stable font", font_family="Georgia",
                    font_size=18, source_lines=[{"ink_height": 18, "height": 22}],
                )
            ]
        )
        try:
            # Per-block family is honored only when auto_font_family is on.
            self.assertEqual("Georgia", widget.labels[0].font().family())
        finally:
            widget.close()
            _APP.processEvents()
            ovl.CONFIG["font_family"] = old_font
            ovl.CONFIG["auto_font_family"] = old_auto_family

    def test_ink_height_caps_oversized_font_guess(self):
        widget = ovl.Overlay(
            [
                ovl.TextBox(
                    20, 20, 300, 40, "Body text", font_size=30,
                    source_lines=[{"ink_height": 18, "height": 24}],
                )
            ]
        )
        try:
            # ink 18 → ~1.15× pixel size cap (em square vs cap height)
            self.assertEqual(21, widget.labels[0].font().pixelSize())
        finally:
            widget.close()
            _APP.processEvents()

    def test_missing_ink_never_uses_padded_ocr_height(self):
        widget = ovl.Overlay(
            [
                ovl.TextBox(
                    20, 20, 300, 60, "OK",
                    source_lines=[{"height": 60, "width": 300}],
                )
            ]
        )
        try:
            self.assertLessEqual(widget.labels[0].font().pixelSize(), 20)
        finally:
            widget.close()
            _APP.processEvents()

    def test_overlay_never_expands_past_source_block_width(self):
        box = ovl.TextBox(
            20, 20, 240, 60, "translated body", font_size=18,
            source_lines=[{"x": 20, "y": 20, "width": 240, "height": 20}],
        )
        widget = ovl.Overlay([box])
        try:
            self.assertLessEqual(widget.labels[0].width(), box.width + 4)
        finally:
            widget.close()
            _APP.processEvents()

    def test_card_grid_uses_gutters_as_safe_flow_bounds(self):
        boxes = []
        for row, y in enumerate((40, 400)):
            for column, x in enumerate((40, 520)):
                region = row * 2 + column + 1
                boxes.extend(
                    [
                        ovl.TextBox(
                            x, y, 150, 28, "title", role="title",
                            layout_id=7, region_id=region,
                        ),
                        ovl.TextBox(
                            x, y + 55, 320, 90, "body", role="body",
                            layout_id=7, region_id=region,
                        ),
                    ]
                )
        flow = ovl._region_flow_boxes(boxes, 1000, 800)
        first_title, first_body = flow[:2]
        self.assertGreater(first_title.width, boxes[0].width)
        self.assertLessEqual(first_title.y + first_title.height, boxes[1].y - 6)
        self.assertLessEqual(first_title.x + first_title.width, 440)
        self.assertGreater(first_body.height, boxes[1].height)

    def test_compact_discord_region_never_expands(self):
        boxes = [
            ovl.TextBox(150, 20, 100, 20, "", role="protected", layout_id=8, region_id=1),
            ovl.TextBox(260, 20, 80, 20, "", role="metadata", layout_id=8, region_id=1),
            ovl.TextBox(150, 48, 400, 22, "body A", role="body", layout_id=8, region_id=1),
            ovl.TextBox(150, 78, 100, 20, "", role="protected", layout_id=8, region_id=1),
            ovl.TextBox(260, 78, 80, 20, "", role="metadata", layout_id=8, region_id=1),
            ovl.TextBox(150, 106, 400, 22, "body B", role="body", layout_id=8, region_id=1),
        ]
        flow = ovl._region_flow_boxes(boxes, 1200, 800)
        self.assertEqual(
            [(box.width, box.height) for box in boxes],
            [(box.width, box.height) for box in flow],
        )

    def test_long_card_title_renders_inside_grid_cell(self):
        boxes = []
        for row, y in enumerate((20, 260)):
            for column, x in enumerate((20, 400)):
                region = row * 2 + column + 1
                boxes.extend(
                    [
                        ovl.TextBox(
                            x, y, 120, 28,
                            "Chuyển văn bản thành giọng nói" if region == 1 else "",
                            role="title", layout_id=9, region_id=region,
                            font_size=20,
                            source_lines=[{"x": x, "y": y, "width": 120, "height": 22, "ink_height": 18}],
                        ),
                        ovl.TextBox(
                            x, y + 58, 250, 55,
                            (
                                "Hỗ trợ nhiều công cụ chuyển văn bản thành giọng nói "
                                "trực tuyến và ngoại tuyến."
                                if region == 1
                                else ""
                            ),
                            role="body", layout_id=9, region_id=region,
                            font_size=18,
                            source_lines=[
                                {
                                    "x": x, "y": y + 58, "width": 250,
                                    "height": 20, "ink_height": 16,
                                }
                            ],
                        ),
                    ]
                )
        widget = ovl.Overlay(boxes)
        try:
            self.assertEqual(2, len(widget.labels))
            self.assertEqual("Chuyển văn bản thành giọng nói", widget.labels[0].text())
            self.assertIn("trực tuyến và ngoại tuyến", widget.labels[1].text())
            # Background must cover at least the source OCR area (may grow with
            # region-flow expansion so longer translations still hide the source).
            self.assertGreaterEqual(widget.labels[0]._background_rect.width(), 120)
            self.assertGreaterEqual(widget.labels[1]._background_rect.height(), 55)
        finally:
            widget.close()
            _APP.processEvents()

    def test_partial_stream_uses_full_grid_snapshot_for_flow(self):
        blocks = []
        for row, y in enumerate((20, 260)):
            for column, x in enumerate((20, 400)):
                region = row * 2 + column + 1
                blocks.extend(
                    [
                        {
                            "x": x, "y": y, "width": 120, "height": 28,
                            "text": "title", "role": "title", "region_id": region,
                            "lines": [
                                {
                                    "x": x, "y": y, "width": 120,
                                    "height": 22, "text": "title", "ink_height": 18,
                                }
                            ],
                        },
                        {
                            "x": x, "y": y + 58, "width": 250, "height": 55,
                            "text": "body", "role": "body", "region_id": region,
                        },
                    ]
                )
        first_marker = ovl.set_pending_boxes(blocks)[0]
        translated = ovl.parse_indexed_boxes(
            f"[#{first_marker}] Chuyển văn bản thành giọng nói",
            stream_id="partial-grid",
        )
        widget = ovl.Overlay(translated)
        try:
            self.assertEqual(1, len(widget.labels))
        finally:
            widget.close()
            _APP.processEvents()

    def test_card_weight_is_role_based_without_touching_colors(self):
        old_weight = ovl.CONFIG["auto_font_weight"]
        old_text = ovl.CONFIG["auto_text_color"]
        old_background = ovl.CONFIG["auto_background"]
        ovl.CONFIG.update(
            {"auto_font_weight": 1, "auto_text_color": 1, "auto_background": 1}
        )
        try:
            title = ovl._apply_source_style(
                ovl.TextBox(
                    0, 0, 120, 24, "Title", role="title", bold=False,
                    text_color="rgba(240, 240, 240, 255)",
                    background_color="rgba(32, 33, 36, 255)",
                ),
                None,
                (0, 0),
            )
            body = ovl._apply_source_style(
                ovl.TextBox(
                    0, 30, 260, 22, "Body", role="body", bold=True,
                    text_color="rgba(170, 172, 178, 255)",
                    background_color="rgba(32, 33, 36, 255)",
                ),
                None,
                (0, 0),
            )
            self.assertTrue(title.bold)
            self.assertFalse(body.bold)
            self.assertEqual("rgba(240, 240, 240, 255)", title.text_color)
            self.assertEqual("rgba(170, 172, 178, 255)", body.text_color)
        finally:
            ovl.CONFIG["auto_font_weight"] = old_weight
            ovl.CONFIG["auto_text_color"] = old_text
            ovl.CONFIG["auto_background"] = old_background

    def _blank_image(self, width, height, color):
        image_format = (
            QImage.Format.Format_RGB32
            if hasattr(QImage, "Format")
            else QImage.Format_RGB32
        )
        image = QImage(width, height, image_format)
        image.fill(QColor(color))
        return image

    def test_badge_cover_reaches_the_pill_border_not_just_the_label(self):
        # The label's OCR box stops at the glyphs; the pill (fill + 1px border)
        # runs ~12px further. Covering only the box leaves the pill drawn
        # around the translation.
        image = self._blank_image(300, 140, "#0d1117")
        painter = QPainter(image)
        painter.fillRect(40, 18, 120, 24, QColor("#28393e"))
        painter.fillRect(40, 18, 120, 1, QColor("#4a6f77"))
        painter.fillRect(40, 41, 120, 1, QColor("#4a6f77"))
        painter.end()

        pad = ovl.surface_pad(image, ovl.TextBox(52, 22, 96, 16, ""))
        self.assertIsNotNone(pad)
        left, top, right, bottom = pad
        self.assertGreaterEqual(left, 10)
        self.assertGreaterEqual(right, 10)
        self.assertGreaterEqual(top, 3)
        self.assertGreaterEqual(bottom, 3)

    def test_cover_stops_at_an_icon_sharing_the_button(self):
        # Same surface, but an icon sits to the left of the label. Growing
        # over it would erase content the user still reads.
        image = self._blank_image(300, 140, "#0d1117")
        painter = QPainter(image)
        painter.fillRect(40, 18, 160, 24, QColor("#238636"))
        painter.fillRect(48, 24, 12, 12, QColor("#ffffff"))
        painter.end()

        pad = ovl.surface_pad(image, ovl.TextBox(70, 22, 110, 16, ""))
        self.assertIsNotNone(pad)
        # The cover may run up to the icon but must not touch it: the icon
        # occupies x 48..59, so the cover's left edge stays above 59.
        self.assertGreater(70 - pad[0], 59)
        # Free surface on the other side is still covered out to the border.
        self.assertGreaterEqual(pad[2], 10)

    def test_plain_text_on_the_page_grows_no_cover(self):
        image = self._blank_image(300, 140, "#0d1117")
        painter = QPainter(image)
        painter.fillRect(40, 22, 120, 16, QColor("#e6edf3"))
        painter.end()

        self.assertIsNone(ovl.surface_pad(image, ovl.TextBox(40, 22, 120, 16, "")))

    def test_chat_line_never_grows_over_the_name_above_it(self):
        # One flat background with several lines on it. A probe far enough out
        # lands on a neighbour's glyphs, and reading those as "the page" would
        # let a message cover the username above it.
        image = self._blank_image(400, 200, "#313338")
        painter = QPainter(image)
        painter.fillRect(60, 30, 110, 18, QColor("#c9a0ff"))  # username
        painter.fillRect(60, 60, 240, 18, QColor("#dbdee1"))  # message line
        painter.fillRect(60, 88, 200, 18, QColor("#dbdee1"))
        painter.fillRect(20, 28, 30, 30, QColor("#4a7fd6"))  # avatar
        painter.end()

        self.assertIsNone(ovl.surface_pad(image, ovl.TextBox(60, 60, 240, 18, "")))

    def test_menu_and_article_in_one_frame_each_keep_their_own_strategy(self):
        # Half the window is a sidebar, half is a card. One verdict for the
        # whole frame either blobs the sidebar or leaves the card in pieces.
        rows = [
            ovl.TextBox(
                20, 40 + index * 40, 160, 22, f"Mục {index}", role="body",
                list_item=True, list_run=1, region_id=1, layout_id=1,
                font_size=15,
            )
            for index in range(6)
        ]
        paragraph = [
            ovl.TextBox(
                300, 40, 320, 22, "Câu đầu của đoạn văn dài", role="body",
                region_id=2, layout_id=1, font_size=15,
            ),
            ovl.TextBox(
                300, 68, 320, 22, "tiếp tục sang dòng thứ hai và", role="body",
                region_id=2, layout_id=1, font_size=15,
            ),
            ovl.TextBox(
                300, 96, 210, 22, "kết thúc ở dòng thứ ba.", role="body",
                region_id=2, layout_id=1, font_size=15,
            ),
        ]
        merged = ovl.coalesce_paint_boxes(rows + paragraph)
        kept_rows = [box for box in merged if box.list_item]
        self.assertEqual(6, len(kept_rows))
        body = [box for box in merged if not box.list_item and box.text.strip()]
        self.assertEqual(1, len(body))
        self.assertIn("Câu đầu", body[0].text)
        self.assertIn("dòng thứ ba", body[0].text)

    def test_clipped_menu_row_is_sized_by_its_run_not_its_own_ink(self):
        min_px, max_px = 12, 60
        rows = [
            ovl.TextBox(
                20, 40 + index * 40, 160, 22, f"Mục {index}", role="body",
                list_item=True, list_run=1, layout_id=1,
                source_lines=[{"ink_height": 20, "text": f"Item {index}"}],
            )
            for index in range(4)
        ]
        # The last row is cut off by the panel edge, so its ink measures short.
        rows[-1].source_lines = [{"ink_height": 12, "text": "Item 3"}]
        ovl._normalize_layout_styles(rows)
        starts = [ovl._estimate_start_font_px(box, min_px, max_px) for box in rows]
        self.assertEqual(1, len(set(starts)), starts)

    def test_overlapping_labels_keep_one_deterministic_winner(self):
        widget = ovl.Overlay(
            [
                ovl.TextBox(20, 20, 180, 28, "first text", font_size=16),
                ovl.TextBox(20, 20, 180, 28, "second text", font_size=16),
            ]
        )
        try:
            self.assertEqual(1, len(widget.labels))
            self.assertEqual("first text", widget.labels[0].text())
        finally:
            widget.close()
            _APP.processEvents()


if __name__ == "__main__":
    unittest.main()
