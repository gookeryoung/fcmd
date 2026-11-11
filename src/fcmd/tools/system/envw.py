"""envw - 环境变量编辑器窗口（Tk，参考 Rapid Environment Editor）。

Rapidee 式交互：按作用域（用户 / 系统 / 当前进程）列出环境变量，支持搜索、
新建、编辑、删除、重命名与批量保存；PATH 变量提供逐条路径编辑（浏览目录、
上移 / 下移、去重）；选中变量时展示原始值与变量引用展开后的预览；可导出 /
导入 JSON 备份。

作用域后端：

- Windows：用户变量写 ``HKCU\\Environment``，系统变量写
  ``HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment``
  （需管理员权限），保存后广播环境变更通知。
- Linux / macOS：用户变量读写 ``~/.profile`` 中的 ``export NAME="value"`` 行，
  并在当前进程立即生效；无系统级作用域。
- 当前进程：只读，用于查看 ``os.environ`` 快照。

设置（上次作用域 / 窗口位置）保存在 ``${FCMD_HOME:-~/.fcmd}/ui/envw.json``。

示例
----
    fcmd envw        # 打开环境变量编辑器窗口
    envw             # 全局命令等价入口
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.infras.system import shell_profile_path

__all__ = [
    "ALL_SCOPES",
    "DEFAULT_GEOMETRY",
    "SCOPE_PROCESS",
    "SCOPE_SYSTEM",
    "SCOPE_USER",
    "EnvSettings",
    "available_scopes",
    "build_backup",
    "config_path",
    "default_path_separator",
    "expand_value",
    "format_export_line",
    "join_path_entries",
    "load_backup",
    "load_settings",
    "parse_backup",
    "parse_export_line",
    "parse_path_entries",
    "parse_profile_text",
    "read_profile_variables",
    "read_scope",
    "registry_value_type",
    "render_profile_text",
    "resolve_geometry",
    "save_backup",
    "save_scope",
    "save_settings",
    "scope_writable",
    "valid_geometry",
    "validate_name",
    "validate_value",
    "write_profile_variables",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "环境变量编辑器窗口版（Tk，参考 Rapid Environment Editor）：用户 / 系统 / 当前进程三作用域，"
    "搜索、新建、编辑、删除与重命名，PATH 逐条路径编辑，变量引用展开预览，JSON 备份与恢复；"
    "设置持久化到 ~/.fcmd/ui/envw.json"
)

SCOPE_USER = "user"
SCOPE_SYSTEM = "system"
SCOPE_PROCESS = "process"
ALL_SCOPES = frozenset({SCOPE_USER, SCOPE_SYSTEM, SCOPE_PROCESS})
SCOPE_LABELS: dict[str, str] = {
    SCOPE_USER: "用户变量",
    SCOPE_SYSTEM: "系统变量",
    SCOPE_PROCESS: "当前进程（只读）",
}

WINDOWS_USER_KEY = "Environment"
WINDOWS_SYSTEM_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
REG_SZ = 1
REG_EXPAND_SZ = 2

DEFAULT_GEOMETRY = "1000x700+220+110"
MIN_WIDTH = 820
MIN_HEIGHT = 560

_POSIX_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPORT_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_WINDOWS_REF_RE = re.compile(r"%([^%]+)%")
_POSIX_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


# ---------------------------------------------------------------------- #
# 作用域与校验（纯逻辑，可单测）
# ---------------------------------------------------------------------- #
def _is_windows(platform: str) -> bool:
    """平台标识是否属于 Windows。"""
    return platform.startswith("win")


def available_scopes(platform: str | None = None) -> tuple[str, ...]:
    """返回当前平台可用的作用域顺序。"""
    current = platform or sys.platform
    if _is_windows(current):
        return (SCOPE_USER, SCOPE_SYSTEM, SCOPE_PROCESS)
    return (SCOPE_USER, SCOPE_PROCESS)


def scope_writable(scope: str, platform: str | None = None) -> bool:
    """作用域是否允许写入（当前进程快照只读）。"""
    return scope in available_scopes(platform) and scope != SCOPE_PROCESS


def validate_name(name: str, *, platform: str | None = None) -> str | None:
    """校验变量名；合法返回 ``None``，否则返回中文错误提示。"""
    if not name or not name.strip():
        return "变量名不能为空"
    if name != name.strip():
        return "变量名不能以空白开头或结尾"
    if "=" in name:
        return "变量名不能包含 ="
    if "\x00" in name:
        return "变量名不能包含 NUL 字符"
    if not _is_windows(platform or sys.platform) and not _POSIX_NAME_RE.fullmatch(name):
        return "POSIX 变量名只能包含字母、数字与下划线，且不能以数字开头"
    return None


def validate_value(value: str) -> str | None:
    """校验变量值；合法返回 ``None``，否则返回中文错误提示。"""
    if "\x00" in value:
        return "变量值不能包含 NUL 字符"
    if "\n" in value or "\r" in value:
        return "变量值不能包含换行"
    return None


def default_path_separator(platform: str | None = None) -> str:
    """返回 PATH 类变量的分隔符：Windows 为 ``;``，其余为 ``:``。"""
    return ";" if _is_windows(platform or sys.platform) else ":"


def _clean_path_entry(entry: str) -> str:
    """去掉路径条目首尾空白与成对引号。"""
    text = entry.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return text.strip()


def parse_path_entries(value: str, separator: str | None = None) -> list[str]:
    """把 PATH 值拆成非空路径条目列表。"""
    sep = separator or default_path_separator()
    return [entry for entry in (_clean_path_entry(part) for part in value.split(sep)) if entry]


def join_path_entries(entries: Sequence[str], separator: str | None = None) -> str:
    """把路径条目列表拼回 PATH 值（丢弃空条目）。"""
    sep = separator or default_path_separator()
    return sep.join(entry for entry in (_clean_path_entry(item) for item in entries) if entry)


def registry_value_type(value: str) -> int:
    """按是否含 ``%`` 引用选择注册表值类型（纯函数，便于跨平台单测）。"""
    return REG_EXPAND_SZ if "%" in value else REG_SZ


# ---------------------------------------------------------------------- #
# 变量引用展开
# ---------------------------------------------------------------------- #
def _lookup_variable(variables: Mapping[str, str], name: str, *, windows: bool) -> str | None:
    """按平台语义查找变量：Windows 不区分大小写。"""
    value = variables.get(name)
    if value is not None or not windows:
        return value
    upper = name.upper()
    for key, candidate in variables.items():
        if key.upper() == upper:
            return candidate
    return None


def _expand_once(text: str, variables: Mapping[str, str], *, windows: bool) -> str:
    """执行一轮变量引用替换；未知引用保持原样。"""
    if windows:

        def replace_windows(match: re.Match[str]) -> str:
            value = _lookup_variable(variables, match.group(1), windows=True)
            return match.group(0) if value is None else value

        return _WINDOWS_REF_RE.sub(replace_windows, text)

    def replace_posix(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        value = variables.get(name)
        return match.group(0) if value is None else value

    return _POSIX_REF_RE.sub(replace_posix, text)


def expand_value(
    value: str,
    variables: Mapping[str, str],
    *,
    platform: str | None = None,
    max_depth: int = 16,
) -> str:
    """展开 ``%VAR%``（Windows）或 ``$VAR`` / ``${VAR}``（POSIX）引用。

    未知引用保持原样；出现循环引用时在重复状态处停止，不会无限展开。
    """
    windows = _is_windows(platform or sys.platform)
    text = value
    seen = {text}
    for _ in range(max_depth):
        expanded = _expand_once(text, variables, windows=windows)
        if expanded == text or expanded in seen:
            return expanded
        seen.add(expanded)
        text = expanded
    return text


# ---------------------------------------------------------------------- #
# POSIX shell 启动文件读写
# ---------------------------------------------------------------------- #
def _unescape_double_quoted(body: str) -> str:
    """还原 ``export NAME="..."`` 中的转义。"""
    return re.sub(r'\\(["\\$`])', r"\1", body)


def _unquote_value(raw: str) -> str:
    """去掉值的成对引号并还原转义；未加引号时保留原文。"""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return _unescape_double_quoted(text[1:-1])
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    return text


def parse_export_line(line: str) -> tuple[str, str] | None:
    """解析 ``export NAME=value`` 行；非 export 行返回 ``None``。"""
    match = _EXPORT_RE.match(line)
    if match is None:
        return None
    return match.group(1), _unquote_value(match.group(2))


def parse_profile_text(text: str) -> dict[str, str]:
    """从 shell 启动文件内容中提取全部 ``export`` 变量。"""
    variables: dict[str, str] = {}
    for line in text.splitlines():
        parsed = parse_export_line(line)
        if parsed is not None:
            variables[parsed[0]] = parsed[1]
    return variables


def format_export_line(name: str, value: str) -> str:
    """生成可安全写入 shell 启动文件的 ``export`` 行。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'export {name}="{escaped}"'


