"""Qt 窗口骨架：侧边栏导航 + 页面栈 + 状态栏 + 主题菜单 + 窗口记忆。

Qt 窗口骨架负责「导航 / 菜单 / 状态栏 / 日志区 / 后台任务」，
并把窗口位置与大小继续写进
``${FCMD_HOME:-~/.fcmd}/ui/<name>.json``（与旧版本设置文件互读）。

页面按需构建：登记时只给工厂函数，首次切到该页才实例化，冷启动不付全量
页面的构造成本（llmw 模型页 / 设置页会扫描目录、探测引擎）。

几何字符串沿用 ``WxH+X+Y`` 格式，解析与格式化是纯函数（:mod:`fcmd.gui.geometry`），便于单测。
"""

from __future__ import annotations

import platform
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui import config as _guiconfig
from fcmd.gui.geometry import format_geometry, parse_geometry, resolve_geometry
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt._pyside_compat import binding_version
from fcmd.gui.qt.theme_menu import ThemeMenu
from fcmd.gui.qt.widgets import LoadingBar

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtGui import QIcon

    from fcmd.gui.theme import Theme

__all__ = [
    "QtMainWindow",
    "about_version_text",
    "format_geometry",
    "parse_geometry",
    "resolve_geometry_text",
    "restore_geometry",
]


def about_version_text() -> str:
    """关于对话框的运行时版本行：fcmd / Python / PySide。"""
    import fcmd

    return f"fcmd {fcmd.__version__}\nPython 版本：{platform.python_version()}\nPySide 版本：{binding_version()}"


def resolve_geometry_text(  # noqa: PLR0913 - 与 geometry.resolve_geometry 参数对齐
    saved: str | None,
    screen_width: int,
    screen_height: int,
    *,
    default: str,
    min_width: int,
    min_height: int,
) -> str:
    """恢复记忆位置，越界 / 非法时回落到默认尺寸（按屏幕夹紧）。"""
    return resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=default,
        min_width=min_width,
        min_height=min_height,
    )


