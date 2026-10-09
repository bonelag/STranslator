from qtsymbols import *
import functools
import json
from pathlib import Path
from gui.usefulwidget import (
    D_getspinbox,
    D_getcolorbutton,
    D_getsimpleswitch,
    getboxlayout,
    FocusFontCombo,
    makecardrow,
)
from gui.dynalang import LLabel
import ovl

ovl.load_config()


def save_overlay_config():
    config_path = Path("userconfig/overlay.json")
    if not config_path.parent.exists():
        config_path.parent.mkdir(parents=True)

    save_data = {
        k: v
        for k, v in ovl.CONFIG.items()
        if k
        in [
            "enable",
            "show_in_main",
            "text_color",
            "stroke_color",
            "stroke_width",
            "min_font_size",
            "max_font_size",
            "font_family",
            "background_color",
            "timeout_ms",
            "horizontal_padding",
            "vertical_padding",
            "screen_capture_protection",
            "auto_background",
            "auto_text_color",
            "auto_font_weight",
            "auto_font_family",
            "adaptive_font_size",
        ]
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=4)


def update_color_with_opacity(color_key, rgb_key, alpha_key):
    color = QColor(ovl.CONFIG.get(rgb_key, "#000000"))
    alpha = ovl.CONFIG.get(alpha_key, 255)
    ovl.CONFIG[color_key] = (
        f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha/255:.2f})"
    )
    save_overlay_config()


def create_opacity_slider_generic(color_key, rgb_key, alpha_key, slider_width=140):
    slider = QSlider(Qt.Orientation.Horizontal)
    if slider_width is not None:
        slider.setFixedWidth(slider_width)
    slider.setRange(0, 255)

    current_color = ovl.CONFIG.get(color_key, "rgba(0, 0, 0, 1)")
    current_alpha = 255
    try:
        c = ovl.parse_color(current_color)
        current_alpha = c.alpha()
    except Exception:
        pass

    ovl.CONFIG[alpha_key] = current_alpha
    slider.setValue(current_alpha)

    label = QLabel(f"{int(current_alpha/255*100)}%")
    label.setFixedWidth(40)

    def on_change(val):
        ovl.CONFIG[alpha_key] = val
        label.setText(f"{int(val/255*100)}%")
        update_color_with_opacity(color_key, rgb_key, alpha_key)

    slider.valueChanged.connect(on_change)
    return getboxlayout([slider, label])


def _srow(title):
    lab = LLabel(title)
    font = lab.font()
    font.setBold(True)
    font.setPixelSize(15)
    lab.setFont(font)
    return [[(lab, 0)]]


def _cardrow(label, *controls):
    return [[(makecardrow(label, *controls), 0)]]


def _create_font_combo():
    combo = FocusFontCombo()
    current_font = ovl.CONFIG.get("font_family", "")
    if current_font:
        combo.setCurrentFont(QFont(current_font))

    def on_change(font_family):
        ovl.CONFIG["font_family"] = font_family
        save_overlay_config()

    combo.currentTextChanged.connect(on_change)
    return combo


