"""wslmanw - WSL 发行版管理的 Tk 窗口。

窗口只做编排：业务判断仍走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出核心、预检、注销前备份），
避免 GUI 与 CLI 两套口径。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``。
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman._config import WslmanConfig, load_config
from fcmd.apps.wslman._disk import distro_disk_bytes, format_bytes, recommended_backup_dir
from fcmd.apps.wslman._distro import Distro, state_label
from fcmd.apps.wslman._errors import WslmanError, invalid_argument, runtime_error
from fcmd.apps.wslman._terminal import LaunchError, SubprocessLauncher, build_command
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "SETTINGS_NAME",
    "WslmanWindow",
    "WslmanwSettings",
    "action_states",
    "clamp_poll_ms",
    "column_widths",
    "config_path",
    "detail_fields",
    "detail_lines",
    "distro_row",
    "elide_name",
    "load_settings",
    "main",
    "poll_label",
    "resolve_geometry",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "shutdown_confirm_enabled",
    "summary_text",
    "text_units",
    "unregister_confirm_enabled",
]

SETTINGS_NAME = "wslmanw"
DEFAULT_GEOMETRY = "980x660"
MIN_WIDTH = 900
MIN_HEIGHT = 600

_DESCRIPTION = "WSL 发行版管理的图形界面：列表/详情、打开终端、导出导入、下载、注销与关闭全部"

_DOWNLOAD_SOURCES = ("auto", "cli", "manifest", "file")

DEFAULT_POLL_MS = 3000
"""默认 3 秒：单次 wsl -l -v 通常几十到一百多毫秒，3 秒足以看清状态迁移，
又不会让后台频繁拉起 wsl.exe。"""

POLL_MIN_MS = 1000
POLL_MAX_MS = 30000

POLL_CHOICES: tuple[tuple[int, str], ...] = (
    (2000, "2 秒"),
    (3000, "3 秒"),
    (5000, "5 秒"),
    (10000, "10 秒"),
    (30000, "30 秒"),
)
"""下拉框可选轮询间隔；旧文件里的其它取值按区间夹取后显示为秒数。"""

HIDDEN_FACTOR = 3
"""窗口失焦时把轮询间隔放大到 3 倍；最小化时直接暂停。"""

_DOWNLOAD_HINT = "wsl --install 会触发系统级安装、可能弹 UAC，且不能指定自定义镜像"

_COLUMNS: tuple[tuple[str, str, int, int], ...] = (
    ("name", "名称", 160, 360),
    ("state", "状态", 72, 120),
    ("version", "版本", 56, 84),
    ("disk", "占用", 96, 180),
    ("default", "默认", 50, 80),
)
"""列定义：``(列 id, 表头, 最小宽度, 最大宽度)``，宽度按内容自适应后夹在两者之间。"""

_CHAR_PX = 7
"""窄字符的估算宽度（9pt 界面字体）；宽字符按 2 个窄字符计。"""

_CELL_PAD_PX = 16
"""单元格左右留白与排序指示器的估算宽度。"""

_DETAIL_LABELS: tuple[str, ...] = (
    "名称",
    "状态",
    "版本",
    "默认",
    "BasePath",
    "虚拟磁盘",
    "占用",
    "默认用户",
    "来源",
)
"""详情页字段顺序：前 5 项为身份信息，后 4 项为存储信息。"""

_DETAIL_ROWS = 5
"""详情页左右两列各占的行数，长路径换行后仍不会压叠。"""


def text_units(text: str) -> int:
    """按显示宽度计字符数：CJK / 全角字符算 2，其余算 1。"""
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text)


def elide_name(name: str, limit: int = 24) -> str:
    """按显示宽度截断过长的发行版名，末尾补 ``…``；未超长时原样返回。"""
    if text_units(name) <= limit:
        return name
    kept: list[str] = []
    used = 0
    for char in name:
        width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + width > limit - 1:
            break
        kept.append(char)
        used += width
    return "".join(kept) + "…"


def column_widths(rows: Sequence[Sequence[str]], available: int | None = None) -> dict[str, int]:
    """按表头与各行显示宽度估算列宽，夹在每列的最小 / 最大宽度之间。

    ``available`` 为列表可用总宽时，把富余宽度按各列基础宽度的比例分摊，
    而不是全部塞给最后一列；分摊后仍以每列最大宽度为上限，剩余留白留在右侧。
    """
    widths: dict[str, int] = {}
    for index, (key, title, minimum, maximum) in enumerate(_COLUMNS):
        widest = max([text_units(title), *(text_units(str(row[index])) for row in rows if index < len(row))])
        widths[key] = min(maximum, max(minimum, widest * _CHAR_PX + _CELL_PAD_PX))
    if available is None:
        return widths
    extra = available - sum(widths.values())
    total = sum(widths.values())
    if extra <= 0 or total <= 0:
        return widths
    for key, _title, _minimum, maximum in _COLUMNS:
        share = int(extra * widths[key] / total)
        widths[key] = min(maximum, widths[key] + share)
    return widths


def detail_fields(distro: Distro, size: int | None, estimated: bool) -> tuple[tuple[str, str], ...]:
    """详情页的「字段 → 值」；字段口径与 ``detail_lines`` 完全一致。"""
    vhdx = str(Path(distro.base_path) / "ext4.vhdx") if distro.base_path else "未知"
    return (
        ("名称", distro.name),
        ("状态", state_label(distro)),
        ("版本", str(distro.version) if distro.version else "未知"),
        ("默认", "是" if distro.default else "否"),
        ("BasePath", distro.base_path or "未知"),
        ("虚拟磁盘", vhdx),
        ("占用", format_bytes(size, estimated=estimated)),
        ("默认用户", str(distro.default_uid) if distro.default_uid is not None else "未知"),
        ("来源", "Lxss 注册表" if distro.registered else "wsl --list"),
    )


def summary_text(distro: Distro, size: int | None, estimated: bool) -> str:
    """状态栏的完整发行版摘要；列表里截断的名称在这里显示全名。"""
    return (
        f"{distro.name} · {state_label(distro)} · "
        f"v{distro.version or '未知'} · {format_bytes(size, estimated=estimated)}"
    )


def clamp_poll_ms(value: int) -> int:
    """把轮询间隔夹在 1–30 秒，防止旧设置或手改 JSON 造成空转。"""
    return min(POLL_MAX_MS, max(POLL_MIN_MS, value))


def poll_label(value: int) -> str:
    """把毫秒间隔转成下拉框文案；非预设值显示为整数秒。"""
    for milliseconds, label in POLL_CHOICES:
        if milliseconds == value:
            return label
    return f"{max(1, value // 1000)} 秒"


def action_states(state: str | None) -> tuple[bool, bool]:
    """按选中行状态返回「启动可用, 停止可用」；未选中时两个都禁用。"""
    if state == "running":
        return False, True
    return (state is not None), False


@dataclass(frozen=True, slots=True)
class WslmanwSettings:
    geometry: str | None = None
    download_dir: str = ""
    manifest_url: str = ""
    download_source: str = "auto"
    auto_refresh: bool = True
    poll_interval_ms: int = DEFAULT_POLL_MS


def settings_to_mapping(settings: WslmanwSettings) -> dict[str, object]:
    return {
        "geometry": settings.geometry,
        "download_dir": settings.download_dir,
        "manifest_url": settings.manifest_url,
        "download_source": settings.download_source,
        "auto_refresh": settings.auto_refresh,
        "poll_interval_ms": settings.poll_interval_ms,
    }


def settings_from_mapping(raw: Mapping[str, Any]) -> WslmanwSettings:
    source = _str(raw, "download_source") or "auto"
    if source not in _DOWNLOAD_SOURCES:
        source = "auto"
    interval = raw.get("poll_interval_ms", DEFAULT_POLL_MS)
    if isinstance(interval, bool) or not isinstance(interval, int):
        interval = DEFAULT_POLL_MS
    auto_refresh = raw.get("auto_refresh", True)
    return WslmanwSettings(
        geometry=_guiconfig.valid_geometry(_str(raw, "geometry")),
        download_dir=_str(raw, "download_dir"),
        manifest_url=_str(raw, "manifest_url"),
        download_source=source,
        auto_refresh=auto_refresh if isinstance(auto_refresh, bool) else True,
        poll_interval_ms=clamp_poll_ms(interval),
    )


def _str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""


def config_path() -> Path:
    """返回设置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> WslmanwSettings:
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return WslmanwSettings()
    return settings_from_mapping(raw)