def restore_geometry(
    widget: QWidget,
    saved: str | None,
    *,
    default_size: tuple[int, int],
    min_size: tuple[int, int],
) -> None:  # pragma: no cover - 需要 Qt 屏幕信息
    """按记忆位置恢复窗口；支持多屏，越界 / 非法时用默认尺寸。"""
    rect = parse_geometry(saved)
    screen = QApplication.primaryScreen()
    if rect is not None:
        width, height, x, y = rect
        screen = QApplication.screenAt(QPoint(x + width // 2, y + height // 2)) or screen
    if screen is None:
        widget.resize(*default_size)
        return
    available = screen.availableGeometry()
    if rect is None:
        widget.resize(*default_size)
        return
    width = min(max(rect[0], min_size[0]), available.width())
    height = min(max(rect[1], min_size[1]), available.height())
    x = min(max(rect[2], available.left()), available.left() + max(available.width() - width, 0))
    y = min(max(rect[3], available.top()), available.top() + max(available.height() - height, 0))
    widget.setGeometry(x, y, width, height)


class QtMainWindow(QMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """通用窗口骨架：左侧分页导航 + 右侧页面栈 + 状态栏 + 主题菜单。"""

    def __init__(  # noqa: PLR0913 - 窗口骨架参数成组出现，显式优于配置对象
        self,
        *,
        title: str,
        config_name: str,
        nav_title: str = "",
        default_size: tuple[int, int] = (1080, 880),
        min_size: tuple[int, int] = (1000, 800),
        on_close: Callable[[], bool] | None = None,
        persist: Callable[[str], None] | None = None,
        sidebar_collapsed: bool = False,
        icon: QIcon | None = None,
        settings_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._config_name = config_name
        self._settings_path = settings_path or _guiconfig.config_path(config_name)
        self._raw_settings = _guiconfig.read_settings(self._settings_path) or {}
        self._on_close = on_close
        self._persist = persist
        self._factories: dict[str, Callable[[], QWidget]] = {}
        self._built: dict[str, QWidget] = {}
        self._current = ""
        self._closing = False
        self._theme: Theme = _qt_theme.load_theme()

        self.setWindowTitle(title)
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)
        self.setMinimumSize(*min_size)
        self._restore_geometry(default_size, min_size)

        from fcmd.gui.qt.sidebar import SidebarNav

        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        collapsed = sidebar_collapsed or bool(self._raw_settings.get("sidebar_collapsed"))
        self.nav = SidebarNav(title=nav_title or title, collapsed=collapsed, icon=icon)
        self.nav.collapsedChanged.connect(self._handle_sidebar_collapsed)
        layout.addWidget(self.nav)
        right = QWidget(central)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 12, 16, 8)
        right_layout.setSpacing(8)
        self.page_title = QLabel("", right)
        self.page_title.setObjectName("SectionTitle")
        self.page_hint = QLabel("", right)
        self.page_hint.setObjectName("Hint")
        right_layout.addWidget(self.page_title)
        right_layout.addWidget(self.page_hint)
        self.stack = QStackedWidget(right)
        right_layout.addWidget(self.stack, 1)
        layout.addWidget(right, 1)
        self.setCentralWidget(central)

        self.status_label = QLabel("就绪", self)
        self.statusBar().addWidget(self.status_label, 1)
        self.status_note = QLabel("", self)
        self.status_note.setObjectName("Hint")
        self.status_note.setVisible(False)
        self.statusBar().addPermanentWidget(self.status_note, 0)
        self.loading_bar = LoadingBar("加载中…", self)
        self.statusBar().addPermanentWidget(self.loading_bar, 0)
        self.nav.changed.connect(self.show_page)
        self.nav.enable_shortcuts(self)
        self._build_menu()

    # ------------------------------------------------------------------ #
    # 页面
    # ------------------------------------------------------------------ #
    def add_page(self, key: str, label: str, *, hint: str = "", factory: Callable[[], QWidget]) -> None:
        """登记页面；首次切到该页时才调用 ``factory`` 构建内容。"""
        self.nav.add(key, label, hint=hint)
        self._factories[key] = factory
        placeholder = QWidget(self)
        placeholder.setObjectName("PageBody")
        self.stack.addWidget(placeholder)
        first = not self._current
        if first:
            self._current = key
            self.page_title.setText(label)
            self.page_hint.setText(hint)
        if first:
            # 首屏页面立即构建：否则右侧只有占位控件，点击已选中的首项也不会
            # 触发 changed，用户会一直看到空白 / 「读取中」。
            self.show_page(key)

    def show_page(self, key: str) -> None:
        """切换到指定页，必要时先构建页面内容。"""
        if key not in self._factories:
            return
        page = self._build_if_needed(key)
        if self.stack.currentWidget() is page and self._current == key:
            return
        self._current = key
        self.stack.setCurrentWidget(page)
        self.page_title.setText(self.nav.label(key))
        self.page_hint.setText(self.nav.hint(key))
        if self.nav.selected() != key:
            self.nav.select(key)
        page.setFocus()

    def _build_if_needed(self, key: str) -> QWidget:
        """构建尚未实例化的页面，并用真实内容替换占位控件。"""
        page = self._built.get(key)
        if page is not None:
            return page
        placeholder = self.stack.widget(self._index_of(key))
        page = self._factories[key]()
        self._built[key] = page
        if placeholder is not None:
            index = self.stack.indexOf(placeholder)
            self.stack.insertWidget(index, page)
            self.stack.removeWidget(placeholder)
            placeholder.deleteLater()
        else:  # pragma: no cover - 占位控件总存在，防御分支
            self.stack.addWidget(page)
        return page

    def _index_of(self, key: str) -> int:
        """页面在栈中的索引（与导航登记顺序一致）。"""
        return list(self._factories).index(key)

    def current_page(self) -> str:
        """当前页面 key。"""
        return self._current

    def page(self, key: str) -> QWidget | None:
        """返回已构建的页面；未构建时返回 ``None``（供跨页联动调用）。"""
        return self._built.get(key)

    def ensure_page(self, key: str) -> QWidget | None:
        """确保页面已构建并返回它（顺带切到该页，便于「去设置」这类跳转）。"""
        if key not in self._factories:
            return None
        self.show_page(key)
        return self._built.get(key)

    # ------------------------------------------------------------------ #
    # 状态栏 / 设置
    # ------------------------------------------------------------------ #
    def set_status(self, text: str) -> None:
        """更新状态栏文字。"""
        self.status_label.setText(text)

    def status_text(self) -> str:
        """当前状态栏文字。"""
        return self.status_label.text()

    def set_status_note(self, text: str) -> None:
        """状态栏左侧文字与右侧加载条之间的常驻备注（如模型更新时间）。"""
        self.status_note.setText(text)
        self.status_note.setVisible(bool(text))

    def show_loading(self, text: str) -> None:
        """在状态栏右侧显示 / 更新加载提示；空串收起。左侧状态文字保持可见。"""
        if not text:
            self.loading_bar.stop()
            return
        if self.loading_bar.is_running():
            self.loading_bar.set_text(text)
        else:
            self.loading_bar.start(text)

    def settings(self) -> dict[str, Any]:
        """读取窗口设置文件原始内容（缺失字段按空处理）。"""
        return dict(self._raw_settings)

    def save_settings(self, payload: dict[str, Any]) -> None:
        """把 ``payload`` 合并进设置文件（写入失败静默，不影响关闭）。"""
        merged = {**self._raw_settings, **payload}
        self._raw_settings = merged
        _guiconfig.write_settings(merged, self._settings_path)

    def _restore_geometry(self, default_size: tuple[int, int], min_size: tuple[int, int]) -> None:
        """按记忆位置恢复窗口；越界 / 非法时用默认尺寸居中。"""
        restore_geometry(
            self,
            self._raw_settings.get("geometry"),
            default_size=default_size,
            min_size=min_size,
        )

    def geometry_text(self) -> str:
        """当前窗口的 ``WxH+X+Y``，用于关闭时写回设置。

        取 ``geometry()``（客户区，不含窗口装饰）而不是 ``frameGeometry()``：
        ``restore_geometry`` 用 ``setGeometry`` 恢复客户区，两者口径一致，
        标题栏高度与边框不会在每次启动时被重复累加导致窗口漂移。
        """
        rect = self.geometry()
        return format_geometry(rect.width(), rect.height(), rect.x(), rect.y())

    # ------------------------------------------------------------------ #
    # 菜单 / 主题
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        """文件 / 视图 / 帮助三组菜单：退出、主题切换、关于。"""
        file_menu = self.menuBar().addMenu("文件")
        quit_action = QAction("退出", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("视图")
        self.sidebar_action = QAction("折叠侧边栏", self)
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(self.nav.is_collapsed())
        self.sidebar_action.setShortcut(QKeySequence("Ctrl+Shift+B"))
        self.sidebar_action.triggered.connect(self.nav.set_collapsed)
        view_menu.addAction(self.sidebar_action)
        view_menu.addSeparator()
        self.theme_menu = ThemeMenu(self, self._theme)
        self.theme_menu.attach(view_menu.addMenu("主题"))

        # 子窗口可在「关于」之前插入自己的帮助项（如 llmw 的「服务日志…」）。
        self.help_menu = self.menuBar().addMenu("帮助")
        self.about_action = QAction("关于", self)
        self.about_action.triggered.connect(self._show_about)
        self.help_menu.addAction(self.about_action)

    def set_theme(self, theme: Theme) -> None:
        """切换主题：立即重设 QPalette / QSS 并持久化选择。"""
        self.theme_menu.set_theme(theme)
        self._theme = self.theme_menu.theme()

    def theme(self) -> Theme:
        """当前主题。"""
        return self._theme

    def _handle_sidebar_collapsed(self, collapsed: bool) -> None:
        """同步菜单勾选状态；无自定义 persist 时把折叠状态写进窗口设置。"""
        self.sidebar_action.blockSignals(True)
        self.sidebar_action.setChecked(collapsed)
        self.sidebar_action.blockSignals(False)
        if self._persist is None:
            self.save_settings({"sidebar_collapsed": collapsed})

    def _show_about(self) -> None:
        """关于对话框：程序 / 解释器 / Qt 绑定版本、配置文件与当前主题。"""
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("关于")
        box.setText(f"{about_version_text()}\n配置文件：{self._settings_path}\n主题：{self._theme.label}")
        icon = self.windowIcon()
        if not icon.isNull():
            box.setIconPixmap(icon.pixmap(64, 64))
        box.exec()

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭前回调 ``on_close``；返回 ``False`` 时取消关闭。"""
        if self._closing:
            event.accept()
            return
        if self._on_close is not None and not self._on_close():
            event.ignore()
            return
        self._closing = True
        if self._persist is not None:
            self._persist(self.geometry_text())
        else:
            self.save_settings({"geometry": self.geometry_text()})
        event.accept()

    def is_closing(self) -> bool:
        """窗口是否正在关闭（后台线程回调可据此提前返回）。"""
        return self._closing
