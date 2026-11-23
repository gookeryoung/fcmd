"""Qt 单页工具窗口骨架：菜单 / 状态栏 / 几何记忆 / 主题 / 日志。

各单页工具窗口共用这一套骨架（菜单 / 状态栏 / 几何记忆 / 主题 / 日志）。分页窗口继续用
:class:`fcmd.gui.window.QtMainWindow`，单页工具继承本类：

    class DemoWindow(QtToolWindow):
        def __init__(self, settings_path=None):
            super().__init__(title="示例", config_name="demo")
            self.add(QLabel("内容"))

设置文件与 geometry 字符串沿用 ``ui/<name>.json`` 与 ``WxH+X+Y``，
与旧版本设置文件互读。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMainWindow, QMenu, QMessageBox, QVBoxLayout, QWidget

from fcmd.gui.logdialog import LogChannelMixin
from fcmd.gui.shell import WindowShellMixin, WindowSpec
from fcmd.gui.widgets import PageScroll
from fcmd.gui.window import about_version_text

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["QtToolWindow"]


class QtToolWindow(LogChannelMixin, WindowShellMixin, QMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """单页工具窗口：统一菜单、状态栏、主题切换、关闭落盘与任务日志。"""

    logLine = Signal(str)
    """任务日志行信号：写日志 → 已打开的日志窗口实时追加。"""

    def __init__(  # noqa: PLR0913 - 窗口骨架参数成组出现，显式优于配置对象
        self,
        *,
        title: str,
        config_name: str,
        default_size: tuple[int, int] = (900, 700),
        min_size: tuple[int, int] = (720, 540),
        on_close: Callable[[], bool] | None = None,
        persist: Callable[[str], None] | None = None,
        scroll: bool = False,
        status: str = "就绪",
    ) -> None:
        super().__init__()
        self._init_log_channel()
        self._init_window_shell(
            WindowSpec(
                title=title,
                config_name=config_name,
                default_size=default_size,
                min_size=min_size,
            ),
            on_close=on_close,
            persist=persist,
        )
        self._build_central(scroll)
        self.status_label = QLabel(status, self)
        self.statusBar().addWidget(self.status_label, 1)
        self._build_menu()

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #
    def _build_central(self, scroll: bool) -> None:
        """创建中央容器：内容统一放在纵向滚动页面里，窗口缩小时不会裁掉底部控件。

        窗口高度低于内容最小高度时出现滚动条，而不是把表格 / 工具栏压出卡片边界；
        ``scroll=True`` 额外保留页面内边距，供纯表单窗口使用。
        """
        root = QWidget(self)
        root.setObjectName("ToolBody")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 10)
        outer.setSpacing(10)
        self.scroll: PageScroll | None = PageScroll(root, margins=(0, 0, 4, 8) if scroll else (0, 0, 0, 0))
        outer.addWidget(self.scroll, 1)
        self.body = self.scroll.body
        self.body.setSpacing(10)
        self.setCentralWidget(root)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """把控件加进内容区（自上而下），返回该控件便于链式使用。"""
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: QVBoxLayout, stretch: int = 0) -> None:
        """把布局加进内容区。"""
        self.body.addLayout(layout, stretch)

    def add_stretch(self) -> None:
        """在内容区末尾加入弹性空白（控件靠上排列）。"""
        self.body.addStretch(1)

    # ------------------------------------------------------------------ #
    # 菜单 / 主题
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        """文件 / 视图 / 帮助三组菜单，与分页窗口保持同一位置与快捷键。

        与 :meth:`fcmd.gui.window.QtMainWindow._build_menu` 同一口径：菜单显式以
        窗口为父并保留引用，避开 PySide6 6.11 回收 ``addMenu(str)`` 返回对象导致
        子类补菜单时崩溃。
        """
        self.file_menu = QMenu("文件", self)
        self.menuBar().addMenu(self.file_menu)
        quit_action = QAction("退出", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        self.file_menu.addAction(quit_action)

        self.view_menu = QMenu("视图", self)
        self.menuBar().addMenu(self.view_menu)
        self._attach_theme_menu(self.view_menu.addMenu("主题"))

        self.help_menu = QMenu("帮助", self)
        self.menuBar().addMenu(self.help_menu)
        self.about_action = QAction("关于", self)
        self.about_action.triggered.connect(self._show_about)
        self.help_menu.addAction(self.about_action)
        # 日志入口是所有窗口的公共组件：统一挂在「帮助 → 关于」之前。
        self.enable_log_menu()

    # ------------------------------------------------------------------ #
    # 状态 / 设置
    # ------------------------------------------------------------------ #
    def set_status(self, text: str) -> None:
        """更新状态栏文字。"""
        self.status_label.setText(text)

    def status_text(self) -> str:
        """当前状态栏文字。"""
        return self.status_label.text()

    def _show_about(self) -> None:
        """关于对话框：程序 / 解释器 / Qt 绑定版本、设置文件与当前主题。"""
        QMessageBox.information(
            self,
            "关于",
            f"{about_version_text()}\n设置文件：{self._settings_path}\n主题：{self._theme.label}",
        )
