from qtsymbols import *
import functools, re
from myutils.config import globalconfig, static_data, _TR, dynamiclink
from myutils.wrapper import threader
from myutils.utils import makehtml, getlanguse
import requests, importlib
import gobject
import os, NativeUtils
from traceback import print_exc
from gui.usefulwidget import (
    D_getsimpleswitch,
    makescrollgrid,
    createfoldgrid,
    SuperCombo,
    getsmalllabel,
    getboxlayout,
    NQGroupBox,
    LinkLabel,
    SClickableLabel,
    VisLFormLayout,
    D_getIconButton,
    tabadd_lazy,
)
from gui.setting.setting_year import yearsummary
from language import UILanguages, Languages
from myutils.updater import versionchecktask


def createversionlabel():

    versionlabel = LinkLabel()
    versionlabel.setOpenExternalLinks(False)
    versionlabel.linkActivated.connect(lambda _: os.startfile(dynamiclink("ChangeLog")))

    gobject.base.connectsignal(
        gobject.base.versiontextsignal,
        functools.partial(versionlabelmaybesettext, versionlabel),
    )
    return versionlabel


def versionlabelmaybesettext(versionlabel: QLabel, x):
    x = '<a href="fuck">{}</a>'.format(x)
    versionlabel.setText(x)


def delayloadlinks(key):
    sources: "list[dict]" = static_data["aboutsource"][key]
    grid = []
    for source in sources:
        link = source.get("link")
        if link:
            grid.append(
                [
                    source.get("name", ""),
                    (makehtml(link, source.get("vis", None)), 2),
                    source.get("about", ""),
                ]
            )
            continue
        __grid = []
        function = source.get("function")
        if function:
            try:
                func = getattr(
                    importlib.import_module(function[0]),
                    function[1],
                )
                __grid.append([(func, 0)])
            except:
                print_exc()
        else:
            for link in source["links"]:
                __grid.append(
                    [
                        link["name"],
                        (makehtml(link["link"], link.get("vis", None)), 2),
                    ]
                    + ([link.get("about")] if link.get("about") else [])
                )

        grid.append([dict(title=source.get("name", None), type="grid", grid=__grid)])
    return grid


def offlinelinks(key):
    box = createfoldgrid(delayloadlinks(key), "资源下载")
    return box


def changeUIlanguage(_):
    languageChangeEvent = QEvent(QEvent.Type.LanguageChange)
    QApplication.sendEvent(QApplication.instance(), languageChangeEvent)
    try:
        gobject.base.textsource.setlang()
    except:
        pass


def updatexx(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "autoupdate",
                callback=lambda _: (
                    versionchecktask.put(_),
                    (
                        self.aboutlayout.layout().setRowVisible(6, False)
                        if not _
                        else ""
                    ),
                ),
                default=True,
            ),
            getsmalllabel(""),
            getsmalllabel("最新版本"),
            createversionlabel,
            "",
        ]
    )


def debugocr_widget(self, callback):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr",
                default=False,
                callback=callback,
            ),
            "",
        ]
    )


def debugocr_show_para_widget(self, callback):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_show_para",
                default=True,
                callback=callback,
            ),
            "",
        ]
    )


def debugocr_fill_para_widget(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_fill_para",
                default=False,
            ),
            "",
        ]
    )


def debugocr_fill_text_para_widget(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_fill_text_para",
                default=False,
            ),
            "",
        ]
    )


def debugocr_show_line_widget(self, callback):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_show_line",
                default=True,
                callback=callback,
            ),
            "",
        ]
    )


def debugocr_fill_line_widget(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_fill_line",
                default=False,
            ),
            "",
        ]
    )


def debugocr_fill_text_line_widget(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_fill_text_line",
                default=False,
            ),
            "",
        ]
    )


def debugocr_show_title_widget(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_show_title",
                default=True,
            ),
            "",
        ]
    )


def debugocr_show_word_widget(self, callback):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_show_word",
                default=True,
                callback=callback,
            ),
            "",
        ]
    )


def debugocr_fill_word_widget(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_fill_word",
                default=False,
            ),
            "",
        ]
    )


def debugocr_fill_text_word_widget(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_fill_text_word",
                default=False,
            ),
            "",
        ]
    )


def debugocr_detect_font_widget(self):
    return getboxlayout(
        [
            D_getsimpleswitch(
                globalconfig,
                "debugocr_detect_font",
                default=False,
            ),
            "",
        ]
    )


def progress___(self):

    downloadprogress = QProgressBar(self)
    downloadprogress.setRange(0, 10000)
    downloadprogress.setAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    self.downloadprogress = downloadprogress
    return downloadprogress


