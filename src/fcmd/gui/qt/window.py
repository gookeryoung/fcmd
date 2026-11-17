"""Qt 窗口骨架：侧边栏导航 + 页面栈 + 状态栏 + 主题菜单 + 窗口记忆。

Tk 基座（:mod:`fcmd.gui.window`）负责「创建根窗口 / 菜单 / 日志区 / 后台线程」，
本模块在 Qt 下提供等价骨架，并把窗口位置与大小继续写进
``${FCMD_HOME:-~/.fcmd}/ui/<name>.json``（与 Tk 窗口共用同一套设置文件与校验）。

页面按需构建：登记时只给工厂函数，首次切到该页才实例化，冷启动不付全量
页面的构造成本（locllmw 模型页 / 设置页会扫描目录、探测引擎）。

几何字符串沿用 Tk 的 ``WxH+X+Y`` 格式，因此同一个设置文件在 Tk 与 Qt
窗口之间可以直接互读；解析与格式化是纯函数，便于单测。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

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
from fcmd.gui import window as _guiwindow
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.theme_menu import ThemeMenu

if TYPE_CHECKING:
    from collections.abc import Callable

    from fcmd.gui.theme import Theme

__all__ = [
    "QtMainWindow",
    "format_geometry",
    "parse_geometry",
    "resolve_geometry_text",
    "restore_geometry",
]

_GEOMETRY_RE = re.compile(r"^(?P<width>\d+)x(?P<height>\d+)(?:(?P<sign_x>[+-])(?P<x>\d+)(?P<sign_y>[+-])(?P<y>\d+))?$")


def parse_geometry(text: str | None) -> tuple[int, int, int, int] | None:
    """解析 ``WxH+X+Y``；缺少位置时按左上角 ``0,0`` 处理，非法文本返回 ``None``。"""
    if not isinstance(text, str):
        return None
    match = _GEOMETRY_RE.fullmatch(text.strip())
    if match is None:
        return None
    width = int(match["width"])
    height = int(match["height"])
    if width <= 0 or height <= 0:
        return None
    if match["x"] is None:
        return width, height, 0, 0
    x = int(match["x"]) if match["sign_x"] == "+" else -int(match["x"])
    y = int(match["y"]) if match["sign_y"] == "+" else -int(match["y"])
    return width, height, x, y


def format_geometry(width: int, height: int, x: int, y: int) -> str:
    """把窗口矩形格式化为 Tk 兼容的 ``WxH+X+Y``（负数坐标写成 ``+-`` 形式）。"""
    sign_x = "+" if x >= 0 else "-"
    sign_y = "+" if y >= 0 else "-"
    return f"{max(width, 0)}x{max(height, 0)}{sign_x}{abs(x)}{sign_y}{abs(y)}"


def resolve_geometry_text(  # noqa: PLR0913 - 与 Tk 版 resolve_geometry 参数对齐
    saved: str | None,
    screen_width: int,
    screen_height: int,
    *,
    default: str,
    min_width: int,
    min_height: int,
) -> str:
    """恢复记忆位置，越界 / 非法时回落到默认尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
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
    """按记忆位置恢复窗口；越界 / 非法时用默认尺寸。"""
    screen = QApplication.primaryScreen()
    available = screen.availableGeometry() if screen is not None else None
    screen_width = available.width() if available is not None else 1920
    screen_height = available.height() if available is not None else 1080
    text = resolve_geometry_text(
        saved,
        screen_width,
        screen_height,
        default=f"{default_size[0]}x{default_size[1]}",
        min_width=min_size[0],
        min_height=min_size[1],
    )
    rect = parse_geometry(text)
    if rect is None:
        widget.resize(*default_size)
        return
    width, height, x, y = rect
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
    ) -> None:
        super().__init__()
        self._config_name = config_name
        self._settings_path = _guiconfig.config_path(config_name)
        self._raw_settings = _guiconfig.read_settings(self._settings_path) or {}
        self._on_close = on_close
        self._persist = persist
        self._factories: dict[str, Callable[[], QWidget]] = {}
        self._built: dict[str, QWidget] = {}
        self._current = ""
        self._closing = False
        self._theme: Theme = _qt_theme.load_theme()

        self.setWindowTitle(title)
        self.setMinimumSize(*min_size)
        self._restore_geometry(default_size, min_size)

        from fcmd.gui.qt.sidebar import SidebarNav

        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        collapsed = sidebar_collapsed or bool(self._raw_settings.get("sidebar_collapsed"))
        self.nav = SidebarNav(title=nav_title or title, collapsed=collapsed)
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
        """当前窗口的 ``WxH+X+Y``，用于关闭时写回设置。"""
        frame = self.frameGeometry()
        return format_geometry(frame.width(), frame.height(), frame.x(), frame.y())

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

        help_menu = self.menuBar().addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

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
        """关于对话框：版本、配置文件与当前主题。"""
        from PySide6.QtWidgets import QMessageBox

        import fcmd

        QMessageBox.information(
            self,
            "关于",
            f"fcmd {fcmd.__version__}\n配置文件：{self._settings_path}\n主题：{self._theme.label}",
        )

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


def qt_exec(window: QtMainWindow) -> int:  # pragma: no cover - 需要 Qt 事件循环
    """显示窗口并进入事件循环。"""
    window.show()
    return int(QApplication.instance().exec())
