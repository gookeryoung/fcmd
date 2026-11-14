"""llmenvw - LLM 环境与配置的 Tk 窗口版。

基于 :mod:`fcmd.apps.locllm.llmenv` 的核心逻辑，提供四块能力：

* 目录布局：展示并编辑 ``FCMD_HOME``、服务状态目录、配置文件、引擎根目录；
* 模型目录：列出当前生效的模型目录及来源（已登记 / ``FCMD_MODELS`` / 默认），
  支持新增、编辑、移除与恢复默认配置；
* 已登记服务与客户端环境：列出 ``llmenv list`` 的服务状态，选中后按
  ``plain`` / ``powershell`` / ``bash`` 三种格式生成 ``OPENAI_*`` 环境变量并一键复制；
* 配置文件：直接编辑 ``config.json``，保存前校验 JSON，并可重新加载 / 恢复默认。

目录布局的编辑采用与 ``llmenv`` 相同的覆盖环境变量（``FCMD_HOME`` /
``FCMD_SERVE_DIR`` / ``FCMD_CONFIG`` / ``FCMD_ENGINES``）：改动会写入窗口设置并
立即作用于本进程，下次启动自动恢复；对其他终端/命令不再生效，需自行导出同名
环境变量。界面只做装配与调度，解析、配置读写与格式生成全部复用 ``llmenv``。

示例
----
    fcmd llmenvw
"""

from __future__ import annotations

import json
import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import sidebar as _guisidebar
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.apps.locllm import llmenv
from fcmd.apps.locllm.llmbase import ServeError, ServeState

__all__ = [
    "EnvSettings",
    "apply_overrides",
    "config_path",
    "config_text",
    "env_text",
    "layout_rows",
    "load_settings",
    "model_root_rows",
    "parse_config_text",
    "replace_models_root",
    "resolve_geometry",
    "save_settings",
    "shell_env_lines",
]

_DESCRIPTION = (
    "llmenv 的 Tk 窗口版：展示并编辑 FCMD_HOME 目录布局（服务状态 / 配置 / 引擎根目录），"
    "管理模型目录（查看来源、新增 / 编辑 / 移除 / 恢复默认），"
    "直接编辑 config.json（保存前校验 JSON），"
    "列出已登记服务并按 plain / powershell / bash 生成 OPENAI_* 环境变量、一键复制；"
    "窗口位置与目录覆盖记忆到 ~/.fcmd/ui/llmenvw.json"
)

_SHELL_LABELS = {"plain": "纯文本", "powershell": "PowerShell", "bash": "Bash"}
SHELLS = tuple(_SHELL_LABELS)
_SOURCE_CONFIGURED = "已登记"
_SOURCE_ENV = "环境变量 FCMD_MODELS"
_SOURCE_DEFAULT = "默认"
_SOURCE_WINDOW = "窗口覆盖"
_SOURCE_PROCESS_ENV = "环境变量"
_SOURCE_AUTO = "自动发现"

# 目录布局中可直接编辑的行：行名 → (设置字段, 覆盖环境变量, 说明)
_LAYOUT_EDITORS: dict[str, tuple[str, str, str]] = {
    "FCMD_HOME": ("fcmd_home", "FCMD_HOME", "服务状态、配置与引擎目录的根"),
    "服务状态目录": ("serve_dir", "FCMD_SERVE_DIR", "服务状态与日志所在目录"),
    "配置文件": ("config_file", "FCMD_CONFIG", "配置文件路径（默认 服务目录/config.json）"),
    "引擎根目录": ("engines_root", "FCMD_ENGINES", "llama.cpp 引擎根目录"),
}
_OVERRIDE_ENV_VARS = tuple(env for _field, env, _hint in _LAYOUT_EDITORS.values())

MIN_WIDTH = 820
MIN_HEIGHT = 600
DEFAULT_WIDTH = 980
DEFAULT_HEIGHT = 700


@dataclass
class EnvSettings:
    """窗口持久化设置：geometry 与目录覆盖。"""

    geometry: str | None = None
    fcmd_home: str | None = None
    serve_dir: str | None = None
    config_file: str | None = None
    engines_root: str | None = None


