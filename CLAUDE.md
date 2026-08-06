# CLAUDE.md

Guidance for working in this repo (LunaTranslator fork, branch `dev`).

## What this is

LunaTranslator — desktop translator for visual novels / games (Windows). Grabs
source text via **text hooks** (injected into the game process) or **OCR**
(screen capture), runs it through a translator (Google, DeepL, LLMs, etc.),
shows the result. PyQt UI. Native C++ hook engine lives under
`src/NativeImpl/LunaHook/`; Python app under `src/LunaTranslator/`.

Entry: `src/LunaTranslator/main.py` → `LunaTranslator.py` (`BASEOBJECT`, the app
god-object on `gobject.base`).

The fork's own work is the **overlay** (`ovl.py` + `overlay_layout.py`, ~5.6k
lines and the only part with tests). Assume a task here is overlay-related
unless it clearly is not.

### Layout (`src/LunaTranslator/`)

| Path | What |
|------|------|
| `LunaTranslator.py` | App core, `textgetmethod` (source text → translate → display pipeline) |
| `ovl.py` | **Overlay rendering** — 4.5k lines (see below) |
| `overlay_layout.py` | **Overlay segmentation** — OCR atoms → `LayoutBlock`s, 1.1k lines |
| `gui/` | All PyQt windows. `translatorUI.py` = main floating window. `setting/` = settings tabs. |
| `ocrengines/` | OCR backends. `baseocrclass.py` = shared OCR result parsing + marker emission. |
| `translator/` | Translator backends (one file each, `class TS(basetrans)`). |
| `myutils/` | `config.py` (`globalconfig` dict), `ocrutil.py` (`ocr_run`), `utils.py`. |
| `textio/` | Text sources (hook/clipboard/OCR) + outputs. |
| `files/lang/*.json` | UI translations (zh/en/vi). |
| `tests/` (repo root) | 86 unittests, overlay only. |

Config is one big `globalconfig` dict (`myutils/config.py`), persisted to
`config.json`. Read/write keys directly.

## Working in this repo

**Tests.** Overlay changes must keep these green:

```bash
cd tests && python -m unittest test_overlay_layout test_overlay_mapping
```

86 tests, <1s. `python -m unittest discover -s tests` **fails** — no
`__init__.py`. `test_overlay_mapping` needs Qt and chdirs into `src/` to import
`ovl`. Nothing else in the repo has tests.

**Unit tests are necessary but not sufficient.** `src/p1..p6.png` are real
captures (Claude settings, Discord ×2, a feature-card grid, GitHub repo, GitHub
issues) and `src/r1..r6.png` are the rendered overlays in Vietnamese. Every
threshold in the layout code was set from them, and the suite passes on several
settings that visibly break a real frame. For anything touching segmentation,
sizing, colour or masks: re-render and **look**.

**Rendering the real OCR headlessly** (to produce an `r*.png`):
`os.add_dll_directory("files/DLL64")`, then `import NativeUtils` **before** Qt
(after PyQt loads its own Qt, `NativeUtils.dll` fails with WinError 1114), cwd =
`src/`. Then `ocrutil.ocr_init()`, then
`ocrutil._ocrengine._private_ocr(qimage, (0, 0))`. The user's engine is
SnippingTool (oneocr, line-level boxes). Do **not** set
`QT_QPA_PLATFORM=offscreen` if text must rasterise — offscreen draws no glyphs.

**CodeGraph.** The repo is indexed (`.codegraph/`), and `codegraph_explore` is
the right first move on `ovl.py` — reading the whole file costs ~50k tokens for
what is usually two functions. Two caveats learned the hard way:

- Its **"⚠️ no covering tests found"** signal is wrong here. It does not resolve
  `ovl.parse_boxes(...)`-style module-attribute calls, so it reports the
  well-tested overlay functions as untested. Never repeat that claim without
  grepping `tests/` yourself.
- Name matching is substring-based, so a query containing `parse_boxes` also
  drags in `mecab.parse` and the `HTTPHandler.parse` family. Query exact symbol
  names.

## Overlay: what it is

In-place translation: draws translated text as floating, click-through windows
positioned **on top of the original text**, instead of (or in addition to) the
main translator window.

**The fidelity contract.** The painted layer should be indistinguishable from
the source. That single rule explains most of the code's apparent paranoia:

- A wrong **merge** is far more costly than a wrong split — a merge destroys
  structure that cannot be recovered, a split merely looks slightly off.
- Style is **measured, never assigned by role**. Colour, size and weight come
  from the source pixels; `text_rgb is None` is the only licence to invent a
  colour. Picking "one colour per role" is what once painted a whole GitHub page
  cyan and erased dark titles on light surfaces.
- Painting **nothing** beats painting wrong. Several paths deliberately
  `return []` rather than guess — see `parse_boxes` and `parse_indexed_boxes`.

## Overlay pipeline

1. **OCR with coords.** `ocr_run(qimage, offset)` (`myutils/ocrutil.py`) —
   `offset` is the screen-space top-left of the captured region, so box coords
   become absolute screen coords. All `ocr_run` callers pass it.

