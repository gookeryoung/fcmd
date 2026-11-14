"""locllmw 设置页：目录布局、模型目录、服务环境变量与 config.json 编辑。

本模块承载原独立窗口 ``llmenvw`` 的全部能力，统一挂在 locllmw 的「设置」分页下。
所有目录解析都委托给 :mod:`fcmd.apps.locllm._llmconfig`，页面本身不保存私有默认值；
目录布局的修改直接写入权威 ``config.json``，不再只影响当前进程。
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from fcmd.apps.locllm import _llmconfig, llmenv
from fcmd.apps.locllm.llmbase import ServeError, ServeState

__all__ = [
    "SettingsPage",
    "config_text",
    "env_text",
    "layout_rows",
    "model_root_rows",
    "parse_config_text",
    "replace_models_root",
    "shell_env_lines",
]

_SHELL_LABELS = {"plain": "纯文本", "powershell": "PowerShell", "bash": "Bash"}
_SHELL_KEYS = {label: key for key, label in _SHELL_LABELS.items()}
_CONFIGURED = "已登记"
_ENV_MODELS = "环境变量 FCMD_MODELS"
_DEFAULT = "默认"
_ENV = "环境变量"
_CONFIG = "配置"
_AUTO = "自动发现"

# 目录布局中可直接落盘的行：行名 → (配置键, 提示)
_LAYOUT_EDITORS: dict[str, tuple[str, str]] = {
    "服务状态目录": ("serve_dir", "服务状态与日志所在目录"),
    "引擎根目录": ("engines_root", "llama.cpp 引擎根目录"),
    "工作目录": ("work_dir", "下载 / 转换等工作的基准目录"),
}


def _source(env_name: str, config_key: str, home: Path | None = None) -> str:
    """来源标注：环境变量 > 配置 > 默认（显式 home 时忽略环境变量）。"""
    if home is None and os.environ.get(env_name, "").strip():
        return _ENV
    value = _llmconfig.get_option(config_key, home)
    if isinstance(value, str) and value.strip():
        return _CONFIG
    return _DEFAULT


def layout_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回目录布局表 ``(项目, 路径, 来源)``，与 ``llmenv show`` 同口径。"""
    return [
        ("FCMD_HOME", str(_llmconfig.fcmd_home()), _ENV if os.environ.get("FCMD_HOME", "").strip() else _DEFAULT),
        ("服务状态目录", str(_llmconfig.serve_dir(home)), _source("FCMD_SERVE_DIR", "serve_dir", home)),
        (
            "配置文件",
            str(_llmconfig.config_path(home)),
            _ENV if os.environ.get("FCMD_CONFIG", "").strip() else _DEFAULT,
        ),
        ("引擎根目录", str(_llmconfig.engines_root(home)), _source("FCMD_ENGINES", "engines_root", home)),
        ("工作目录", str(_llmconfig.work_dir(home=home)), _source("FCMD_WORK", "work_dir", home)),
        ("模型目录", ", ".join(str(path) for path in _llmconfig.models_roots(home=home)), "模型目录列表"),
        ("已登记服务", str(len(llmenv.list_states(home))), _AUTO),
    ]


