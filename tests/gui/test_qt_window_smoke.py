"""已迁 Qt 窗口的真实构造冒烟。

每个窗口都在离屏环境下完整走一遍 ``__init__``（含保存设置读取、控件搭建、
初始刷新），构造后再核对类源码里读过的 ``self.<attr>`` 是否真的落到了实例上，
用来挡住“旧属性名 / 旧方法名 / 初始化顺序错位”这类迁移残留：

- 读取但从未赋值的成员 → 构造后 ``hasattr`` 为假，测试失败；
- 赋值晚于读取 → 构造期即抛 ``AttributeError``，测试失败。

惰性属性（在某个方法里赋值、只在之后访问）由源码里的赋值语句豁免。
"""

from __future__ import annotations

import ast
import inspect
import os
import textwrap
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from fcmd.gui.qt.app import create_application
from fcmd.gui.qt.toolwindow import QtToolWindow

# (模块, 类名)；构造参数一律用默认值，等价于用户首次 / 常规启动。
WINDOWS: tuple[tuple[str, str], ...] = (
    ("fcmd.apps.cmder._cmder_qt", "CmderQtWindow"),
    ("fcmd.apps.devman._devmanw_qt", "DevmanWindow"),
    ("fcmd.apps.dynacalc._dynacalcw_qt", "DynacalcWindow"),
    ("fcmd.apps.filescan._filescanw_qt", "FilescanQtWindow"),
    ("fcmd.apps.filesync._filesyncw_qt", "FilesyncWindow"),
    ("fcmd.apps.llm._llmw_qt_window", "LlmWindow"),
    ("fcmd.apps.paramcad._paramcadw_qt", "ParamcadWindow"),
    ("fcmd.apps.paramesh._parameshw_qt", "ParameshWindow"),
    ("fcmd.apps.peview._pevieww_qt", "PeviewWindow"),
    ("fcmd.apps.pptmake._pptmakew_qt", "PptmakeQtWindow"),
    ("fcmd.apps.renamer._renamerw_qt", "RenamerWindow"),
    ("fcmd.apps.reshack._reshackw_qt", "ReshackWindow"),
    ("fcmd.apps.wslman._wslmanw_qt", "WslmanQtWindow"),
    ("fcmd.tools.dev._mirrortoolw_qt", "MirrorToolWindow"),
    ("fcmd.tools.media._img2icow_qt", "Img2IcoWindow"),
    ("fcmd.tools.media._ncmconvertw_qt", "NcmConvertWindow"),
    ("fcmd.tools.media._videoconvw_qt", "VideoConvWindow"),
    ("fcmd.tools.office._docdiffw_qt", "DocDiffWindow"),
    ("fcmd.tools.pdf._img2pdfw_qt", "Img2PdfWindow"),
    ("fcmd.tools.pdf._pdfcryptw_qt", "PdfCryptWindow"),
    ("fcmd.tools.pdf._pdfsplitw_qt", "PdfSplitWindow"),
    ("fcmd.tools.system._alarmclock_qt", "AlarmClockQtWindow"),
    ("fcmd.tools.system._checksum_qt", "ChecksumQtWindow"),
    ("fcmd.tools.system._envw_qt", "EnvQtWindow"),
    ("fcmd.tools.text._regexvalidatew_qt", "RegexValidateWindow"),
)


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QApplication:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
    # 构造期可能弹出的模态框在离屏环境会阻塞，统一打桩。
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    return create_application("qt-window-smoke")


def _self_members(cls: type) -> tuple[set[str], set[str]]:
    """返回 ``(读取的成员, 类内赋值过的成员)``；嵌套函数与类一并扫描。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    reads: set[str] = set()
    writes: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"):
            continue
        if isinstance(node.ctx, ast.Store):
            writes.add(node.attr)
        else:
            reads.add(node.attr)
    return reads, writes


@pytest.mark.parametrize(("module_name", "class_name"), WINDOWS, ids=[name for _, name in WINDOWS])
def test_window_constructs_and_initializes(app: QApplication, module_name: str, class_name: str) -> None:
    module = __import__(module_name, fromlist=[class_name])
    cls: Any = getattr(module, class_name)
    window = cls()
    try:
        app.processEvents()
        assert window.windowTitle()
        assert window.centralWidget() is not None
        if isinstance(window, QtToolWindow):
            assert window.body is not None
            assert window.status_text()

        reads, writes = _self_members(cls)
        # 只在方法里惰性赋值、构造期不访问的成员由 writes 豁免。
        missing = sorted(name for name in reads - writes if not hasattr(window, name))
        assert missing == [], f"{class_name} 读取了从未赋值的成员：{missing}"
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
