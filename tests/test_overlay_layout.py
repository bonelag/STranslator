import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src" / "LunaTranslator"))

from overlay_layout import build_ocr_layout, parse_indexed_segments, segment_ocr_lines


def card_lines(x, y, title, body):
    lines = [(x, y, 170, 30, title)]
    for index, value in enumerate(body):
        lines.append((x, y + 62 + index * 42, 360 - index * 20, 20, value))
    return lines


class OverlaySegmentationTests(unittest.TestCase):
    def test_style_separates_similar_height_title_and_body(self):
        blocks = build_ocr_layout(
            [
                {
                    "x": 40, "y": 40, "width": 180, "height": 26,
                    "text": "Title", "text_rgb": (242, 243, 245),
                    "ink_height": 23, "bold_score": 0.17,
                },
                {
                    "x": 40, "y": 84, "width": 330, "height": 24,
                    "text": "Body first line", "text_rgb": (170, 172, 178),
                    "ink_height": 21, "bold_score": 0.10,
                },
                {
                    "x": 40, "y": 122, "width": 300, "height": 24,
                    "text": "Body second line", "text_rgb": (171, 173, 179),
                    "ink_height": 21, "bold_score": 0.10,
                },
            ],
            separator=" ",
        )
        self.assertEqual(["Title", "Body first line Body second line"], [b.text for b in blocks])
        self.assertNotEqual(blocks[0].text_rgb, blocks[1].text_rgb)

    def test_combined_size_and_weight_separate_same_color_title(self):
        blocks = build_ocr_layout(
            [
                {
                    "x": 40, "y": 40, "width": 180, "height": 26,
                    "text": "Title", "text_rgb": (240, 240, 240),
                    "ink_height": 23, "bold_score": 0.15, "bold": True,
                },
                {
                    "x": 40, "y": 82, "width": 330, "height": 24,
                    "text": "Body first", "text_rgb": (240, 240, 240),
                    "ink_height": 21, "bold_score": 0.10, "bold": False,
                },
                {
                    "x": 40, "y": 120, "width": 300, "height": 24,
                    "text": "Body second", "text_rgb": (240, 240, 240),
                    "ink_height": 21, "bold_score": 0.10, "bold": False,
                },
            ],
            separator=" ",
        )
        self.assertEqual(["Title", "Body first Body second"], [b.text for b in blocks])
        self.assertEqual("title", blocks[0].role)

    def test_discord_username_and_timestamp_are_separate_style_runs(self):
        blocks = build_ocr_layout(
            [
                {
                    "x": 150, "y": 30, "width": 110, "height": 22,
                    "text": "Bone Lag", "text_rgb": (245, 245, 245),
                    "ink_height": 19, "bold_score": 0.18,
                },
                {
                    "x": 267, "y": 31, "width": 130, "height": 20,
                    "text": "20:14 8/7/26", "text_rgb": (175, 178, 185),
                    "ink_height": 17, "bold_score": 0.09,
                },
            ],
            separator=" ",
        )
        self.assertEqual(2, len(blocks))
        self.assertEqual(["Bone Lag", "20:14 8/7/26"], [b.text for b in blocks])

    def test_discord_header_is_protected_from_realistic_body_color(self):
        blocks = build_ocr_layout(
            [
                {
                    "x": 150, "y": 30, "width": 110, "height": 22,
                    "text": "Dominostars", "text_rgb": (242, 243, 245),
                    "ink_height": 19, "bold_score": 0.15, "bold": True,
                },
                {
                    "x": 268, "y": 31, "width": 130, "height": 20,
                    "text": "20:14 8/7/26", "text_rgb": (148, 155, 164),
                    "ink_height": 17, "bold_score": 0.09, "bold": False,
                },
                {
                    "x": 150, "y": 60, "width": 440, "height": 22,
                    "text": "I will consider it", "text_rgb": (219, 222, 225),
                    "ink_height": 19, "bold_score": 0.10, "bold": False,
                },
            ],
            separator=" ",
        )
        self.assertEqual(
            ["protected", "metadata", "body"], [block.role for block in blocks]
        )

    def test_noisy_same_style_words_stay_one_line(self):
        atoms = []
        x = 20
        for index, word in enumerate(("six", "adjacent", "same", "style", "word", "atoms")):
            width = 35 + len(word) * 3
            atoms.append(
                {
                    "x": x, "y": 40 + index % 2, "width": width,
                    "height": 21 + index % 2, "text": word,
                    "text_rgb": (180 + index % 3, 181, 183),
                    "ink_height": 17 + index % 2,
                    "bold_score": 0.105 + (index % 2) * 0.008,
                }
            )
            x += width + 5
        blocks = build_ocr_layout(atoms, separator=" ")
        self.assertEqual(1, len(blocks))
        self.assertEqual("six adjacent same style word atoms", blocks[0].text)

    def test_contained_but_differently_indented_lines_do_not_merge(self):
        blocks = build_ocr_layout(
            [
                (154, 100, 1180, 22, "Long outer line"),
                (435, 128, 370, 22, "Contained indented line"),
            ],
            separator=" ",
        )
        self.assertEqual(2, len(blocks))

    def test_card_title_and_multiline_body_are_separate_blocks(self):
        blocks = segment_ocr_lines(
            card_lines(40, 40, "HOOK", ["First body line", "Second body line", "Third"]),
            separator=" ",
        )

        self.assertEqual(2, len(blocks))
        self.assertEqual("title", blocks[0].role)
        self.assertEqual("body", blocks[1].role)
        self.assertEqual(1, len(blocks[0].lines))
        self.assertEqual(3, len(blocks[1].lines))
        self.assertEqual(blocks[0].region_id, blocks[1].region_id)

    def test_parallel_cards_never_merge_and_use_region_reading_order(self):
        lines = []
        lines += card_lines(40, 40, "HOOK", ["One", "Two", "Three"])
        lines += card_lines(520, 40, "OCR", ["Four", "Five", "Six"])

        blocks = segment_ocr_lines(lines, separator=" ")

        self.assertEqual(4, len(blocks))
        self.assertEqual(["HOOK", "One Two Three", "OCR", "Four Five Six"], [b.text for b in blocks])
        self.assertEqual([1, 1, 2, 2], [b.region_id for b in blocks])
        self.assertLess(blocks[0].right, blocks[2].x)

    def test_two_card_rows_remain_four_independent_regions(self):
        lines = []
        lines += card_lines(40, 40, "A", ["A body 1", "A body 2"])
        lines += card_lines(520, 40, "B", ["B body 1", "B body 2"])
        lines += card_lines(40, 400, "C", ["C body 1", "C body 2"])
        lines += card_lines(520, 400, "D", ["D body 1", "D body 2"])

        blocks = segment_ocr_lines(lines, separator=" ")

        self.assertEqual(8, len(blocks))
        self.assertEqual([1, 1, 2, 2, 3, 3, 4, 4], [b.region_id for b in blocks])
        self.assertEqual(["A", "A body 1 A body 2", "B", "B body 1 B body 2", "C", "C body 1 C body 2", "D", "D body 1 D body 2"], [b.text for b in blocks])

    def test_different_line_heights_split_even_when_boxes_are_close(self):
        blocks = segment_ocr_lines(
            [
                (10, 10, 120, 32, "Heading"),
                (10, 48, 280, 19, "Body starts close"),
                (10, 88, 250, 19, "Body continues"),
            ],
            separator=" ",
        )
        self.assertEqual(2, len(blocks))
        self.assertEqual("Heading", blocks[0].text)
        self.assertEqual("Body starts close Body continues", blocks[1].text)


