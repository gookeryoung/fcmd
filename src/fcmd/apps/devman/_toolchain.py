"""工具与版本模型：规格解析、已安装扫描、版本比较与解析。

纯逻辑模块：不联网、不执行子进程，只读文件系统；单元测试可全部覆盖。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.devman._errors import INCOMPLETE_INSTALL, invalid_argument, not_found
from fcmd.apps.devman._paths import Layout, normalize_tool
from fcmd.apps.devman._state import MARKER_SCHEMA, read_marker

__all__ = [
    "CHANNELS",
    "Installed",
    "Spec",
    "executable_name",
    "executable_path",
    "is_complete",
    "list_installed",
    "matches",
    "parse_spec",
    "resolve_installed",
    "sort_key",
    "version_of",
]

CHANNELS = ("latest", "lts")

_SPEC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_NUM_RE = re.compile(r"(\d+(?:\.\d+)*)")
_PY_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(frozen=True, slots=True)
class Spec:
    """``<tool>[@<version|channel>]`` 的解析结果；``raw`` 为空表示不限版本。"""

    tool: str
    raw: str = ""

    @property
    def channel(self) -> str | None:
        return self.raw.lower() if self.raw.lower() in CHANNELS else None

    @property
    def text(self) -> str:
        return self.tool if not self.raw else f"{self.tool}@{self.raw}"


def parse_spec(text: str) -> Spec:
    """解析版本规格；非法输入抛 ``INVALID_ARGUMENT``。"""
    value = (text or "").strip()
    if not value:
        raise invalid_argument("缺少工具或版本规格", hint="例如：node@20.11.1、python@3.12")
    tool_part, _, version_part = value.partition("@")
    tool = normalize_tool(tool_part)
    raw = version_part.strip()
    if raw and not _SPEC_RE.fullmatch(raw):
        raise invalid_argument(
            f"版本规格不合法：{raw!r}",
            hint="允许版本号（20.11.1 / 3.12）、通道（latest / lts）；不允许路径分隔符",
            field="spec",
        )
    if raw.lower() == "lts" and tool not in {"node", "jdk"}:
        raise invalid_argument(
            "lts 通道仅适用于 node / jdk",
            hint="其余运行时请用具体版本或 latest",
            field="spec",
        )
    return Spec(tool, raw)


def version_of(tool: str, name: str) -> str:
    """从安装目录名推出可比较的版本号（python 取 cpython-<ver> 段）。"""
    if tool == "python":
        found = _PY_VERSION_RE.search(name)
        if found:
            return found.group(1)
    return name


def sort_key(version: str) -> tuple[int, ...]:
    """版本排序键：取全部数字段（``3.12.15`` / ``20.11.1``）。"""
    parts = [int(part) for part in re.findall(r"\d+", version)]
    return tuple(parts)


def matches(raw: str, version: str, *, name: str | None = None) -> bool:
    """``raw`` 是否匹配目标：全键（``cpython-3.12.15-...``）按名字前缀，其余按数字段前缀。"""
    if not raw:
        return True
    if name is not None and not raw[:1].isdigit():
        return name == raw or name.startswith(raw)
    wanted = sort_key(raw)
    actual = sort_key(version)
    return bool(wanted) and actual[: len(wanted)] == wanted


@dataclass(frozen=True, slots=True)
class Installed:
    """一个已安装（或半完成）版本的快照。"""

    tool: str
    name: str
    version: str
    path: Path
    complete: bool
    marker: dict[str, Any] | None

    def to_data(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "name": self.name,
            "version": self.version,
            "path": str(self.path),
            "status": "ok" if self.complete else "incomplete",
            "source": (self.marker or {}).get("source"),
            "installed_at": (self.marker or {}).get("installed_at"),
            "verify": (self.marker or {}).get("verify"),
        }


_EXECUTABLES = {
    "node": "node.exe",
    "python": "python.exe",
    "go": "bin/go.exe",
    "deno": "deno.exe",
    "bun": "bun.exe",
    "jdk": "bin/java.exe",
}


def executable_name(tool: str) -> str:
    """主可执行文件相对版本目录的路径。"""
    return _EXECUTABLES.get(tool, f"{tool}.exe")


def executable_path(layout: Layout, tool: str, name: str) -> Path:
    """版本目录内主可执行文件路径。"""
    return layout.version_dir(tool, name) / executable_name(tool)


def is_complete(layout: Layout, tool: str, name: str) -> bool:
    """安装完成判据：标记可解析、字段自洽、主可执行文件存在。"""
    marker = read_marker(layout.version_dir(tool, name))
    if marker is None:
        return False
    if marker.get("schema") != MARKER_SCHEMA:
        return False
    if marker.get("tool") != tool:
        return False
    if marker.get("version") != version_of(tool, name):
        return False
    return executable_path(layout, tool, name).is_file()


def list_installed(layout: Layout, tool: str) -> list[Installed]:
    """列出某工具的全部版本目录（含半完成），按版本升序。"""
    root = layout.tool_root(tool)
    try:
        entries = [
            item
            for item in root.iterdir()
            if item.is_dir()
            and not item.name.startswith(".")
            # uv 会在安装目录里放 <impl>-<major>.<minor> 的 junction 别名，不算版本
            and not (item.is_symlink() or os.path.isjunction(item))
        ]
    except FileNotFoundError:
        return []
    except OSError:
        return []
    rows: list[Installed] = []
    for entry in entries:
        marker = read_marker(entry)
        complete = is_complete(layout, tool, entry.name)
        rows.append(
            Installed(
                tool=tool,
                name=entry.name,
                version=version_of(tool, entry.name),
                path=entry,
                complete=complete,
                marker=marker,
            )
        )
    return sorted(rows, key=lambda row: sort_key(row.version))


def resolve_installed(layout: Layout, spec: Spec, *, require_complete: bool = True) -> Installed:
    """在已安装集合里解析到唯一版本（取最高匹配）。

    ``require_complete`` 为真时跳过半完成目录（半完成版本不可切换 / 执行）。
    """
    rows = list_installed(layout, spec.tool)
    candidates = [
        row for row in rows if matches(spec.raw, row.version, name=row.name) and (row.complete or not require_complete)
    ]
    if not candidates:
        if any(matches(spec.raw, row.version, name=row.name) for row in rows) and require_complete:
            raise not_found(
                f"{spec.tool} 的 {spec.raw or '任意版本'} 只有半完成安装",
                hint=f"运行 fcmd devman install {spec.text} 重新安装",
                code=INCOMPLETE_INSTALL,
            )
        raise not_found(
            f"未安装 {spec.text}",
            hint=f"运行 fcmd devman install {spec.text}",
        )
    return max(candidates, key=lambda row: sort_key(row.version))