def save_settings(settings: WslmanwSettings, path: Path | None = None) -> None:
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def unregister_confirm_enabled(typed: str, name: str) -> bool:
    """注销按钮只在手输名称与目标完全一致（忽略大小写与首尾空格）时可用。"""
    return bool(name) and typed.strip().casefold() == name.strip().casefold()


def shutdown_confirm_enabled(checked: bool) -> bool:
    """``wsl --shutdown`` 必须显式勾选影响说明。"""
    return checked is True


def distro_row(distro: Distro, size: int | None, estimated: bool) -> tuple[str, ...]:
    """Treeview 一行：名称（过长截断）、状态、版本、磁盘、默认标记。"""
    return (
        elide_name(distro.name),
        state_label(distro),
        str(distro.version) if distro.version else "未知",
        format_bytes(size, estimated=estimated),
        "是" if distro.default else "",
    )


def detail_lines(distro: Distro, size: int | None, estimated: bool) -> list[str]:
    """详情面板文本行；与 :func:`detail_fields` 同源，未知字段统一显示 ``未知``。"""
    return [f"{label}: {value}" for label, value in detail_fields(distro, size, estimated)]


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, WslmanError):
        text = str(exc)
        return f"{text}（{exc.hint}）" if exc.hint else text
    return f"{type(exc).__name__}: {exc}"


