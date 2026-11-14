"""locllmw 设置页：目录 / 引擎 / 界面 三组配置。

所有目录解析都委托给 :mod:`fcmd.apps.locllm._llmconfig`，页面不保存私有默认值；
修改直接写入权威 ``config.json``，界面分组只读展示窗口配置文件与窗口几何。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fcmd.apps.locllm import _llmconfig
from fcmd.gui import config as _guiconfig

__all__ = [
    "SettingsPage",
    "apply_path",
    "directory_rows",
    "engine_rows",
    "migration_note",
    "ui_rows",
]

_ENV = "环境变量"
_CONFIG = "配置"
_DEFAULT = "默认"
_AUTO = "自动保存"

#: 设置页可编辑的目录项：配置键 → (行标题, 选择对话框标题)
_EDITABLE: dict[str, tuple[str, str]] = {
    "models_dir": ("模型目录", "选择模型目录（仅支持单一目录）"),
    "work_dir": ("工作目录", "选择工作目录（下载 / 转换等产物）"),
    "engines_root": ("引擎根目录", "选择 llama.cpp 引擎根目录"),
}


def _source(env_name: str, config_key: str, home: Path | None = None) -> str:
    """来源标注：环境变量 > 配置 > 默认（显式 home 时忽略环境变量）。"""
    if home is None and os.environ.get(env_name, "").strip():
        return _ENV
    value = _llmconfig.get_option(config_key, home)
    if isinstance(value, str) and value.strip():
        return _CONFIG
    return _DEFAULT


def directory_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回「目录」组 ``(项目, 路径, 来源)``。"""
    return [
        ("模型目录", str(_llmconfig.models_dir(home=home)), _source("FCMD_MODELS", "models_dir", home)),
        ("工作目录", str(_llmconfig.work_dir(home=home)), _source("FCMD_WORK", "work_dir", home)),
    ]


