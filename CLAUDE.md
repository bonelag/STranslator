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

### Layout (`src/LunaTranslator/`)

| Path | What |
|------|------|
| `LunaTranslator.py` | App core, `textgetmethod` (source text → translate → display pipeline) |
| `gui/` | All PyQt windows. `translatorUI.py` = main floating window. `setting/` = settings tabs. |
| `ocrengines/` | OCR backends. `baseocrclass.py` = shared OCR result parsing. |
| `translator/` | Translator backends (one file each, `class TS(basetrans)`). |
| `myutils/` | `config.py` (`globalconfig` dict), `ocrutil.py` (`ocr_run`), `utils.py`. |
| `textio/` | Text sources (hook/clipboard/OCR) + outputs. |
| `ovl.py` | **Overlay feature** (see below). |
| `files/lang/*.json` | UI translations (zh/en/vi). |

Config is one big `globalconfig` dict (`myutils/config.py`), persisted to
`config.json`. Read/write keys directly.

## Overlay feature (`ovl.py`)

In-place translation: draws translated text as floating, click-through windows
positioned **on top of the original text** in the game — instead of (or in
addition to) the main translator window. New, uncommitted on `dev`.

### Pipeline

1. **OCR with coords.** `ocr_run(qimage, offset)` (`myutils/ocrutil.py`) — `offset`
   is the screen-space top-left of the captured region, so box coords become
   absolute screen coords. All `ocr_run` callers now pass it.
2. **Boxes tagged into text.** `OCRResultParsed.textonly`
   (`ocrengines/baseocrclass.py`) groups OCR blocks into lines, and for each line
   emits a coordinate tag:
   - translate engines → `[x y|w h] text`
   - plain OCR → `[#n] text` (index), and the real boxes are stashed via
     `ovl.set_pending_boxes(self._line_boxes)`.
3. **Translate.** Translator runs on the tagged text. LLM prompts are expected to
   preserve the `[...]` tags per line.
4. **Display split** (`LunaTranslator.py:textgetmethod`): the box tags are
   stripped for the main-window text (`res_ui`), and the tagged result is sent to
   `ovl.show_overlay(formatted)`.
5. **Render.** `show_overlay(data)` → `parse_boxes(text)` →
   - has `[x y|w h]` tags → use those coords directly.
   - has `[#n]` tags → `parse_indexed_boxes` maps lines back to `PENDING_BOXES`.
   - neither → `distribute_lines` spreads translated lines across the pending
     boxes by character-length ratio.
   Each `TextBox` becomes a `StrokedLabel` inside a transparent, top-most,
   click-through `Overlay` QWidget. Auto-closes after `timeout_ms`.

### Key symbols (`ovl.py`)

- `CONFIG` — overlay settings dict. Persisted to `userconfig/overlay.json` via
  `load_config` / `save_config`. Keys: `enable`, `show_in_main`, colors,
  `stroke_width`, font size min/max, `timeout_ms`, padding, and `auto_background`
  / `auto_text_color` / `auto_font_weight` (sample colors from a live screenshot).
- `show_overlay(data)` / `close_all()` — entry points. `close_all` is called
  before any OCR range-select (`translatorUI.py`) so old overlays don't capture.
- `set_pending_boxes` / `PENDING_BOXES` — module-global handoff of OCR box coords
  from the OCR layer to the renderer. (Single global, not reentrant — one OCR
  result in flight at a time.)
- `Overlay` (QWidget) — one full-screen transparent window per result; holds the
  per-box labels. Also doubles as the **debug visualizer** (see below).
- `StrokedLabel` (QLabel) — custom `paintEvent`: rounded bg, stroked + filled
  text, auto-wrap.

### Config / hotkeys / settings

- Settings tab: `gui/setting/display_overlay.py` (`overlaysetting`), registered in
  `display.py` as tab "浮窗叠加".
- Hotkeys (`gui/setting/hotkey.py`): `_52` toggle overlay, `_53` close all.
- `show_in_main=0` suppresses translation in the main window (shows an "overlay
  enabled" notice instead); `translatorUI.py` keeps the main window hidden while
  the overlay is active.

## OCR debug mode

`debugocr` + `debugocr_*` config flags (toggles in `gui/setting/about.py`).
When on, `ocr_run` routes to `dispatch_debug_ocr` (`myutils/ocrutil.py`) instead
of normal display: it reconstructs paragraphs / lines / words / per-word boxes
(and optionally guesses the font by `QFontMetrics` width-fitting) and renders
colored debug boxes via the same `Overlay` (`update_debug_content`).

Notes:
- `debugocr` is **forced `False` at load and never persisted** (`config.py`) — a
  session-only switch.
- `dispatch_debug_ocr` does per-pixel Python loops over the captured image —
  slow, but debug-only.

## Gotchas

- `globalconfig` keys are created lazily; a missing key = `KeyError`. Use
  `.get(key, default)`.
- Overlay coords are **absolute screen px**; `Overlay._render_boxes` divides by
  device-pixel-ratio and subtracts `screen_origin`. Multi-monitor uses the
  primary screen only.
- OCR box tags (`[x y|w h]`, `[#n]`) flow through the translator as text — adding
  a translator backend that mangles bracket content breaks overlay positioning.
- Debug-mode labels in `about.py` are hardcoded VI/EN inline (not in
  `files/lang/*.json` like everything else) — inconsistent; match the JSON
  pattern if you touch them.
- `.codegraph/` is untracked index data — leave it out of commits.
