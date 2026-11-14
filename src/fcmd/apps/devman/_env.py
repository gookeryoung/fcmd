"""环境集成：PATH 解析、用户环境变量读写、shim 生成、冲突检测。

``install`` / ``init`` 默认把 ``<root>/bin`` 写入 HKCU 的 ``Path``（用户级、
无 UAC、幂等）；``--no-path`` 关闭。只有 devman 实际新增的条目会记入
``state.json``，``uninstall`` / ``uninit`` 只精确移除这些条目。
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from fcmd.apps.devman._errors import PATH_WRITE_FAILED, runtime_error
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import active_version, write_atomic
from fcmd.apps.devman._toolchain import version_of

__all__ = [
    "MemoryEnvStore",
    "WindowsEnvStore",
    "add_path_entry",
    "child_env",
    "command_candidates",
    "default_env_store",
    "detect_environment",
    "path_dirs",
    "path_entries",
    "path_entry_present",
    "remove_path_entry",
    "shell_snippet",
    "shim_scripts",
    "write_shims",
]

_SHELLS = ("pwsh", "cmd", "bash")
SHIM_TARGETS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    # (shim 文件名, 版本目录内目标, 是否用 call —— .cmd 目标必须 call 才能返回)
    "node": (("node.cmd", "node.exe", False), ("npm.cmd", "npm.cmd", True), ("npx.cmd", "npx.cmd", True)),
    "python": (("python.cmd", "python.exe", False), ("python3.cmd", "python.exe", False)),
}


class EnvStore(Protocol):
    """用户级环境变量存储（Windows 实现是 HKCU\\Environment）。"""

    def get(self, name: str) -> str | None:
        """读取原始值（保留 ``%VAR%`` 不展开）。"""

    def set(self, name: str, value: str) -> None:
        """写入并广播变更。"""


class MemoryEnvStore:
    """内存实现：用于非 Windows 平台与测试。"""

    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self._values = dict(initial or {})

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value


class WindowsEnvStore:
    """HKCU\\Environment 读写；不写 HKLM、不请求提权。"""

    def get(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise runtime_error(f"读取用户环境变量 {name} 失败：{exc}", code=PATH_WRITE_FAILED) from exc
        return value if isinstance(value, str) else None

    def set(self, name: str, value: str) -> None:
        import winreg

        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
        except OSError as exc:
            raise runtime_error(
                f"写入用户环境变量 {name} 失败：{exc}",
                hint="检查注册表权限；devman 不会使用管理员权限",
                code=PATH_WRITE_FAILED,
            ) from exc
        _broadcast_environment_change()


def _broadcast_environment_change() -> None:
    """通知已运行的资源管理器 / 终端环境变量已变（失败不致命）。"""
    try:
        import ctypes

        smto_abortifhung = 0x0002
        hwnd_broadcast = 0xFFFF
        wm_settingchange = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            hwnd_broadcast, wm_settingchange, 0, "Environment", smto_abortifhung, 5000, None
        )
    except Exception:
        return


def default_env_store() -> EnvStore:
    return WindowsEnvStore() if sys.platform == "win32" else MemoryEnvStore()


# ---- PATH 文本处理（纯函数） ----
def path_entries(value: str | None) -> list[str]:
    """拆分 PATH；丢弃空段。"""
    if not value:
        return []
    return [part for part in value.split(";") if part.strip()]


def _normalized(value: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.expandvars(value.strip())))


def add_path_entry(value: str | None, entry: str, *, append: bool = False) -> str:
    """把 ``entry`` 加入 PATH 文本（已存在则原样返回，保留其余段格式）。"""
    if not value:
        return entry
    parts = value.split(";")
    wanted = _normalized(entry)
    for part in parts:
        if part.strip() and _normalized(part) == wanted:
            return value
    return f"{value};{entry}" if append else f"{entry};{value}"


def path_entry_present(value: str | None, entry: str) -> bool:
    """PATH 文本里是否已有与 ``entry`` 相等的段。"""
    wanted = _normalized(entry)
    return any(part.strip() and _normalized(part) == wanted for part in (value or "").split(";"))


def remove_path_entry(value: str | None, entry: str) -> tuple[str, int]:
    """移除与 ``entry`` 完全相等的段；返回 ``(新值, 移除条数)``。

    按原始 ``;`` 段删除，其余段（含空段与 ``%VAR%`` 写法）原样保留，
    以便撤销时精确回到写入前的文本。
    """
    if not value:
        return value or "", 0
    parts = value.split(";")
    wanted = _normalized(entry)
    for index, part in enumerate(parts):
        if part.strip() and _normalized(part) == wanted:
            del parts[index]
            return ";".join(parts), 1
    return value, 0


_EXT_SPLIT = re.compile(r"[;]")


def _executable_names(name: str) -> list[str]:
    if Path(name).suffix:
        return [name]
    exts = [ext for ext in _EXT_SPLIT.split(os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")) if ext]
    return [name + ext.lower() for ext in exts] + [name + ext for ext in exts]


def command_candidates(name: str, path_value: str | None) -> list[Path]:
    """按 PATH 顺序列出所有同名可执行文件（用于冲突展示）。"""
    found: list[Path] = []
    seen: set[str] = set()
    for directory in path_entries(path_value):
        base = Path(directory.strip('"'))
        for candidate_name in _executable_names(name):
            candidate = base / candidate_name
            try:
                exists = candidate.is_file()
            except OSError:
                exists = False
            if not exists:
                continue
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)
    return found


def path_dirs(layout: Layout) -> list[Path]:
    """当前活动版本的真实 bin 目录（``devman env`` / ``exec`` 用）。"""
    dirs: list[Path] = []
    for tool in ("node", "python"):
        name = active_version(layout, tool)
        if name:
            directory = layout.version_dir(tool, name)
            if directory.is_dir():
                dirs.append(directory)
    return dirs


# ---- shim ----
def _shim_text(tool: str, target: str, *, call: bool) -> str:
    prefix = "call " if call else ""
    lines = [
        "@echo off",
        "setlocal",
        f'for /f "usebackq delims=" %%v in ("%~dp0..\\active\\{tool}.txt") do set "DEVMAN_ACTIVE=%%v"',
        "if not defined DEVMAN_ACTIVE exit /b 127",
        f'{prefix}"%~dp0..\\{tool}\\%DEVMAN_ACTIVE%\\{target}" %*',
        "exit /b %ERRORLEVEL%",
    ]
    return "\r\n".join(lines) + "\r\n"


def shim_scripts() -> dict[str, str]:
    """全部 shim 的文本内容：文件名 → 内容（CRLF）。"""
    scripts: dict[str, str] = {}
    for tool, targets in SHIM_TARGETS.items():
        for shim_name, target, call in targets:
            scripts[shim_name] = _shim_text(tool, target, call=call)
    return scripts


def write_shims(layout: Layout) -> tuple[list[str], list[str]]:
    """生成 / 更新 shim；返回 ``(写入的文件名, 未变化的文件名)``。"""
    written: list[str] = []
    unchanged: list[str] = []
    layout.shim_dir.mkdir(parents=True, exist_ok=True)
    for name, text in shim_scripts().items():
        path = layout.shim_dir / name
        try:
            # 按字节解码，避免通用换行把 shim 的 CRLF 归一成 LF 导致每次重写
            current = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            current = None
        if current == text:
            unchanged.append(name)
            continue
        write_atomic(path, text)
        written.append(name)
    return written, unchanged


# ---- shell 片段 ----
def shell_snippet(layout: Layout, shell: str) -> str:
    """把活动版本目录前置到当前 shell 的 PATH（只打印，不落盘）。"""
    if shell not in _SHELLS:
        raise runtime_error(f"不支持的 shell：{shell}", hint="可选 pwsh / cmd / bash", code="INVALID_ARGUMENT")
    dirs = [str(item) for item in path_dirs(layout)]
    if not dirs:
        return f"# devman: 没有活动版本，先运行 fcmd devman use <tool>@<version>（{shell}）"
    if shell == "pwsh":
        joined = ";".join(dirs)
        return f"$env:PATH = '{joined};' + $env:PATH"
    if shell == "cmd":
        joined = ";".join(dirs)
        return f'set "PATH={joined};%PATH%"'
    posix = [str(item).replace("\\", "/").replace("'", "'\\''") for item in dirs]
    return "export PATH='" + ":".join(posix) + "':$PATH"


def child_env(layout: Layout, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """构造把活动版本目录前置的子进程环境（不修改父进程）。"""
    env = dict(os.environ if base is None else base)
    dirs = [str(item) for item in path_dirs(layout)]
    if dirs:
        env["PATH"] = ";".join([*dirs, env.get("PATH", "")]).strip(";")
    return env


# ---- 检测与冲突 ----
_KNOWN_LOCATIONS = (
    ("node", "{LOCALAPPDATA}\\Programs\\nodejs"),
    ("node", "{ProgramFiles}\\nodejs"),
    ("python", "{LOCALAPPDATA}\\Programs\\Python"),
    ("python", "{LOCALAPPDATA}\\Microsoft\\WindowsApps"),
    ("python", "{ProgramFiles}\\Python311"),
    ("python", "{ProgramFiles}\\Python312"),
)


def detect_environment(
    layout: Layout,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """只读探测：PATH 解析、活动版本、外部安装与需要提示的环境标记。"""
    source = os.environ if env is None else env
    path_value = source.get("PATH", "")
    candidates = {tool: [str(item) for item in command_candidates(tool, path_value)] for tool in ("node", "python")}
    shim_dir = str(layout.shim_dir)
    shim_index = next(
        (index for index, part in enumerate(path_entries(path_value)) if _normalized(part) == _normalized(shim_dir)),
        None,
    )
    markers = {
        "VIRTUAL_ENV": source.get("VIRTUAL_ENV"),
        "CONDA_PREFIX": source.get("CONDA_PREFIX"),
        "UV_PYTHON": source.get("UV_PYTHON"),
        "NVM_HOME": source.get("NVM_HOME"),
        "NVM_SYMLINK": source.get("NVM_SYMLINK"),
    }
    known: list[dict[str, str]] = []
    for tool, pattern in _KNOWN_LOCATIONS:
        expanded = os.path.expandvars(pattern)
        if "%" in expanded or not Path(expanded).is_dir():
            continue
        known.append({"tool": tool, "path": expanded})
    warnings: list[str] = []
    for tool, paths in candidates.items():
        if shim_index is not None and paths:
            first_other = next(
                (item for item in paths if _normalized(str(Path(item).parent)) != _normalized(shim_dir)), None
            )
            if first_other is not None:
                warnings.append(f"PATH 里 {tool} 首先解析到 {first_other}；devman shim 在顺位 {shim_index + 1}")
    if markers["VIRTUAL_ENV"]:
        warnings.append(f"当前 shell 激活了虚拟环境 {markers['VIRTUAL_ENV']}；devman 不会改动它")
    if markers["CONDA_PREFIX"]:
        warnings.append(f"当前 shell 处于 conda 环境 {markers['CONDA_PREFIX']}；conda 激活优先于 devman")
    return {
        "root": str(layout.root),
        "path_resolution": candidates,
        "shim_dir": shim_dir,
        "shim_on_path": shim_index is not None,
        "shim_index": shim_index,
        "active": {tool: active_version(layout, tool) for tool in ("node", "python")},
        "markers": {key: value for key, value in markers.items() if value},
        "known_locations": known,
        "warnings": warnings,
    }


def shadowed_commands(layout: Layout, *, env: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    """前置 shim 目录后会被遮蔽的可执行文件（按工具分组）。"""
    source = os.environ if env is None else env
    path_value = source.get("PATH", "")
    result: dict[str, list[str]] = {}
    for tool in ("node", "python"):
        others: list[str] = []
        for candidate in command_candidates(tool, path_value):
            if _normalized(str(candidate.parent)) == _normalized(str(layout.shim_dir)):
                continue
            others.append(str(candidate))
        if others:
            result[tool] = others
    return result


def install_path(layout: Layout, *, store: EnvStore | None = None, append: bool = False) -> dict[str, object]:
    """把 shim 目录写入用户 PATH；返回 ``{changed, entry, previous, position}``。"""
    backend = store or default_env_store()
    current = backend.get("Path")
    entry = str(layout.shim_dir)
    updated = add_path_entry(current, entry, append=append)
    if updated == (current or ""):
        return {"changed": False, "entry": entry, "previous": current, "position": "append" if append else "prepend"}
    backend.set("Path", updated)
    return {"changed": True, "entry": entry, "previous": current, "position": "append" if append else "prepend"}


def remove_path(layout: Layout, *, store: EnvStore | None = None) -> dict[str, object]:
    """从用户 PATH 精确移除 shim 目录；返回 ``{changed, removed, previous}``。"""
    backend = store or default_env_store()
    current = backend.get("Path")
    updated, removed = remove_path_entry(current, str(layout.shim_dir))
    if removed == 0:
        return {"changed": False, "removed": 0, "previous": current}
    backend.set("Path", updated)
    return {"changed": True, "removed": removed, "previous": current}


def python_hint(layout: Layout, name: str) -> str:
    """给 venv 用户的提示：拿 devman 的解释器建环境，而不是替换它。"""
    exe = layout.version_dir("python", name) / "python.exe"
    return f'uv venv --python "{exe}"'


def describe_version(tool: str, name: str) -> str:
    return f"{tool} {version_of(tool, name)}"
