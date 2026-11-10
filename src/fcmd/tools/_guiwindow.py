"""GUI 窗口共享骨架（不注册工具）。

10 个 ``*w.py`` 窗口与 ``checksum`` / ``alarmclock`` 的窗口类各自重复了同一套
骨架：创建根窗口（DPI 感知、主题、标题、geometry、最小尺寸、关闭协议）、恢复
窗口位置、构建菜单栏、只读日志区、启动后台 daemon 线程。

本模块收敛上述部分；后台事件（进度 / 完成 / 错误）的语义与状态更新差异较大，
轮询逻辑仍留在各窗口。文件名以下划线开头，``fcmd.tools`` 的工具发现会跳过本模块。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, cast

from fcmd.tools import _guiconfig, _guitheme

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from tkinter import Misc, Tk

    from fcmd.tools._guitheme import Theme

__all__ = [
    "WORKER_INTERVAL_MS",
    "append_log",
    "build_menu",
    "clamp_saved_geometry",
    "clear_log",
    "create_log",
    "create_root",
    "resolve_geometry",
    "run_window",
    "start_worker",
]

WORKER_INTERVAL_MS: Final[int] = 80
"""后台任务轮询间隔（毫秒）：沿用各窗口原先的 80ms，避免改变事件刷新节奏。"""


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
    default: str,
    min_width: int,
    min_height: int,
) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（同样按屏幕夹紧）。"""
    restored = clamp_saved_geometry(
        saved,
        screen_width,
        screen_height,
        min_width=min_width,
        min_height=min_height,
    )
    if restored is not None:
        return restored
    clamped = clamp_saved_geometry(
        default,
        screen_width,
        screen_height,
        min_width=min_width,
        min_height=min_height,
    )
    return clamped or default


def create_root(  # noqa: PLR0913 - 关键字参数描述窗口骨架，显式优于打包配置对象
    *,
    title: str,
    saved_geometry: str | None,
    default_geometry: str,
    min_width: int,
    min_height: int,
    on_close: Callable[[], None],
) -> tuple[Tk, Theme]:  # pragma: no cover - 需要真实 Tk 窗口
    """创建根窗口：应用主题、标题、geometry、最小尺寸并绑定关闭回调。"""
    import tkinter as tk

    root = tk.Tk()
    theme = _guitheme.apply_theme(root)
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