def model_root_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回当前生效的模型目录 ``(路径, 状态, 来源)``，顺序即解析优先级。"""
    configured = {_llmconfig.path_key(path) for path in _llmconfig.configured_roots(home)}
    env_raw = os.environ.get("FCMD_MODELS")
    env_key = _llmconfig.path_key(Path(env_raw).expanduser()) if env_raw else None
    rows: list[tuple[str, str, str]] = []
    for path in _llmconfig.models_roots(home=home):
        key = _llmconfig.path_key(path)
        if key in configured:
            source = _CONFIGURED
        elif env_key is not None and key == env_key:
            source = _ENV_MODELS
        else:
            source = _DEFAULT
        rows.append((str(path), "存在" if path.is_dir() else "不存在", source))
    return rows


def replace_models_root(old: Path, new: Path, home: Path | None = None) -> list[Path]:
    """把配置中的 ``old`` 原位替换为 ``new``，保留顺序与其他配置键。"""
    roots = _llmconfig.configured_roots(home)
    old_key = _llmconfig.path_key(old)
    if not any(_llmconfig.path_key(item) == old_key for item in roots):
        raise ServeError(f"配置中没有模型目录: {old}")
    updated = [new.expanduser() if _llmconfig.path_key(item) == old_key else item for item in roots]
    return _llmconfig.set_models_roots(updated, home)


def config_text(home: Path | None = None) -> str:
    """读取配置文件原文；文件缺失或不可读时返回空配置 ``{}``。"""
    path = _llmconfig.config_path(home)
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


class SettingsPage:  # pragma: no cover - Tk 界面不纳入单元覆盖（CI 无显示环境）
    """设置页容器：四个子页共用同一份权威配置。"""

    def __init__(
        self,
        parent: Any,
        *,
        tk: Any,
        ttk: Any,
        theme: Any,
        on_config_changed: Any = None,
    ) -> None:
        self._tk = tk
        self._ttk = ttk
        self._theme = theme
        self._on_config_changed = on_config_changed
        self._status_var = tk.StringVar(value="")
        self._notebook = ttk.Notebook(parent)
        self._notebook.pack(fill="both", expand=True)
        self._tabs: dict[str, Any] = {}
        for key, title in (
            ("layout", "目录布局"),
            ("roots", "模型目录"),
            ("services", "服务与环境"),
            ("config", "配置文件"),
        ):
            frame = ttk.Frame(self._notebook, padding=theme.spacing.sm)
            self._tabs[key] = frame
            self._notebook.add(frame, text=title)
        self._build_layout(self._tabs["layout"])
        self._build_roots(self._tabs["roots"])
        self._build_services(self._tabs["services"])
        self._build_config(self._tabs["config"])
        ttk.Label(parent, textvariable=self._status_var, style="Hint.TLabel").pack(fill="x", pady=(theme.spacing.xs, 0))
        self._refresh_all()

    # ------------------------------------------------------------------ #
    # 通用
    # ------------------------------------------------------------------ #
    def _notify(self, message: str) -> None:
        self._status_var.set(message)

    def _configuration_changed(self) -> None:
        """配置落盘后刷新页面并通知宿主（模型页同步工作目录）。"""
        self._refresh_all()
        if self._on_config_changed is not None:
            self._on_config_changed()

    def _tree(self, parent: Any, columns: tuple[tuple[str, str, int], ...], *, height: int) -> Any:
        tree = self._ttk.Treeview(
            parent, columns=tuple(key for key, _title, _width in columns), show="headings", height=height
        )
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="w")
        scroll = self._ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _refresh_all(self) -> None:
        self._refresh_layout()
        self._refresh_roots()
        self._refresh_services()

    # ------------------------------------------------------------------ #
    # 目录布局
    # ------------------------------------------------------------------ #
    def _build_layout(self, page: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        ttk.Label(
            page,
            text="目录布局以 config.json 为唯一权威；FCMD_HOME / 配置文件路径只能由环境变量控制。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, space.xs))
        body = ttk.Frame(page)
        body.pack(fill="both", expand=True)
        self._layout_tree = self._tree(
            body, (("item", "项目", 130), ("path", "路径", 520), ("source", "来源", 120)), height=7
        )
        self._layout_tree.bind("<Double-1>", lambda _event: self._edit_layout())
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(space.sm, 0))
        ttk.Button(row, text="编辑目录", command=self._edit_layout).pack(side="left")
        ttk.Button(row, text="刷新", command=self._refresh_all).pack(side="left", padx=(space.xs, 0))

    def _refresh_layout(self) -> None:
        tree = getattr(self, "_layout_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        for item, path, source in layout_rows():
            tree.insert("", "end", values=(item, path, source))

    def _edit_layout(self) -> None:
        tree = getattr(self, "_layout_tree", None)
        if tree is None or not tree.selection():
            self._notify("先选择一行目录")
            return
        item = str(tree.item(tree.selection()[0], "values")[0])
        editor = _LAYOUT_EDITORS.get(item)
        if editor is None:
            self._notify(f"{item} 由环境变量或「模型目录」子页管理")
            return
        key, hint = editor
        initial = str(
            _llmconfig.get_option(key)
            or (
                _llmconfig.serve_dir()
                if key == "serve_dir"
                else _llmconfig.engines_root()
                if key == "engines_root"
                else _llmconfig.work_dir()
            )
        )
        value = _PathDialog(
            self._notebook,
            tk=self._tk,
            ttk=self._ttk,
            title=f"编辑{item}",
            hint=hint,
            value=initial,
        ).result
        if value is None:
            return
        _llmconfig.set_option(key, value.strip())
        self._notify(f"{item} 已写入配置文件")
        self._configuration_changed()

    # ------------------------------------------------------------------ #
    # 模型目录
    # ------------------------------------------------------------------ #
    def _build_roots(self, page: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        ttk.Label(
            page,
            text="模型目录按顺序解析；FCMD_MODELS 只影响当前进程，登记项写入 config.json。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, space.xs))
        body = ttk.Frame(page)
        body.pack(fill="both", expand=True)
        self._roots_tree = self._tree(
            body, (("path", "路径", 520), ("state", "状态", 90), ("source", "来源", 150)), height=7
        )
        self._roots_tree.bind("<Double-1>", lambda _event: self._edit_root())
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(space.sm, 0))
        for text, command in (
            ("新增", self._add_root),
            ("编辑", self._edit_root),
            ("移除", self._remove_root),
            ("恢复默认", self._reset_roots),
        ):
            ttk.Button(row, text=text, command=command).pack(side="left", padx=(0, space.xs))

    def _refresh_roots(self) -> None:
        tree = getattr(self, "_roots_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        for path, state, source in model_root_rows():
            tree.insert("", "end", values=(path, state, source))

    def _selected_root(self) -> tuple[Path, str] | None:
        tree = getattr(self, "_roots_tree", None)
        if tree is None or not tree.selection():
            self._notify("先选择一个模型目录")
            return None
        values = tree.item(tree.selection()[0], "values")
        return Path(str(values[0])).expanduser(), str(values[2])

    def _add_root(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(initialdir=str(_llmconfig.primary_models_root()), title="选择模型目录")
        if not chosen:
            return
        _llmconfig.add_models_root(Path(chosen))
        self._notify(f"已登记模型目录: {chosen}")
        self._configuration_changed()

    def _edit_root(self) -> None:
        selected = self._selected_root()
        if selected is None:
            return
        path, source = selected
        if source != _CONFIGURED:
            self._notify("只能编辑已登记的模型目录")
            return
        value = _PathDialog(
            self._notebook,
            tk=self._tk,
            ttk=self._ttk,
            title="编辑模型目录",
            hint="替换 config.json 中的登记项，保留顺序",
            value=str(path),
        ).result
        if value is None or not value.strip():
            return
        replace_models_root(path, Path(value.strip()))
        self._notify(f"模型目录已更新: {value.strip()}")
        self._configuration_changed()

    def _remove_root(self) -> None:
        selected = self._selected_root()
        if selected is None:
            return
        path, source = selected
        if source != _CONFIGURED:
            self._notify("只能移除已登记的模型目录")
            return
        _llmconfig.remove_models_root(path)
        self._notify(f"已移除模型目录: {path}")
        self._configuration_changed()

    def _reset_roots(self) -> None:
        _llmconfig.reset_models_roots()
        self._notify("已清空模型目录配置，回到默认目录")
        self._configuration_changed()

    # ------------------------------------------------------------------ #
    # 服务与环境变量
    # ------------------------------------------------------------------ #
    def _build_services(self, page: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        top = ttk.Frame(page)
        top.pack(fill="x")
        ttk.Label(top, text="服务").pack(side="left")
        self._service_var = self._tk.StringVar(value="")
        self._service_box = ttk.Combobox(top, textvariable=self._service_var, state="readonly", width=24)
        self._service_box.pack(side="left", padx=(space.xs, space.sm))
        self._service_box.bind("<<ComboboxSelected>>", lambda _event: self._update_env())
        ttk.Label(top, text="格式").pack(side="left")
        self._shell_var = self._tk.StringVar(value=_SHELL_LABELS["plain"])
        shell_box = ttk.Combobox(
            top, textvariable=self._shell_var, values=tuple(_SHELL_LABELS.values()), state="readonly", width=14
        )
        shell_box.pack(side="left", padx=(space.xs, space.sm))
        shell_box.bind("<<ComboboxSelected>>", lambda _event: self._update_env())
        ttk.Button(top, text="刷新", command=self._refresh_services).pack(side="left")
        ttk.Button(top, text="复制", command=self._copy_env).pack(side="left", padx=(space.xs, 0))
        self._env_box = self._tk.Text(page, height=10, wrap="none")
        self._env_box.pack(fill="both", expand=True, pady=(space.sm, 0))

    def _refresh_services(self) -> None:
        box = getattr(self, "_service_box", None)
        if box is None:
            return
        names: list[str] = []
        with contextlib.suppress(ServeError):
            names = [state.name for state in llmenv.list_states()]
        box.configure(values=names)
        if names and self._service_var.get() not in names:
            self._service_var.set(names[0])
        elif not names:
            self._service_var.set("")
        self._update_env()

    def _selected_state(self) -> ServeState | None:
        name = self._service_var.get().strip()
        if not name:
            return None
        try:
            return llmenv.require_state(name)
        except ServeError:
            return None

    def _update_env(self) -> None:
        box = getattr(self, "_env_box", None)
        if box is None:
            return
        state = self._selected_state()
        shell = _SHELL_KEYS.get(self._shell_var.get(), "plain")
        text = env_text(state, shell) if state is not None else ""
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _copy_env(self) -> None:
        box = getattr(self, "_env_box", None)
        if box is None:
            return
        text = box.get("1.0", "end").strip()
        if not text:
            self._notify("没有可复制的环境变量")
            return
        self._notebook.clipboard_clear()
        self._notebook.clipboard_append(text)
        self._notify("已复制环境变量")

    # ------------------------------------------------------------------ #
    # 配置文件
    # ------------------------------------------------------------------ #
    def _build_config(self, page: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        self._config_path_var = self._tk.StringVar(value=f"配置文件：{_llmconfig.config_path()}")
        ttk.Label(page, textvariable=self._config_path_var).pack(anchor="w")
        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(space.xs, 0))
        self._config_box = self._tk.Text(body, height=12, wrap="none", undo=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self._config_box.yview)
        self._config_box.configure(yscrollcommand=scroll.set)
        self._config_box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(space.sm, 0))
        ttk.Button(row, text="重新加载", command=lambda: self._load_config_text(force=True)).pack(side="left")
        ttk.Button(row, text="校验并保存", command=self._save_config_text).pack(side="left", padx=(space.xs, 0))
        ttk.Button(row, text="恢复默认文本", command=self._reset_config).pack(side="left", padx=(space.xs, 0))
        self._load_config_text(force=True)

    def _load_config_text(self, *, force: bool) -> None:
        box = getattr(self, "_config_box", None)
        if box is None:
            return
        if not force:
            return
        box.delete("1.0", "end")
        box.insert("1.0", config_text())
        self._config_path_var.set(f"配置文件：{_llmconfig.config_path()}")

    def _save_config_text(self) -> None:
        box = getattr(self, "_config_box", None)
        if box is None:
            return
        try:
            data = parse_config_text(box.get("1.0", "end"))
        except ValueError as exc:
            self._notify(str(exc))
            return
        _llmconfig.save_config(data)
        self._notify("配置已保存")
        self._configuration_changed()

    def _reset_config(self) -> None:
        box = getattr(self, "_config_box", None)
        if box is None:
            return
        box.delete("1.0", "end")
        box.insert("1.0", "{}\n")
        self._notify("已载入默认配置文本，保存后生效")


class _PathDialog:  # pragma: no cover - Tk 模态窗口不纳入单元覆盖
    """简单的路径编辑对话框（确认 / 取消 / 浏览目录）。"""

    def __init__(  # noqa: PLR0913 - Tk 控件依赖注入，参数即构造契约
        self, parent: Any, *, tk: Any, ttk: Any, title: str, hint: str, value: str
    ) -> None:
        self.result: str | None = None
        self._tk = tk
        self._window = tk.Toplevel(parent)
        self._window.title(title)
        self._window.transient(parent)
        frame = ttk.Frame(self._window, padding="12")
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=hint, style="Hint.TLabel").pack(anchor="w", pady=(0, 6))
        self._var = tk.StringVar(value=value)
        entry_row = ttk.Frame(frame)
        entry_row.pack(fill="x")
        ttk.Entry(entry_row, textvariable=self._var).pack(side="left", fill="x", expand=True)
        ttk.Button(entry_row, text="浏览", command=self._browse).pack(side="left", padx=(6, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="确定", command=self._confirm).pack(side="right")
        ttk.Button(buttons, text="取消", command=self._cancel).pack(side="right", padx=(0, 6))
        self._window.protocol("WM_DELETE_WINDOW", self._cancel)
        self._window.grab_set()
        self._window.wait_window()

    def _browse(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(initialdir=self._var.get() or str(Path.home()), title="选择目录")
        if chosen:
            self._var.set(chosen)

    def _confirm(self) -> None:
        self.result = self._var.get()
        self._window.destroy()

    def _cancel(self) -> None:
        self.result = None
        self._window.destroy()
