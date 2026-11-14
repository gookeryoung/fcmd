"""devmanw - devman 的 Tk 窗口。

窗口只做编排：安装 / 卸载 / 切换 / 升级 / PATH 集成全部调用
:mod:`fcmd.apps.devman._ops`（与 ``fcmd devman`` 共用的机读契约），GUI 不重写
任何安装逻辑；耗时操作都在后台线程执行，主线程只消费事件队列。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/devmanw.json``。
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.devman import _env, _ops
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import TOOLS
from fcmd.apps.devman._toolchain import executable_name
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "SETTINGS_NAME",
    "DevmanWindow",
    "DevmanwSettings",
    "action_states",
    "available_rows",
    "config_path",
    "detail_values",
    "installed_rows",
    "load_settings",
    "main",
    "parse_iid",
    "path_lines",
    "path_snapshot",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "tool_iid",
    "version_iid",
]

SETTINGS_NAME = "devmanw"
DEFAULT_GEOMETRY = "1080x720"
MIN_WIDTH = 920
MIN_HEIGHT = 620

_DESCRIPTION = "devman 的图形界面：按工具查看已安装 / 活动 / 可用版本，安装、卸载、切换、升级与 PATH 集成"

_POLL_MS = 100

_DETAIL_FIELDS: tuple[str, ...] = ("工具", "版本", "状态", "活动", "版本目录", "可执行文件", "来源", "安装时间")
"""详情区字段顺序；缺失值统一渲染成 ``—``。"""


# ---------------------------------------------------------------------- #
# 设置持久化
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DevmanwSettings:
    geometry: str | None = None
    tool: str = "node"


def settings_to_mapping(settings: DevmanwSettings) -> dict[str, object]:
    return {"geometry": settings.geometry, "tool": settings.tool}


def settings_from_mapping(raw: Mapping[str, Any]) -> DevmanwSettings:
    tool = raw.get("tool")
    return DevmanwSettings(
        geometry=_guiconfig.valid_geometry(raw.get("geometry") if isinstance(raw.get("geometry"), str) else None),
        tool=tool if isinstance(tool, str) and tool in TOOLS else "node",
    )


def config_path() -> Path:
    """返回设置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/devmanw.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> DevmanwSettings:
    raw = _guiconfig.read_settings(path or config_path())
    return DevmanwSettings() if raw is None else settings_from_mapping(raw)


def save_settings(settings: DevmanwSettings, path: Path | None = None) -> None:
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


# ---------------------------------------------------------------------- #
# 列表行（纯函数，便于单测）
# ---------------------------------------------------------------------- #
def tool_iid(tool: str) -> str:
    return f"tool:{tool}"


def version_iid(tool: str, name: str) -> str:
    return f"{tool}:{name}"


def parse_iid(iid: str) -> tuple[str, str | None]:
    """``tool:node`` → ``(node, None)``；``node:22.23.5`` → ``(node, 22.23.5)``。"""
    prefix, _, rest = iid.partition(":")
    return (rest, None) if prefix == "tool" else (prefix, rest)


def installed_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """已安装树的行：每个工具一个分组节点，下面是版本节点。"""
    rows: list[dict[str, Any]] = []
    tools = payload.get("tools") or {}
    for tool in TOOLS:
        data = tools.get(tool) or {}
        installed = data.get("installed") or []
        active = data.get("active")
        rows.append(
            {
                "kind": "tool",
                "iid": tool_iid(tool),
                "tool": tool,
                "text": f"{tool}（{len(installed)} 个已安装，活动 {active or '未设置'}）",
                "values": ("", "", ""),
            }
        )
        for item in installed:
            is_active = item.get("name") == active
            rows.append(
                {
                    "kind": "installed",
                    "iid": version_iid(tool, item["name"]),
                    "tool": tool,
                    "name": item["name"],
                    "version": item["version"],
                    "path": item.get("path", ""),
                    "status": item.get("status", "ok"),
                    "source": item.get("source") or "",
                    "installed_at": item.get("installed_at") or "",
                    "active": is_active,
                    "text": ("● " if is_active else "   ") + item["version"],
                    "values": (
                        "完整" if item.get("status") == "ok" else "半完成",
                        "当前活动" if is_active else "",
                        item.get("source") or "",
                    ),
                }
            )
    return rows


