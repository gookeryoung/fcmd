"""GUI 窗口共享骨架（不注册工具）。

10 个 ``*w.py`` 窗口与 ``checksum`` / ``alarmclock`` 的窗口类各自重复了同一套
骨架：创建根窗口（DPI 感知、主题、标题、geometry、最小尺寸、关闭协议）、恢复
窗口位置、构建菜单栏、只读日志区、启动后台 daemon 线程。

本模块收敛上述部分；后台事件（进度 / 完成 / 错误）的语义与状态更新差异较大，
轮询逻辑仍留在各窗口。本模块位于 :mod:`fcmd.gui`，不参与 :mod:`fcmd.tools` 的工具发现。
"""

from __future__ import annotations

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
    "clamp_saved_geometry",
    "clear_log",
    "collapsible",
    "create_log",
    "create_root",
    "fit_wraplength",
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


def create_root(  # noqa: PLR0913 - 关键字参数描述窗口骨架，显式优于打包配置对象
    *,
    title: str,
    saved_geometry: str | None,
    default_geometry: str | Callable[[int, int], str],
    min_width: int,
    min_height: int,
    on_close: Callable[[], None],
) -> tuple[Tk, Theme]:  # pragma: no cover - 需要真实 Tk 窗口
    """创建根窗口：应用主题、标题、geometry、最小尺寸并绑定关闭回调。"""
    import tkinter as tk

    root = tk.Tk()
    theme = _guitheme.apply_theme(root)
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
