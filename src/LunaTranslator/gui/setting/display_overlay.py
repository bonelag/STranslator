from qtsymbols import *
import functools
import json
from pathlib import Path
from gui.usefulwidget import (
    D_getspinbox,
    D_getcolorbutton,
    D_getsimpleswitch,
    getboxlayout,
    FocusFontCombo
)
from myutils.config import _TR, globalconfig
import ovl
ovl.load_config()

_bg_widgets = {}


def save_overlay_config():
    config_path = Path("userconfig/overlay.json")
    if not config_path.parent.exists():
        config_path.parent.mkdir(parents=True)

    save_data = {k: v for k, v in ovl.CONFIG.items() if k in [
        "enable", "show_in_main", "text_color", "stroke_color", "stroke_width",
        "min_font_size", "max_font_size", "font_family",
        "background_color", "timeout_ms",
        "horizontal_padding", "vertical_padding",
        "screen_capture_protection",
        "auto_background",
        "auto_text_color",
        "auto_font_weight",
    ]}

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=4)


def update_color_with_opacity(self, color_key, rgb_key, alpha_key):
    color = QColor(ovl.CONFIG.get(rgb_key, "#000000"))
    alpha = ovl.CONFIG.get(alpha_key, 255)
    ovl.CONFIG[color_key] = f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha/255:.2f})"
    save_overlay_config()


def create_opacity_slider_generic(self, color_key, rgb_key, alpha_key, slider_width=None):
    slider = QSlider(Qt.Orientation.Horizontal)
    if slider_width is not None:
        slider.setFixedWidth(slider_width)
    slider.setRange(0, 255)

    current_color = ovl.CONFIG.get(color_key, "rgba(0, 0, 0, 1)")
    current_alpha = 255
    try:
        c = ovl.parse_color(current_color)
        current_alpha = c.alpha()
    except:
        pass

    ovl.CONFIG[alpha_key] = current_alpha
    slider.setValue(current_alpha)

    label = QLabel(f"{int(current_alpha/255*100)}%")

    def on_change(val):
        ovl.CONFIG[alpha_key] = val
        label.setText(f"{int(val/255*100)}%")
        update_color_with_opacity(self, color_key, rgb_key, alpha_key)

    slider.valueChanged.connect(on_change)

    return getboxlayout([slider, label])