def available_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """可用版本树的行（按版本升序，保持与 ``versions_payload`` 一致）。"""
    tool = payload.get("tool") or ""
    rows: list[dict[str, Any]] = []
    for item in payload.get("versions") or []:
        rows.append(
            {
                "kind": "available",
                "iid": f"avail:{tool}:{item['version']}",
                "tool": tool,
                "version": item["version"],
                "installed": bool(item.get("installed")),
                "active": bool(item.get("active")),
                "lts": item.get("lts") or "",
                "key": item.get("key") or "",
                "text": item["version"],
                "values": (
                    "已安装" if item.get("installed") else "",
                    "当前活动" if item.get("active") else "",
                    "LTS" if item.get("lts") else "",
                ),
            }
        )
    return rows


def path_snapshot(
    *,
    registry_path: str | None,
    entry: str,
    recorded: Sequence[str],
    shadowed: Mapping[str, Sequence[str]],
    shims: Mapping[str, Any],
) -> dict[str, Any]:
    """汇总 PATH 集成状态：注册表是否已写入、当前进程顺位、记录条目与遮蔽项。"""
    registered = _env.path_entry_present(registry_path, entry)
    on_path = bool(shims.get("on_path"))
    return {
        "entry": entry,
        "registered": registered,
        "recorded": [item for item in recorded if item],
        "shadowed": {tool: list(items) for tool, items in shadowed.items()},
        "process_on_path": on_path,
        "process_index": shims.get("index") if on_path else None,
        "needs_restart": registered and not on_path,
    }


def path_lines(snapshot: Mapping[str, Any]) -> list[tuple[str, str]]:
    """PATH 面板的「字段 / 值」：注册表状态、进程顺位、记录条目、遮蔽项。"""
    registered = "已写入" if snapshot.get("registered") else "未写入"
    index = snapshot.get("process_index")
    if snapshot.get("process_on_path"):
        current = f"顺位 {int(index) + 1}" if isinstance(index, int) else "在 PATH 中"
    else:
        current = "不在当前终端 PATH（重开终端后生效）" if snapshot.get("registered") else "不在 PATH 中"
    recorded = snapshot.get("recorded") or []
    recorded_text = f"{len(recorded)} 条（devman 可精确回滚）" if recorded else "无"
    shadowed = snapshot.get("shadowed") or {}
    shadow_text = "；".join(f"{tool}: {', '.join(items)}" for tool, items in shadowed.items()) if shadowed else "无"
    return [
        ("用户 PATH", registered),
        ("当前终端", current),
        ("条目", str(snapshot.get("entry") or "")),
        ("写入记录", recorded_text),
        ("会遮蔽", shadow_text),
    ]


def action_states(  # noqa: PLR0913 - 按钮可用性由选中项与运行状态共同决定
    *,
    busy: bool,
    has_installed: bool,
    installed_is_active: bool,
    has_available: bool,
    has_tool_versions: bool,
    path_registered: bool,
) -> dict[str, bool]:
    """按钮可用性：任务进行中全部禁用；其余按选中项与 PATH 状态联动。"""
    idle = not busy
    return {
        "refresh": idle,
        "refresh_versions": idle,
        "install": idle and has_available,
        "uninstall": idle and has_installed,
        "use": idle and has_installed and not installed_is_active,
        "upgrade": idle and has_tool_versions,
        "init": idle and not path_registered,
        "uninit": idle and path_registered,
    }


