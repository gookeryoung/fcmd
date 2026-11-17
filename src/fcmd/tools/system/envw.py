"""envw - 环境变量编辑器窗口（Qt，参考 Rapid Environment Editor）。

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
    "环境变量编辑器窗口版（Qt，参考 Rapid Environment Editor）：用户 / 系统 / 当前进程三作用域，"
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
# 启动入口
# ---------------------------------------------------------------------- #
def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.tools.system._envw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 缺少可选依赖
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path)


@fcmd.tool("envw", help="打开环境变量编辑器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开环境变量编辑器窗口。"""
    run_gui()


@fcmd.main("envw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