def _progresssignal4(
    updatelayout: VisLFormLayout, downloadprogress: QProgressBar, text, val
):
    downloadprogress.setValue(val)
    downloadprogress.setFormat(text)
    if (val or text) and globalconfig.get("autoupdate", True):
        updatelayout.setRowVisible(6, True)


class MDLabel(LinkLabel):
    def setMD(self, md, static=True):
        self._md = md
        self.static = static
        self.updatelangtext()

    def __init__(self, md: str, static=False):
        super().__init__()
        self._md = md
        self.static = static
        self.setWordWrap(True)
        self.updatelangtext()

    def updatelangtext(self):
        self.setText(
            NativeUtils.Markdown2Html(self._md if self.static else _TR(self._md))
        )


class MDLabel1(MDLabel):
    def __init__(self, md):
        super().__init__(md, True)
        self.setOpenExternalLinks(False)
        self.linkActivated.connect(
            lambda link: gobject.base.aboutlinkclicked(link, self.window())
        )

    def setText(self, t):
        t = re.sub('<a href="WEIXIN".*?>(.*?)</a>', "\\1", t)
        super().setText(t)


def get_about_info():
    lang = getlanguse()
    t3 = "如果使用中遇到困难，可以查阅[使用说明](/)、观看[我的B站视频](https://space.bilibili.com/592120404/video)，也欢迎加入[QQ群](https://lunatranslator.org/Resource/QQGroup)。"
    t2 = "软件维护不易，如果您感觉该软件对你有帮助，欢迎通过[爱发电](https://afdian.com/a/HIllya51)，或[微信扫码](WEIXIN)赞助，您的支持将成为软件长期维护的助力，谢谢~"
    t5 = "如果使用中遇到困難，可以查閱[使用說明](/)、觀看[我的 B 站影片](https://space.bilibili.com/592120404/video)，也歡迎加入 [Discord](https://discord.com/invite/ErtDwVeAbhtB)／[QQ 群](https://lunatranslator.org/Resource/QQGroup)。"
    t6 = "如果使用中遇到困难，可以查阅[使用说明](/)，也欢迎加入[Discord](https://discord.com/invite/ErtDwVeAbB)。"
    t4 = "软件维护不易，如果您感觉该软件对你有帮助，欢迎通过[patreon](https://patreon.com/HIllya51)支持我，您的支持将成为软件长期维护的助力，谢谢~"
    if lang == Languages.Chinese:
        return "\n\n".join([t3, t2])

    elif lang == Languages.TradChinese:
        return "\n\n".join([t5, _TR(t4)])
    else:
        return _TR("\n\n".join([t6, t4]))


def load_scaled_pixmap(
    file_path: str,
    target_width: int,
    device_pixel_ratio: float = 1.0,
) -> QPixmap:

    if file_path.endswith(".svg"):
        renderer = QSvgRenderer(file_path)
        physical_width = int(target_width * device_pixel_ratio)
        size = renderer.defaultSize()
        size.scale(physical_width, int(1e6), Qt.AspectRatioMode.KeepAspectRatio)
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size.width(), size.height()))
        painter.end()

        pixmap.setDevicePixelRatio(device_pixel_ratio)
        return pixmap
    else:
        img = QPixmap.fromImage(QImage(file_path))
        img.setDevicePixelRatio(device_pixel_ratio)
        img = img.scaledToWidth(
            int(target_width * device_pixel_ratio),
            Qt.TransformationMode.SmoothTransformation,
        )
        return img


class aboutwidget(NQGroupBox):
    def __init__(self, *a):
        super().__init__(*a)
        self.grid = QFormLayout(self)
        self.labels: "list[QWidget]" = []
        self.mdlabel = MDLabel1("")
        self.grid.addRow(self.mdlabel)
        self.updatelangtext()

    def createlabel(self, img: str, w, link=None):
        if link:
            lb = SClickableLabel()
            lb.clicked.connect(lambda: os.startfile(link))
        else:
            lb = QLabel()
        sp = lb.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Policy.Fixed)
        lb.setSizePolicy(sp)
        img = load_scaled_pixmap(img, w, self.devicePixelRatioF())
        lb.setPixmap(img)
        self.labels.append(lb)
        self.grid.addRow(lb)

    def updatelangtext(self):
        self.mdlabel.setMD(get_about_info())
        lang = getlanguse()
        for _ in self.labels:
            _.deleteLater()
        self.labels.clear()
        if lang == Languages.Chinese:
            self.createlabel(
                "files/static/button-sponsorme.png",
                200,
                "https://afdian.com/a/HIllya51",
            )
            self.createlabel("files/static/zan.jpg", 300)
        elif lang == Languages.TradChinese:
            self.createlabel(
                "files/static/become_a_patron_4x1_black_logo_white_text_on_coral.svg",
                200,
                "https://patreon.com/HIllya51",
            )
        else:
            self.createlabel(
                "files/static/become_a_patron_4x1_black_logo_white_text_on_coral.svg",
                200,
                "https://patreon.com/HIllya51",
            )