class WslmanWindow:  # pragma: no cover - 需要真实 Tk 窗口
    """主窗口：左侧发行版列表 + 详情，右侧分页放下载与日志。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.simpledialog = simpledialog
        self.settings_path = settings_path or config_path()
        self.settings = load_settings(self.settings_path)
        self.config_data: WslmanConfig = load_config()
        self.launcher = SubprocessLauncher()
        self.view: Any = None
        self.sizes: dict[str, tuple[int | None, bool]] = {}
        self._events: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self._busy = False
        self._cancel = threading.Event()
        self._label = ""
        self._rows: list[tuple[str, ...]] = []
        self._poll_after: str | None = None
        self._status_busy = False
        self._last_status_at = 0.0
        self._closing = False

        self.root, self.theme = _guiwindow.create_root(
            title="WSL 管理",
            saved_geometry=self.settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._on_close,
        )
        # 主题间距已按 DPI 缩放（apply_theme 内走 scale_spacing），窗口只引用不另算。
        self.space = self.theme.spacing
        self.auto_refresh_var = tk.BooleanVar(value=self.settings.auto_refresh)
        self.poll_ms_var = tk.IntVar(value=self.settings.poll_interval_ms)
        self._build()
        _guiwindow.build_menu(self.root, on_close=self._on_close)
        _guitheme.install_theme_switcher(self.root)
        self.root.bind("<Map>", self._on_window_active)
        self.root.bind("<FocusIn>", self._on_window_active)
        self.root.after(100, self._poll)
        self.refresh()
        self._schedule_poll(self._poll_ms())

    # -- 布局 ---------------------------------------------------------- #

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=3)
        self.root.rowconfigure(2, weight=2)
        self._build_toolbar()
        self._build_content()
        self._build_notebook()
        self._build_status()

    def _build_toolbar(self) -> None:
        """按钮按危险等级分组：安全 / 变更 / 传输 / 破坏，破坏性用 danger 样式。"""
        ttk = self.ttk
        space = self.space
        bar = ttk.Frame(self.root, padding=(space.xs, space.xxs))
        bar.grid(row=0, column=0, sticky="ew")

        self._buttons: list[Any] = []
        self._button_by_text: dict[str, Any] = {}
        groups: tuple[tuple[tuple[str, Callable[[], None], str | None], ...], ...] = (
            (("刷新", self.refresh, None),),
            (
                ("打开终端", self._open_selected, "Accent.TButton"),
                ("启动", self._start_selected, None),
                ("停止", self._terminate_selected, None),
            ),
            (
                ("导出", self._export_selected, None),
                ("导入", self._import, None),
            ),
            (
                ("注销", self._unregister, "Danger.TButton"),
                ("关闭全部", self._shutdown, "Danger.TButton"),
            ),
        )
        for index, group in enumerate(groups):
            if index:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=space.xs, pady=space.xxs)
            for text, command, style in group:
                button = ttk.Button(bar, text=text, command=command, style=style or "TButton")
                button.pack(side="left", padx=(0, space.xxs))
                self._buttons.append(button)
                self._button_by_text[text] = button

        # 自动刷新开关放在右端：可整体关闭，间隔可选 2–30 秒。
        ttk.Separator(bar, orient="vertical").pack(side="right", fill="y", padx=space.xs, pady=space.xxs)
        self.poll_combo = ttk.Combobox(
            bar,
            width=6,
            state="readonly" if self.auto_refresh_var.get() else "disabled",
            values=[label for _milliseconds, label in POLL_CHOICES],
        )
        self.poll_combo.set(poll_label(self._poll_ms()))
        self.poll_combo.pack(side="right", padx=(0, space.xxs))
        self.poll_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_poll_choice())
        ttk.Checkbutton(
            bar,
            text="自动刷新",
            variable=self.auto_refresh_var,
            command=self._on_auto_toggle,
        ).pack(side="right", padx=(0, space.xs))

        self.start_button = self._button_by_text["启动"]
        self.stop_button = self._button_by_text["停止"]

    def _build_content(self) -> None:
        """发行版列表独占整幅宽度；详情移到笔记本页，不再挤占右栏。"""
        ttk = self.ttk
        space = self.space
        tree_wrap = ttk.Frame(self.root)
        tree_wrap.grid(row=1, column=0, sticky="nsew", padx=space.xs, pady=(0, space.xxs))
        tree_wrap.columnconfigure(0, weight=1)
        tree_wrap.rowconfigure(0, weight=1)

        columns = tuple(key for key, _title, _minimum, _maximum in _COLUMNS)
        self.tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", selectmode="browse")
        for column, title, minimum, _maximum in _COLUMNS:
            self.tree.heading(column, text=title)
            # 宽度由 column_widths 统一计算，stretch 关闭后不会把多余宽度堆给最后一列。
            self.tree.column(column, width=minimum, minwidth=minimum, anchor="w", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._show_details())
        self.tree.bind("<Motion>", self._on_hover)
        self.tree.bind("<Leave>", lambda _event: self.hover.set(""))
        self.tree.bind("<Double-1>", lambda _event: self._open_selected())
        self.tree.bind("<Configure>", self._on_tree_resize)

    def _build_notebook(self) -> None:
        ttk = self.ttk
        space = self.space
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=space.xs, pady=(0, space.xxs))
        self.notebook = notebook
        self._build_detail_tab(notebook)
        self._build_download_tab(notebook)
        log_frame = ttk.Frame(notebook, padding=space.xs)
        notebook.add(log_frame, text="日志")
        self.log = _guiwindow.create_log(log_frame, height=8)
        notebook.select(0)

    def _build_detail_tab(self, notebook: Any) -> None:
        """详情页：两列「字段 / 值」，窄高度下靠两列排布避免裁切与压叠。"""
        tk, ttk = self.tk, self.ttk
        space = self.space
        frame = ttk.Frame(notebook, padding=(space.sm, space.xs))
        notebook.add(frame, text="详情")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        self.detail_value_labels: list[Any] = []
        self.detail_vars: dict[str, Any] = {}
        for index, label in enumerate(_DETAIL_LABELS):
            group, row = divmod(index, _DETAIL_ROWS)
            column = group * 2
            ttk.Label(frame, text=label, style="Muted.TLabel", anchor="nw").grid(
                row=row, column=column, sticky="nw", padx=(0, space.xs), pady=space.xxs
            )
            variable = tk.StringVar(value="—")
            value = ttk.Label(frame, textvariable=variable, anchor="w", justify="left")
            value.grid(row=row, column=column + 1, sticky="ew", padx=(0, space.lg), pady=space.xxs)
            self.detail_vars[label] = variable
            self.detail_value_labels.append(value)

        self.detail_hint = tk.StringVar(value="选择上方列表中的发行版查看详情")
        ttk.Label(frame, textvariable=self.detail_hint, style="Hint.TLabel").grid(
            row=_DETAIL_ROWS, column=0, columnspan=4, sticky="w", pady=(space.xs, 0)
        )
        frame.bind("<Configure>", self._on_detail_resize)

    def _on_detail_resize(self, event: Any) -> None:
        """按当前半栏宽度设置换行点，长路径不会横向溢出。"""
        half = max(160, (event.width - int(self.space.lg) * 2) // 2)
        for label in self.detail_value_labels:
            label.configure(wraplength=max(120, half - 80))

    def _build_status(self) -> None:
        ttk = self.ttk
        space = self.space
        bar = ttk.Frame(self.root)
        bar.grid(row=3, column=0, sticky="ew")
        ttk.Separator(bar, orient="horizontal").pack(fill="x")
        inner = ttk.Frame(bar, padding=(space.xs, space.xxs))
        inner.pack(fill="x")
        self.hover = self.tk.StringVar(value="")
        ttk.Label(inner, textvariable=self.hover, style="Muted.TLabel", anchor="w").pack(
            side="left", fill="x", expand=True
        )
        self.status = self.tk.StringVar(value="就绪")
        ttk.Label(inner, textvariable=self.status, anchor="e").pack(side="right")

    def _build_download_tab(self, notebook: Any) -> None:
        ttk = self.ttk
        space = self.space
        frame = ttk.Frame(notebook, padding=(space.sm, space.xs))
        notebook.add(frame, text="下载")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        self.download_name = self._field(frame, 0, 0, "名称", "Ubuntu")
        self.download_source = self.tk.StringVar(value=self.settings.download_source)
        ttk.Label(frame, text="来源").grid(row=0, column=2, sticky="w", padx=(space.md, space.xs), pady=space.xxs)
        ttk.Combobox(
            frame,
            textvariable=self.download_source,
            values=_DOWNLOAD_SOURCES,
            state="readonly",
            width=8,
        ).grid(row=0, column=3, sticky="w", pady=space.xxs)

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(space.xs, 0))
        ttk.Button(buttons, text="开始下载", command=self._download, style="Accent.TButton").pack(side="left")
        ttk.Button(buttons, text="取消", command=self._cancel_task).pack(side="left", padx=(space.xs, 0))
        ttk.Label(buttons, text=_DOWNLOAD_HINT, style="Hint.TLabel", wraplength=380).pack(
            side="left", padx=(space.md, 0)
        )

        advanced = ttk.Frame(frame)
        advanced.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(space.xs, 0))
        container, body = _guiwindow.collapsible(
            advanced,
            "高级：下载目录 / 清单源 / 校验",
            padding=(space.sm, space.xs, space.sm, space.xs),
            open_state=False,
        )
        container.pack(fill="x")
        body.columnconfigure(1, weight=1)
        self.download_dir = self._field(body, 0, 0, "下载目录", self.settings.download_dir)
        self.manifest_url = self._field(body, 1, 0, "清单地址", self.settings.manifest_url)
        self.expected_sha = self._field(body, 2, 0, "期望 sha256", "")

    def _field(self, parent: Any, row: int, column: int, label: str, default: str) -> Any:
        """紧凑的「标签 + 输入框」；列坐标以 ``column`` 为标签列。"""
        space = self.space
        self.ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, space.xs), pady=space.xxs)
        variable = self.tk.StringVar(value=default)
        self.ttk.Entry(parent, textvariable=variable).grid(row=row, column=column + 1, sticky="ew", pady=space.xxs)
        return variable

    # -- 任务编排 ------------------------------------------------------ #

    def _start(self, label: str, work: Callable[[threading.Event], str]) -> None:
        if self._busy:
            self.messagebox.showinfo("请稍候", "已有任务在执行")
            return
        self._busy = True
        self._label = label
        self._cancel = threading.Event()
        self.status.set(f"{label}…")
        self._set_buttons(False)
        cancel = self._cancel

        def runner() -> None:
            try:
                message = work(cancel)
            except KeyboardInterrupt:
                self._events.put(("cancelled", label, None))
            except WslmanError as exc:
                self._events.put(("error", label, exc))
            except Exception as exc:
                self._events.put(("error", label, exc))
            else:
                self._events.put(("done", label, message))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, label, payload = self._events.get_nowait()
                if kind == "log":
                    self._log(payload)
                    continue
                if kind == "status":
                    self._status_busy = False
                    if not self._busy:
                        self._apply_status(payload)
                    continue
                if kind == "status_error":
                    self._status_busy = False
                    if not self._busy:
                        self.status.set(f"状态刷新失败：{_error_text(payload)}")
                    continue
                self._busy = False
                self._set_buttons(True)
                if kind == "done":
                    self._log(f"[{label}] {payload}")
                    self.status.set(f"{label}完成")
                    if label == "刷新":
                        self._refresh_rows()
                    else:
                        self.refresh()
                    break
                if kind == "cancelled":
                    self._log(f"[{label}] 已取消；未完成的 .part 按配置保留或清理")
                    self.status.set(f"{label}已取消")
                    continue
                self._log(f"[{label}] 失败：{_error_text(payload)}")
                self.status.set(f"{label}失败")
                self.messagebox.showerror(label, _error_text(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._buttons:
            button.configure(state=state)
        if enabled:
            self._update_action_states()

    # -- 运行状态自动刷新 ---------------------------------------------- #

    def _poll_ms(self) -> int:
        """当前设置的轮询间隔（毫秒），越界值按 1–30 秒夹取。"""
        try:
            value = int(self.poll_ms_var.get())
        except (ValueError, TypeError):
            value = DEFAULT_POLL_MS
        return clamp_poll_ms(value)

    def _poll_delay(self) -> int:
        """本次轮询应等待的毫秒数：最小化返回 0（暂停），失焦放大 ``HIDDEN_FACTOR`` 倍。"""
        if self.root.state() == "iconic":
            return 0
        if not self.root.focus_displayof():
            return self._poll_ms() * HIDDEN_FACTOR
        return self._poll_ms()

    def _schedule_poll(self, milliseconds: int | None = None) -> None:
        """重置单次定时器；``milliseconds`` 为 ``None`` 时按窗口状态决定间隔。"""
        self._cancel_poll()
        if self._closing or not self.auto_refresh_var.get():
            return
        delay = self._poll_ms() if milliseconds is not None else self._poll_delay()
        if delay <= 0:
            return
        self._poll_after = self.root.after(delay, self._poll_tick)

    def _cancel_poll(self) -> None:
        if self._poll_after is None:
            return
        with contextlib.suppress(self.tk.TclError):
            self.root.after_cancel(self._poll_after)
        self._poll_after = None

    def _poll_tick(self) -> None:
        self._poll_after = None
        if self._closing or not self.auto_refresh_var.get():
            return
        self._request_status()
        self._schedule_poll()

    def _request_status(self) -> None:
        """发起一次后台状态查询；上一次未完成时直接跳过，避免任务叠加。"""
        if self._status_busy or self._busy:
            return
        self._status_busy = True
        self._last_status_at = time.monotonic()
        threading.Thread(target=self._status_worker, daemon=True).start()

    def _status_worker(self) -> None:
        try:
            view = cli._view(cli._build_environment())
        except WslmanError as exc:
            self._events.put(("status_error", "状态刷新", exc))
        except Exception as exc:
            self._events.put(("status_error", "状态刷新", exc))
        else:
            self._events.put(("status", "状态刷新", view))

    def _apply_status(self, view: Any) -> None:
        """只改状态列与按钮态，不重建整表：选中行、滚动位置、详情页输入都不受影响。"""
        previous = self.view
        self.view = view
        names = [distro.name for distro in view.distros]
        if previous is None or names != [distro.name for distro in previous.distros]:
            # 增删发行版属于结构性变化，此时才整表重建（内部保留选中行）。
            self._refresh_rows()
            return
        for distro in view.distros:
            if self.tree.exists(distro.name):
                self.tree.set(distro.name, "state", state_label(distro))
        self._update_action_states()
        self._show_details()

    def _on_window_active(self, event: Any) -> None:
        """窗口恢复或重新获得焦点：立即刷新一次，再按当前状态重排定时器。"""
        if self._closing or event.widget is not self.root:
            return
        if self.root.state() == "iconic":
            self._cancel_poll()
            return
        self._cancel_poll()
        self._request_status()
        self._schedule_poll()

    def _on_auto_toggle(self) -> None:
        self.poll_combo.configure(state="readonly" if self.auto_refresh_var.get() else "disabled")
        self._save_settings()
        if self.auto_refresh_var.get():
            self.status.set("自动刷新已开启")
            self._request_status()
            self._schedule_poll()
        else:
            self._cancel_poll()
            self._status_busy = False
            self.status.set("自动刷新已关闭")

    def _on_poll_choice(self) -> None:
        chosen = self.poll_combo.get()
        for milliseconds, label in POLL_CHOICES:
            if label == chosen:
                self.poll_ms_var.set(milliseconds)
                break
        self._save_settings()
        self._schedule_poll()
        self.status.set(f"自动刷新间隔：{poll_label(self._poll_ms())}")

    def _update_action_states(self) -> None:
        """按选中行的运行状态切换「启动 / 停止」；未选中或任务进行中时都禁用。"""
        if self._busy:
            return
        distro = self._selected()
        can_start, can_stop = action_states(distro.state if distro is not None else None)
        for button, enabled in ((self.start_button, can_start), (self.stop_button, can_stop)):
            button.configure(state="normal" if enabled else "disabled")

    def _save_settings(self) -> None:
        save_settings(
            WslmanwSettings(
                geometry=self.root.geometry(),
                download_dir=self.download_dir.get().strip(),
                manifest_url=self.manifest_url.get().strip(),
                download_source=self.download_source.get(),
                auto_refresh=self.auto_refresh_var.get(),
                poll_interval_ms=self._poll_ms(),
            ),
            self.settings_path,
        )

    def _cancel_task(self) -> None:
        if self._busy:
            self._cancel.set()
            self.status.set(f"{self._label}取消中…")

    def _on_close(self) -> None:
        if self._closing:
            return
        if self._busy:
            if not self.messagebox.askyesno("任务进行中", "有任务正在执行，取消并退出？"):
                return
            self._cancel.set()
        self._closing = True
        self._cancel_poll()
        self._save_settings()
        self.root.destroy()

    # -- 列表与详情 ---------------------------------------------------- #

    def refresh(self) -> None:
        if self._busy:
            return
        self._start("刷新", lambda _cancel: self._load())

    def _load(self) -> str:
        env = cli._build_environment()
        view = cli._view(env)
        self.view = view
        sizes: dict[str, tuple[int | None, bool]] = {}
        for distro in view.distros:
            sizes[distro.name] = distro_disk_bytes(distro.base_path, distro.version)
        self.sizes = sizes
        return f"已加载 {len(view.distros)} 个发行版"

    def _selected(self) -> Distro | None:
        selection = self.tree.selection()
        if not selection or self.view is None:
            return None
        return self.view.find(selection[0])

    def _show_details(self) -> None:
        """只刷新详情页与状态栏摘要，不切换当前页签。"""
        distro = self._selected()
        if distro is None:
            for variable in self.detail_vars.values():
                variable.set("—")
            self.detail_hint.set("选择上方列表中的发行版查看详情")
            self.hover.set("")
            self._update_action_states()
            return
        size, estimated = self.sizes.get(distro.name, (None, False))
        for label, value in detail_fields(distro, size, estimated):
            self.detail_vars[label].set(value)
        self.detail_hint.set("")
        self.hover.set(summary_text(distro, size, estimated))
        self._update_action_states()

    def _on_hover(self, event: Any) -> None:
        """悬浮显示完整发行版名（列表里过长会截断）。"""
        name = self.tree.identify_row(event.y)
        distro = self.view.find(name) if name and self.view is not None else None
        if distro is None:
            self.hover.set("")
            return
        self.hover.set(summary_text(distro, *self.sizes.get(distro.name, (None, False))))

    def _refresh_rows(self) -> None:
        view = self.view
        if view is None:
            self._rows = []
            self._apply_column_widths(self.tree.winfo_width())
            return
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        row_values: list[tuple[str, ...]] = []
        for distro in view.distros:
            row = distro_row(distro, *self.sizes.get(distro.name, (None, False)))
            row_values.append(row)
            self.tree.insert("", "end", iid=distro.name, values=row)
        self._rows = row_values
        self._apply_column_widths(self.tree.winfo_width())
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self._show_details()

    def _on_tree_resize(self, event: Any) -> None:
        self._apply_column_widths(event.width)

    def _apply_column_widths(self, available: int) -> None:
        """按列表可用宽度分摊列宽；``available`` 无效时只按内容夹取。"""
        widths = column_widths(self._rows, available if available > 1 else None)
        for column, _title, minimum, _maximum in _COLUMNS:
            if int(self.tree.column(column, "width")) != widths[column]:
                self.tree.column(column, width=widths[column], minwidth=minimum)

    def _log(self, text: str) -> None:
        _guiwindow.append_log(self.log, text, timestamp=True)

    # -- 操作 ---------------------------------------------------------- #

    def _open_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        try:
            env = cli._build_environment()
            command = build_command(
                wsl_path=cli._require_wsl(env),
                distro=distro.name,
                cwd=None,
                user=None,
                program=env.config.terminal_program,
                extra_args=env.config.terminal_extra_args,
            )
            self.launcher.launch(command)
        except (LaunchError, WslmanError) as exc:
            self.messagebox.showerror("打开失败", _error_text(exc))
            return
        self._log(f"[打开] 已启动 {distro.name}")

    def _start_selected(self) -> None:
        """后台启动发行版：``--exec`` 执行完即退出，不弹终端窗口。"""
        distro = self._selected()
        if distro is None:
            return
        if distro.state == "running":
            self.status.set(f"{distro.name} 已在运行")
            self._update_action_states()
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            target = cli._require_target(env, distro.name)
            if target.state == "running":
                return f"{target.name} 已在运行"
            # 用一次性命令把发行版带起来；直接 wsl -d 会进交互式 shell 并挂住线程。
            cli._run_step(
                env,
                ["-d", target.name, "--exec", "/bin/true"],
                action=f"启动 {target.name}",
                hint="确认发行版仍已注册；若 WSL 未启用请以管理员运行 wsl --install",
                timeout=120.0,
            )
            current = cli._require_target(env, target.name)
            if current.state != "running":
                raise runtime_error(
                    f"{target.name} 启动后状态仍为 {state_label(current)}",
                    hint="运行 fcmd wslman doctor 检查 WSL 状态，稍后可点「刷新」再次确认",
                )
            return f"已启动 {target.name}"

        self._start("启动", work)

    def _terminate_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        if not self.messagebox.askyesno("停止发行版", f"停止 {distro.name}？正在运行的任务会被中断。"):
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            # CLI 子命令仍是 terminate，只有展示层文案叫「停止」。
            cli._run_step(env, ["--terminate", distro.name], action=f"停止 {distro.name}")
            return f"已停止 {distro.name}"

        self._start("停止", work)

    def _export_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        target = self.filedialog.asksaveasfilename(
            title=f"导出发行版 {distro.name}",
            defaultextension=".tar",
            filetypes=[("tar 归档", "*.tar")],
            initialfile=f"{distro.name}.tar",
        )
        if not target:
            return
        terminate_first = False
        if distro.state == "running":
            answer = self.messagebox.askyesnocancel(
                "发行版运行中", "运行中导出可能不一致。选择「是」先停止，选择「否」接受风险继续？"
            )
            if answer is None:
                return
            terminate_first = answer

        path = Path(target)

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            current = cli._require_target(env, distro.name)
            cli._guard_running_export(
                current,
                terminate_first=terminate_first,
                allow_running=not terminate_first,
                command=f"fcmd wslman export {current.name} {path}",
            )
            estimate, estimated = distro_disk_bytes(current.base_path, current.version)
            check = cli._preflight_for(env, path, estimate, estimated=estimated)
            if not check.ok:
                raise runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个位置或清理磁盘")
            parts = cli._export_parts(path, "none")
            try:
                size, digest = cli._export_stream(
                    env,
                    current.name,
                    path,
                    parts,
                    level=6,
                    terminate_first=terminate_first,
                    estimate=estimate,
                )
            except BaseException:
                cli._abort_parts([item for item in parts if item is not None], keep=env.config.keep_partial)
                raise
            return f"已导出 {path}（{format_bytes(size)}，sha256 {digest}）"

        self._start("导出", work)

    def _import(self) -> None:
        archive = self.filedialog.askopenfilename(
            title="选择要导入的 tar", filetypes=[("tar 归档", "*.tar;*.gz;*.tgz")]
        )
        if not archive:
            return
        directory = self.filedialog.askdirectory(title="选择新的安装目录（必须为空）")
        if not directory:
            return
        name = self.simpledialog.askstring("导入", "新发行版名称", initialvalue=Path(archive).stem)
        if not name:
            return
        target = Path(directory)

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            problem = cli._dir_problem(target)
            if problem:
                raise invalid_argument(problem, hint="选择一个空目录")
            view = cli._view(env)
            if view.find(name) is not None:
                raise invalid_argument(
                    f"发行版 {name} 已存在",
                    hint="GUI 不提供覆盖；如需替换请用 CLI：fcmd wslman import <名> <归档> --dir <目录> --replace --yes --backup <备份>",
                )
            cli._run_step(
                env,
                ["--import", name, str(target), archive, "--version", "2"],
                action=f"导入 {name}",
                hint="目标目录必须为空；WSL1 镜像请改用 CLI 指定 --version 1",
                timeout=3600.0,
            )
            return f"已导入 {name} 到 {target}"

        self._start("导入", work)

    def _download(self) -> None:
        name = self.download_name.get().strip()
        source = self.download_source.get()
        directory_text = self.download_dir.get().strip()
        manifest = self.manifest_url.get().strip() or None
        expected = self.expected_sha.get().strip() or None
        if not name:
            self.messagebox.showerror("下载", "请填写发行版名称")
            return
        if source == "file":
            self.messagebox.showerror("下载", "GUI 的 file 来源请用「导入」页选择本地 tar")
            return

        def work(cancel: threading.Event) -> str:
            from fcmd.apps.wslman import _source

            env = cli._build_environment()
            if source in {"cli", "auto"} and env.capabilities.has_install_no_launch:
                argv = [env.capabilities.wsl_path or "wsl.exe", "--install", "-d", name, "--no-launch"]
                result = cli._run_wsl(env, argv[1:], timeout=3600.0)
                if result.returncode != 0:
                    if source == "cli":
                        raise runtime_error(
                            f"wsl --install 失败（退出码 {result.returncode}）",
                            hint="检查网络或改用 --source manifest/file",
                        )
                    self._events.put(("log", "下载", "wsl --install 失败，改用清单"))
                else:
                    return f"已通过 wsl --install 安装 {name}；用「打开终端」启动"
            url = manifest or env.config.manifest_url
            if not url:
                raise invalid_argument("请填写清单地址，或在 wslman.json 配置 download.manifest_url")
            directory = Path(directory_text) if directory_text else Path(env.config.download_dir or ".")
            payload = _source.fetch_manifest(url)
            parsed = _source.parse_manifest(payload)
            entry = _source.find_entry(parsed, name)
            target = directory / _source.download_name(name, entry.url, parsed.arch)
            if target.exists():
                raise invalid_argument(f"目标文件已存在：{target}", hint="换一个目录或先删除旧文件")
            estimate = entry.size
            check = cli._preflight_for(env, target, estimate, estimated=estimate is None)
            if not check.ok:
                raise runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个目录")
            result_data = _source.download(
                entry.url,
                target,
                expected_sha256=expected or entry.sha256,
                cancelled=cancel.is_set,
            )
            return f"已下载 {target}（{format_bytes(result_data.size)}，sha256 {result_data.sha256}）"

        self._start("下载", work)

    def _unregister(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        dialog = _UnregisterDialog(self, distro)
        self.root.wait_window(dialog.window)
        if not dialog.confirmed:
            return
        backup = dialog.backup_path()
        terminate_first = dialog.terminate_first.get()

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            target = cli._require_target(env, distro.name)
            if target.state == "running" and not terminate_first:
                raise invalid_argument(f"{target.name} 正在运行，请勾选「先停止」")
            estimate, estimated = distro_disk_bytes(target.base_path, target.version)
            with cli.TransferLock(target.name, directory=env.lock_dir):
                if backup is not None:
                    cli._export_for_backup(env, target.name, backup, estimate, estimated=estimated)
                if terminate_first:
                    cli._run_step(env, ["--terminate", target.name], action=f"停止 {target.name}")
                cli._run_step(env, ["--unregister", target.name], action=f"注销 {target.name}")
            return f"已注销 {target.name}" + (f"；备份在 {backup}" if backup else "")

        self._start("注销", work)

    def _shutdown(self) -> None:
        dialog = _ShutdownDialog(self)
        self.root.wait_window(dialog.window)
        if not dialog.confirmed:
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            cli._run_step(env, ["--shutdown"], action="wsl --shutdown")
            return "已关闭全部发行版与 WSL 虚拟机"

        self._start("关闭全部", work)


class _UnregisterDialog:  # pragma: no cover - 需要真实 Tk 窗口
    """注销确认：手输发行版名、可选先停止、可选先备份。"""

    def __init__(self, owner: WslmanWindow, distro: Distro) -> None:
        tk, ttk = owner.tk, owner.ttk
        self.confirmed = False
        self.distro = distro
        self.filedialog = owner.filedialog
        window = tk.Toplevel(owner.root)
        self.window = window
        window.title(f"注销 {distro.name}")
        window.transient(owner.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)

        estimate, estimated = owner.sizes.get(distro.name, (None, False))
        ttk.Label(
            frame,
            text=(
                f"BasePath: {distro.base_path or '未知'}\n"
                f"占用: {format_bytes(estimate, estimated=estimated)}\n"
                "虚拟磁盘删除后无回收站。"
            ),
            justify="left",
        ).pack(anchor="w")

        self.terminate_first = tk.BooleanVar(value=distro.state == "running")
        ttk.Checkbutton(frame, text="先停止发行版", variable=self.terminate_first).pack(anchor="w", pady=4)

        self.backup_enabled = tk.BooleanVar(value=owner.config_data.require_backup_on_replace)
        ttk.Checkbutton(frame, text="先导出备份到", variable=self.backup_enabled).pack(anchor="w")
        default_backup = recommended_backup_dir() / f"{distro.name}.tar"
        self.backup = tk.StringVar(value=str(default_backup))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=4)
        ttk.Entry(row, textvariable=self.backup).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择…", command=self._choose_backup).pack(side="left", padx=(4, 0))

        ttk.Label(frame, text=f"手输发行版名以确认：{distro.name}").pack(anchor="w", pady=(8, 2))
        self.typed = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=self.typed)
        entry.pack(fill="x")
        entry.focus_set()

        self.confirm_button = ttk.Button(frame, text="注销", command=self._confirm, state="disabled")
        self.confirm_button.pack(anchor="e", pady=(10, 0))
        ttk.Button(frame, text="取消", command=window.destroy).pack(anchor="e")
        self.typed.trace_add("write", lambda *_: self._sync())
        self._sync()

    def _sync(self) -> None:
        self.confirm_button.configure(
            state="normal" if unregister_confirm_enabled(self.typed.get(), self.distro.name) else "disabled"
        )

    def _choose_backup(self) -> None:
        chosen = self.filedialog.asksaveasfilename(
            parent=self.window,
            title="选择备份文件",
            defaultextension=".tar",
            filetypes=[("tar 归档", "*.tar")],
            initialfile=f"{self.distro.name}.tar",
        )
        if chosen:
            self.backup.set(chosen)

    def backup_path(self) -> Path | None:
        return Path(self.backup.get().strip()) if self.backup_enabled.get() and self.backup.get().strip() else None

    def _confirm(self) -> None:
        if not unregister_confirm_enabled(self.typed.get(), self.distro.name):
            return
        self.confirmed = True
        self.window.destroy()


class _ShutdownDialog:  # pragma: no cover - 需要真实 Tk 窗口
    """``wsl --shutdown`` 确认：必须勾选影响说明。"""

    def __init__(self, owner: WslmanWindow) -> None:
        tk, ttk = owner.tk, owner.ttk
        self.confirmed = False
        window = tk.Toplevel(owner.root)
        self.window = window
        window.title("关闭全部 WSL")
        window.transient(owner.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="会停止全部发行版与 WSL 虚拟机，并连带停掉 Docker Desktop、IDE Remote 等后端。",
            wraplength=420,
            justify="left",
        ).pack(anchor="w")
        self.checked = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="我了解上述影响", variable=self.checked).pack(anchor="w", pady=8)
        self.confirm_button = ttk.Button(frame, text="关闭全部", command=self._confirm, state="disabled")
        self.confirm_button.pack(anchor="e")
        ttk.Button(frame, text="取消", command=window.destroy).pack(anchor="e")
        self.checked.trace_add("write", lambda *_: self._sync())

    def _sync(self) -> None:
        self.confirm_button.configure(state="normal" if shutdown_confirm_enabled(self.checked.get()) else "disabled")

    def _confirm(self) -> None:
        if not shutdown_confirm_enabled(self.checked.get()):
            return
        self.confirmed = True
        self.window.destroy()


def run_gui() -> None:
    """创建并运行窗口；tkinter 缺失时由 ``run_window`` 提示后返回。"""
    _guiwindow.run_window(WslmanWindow, "WSL 管理")


@fcmd.tool("wslmanw", help="打开 WSL 发行版管理窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 WSL 发行版管理窗口。"""
    run_gui()


@fcmd.main("wslmanw")
def main() -> None:
    """``wslmanw`` 命令入口。"""
    launch_cmd()
