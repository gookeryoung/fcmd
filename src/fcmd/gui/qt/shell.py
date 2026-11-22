"""Qt 窗口外壳 mixin：设置 / 几何 / 主题 / 关闭落盘。

分页窗口 :class:`fcmd.gui.qt.window.QtMainWindow` 与单页窗口
:class:`fcmd.gui.qt.toolwindow.QtToolWindow` 的「窗口外壳」完全一致：读设置、
恢复几何、加载主题、切换主题、关闭前回调与落盘。历史上两份实现逐段复制，
改一处（例如主题按窗口覆盖）就要改两遍。这里按职责拆成四个 mixin：

- :class:`WindowSettingsMixin`：``ui/<config_name>.json`` 的读取 / 合并写入；
- :class:`WindowGeometryMixin`：``WxH+X+Y`` 几何恢复与回写（客户区口径）；
- :class:`WindowThemeMixin`：加载 / 切换主题，按窗口叠加调色板与度量覆盖；
- :class:`WindowLifecycleMixin`：``on_close`` 确认 + ``persist`` 落盘 + 幂等关闭。

:class:`WindowShellMixin` 把四者组合成一份入口 :meth:`WindowShellMixin._init_window_shell`，
窗口子类只需要再加自己的布局与菜单。:class:`WindowSpec` 把「标题 / 配置名 / 尺寸 /
侧边栏状态 / 主题覆盖键」收成一个可复用配置对象，新增窗口不用再逐项传参。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMenu

from fcmd.gui import config as _guiconfig
from fcmd.gui.geometry import format_geometry, parse_geometry, resolve_geometry
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.theme_menu import ThemeMenu

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtGui import QIcon

    from fcmd.gui.theme import Theme

__all__ = [
    "WindowGeometryMixin",
    "WindowLifecycleMixin",
    "WindowSettingsMixin",
    "WindowShellMixin",
    "WindowSpec",
    "WindowThemeMixin",
]


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """窗口外壳配置：标题、配置名、尺寸与主题覆盖键。

    ``app_name`` 为空时等同 ``config_name``：``ui/theme.json`` 的 ``apps`` 段按它取
    窗口级调色板 / 度量覆盖（见 :func:`fcmd.gui.theme.merge_theme_overrides`）。
    """

    title: str
    config_name: str
    nav_title: str = ""
    default_size: tuple[int, int] = (1080, 880)
    min_size: tuple[int, int] = (1000, 800)
    sidebar_collapsed: bool = False
    app_name: str = ""
    settings_path: Path | None = field(default=None)

    @property
    def theme_app(self) -> str:
        """主题覆盖用的应用标识（``app_name`` 优先，回退 ``config_name``）。"""
        return self.app_name or self.config_name


class WindowSettingsMixin:
    """窗口设置读写：``ui/<config_name>.json`` 的读取与合并写入。"""

    _settings_path: Path
    _raw_settings: dict[str, object]

    def _init_settings(self, spec: WindowSpec) -> None:
        self._config_name = spec.config_name
        self._settings_path = spec.settings_path or _guiconfig.config_path(spec.config_name)
        self._raw_settings = _guiconfig.read_settings(self._settings_path) or {}

    @property
    def settings_path(self) -> Path:
        """当前窗口的设置文件路径。"""
        return self._settings_path

    def settings(self) -> dict[str, object]:
        """读取设置文件原始内容（缺失字段按空处理）。"""
        return dict(self._raw_settings)

    def settings_value(self, key: str, default: object = None) -> object:
        """取单项设置（缺失时返回 ``default``），供窗口在构造期回填控件。"""
        return self._raw_settings.get(key, default)

    def save_settings(self, payload: dict[str, object]) -> None:
        """把 ``payload`` 合并进设置文件（写入失败静默，不影响关闭）。"""
        merged = {**self._raw_settings, **payload}
        self._raw_settings = merged
        _guiconfig.write_settings(merged, self._settings_path)


class WindowGeometryMixin:
    """窗口几何：按记忆位置恢复，关闭时回写客户区几何。"""

    def _restore_geometry_from(self, spec: WindowSpec) -> None:
        """按 ``WindowSpec`` 的默认 / 最小尺寸恢复记忆位置。"""
        raw = getattr(self, "_raw_settings", {})
        saved = raw.get("geometry") if isinstance(raw, dict) else None
        if not isinstance(saved, str) or parse_geometry(saved) is None:
            # 首次启动（或记忆损坏）按默认尺寸打开，不拿离屏 / 小屏的可用尺寸去夹：
            # 夹紧只用于「有记忆位置、但换屏后越界」的场景。
            self.resize(*spec.default_size)  # type: ignore[attr-defined]
            return
        resolved = resolve_geometry(
            saved,
            *self._screen_size(),
            default=f"{spec.default_size[0]}x{spec.default_size[1]}",
            min_width=spec.min_size[0],
            min_height=spec.min_size[1],
        )
        rect = parse_geometry(resolved)
        if rect is None:  # pragma: no cover - resolve_geometry 总返回合法几何
            self.resize(*spec.default_size)  # type: ignore[attr-defined]
            return
        width, height, x, y = rect
        self.setGeometry(x, y, width, height)  # type: ignore[attr-defined]

    @staticmethod
    def _screen_size() -> tuple[int, int]:
        """主屏可用尺寸；无屏幕（离屏测试）时退回常见桌面尺寸。"""
        from PySide6.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        if screen is None:  # pragma: no cover - 离屏平台也有虚拟屏幕
            return (1920, 1080)
        rect = screen.availableGeometry()
        return (rect.width(), rect.height())

    def geometry_text(self) -> str:
        """当前窗口的 ``WxH+X+Y``，用于关闭时写回设置。

        取 ``geometry()``（客户区，不含窗口装饰）而不是 ``frameGeometry()``：
        与恢复时的 ``setGeometry`` 同一口径，标题栏与边框不会在每次启动时
        被重复累加，窗口不会越开越大 / 逐次上移。
        """
        rect = self.geometry()  # type: ignore[attr-defined]
        return format_geometry(rect.width(), rect.height(), rect.x(), rect.y())


class WindowThemeMixin:
    """窗口主题：加载（含按窗口覆盖）、即时切换与「视图 → 主题」菜单。"""

    _theme: Theme

    def _init_theme(self, app_name: str = "") -> None:
        """加载持久化主题；``app_name`` 非空时叠加该窗口的调色板 / 度量覆盖。"""
        self._theme = _qt_theme.load_theme(app=app_name or None)

    def theme(self) -> Theme:
        """当前主题。"""
        return self._theme

    def set_theme(self, theme: Theme) -> None:
        """切换主题：立即重设 QPalette / QSS 并持久化选择。"""
        self.theme_menu.set_theme(theme)
        self._theme = self.theme_menu.theme()

    def _attach_theme_menu(self, menu: QMenu) -> ThemeMenu:
        """在「视图」菜单下挂主题子菜单，并记录控制器供 :meth:`set_theme` 复用。"""
        self.theme_menu = ThemeMenu(self, self._theme)  # type: ignore[attr-defined]
        self.theme_menu.attach(menu)
        return self.theme_menu


class WindowLifecycleMixin:
    """窗口关闭：``on_close`` 确认 → ``persist`` 或写回几何，幂等。"""

    _closing: bool
    _on_close: Callable[[], bool] | None
    _persist: Callable[[str], None] | None

    def _init_lifecycle(
        self,
        *,
        on_close: Callable[[], bool] | None = None,
        persist: Callable[[str], None] | None = None,
    ) -> None:
        self._on_close = on_close
        self._persist = persist
        self._closing = False

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭前回调 ``on_close``；返回 ``False`` 时取消关闭。"""
        if self._closing:
            event.accept()
            return
        if self._on_close is not None and not self._on_close():
            event.ignore()
            return
        self._closing = True
        geometry = self.geometry_text()  # type: ignore[attr-defined]
        if self._persist is not None:
            self._persist(geometry)
        else:
            self.save_settings({"geometry": geometry})  # type: ignore[attr-defined]
        event.accept()

    def is_closing(self) -> bool:
        """窗口是否正在关闭（后台线程回调可据此提前返回）。"""
        return self._closing


class WindowShellMixin(
    WindowSettingsMixin,
    WindowGeometryMixin,
    WindowThemeMixin,
    WindowLifecycleMixin,
):
    """组合四类外壳 mixin 的统一入口。"""

    def _init_window_shell(
        self,
        spec: WindowSpec,
        *,
        on_close: Callable[[], bool] | None = None,
        persist: Callable[[str], None] | None = None,
        icon: QIcon | None = None,
    ) -> None:
        """初始化设置 / 主题 / 几何 / 关闭回调，并写入标题与窗口图标。"""
        self._window_spec = spec
        self._init_settings(spec)
        self._init_theme(spec.theme_app)
        self._init_lifecycle(on_close=on_close, persist=persist)
        self.setWindowTitle(spec.title)  # type: ignore[attr-defined]
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)  # type: ignore[attr-defined]
        self.setMinimumSize(*spec.min_size)  # type: ignore[attr-defined]
        self._restore_geometry_from(spec)