def overlaysetting(self):
    import gobject

    for color_key, rgb_key in [
        ("background_color", "_bg_rgb"),
        ("text_color", "_text_rgb"),
        ("stroke_color", "_stroke_rgb"),
    ]:
        current_val = ovl.CONFIG.get(color_key, "rgba(0, 0, 0, 1)")
        try:
            c = ovl.parse_color(current_val)
            ovl.CONFIG[rgb_key] = c.name()
        except Exception:
            ovl.CONFIG[rgb_key] = "#000000"

    # --- Nền (Background) ---
    bg_color_btn = D_getcolorbutton(
        self,
        ovl.CONFIG,
        "_bg_rgb",
        callback=lambda _: update_color_with_opacity(
            "background_color", "_bg_rgb", "_bg_alpha"
        ),
    )()
    bg_opacity_slider = create_opacity_slider_generic(
        "background_color", "_bg_rgb", "_bg_alpha"
    )
    auto_bg_switch = D_getsimpleswitch(ovl.CONFIG, "auto_background")()

    def on_auto_bg_toggle(val):
        ovl.CONFIG["auto_background"] = int(val)
        bg_opacity_slider.setEnabled(not val)
        bg_color_btn.setEnabled(not val)
        save_overlay_config()

    auto_bg_switch.toggled.connect(on_auto_bg_toggle)
    on_auto_bg_toggle(auto_bg_switch.isChecked())

    bg_ctrl_widget = QWidget()
    bg_ctrl_lay = QHBoxLayout(bg_ctrl_widget)
    bg_ctrl_lay.setContentsMargins(0, 0, 0, 0)
    bg_ctrl_lay.setSpacing(8)
    bg_ctrl_lay.addWidget(bg_color_btn)
    bg_ctrl_lay.addLayout(bg_opacity_slider)
    bg_ctrl_lay.addWidget(LLabel("自动"))
    bg_ctrl_lay.addWidget(auto_bg_switch)

    # --- Chữ (Text Color) ---
    text_color_btn = D_getcolorbutton(
        self,
        ovl.CONFIG,
        "_text_rgb",
        callback=lambda _: update_color_with_opacity(
            "text_color", "_text_rgb", "_text_alpha"
        ),
    )()
    text_opacity_slider = create_opacity_slider_generic(
        "text_color", "_text_rgb", "_text_alpha"
    )
    auto_text_switch = D_getsimpleswitch(ovl.CONFIG, "auto_text_color")()

    def on_auto_text_toggle(val):
        ovl.CONFIG["auto_text_color"] = int(val)
        text_opacity_slider.setEnabled(not val)
        text_color_btn.setEnabled(not val)
        save_overlay_config()

    auto_text_switch.toggled.connect(on_auto_text_toggle)
    on_auto_text_toggle(auto_text_switch.isChecked())

    text_ctrl_widget = QWidget()
    text_ctrl_lay = QHBoxLayout(text_ctrl_widget)
    text_ctrl_lay.setContentsMargins(0, 0, 0, 0)
    text_ctrl_lay.setSpacing(8)
    text_ctrl_lay.addWidget(text_color_btn)
    text_ctrl_lay.addLayout(text_opacity_slider)
    text_ctrl_lay.addWidget(LLabel("自动"))
    text_ctrl_lay.addWidget(auto_text_switch)

    # --- Viền chữ (Stroke) ---
    stroke_color_btn = D_getcolorbutton(
        self,
        ovl.CONFIG,
        "_stroke_rgb",
        callback=lambda _: update_color_with_opacity(
            "stroke_color", "_stroke_rgb", "_stroke_alpha"
        ),
    )()
    stroke_opacity_slider = create_opacity_slider_generic(
        "stroke_color", "_stroke_rgb", "_stroke_alpha"
    )
    stroke_ctrl_widget = QWidget()
    stroke_ctrl_lay = QHBoxLayout(stroke_ctrl_widget)
    stroke_ctrl_lay.setContentsMargins(0, 0, 0, 0)
    stroke_ctrl_lay.setSpacing(8)
    stroke_ctrl_lay.addWidget(stroke_color_btn)
    stroke_ctrl_lay.addLayout(stroke_opacity_slider)

    generic_save = lambda _: save_overlay_config()

    enable_switch = D_getsimpleswitch(
        ovl.CONFIG,
        "enable",
        callback=lambda x: (
            ovl.CONFIG.update({"enable": int(x)}),
            save_overlay_config(),
        ),
    )
    show_main_switch = D_getsimpleswitch(
        ovl.CONFIG,
        "show_in_main",
        callback=lambda x: (
            ovl.CONFIG.update({"show_in_main": int(x)}),
            save_overlay_config(),
            gobject.base.translation_ui.update_main_window_translation_display()
            if hasattr(gobject, "base")
            and hasattr(gobject.base, "translation_ui")
            else None
        ),
    )
    auto_weight_switch = D_getsimpleswitch(
        ovl.CONFIG,
        "auto_font_weight",
        callback=lambda x: (
            ovl.CONFIG.update({"auto_font_weight": int(x)}),
            save_overlay_config(),
        ),
    )
    auto_family_switch = D_getsimpleswitch(
        ovl.CONFIG,
        "auto_font_family",
        callback=lambda x: (
            ovl.CONFIG.update({"auto_font_family": int(x)}),
            save_overlay_config(),
        ),
    )
    adaptive_size_switch = D_getsimpleswitch(
        ovl.CONFIG,
        "adaptive_font_size",
        callback=lambda x: (
            ovl.CONFIG.update({"adaptive_font_size": int(x)}),
            save_overlay_config(),
        ),
    )

    stroke_width_box = D_getspinbox(
        0,
        10,
        ovl.CONFIG,
        "stroke_width",
        double=True,
        step=0.5,
        callback=generic_save,
    )
    min_size_box = D_getspinbox(
        1,
        100,
        ovl.CONFIG,
        "min_font_size",
        double=True,
        step=0.5,
        callback=generic_save,
    )
    max_size_box = D_getspinbox(
        1,
        200,
        ovl.CONFIG,
        "max_font_size",
        double=True,
        step=0.5,
        callback=generic_save,
    )
    h_padding_box = D_getspinbox(
        0,
        50,
        ovl.CONFIG,
        "horizontal_padding",
        double=True,
        step=0.5,
        callback=generic_save,
    )
    v_padding_box = D_getspinbox(
        0,
        50,
        ovl.CONFIG,
        "vertical_padding",
        double=True,
        step=0.5,
        callback=generic_save,
    )
    timeout_box = D_getspinbox(
        100, 60000, ovl.CONFIG, "timeout_ms", callback=generic_save
    )

    grid = (
        _srow("常规")
        + _cardrow("ovlEnable", enable_switch)
        + _cardrow("ovlShowInMain", show_main_switch)
        + _cardrow("ovlTimeout", timeout_box, LLabel("ms"))
        + _srow("文本与排版")
        + _cardrow("ovlAdaptiveSize", adaptive_size_switch)
        + _cardrow("ovlTextSizeMin", min_size_box, LLabel("px"))
        + _cardrow("ovlTextSizeMax", max_size_box, LLabel("px"))
        + _cardrow("ovlAutoWeight", auto_weight_switch)
        + _cardrow("ovlAutoFamily", auto_family_switch)
        + _cardrow("ovlTextFont", _create_font_combo)
        + _cardrow("ovlPaddingH", h_padding_box, LLabel("px"))
        + _cardrow("ovlPaddingV", v_padding_box, LLabel("px"))
        + _srow("颜色与外观")
        + _cardrow("ovlTextColor", text_ctrl_widget)
        + _cardrow("ovlStrokeColor", stroke_ctrl_widget)
        + _cardrow("ovlStrokeWidth", stroke_width_box, LLabel("px"))
        + _cardrow("ovlBackColor", bg_ctrl_widget)
    )
    return grid
