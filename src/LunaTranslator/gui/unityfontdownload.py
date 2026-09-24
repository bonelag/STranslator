import os
import shutil
import zipfile
import requests, gobject
from qtsymbols import *
from myutils.config import _TR, dynamiclink
from myutils.utils import stringfyerror, format_bytes
from myutils.proxy import getproxy
from myutils.wrapper import threader
from gui.dynalang import LPushButton, LDialog, LLabel
from gui.usefulwidget import VisLFormLayout
from gui.RichMessageBox import RichMessageBox

RESOURCE = "Resource/TMP_Font_AssetBundles_2025-12-08.zip"
ARCHIVENAME = "TMP_Font_AssetBundles_2025-12-08.zip"
BUNDLEFOLDER = "TMP_Font_AssetBundles_2025-12-08"


class UnityFontDownloadDialog(LDialog):
    progresssetval = pyqtSignal(str, int)
    installsucc = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setWindowTitle("下载Unity字体")
        self.url = dynamiclink(RESOURCE)
        self.progresssetval.connect(self._progresssetval)
        self.installsucc.connect(self._installsucc)
        formLayout = VisLFormLayout(self)
        label = LLabel(
            "未检测到Unity TMP字体文件。内嵌翻译修改游戏字体需要这些文件，请点击下载："
        )
        label.setWordWrap(True)
        formLayout.addRow(label)
        self.downloadbtn = LPushButton("下载")
        self.downloadbtn.clicked.connect(self.downloadauto)
        formLayout.addRow(self.downloadbtn)
        self.downloadprogress = QProgressBar()
        self.downloadprogress.setRange(0, 10000)
        self.downloadprogress.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        formLayout.addRow(self.downloadprogress)
        self.formLayout = formLayout
        self._progressrow = formLayout.rowCount() - 1
        formLayout.setRowVisible(self._progressrow, False)
        self.resize(520, 1)

    def _progresssetval(self, text, val):
        self.formLayout.setRowVisible(self._progressrow, True)
        self.downloadprogress.setValue(val)
        self.downloadprogress.setFormat(text)

    def _installsucc(self, succ, failreason):
        self.downloadbtn.setEnabled(True)
        if succ:
            self.accept()
        else:
            self.formLayout.setRowVisible(self._progressrow, False)
            RichMessageBox(
                self,
                _TR("错误"),
                _TR("下载/解压失败")
                + "\n"
                + _TR("请手动下载并解压到软件目录下")
                + "\n"
                + '<a href="{}">{}</a>'.format(self.url, ARCHIVENAME),
            )

    def _walk_find(self, base: str):
        if not base or not os.path.isdir(base):
            return ""
        for _root, _dirs, _files in os.walk(base):
            for _f in _files:
                if _f.startswith("arialuni_sdf_u"):
                    return _root
        return ""

    @threader
    def downloadxSafe(self):
        try:
            self.progresssetval.emit("……", 0)
            archive = gobject.gettempdir(ARCHIVENAME)
            req = requests.get(self.url, stream=True, proxies=getproxy())
            size = int(req.headers.get("Content-Length", 0) or 0)
            szstr = format_bytes(size) if size else "?"
            file_size = 0
            with open(archive, "wb") as ff:
                for chunk in req.iter_content(chunk_size=1024 * 32):
                    ff.write(chunk)
                    file_size += len(chunk)
                    if size:
                        prg = int(10000 * file_size / size)
                        self.progresssetval.emit(
                            _TR("{}/{}_进度_{:0.2f}%").format(
                                format_bytes(file_size), szstr, prg / 100
                            ),
                            prg,
                        )
            self.progresssetval.emit(_TR("正在解压"), 10000)
            target = gobject.getcachedir(BUNDLEFOLDER)
            shutil.rmtree(target, ignore_errors=True)
            with zipfile.ZipFile(archive) as z:
                z.extractall(target)
            d = self._walk_find(target)
            if not d:
                raise Exception()
            self.installsucc.emit(True, "")
        except Exception as e:
            self.installsucc.emit(False, stringfyerror(e))

    def downloadauto(self):
        self.downloadbtn.setEnabled(False)
        self.formLayout.setRowVisible(self._progressrow, True)
        self.downloadxSafe()