def render_profile_text(
    text: str,
    variables: Mapping[str, str],
    *,
    removed: Iterable[str] = (),
) -> str:
    """按目标变量表重写启动文件内容，保留注释与无关行。

    ``variables`` 中已存在的 ``export`` 行原地替换，``removed`` 中的行删除，
    原先不存在的新变量追加到文件末尾。
    """
    removed_set = set(removed)
    written: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        parsed = parse_export_line(line)
        if parsed is None:
            lines.append(line)
            continue
        name = parsed[0]
        if name in removed_set:
            continue
        if name in variables:
            lines.append(format_export_line(name, variables[name]))
            written.add(name)
        else:
            lines.append(line)
    for name, value in variables.items():
        if name not in written:
            lines.append(format_export_line(name, value))
    return "\n".join(lines) + ("\n" if lines else "")


def read_profile_variables(path: Path) -> dict[str, str]:
    """读取启动文件中的 ``export`` 变量；文件缺失或编码异常时返回空表。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    return parse_profile_text(text)


def write_profile_variables(path: Path, variables: Mapping[str, str], original: Mapping[str, str]) -> None:
    """重写启动文件中的 ``export`` 变量（保留其他内容）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    removed = set(original) - set(variables)
    updated = render_profile_text(text, variables, removed=removed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------- #
# 平台后端
# ---------------------------------------------------------------------- #
def _read_windows_scope(scope: str) -> dict[str, str]:  # pragma: no cover - 仅 Windows 运行
    """读取 Windows 注册表中的用户 / 系统环境变量。"""
    import winreg

    key_path = WINDOWS_USER_KEY if scope == SCOPE_USER else WINDOWS_SYSTEM_KEY
    hive = winreg.HKEY_CURRENT_USER if scope == SCOPE_USER else winreg.HKEY_LOCAL_MACHINE
    variables: dict[str, str] = {}
    try:
        with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                if isinstance(value, str):
                    variables[name] = value
                index += 1
    except OSError:
        return {}
    return variables


def _notify_windows_environment_change() -> None:  # pragma: no cover - 仅 Windows 运行
    """广播 ``WM_SETTINGCHANGE``，通知已运行进程刷新环境块。"""
    import ctypes

    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x001A
    smto_abortifhung = 0x0002
    windll = getattr(ctypes, "windll")  # noqa: B009 - 跨平台动态访问，非安全访问用途
    windll.user32.SendMessageTimeoutW(hwnd_broadcast, wm_settingchange, 0, "Environment", smto_abortifhung, 5000, None)


def _write_windows_scope(
    scope: str, variables: Mapping[str, str], original: Mapping[str, str]
) -> None:  # pragma: no cover
    """把变更写回注册表，并广播环境变更通知。"""
    import winreg

    key_path = WINDOWS_USER_KEY if scope == SCOPE_USER else WINDOWS_SYSTEM_KEY
    hive = winreg.HKEY_CURRENT_USER if scope == SCOPE_USER else winreg.HKEY_LOCAL_MACHINE
    with winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
        for name in set(original) - set(variables):
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                continue
        for name, value in variables.items():
            if original.get(name) == value:
                continue
            winreg.SetValueEx(key, name, 0, registry_value_type(value), value)
    _notify_windows_environment_change()


def _sync_process_environment(variables: Mapping[str, str], original: Mapping[str, str]) -> None:
    """把用户级变更同步到当前进程，使本会话立即可用。"""
    for name in set(original) - set(variables):
        os.environ.pop(name, None)
    for name, value in variables.items():
        if original.get(name) != value:
            os.environ[name] = value


def read_scope(
    scope: str,
    *,
    platform: str | None = None,
    profile_path: Path | None = None,
) -> dict[str, str]:
    """读取指定作用域的环境变量快照。"""
    current = platform or sys.platform
    if scope not in available_scopes(current):
        raise ValueError(f"当前平台不支持作用域：{SCOPE_LABELS.get(scope, scope)}")
    if scope == SCOPE_PROCESS:
        return dict(os.environ)
    if _is_windows(current):
        return _read_windows_scope(scope)  # pragma: no cover - 仅 Windows 运行
    return read_profile_variables(profile_path or shell_profile_path())


def save_scope(
    scope: str,
    variables: Mapping[str, str],
    original: Mapping[str, str],
    *,
    platform: str | None = None,
    profile_path: Path | None = None,
) -> None:
    """把作用域变更写回持久层；失败时抛出 ``OSError`` / ``ValueError``。"""
    current = platform or sys.platform
    if scope not in available_scopes(current):
        raise ValueError(f"当前平台不支持作用域：{SCOPE_LABELS.get(scope, scope)}")
    if scope == SCOPE_PROCESS:
        raise ValueError("当前进程变量为只读快照，无法保存")
    if _is_windows(current):
        _write_windows_scope(scope, variables, original)  # pragma: no cover - 仅 Windows 运行
    else:
        write_profile_variables(profile_path or shell_profile_path(), variables, original)
    if scope == SCOPE_USER:
        _sync_process_environment(variables, original)


# ---------------------------------------------------------------------- #
# 备份 / 恢复
# ---------------------------------------------------------------------- #
def build_backup(scopes: Mapping[str, Mapping[str, str]], *, created: str | None = None) -> dict[str, Any]:
    """构造备份内容（工具名、版本、时间与各作用域变量表）。"""
    return {
        "tool": "envw",
        "version": 1,
        "created": created or datetime.now().isoformat(timespec="seconds"),
        "scopes": {scope: dict(values) for scope, values in scopes.items()},
    }


def parse_backup(raw: Any) -> dict[str, dict[str, str]]:
    """校验并提取备份中的作用域表；格式非法时抛出 ``ValueError``。"""
    if not isinstance(raw, dict):
        raise ValueError("备份文件格式错误：顶层应为对象")
    scopes = raw.get("scopes")
    if not isinstance(scopes, dict):
        raise ValueError("备份文件格式错误：缺少 scopes 对象")
    result: dict[str, dict[str, str]] = {}
    for scope, values in scopes.items():
        if not isinstance(scope, str) or not isinstance(values, dict):
            raise ValueError("备份文件格式错误：scopes 内容非法")
        result[scope] = {
            name: value for name, value in values.items() if isinstance(name, str) and isinstance(value, str)
        }
    return result


def save_backup(path: Path, scopes: Mapping[str, Mapping[str, str]], *, created: str | None = None) -> None:
    """把各作用域变量表写入 JSON 备份文件。"""
    payload = build_backup(scopes, created=created)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_backup(path: Path) -> dict[str, dict[str, str]]:
    """读取 JSON 备份；读取或解析失败时抛出 ``ValueError``。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取备份文件：{exc}") from exc
    return parse_backup(raw)


# ---------------------------------------------------------------------- #
# 设置持久化
# ---------------------------------------------------------------------- #
@dataclass
class EnvSettings:
    """环境变量编辑器的持久化设置。"""

    scope: str = SCOPE_USER
    geometry: str | None = None


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/envw.json``。"""
    return _guiconfig.config_path("envw")


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> EnvSettings:
    """读取设置；文件缺失、损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return EnvSettings()
    scope = raw.get("scope")
    geometry = raw.get("geometry")
    return EnvSettings(
        scope=scope if isinstance(scope, str) and scope in ALL_SCOPES else SCOPE_USER,
        geometry=geometry if isinstance(geometry, str) and valid_geometry(geometry) else None,
    )


def save_settings(settings: EnvSettings, path: Path | None = None) -> None:
    """保存设置；写入失败时静默忽略（不影响窗口关闭）。"""
    _guiconfig.write_settings(
        {"scope": settings.scope, "geometry": settings.geometry},
        path or config_path(),
    )


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
def _make_modal(window: Any) -> None:  # pragma: no cover - Tk 模态辅助
    """等窗口映射完成后再抢占输入，避免阻塞事件回调或 grab 失败。"""
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


class PathEditorDialog:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """PATH 变量逐条编辑对话框（参考 Rapidee 的路径列表）。"""

    def __init__(self, parent: Any, entries: Sequence[str], *, separator: str, windows: bool) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._entries = [entry for entry in entries if entry]
        self._separator = separator
        self._windows = windows
        self.result: list[str] | None = None

        self.window = tk.Toplevel(parent)
        self.window.title("编辑路径")
        self.window.transient(parent)
        _guiwindow.center_over_parent(self.window, parent, width=720, height=420)
        self.window.minsize(560, 320)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.rowconfigure(0, weight=1)
        self.window.columnconfigure(0, weight=1)

        list_frame = ttk.Frame(self.window, padding=8)
        list_frame.grid(row=0, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self._listbox = tk.Listbox(list_frame, selectmode="extended", exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=scrollbar.set)
        self._listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        input_frame = ttk.Frame(self.window, padding=(8, 0))
        input_frame.grid(row=1, column=0, sticky="ew")
        input_frame.columnconfigure(0, weight=1)
        self._entry_var = tk.StringVar()
        entry = ttk.Entry(input_frame, textvariable=self._entry_var)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        entry.bind("<Return>", lambda _event: self._add())
        ttk.Button(input_frame, text="添加", command=self._add).grid(row=0, column=1)
        ttk.Button(input_frame, text="浏览…", command=self._browse).grid(row=0, column=2, padx=(6, 0))

        button_frame = ttk.Frame(self.window, padding=8)
        button_frame.grid(row=2, column=0, sticky="ew")
        for text, command in (
            ("删除", self._remove),
            ("上移", self._move_up),
            ("下移", self._move_down),
            ("去重", self._deduplicate),
        ):
            ttk.Button(button_frame, text=text, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(button_frame, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(button_frame, text="确定", command=self._confirm, style="Accent.TButton").pack(
            side="right", padx=(0, 6)
        )

        self._refresh()
        _make_modal(self.window)

    def _refresh(self) -> None:
        """重建列表框内容。"""
        self._listbox.delete(0, "end")
        for entry in self._entries:
            self._listbox.insert("end", entry)

    def _selected_indexes(self) -> list[int]:
        """返回当前选中的下标（升序）。"""
        return sorted(int(index) for index in self._listbox.curselection())

    def _add(self) -> None:
        """把输入框内容追加到路径列表。"""
        text = self._entry_var.get().strip()
        if not text:
            return
        self._entries.append(text.strip('"'))
        self._entry_var.set("")
        self._refresh()
        self._listbox.selection_clear(0, "end")
        self._listbox.selection_set("end")

    def _browse(self) -> None:
        """选择目录并追加到路径列表。"""
        directory = self._filedialog.askdirectory(parent=self.window, title="选择目录")
        if directory:
            self._entries.append(directory)
            self._refresh()
            self._listbox.selection_clear(0, "end")
            self._listbox.selection_set("end")

    def _remove(self) -> None:
        """删除选中的路径条目。"""
        indexes = self._selected_indexes()
        if not indexes:
            return
        for index in reversed(indexes):
            del self._entries[index]
        self._refresh()

    def _move_up(self) -> None:
        """把选中的路径条目上移。"""
        indexes = self._selected_indexes()
        if not indexes or indexes[0] == 0:
            return
        for index in indexes:
            self._entries[index - 1], self._entries[index] = self._entries[index], self._entries[index - 1]
        self._refresh()
        self._listbox.selection_clear(0, "end")
        for index in (index - 1 for index in indexes):
            self._listbox.selection_set(index)

    def _move_down(self) -> None:
        """把选中的路径条目下移。"""
        indexes = self._selected_indexes()
        if not indexes or indexes[-1] == len(self._entries) - 1:
            return
        for index in reversed(indexes):
            self._entries[index], self._entries[index + 1] = self._entries[index + 1], self._entries[index]
        self._refresh()
        self._listbox.selection_clear(0, "end")
        for index in (index + 1 for index in indexes):
            self._listbox.selection_set(index)

    def _deduplicate(self) -> None:
        """按平台大小写规则去重，保留首次出现顺序。"""
        seen: set[str] = set()
        unique: list[str] = []
        for entry in self._entries:
            key = entry.casefold() if self._windows else entry
            if key in seen:
                continue
            seen.add(key)
            unique.append(entry)
        self._entries = unique
        self._refresh()

    def _confirm(self) -> None:
        """确认并关闭对话框。"""
        self.result = list(self._entries)
        self.window.destroy()

    def _cancel(self) -> None:
        """取消并关闭对话框。"""
        self.result = None
        self.window.destroy()


class VariableDialog:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """新建 / 编辑变量的模态对话框。"""

    def __init__(
        self,
        parent: Any,
        *,
        name: str,
        value: str,
        names: Mapping[str, str],
        platform: str,
    ) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._messagebox = messagebox
        self._ttk = ttk
        self._original_name = name
        self._names = names
        self._platform = platform
        self._separator = default_path_separator(platform)
        self.result: tuple[str, str] | None = None

        self.window = tk.Toplevel(parent)
        self.window.title("编辑变量" if name else "新建变量")
        self.window.transient(parent)
        _guiwindow.center_over_parent(self.window, parent, width=640, height=400)
        self.window.minsize(520, 340)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(2, weight=1)

        ttk.Label(self.window, text="变量名：").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        self._name_var = tk.StringVar(value=name)
        ttk.Entry(self.window, textvariable=self._name_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 12), pady=(12, 6)
        )

        ttk.Label(self.window, text="变量值：").grid(row=1, column=0, sticky="nw", padx=12)
        value_frame = ttk.Frame(self.window)
        value_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=12, pady=(0, 6))
        value_frame.rowconfigure(0, weight=1)
        value_frame.columnconfigure(0, weight=1)
        self._value_text = tk.Text(value_frame, wrap="word", height=8)
        scrollbar = ttk.Scrollbar(value_frame, orient="vertical", command=self._value_text.yview)
        self._value_text.configure(yscrollcommand=scrollbar.set)
        self._value_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._value_text.insert("1.0", value)

        self._hint_var = tk.StringVar(value="")
        ttk.Label(self.window, textvariable=self._hint_var, foreground=_guitheme.PALETTE.muted).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=12
        )

        button_frame = ttk.Frame(self.window, padding=12)
        button_frame.grid(row=4, column=0, columnspan=2, sticky="ew")
        self._path_button = ttk.Button(button_frame, text="路径编辑…", command=self._edit_path)
        self._path_button.pack(side="left")
        ttk.Button(button_frame, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(button_frame, text="确定", command=self._confirm, style="Accent.TButton").pack(
            side="right", padx=(0, 6)
        )
        self._name_var.trace_add("write", lambda *_args: self._update_path_button())
        self._update_path_button()
        _make_modal(self.window)

    def _update_path_button(self) -> None:
        """仅 PATH 变量显示路径编辑入口。"""
        state = "normal" if self._name_var.get().strip().upper() == "PATH" else "disabled"
        self._path_button.configure(state=state)
        self._hint_var.set(
            "PATH 支持逐条编辑（分号/冒号分隔），也可直接修改下方文本。"
            if state == "normal"
            else "变量名区分平台规则；保存后对新进程生效。"
        )

    def _read_value(self) -> str:
        """读取变量值文本（去掉末尾自动附加的换行）。"""
        return self._value_text.get("1.0", "end-1c")

    def _edit_path(self) -> None:
        """打开路径编辑器并把结果写回变量值。"""
        dialog = PathEditorDialog(
            self.window,
            parse_path_entries(self._read_value(), self._separator),
            separator=self._separator,
            windows=_is_windows(self._platform),
        )
        self.window.wait_window(dialog.window)
        if dialog.result is not None:
            self._value_text.delete("1.0", "end")
            self._value_text.insert("1.0", join_path_entries(dialog.result, self._separator))
        if self.window.winfo_exists():
            _make_modal(self.window)

    def _confirm(self) -> None:
        """校验并回传结果。"""
        name = self._name_var.get().strip()
        error = validate_name(name, platform=self._platform)
        if error is not None:
            self._messagebox.showerror("变量名无效", error, parent=self.window)
            return
        if name != self._original_name and name in self._names:
            self._messagebox.showerror("变量名重复", f"变量 {name} 已存在", parent=self.window)
            return
        value = self._read_value()
        error = validate_value(value)
        if error is not None:
            self._messagebox.showerror("变量值无效", error, parent=self.window)
            return
        self.result = (name, value)
        self.window.destroy()

    def _cancel(self) -> None:
        """取消并关闭对话框。"""
        self.result = None
        self.window.destroy()


class EnvWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """环境变量编辑器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._platform = sys.platform
        self._profile_path = None if _is_windows(self._platform) else shell_profile_path()
        self._scopes = available_scopes(self._platform)
        self._scope = self._settings.scope if self._settings.scope in self._scopes else self._scopes[0]
        self._scope_labels = {SCOPE_LABELS[scope]: scope for scope in self._scopes}
        self._separator = default_path_separator(self._platform)
        self._variables = read_scope(self._scope, platform=self._platform, profile_path=self._profile_path)
        self._original = dict(self._variables)
        self._row_names: dict[str, str] = {}
        self._closing = False

        self.root, self._theme = _guiwindow.create_root(
            title=f"环境变量编辑器 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._scope_var = tk.StringVar(value=SCOPE_LABELS[self._scope])
        self._search_var = tk.StringVar()
        self._status_var = tk.StringVar(value="")
        self._detail_name_var = tk.StringVar(value="—")

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._refresh_tree()
        self._update_detail(None)

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        spacing = self._theme.spacing
        palette = self._theme.palette
        self._build_menu()

        top = ttk.Frame(self.root, padding=(spacing.md, spacing.md, spacing.md, spacing.sm))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="作用域：").grid(row=0, column=0, sticky="w")
        self._scope_combo = ttk.Combobox(
            top,
            textvariable=self._scope_var,
            values=[SCOPE_LABELS[scope] for scope in self._scopes],
            state="readonly",
            width=18,
        )
        self._scope_combo.grid(row=0, column=1, sticky="w", padx=(spacing.xs, spacing.lg))
        self._scope_combo.bind("<<ComboboxSelected>>", self._on_scope_changed)

        ttk.Label(top, text="搜索：").grid(row=0, column=2, sticky="w")
        self._search_var.trace_add("write", lambda *_args: self._refresh_tree())
        ttk.Entry(top, textvariable=self._search_var).grid(row=0, column=3, sticky="ew", padx=(spacing.xs, spacing.sm))
        ttk.Button(top, text="刷新", command=self._reload).grid(row=0, column=4)

        tree_frame = ttk.Frame(self.root, padding=(spacing.md, 0, spacing.md, spacing.sm))
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self._tree = ttk.Treeview(tree_frame, columns=("name", "value"), show="headings", selectmode="browse")
        self._tree.heading("name", text="变量名")
        self._tree.heading("value", text="值")
        self._tree.column("name", width=260, minwidth=160, stretch=False)
        self._tree.column("value", width=620, minwidth=240, stretch=True)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._tree.bind("<<TreeviewSelect>>", lambda _event: self._on_select())
        self._tree.bind("<Double-1>", self._on_double_click)

        detail = ttk.LabelFrame(self.root, text="变量详情", padding=spacing.sm)
        detail.grid(row=2, column=0, sticky="ew", padx=spacing.md)
        detail.columnconfigure(1, weight=1)
        ttk.Label(detail, text="变量名：").grid(row=0, column=0, sticky="nw")
        ttk.Label(detail, textvariable=self._detail_name_var).grid(row=0, column=1, sticky="w")
        ttk.Label(detail, text="原始值：").grid(row=1, column=0, sticky="nw", pady=(spacing.xs, 0))
        self._raw_text = self._create_text(detail, height=2)
        self._raw_text.grid(row=1, column=1, sticky="ew", pady=(spacing.xs, 0))
        ttk.Label(detail, text="展开值：").grid(row=2, column=0, sticky="nw", pady=(spacing.xs, 0))
        self._expanded_text = self._create_text(detail, height=2)
        self._expanded_text.grid(row=2, column=1, sticky="ew", pady=(spacing.xs, 0))

        actions = ttk.Frame(self.root, padding=(spacing.md, spacing.sm))
        actions.grid(row=3, column=0, sticky="ew")
        self._new_button = ttk.Button(actions, text="新建", command=self._new_variable)
        self._new_button.pack(side="left", padx=(0, spacing.sm))
        self._edit_button = ttk.Button(actions, text="编辑", command=self._edit_selected)
        self._edit_button.pack(side="left", padx=(0, spacing.sm))
        self._delete_button = ttk.Button(actions, text="删除", command=self._delete_selected)
        self._delete_button.pack(side="left", padx=(0, spacing.sm))
        self._path_button = ttk.Button(actions, text="路径编辑…", command=self._edit_path_selected)
        self._path_button.pack(side="left", padx=(0, spacing.sm))
        ttk.Button(actions, text="备份…", command=self._backup).pack(side="left", padx=(0, spacing.sm))
        ttk.Button(actions, text="恢复…", command=self._restore).pack(side="left", padx=(0, spacing.sm))
        self._save_button = ttk.Button(actions, text="保存", command=self._save, style="Accent.TButton")
        self._save_button.pack(side="right")

        status = ttk.Frame(self.root, padding=(spacing.md, 0, spacing.md, spacing.sm))
        status.grid(row=4, column=0, sticky="ew")
        ttk.Label(status, textvariable=self._status_var, foreground=palette.muted).pack(side="left")

        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)

    def _build_menu(self) -> None:
        _guiwindow.build_menu(
            self.root,
            on_close=self.close,
            on_about=self._show_about,
            help_items=(("备份全部作用域…", self._backup), ("从备份恢复…", self._restore)),
        )

    def _create_text(self, parent: Any, *, height: int) -> Any:
        """创建只读等宽文本区。"""
        tk = self._tk
        palette = self._theme.palette
        return tk.Text(
            parent,
            height=height,
            wrap="word",
            state="disabled",
            font=_guitheme.MONO_FONT,
            background=palette.log_bg,
            foreground=palette.log_fg,
            insertbackground=palette.cursor,
            relief="flat",
            borderwidth=0,
        )

    # ------------------------------------------------------------------ #
    # 状态与列表
    # ------------------------------------------------------------------ #
    def _has_unsaved_changes(self) -> bool:
        """当前变量表是否与加载时快照不同。"""
        return self._variables != self._original

    def _set_status(self, message: str) -> None:
        """更新状态栏（附带作用域提示）。"""
        self._status_var.set(f"{message}｜{self._scope_hint()}")

    def _scope_hint(self) -> str:
        """返回当前作用域的后端说明。"""
        if self._scope == SCOPE_PROCESS:
            return "当前进程快照为只读"
        if _is_windows(self._platform):
            if self._scope == SCOPE_SYSTEM:
                return "系统变量写入 HKLM，需要管理员权限"
            return "用户变量写入 HKCU\\Environment"
        return f"用户变量写入 {self._profile_path or shell_profile_path()}，新终端生效"

    def _refresh_tree(self) -> None:
        """按搜索词过滤并重建变量列表。"""
        search = self._search_var.get().strip().lower()
        self._tree.delete(*self._tree.get_children())
        self._row_names.clear()
        for index, (name, value) in enumerate(sorted(self._variables.items(), key=lambda item: item[0].lower())):
            if search and search not in name.lower() and search not in value.lower():
                continue
            iid = f"row-{index}"
            self._row_names[iid] = name
            self._tree.insert("", "end", iid=iid, values=(name, value.replace("\n", "\\n")))
        self._update_action_state()
        self._set_status(f"{len(self._row_names)} / {len(self._variables)} 项")

    def _selected_name(self) -> str | None:
        """返回列表选中项对应的变量名。"""
        selection = self._tree.selection()
        if not selection:
            return None
        return self._row_names.get(selection[0])

    def _select_name(self, name: str) -> None:
        """在列表中选中指定变量（过滤后不可见时跳过）。"""
        for iid, row_name in self._row_names.items():
            if row_name == name:
                self._tree.selection_set(iid)
                self._tree.see(iid)
                break
        self._on_select()

    def _update_action_state(self) -> None:
        """按作用域可写性与选中项启用/禁用操作按钮。"""
        writable = scope_writable(self._scope, self._platform)
        name = self._selected_name()
        has_selection = name is not None
        state = "normal" if writable and has_selection else "disabled"
        self._new_button.configure(state="normal" if writable else "disabled")
        self._edit_button.configure(state=state)
        self._delete_button.configure(state=state)
        self._path_button.configure(
            state="normal" if state == "normal" and name is not None and name.upper() == "PATH" else "disabled"
        )
        self._save_button.configure(state="normal" if writable and self._has_unsaved_changes() else "disabled")

    def _set_text(self, widget: Any, text: str) -> None:
        """更新只读文本区内容。"""
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _update_detail(self, name: str | None) -> None:
        """刷新详情区的原始值与展开预览。"""
        if name is None:
            self._detail_name_var.set("—")
            self._set_text(self._raw_text, "")
            self._set_text(self._expanded_text, "")
            return
        value = self._variables.get(name, "")
        merged = dict(os.environ)
        merged.update(self._variables)
        self._detail_name_var.set(name)
        self._set_text(self._raw_text, value)
        self._set_text(self._expanded_text, expand_value(value, merged, platform=self._platform))

    def _on_select(self) -> None:
        """列表选中项变化。"""
        self._update_detail(self._selected_name())
        self._update_action_state()

    def _on_double_click(self, event: Any) -> None:
        """双击行进入编辑。"""
        row = self._tree.identify_row(event.y)
        if row:
            self._tree.selection_set(row)
            self._edit_selected()

    # ------------------------------------------------------------------ #
    # 作用域与数据加载
    # ------------------------------------------------------------------ #
    def _load_scope(self) -> None:
        """重新读取当前作用域并刷新界面。"""
        try:
            self._variables = read_scope(self._scope, platform=self._platform, profile_path=self._profile_path)
        except (OSError, ValueError) as exc:
            self._messagebox.showerror("读取失败", str(exc))
            return
        self._original = dict(self._variables)
        self._refresh_tree()
        self._update_detail(None)

    def _reload(self) -> None:
        """刷新按钮：有未保存更改时先确认。"""
        if self._has_unsaved_changes() and not self._messagebox.askyesno(
            "未保存的更改", "刷新会丢弃未保存的更改，是否继续？"
        ):
            return
        self._load_scope()

    def _on_scope_changed(self, _event: Any = None) -> None:
        """切换作用域；未保存时先确认。"""
        label = self._scope_var.get()
        scope = self._scope_labels.get(label)
        if scope is None or scope == self._scope:
            return
        if self._has_unsaved_changes() and not self._messagebox.askyesno(
            "未保存的更改", "切换作用域会丢弃未保存的更改，是否继续？"
        ):
            self._scope_var.set(SCOPE_LABELS[self._scope])
            return
        self._scope = scope
        self._load_scope()

    # ------------------------------------------------------------------ #
    # 编辑操作
    # ------------------------------------------------------------------ #
    def _open_variable_dialog(self, name: str, value: str) -> tuple[str, str] | None:
        """打开变量编辑对话框并等待结果。"""
        dialog = VariableDialog(
            self.root,
            name=name,
            value=value,
            names=self._variables,
            platform=self._platform,
        )
        self.root.wait_window(dialog.window)
        return dialog.result

    def _new_variable(self) -> None:
        """新建变量。"""
        if not scope_writable(self._scope, self._platform):
            return
        result = self._open_variable_dialog("", "")
        if result is None:
            return
        name, value = result
        self._variables[name] = value
        self._refresh_tree()
        self._select_name(name)

    def _edit_selected(self) -> None:
        """编辑选中变量（支持重命名）。"""
        if not scope_writable(self._scope, self._platform):
            return
        name = self._selected_name()
        if name is None:
            return
        result = self._open_variable_dialog(name, self._variables.get(name, ""))
        if result is None:
            return
        new_name, new_value = result
        if new_name != name:
            del self._variables[name]
        self._variables[new_name] = new_value
        self._refresh_tree()
        self._select_name(new_name)

    def _delete_selected(self) -> None:
        """删除选中变量。"""
        if not scope_writable(self._scope, self._platform):
            return
        name = self._selected_name()
        if name is None:
            return
        if not self._messagebox.askyesno("删除变量", f"确定删除 {name}？", parent=self.root):
            return
        del self._variables[name]
        self._refresh_tree()
        self._update_detail(None)

    def _edit_path_selected(self) -> None:
        """编辑选中 PATH 变量的路径条目。"""
        if not scope_writable(self._scope, self._platform):
            return
        name = self._selected_name()
        if name is None or name.upper() != "PATH":
            return
        dialog = PathEditorDialog(
            self.root,
            parse_path_entries(self._variables.get(name, ""), self._separator),
            separator=self._separator,
            windows=_is_windows(self._platform),
        )
        self.root.wait_window(dialog.window)
        if dialog.result is None:
            return
        self._variables[name] = join_path_entries(dialog.result, self._separator)
        self._refresh_tree()
        self._select_name(name)

    # ------------------------------------------------------------------ #
    # 保存 / 备份 / 恢复
    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        """把当前作用域变更写回持久层。"""
        if not scope_writable(self._scope, self._platform):
            return
        try:
            save_scope(
                self._scope,
                self._variables,
                self._original,
                platform=self._platform,
                profile_path=self._profile_path,
            )
        except (OSError, ValueError) as exc:
            self._messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        self._original = dict(self._variables)
        self._set_status("已保存")
        self._update_action_state()

    def _backup(self) -> None:
        """导出所有持久化作用域到 JSON。"""
        scopes: dict[str, dict[str, str]] = {}
        for scope in self._scopes:
            if scope == SCOPE_PROCESS:
                continue
            try:
                scopes[scope] = read_scope(scope, platform=self._platform, profile_path=self._profile_path)
            except (OSError, ValueError) as exc:
                self._messagebox.showerror("备份失败", str(exc), parent=self.root)
                return
        if self._scope in scopes:
            scopes[self._scope] = dict(self._variables)
        filename = self._filedialog.asksaveasfilename(
            parent=self.root,
            title="导出环境变量备份",
            defaultextension=".json",
            initialfile="env-backup.json",
            filetypes=[("JSON 备份", "*.json"), ("所有文件", "*.*")],
        )
        if not filename:
            return
        try:
            save_backup(Path(filename), scopes)
        except OSError as exc:
            self._messagebox.showerror("备份失败", str(exc), parent=self.root)
            return
        self._set_status(f"已备份到 {filename}")

    def _restore(self) -> None:
        """从 JSON 备份恢复各作用域变量。"""
        filename = self._filedialog.askopenfilename(
            parent=self.root,
            title="选择环境变量备份",
            filetypes=[("JSON 备份", "*.json"), ("所有文件", "*.*")],
        )
        if not filename:
            return
        try:
            scopes = load_backup(Path(filename))
        except ValueError as exc:
            self._messagebox.showerror("恢复失败", str(exc), parent=self.root)
            return
        writable = [scope for scope in scopes if scope != SCOPE_PROCESS and scope in self._scopes]
        if not writable:
            self._messagebox.showerror("恢复失败", "备份中没有当前平台可写入的作用域", parent=self.root)
            return
        if not self._messagebox.askyesno(
            "恢复备份",
            f"将覆盖 {len(writable)} 个作用域的环境变量，是否继续？",
            parent=self.root,
        ):
            return
        errors: list[str] = []
        for scope in writable:
            try:
                original = read_scope(scope, platform=self._platform, profile_path=self._profile_path)
                save_scope(
                    scope,
                    scopes[scope],
                    original,
                    platform=self._platform,
                    profile_path=self._profile_path,
                )
            except (OSError, ValueError) as exc:
                errors.append(f"{SCOPE_LABELS.get(scope, scope)}：{exc}")
        self._load_scope()
        if errors:
            self._messagebox.showerror("部分恢复失败", "\n".join(errors), parent=self.root)
        else:
            self._messagebox.showinfo("恢复完成", f"已恢复 {len(writable)} 个作用域", parent=self.root)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _show_about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于",
            f"环境变量编辑器 v{__version__}\n\n"
            "交互参考 Rapid Environment Editor：\n"
            "用户 / 系统 / 当前进程作用域、搜索、增删改、\n"
            "PATH 逐条编辑、变量引用展开、JSON 备份恢复。",
            parent=self.root,
        )

    def close(self) -> None:
        """有未保存更改时确认，保存设置并退出。"""
        if self._closing:
            return
        if self._has_unsaved_changes() and not self._messagebox.askyesno(
            "未保存的更改", "确定退出并放弃未保存的更改？", parent=self.root
        ):
            return
        self._closing = True
        save_settings(
            EnvSettings(scope=self._scope, geometry=self.root.winfo_geometry()),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: EnvWindow(settings_path), "环境变量编辑器")


@fcmd.tool("envw", help="打开环境变量编辑器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开环境变量编辑器窗口。"""
    run_gui()


@fcmd.main("envw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
