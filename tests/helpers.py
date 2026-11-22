"""测试共用辅助：DSL 动作输出捕获、框架输出行过滤与 Qt 控件回收。"""

from __future__ import annotations

import re
from typing import Any

import pytest

from fcmd.core.toolkit import run_tool

# 框架进度行（与 fcmd.console 的步骤行约定一致）：
#   $ git add .       命令回显
#   完成  暂存更改  0.02s
#   失败  提交更改  0.02s
#   跳过  上传（上游失败）
#   共 3 步，用时 0.03s
# 注意：``错误:`` / ``提示:`` 属于用户可见的诊断信息，不属于「框架噪音」，
# 不在这里过滤，否则会掩盖真正的失败原因。
_FRAMEWORK_LINE_RE = re.compile(
    r"^(\$ |完成  |失败  |跳过  |共 \d+ 步|  运行  |  目录  |  返回码 |  没有需要提交的更改$)"
)


def strip_framework_lines(text: str) -> str:
    """去掉框架自己的进度行，只留工具的业务输出与诊断信息。"""
    return "\n".join(line for line in text.splitlines() if not _FRAMEWORK_LINE_RE.match(line))


def run_and_capture(name: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """运行工具并返回 (退出码, 过滤掉框架输出行后的业务输出)。"""
    code = run_tool(name, argv)
    return code, strip_framework_lines(capsys.readouterr().out)


def qt_top_levels() -> set[Any]:
    """记录当前进程的顶层 Qt 控件（构造窗口前调用，配合 :func:`destroy_qt_top_levels`）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return set(app.topLevelWidgets()) if app is not None else set()


def destroy_qt_top_levels(before: set[Any]) -> None:
    """销毁本轮新建的顶层 Qt 控件。

    Qt 里 ``close()`` 只隐藏窗口、不销毁对象；而按需构建的页面 / 菜单 / 对话框
    常常没有父控件，只 close 会让它们一直留在 ``QApplication`` 的顶层控件表里。
    累积到上万个控件后，下一次 ``apply_theme`` 的 ``setStyleSheet`` 要为整棵旧树
    重新 polish（实测单次数秒），表现为测试尾部长时间没动静。

    这里只删掉 ``before`` 之后新建的顶层控件，不动会话级 / 其它 fixture 的控件。
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if widget not in before:
            widget.deleteLater()
    # deleteLater 只在事件循环里生效；测试里没有事件循环，显式派发 DeferredDelete。
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