def overlaysetting(self):
    def generic_save(_):
        save_overlay_config()

    for color_key, rgb_key in [
        ("background_color", "_bg_rgb"),
        ("text_color", "_text_rgb"),
        ("stroke_color", "_stroke_rgb")
    ]:
        current_val = ovl.CONFIG.get(color_key, "rgba(0, 0, 0, 1)")
        try:
            c = ovl.parse_color(current_val)
            ovl.CONFIG[rgb_key] = c.name()
        except:
            ovl.CONFIG[rgb_key] = "#000000"

    box_width = 250

    bg_color_btn = D_getcolorbutton(
        self, ovl.CONFIG, "_bg_rgb",
        callback=lambda _: update_color_with_opacity(
            self, "background_color", "_bg_rgb", "_bg_alpha"
        )
    )()
    _bg_widgets["bg_color_btn"] = bg_color_btn

    bg_opacity_slider = create_opacity_slider_generic(
        self, "background_color", "_bg_rgb", "_bg_alpha"
    )
    _bg_widgets["bg_opacity_slider"] = bg_opacity_slider

    auto_switch = D_getsimpleswitch(ovl.CONFIG, "auto_background", callback=None)()

    bg_opacity_and_auto = QWidget()
    bg_opacity_and_auto_lay = QHBoxLayout(bg_opacity_and_auto)
    bg_opacity_and_auto_lay.setContentsMargins(0, 0, 0, 0)
    bg_opacity_and_auto_lay.setSpacing(6)
    bg_opacity_and_auto_lay.addLayout(bg_opacity_slider)
    bg_opacity_and_auto_lay.addSpacing(0)
    bg_opacity_and_auto_lay.addWidget(QLabel(_TR("Auto")))
    bg_opacity_and_auto_lay.addWidget(auto_switch)
    bg_opacity_and_auto_lay.addSpacing(30)

    def on_auto_toggle(val):
        ovl.CONFIG["auto_background"] = int(val)
        bg_opacity_slider.setEnabled(not val)
        bg_color_btn.setEnabled(not val)
        save_overlay_config()

    auto_switch.toggled.connect(on_auto_toggle)
    on_auto_toggle(auto_switch.isChecked())

    text_color_btn = D_getcolorbutton(
        self, ovl.CONFIG, "_text_rgb",
        callback=lambda _: update_color_with_opacity(
            self, "text_color", "_text_rgb", "_text_alpha"
        )
    )()

    text_opacity_slider = create_opacity_slider_generic(
        self, "text_color", "_text_rgb", "_text_alpha"
    )

    auto_text_switch = D_getsimpleswitch(ovl.CONFIG, "auto_text_color", callback=None)()

    text_opacity_and_auto = QWidget()
    text_opacity_and_auto_lay = QHBoxLayout(text_opacity_and_auto)
    text_opacity_and_auto_lay.setContentsMargins(0, 0, 0, 0)
    text_opacity_and_auto_lay.setSpacing(6)
    text_opacity_and_auto_lay.addLayout(text_opacity_slider)
    text_opacity_and_auto_lay.addSpacing(0)
    text_opacity_and_auto_lay.addWidget(QLabel(_TR("Auto")))
    text_opacity_and_auto_lay.addWidget(auto_text_switch)
    text_opacity_and_auto_lay.addSpacing(30)

    def on_auto_text_toggle(val):
        ovl.CONFIG["auto_text_color"] = int(val)
        text_opacity_slider.setEnabled(not val)
        text_color_btn.setEnabled(not val)
        save_overlay_config()

    auto_text_switch.toggled.connect(on_auto_text_toggle)
    on_auto_text_toggle(auto_text_switch.isChecked())

    import gobject
    lang = globalconfig.get("languageuse2", "zh")
    if lang == "vi":
        show_in_main_text = "Hiển thị bản dịch trên cửa sổ chính"
    elif lang == "zh":
        show_in_main_text = "在主窗口显示翻译"
    else:
        show_in_main_text = "Show translation on main window"

    show_in_main_row = [
        QLabel(show_in_main_text),
        (QWidget(), 0),
        _create_switch_row(
            ovl.CONFIG, "show_in_main",
            callback=lambda x: (
                ovl.CONFIG.update({"show_in_main": int(x)}),
                save_overlay_config(),
                gobject.base.translation_ui.update_main_window_translation_display()
                if hasattr(gobject, "base") and hasattr(gobject.base, "translation_ui") else None
            )
        )
    ]

    if lang == "vi":
        auto_weight_text = "Tự động độ đậm phông theo văn bản gốc"
    elif lang == "zh":
        auto_weight_text = "根据原文自动字体粗细"
    else:
        auto_weight_text = "Auto font weight from source text"

    auto_font_weight_row = [
        QLabel(auto_weight_text),
        (QWidget(), 0),
        _create_switch_row(
            ovl.CONFIG, "auto_font_weight",
            callback=lambda x: (
                ovl.CONFIG.update({"auto_font_weight": int(x)}),
                save_overlay_config(),
            ),
        ),
    ]

    manual_rows = [
        show_in_main_row,
        auto_font_weight_row,
        [
            _TR("ovlTextColor"),
            text_color_btn,
            (QWidget(), 0),
            _TR("ovlTextOpacity"),
            text_opacity_and_auto,
        ],
        [
            _TR("ovlStrokeColor"),
            D_getcolorbutton(self, ovl.CONFIG, "_stroke_rgb", callback=lambda _: update_color_with_opacity(self, "stroke_color", "_stroke_rgb", "_stroke_alpha")),
            (QWidget(), 0),
            _TR("ovlTextOpacity"),
            functools.partial(create_opacity_slider_generic, self, "stroke_color", "_stroke_rgb", "_stroke_alpha"),
        ],
        [
            _TR("ovlBackColor"),
            bg_color_btn,
            (QWidget(), 0),
            _TR("ovlTextOpacity"),
            bg_opacity_and_auto,
        ],
        [
            _TR("ovlStrokeWidth"),
            (QWidget(), 0),
            _right_layout(D_getspinbox(0, 10, ovl.CONFIG, "stroke_width", double=True, step=0.5, callback=generic_save), "px", fixed_width=box_width),
        ],
        [
            _TR("ovlTextSizeMin"),
            (QWidget(), 0),
            _right_layout(D_getspinbox(1, 100, ovl.CONFIG, "min_font_size", double=True, step=0.5, callback=generic_save), "px", fixed_width=box_width),
        ],
        [
            _TR("ovlTextSizeMax"),
            (QWidget(), 0),
            _right_layout(D_getspinbox(1, 200, ovl.CONFIG, "max_font_size", double=True, step=0.5, callback=generic_save), "px", fixed_width=box_width),
        ],
        [
            _TR("ovlTextFont"),
            (QWidget(), 0),
            _right_layout(_create_font_combo(), "", fixed_width=box_width),
        ],
        [
            _TR("ovlPaddingH"),
            (QWidget(), 0),
            _right_layout(D_getspinbox(0, 50, ovl.CONFIG, "horizontal_padding", double=True, step=0.5, callback=generic_save), "px", fixed_width=box_width),
        ],
        [
            _TR("ovlPaddingV"),
            (QWidget(), 0),
            _right_layout(D_getspinbox(0, 50, ovl.CONFIG, "vertical_padding", double=True, step=0.5, callback=generic_save), "px", fixed_width=box_width),
        ],
        [
            _TR("ovlTimeout"),
            (QWidget(), 0),
            _right_layout(D_getspinbox(100, 60000, ovl.CONFIG, "timeout_ms", callback=generic_save), "ms", fixed_width=box_width),
        ],
    ]

    return [
        [
            dict(
                title=_TR("浮窗叠加"),
                type="grid",
                grid=[
                    [
                        _TR("ovlEnable"),
                        (QWidget(), 0),
                        _create_switch_row(ovl.CONFIG, "enable", callback=lambda x: (ovl.CONFIG.update({"enable": int(x)}), save_overlay_config())),
                    ],
                    *manual_rows,
                ]
            )
        ]
    ]


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


def _right_layout(w, unit="", fixed_width=None, unit_width=30):
    if callable(w):
        w = w()
    if fixed_width:
        w.setFixedWidth(fixed_width)
    bg = QWidget()
    lay = QHBoxLayout(bg)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch()
    lay.addWidget(w)
    unit_label = QLabel(unit)
    unit_label.setFixedWidth(unit_width)
    unit_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    lay.addWidget(unit_label)
    return bg


def _create_switch_row(d, key, callback=None):
    switch = D_getsimpleswitch(d, key, callback=callback)()
    anchor = QWidget()
    lay = QHBoxLayout(anchor)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch()
    lay.addWidget(switch)
    lay.addSpacing(30)
    return anchor