def engine_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回「引擎」组 ``(项目, 路径, 来源)``。"""
    return [("引擎根目录", str(_llmconfig.engines_root(home)), _source("FCMD_ENGINES", "engines_root", home))]


def ui_rows() -> list[tuple[str, str, str]]:
    """返回「界面」组：窗口配置文件与已保存的窗口几何（只读）。"""
    path = _guiconfig.config_path("locllmw")
    settings = _guiconfig.read_settings(path) or {}
    geometry = settings.get("geometry")
    geometry_text = geometry if isinstance(geometry, str) and geometry.strip() else "未保存（使用默认尺寸）"
    return [
        ("窗口配置文件", str(path), _AUTO),
        ("窗口位置与大小", geometry_text, _AUTO),
    ]


def migration_note(home: Path | None = None) -> str:
    """旧多值模型目录合并后的迁移提示；无记录时返回空串。"""
    extras = _llmconfig.models_dir_extra(home)
    if not extras:
        return ""
    return "旧配置中的其他模型目录已记录但不再扫描：" + "、".join(extras)


def apply_path(key: str, value: str | None, home: Path | None = None) -> Path:
    """写入设置页目录项并返回当前生效路径；``value`` 为 ``None`` 表示恢复默认。"""
    if key == "models_dir":
        return _llmconfig.set_models_dir(Path(value) if value else None, home)
    if key == "work_dir":
        _llmconfig.set_option("work_dir", value or None, home)
        return _llmconfig.work_dir(home=home)
    if key == "engines_root":
        _llmconfig.set_option("engines_root", value or None, home)
        return _llmconfig.engines_root(home)
    raise ValueError(f"未知设置项: {key}")


class SettingsPage:  # pragma: no cover - Tk 界面不纳入单元覆盖（CI 无显示环境）
    """设置页容器：目录 / 引擎 / 界面三组，共用同一份权威配置。"""

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
        self._vars: dict[str, Any] = {}
        self._migration_var = tk.StringVar(value="")
        self._ui_vars: dict[str, Any] = {}
        self._groups: dict[str, Any] = {}
        self._build_directory(parent)
        self._build_engine(parent)
        self._build_ui(parent)
        ttk.Label(parent, textvariable=self._status_var, style="Hint.TLabel").pack(fill="x", pady=(theme.spacing.xs, 0))
        self.refresh()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _group(self, parent: Any, title: str) -> Any:
        frame = self._ttk.LabelFrame(parent, text=title, padding=self._theme.spacing.sm)
        frame.pack(fill="x", pady=(0, self._theme.spacing.sm))
        frame.columnconfigure(1, weight=1)
        self._groups[title] = frame
        return frame

    def _editable_row(self, group: Any, key: str, row: int) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        label, dialog_title = _EDITABLE[key]
        ttk.Label(group, text=label).grid(row=row, column=0, sticky="w", padx=(0, space.sm))
        var = self._tk.StringVar()
        ttk.Entry(group, textvariable=var, state="readonly").grid(row=row, column=1, sticky="we")
        ttk.Button(group, text="选择…", command=lambda: self._choose(key, dialog_title)).grid(
            row=row, column=2, padx=(space.sm, 0)
        )
        ttk.Button(group, text="恢复默认", command=lambda: self._apply(key, None)).grid(
            row=row, column=3, padx=(space.xs, 0)
        )
        self._vars[key] = var

    def _build_directory(self, parent: Any) -> None:
        group = self._group(parent, "目录")
        self._editable_row(group, "models_dir", 0)
        self._editable_row(group, "work_dir", 1)
        self._ttk.Label(group, text="模型目录只支持单一目录；工作目录用于存放下载 / 转换等产物。").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(self._theme.spacing.xs, 0)
        )
        self._ttk.Label(group, textvariable=self._migration_var, style="Hint.TLabel", justify="left").grid(
            row=3, column=0, columnspan=4, sticky="w"
        )

    def _build_engine(self, parent: Any) -> None:
        group = self._group(parent, "引擎")
        self._editable_row(group, "engines_root", 0)
        self._ttk.Label(group, text="llama.cpp 引擎根目录；留空使用内置默认。").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(self._theme.spacing.xs, 0)
        )

    def _build_ui(self, parent: Any) -> None:
        group = self._group(parent, "界面")
        for row, (label, _value, _source) in enumerate(ui_rows()):
            self._ttk.Label(group, text=label).grid(row=row, column=0, sticky="w", padx=(0, self._theme.spacing.sm))
            var = self._tk.StringVar()
            self._ttk.Entry(group, textvariable=var, state="readonly").grid(row=row, column=1, sticky="we")
            self._ui_vars[label] = var
        self._ttk.Label(group, text="窗口位置与大小在关闭时自动写入，无需手动编辑。", style="Hint.TLabel").grid(
            row=len(self._ui_vars), column=0, columnspan=2, sticky="w", pady=(self._theme.spacing.xs, 0)
        )

    # ------------------------------------------------------------------ #
    # 行为
    # ------------------------------------------------------------------ #
    def _notify(self, message: str) -> None:
        self._status_var.set(message)

    def _choose(self, key: str, title: str) -> None:
        from tkinter import filedialog

        label = _EDITABLE[key][0]
        chosen = filedialog.askdirectory(
            parent=self._groups.get("目录"), title=title, initialdir=str(self._current(key))
        )
        if not chosen:
            return
        self._apply(key, chosen, label=label)

    def _current(self, key: str) -> Path:
        if key == "models_dir":
            return _llmconfig.models_dir()
        if key == "work_dir":
            return _llmconfig.work_dir()
        return _llmconfig.engines_root()

    def _apply(self, key: str, value: str | None, *, label: str | None = None) -> None:
        label = label or _EDITABLE[key][0]
        try:
            resolved = apply_path(key, value)
        except (OSError, ValueError) as exc:
            self._notify(f"{label} 保存失败：{exc}")
            return
        self._notify(f"{label} 已保存，当前生效：{resolved}")
        self.refresh()
        if self._on_config_changed is not None:
            self._on_config_changed()

    def refresh(self) -> None:
        """从权威配置重新读取三组显示值。"""
        values = {label: path for label, path, _source in (*directory_rows(), *engine_rows())}
        for key, (label, _title) in _EDITABLE.items():
            self._vars[key].set(values.get(label, ""))
        for label, value, _source_text in ui_rows():
            var = self._ui_vars.get(label)
            if var is not None:
                var.set(value)
        self._migration_var.set(migration_note())