class delayloadsvg(QSvgWidget):
    def __init__(self, REPO):
        super().__init__()
        self.REPO = REPO
        link = "https://img.shields.io/github/license/" + REPO
        self._load(link)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, _: QMouseEvent):
        if _.button() == Qt.MouseButton.LeftButton:
            os.startfile("https://github.com/{repo}".format(repo=self.REPO))

    def event(self, a0: QEvent) -> bool:
        if a0.type() == QEvent.Type.FontChange:
            self.loadh()
        return super().event(a0)

    def loadh(self):
        h = QFontMetricsF(self.font(), self).height()
        renderer = self.renderer()
        if renderer != None:
            size = renderer.defaultSize()
            self.setFixedSize(QSizeF(size.width() * h / size.height(), h).toSize())

    @threader
    def _load(self, link):
        self.load(requests.get(link).content)
        self.loadh()


def makelink(repo):
    return [
        functools.partial(delayloadsvg, repo),
        functools.partial(
            LinkLabel,
            '<a href="https://github.com/{repo}">{repo}</a>'.format(repo=repo),
        ),
    ]


class __delayloadlangs(QHBoxLayout):
    def __init__(self):
        super().__init__()
        self.como = SuperCombo(static=True)
        self.como.addItem(Languages.fromcode(globalconfig["languageuse2"]).nativename)
        # Qt6的脑残fontmerging机制导致变得很慢。
        QTimer.singleShot(0, self.delayload)
        self.addWidget(self.como)

    def delayload(self):
        self.como.clear()
        inner, vis = [_.code for _ in UILanguages], [_.nativename for _ in UILanguages]
        self.como.addItems(vis, inner)
        self.como.setCurrentData(globalconfig["languageuse2"])
        self.como.currentIndexChanged.connect(
            lambda _: (
                globalconfig.__setitem__("languageuse2", self.como.getCurrentData()),
                changeUIlanguage(0),
            )
        )


