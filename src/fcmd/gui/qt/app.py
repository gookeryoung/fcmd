"""Qt 应用生命周期：``QApplication`` 复用、高 DPI 策略与统一窗口入口。

所有 Qt 窗口（分页窗口与单页工具窗口）都从这里启动，避免各自
``QApplication(...)`` 时漏掉高 DPI 舍入策略或主题初始化。

- ``argv`` 默认只传程序名：窗口工具不接受命令行参数，把真实 ``sys.argv``
  交给 Qt 会让 ``--help`` / 未知参数被 Qt 抢先解析；
- 高 DPI：Qt6 默认按屏幕缩放，这里把舍入策略设为 ``PassThrough``，避免
  125% / 150% 缩放下窗口尺寸被系统取整后与设计值不一致；
- 主题：启动时读取 ``ui/theme.json`` 并应用一次，之后由窗口内主题菜单切换。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

from fcmd.gui.qt import theme as _qt_theme

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from PySide6.QtWidgets import QWidget

    from fcmd.gui.theme import Theme

__all__ = ["apply_theme", "create_application", "run_window"]


def _configure_high_dpi() -> None:  # pragma: no cover - 需要 QtGui 初始化前调用
    """在创建 ``QApplication`` 前设定高 DPI 舍入策略（幂等）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


def create_application(
    app_name: str = "fcmd",
    argv: Sequence[str] | None = None,
) -> QApplication:  # pragma: no cover - 需要 Qt 事件循环
    """创建（或复用）``QApplication``：应用名与组织名写入 Qt 设置根。"""
    existing = QApplication.instance()
    if existing is not None:
        return existing  # type: ignore[return-value]
    _configure_high_dpi()
    app = QApplication(list(argv) if argv is not None else [sys.argv[0]])
    app.setApplicationName(app_name)
    app.setOrganizationName("fcmd")
    return app


def apply_theme(app: QApplication, theme: Theme | None = None) -> Theme:  # pragma: no cover - 需要 Qt
    """把主题应用到应用实例；``theme`` 省略时读取持久化选择。"""
    chosen = theme if theme is not None else _qt_theme.load_theme()
    _qt_theme.apply_theme(app, chosen)
    return chosen


def run_window(
    factory: Callable[[], QWidget],
    *,
    app_name: str,
    argv: Sequence[str] | None = None,
) -> int:  # pragma: no cover - 需要 Qt 事件循环
    """创建应用与窗口，显示后进入事件循环，返回退出码。"""
    app = create_application(app_name, argv)
    apply_theme(app)
    window = factory()
    window.show()
    return int(app.exec())
