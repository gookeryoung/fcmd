"""GUI 窗口共享骨架（不注册工具）。

10 个 ``*w.py`` 窗口与 ``checksum`` / ``alarmclock`` 的窗口类各自重复了同一套
骨架：创建根窗口（DPI 感知、主题、标题、geometry、最小尺寸、关闭协议）、恢复
窗口位置、构建菜单栏、只读日志区、启动后台 daemon 线程。

本模块收敛上述部分；后台事件（进度 / 完成 / 错误）的语义与状态更新差异较大，
轮询逻辑仍留在各窗口。本模块位于 :mod:`fcmd.gui`，不参与 :mod:`fcmd.tools` 的工具发现。
"""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, cast

from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from tkinter import Misc, Tk

    from fcmd.gui.theme import Theme

__all__ = [
    "WORKER_INTERVAL_MS",
    "append_log",
    "build_menu",
    "center_over_parent",
    "centered_geometry",
    "clamp_saved_geometry",
    "clear_log",
    "collapsible",
    "create_log",
    "create_root",
    "fit_wraplength",
    "pair_row",
    "resolve_geometry",
    "run_window",
    "start_worker",
]

WORKER_INTERVAL_MS: Final[int] = 80
"""后台任务轮询间隔（毫秒）：沿用各窗口原先的 80ms，避免改变事件刷新节奏。"""

CJK_FONT_WARNING: Final[str] = (
    "当前 Tk 字体后端无法显示中文，窗口文字会显示为方块。"
    "WSL / Linux 下常见于 uv 独立构建的 Python（自带 Tk 未启用 Xft）或系统缺少中文字体。"
    "修复：安装 X11 中文核心字体后重开窗口（sudo apt install xfonts-wqy && xset fp rehash），"
    "或改用带 python3-tk 的系统 Python 运行 fcmd。"
)
"""中文缺字告警：所有窗口共用的启动自检文案。"""


def fit_wraplength(width: int) -> int:
    """按标签当前宽度计算换行宽度：留 8px 余量，并设 120px 下限防止塌陷。"""
    return max(width - 8, 120)


def pair_row(  # noqa: PLR0913, PLR0917 - 两列控件布局本就需要 6 个位置参数，与调用点顺序一致
    target: Any,
    row: int,
    label: str,
    widget: Any,
    right_label: str,
    right_widget: Any,
    *,
    theme: Theme,
) -> None:  # pragma: no cover - 需要真实 Tk 控件
    """在第 ``row`` 行放置两组「标签 + 输入控件」，右侧标签留空时只占位。

    两个 LLM 窗口曾各复制一份同名 ``_pair_row``；间距全部来自主题，没有窗口
    私有常量，因此收敛为直接调用。
    """
    from tkinter import ttk

    space = theme.spacing
    ttk.Label(target, text=label).grid(row=row, column=0, sticky="w", padx=(0, space.sm), pady=space.xxs)
    widget.grid(row=row, column=1, sticky="ew", padx=(0, space.md), pady=space.xxs)
    if right_label:
        ttk.Label(target, text=right_label).grid(row=row, column=2, sticky="w", padx=(0, space.sm), pady=space.xxs)
    right_widget.grid(row=row, column=3, sticky="ew", pady=space.xxs)


def clamp_saved_geometry(
    saved: str | None,
    screen_width: int,
    screen_height: int,
    *,
    min_width: int,
    min_height: int,
) -> str | None:
    """把记忆的 geometry 夹回屏幕可见范围；非法或缺失时返回 ``None``。"""
    return _guiconfig.clamp_geometry(
        saved,
        screen_width,
        screen_height,
        min_width=min_width,
        min_height=min_height,
    )