NAV_LABELS = [
    ("General", 106),
    ("Account", 112),
    ("Privacy", 100),
    ("Billing", 90),
    ("Usage", 90),
    ("Capabilities", 160),
    ("Reflect", 90),
    ("Time and focus", 165),
    ("Claude Code", 145),
    ("Cowork", 100),
    ("Claude in Ch...", 160),
]
NAV_PITCH = 66


def measured_ink(text, em=24):
    """Ink height a row scanner really reports for ``text`` at size ``em``.

    A scan sees the letters that are there, so the same font measures ~24px on
    "Privacy" (descender) and ~19px on "General" (none). Fixtures that hand
    every row one constant ink height describe pixels that cannot exist.
    """

    descender = any(char in "gjpqy" for char in text)
    ascender = any(char.isupper() or char.isdigit() for char in text) or any(
        char in "bdfhijklt" for char in text
    )
    if ascender and descender:
        return round(em)
    if ascender:
        return round(em * 0.78)
    if descender:
        return round(em * 0.74)
    return round(em * 0.54)


def nav_rows(box_height, em=24):
    """Settings sidebar: one short label per row, constant pitch.

    ``box_height`` is the padding the OCR engine puts around the glyphs, which
    varies per engine and must not change the segmentation. ``em=None`` drops
    the ink measurements entirely (no source image was available).
    """

    rows = []
    for index, (text, width) in enumerate(NAV_LABELS):
        centre = 121 + index * NAV_PITCH + 13
        row = {
            "x": 118,
            "y": centre - box_height / 2,
            "width": width,
            "height": box_height,
            "text": text,
            "text_rgb": (236, 236, 233),
            "bold_score": 0.10,
        }
        if em is not None:
            row["ink_height"] = measured_ink(text, em)
        rows.append(row)
    return rows


