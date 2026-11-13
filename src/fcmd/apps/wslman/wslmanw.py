"""wslmanw - WSL 发行版管理的 Tk 窗口。

窗口只做编排：业务判断仍走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出核心、预检、注销前备份），
避免 GUI 与 CLI 两套口径。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``。
"""

from __future__ import annotations

import queue
import threading
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
    "column_widths",
    "config_path",
    "detail_lines",
    "distro_row",
    "elide_name",
    "load_settings",
    "main",
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
DEFAULT_GEOMETRY = "1040x640"
MIN_WIDTH = 880
MIN_HEIGHT = 560

_DESCRIPTION = "WSL 发行版管理的图形界面：列表/详情、打开终端、导出导入、下载、注销与关闭全部"

_DOWNLOAD_SOURCES = ("auto", "cli", "manifest", "file")

_DOWNLOAD_HINT = "wsl --install 会触发系统级安装、可能弹 UAC，且不能指定自定义镜像"

_COLUMNS: tuple[tuple[str, str, int, int], ...] = (
    ("name", "名称", 140, 260),
    ("state", "状态", 68, 104),
    ("version", "版本", 52, 68),
    ("disk", "占用", 88, 148),
    ("default", "默认", 46, 60),
)
"""列定义：``(列 id, 表头, 最小宽度, 最大宽度)``，宽度按内容自适应后夹在两者之间。"""

_CHAR_PX = 7
"""窄字符的估算宽度（9pt 界面字体）；宽字符按 2 个窄字符计。"""

_CELL_PAD_PX = 16
"""单元格左右留白与排序指示器的估算宽度。"""


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


def column_widths(rows: Sequence[Sequence[str]]) -> dict[str, int]:
    """按表头与各行的显示宽度估算列宽，夹在每列的最小 / 最大宽度之间。"""
    widths: dict[str, int] = {}
    for index, (key, title, minimum, maximum) in enumerate(_COLUMNS):
        widest = max([text_units(title), *(text_units(str(row[index])) for row in rows if index < len(row))])
        widths[key] = min(maximum, max(minimum, widest * _CHAR_PX + _CELL_PAD_PX))
    return widths


def summary_text(distro: Distro, size: int | None, estimated: bool) -> str:
    """状态栏的完整发行版摘要；列表里截断的名称在这里显示全名。"""
    return (
        f"{distro.name} · {state_label(distro)} · "
        f"v{distro.version or '未知'} · {format_bytes(size, estimated=estimated)}"
    )


@dataclass(frozen=True, slots=True)
class WslmanwSettings:
    geometry: str | None = None
    download_dir: str = ""
    manifest_url: str = ""
    download_source: str = "auto"


def settings_to_mapping(settings: WslmanwSettings) -> dict[str, object]:
    return {
        "geometry": settings.geometry,
        "download_dir": settings.download_dir,
        "manifest_url": settings.manifest_url,
        "download_source": settings.download_source,
    }


