"""项目级版本绑定：``devman.toml`` 与 ``.node-version`` / ``.python-version``。

只读文件系统、不联网，也不改任何全局状态：``exec`` / ``env`` 按项目文件
构造进程环境，``use --project`` 只写当前目录的单行版本文件。
``devman.toml`` 由用户手写（``[versions]`` 表），devman 只读不写，避免重排注释。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import TOOLS
from fcmd.apps.devman._state import write_atomic
from fcmd.apps.devman._toolchain import parse_spec

__all__ = [
    "PROJECT_FILE",
    "SCHEMA",
    "VERSION_FILES",
    "Pin",
    "discover",
    "read_directory",
    "read_project_file",
    "read_version_file",
    "remove_pin",
    "write_pin",
]

PROJECT_FILE = "devman.toml"
SCHEMA = "devman-project/1"
VERSION_FILES = {"node": ".node-version", "python": ".python-version"}


@dataclass(frozen=True, slots=True)
class Pin:
    """一条项目版本绑定：``tool@raw`` 来自哪个文件。"""

    tool: str
    raw: str
    source: str
    kind: str


def _normalize(tool: str, text: str) -> str | None:
    """校验并归一版本写法；``v20.11.1`` 去掉前缀，非法返回 ``None``。

    项目文件只接受以数字开头的具体版本/前缀（``20``、``3.12``），
    不接受 ``latest`` / ``lts`` 这类通道，否则新机复现不可控。
    """
    value = text.strip()
    if value[:1] in {"v", "V"} and value[1:2].isdigit():
        value = value[1:]
    if not value or not value[0].isdigit():
        return None
    try:
        parse_spec(f"{tool}@{value}")
    except DevmanError:
        return None
    return value


def read_version_file(tool: str, path: Path) -> tuple[Pin | None, list[str]]:
    """读取 ``.node-version`` / ``.python-version``：取第一条合法版本。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"{path} 无法读取：{exc}"]
    for line in text.splitlines():
        raw = _normalize(tool, line)
        if raw is not None:
            return Pin(tool=tool, raw=raw, source=str(path), kind=path.name), []
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return None, [f"{path} 的版本写法不合法：{stripped!r}"]
    return None, []


def read_project_file(path: Path) -> tuple[dict[str, Pin], list[str]]:
    """读取 ``devman.toml`` 的 ``[versions]`` 表；单字段非法只跳过该字段。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, [f"{path} 无法读取：{exc}"]
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return {}, [f"{path} 不是合法 TOML：{exc}"]
    versions = data.get("versions")
    if versions is None:
        return {}, []
    if not isinstance(versions, dict):
        return {}, [f"{path} 的 [versions] 必须是表"]

    pins: dict[str, Pin] = {}
    warnings: list[str] = []
    for tool in TOOLS:
        value = versions.get(tool)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            warnings.append(f"{path} 的 versions.{tool} 非法，已忽略")
            continue
        raw = _normalize(tool, value)
        if raw is None:
            warnings.append(f"{path} 的 versions.{tool} 版本写法不合法：{value!r}")
            continue
        pins[tool] = Pin(tool=tool, raw=raw, source=str(path), kind=PROJECT_FILE)
    return pins, warnings


def read_directory(directory: Path) -> tuple[dict[str, Pin], list[str]]:
    """读取单个目录的绑定：同目录 ``devman.toml`` 优先于单行版本文件。"""
    pins: dict[str, Pin] = {}
    warnings: list[str] = []
    project = directory / PROJECT_FILE
    if project.is_file():
        pins, warnings = read_project_file(project)
    for tool in TOOLS:
        if tool in pins:
            continue
        legacy = directory / VERSION_FILES[tool]
        if legacy.is_file():
            pin, problems = read_version_file(tool, legacy)
            warnings.extend(problems)
            if pin is not None:
                pins[tool] = pin
    return pins, warnings


def discover(
    *,
    start: Path | str | None = None,
    stop: Path | str | None = None,
) -> tuple[dict[str, Pin], list[str]]:
    """从 ``start`` 向上查找，每个工具取最近的一条绑定（``devman.toml`` 优先）。

    默认不读用户主目录及其上级：``~/.python-version`` 属于 pyenv 的全局语义，
    devman 的全局版本由 ``active/<tool>.txt`` 表示，两者混用会互相覆盖。
    ``stop`` 为包含在内的最后一层目录（显式指定时不套用主目录规则）。
    """
    current = Path(start or Path.cwd()).resolve()
    boundary = Path(stop).resolve() if stop is not None else None
    home = Path.home().resolve()
    pins: dict[str, Pin] = {}
    warnings: list[str] = []
    for index, directory in enumerate((current, *current.parents)):
        if boundary is None and index > 0 and directory == home:
            break
        found, problems = read_directory(directory)
        warnings.extend(problems)
        for tool, pin in found.items():
            pins.setdefault(tool, pin)
        if len(pins) == len(TOOLS):
            break
        if boundary is not None and directory == boundary:
            break
    return pins, warnings


def write_pin(directory: Path | str, tool: str, raw: str) -> Path:
    """把 ``tool@raw`` 写入目录下的单行版本文件，返回文件路径。"""
    if tool not in VERSION_FILES:
        raise KeyError(tool)
    path = Path(directory) / VERSION_FILES[tool]
    write_atomic(path, f"{raw}\n")
    return path


def remove_pin(directory: Path | str, tool: str) -> bool:
    """删除目录下的单行版本文件；返回是否真的删除了。"""
    path = Path(directory) / VERSION_FILES[tool]
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