def resolve_geometry(  # noqa: PLR0913 - 与 clamp_geometry 参数对齐，显式传入屏幕与最小尺寸
    saved: str | None,
    screen_width: int,
    screen_height: int,
    *,
    default: str | Callable[[int, int], str],
    min_width: int,
    min_height: int,
) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（同样按屏幕夹紧）。

    ``default`` 可以是固定字符串，也可以是 ``(屏幕宽, 屏幕高) -> geometry`` 的
    回调，供按屏幕比例计算初始尺寸的窗口使用。
    """
    restored = clamp_saved_geometry(
        saved,
        screen_width,
        screen_height,
        min_width=min_width,
        min_height=min_height,
    )
    if restored is not None:
        return restored
    default_text = default(screen_width, screen_height) if callable(default) else default
    clamped = clamp_saved_geometry(
        default_text,
        screen_width,
        screen_height,
        min_width=min_width,
        min_height=min_height,
    )
    return clamped or default_text


def centered_geometry(  # noqa: PLR0913 - 纯几何计算，参数与父窗口/屏幕坐标一一对应
    width: int,
    height: int,
    *,
    parent_x: int,
    parent_y: int,
    parent_width: int,
    parent_height: int,
    screen_width: int,
    screen_height: int,
) -> str:
    """计算在父窗口内居中的 Toplevel geometry，并夹回屏幕可见范围。"""
    x = parent_x + (parent_width - width) // 2
    y = parent_y + (parent_height - height) // 2
    x = min(max(x, 0), max(screen_width - width, 0))
    y = min(max(y, 0), max(screen_height - height, 0))
    return f"{width}x{height}+{x}+{y}"


def center_over_parent(  # pragma: no cover - 需要真实 Tk 窗口
    window: Any,
    parent: Any,
    *,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """把 Toplevel 放到父窗口中央；父窗口尚未映射时按屏幕居中。

    ``width`` / ``height`` 省略时使用窗口自身布局算出的请求尺寸，适合没有显式
    geometry 的自动布局对话框；显式传入时保持固定尺寸并居中。
    """
    if width is None or height is None:
        window.update_idletasks()
        width = window.winfo_reqwidth() if width is None else width
        height = window.winfo_reqheight() if height is None else height
    parent_width = parent.winfo_width()
    parent_height = parent.winfo_height()
    if parent_width <= 1 or parent_height <= 1:
        parent_x = 0
        parent_y = 0
        parent_width = parent.winfo_screenwidth()
        parent_height = parent.winfo_screenheight()
    else:
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
    window.geometry(
        centered_geometry(
            width,
            height,
            parent_x=parent_x,
            parent_y=parent_y,
            parent_width=parent_width,
            parent_height=parent_height,
            screen_width=parent.winfo_screenwidth(),
            screen_height=parent.winfo_screenheight(),
        )
    )


def create_root(  # noqa: PLR0913 - 关键字参数描述窗口骨架，显式优于打包配置对象
    *,
    title: str,
    saved_geometry: str | None,
    default_geometry: str | Callable[[int, int], str],
    min_width: int,
    min_height: int,
    on_close: Callable[[], None],
    theme: str | _guitheme.Theme | None = None,
) -> tuple[Tk, Theme]:  # pragma: no cover - 需要真实 Tk 窗口
    """创建根窗口：应用主题、标题、geometry、最小尺寸并绑定关闭回调。

    ``theme`` 为 ``None`` 时沿用持久化主题配置；显式传入时优先使用（非法值回落默认）。
    """
    import tkinter as tk

    root = tk.Tk()
    theme = _guitheme.apply_theme(root, theme)
    if not _guitheme.can_render_cjk(root):
        from fcmd.console import print_error

        print_error(CJK_FONT_WARNING)
    root.title(title)
    root.geometry(
        resolve_geometry(
            saved_geometry,
            root.winfo_screenwidth(),
            root.winfo_screenheight(),
            default=default_geometry,
            min_width=min_width,
            min_height=min_height,
        )
    )
    root.minsize(min_width, min_height)
    root.protocol("WM_DELETE_WINDOW", on_close)
    with contextlib.suppress(Exception):
        root.update_idletasks()
        _guitheme.apply_dark_titlebar(root, dark=_guitheme.is_dark_palette(theme.palette))
    return root, theme


def build_menu(
    root: Misc,
    *,
    on_close: Callable[[], None],
    on_about: Callable[[], None] | None = None,
    help_items: Sequence[tuple[str, Callable[[], None]]] = (),
) -> Any:  # pragma: no cover - 需要真实 Tk 控件
    """构建「文件 / 帮助」两栏菜单栏：``help_items`` 排在「关于」之前。"""
    import tkinter as tk

    menubar = tk.Menu(root)
    file_menu = tk.Menu(menubar, tearoff=False)
    file_menu.add_command(label="退出", command=on_close)
    menubar.add_cascade(label="文件", menu=file_menu)
    help_menu = tk.Menu(menubar, tearoff=False)
    for label, command in help_items:
        help_menu.add_command(label=label, command=command)
    if on_about is not None:
        help_menu.add_command(label="关于", command=on_about)
    menubar.add_cascade(label="帮助", menu=help_menu)
    cast(Any, root).configure(menu=menubar)
    return menubar


def create_log(parent: Misc, *, height: int = 12, grid: bool = False) -> Any:  # pragma: no cover
    """创建只读日志文本区（等宽字体 + 垂直滚动条），返回 ``Text`` 控件。

    ``grid`` 为假时用 pack（文本在左、滚动条在右），为真时用 grid（第 0 行的
    第 0 / 1 列），由调用方提前设置行列权重。
    """
    import tkinter as tk
    from tkinter import ttk

    text = tk.Text(parent, height=height, wrap="word", state="disabled", font=_guitheme.MONO_FONT)
    scrollbar = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scrollbar.set)
    if grid:
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
    else:
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
    return text


def collapsible(
    parent: Misc,
    title: str,
    *,
    padding: tuple[int, int, int, int],
    open_state: bool = True,
) -> tuple[Any, Any]:  # pragma: no cover - 需要真实 Tk 控件
    """创建可折叠分区，返回（外层容器, 内容 Frame）；``padding`` 为内容区内边距。"""
    from tkinter import ttk

    container = ttk.Frame(parent)
    header = ttk.Frame(container)
    header.pack(fill="x")
    body = ttk.Frame(container, padding=padding)
    state = {"open": open_state}

    def render() -> None:
        if state["open"]:
            body.pack(fill="x")
        else:
            body.pack_forget()
        button.configure(text=("▼ " if state["open"] else "▶ ") + title)

    def flip() -> None:
        state["open"] = not state["open"]
        render()

    button = ttk.Button(header, text="", style="Toolbutton", command=flip)
    button.pack(fill="x", anchor="w")
    render()
    return container, body


def append_log(log: Any, message: str, *, timestamp: bool = False) -> None:
    """向只读日志控件追加一行并滚动到底部；``timestamp`` 为真时加 ``[HH:MM:SS]``。"""
    if timestamp:
        text = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
    else:
        text = f"{message}\n"
    log.configure(state="normal")
    log.insert("end", text)
    log.see("end")
    log.configure(state="disabled")


def clear_log(log: Any) -> None:
    """清空只读日志控件。"""
    log.configure(state="normal")
    log.delete("1.0", "end")
    log.configure(state="disabled")


def start_worker(
    root: Any,
    target: Callable[..., None],
    args: tuple[Any, ...] = (),
    poll: Callable[[], None] | None = None,
    *,
    interval_ms: int = WORKER_INTERVAL_MS,
) -> threading.Thread:
    """启动 daemon 线程，并在主线程用 ``root.after`` 启动首次轮询。"""
    worker = threading.Thread(target=target, args=args, daemon=True)
    worker.start()
    if poll is not None:
        root.after(interval_ms, poll)
    return worker


def run_window(factory: Callable[[], Any], message: str) -> None:
    """DPI 感知 → 创建窗口 → 进入 mainloop；tkinter 缺失时提示并返回。"""
    _guitheme.enable_dpi_awareness()
    try:
        window = factory()
    except ImportError as exc:
        from fcmd.console import print_error

        print_error(f"tkinter 不可用，无法启动{message}: {exc}")
        return
    window.root.mainloop()