class OverlayListSegmentationTests(unittest.TestCase):
    def test_menu_rows_never_collapse_into_one_paragraph(self):
        for box_height in (24, 30, 38, 46, 56, 62):
            with self.subTest(box_height=box_height):
                blocks = build_ocr_layout(nav_rows(box_height))
                self.assertEqual(len(NAV_LABELS), len(blocks))
                self.assertEqual(
                    [label for label, _width in NAV_LABELS],
                    [block.text for block in blocks],
                )

    def test_menu_rows_survive_without_pixel_measurements(self):
        # No source image: ink heights are absent and only the boxes remain.
        blocks = build_ocr_layout(nav_rows(46, em=None))
        self.assertEqual(len(NAV_LABELS), len(blocks))

    def test_menu_rows_are_flagged_and_never_promoted_to_titles(self):
        blocks = build_ocr_layout(nav_rows(46))
        self.assertTrue(all(block.list_item for block in blocks))
        self.assertEqual({"body"}, {block.role for block in blocks})

    def test_section_header_above_a_menu_is_still_a_title(self):
        atoms = [
            {
                "x": 40, "y": 40, "width": 120, "height": 30,
                "text": "Settings", "ink_height": measured_ink("Settings", 30),
                "bold_score": 0.26,
            }
        ]
        atoms.extend(nav_rows(46)[:5])

        blocks = build_ocr_layout(atoms)

        self.assertEqual(6, len(blocks))
        self.assertEqual("title", blocks[0].role)
        self.assertFalse(blocks[0].list_item)
        self.assertTrue(all(block.list_item for block in blocks[1:]))

    def test_loose_leading_but_full_measure_stays_one_paragraph(self):
        blocks = build_ocr_layout(
            [
                {"x": 40, "y": 100 + index * 35, "width": width, "height": 24,
                 "text": text, "ink_height": measured_ink(text, 21)}
                for index, (text, width) in enumerate(
                    [
                        ("this paragraph is set with loose", 500),
                        ("leading but every line still runs", 496),
                        ("the full measure of the column", 502),
                        ("until the last line.", 210),
                    ]
                )
            ]
        )
        self.assertEqual(1, len(blocks))
        self.assertFalse(blocks[0].list_item)

    def test_padded_boxes_do_not_fuse_two_discord_messages(self):
        blocks = build_ocr_layout(
            [
                {"x": 96, "y": 100, "width": 90, "height": 20, "text": "bonelag",
                 "ink_height": 13, "text_rgb": (242, 243, 245), "bold_score": 0.20},
                {"x": 196, "y": 102, "width": 60, "height": 18, "text": "14:32",
                 "ink_height": 11, "text_rgb": (148, 155, 164), "bold_score": 0.08},
                {"x": 96, "y": 124, "width": 430, "height": 20,
                 "text": "when i tried the overlay on a long message",
                 "ink_height": 12, "text_rgb": (219, 222, 225), "bold_score": 0.10},
                {"x": 96, "y": 146, "width": 380, "height": 20,
                 "text": "it wrapped inside the same bubble",
                 "ink_height": 12, "text_rgb": (219, 222, 225), "bold_score": 0.10},
                {"x": 96, "y": 200, "width": 78, "height": 20, "text": "hillya",
                 "ink_height": 13, "text_rgb": (242, 243, 245), "bold_score": 0.20},
                {"x": 96, "y": 224, "width": 300, "height": 20,
                 "text": "that is the expected behaviour",
                 "ink_height": 12, "text_rgb": (219, 222, 225), "bold_score": 0.10},
            ]
        )

        bodies = [block for block in blocks if block.role == "body"]
        self.assertEqual(2, len(bodies))
        self.assertEqual(
            "when i tried the overlay on a long message "
            "it wrapped inside the same bubble",
            bodies[0].text,
        )
        self.assertEqual("that is the expected behaviour", bodies[1].text)