2. **Segment** (`overlay_layout.py`). `build_ocr_layout` = `merge_ocr_atoms`
   (atoms → `LayoutLine`s) → `segment_ocr_lines` (lines → `LayoutBlock`s, with
   `role`, `list_item`, `list_run`, `region_id`). This is where layout structure
   is decided, once, **with the whole OCR frame in view** — including rows that
   never get translated.

3. **Register + tag** (`ocrengines/baseocrclass.py`, `OCRResultParsed.textonly`).
   Blocks go to `ovl.set_pending_boxes(...)`, which returns **stable marker
   ids**. Then per line:
   - OCR-translate engines → `[x y|w h] text` (literal coords)
   - plain OCR → `[#<marker_id>] text`
   - registration failed → plain text, no markers (overlay then no-ops rather
     than mis-positioning)

   `role in ("metadata", "protected")` blocks (usernames, timestamps) are
   **excluded** from the translated text on purpose.

4. **Translate.** The translator runs on the tagged text; LLM prompts must
   preserve the `[...]` tag per line.

5. **Display split** (`LunaTranslator.py:textgetmethod`, ~line 897). Tags are
   stripped for the main window (`res_ui`); the tagged result goes to
   `ovl.show_overlay(formatted, stream_id=f"{signature}:{classname}")`.

6. **Render.** `show_overlay` → `parse_boxes` → `coalesce_paint_boxes` →
   `Overlay._render_boxes` → one `StrokedLabel` per box in a transparent,
   top-most, click-through window. Auto-closes after `timeout_ms`.

`parse_boxes` has three branches:
- `[x y|w h]` tags → use the coords, then `_copy_matching_source_style` recovers
  the sampled style from the pending box with IoU ≥ 0.35.
- `[#n]` tags → `parse_indexed_boxes` resolves markers via `_PENDING_BY_MARKER`.
- **neither → `return []`.** Not a fallback. Without coords or markers there is
  no way to know the translation belongs to the current OCR frame; reusing the
  last global boxes used to paint hook/clipboard translations onto stale OCR
  geometry. (`distribute_lines` still exists at `ovl.py:1961` but is **dead
  code** — nothing calls it.)

## Overlay key symbols

### State (`ovl.py`, module globals — read these first)

`set_pending_boxes` (`:1915`) is the OCR→renderer handoff. It is **not** a single
global any more:

| Name | What |
|------|------|
| `_PENDING_BY_MARKER` | `OrderedDict[marker_id → TextBox]`, capped at 1024, LRU-evicted. The real source of truth. |
| `_LAYOUT_MARKERS` | `layout_id → [marker_id]`. Lets the renderer recover *untranslated* siblings of a frame. |
| `_TRANSLATED_BY_STREAM` | `(stream_id, marker_id) → text`. Keeps concurrent translator streams from mixing. |
| `_CHAT_LAYOUT_IDS` | layout ids classified as chat this cycle. |
| `_PENDING_LOCK` | `RLock` — OCR worker threads register, the UI thread paints. |
| `PENDING_BOXES` | Legacy last-frame list. Kept for the IoU style lookup; prefer the marker map. |

`set_pending_boxes` returns marker ids and samples style **outside** the lock
(sampling is slow; only map mutation is atomic).

### `TextBox` (`:998`) — the unit that flows through everything

Beyond geometry + text + colours, the fields that carry *decisions*:

- `role` — `body` / `title` / `metadata` / `protected`. `metadata`/`protected`
  are never painted.
- `list_item` — this is a menu/option row. Decided in segmentation; paint must
  **not** re-merge it.
- `list_run` / `run_ink` — which proven-uniform row run it belongs to, and that
  run's median ink. A clipped or faded row measures far too small on its own.
- `text_rgb` — the *measured* colour (vs `text_color`, the painted one). Every
  hand-built `TextBox(...)` must carry it through or paint concludes the colour
  was never measured.
- `ink_height` / `ink_top` / `bold_score` — measured glyph geometry.
- `marker_id` / `layout_id` / `region_id` / `source_id` — identity + grouping.
- `demoted` — was a title, reclassified into its card's body.

### Paint path

- `coalesce_paint_boxes` (`:2822`) — picks a strategy **per subset**: `list_item`
  rows keep themselves (`_coalesce_list_boxes`), everything else is classified on
  its own (`_coalesce_flowing_boxes` → chat / card / paragraph). A frame that is
  half sidebar and half article is both; one verdict for the whole frame either
  blobs the sidebar or leaves the article in OCR fragments.
- `_harmonize_role_palette` (`:1770`) — folds *sampling jitter only*. Two blocks
  share a colour only if their samples were within `_PALETTE_MERGE_DISTANCE`
  (60, L1).
- `_shared_font_px_by_role` — one size per role per frame; skipped for chat
  (a shared minimum was crushing long messages).
- `Overlay._render_boxes` (`:3472`) — three phases: scale + sample style → shared
  size/palette → masks + labels. Also does collision rejection (chat uses looser
  thresholds; a dropped label keeps its background mask, or the source shows
  through).