def settings_from_mapping(raw: Mapping[str, Any]) -> WslmanwSettings:
    source = _str(raw, "download_source") or "auto"
    if source not in _DOWNLOAD_SOURCES:
        source = "auto"
    return WslmanwSettings(
        geometry=_guiconfig.valid_geometry(_str(raw, "geometry")),
        download_dir=_str(raw, "download_dir"),
        manifest_url=_str(raw, "manifest_url"),
        download_source=source,
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
    """详情面板文本行；未知字段统一显示 ``未知``。"""
    vhdx = str(Path(distro.base_path) / "ext4.vhdx") if distro.base_path else "未知"
    return [
        f"名称: {distro.name}",
        f"状态: {state_label(distro)}",
        f"版本: {distro.version or '未知'}",
        f"默认: {'是' if distro.default else '否'}",
        f"BasePath: {distro.base_path or '未知'}",
        f"虚拟磁盘: {vhdx}",
        f"占用: {format_bytes(size, estimated=estimated)}",
        f"默认用户: {distro.default_uid if distro.default_uid is not None else '未知'}",
        f"来源: {'Lxss 注册表' if distro.registered else 'wsl --list'}",
    ]


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
        self._build()
        _guiwindow.build_menu(self.root, on_close=self._on_close)
        _guitheme.install_theme_switcher(self.root)
        self.root.after(100, self._poll)
        self.refresh()

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
        groups: tuple[tuple[tuple[str, Callable[[], None], str | None], ...], ...] = (
            (("刷新", self.refresh, None),),
            (
                ("打开终端", self._open_selected, "Accent.TButton"),
                ("终止", self._terminate_selected, None),
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

    def _build_content(self) -> None:
        """发行版列表是主视觉：列表比详情宽，详情只占右侧窄栏。"""
        tk, ttk = self.tk, self.ttk
        space = self.space
        content = ttk.Frame(self.root)
        content.grid(row=1, column=0, sticky="nsew", padx=space.xs, pady=(0, space.xxs))
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)

        tree_wrap = ttk.Frame(content)
        tree_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, space.xs))
        tree_wrap.columnconfigure(0, weight=1)
        tree_wrap.rowconfigure(0, weight=1)

        columns = tuple(key for key, _title, _minimum, _maximum in _COLUMNS)
        self.tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", selectmode="browse")
        for column, title, minimum, _maximum in _COLUMNS:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=minimum, minwidth=minimum, anchor="w", stretch=column in {"name", "disk"})
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._show_details())
        self.tree.bind("<Motion>", self._on_hover)
        self.tree.bind("<Leave>", lambda _event: self.hover.set(""))
        self.tree.bind("<Double-1>", lambda _event: self._open_selected())

        detail_frame = ttk.LabelFrame(content, text="详情", padding=space.xs)
        detail_frame.grid(row=0, column=1, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self.detail = tk.Text(
            detail_frame,
            height=8,
            wrap="word",
            state="disabled",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=_guitheme.BODY_FONT,
        )
        self.detail.grid(row=0, column=0, sticky="nsew")

    def _build_notebook(self) -> None:
        ttk = self.ttk
        space = self.space
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=space.xs, pady=(0, space.xxs))
        self._build_download_tab(notebook)
        log_frame = ttk.Frame(notebook, padding=space.xs)
        notebook.add(log_frame, text="日志")
        self.log = _guiwindow.create_log(log_frame, height=8)

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

    def _cancel_task(self) -> None:
        if self._busy:
            self._cancel.set()
            self.status.set(f"{self._label}取消中…")

    def _on_close(self) -> None:
        if self._busy:
            if not self.messagebox.askyesno("任务进行中", "有任务正在执行，取消并退出？"):
                return
            self._cancel.set()
        geometry = self.root.geometry()
        save_settings(
            WslmanwSettings(
                geometry=geometry,
                download_dir=self.download_dir.get().strip(),
                manifest_url=self.manifest_url.get().strip(),
                download_source=self.download_source.get(),
            ),
            self.settings_path,
        )
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
        distro = self._selected()
        if distro is None:
            lines = ["请选择一个发行版"]
            self.hover.set("")
        else:
            size, estimated = self.sizes.get(distro.name, (None, False))
            lines = detail_lines(distro, size, estimated)
            self.hover.set(summary_text(distro, size, estimated))
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", "\n".join(lines))
        self.detail.configure(state="disabled")

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
            return
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        rows: list[tuple[str, ...]] = []
        for distro in view.distros:
            row = distro_row(distro, *self.sizes.get(distro.name, (None, False)))
            rows.append(row)
            self.tree.insert("", "end", iid=distro.name, values=row)
        widths = column_widths(rows)
        for column, _title, minimum, _maximum in _COLUMNS:
            self.tree.column(column, width=widths[column], minwidth=minimum)
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self._show_details()

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

    def _terminate_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        if not self.messagebox.askyesno("终止发行版", f"终止 {distro.name}？正在运行的任务会被中断。"):
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            cli._run_step(env, ["--terminate", distro.name], action=f"终止 {distro.name}")
            return f"已终止 {distro.name}"

        self._start("终止", work)

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
                "发行版运行中", "运行中导出可能不一致。选择「是」先终止，选择「否」接受风险继续？"
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
                raise invalid_argument(f"{target.name} 正在运行，请勾选「先终止」")
            estimate, estimated = distro_disk_bytes(target.base_path, target.version)
            with cli.TransferLock(target.name, directory=env.lock_dir):
                if backup is not None:
                    cli._export_for_backup(env, target.name, backup, estimate, estimated=estimated)
                if terminate_first:
                    cli._run_step(env, ["--terminate", target.name], action=f"终止 {target.name}")
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
    """注销确认：手输发行版名、可选先终止、可选先备份。"""

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
        ttk.Checkbutton(frame, text="先终止发行版", variable=self.terminate_first).pack(anchor="w", pady=4)

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