def detail_values(
    row: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any],
    *,
    source: str | None = None,
) -> dict[str, str]:
    """详情区字段值；未选中时给出根目录与 PATH 概览。"""
    path_state = path_lines(snapshot)
    if row is None:
        return {
            "工具": "—",
            "版本": "—",
            "状态": "选择左侧列表中已安装或可用的版本",
            "活动": "—",
            "版本目录": str(snapshot.get("root") or ""),
            "可执行文件": "—",
            "来源": "—",
            "安装时间": "—",
        }
    tool = str(row.get("tool") or "")
    if row.get("kind") == "tool":
        tools = snapshot.get("tools") or {}
        data = tools.get(tool) or {}
        installed = data.get("installed") or []
        return {
            "工具": tool,
            "版本": f"{len(installed)} 个已安装",
            "状态": "PATH " + path_state[0][1],
            "活动": data.get("active") or "未设置",
            "版本目录": str(snapshot.get("tool_roots", {}).get(tool, "")),
            "可执行文件": "—",
            "来源": "—",
            "安装时间": "—",
        }
    if row.get("kind") == "available":
        return {
            "工具": tool,
            "版本": str(row.get("version") or ""),
            "状态": "已安装" if row.get("installed") else "可安装",
            "活动": "是" if row.get("active") else "否",
            "版本目录": str(row.get("key") or "安装后显示"),
            "可执行文件": "—",
            "来源": source or "—",
            "安装时间": "—",
        }
    name = str(row.get("name") or "")
    tool_root = str(snapshot.get("tool_roots", {}).get(tool, ""))
    version_dir = str(row.get("path") or (Path(tool_root) / name if tool_root else ""))
    exe = executable_name(tool)
    return {
        "工具": tool,
        "版本": str(row.get("version") or ""),
        "状态": "完整" if row.get("status") == "ok" else "半完成（不可切换，需重装）",
        "活动": "是" if row.get("active") else "否",
        "版本目录": version_dir,
        "可执行文件": str(Path(version_dir) / exe),
        "来源": str(row.get("source") or "—"),
        "安装时间": str(row.get("installed_at") or "—"),
    }


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, DevmanError):
        return f"{exc.message}\n{exc.hint}" if exc.hint else exc.message
    return str(exc) or exc.__class__.__name__