def setTab_about(self: QWidget, basel):
    def ____():
        tabadd_lazy(
            self.tab_widget, _TR("年度总结"), functools.partial(yearsummary, self)
        )
        self.tab_widget.adjust_list_widget_width()

    from language import Languages
    lang = getlanguse()
    if lang == Languages.Vietnamese:
        lbl_debug = "Chế độ debug OCR"
        lbl_para = "  └─ Hiện viền đoạn"
        lbl_fill_para = "      └─ Đổ màu nền đoạn"
        lbl_fill_text_para = "      └─ Đổ màu chữ đoạn"
        lbl_line = "  └─ Hiện viền dòng"
        lbl_fill_line = "      └─ Đổ màu nền dòng"
        lbl_fill_text_line = "      └─ Đổ màu chữ dòng"
        lbl_show_title = "      └─ Vẽ tiêu đề"
        lbl_word = "  └─ Hiện viền chữ"
        lbl_fill_word = "      └─ Đổ màu nền chữ"
        lbl_fill_text_word = "      └─ Đổ màu chữ của chữ"
        lbl_detect_font = "  └─ Hiện phông chữ"
    else:
        lbl_debug = "OCR Debug Mode"
        lbl_para = "  └─ Show Paragraph Border"
        lbl_fill_para = "      └─ Fill Paragraph Background"
        lbl_fill_text_para = "      └─ Fill Paragraph Text"
        lbl_line = "  └─ Show Line Border"
        lbl_fill_line = "      └─ Fill Line Background"
        lbl_fill_text_line = "      └─ Fill Line Text"
        lbl_show_title = "      └─ Draw Title Border"
        lbl_word = "  └─ Show Word Border"
        lbl_fill_word = "      └─ Fill Word Background"
        lbl_fill_text_word = "      └─ Fill Word Text"
        lbl_detect_font = "  └─ Show Font Name"

    def update_debug_rows_visibility(_=None):
        if not hasattr(self, "aboutlayout"):
            return
        debug_on = globalconfig.get("debugocr", False)
        layout = self.aboutlayout.layout()
        if not debug_on:
            for r in range(3, 14):
                layout.setRowVisible(r, False)
        else:
            show_para = globalconfig.get("debugocr_show_para", True)
            show_line = globalconfig.get("debugocr_show_line", True)
            show_word = globalconfig.get("debugocr_show_word", True)
            
            layout.setRowVisible(3, True)
            layout.setRowVisible(4, show_para)
            layout.setRowVisible(5, show_para)
            
            layout.setRowVisible(6, True)
            layout.setRowVisible(7, show_line)
            layout.setRowVisible(8, show_line)
            layout.setRowVisible(9, show_line)
            
            layout.setRowVisible(10, True)
            layout.setRowVisible(11, show_word)
            layout.setRowVisible(12, show_word)
            
            layout.setRowVisible(13, True)

    hiderows_init = [14]
    if not globalconfig.get("debugocr", False):
        hiderows_init.extend(list(range(3, 14)))
    else:
        show_para = globalconfig.get("debugocr_show_para", True)
        show_line = globalconfig.get("debugocr_show_line", True)
        show_word = globalconfig.get("debugocr_show_word", True)
        if not show_para:
            hiderows_init.extend([4, 5])
        if not show_line:
            hiderows_init.extend([7, 8, 9])
        if not show_word:
            hiderows_init.extend([11, 12])

    makescrollgrid(
        [
            [
                dict(
                    name="aboutlayout",
                    parent=self,
                    hiderows=hiderows_init,
                    grid=[
                        ["UI语言", __delayloadlangs],
                        ["自动更新", functools.partial(updatexx, self)],
                        [QLabel(lbl_debug), functools.partial(debugocr_widget, self, update_debug_rows_visibility)],
                        [QLabel(lbl_para), functools.partial(debugocr_show_para_widget, self, update_debug_rows_visibility)],
                        [QLabel(lbl_fill_para), functools.partial(debugocr_fill_para_widget, self)],
                        [QLabel(lbl_fill_text_para), functools.partial(debugocr_fill_text_para_widget, self)],
                        [QLabel(lbl_line), functools.partial(debugocr_show_line_widget, self, update_debug_rows_visibility)],
                        [QLabel(lbl_fill_line), functools.partial(debugocr_fill_line_widget, self)],
                        [QLabel(lbl_fill_text_line), functools.partial(debugocr_fill_text_line_widget, self)],
                        [QLabel(lbl_show_title), functools.partial(debugocr_show_title_widget, self)],
                        [QLabel(lbl_word), functools.partial(debugocr_show_word_widget, self, update_debug_rows_visibility)],
                        [QLabel(lbl_fill_word), functools.partial(debugocr_fill_word_widget, self)],
                        [QLabel(lbl_fill_text_word), functools.partial(debugocr_fill_text_word_widget, self)],
                        [QLabel(lbl_detect_font), functools.partial(debugocr_detect_font_widget, self)],
                        [functools.partial(progress___, self)],
                    ],
                ),
            ],
            [aboutwidget],
            [
                functools.partial(
                    createfoldgrid,
                    [
                        [
                            functools.partial(
                                delayloadsvg,
                                "HIllya51/LunaTranslator",
                            ),
                            functools.partial(
                                MDLabel,
                                "[LunaTranslator](https://github.com/HIllya51/LunaTranslator)使用[GPLv3](https://github.com/HIllya51/LunaTranslator/blob/main/LICENSE)许可证。",
                            ),
                        ],
                        [("引用的项目", -1)],
                        makelink("opencv/opencv"),
                        makelink("microsoft/onnxruntime"),
                        makelink("Artikash/Textractor"),
                        makelink("RapidAI/RapidOcrOnnx"),
                        makelink("PaddlePaddle/PaddleOCR"),
                        makelink("Blinue/Magpie"),
                        makelink("nanokina/ebyroid"),
                        makelink("xupefei/Locale-Emulator"),
                        makelink("InWILL/Locale_Remulator"),
                        makelink("zxyacb/ntlea"),
                        makelink("Chuyu-Team/YY-Thunks"),
                        makelink("Chuyu-Team/VC-LTL5"),
                        makelink("uyjulian/AtlasTranslate"),
                        makelink("ilius/pyglossary"),
                        makelink("ikegami-yukino/mecab"),
                        makelink("AngusJohnson/Clipper2"),
                        makelink("rapidfuzz/rapidfuzz-cpp"),
                        makelink("TsudaKageyu/minhook"),
                        makelink("lobehub/lobe-icons"),
                        makelink("kokke/tiny-AES-c"),
                        makelink("AuroraWright/owocr"),
                        makelink("b1tg/win11-oneocr"),
                        makelink("mity/md4c"),
                        makelink("swigger/wechat-ocr"),
                        makelink("rupeshk/MarkdownHighlighter"),
                        makelink("sindresorhus/github-markdown-css"),
                        makelink("gexgd0419/NaturalVoiceSAPIAdapter"),
                        makelink("microsoft/PowerToys"),
                        makelink("WaterJuice/WjCryptLib"),
                        makelink("dpirch/libfvad"),
                        makelink("chromium/chromium"),
                    ],
                    "LICENSE",
                )
            ],
            # [getboxlayout([D_getIconButton(____, icon="fa.calendar"), ""])],
        ],
        basel,
    )
    gobject.base.connectsignal(
        gobject.base.progresssignal4,
        functools.partial(
            _progresssignal4, self.aboutlayout.layout(), self.downloadprogress
        ),
    )