def config_path() -> Path:
    """返回窗口设置路径：``${FCMD_HOME:-~/.fcmd}/ui/llmenvw.json``。"""
    return _guiconfig.config_path("llmenvw")


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> EnvSettings:
    """读取设置；缺失字段或非法值回退默认。"""
    raw = _guiconfig.read_settings(path or config_path())
    settings = EnvSettings()
    if raw is None:
        return settings
    geometry = raw.get("geometry")
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    for field, _env_name, _hint in _LAYOUT_EDITORS.values():
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            setattr(settings, field, value.strip())
    return settings


def save_settings(settings: EnvSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略（不影响窗口关闭）。"""
    data: dict[str, Any] = {"geometry": settings.geometry}
    for field, _env_name, _hint in _LAYOUT_EDITORS.values():
        data[field] = getattr(settings, field)
    _guiconfig.write_settings(data, path or config_path())


def apply_overrides(settings: EnvSettings, environ: MutableMapping[str, str] | None = None) -> None:
    """把设置里的目录覆盖写入环境变量（只写非空值，不清除用户已有变量）。"""
    target = os.environ if environ is None else environ
    for field, env_name, _hint in _LAYOUT_EDITORS.values():
        value = getattr(settings, field)
        if value:
            target[env_name] = value


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到窗口原默认尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def _row_source(saved: str | None, env_name: str) -> str:
    """目录行来源：窗口覆盖 > 环境变量 > 默认。"""
    if saved:
        return _SOURCE_WINDOW
    return _SOURCE_PROCESS_ENV if os.environ.get(env_name, "").strip() else _SOURCE_DEFAULT


def layout_rows(home: Path | None = None, settings: EnvSettings | None = None) -> list[tuple[str, str, str]]:
    """返回目录布局表 ``(项目, 路径, 来源)``，与 ``llmenv show`` 同口径。"""
    current = settings if settings is not None else EnvSettings()
    return [
        ("FCMD_HOME", str(llmenv.fcmd_home()), _row_source(current.fcmd_home, "FCMD_HOME")),
        ("服务状态目录", str(llmenv.serve_dir(home)), _row_source(current.serve_dir, "FCMD_SERVE_DIR")),
        ("配置文件", str(llmenv.config_path(home)), _row_source(current.config_file, "FCMD_CONFIG")),
        ("引擎根目录", str(llmenv.engines_root(home)), _row_source(current.engines_root, "FCMD_ENGINES")),
        ("模型目录", ", ".join(str(path) for path in llmenv.models_roots(home=home)), "模型目录列表"),
        ("已登记服务", str(len(llmenv.list_states(home))), _SOURCE_AUTO),
    ]


def model_root_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回当前生效的模型目录 ``(路径, 状态, 来源)``，顺序即解析优先级。"""
    configured = {llmenv.path_key(path) for path in llmenv.configured_roots(home)}
    env_raw = os.environ.get("FCMD_MODELS")
    env_key = llmenv.path_key(Path(env_raw).expanduser()) if env_raw else None
    rows: list[tuple[str, str, str]] = []
    for path in llmenv.models_roots(home=home):
        key = llmenv.path_key(path)
        if key in configured:
            source = _SOURCE_CONFIGURED
        elif env_key is not None and key == env_key:
            source = _SOURCE_ENV
        else:
            source = _SOURCE_DEFAULT
        rows.append((str(path), "存在" if path.is_dir() else "不存在", source))
    return rows


def replace_models_root(old: Path, new: Path, home: Path | None = None) -> list[Path]:
    """把配置中的 ``old`` 原位替换为 ``new``，保留顺序与其他配置键。"""
    roots = llmenv.configured_roots(home)
    old_key = llmenv.path_key(old)
    if not any(llmenv.path_key(item) == old_key for item in roots):
        raise ServeError(f"配置中没有模型目录: {old}")
    updated: list[Path] = []
    seen: set[str] = set()
    for item in roots:
        candidate = new.expanduser() if llmenv.path_key(item) == old_key else item
        key = llmenv.path_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        updated.append(candidate)
    config = llmenv.load_config(home)
    config["models_roots"] = [str(path) for path in updated]
    llmenv.save_config(config, home)
    return updated


def config_text(home: Path | None = None) -> str:
    """读取配置文件原文；文件缺失或不可读时返回空配置 ``{}``。"""
    path = llmenv.config_path(home)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return "{}\n"


def parse_config_text(text: str) -> dict[str, Any]:
    """解析配置文件文本，要求顶层为 JSON 对象。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: 第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象")
    return data


def shell_env_lines(state: ServeState, shell: str) -> list[str]:
    """按 ``plain`` / ``powershell`` / ``bash`` 语法生成客户端环境变量行。"""
    formatters = {
        "plain": lambda key, value: f"{key}={value}",
        "powershell": lambda key, value: f'$env:{key}="{value}"',
        "bash": lambda key, value: f'export {key}="{value}"',
    }
    try:
        render = formatters[shell]
    except KeyError:
        raise ValueError(f"不支持的输出格式: {shell}") from None
    return [render(key, value) for key, value in llmenv.client_env(state).items()]


def env_text(state: ServeState, shell: str) -> str:
    """把 :func:`shell_env_lines` 拼接为可直接复制的多行文本。"""
    return "\n".join(shell_env_lines(state, shell))


def _make_modal(window: Any) -> None:  # pragma: no cover - Tk 模态辅助
    """等窗口映射完成后再抢占输入，避免 grab 失败。"""
    import tkinter as tk

    def grab() -> None:
        if not window.winfo_exists():
            return
        try:
            window.focus_set()
            window.grab_set()
        except tk.TclError:
            pass

    window.after_idle(grab)


class PathEditDialog:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """单行路径编辑对话框（借鉴 envw 的编辑弹窗样式）。"""

    def __init__(self, parent: Any, *, title: str, value: str, hint: str, browse: bool = True) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self.result: str | None = None

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.transient(parent)
        _guiwindow.center_over_parent(self.window, parent, width=680, height=180)
        self.window.minsize(480, 150)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)

        ttk.Label(self.window, text="路径：").pack(anchor="w", padx=12, pady=(12, 4))
        self._var = tk.StringVar(value=value)
        entry = ttk.Entry(self.window, textvariable=self._var)
        entry.pack(fill="x", padx=12)
        entry.focus_set()
        entry.select_range(0, "end")
        ttk.Label(self.window, text=hint, foreground=_guitheme.PALETTE.muted).pack(anchor="w", padx=12, pady=(6, 0))

        actions = ttk.Frame(self.window, padding=12)
        actions.pack(fill="x", side="bottom")
        if browse:
            ttk.Button(actions, text="浏览…", command=self._browse).pack(side="left")
        ttk.Button(actions, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(actions, text="确定", command=self._confirm, style="Accent.TButton").pack(side="right", padx=(0, 6))
        self.window.bind("<Return>", lambda _event: self._confirm())
        self.window.bind("<Escape>", lambda _event: self._cancel())
        _make_modal(self.window)

    def _browse(self) -> None:
        """浏览选择目录并回填。"""
        current = Path(self._var.get().strip()).expanduser()
        initial = current if current.is_dir() else current.parent
        options: dict[str, Any] = {"parent": self.window, "title": "选择目录"}
        if initial.exists():
            options["initialdir"] = str(initial)
        chosen = self._filedialog.askdirectory(**options)
        if chosen:
            self._var.set(chosen)

    def _confirm(self) -> None:
        """校验非空后回传。"""
        value = self._var.get().strip()
        if not value:
            self._messagebox.showerror("路径为空", "路径不能为空", parent=self.window)
            return
        self.result = value
        self.window.destroy()

    def _cancel(self) -> None:
        """取消并关闭。"""
        self.result = None
        self.window.destroy()


class EnvWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 环境窗口：目录布局 / 模型目录 / 服务与环境变量 / 配置文件。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._base_env = {name: os.environ.get(name) for name in _OVERRIDE_ENV_VARS}
        self._settings = load_settings(settings_path)
        apply_overrides(self._settings)
        self.root, self._theme = _guiwindow.create_root(
            title="LLM 环境与配置",
            saved_geometry=self._settings.geometry,
            default_geometry=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._close,
        )

        self._status_var = tk.StringVar(value="就绪")
        self._shell_var = tk.StringVar(value=SHELLS[0])
        self._service_var = tk.StringVar(value="")
        self._config_path_var = tk.StringVar(value="")
        self._states: list[ServeState] = []
        self._root_rows: list[tuple[str, str, str]] = []
        self._layout_values: dict[str, str] = {}
        self._config_original = ""

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._refresh_all()
        self._load_config_text(force=True)

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        spacing = self._theme.spacing
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, textvariable=self._status_var).pack(side="bottom", fill="x", pady=(spacing.xs, 0))
        self._nav = _guisidebar.SidebarNav(
            outer,
            theme=self._theme,
            page_padding=spacing.md,
            width=210,
            title="环境与目录",
            on_change=self._on_tab_changed,
        )
        self._nav.container.pack(fill="both", expand=True)
        layout_page = self._nav.add("layout", "目录布局", hint="各目录的实际来源")
        self._roots_page = self._nav.add("roots", "模型目录", hint="额外的模型根目录")
        service_page = self._nav.add("services", "已登记服务 / 客户端环境", hint="服务与客户端环境变量")
        config_page = self._nav.add("config", "配置文件（JSON）", hint="直接编辑配置原文")
        self._nav.enable_shortcuts()
        self._build_layout(layout_page)
        self._build_roots(self._roots_page)
        self._build_services(service_page)
        self._build_env(service_page)
        self._build_config(config_page)

    def _build_layout(self, page: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(page, text="目录布局（双击行编辑）", padding=self._theme.spacing.sm)
        box.pack(fill="both", expand=True)
        self._layout_tree = self._make_tree(
            box,
            (("item", "项目", 130, False), ("path", "路径", 560, True), ("source", "来源", 110, False)),
            height=7,
        )
        self._layout_tree.bind("<Double-1>", self._on_layout_double_click)
        actions = ttk.Frame(box)
        actions.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        for text, command in (
            ("编辑选中", self._edit_layout_row),
            ("清除覆盖", self._clear_layout_override),
            ("刷新", self._refresh_all),
        ):
            ttk.Button(actions, text=text, command=command).pack(side="left", padx=(0, self._theme.spacing.sm))
        ttk.Label(
            box,
            text="可编辑：FCMD_HOME / 服务状态目录 / 配置文件 / 引擎根目录；"
            "覆盖只对本窗口及其子进程生效，可点「清除覆盖」恢复。",
            foreground=self._theme.palette.muted,
        ).pack(anchor="w", pady=(self._theme.spacing.xs, 0))

    def _build_roots(self, page: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(
            page,
            text="模型目录（优先级：--models-root → FCMD_MODELS → 配置 → 默认）",
            padding=self._theme.spacing.sm,
        )
        box.pack(fill="both", expand=True)
        self._root_tree = self._make_tree(
            box,
            (
                ("path", "目录", 520, True),
                ("state", "状态", 80, False),
                ("source", "来源", 170, False),
            ),
            height=8,
        )
        self._root_tree.bind("<Double-1>", self._on_root_double_click)
        actions = ttk.Frame(box)
        actions.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        for text, command in (
            ("添加目录…", self._add_root),
            ("编辑选中", self._edit_root),
            ("移除选中", self._remove_root),
            ("恢复默认", self._reset_roots),
            ("刷新", self._refresh_all),
        ):
            ttk.Button(actions, text=text, command=command).pack(side="left", padx=(0, self._theme.spacing.sm))
        ttk.Label(
            box,
            text="双击已登记目录可原地改写路径；默认目录编辑后会登记为配置项。",
            foreground=self._theme.palette.muted,
        ).pack(anchor="w", pady=(self._theme.spacing.xs, 0))

    def _build_services(self, page: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(page, text="已登记服务", padding=self._theme.spacing.sm)
        box.pack(fill="both", expand=True)
        self._service_tree = self._make_tree(
            box,
            (
                ("name", "服务", 110, False),
                ("url", "地址", 190, False),
                ("profile", "档位", 150, False),
                ("pid", "PID", 70, False),
                ("started", "启动时间", 200, True),
            ),
            height=6,
        )
        self._service_tree.bind("<<TreeviewSelect>>", self._on_service_selected)
        actions = ttk.Frame(box)
        actions.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        ttk.Button(actions, text="刷新", command=self._refresh_all).pack(side="left")

    def _build_env(self, page: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(
            page,
            text="客户端环境变量（dsh / pi 等 OpenAI 兼容客户端）",
            padding=self._theme.spacing.sm,
        )
        box.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="服务").pack(side="left", padx=(0, self._theme.spacing.sm))
        self._service_combo = ttk.Combobox(row, textvariable=self._service_var, state="readonly", width=18)
        self._service_combo.pack(side="left", padx=(0, self._theme.spacing.md))
        self._service_combo.bind("<<ComboboxSelected>>", self._on_combo_selected)
        for shell, label in _SHELL_LABELS.items():
            ttk.Radiobutton(row, text=label, value=shell, variable=self._shell_var, command=self._update_env).pack(
                side="left", padx=(0, self._theme.spacing.sm)
            )
        ttk.Button(row, text="复制", command=self._copy_env).pack(side="right")

        self._env_text = self._tk.Text(box, height=5, wrap="none", state="disabled")
        self._env_text.pack(fill="both", expand=True, pady=(self._theme.spacing.sm, 0))

    def _build_config(self, page: Any) -> None:
        ttk = self._ttk
        spacing = self._theme.spacing
        box = ttk.LabelFrame(page, text="config.json", padding=spacing.sm)
        box.pack(fill="both", expand=True)
        ttk.Label(box, textvariable=self._config_path_var, foreground=self._theme.palette.muted).pack(
            anchor="w", pady=(0, spacing.xs)
        )
        palette = self._theme.palette
        self._config_text = self._tk.Text(
            box,
            wrap="word",
            undo=True,
            font=_guitheme.MONO_FONT,
            background=palette.log_bg,
            foreground=palette.log_fg,
            insertbackground=palette.cursor,
            relief="flat",
            borderwidth=0,
        )
        self._config_text.pack(fill="both", expand=True)
        actions = ttk.Frame(box)
        actions.pack(fill="x", pady=(spacing.sm, 0))
        for text, command in (
            ("重新加载", lambda: self._load_config_text(force=True)),
            ("格式化", self._format_config_text),
            ("恢复默认", self._reset_config),
        ):
            ttk.Button(actions, text=text, command=command).pack(side="left", padx=(0, spacing.sm))
        ttk.Button(actions, text="保存", command=self._save_config_text, style="Accent.TButton").pack(side="right")
        ttk.Label(
            box,
            text="保存前会校验 JSON 且顶层必须是对象；「恢复默认」删除配置文件，不会删除模型文件。",
            foreground=palette.muted,
        ).pack(anchor="w", pady=(spacing.xs, 0))

    def _make_tree(self, parent: Any, columns: tuple[tuple[str, str, int, bool], ...], *, height: int) -> Any:
        """创建带滚动条的只读表格，返回 ``Treeview``。"""
        ttk = self._ttk
        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=[name for name, *_ in columns], show="headings", height=height)
        for name, title, width, stretch in columns:
            tree.heading(name, text=title)
            tree.column(name, width=width, anchor="w", stretch=stretch)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return tree

    # ------------------------------------------------------------------ #
    # 刷新
    # ------------------------------------------------------------------ #
    def _refresh_all(self) -> None:
        self._refresh_layout()
        self._refresh_roots()
        self._refresh_services()

    def _refresh_layout(self) -> None:
        for item in self._layout_tree.get_children():
            self._layout_tree.delete(item)
        self._layout_values = {}
        for label, value, source in layout_rows(settings=self._settings):
            self._layout_tree.insert("", "end", iid=label, values=(label, value, source))
            self._layout_values[label] = value

    def _refresh_roots(self) -> None:
        for item in self._root_tree.get_children():
            self._root_tree.delete(item)
        self._root_rows = model_root_rows()
        for index, row in enumerate(self._root_rows):
            self._root_tree.insert("", "end", iid=str(index), values=row)

    def _refresh_services(self) -> None:
        for item in self._service_tree.get_children():
            self._service_tree.delete(item)
        self._states = llmenv.list_states()
        for state in self._states:
            self._service_tree.insert(
                "",
                "end",
                iid=state.name,
                values=(
                    state.name,
                    state.base_url,
                    f"{state.profile}/ctx{state.context}",
                    str(state.pid),
                    state.started_at,
                ),
            )
        names = [state.name for state in self._states]
        self._service_combo.configure(values=names)
        if self._service_var.get() not in names:
            self._service_var.set(names[0] if names else "")
        if names:
            self._service_tree.selection_set(self._service_var.get())
        self._update_env()

    # ------------------------------------------------------------------ #
    # 目录布局编辑
    # ------------------------------------------------------------------ #
    def _on_layout_double_click(self, event: Any) -> None:
        """双击行进入编辑（模型目录行跳转到对应页签）。"""
        row = self._layout_tree.identify_row(event.y)
        if row:
            self._layout_tree.selection_set(row)
            self._edit_layout_row()

    def _edit_layout_row(self) -> None:
        selection = self._layout_tree.selection()
        if not selection:
            self._messagebox.showinfo("未选择", "请先选择一行")
            return
        label = selection[0]
        if label == "已登记服务":
            self._messagebox.showinfo("只读", "服务数量由状态目录自动发现，不能直接编辑")
            return
        if label == "模型目录":
            self._nav.select("roots")
            children = self._root_tree.get_children()
            if children:
                self._root_tree.selection_set(children[0])
                self._root_tree.focus(children[0])
            return
        field, env_name, hint = _LAYOUT_EDITORS[label]
        current = getattr(self._settings, field) or self._layout_values.get(label, "")
        dialog = PathEditDialog(
            self.root,
            title=f"编辑 {label}",
            value=current,
            hint=f"{hint}；写入 {env_name}，仅对本窗口及其子进程生效",
        )
        self.root.wait_window(dialog.window)
        if dialog.result is None:
            return
        setattr(self._settings, field, dialog.result)
        apply_overrides(self._settings)
        save_settings(self._settings, self._settings_path)
        self._status_var.set(f"{label} 已改为 {dialog.result}")
        self._refresh_all()

    def _clear_layout_override(self) -> None:
        selection = self._layout_tree.selection()
        if not selection:
            self._messagebox.showinfo("未选择", "请先选择一行")
            return
        label = selection[0]
        if label not in _LAYOUT_EDITORS:
            self._messagebox.showinfo("不可清除", f"{label} 不是窗口覆盖项")
            return
        field, env_name, _hint = _LAYOUT_EDITORS[label]
        if not getattr(self._settings, field):
            self._messagebox.showinfo("无需清除", f"{label} 当前没有窗口覆盖")
            return
        setattr(self._settings, field, None)
        original = self._base_env.get(env_name)
        if original is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = original
        save_settings(self._settings, self._settings_path)
        self._status_var.set(f"{label} 已恢复为启动时的取值")
        self._refresh_all()

    # ------------------------------------------------------------------ #
    # 模型目录编辑
    # ------------------------------------------------------------------ #
    def _on_root_double_click(self, event: Any) -> None:
        """双击模型目录行进入编辑。"""
        row = self._root_tree.identify_row(event.y)
        if row:
            self._root_tree.selection_set(row)
            self._edit_root()

    def _edit_root(self) -> None:
        selection = self._root_tree.selection()
        if not selection:
            self._messagebox.showinfo("未选择目录", "请先在列表中选择一个模型目录")
            return
        path, _state, source = self._root_rows[int(selection[0])]
        if source == _SOURCE_ENV:
            self._messagebox.showinfo("无法编辑", "该目录来自环境变量 FCMD_MODELS，请先移除环境变量或改用「添加目录…」")
            return
        dialog = PathEditDialog(
            self.root,
            title="编辑模型目录",
            value=path,
            hint="目录不存在时仍可保存，列表中会标记为「不存在」",
        )
        self.root.wait_window(dialog.window)
        if dialog.result is None:
            return
        target = Path(dialog.result)
        try:
            if source == _SOURCE_CONFIGURED:
                replace_models_root(Path(path), target)
            else:
                llmenv.add_models_root(target)
        except (OSError, ServeError) as exc:
            self._messagebox.showerror("编辑失败", str(exc))
            return
        self._status_var.set(f"模型目录已更新: {target.expanduser()}")
        self._refresh_all()

    def _add_root(self) -> None:
        chosen = self._filedialog.askdirectory(title="选择模型目录")
        if not chosen:
            return
        try:
            llmenv.add_models_root(Path(chosen))
        except (OSError, ServeError) as exc:
            self._messagebox.showerror("登记失败", str(exc))
            return
        self._status_var.set(f"已登记模型目录: {Path(chosen).expanduser()}")
        self._refresh_all()

    def _remove_root(self) -> None:
        selection = self._root_tree.selection()
        if not selection:
            self._messagebox.showinfo("未选择目录", "请先在列表中选择一个模型目录")
            return
        path, _state, source = self._root_rows[int(selection[0])]
        if source != _SOURCE_CONFIGURED:
            self._messagebox.showinfo("无法移除", "该目录来自环境变量或默认值，不在配置文件中登记")
            return
        try:
            llmenv.remove_models_root(Path(path))
        except (OSError, ServeError) as exc:
            self._messagebox.showerror("移除失败", str(exc))
            return
        self._status_var.set(f"已移除模型目录: {path}")
        self._refresh_all()

    def _reset_roots(self) -> None:
        if not self._messagebox.askyesno("恢复默认", "清空已登记的模型目录配置（不会删除任何文件）？"):
            return
        try:
            llmenv.reset_models_roots()
        except OSError as exc:
            self._messagebox.showerror("恢复失败", str(exc))
            return
        self._status_var.set("已清空模型目录配置，回到默认目录")
        self._refresh_all()
        self._load_config_text(force=False)

    # ------------------------------------------------------------------ #
    # 配置文件编辑
    # ------------------------------------------------------------------ #
    def _on_tab_changed(self, key: str = "") -> None:
        """切到配置页时，在用户未改动的前提下重新加载磁盘内容。"""
        if key == "config":
            self._load_config_text(force=False)

    def _config_dirty(self) -> bool:
        """配置文本是否与最近一次加载不同。"""
        return self._config_text.get("1.0", "end-1c") != self._config_original

    def _load_config_text(self, *, force: bool) -> None:
        """加载配置文件原文；有未保存改动时仅在 ``force`` 下覆盖。"""
        if not force and self._config_dirty():
            return
        text = config_text()
        self._config_original = text
        self._config_text.delete("1.0", "end")
        self._config_text.insert("1.0", text)
        self._config_path_var.set(f"当前文件: {llmenv.config_path()}")

    def _format_config_text(self) -> None:
        """校验并格式化 JSON（不落盘）。"""
        try:
            data = parse_config_text(self._config_text.get("1.0", "end-1c"))
        except ValueError as exc:
            self._messagebox.showerror("JSON 格式错误", str(exc))
            return
        formatted = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        self._config_text.delete("1.0", "end")
        self._config_text.insert("1.0", formatted)
        self._status_var.set("已格式化配置文本，尚未保存")

    def _save_config_text(self) -> None:
        """校验 JSON 后写入配置文件并刷新界面。"""
        try:
            data = parse_config_text(self._config_text.get("1.0", "end-1c"))
        except ValueError as exc:
            self._messagebox.showerror("JSON 格式错误", str(exc))
            return
        try:
            path = llmenv.save_config(data)
        except OSError as exc:
            self._messagebox.showerror("保存失败", str(exc))
            return
        self._status_var.set(f"配置文件已保存: {path}")
        self._refresh_all()
        self._load_config_text(force=True)

    def _reset_config(self) -> None:
        """删除配置文件并恢复空配置。"""
        if not self._messagebox.askyesno("恢复默认", "删除配置文件（含模型目录等全部配置），回到默认？"):
            return
        try:
            llmenv.reset_models_roots()
        except OSError as exc:
            self._messagebox.showerror("恢复失败", str(exc))
            return
        self._status_var.set("配置文件已删除，回到默认配置")
        self._refresh_all()
        self._load_config_text(force=True)

    # ------------------------------------------------------------------ #
    # 服务与环境变量
    # ------------------------------------------------------------------ #
    def _state_by_name(self, name: str) -> ServeState | None:
        return next((state for state in self._states if state.name == name), None)

    def _on_service_selected(self, _event: object | None = None) -> None:
        selection = self._service_tree.selection()
        if not selection:
            return
        self._service_var.set(selection[0])
        self._update_env()

    def _on_combo_selected(self, _event: object | None = None) -> None:
        name = self._service_var.get()
        if name and self._service_tree.exists(name):
            self._service_tree.selection_set(name)
        self._update_env()

    def _update_env(self) -> None:
        state = self._state_by_name(self._service_var.get())
        if state is None:
            text = "没有已登记的服务：先运行 fcmd llmserve start"
        else:
            text = env_text(state, self._shell_var.get())
        self._env_text.configure(state="normal")
        self._env_text.delete("1.0", "end")
        self._env_text.insert("1.0", text)
        self._env_text.configure(state="disabled")

    def _copy_env(self) -> None:
        text = self._env_text.get("1.0", "end-1c")
        if not text:
            self._status_var.set("没有可复制的环境变量")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._status_var.set("环境变量已复制到剪贴板")

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _close(self) -> None:
        self._settings.geometry = self.root.winfo_geometry()
        save_settings(self._settings, self._settings_path)
        self.root.destroy()


def run_gui() -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(EnvWindow, "LLM 环境与配置窗口")


@fcmd.tool("llmenvw", help="打开 LLM 环境与配置窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 LLM 环境与配置窗口。"""
    run_gui()


@fcmd.main("llmenvw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