# ---------------------------------------------------------------------- #
# 窗口
# ---------------------------------------------------------------------- #
class DevmanWindow:  # pragma: no cover - 需要真实 Tk 窗口
    """工具栏 + 已安装/可用双列表 + 详情与 PATH 面板 + 日志 + 状态栏。"""

    def __init__(self, settings_path: Path | None = None, root_dir: str = "") -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.settings_path = settings_path or config_path()
        self.settings = load_settings(self.settings_path)
        self.root_dir = root_dir
        self.current_tool = self.settings.tool
        self.snapshot: dict[str, Any] = {}
        self.path_state: dict[str, Any] = {}
        self.available: dict[str, dict[str, Any]] = {}
        self._rows: dict[str, dict[str, Any]] = {}
        self._events: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self._busy = False
        self._label = ""
        self._closing = False
        self._syncing = False
        self._last_warnings: tuple[str, ...] = ()

        self.root, self.theme = _guiwindow.create_root(
            title="开发环境管理 - devman",
            saved_geometry=self.settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._on_close,
        )
        self.space = self.theme.spacing
        self._build()
        _guiwindow.build_menu(self.root, on_close=self._on_close)
        _guitheme.install_theme_switcher(self.root)
        self.root.after(_POLL_MS, self._poll)
        self.refresh()

    # -- 布局 ---------------------------------------------------------- #

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self._build_toolbar()
        middle = self.ttk.Frame(self.root)
        middle.grid(row=1, column=0, sticky="nsew", padx=self.space.xs, pady=(0, self.space.xxs))
        middle.columnconfigure(0, weight=3)
        middle.columnconfigure(1, weight=2, minsize=440)
        middle.rowconfigure(0, weight=1)
        self._build_lists(middle)
        self._build_detail(middle)
        self._build_log()
        self._build_status()

    def _build_toolbar(self) -> None:
        ttk = self.ttk
        space = self.space
        bar = ttk.Frame(self.root, padding=(space.xs, space.xxs))
        bar.grid(row=0, column=0, sticky="ew")

        self._buttons: dict[str, Any] = {}
        groups: tuple[tuple[tuple[str, str, Callable[[], None], str | None], ...], ...] = (
            (
                ("refresh", "刷新", self.refresh, None),
                ("refresh_versions", "刷新可用版本", self.refresh_available, None),
            ),
            (
                ("install", "安装选中", self.install_selected, "Accent.TButton"),
                ("use", "切换", self.use_selected, None),
                ("upgrade", "升级", self.upgrade_tool, None),
                ("uninstall", "卸载", self.uninstall_selected, "Danger.TButton"),
            ),
            (
                ("init", "写入 PATH", self.init_path, None),
                ("uninit", "移除 PATH 集成", self.uninit_path, "Danger.TButton"),
            ),
        )
        for index, group in enumerate(groups):
            if index:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=space.xs, pady=space.xxs)
            for key, text, command, style in group:
                button = ttk.Button(bar, text=text, command=command, style=style or "TButton")
                button.pack(side="left", padx=(0, space.xxs))
                self._buttons[key] = button

    def _build_lists(self, parent: Any) -> None:
        ttk = self.ttk
        space = self.space

        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=2)

        installed_frame = ttk.LabelFrame(left, text="已安装（按工具分组）", padding=space.xxs)
        installed_frame.grid(row=0, column=0, sticky="nsew")
        installed_frame.columnconfigure(0, weight=1)
        installed_frame.rowconfigure(0, weight=1)
        self.installed_tree = ttk.Treeview(
            installed_frame,
            columns=("status", "active", "source"),
            show="tree headings",
            selectmode="browse",
            height=8,
        )
        self.installed_tree.heading("#0", text="工具 / 版本")
        self.installed_tree.heading("status", text="状态")
        self.installed_tree.heading("active", text="活动")
        self.installed_tree.heading("source", text="来源")
        self.installed_tree.column("#0", width=260, minwidth=180, stretch=True)
        self.installed_tree.column("status", width=84, minwidth=64, anchor="w", stretch=False)
        self.installed_tree.column("active", width=90, minwidth=64, anchor="w", stretch=False)
        self.installed_tree.column("source", width=120, minwidth=80, anchor="w", stretch=False)
        self.installed_tree.grid(row=0, column=0, sticky="nsew")
        installed_scroll = ttk.Scrollbar(installed_frame, orient="vertical", command=self.installed_tree.yview)
        self.installed_tree.configure(yscrollcommand=installed_scroll.set)
        installed_scroll.grid(row=0, column=1, sticky="ns")
        self.installed_tree.bind("<<TreeviewSelect>>", self._on_installed_select)

        self.available_frame = ttk.LabelFrame(left, text="可用版本", padding=space.xxs)
        self.available_frame.grid(row=1, column=0, sticky="nsew", pady=(space.xxs, 0))
        self.available_frame.columnconfigure(0, weight=1)
        self.available_frame.rowconfigure(0, weight=1)
        self.available_tree = ttk.Treeview(
            self.available_frame,
            columns=("installed", "active", "lts"),
            show="tree headings",
            selectmode="browse",
            height=6,
        )
        self.available_tree.heading("#0", text="可用版本")
        self.available_tree.heading("installed", text="已安装")
        self.available_tree.heading("active", text="活动")
        self.available_tree.heading("lts", text="备注")
        self.available_tree.column("#0", width=200, minwidth=140, stretch=True)
        self.available_tree.column("installed", width=84, minwidth=64, anchor="w", stretch=False)
        self.available_tree.column("active", width=90, minwidth=64, anchor="w", stretch=False)
        self.available_tree.column("lts", width=70, minwidth=48, anchor="w", stretch=False)
        self.available_tree.grid(row=0, column=0, sticky="nsew")
        available_scroll = ttk.Scrollbar(self.available_frame, orient="vertical", command=self.available_tree.yview)
        self.available_tree.configure(yscrollcommand=available_scroll.set)
        available_scroll.grid(row=0, column=1, sticky="ns")
        self.available_tree.bind("<<TreeviewSelect>>", self._on_available_select)

    def _build_detail(self, parent: Any) -> None:
        ttk = self.ttk
        space = self.space
        frame = ttk.LabelFrame(parent, text="详情 / PATH 状态", padding=(space.sm, space.xs))
        frame.grid(row=0, column=1, sticky="nsew", padx=(space.xs, 0))
        frame.columnconfigure(1, weight=1)

        self.detail_vars: dict[str, Any] = {}
        for index, label in enumerate(_DETAIL_FIELDS):
            ttk.Label(frame, text=label, style="Muted.TLabel", anchor="nw").grid(
                row=index, column=0, sticky="nw", padx=(0, space.xs), pady=space.xxs
            )
            variable = self.tk.StringVar(value="—")
            ttk.Label(frame, textvariable=variable, anchor="w", justify="left", wraplength=360).grid(
                row=index, column=1, sticky="ew", pady=space.xxs
            )
            self.detail_vars[label] = variable

        offset = len(_DETAIL_FIELDS)
        ttk.Separator(frame, orient="horizontal").grid(row=offset, column=0, columnspan=2, sticky="ew", pady=space.xs)
        self.path_vars: dict[str, Any] = {}
        for index, label in enumerate(("用户 PATH", "当前终端", "条目", "写入记录", "会遮蔽")):
            ttk.Label(frame, text=label, style="Muted.TLabel", anchor="nw").grid(
                row=offset + 1 + index, column=0, sticky="nw", padx=(0, space.xs), pady=space.xxs
            )
            variable = self.tk.StringVar(value="—")
            ttk.Label(frame, textvariable=variable, anchor="w", justify="left", wraplength=360).grid(
                row=offset + 1 + index, column=1, sticky="ew", pady=space.xxs
            )
            self.path_vars[label] = variable

    def _build_log(self) -> None:
        ttk = self.ttk
        space = self.space
        frame = ttk.LabelFrame(self.root, text="日志", padding=space.xxs)
        frame.grid(row=2, column=0, sticky="nsew", padx=space.xs, pady=(0, space.xxs))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log = _guiwindow.create_log(frame, height=6, grid=True)

    def _build_status(self) -> None:
        ttk = self.ttk
        space = self.space
        bar = ttk.Frame(self.root)
        bar.grid(row=3, column=0, sticky="ew")
        ttk.Separator(bar, orient="horizontal").pack(fill="x")
        inner = ttk.Frame(bar, padding=(space.xs, space.xxs))
        inner.pack(fill="x")
        self.progress = ttk.Progressbar(inner, mode="determinate", length=160)
        self.progress.pack(side="left", padx=(0, space.xs))
        self.root_var = self.tk.StringVar(value="")
        ttk.Label(inner, textvariable=self.root_var, style="Muted.TLabel", anchor="w").pack(
            side="left", fill="x", expand=True
        )
        self.status = self.tk.StringVar(value="就绪")
        ttk.Label(inner, textvariable=self.status, anchor="e").pack(side="right")

    # -- 任务编排 ------------------------------------------------------ #

    def _start(self, label: str, work: Callable[[Callable[[str], None], Callable[[int, int], None]], str]) -> None:
        if self._busy:
            self.messagebox.showinfo("请稍候", f"「{self._label}」仍在执行")
            return
        self._busy = True
        self._label = label
        self.status.set(f"{label}…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(80)
        self._set_buttons(False)

        def emit_log(text: str) -> None:
            self._events.put(("log", label, text))

        def emit_progress(done: int, total: int) -> None:
            self._events.put(("progress", label, (done, total)))

        def runner() -> None:
            try:
                message = work(emit_log, emit_progress)
            except DevmanError as exc:
                self._events.put(("error", label, exc))
            except Exception as exc:  # 后台线程不能把异常抛给 Tk
                self._events.put(("error", label, exc))
            else:
                self._events.put(("done", label, message))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                self._handle_event(*self._events.get_nowait())
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(_POLL_MS, self._poll)

    def _handle_event(self, kind: str, label: str, payload: Any) -> None:
        if kind == "log":
            self._log(str(payload))
            return
        if kind == "progress":
            done, total = payload
            if total:
                self.progress.configure(mode="determinate", maximum=total, value=done)
                self.status.set(f"{label}… {done * 100 // total}%")
            else:
                self.status.set(f"{label}… {done // (1024 * 1024)} MB")
            return
        if kind == "snapshot":
            self._apply_snapshot(payload)
            return
        if kind == "available":
            self.available = payload
            self._render_available()
            self._update_details()
            self._update_action_states()
            return
        self._busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_buttons(True)
        if kind == "done":
            if payload:
                self._log(f"[{label}] {payload}")
            self.status.set(f"{label}完成")
            if label != "刷新":
                self.refresh()
            return
        self._log(f"[{label}] 失败：{_error_text(payload)}")
        self.status.set(f"{label}失败")
        self.messagebox.showerror(label, _error_text(payload))

    def _set_buttons(self, enabled: bool) -> None:
        for button in self._buttons.values():
            button.configure(state="normal" if enabled else "disabled")
        if enabled:
            self._update_action_states()

    def _update_action_states(self) -> None:
        if self._busy:
            return
        row = self._selection()
        tool = self.current_tool
        installed = (self.snapshot.get("tools") or {}).get(tool, {}).get("installed") or []
        states = action_states(
            busy=False,
            has_installed=bool(row and row.get("kind") == "installed"),
            installed_is_active=bool(row and row.get("kind") == "installed" and row.get("active")),
            has_available=bool(row and row.get("kind") == "available"),
            has_tool_versions=bool(installed),
            path_registered=bool(self.path_state.get("registered")),
        )
        for key, enabled in states.items():
            button = self._buttons.get(key)
            if button is not None:
                button.configure(state="normal" if enabled else "disabled")

    # -- 数据加载 ------------------------------------------------------ #

    def refresh(self) -> None:
        def work(_log: Callable[[str], None], _progress: Callable[[int, int], None]) -> dict[str, Any]:
            ctx = _ops.new_context(self.root_dir or None)
            store = _env.default_env_store()
            return {
                "list": _ops.list_payload(ctx),
                "registry": store.get("Path"),
                "shadowed": _env.shadowed_commands(ctx.layout),
                "recorded": list(ctx.state.path_added),
                "tool_roots": {tool: str(ctx.layout.tool_root(tool)) for tool in TOOLS},
            }

        def run(log: Callable[[str], None], progress: Callable[[int, int], None]) -> str:
            self._events.put(("snapshot", "刷新", work(log, progress)))
            return ""

        self._start("刷新", run)

    def refresh_available(self) -> None:
        def work(log: Callable[[str], None], _progress: Callable[[int, int], None]) -> dict[str, Any]:
            ctx = _ops.new_context(self.root_dir or None)
            result: dict[str, Any] = {}
            for tool in TOOLS:
                log(f"读取 {tool} 可用版本…")
                result[tool] = _ops.versions_payload(ctx, tool, remote=True)
            return result

        def run(log: Callable[[str], None], progress: Callable[[int, int], None]) -> str:
            payloads = work(log, progress)
            self._events.put(("available", "刷新可用版本", payloads))
            counts = "，".join(f"{tool} {len(payloads[tool].get('versions') or [])} 个" for tool in TOOLS)
            return f"可用版本已更新（{counts}）"

        self._start("刷新可用版本", run)

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = dict(snapshot["list"])
        self.snapshot["tool_roots"] = snapshot["tool_roots"]
        self.path_state = path_snapshot(
            registry_path=snapshot["registry"],
            entry=(snapshot["list"].get("shims") or {}).get("dir", ""),
            recorded=snapshot["recorded"],
            shadowed=snapshot["shadowed"],
            shims=snapshot["list"].get("shims") or {},
        )
        self.root_var.set(f"root: {self.snapshot.get('root') or ''}")
        selected = self._selected_iid()
        self.installed_tree.delete(*self.installed_tree.get_children(""))
        self._rows = {}
        for row in installed_rows(self.snapshot):
            self._rows[row["iid"]] = row
            if row["kind"] == "tool":
                self.installed_tree.insert("", "end", iid=row["iid"], text=row["text"], open=True, values=row["values"])
            else:
                self.installed_tree.insert(
                    tool_iid(row["tool"]), "end", iid=row["iid"], text=row["text"], values=row["values"]
                )
        if selected and self.installed_tree.exists(selected):
            self.installed_tree.selection_set(selected)
        self._render_available()
        self._render_path()
        self._update_details()
        self._update_action_states()
        warnings = tuple(str(item) for item in self.snapshot.get("warnings") or [])
        if warnings != self._last_warnings:
            self._last_warnings = warnings
            for item in warnings:
                self._log(f"[warn] {item}")

    def _render_available(self) -> None:
        self.available_frame.configure(text=f"可用版本（{self.current_tool}）")
        selected = self.available_tree.selection()
        self.available_tree.delete(*self.available_tree.get_children(""))
        payload = self.available.get(self.current_tool)
        if not payload:
            return
        for row in available_rows(payload):
            self._rows[row["iid"]] = row
            self.available_tree.insert("", "end", iid=row["iid"], text=row["text"], values=row["values"])
        if selected and self.available_tree.exists(selected[0]):
            self.available_tree.selection_set(selected[0])

    def _render_path(self) -> None:
        for label, value in path_lines(self.path_state):
            variable = self.path_vars.get(label)
            if variable is not None:
                variable.set(value)

    def _update_details(self) -> None:
        row = self._selection()
        payload = self.available.get(self.current_tool) or {}
        values = detail_values(row, self.snapshot, source=payload.get("source"))
        for label, value in values.items():
            variable = self.detail_vars.get(label)
            if variable is not None:
                variable.set(value)

    # -- 选中项 -------------------------------------------------------- #

    def _selected_iid(self) -> str | None:
        selection = self.installed_tree.selection()
        return selection[0] if selection else None

    def _selection(self) -> dict[str, Any] | None:
        for tree in (self.installed_tree, self.available_tree):
            selection = tree.selection()
            if selection:
                return self._rows.get(selection[0])
        return None

    def _on_installed_select(self, _event: Any = None) -> None:
        if self._syncing:
            return
        selection = self.installed_tree.selection()
        if selection:
            self._syncing = True
            self.available_tree.selection_remove(self.available_tree.selection())
            self._syncing = False
            row = self._rows.get(selection[0]) or {}
            tool = row.get("tool")
            if tool in TOOLS and tool != self.current_tool:
                self.current_tool = tool
                self._render_available()
        self._update_details()
        self._update_action_states()

    def _on_available_select(self, _event: Any = None) -> None:
        if self._syncing:
            return
        selection = self.available_tree.selection()
        if selection:
            self._syncing = True
            self.installed_tree.selection_remove(self.installed_tree.selection())
            self._syncing = False
        self._update_details()
        self._update_action_states()

    # -- 操作 ---------------------------------------------------------- #

    def install_selected(self) -> None:
        row = self._selection()
        if row is None or row.get("kind") != "available":
            self.status.set("请先在上方可用版本列表中选择一个版本")
            return
        tool = row["tool"]
        spec = f"{tool}@{row.get('key') or row['version']}"
        allow_shadow = self._shadow_gate()
        if allow_shadow is None:
            return
        label = f"安装 {tool} {row['version']}"
        self._start(
            label,
            lambda log, progress: self._run_install(spec, allow_shadow, log, progress),
        )

    def _run_install(
        self,
        spec: str,
        allow_shadow: bool,
        log: Callable[[str], None],
        progress: Callable[[int, int], None],
    ) -> str:
        log(f"开始安装 {spec}…")
        ctx = _ops.new_context(self.root_dir or None)
        payload = _ops.install(ctx, spec, allow_shadow=allow_shadow, progress=progress)
        user_path = payload.get("user_path") or {}
        if user_path.get("changed"):
            log(f"用户 PATH 已{('追加' if user_path.get('position') == 'append' else '前置')} {user_path.get('entry')}")
        return f"{payload.get('message') or '安装完成'}（{payload.get('path')}）"

    def uninstall_selected(self) -> None:
        row = self._selection()
        if row is None or row.get("kind") != "installed":
            self.status.set("请先在已安装列表中选择一个版本")
            return
        tool, name = row["tool"], row["name"]
        total = sum(len((self.snapshot.get("tools") or {}).get(item, {}).get("installed") or []) for item in TOOLS)
        lines = [f"将删除 {tool} {row['version']} 的版本目录：", str(row.get("path") or "")]
        if row.get("active"):
            lines.append("该版本是当前活动版本，活动标记会被清空。")
        if total <= 1:
            lines.append("这是最后一个版本，卸载后会从用户 PATH 移除 devman 写入的条目。")
        lines.append("已打开的终端不受影响。继续？")
        if not self.messagebox.askyesno("卸载版本", "\n".join(lines)):
            return

        def work(log: Callable[[str], None], _progress: Callable[[int, int], None]) -> str:
            ctx = _ops.new_context(self.root_dir or None)
            payload = _ops.uninstall(ctx, f"{tool}@{name}", yes=True, force=bool(row.get("active")))
            user_path = payload.get("user_path") or {}
            if user_path.get("changed"):
                log(f"用户 PATH 已精确移除 {user_path.get('removed')} 个 devman 条目")
            removed = "、".join(item["version"] for item in payload.get("removed") or [])
            return f"已卸载 {tool} {removed or row['version']}"

        self._start("卸载", work)

    def use_selected(self) -> None:
        row = self._selection()
        if row is None or row.get("kind") != "installed":
            self.status.set("请先在已安装列表中选择一个版本")
            return
        tool, name = row["tool"], row["name"]

        def work(_log: Callable[[str], None], _progress: Callable[[int, int], None]) -> str:
            ctx = _ops.new_context(self.root_dir or None)
            payload = _ops.use(ctx, f"{tool}@{name}")
            return f"{tool} 活动版本：{payload.get('active') or '（已清空）'}"

        self._start("切换", work)

    def upgrade_tool(self) -> None:
        tool = self.current_tool
        allow_shadow = self._shadow_gate()
        if allow_shadow is None:
            return

        def work(log: Callable[[str], None], progress: Callable[[int, int], None]) -> str:
            ctx = _ops.new_context(self.root_dir or None)
            payload = _ops.upgrade(ctx, tool, allow_shadow=allow_shadow, progress=progress)
            if not payload.get("changed"):
                return str(payload.get("message") or "已是最新")
            log(f"{tool} {payload.get('current')} → {payload.get('target')}")
            return f"{tool} 已升级到 {payload.get('target')}"

        self._start("升级", work)

    def init_path(self) -> None:
        entry = str(self.path_state.get("entry") or "")
        shadowed = self.path_state.get("shadowed") or {}
        lines = [
            f"将把\n{entry}\n写入用户级 PATH（HKCU，无 UAC、幂等），并生成 shim。",
        ]
        if shadowed:
            detail = "；".join(f"{tool}: {', '.join(items)}" for tool, items in shadowed.items())
            lines.append(f"前置后会在新终端里遮蔽现有命令：\n{detail}")
        lines.append("写入后需要重开终端才生效。继续？")
        if not self.messagebox.askyesno("写入用户 PATH", "\n\n".join(lines)):
            return

        def work(log: Callable[[str], None], _progress: Callable[[int, int], None]) -> str:
            ctx = _ops.new_context(self.root_dir or None)
            payload = _ops.init(ctx, allow_shadow=True)
            user_path = payload.get("user_path") or {}
            if user_path.get("changed"):
                log(f"用户 PATH 已写入：{user_path.get('entry')}")
            return f"shim 已就绪（{len(payload.get('shims_written') or [])} 个更新）"

        self._start("写入 PATH", work)

    def uninit_path(self) -> None:
        recorded = self.path_state.get("recorded") or []
        entry = str(self.path_state.get("entry") or "")
        lines = [
            f"将删除 shim 目录 {entry}",
            f"并从用户 PATH 精确移除 devman 写入的 {len(recorded)} 个条目；其余 PATH 内容不受影响。",
            "已打开的终端不受影响。继续？",
        ]
        if not self.messagebox.askyesno("移除 PATH 集成", "\n\n".join(lines)):
            return

        def work(log: Callable[[str], None], _progress: Callable[[int, int], None]) -> str:
            ctx = _ops.new_context(self.root_dir or None)
            payload = _ops.uninit(ctx)
            user_path = payload.get("user_path") or {}
            if user_path.get("changed"):
                log(f"用户 PATH 已精确移除 {user_path.get('removed')} 个条目")
            return f"已删除 shim {len(payload.get('removed_shims') or [])} 个"

        self._start("移除 PATH 集成", work)

    def _shadow_gate(self) -> bool | None:
        """返回 ``allow_shadow``；需要用户确认时弹窗，取消返回 ``None``。"""
        shadowed = self.path_state.get("shadowed") or {}
        if not shadowed or self.path_state.get("registered"):
            return False
        detail = "；".join(f"{tool}: {', '.join(items)}" for tool, items in shadowed.items())
        message = (
            f"devman 的 shim 目录会前置到用户 PATH，在新终端里遮蔽现有命令：\n\n{detail}\n\n确认要接管这些命令吗？"
        )
        return True if self.messagebox.askyesno("会遮蔽现有命令", message) else None

    # -- 收尾 ---------------------------------------------------------- #

    def _log(self, text: str) -> None:
        _guiwindow.append_log(self.log, text, timestamp=True)

    def _save_settings(self) -> None:
        save_settings(
            DevmanwSettings(geometry=self.root.geometry(), tool=self.current_tool),
            self.settings_path,
        )

    def _on_close(self) -> None:
        if self._closing:
            return
        if self._busy and not self.messagebox.askyesno("任务进行中", f"「{self._label}」仍在执行，直接退出？"):
            return
        self._closing = True
        self._save_settings()
        self.root.destroy()


def run_gui(root_dir: str = "") -> None:
    """创建并运行窗口；tkinter 缺失时由 ``run_window`` 提示后返回。"""
    _guiwindow.run_window(lambda: DevmanWindow(root_dir=root_dir), "开发环境管理")


@fcmd.tool("devmanw", help="打开 devman 图形界面（Tk）", description=_DESCRIPTION, param_help={"root": "devman 根目录"})
def launch_cmd(root: str = "") -> None:
    """打开 devman 图形界面。"""
    run_gui(root)


@fcmd.main("devmanw")
def main() -> None:
    """``devmanw`` 命令入口。"""
    launch_cmd()
