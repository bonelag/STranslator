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
    NoWheelSlider,
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
    slider = NoWheelSlider(Qt.Orientation.Horizontal)
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


def _srow_widget(title):
    lab = LLabel(title)
    font = lab.font()
    font.setBold(True)
    font.setPixelSize(15)
    lab.setFont(font)
    lab.setContentsMargins(0, 8, 0, 0)
    return lab


def _srow(title):
    return [[(_srow_widget(title), 0)]]


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

    generic_save = lambda _: save_overlay_config()

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
    stroke_width_box = D_getspinbox(
        0,
        10,
        ovl.CONFIG,
        "stroke_width",
        double=True,
        step=0.5,
        callback=generic_save,
    )()
    stroke_width_box.setFixedWidth(85)

    stroke_ctrl_widget = QWidget()
    stroke_ctrl_lay = QHBoxLayout(stroke_ctrl_widget)
    stroke_ctrl_lay.setContentsMargins(0, 0, 0, 0)
    stroke_ctrl_lay.setSpacing(8)
    stroke_ctrl_lay.addWidget(stroke_color_btn)
    stroke_ctrl_lay.addLayout(stroke_opacity_slider)
    stroke_ctrl_lay.addSpacing(6)
    stroke_ctrl_lay.addWidget(stroke_width_box)
    stroke_ctrl_lay.addWidget(LLabel("px"))

    content_container = QWidget()
    c_lay = QVBoxLayout(content_container)
    c_lay.setContentsMargins(0, 0, 0, 0)
    c_lay.setSpacing(8)

    enable_switch = D_getsimpleswitch(
        ovl.CONFIG,
        "enable",
        callback=lambda x: (
            ovl.CONFIG.update({"enable": int(x)}),
            save_overlay_config(),
            content_container.setVisible(bool(x)),
        ),
    )()
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
    capture_protect_switch = D_getsimpleswitch(
        ovl.CONFIG,
        "screen_capture_protection",
        default=1,
        callback=lambda x: (
            ovl.CONFIG.update({"screen_capture_protection": int(x)}),
            save_overlay_config(),
            [ovl.set_capture_affinity(o, bool(x)) for o in ovl._overlays],
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

    c_lay.addWidget(makecardrow("ovlShowInMain", show_main_switch))
    c_lay.addWidget(makecardrow("ovlCaptureProtection", capture_protect_switch))
    c_lay.addWidget(makecardrow("ovlTimeout", timeout_box, LLabel("ms")))
    c_lay.addWidget(_srow_widget("文本与排版"))
    c_lay.addWidget(makecardrow("ovlAdaptiveSize", adaptive_size_switch))
    c_lay.addWidget(makecardrow("ovlTextSizeMin", min_size_box, LLabel("px")))
    c_lay.addWidget(makecardrow("ovlTextSizeMax", max_size_box, LLabel("px")))
    c_lay.addWidget(makecardrow("ovlAutoWeight", auto_weight_switch))
    c_lay.addWidget(makecardrow("ovlAutoFamily", auto_family_switch))
    c_lay.addWidget(makecardrow("ovlTextFont", _create_font_combo))
    c_lay.addWidget(makecardrow("ovlPaddingH", h_padding_box, LLabel("px")))
    c_lay.addWidget(makecardrow("ovlPaddingV", v_padding_box, LLabel("px")))
    c_lay.addWidget(_srow_widget("颜色与外观"))
    c_lay.addWidget(makecardrow("ovlTextColor", text_ctrl_widget))
    c_lay.addWidget(makecardrow("ovlStrokeColor", stroke_ctrl_widget))
    c_lay.addWidget(makecardrow("ovlBackColor", bg_ctrl_widget))

    content_container.setVisible(bool(ovl.CONFIG.get("enable", 1)))

    grid = (
        _srow("常规")
        + _cardrow("ovlEnable", enable_switch)
        + [[(content_container, 0)]]
    )
    return grid