class OverlayMeasurementTests(unittest.TestCase):
    """Signals taken from pixels, and the noise that comes with them."""

    def test_descenders_alone_never_create_a_heading(self):
        # "Privacy" inks a third taller than "General" in the very same font.
        blocks = build_ocr_layout(
            [
                {"x": 40, "y": 40, "width": 120, "height": 30, "text": "General",
                 "ink_height": measured_ink("General", 28), "text_rgb": (187, 187, 187)},
                {"x": 40, "y": 106, "width": 120, "height": 30, "text": "Privacy",
                 "ink_height": measured_ink("Privacy", 28), "text_rgb": (187, 187, 187)},
                {"x": 40, "y": 172, "width": 150, "height": 30, "text": "Billing",
                 "ink_height": measured_ink("Billing", 28), "text_rgb": (187, 187, 187)},
                {"x": 40, "y": 238, "width": 130, "height": 30, "text": "Reflect",
                 "ink_height": measured_ink("Reflect", 28), "text_rgb": (187, 187, 187)},
            ]
        )
        self.assertEqual(4, len(blocks))
        self.assertEqual({"body"}, {block.role for block in blocks})
        self.assertTrue(all(block.list_item for block in blocks))

    def test_brighter_line_above_body_is_a_heading_at_the_same_size(self):
        blocks = build_ocr_layout(
            [
                {"x": 425, "y": 1035, "width": 480, "height": 34,
                 "text": "Switch models when a message is flagged",
                 "ink_height": 28, "text_rgb": (255, 255, 255), "bold_score": 0.14},
                {"x": 425, "y": 1083, "width": 900, "height": 30,
                 "text": "When safety measures flag a message, automatically "
                         "switch to a different model to keep chatting.",
                 "ink_height": 28, "text_rgb": (130, 130, 130), "bold_score": 0.10},
            ]
        )
        self.assertEqual(2, len(blocks))
        self.assertEqual("title", blocks[0].role)
        self.assertEqual("body", blocks[1].role)

    def test_clipped_dim_last_row_does_not_promote_the_row_above(self):
        # "Plugins" is cut off by the viewport, so it measures short and dim.
        blocks = build_ocr_layout(
            [
                {"x": 98, "y": 968, "width": 140, "height": 24, "text": "Customize",
                 "ink_height": 18, "text_rgb": (128, 128, 128)},
                {"x": 98, "y": 1039, "width": 80, "height": 27, "text": "Skills",
                 "ink_height": 21, "text_rgb": (187, 187, 187)},
                {"x": 98, "y": 1104, "width": 150, "height": 28, "text": "Connectors",
                 "ink_height": 22, "text_rgb": (185, 185, 185)},
                {"x": 98, "y": 1169, "width": 110, "height": 27, "text": "Plugins",
                 "ink_height": 15, "text_rgb": (112, 112, 112)},
            ]
        )
        self.assertNotIn("title", [block.role for block in blocks])

    def test_sidebar_run_survives_a_second_column_interleaved_by_y(self):
        atoms = []
        for index, label in enumerate(
            ("General", "Account", "Privacy", "Billing", "Usage")
        ):
            atoms.append(
                {"x": 98, "y": 209 + index * 66, "width": 120, "height": 30,
                 "text": label, "ink_height": measured_ink(label, 28),
                 "text_rgb": (187, 187, 187)}
            )
        # Panel copy to the right, whose rows fall between the sidebar's.
        for index, text in enumerate(("Claude Code", "Claude understands your codebase")):
            atoms.append(
                {"x": 504, "y": 204 + index * 61, "width": 420, "height": 32,
                 "text": text, "ink_height": measured_ink(text, 28),
                 "text_rgb": (245, 245, 245) if index == 0 else (187, 187, 187)}
            )

        blocks = build_ocr_layout(atoms)

        sidebar = [block for block in blocks if block.x < 300]
        self.assertEqual(5, len(sidebar))
        self.assertTrue(all(block.list_item for block in sidebar))

    def test_bullet_marker_always_opens_a_new_block(self):
        # OCR reports the bullet glyph as a bare '.', tight against the item.
        blocks = build_ocr_layout(
            [
                {"x": 163, "y": 281, "width": 700, "height": 30,
                 "text": ". Added Thai and Hindi as source languages",
                 "ink_height": measured_ink(". Added Thai and Hindi as source languages", 28),
                 "text_rgb": (220, 221, 222)},
                {"x": 163, "y": 321, "width": 900, "height": 30,
                 "text": ". OCR: rotate wedged vertical columns to improve readability",
                 "ink_height": measured_ink(". OCR: rotate wedged vertical columns", 28),
                 "text_rgb": (220, 221, 222)},
                {"x": 163, "y": 361, "width": 200, "height": 30,
                 "text": "never break",
                 "ink_height": measured_ink("never break", 28),
                 "text_rgb": (220, 221, 222)},
            ]
        )
        self.assertEqual(2, len(blocks))
        self.assertTrue(blocks[0].text.startswith(". Added"))
        self.assertIn("never break", blocks[1].text)
        self.assertTrue(all(block.list_item for block in blocks))

    def test_bullet_keeps_its_hanging_continuation_lines(self):
        # Continuations hang past the marker and the item opens bold, so both
        # the left edge and the measured weight disagree with the first line.
        def row(y, x, width, text, bold):
            return {
                "x": x, "y": y, "width": width, "height": 32, "text": text,
                "ink_height": measured_ink(text, 28),
                "text_rgb": (236, 236, 236), "bold_score": bold,
            }

        blocks = build_ocr_layout(
            [
                row(266, 133, 710, ". Added Thai and Hindi as source languages", 0.20),
                row(314, 133, 1063, ". OCR: rotate wedged columns so words", 0.22),
                row(354, 160, 147, "never break", 0.14),
                row(400, 132, 931, ". Magnifying lens grows to fit the dictionary", 0.19),
            ]
        )

        self.assertEqual(3, len(blocks))
        self.assertIn("never break", blocks[1].text)
        self.assertTrue(blocks[2].text.startswith(". Magnifying"))

    def test_run_survives_a_row_the_viewport_clipped(self):
        # The last row is cut off, so it measures far less ink than its box.
        rows = [
            {"x": 98, "y": 968, "width": 140, "height": 27, "text": "Customize",
             "ink_height": measured_ink("Customize", 28), "text_rgb": (185, 185, 185)},
            {"x": 98, "y": 1039, "width": 80, "height": 27, "text": "Skills",
             "ink_height": measured_ink("Skills", 28), "text_rgb": (185, 185, 185)},
            {"x": 98, "y": 1110, "width": 150, "height": 27, "text": "Connectors",
             "ink_height": measured_ink("Connectors", 28), "text_rgb": (185, 185, 185)},
            {"x": 98, "y": 1181, "width": 110, "height": 27, "text": "Plugins",
             "ink_height": 15, "text_rgb": (112, 112, 112)},
        ]

        blocks = build_ocr_layout(rows)

        self.assertEqual(4, len(blocks))
        self.assertTrue(all(block.list_item for block in blocks))

    def test_interleaved_titles_and_captions_are_two_lists_not_none(self):
        # An issue list repeats title-then-caption down one edge, so every
        # other step has the wrong size for the list it belongs to.
        atoms = []
        for index in range(5):
            top = 413 + index * 116
            atoms.append(
                {"x": 571 if index % 2 else 532, "y": top, "width": 500,
                 "height": 33, "text": f"Issue title number {index}",
                 "ink_height": 28, "text_rgb": (243, 243, 243)}
            )
            atoms.append(
                {"x": 575, "y": top + 50, "width": 360, "height": 27,
                 "text": f"#24{index} - someone opened it",
                 "ink_height": 22, "text_rgb": (138, 145, 154)}
            )

        blocks = build_ocr_layout(atoms)

        self.assertEqual(10, len(blocks))
        self.assertTrue(all(block.list_item for block in blocks))
        self.assertEqual({"body"}, {block.role for block in blocks})

    def test_wide_gap_on_one_row_keeps_nav_tabs_apart(self):
        blocks = build_ocr_layout(
            [
                {"x": 600, "y": 110, "width": 230, "height": 26,
                 "text": "Security and quality", "ink_height": 19},
                {"x": 862, "y": 110, "width": 90, "height": 26,
                 "text": "Insights", "ink_height": 19},
                {"x": 985, "y": 110, "width": 80, "height": 26,
                 "text": "Actions", "ink_height": 19},
            ]
        )
        self.assertEqual(3, len(blocks))


class OverlayMarkerParsingTests(unittest.TestCase):
    def test_parses_out_of_order_stable_markers(self):
        self.assertEqual(
            [(42, "Body"), (7, "Title")],
            parse_indexed_segments("[#42] Body\n[#7] Title"),
        )

    def test_does_not_treat_numbered_content_as_overlay_marker(self):
        self.assertEqual([], parse_indexed_segments("[1] First item\n[2] Second item"))

    def test_missing_marker_is_not_materialized_or_merged(self):
        self.assertEqual(
            [(10, "Only the title has arrived")],
            parse_indexed_segments("[#10] Only the title has arrived"),
        )


if __name__ == "__main__":
    unittest.main()
