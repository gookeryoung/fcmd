"""侧边栏分页导航：左侧导航栏 + 右侧页面区。

各 Tk 窗口原先用 ``ttk.Notebook`` 在顶部分页；页数一多或标题较长时，
标签条会挤压内容宽度，且选中态只在标签本身。本模块把同一套能力收敛成
``SidebarNav``：左侧固定宽度导航栏（标题 + 页按钮 + 当前页提示），右侧
页面区按需显示一个页面，窗口只需在 ``add`` 前后构建页面内容。

设计约定：

- 页面用字符串 key 标识，不依赖控件对象身份；``select`` 同时接受 key、
  索引与页面控件，便于从 ``ttk.Notebook`` 平滑迁移。
- 控件构建延迟到实例化时导入 tkinter，保持 ``fcmd.gui`` 在 CLI 快路径上
  不加载图形库。
- 纯逻辑（key 解析、循环切换）拆成模块级函数，CI 无显示环境也能单测；
  Tk 控件部分标注 ``pragma: no cover``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fcmd.gui.theme import Theme

__all__ = [
    "SidebarNav",
    "next_page",
    "resolve_page_key",
]


def resolve_page_key(keys: Sequence[str], target: str | int) -> str:
    """把 key 或索引解析为页面 key。

    未知 key 或越界索引抛 ``KeyError``，避免静默停在不存在的页面。
    """
    if isinstance(target, bool) or not isinstance(target, (str, int)):
        raise KeyError(target)
    if isinstance(target, int):
        try:
            return keys[target]
        except IndexError:
            raise KeyError(target) from None
    if target in keys:
        return target
    raise KeyError(target)


def next_page(keys: Sequence[str], current: str | None, step: int = 1) -> str:
    """按顺序循环切换到 ``current`` 之后第 ``step`` 个页面。

    ``current`` 缺失或不在 ``keys`` 中时从第一页起算；空序列返回空串。
    """
    if not keys:
        return ""
    try:
        index = keys.index(current) if current is not None else -1
    except ValueError:
        index = -1
    return keys[(index + step) % len(keys)]


class SidebarNav:  # pragma: no cover - 需要真实 Tk 窗口
    """左侧导航 + 右侧页面区的分页容器。

    典型用法::

        nav = SidebarNav(outer, theme=theme, page_padding=theme.spacing.md)
        config_page = nav.add("config", "启动配置", hint="模型与参数")
        monitor_page = nav.add("monitor", "运行监控")
        nav.container.pack(fill="both", expand=True)
        nav.select("monitor")

    ``container`` 由调用方负责 pack / grid；``add`` 返回的页面控件可直接
    作为后续布局的父容器。
    """

    def __init__(  # noqa: PLR0913 - 导航外观/回调参数彼此独立，显式优于配置对象
        self,
        parent: Any,
        *,
        theme: Theme,
        width: int = 172,
        title: str = "",
        page_padding: int = 0,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._ttk = ttk
        self._theme = theme
        self._on_change = on_change
        self._page_padding = page_padding
        self._keys: list[str] = []
        self._pages: dict[str, Any] = {}
        self._buttons: dict[str, Any] = {}
        self._hints: dict[str, str] = {}
        self._current = ""
        space = theme.spacing

        self.container = ttk.Frame(parent)
        self.container.columnconfigure(1, weight=1)
        self.container.rowconfigure(0, weight=1)

        self._nav = ttk.Frame(self.container, style="Sidebar.TFrame", padding=(space.xs, space.sm))
        self._nav.grid(row=0, column=0, sticky="nsw")
        self._nav.configure(width=width)
        self._nav.grid_propagate(False)

        if title:
            ttk.Label(self._nav, text=title, style="SidebarTitle.TLabel", anchor="w").pack(
                fill="x", padx=space.xs, pady=(space.xs, space.sm)
            )

        self._hint_var = tk.StringVar(value="")
        self._hint_label = ttk.Label(
            self._nav,
            textvariable=self._hint_var,
            style="SidebarHint.TLabel",
            anchor="w",
            wraplength=max(width - space.md, 80),
            justify="left",
        )
        self._hint_label.pack(side="bottom", fill="x", padx=space.xs, pady=(space.sm, 0))

        self._list = ttk.Frame(self._nav, style="Sidebar.TFrame")
        self._list.pack(fill="both", expand=True)

        self._page_area = ttk.Frame(self.container)
        self._page_area.grid(row=0, column=1, sticky="nsew")
        self._page_area.columnconfigure(0, weight=1)
        self._page_area.rowconfigure(0, weight=1)

        self._shortcuts_bound = False

    # ------------------------------------------------------------------ #
    # 页面管理
    # ------------------------------------------------------------------ #
    def add(self, key: str, label: str, *, hint: str = "") -> Any:
        """新增页面并返回页面控件；首个页面自动选中（不触发 ``on_change``）。"""
        if key in self._pages:
            raise ValueError(f"页面 key 重复：{key}")
        space = self._theme.spacing
        page = self._ttk.Frame(self._page_area, padding=self._page_padding)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        self._pages[key] = page
        self._keys.append(key)
        if hint:
            self._hints[key] = hint
        button = self._ttk.Button(
            self._list,
            text=label,
            style="Sidebar.TButton",
            command=lambda page_key=key: self.select(page_key),
        )
        button.pack(fill="x", pady=space.hairline)
        self._buttons[key] = button
        if len(self._keys) == 1:
            self.select(key, notify=False)
        return page

    def page(self, key: str) -> Any:
        """返回页面控件；未知 key 抛 ``KeyError``。"""
        return self._pages[resolve_page_key(self._keys, key)]

    def select(self, target: Any = None, *, notify: bool = True) -> None:
        """显示指定页面；``target`` 支持 key、索引或页面控件，省略时选首个。"""
        key = self._resolve(target)
        if key == self._current:
            return
        for page_key in self._keys:
            if page_key == key:
                self._pages[page_key].grid()
            else:
                self._pages[page_key].grid_remove()
        for page_key, button in self._buttons.items():
            button.configure(style="SidebarActive.TButton" if page_key == key else "Sidebar.TButton")
        self._current = key
        self._hint_var.set(self._hints.get(key, ""))
        if notify and self._on_change is not None:
            self._on_change(key)

    def step(self, delta: int = 1) -> None:
        """循环切换到相邻页面，供快捷键与外部按钮复用。"""
        self.select(next_page(self._keys, self._current, delta))

    def selected(self) -> str:
        """返回当前页面 key；尚未添加页面时为空串。"""
        return self._current

    def keys(self) -> tuple[str, ...]:
        """按添加顺序返回页面 key。"""
        return tuple(self._keys)

    def enable_shortcuts(self) -> None:
        """绑定 ``Ctrl+Tab`` / ``Ctrl+Shift+Tab`` / ``Ctrl+PageDown`` / ``Ctrl+PageUp``。"""
        if self._shortcuts_bound:
            return
        top = self.container.winfo_toplevel()
        top.bind("<Control-Tab>", lambda _event: self._shortcut(1))
        top.bind("<Control-Shift-Tab>", lambda _event: self._shortcut(-1))
        top.bind("<Control-ISO_Left_Tab>", lambda _event: self._shortcut(-1))
        top.bind("<Control-Next>", lambda _event: self._shortcut(1))
        top.bind("<Control-Prior>", lambda _event: self._shortcut(-1))
        self._shortcuts_bound = True

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _shortcut(self, delta: int) -> str:
        self.step(delta)
        return "break"

    def _resolve(self, target: Any) -> str:
        if target is None:
            return self._keys[0] if self._keys else ""
        if isinstance(target, (str, int)) and not isinstance(target, bool):
            return resolve_page_key(self._keys, target)
        for key, page in self._pages.items():
            if page is target:
                return key
        raise KeyError(target)
