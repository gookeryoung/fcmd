"""Qt 单页工具窗口骨架：菜单 / 状态栏 / 几何记忆 / 主题 / 日志。

各单页工具窗口共用这一套骨架（菜单 / 状态栏 / 几何记忆 / 主题 / 日志）。分页窗口继续用
:class:`fcmd.gui.qt.window.QtMainWindow`，单页工具继承本类：

    class DemoWindow(QtToolWindow):
        def __init__(self, settings_path=None):
            super().__init__(title="示例", config_name="demo")
            self.add(QLabel("内容"))

设置文件与 geometry 字符串沿用 ``ui/<name>.json`` 与 ``WxH+X+Y``，
与旧版本设置文件互读。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QLabel, QMainWindow, QMessageBox, QVBoxLayout, QWidget

from fcmd.gui import config as _guiconfig
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.theme_menu import ThemeMenu
from fcmd.gui.qt.widgets import LogView, PageScroll
from fcmd.gui.qt.window import about_version_text, format_geometry, restore_geometry

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from fcmd.gui.theme import Theme

__all__ = ["QtToolWindow"]


class QtToolWindow(QMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """单页工具窗口：统一菜单、状态栏、主题切换与关闭落盘。"""

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
        self._config_name = config_name
        self._settings_path = _guiconfig.config_path(config_name)
        self._raw_settings = _guiconfig.read_settings(self._settings_path) or {}
        self._on_close = on_close
        self._persist = persist
        self._closing = False
        self._theme: Theme = _qt_theme.load_theme()

        self.setWindowTitle(title)
        self.setMinimumSize(*min_size)
        restore_geometry(
            self,
            self._raw_settings.get("geometry"),
            default_size=default_size,
            min_size=min_size,
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

    def add_log(self, *, height: int = 180) -> LogView:
        """追加一个带工具条的日志区并占满剩余空间。"""
        view = LogView(height=height, parent=self)
        self.body.addWidget(view, 1)
        return view

    # ------------------------------------------------------------------ #
    # 菜单 / 主题
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        """文件 / 视图 / 帮助三组菜单，与分页窗口保持同一位置与快捷键。"""
        file_menu = self.menuBar().addMenu("文件")
        quit_action = QAction("退出", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("视图")
        self.theme_menu = ThemeMenu(self, self._theme)
        self.theme_menu.attach(view_menu.addMenu("主题"))

        help_menu = self.menuBar().addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def set_theme(self, theme: Theme) -> None:
        """切换主题并持久化（与分页窗口共用同一份 ``ui/theme.json``）。"""
        self.theme_menu.set_theme(theme)
        self._theme = self.theme_menu.theme()

    def theme(self) -> Theme:
        """当前主题。"""
        return self._theme

    # ------------------------------------------------------------------ #
    # 状态 / 设置
    # ------------------------------------------------------------------ #
    def set_status(self, text: str) -> None:
        """更新状态栏文字。"""
        self.status_label.setText(text)

    def status_text(self) -> str:
        """当前状态栏文字。"""
        return self.status_label.text()

    @property
    def settings_path(self) -> Path:
        """当前窗口的设置文件路径。"""
        return self._settings_path

    def settings(self) -> dict[str, object]:
        """读取设置文件原始内容（缺失字段按空处理）。"""
        return dict(self._raw_settings)

    def save_settings(self, payload: dict[str, object]) -> None:
        """把 ``payload`` 合并进设置文件（写入失败静默，不影响关闭）。"""
        merged = {**self._raw_settings, **payload}
        self._raw_settings = merged
        _guiconfig.write_settings(merged, self._settings_path)

    def geometry_text(self) -> str:
        """当前窗口的 ``WxH+X+Y``，用于关闭时写回设置。

        取 ``geometry()``（客户区，不含窗口装饰）而不是 ``frameGeometry()``，
        与 ``restore_geometry`` 的 ``setGeometry`` 保持同一口径；否则标题栏与
        边框会在每次启动时叠加到保存的尺寸上，窗口越开越大、位置逐次上移。
        """
        rect = self.geometry()
        return format_geometry(rect.width(), rect.height(), rect.x(), rect.y())

    def _show_about(self) -> None:
        """关于对话框：程序 / 解释器 / Qt 绑定版本、设置文件与当前主题。"""
        QMessageBox.information(
            self,
            "关于",
            f"{about_version_text()}\n设置文件：{self._settings_path}\n主题：{self._theme.label}",
        )

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭前回调 ``on_close`` 并写回 geometry；返回 ``False`` 时取消关闭。"""
        if self._closing:
            event.accept()
            return
        if self._on_close is not None and not self._on_close():
            event.ignore()
            return
        self._closing = True
        geometry = self.geometry_text()
        if self._persist is not None:
            self._persist(geometry)
        else:
            self.save_settings({"geometry": geometry})
        event.accept()

    def is_closing(self) -> bool:
        """窗口是否正在关闭（后台线程回调可据此提前返回）。"""
        return self._closing