- `_widen_list_rows` / `_region_flow_boxes` (`:3016`, `:3069`) — an OCR box is a
  tight ink crop, too narrow for a longer translation and too short for
  Vietnamese diacritics. Widening must run **after** the region pass, which
  otherwise clamps a row back to its card's bounds.
- `surface_pad` (`:206`) / `sample_background` (`:158`) — covers grow to the
  *element* (pill, badge, button) not the label; background prefers the majority
  colour *inside* the box unless that colour is the ink.
- `StrokedLabel` (`:2884`) — custom `paintEvent`: rounded bg, stroked + filled
  text, balanced wrap.

### Segmentation thresholds (`overlay_layout.py`)

Any spacing threshold is in **glyph heights** (`ink_height`, else `font_size`),
never OCR box heights — engines pad boxes by wildly different amounts, so a
box-height threshold silently scales with the padding. That is what collapsed a
Settings sidebar (66px row pitch, 46px padded boxes) into one 700px paragraph.

- pitch ≤ `_PARAGRAPH_LEADING` (1.75 glyph) → paragraph
- ≥ `_CONCLUSIVE_LIST_LEADING` (2.60) → list
- between `_LIST_LEADING` (2.05) and 2.60 → list only if non-final lines are
  ragged (< `_MEASURE_FILL` = 0.82 of the measure)

`_ink_zone_factor(text)` divides measured ink before comparing sizes *across*
lines — a row scan reports the letters present, so "Privacy" inks ~30% taller
than "General" in the same font. Compare raw ink only along one baseline.
Skipping this invents headings out of descenders.

Bullets: a line starting with a marker (OCR often renders the glyph as `.`)
always opens a new block, its continuation lines hang past the marker (allow a
wider left delta), and the bold check is skipped — items routinely open bold and
continue regular.

## Overlay config / hotkeys / settings

- `ovl.CONFIG` — overlay settings dict, persisted to `userconfig/overlay.json`
  via `load_config` / `save_config` (**not** in `globalconfig`). Keys: `enable`,
  `show_in_main`, colours, `stroke_width`, min/max font size, `font_family`,
  `timeout_ms`, padding, `screen_capture_protection`, `auto_background` /
  `auto_text_color` / `auto_font_weight` / `auto_font_family`,
  `adaptive_font_size`, `debug_dump`.
- `debug_dump=1` writes what the OCR layer handed the renderer to
  `userconfig/overlay_dump.json` (`_dump_pending_boxes`) — the fastest way to
  diagnose a bad overlay from data instead of guessing.
- Settings tab: `gui/setting/display_overlay.py` (`overlaysetting`), registered in
  `display.py` as tab "浮窗叠加".
- Hotkeys (`gui/setting/hotkey.py`): `_52` toggle overlay, `_53` close all.
- `show_in_main=0` suppresses translation in the main window (shows an "overlay
  enabled" notice instead); `translatorUI.py` keeps the main window hidden while
  the overlay is active.
- `close_all()` is called before any OCR range-select (`translatorUI.py:718`,
  `:1803`, `:1811`) so old overlays don't get captured into the new screenshot.

## OCR debug mode

`debugocr` + `debugocr_*` config flags (toggles in `gui/setting/about.py`).
When on, `ocr_run` routes to `dispatch_debug_ocr` (`myutils/ocrutil.py`) instead
of normal display: it reconstructs paragraphs / lines / words / per-word boxes
(and optionally guesses the font by `QFontMetrics` width-fitting) and renders
coloured debug boxes through the same `Overlay` (`update_debug_content`).
`show_overlay` detects this path by the payload being JSON containing
`debugocr`.

- `debugocr` is **forced `False` at load and never persisted** (`config.py`) — a
  session-only switch.
- `dispatch_debug_ocr` does per-pixel Python loops over the captured image —
  slow, but debug-only.

## Gotchas

- `globalconfig` keys are created lazily; a missing key = `KeyError`. Use
  `.get(key, default)`. (`ovl.CONFIG` has real defaults, so plain indexing is
  fine there.)
- Overlay coords are **absolute physical screen px**. `Overlay._configure_screen`
  picks the screen containing the boxes' centre (`_screen_for_boxes`) and
  measures the real DPR via `GetWindowRect`; `_render_boxes` divides by DPR and
  subtracts `screen_origin_physical_*`. Multi-monitor works, but one `Overlay`
  covers one screen.
- OCR box tags (`[x y|w h]`, `[#n]`) travel through the translator **as text** —
  a backend that mangles bracket content breaks overlay positioning entirely.
- `set_pending_boxes` runs on OCR threads, painting on the UI thread: touch the
  pending maps only under `_PENDING_LOCK`.
- Debug-mode labels in `about.py` are hardcoded VI/EN inline (not in
  `files/lang/*.json` like everything else) — inconsistent; match the JSON
  pattern if you touch them.
- `.codegraph/` is untracked index data — leave it out of commits.

