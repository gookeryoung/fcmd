"""状态、配置、安装标记与活动版本文件。

``state.json`` / ``config.json`` 用「临时文件 + 原子替换」写入；配置按字段级
容错（单个非法字段只回落该字段），状态文件损坏时按「无活动版本」处理并给
``warn``，不阻塞后续命令。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fcmd.apps.devman._errors import runtime_error
from fcmd.apps.devman._paths import TOOLS, Layout

__all__ = [
    "CONFIG_SCHEMA",
    "MARKER_NAME",
    "MARKER_SCHEMA",
    "STATE_SCHEMA",
    "Config",
    "State",
    "active_version",
    "load_config",
    "load_state",
    "now_iso",
    "read_marker",
    "remove_active",
    "save_state",
    "set_active",
    "write_atomic",
    "write_marker",
]

CONFIG_SCHEMA = "devman-config/1"
STATE_SCHEMA = "devman-state/1"
MARKER_SCHEMA = "devman-install/1"
MARKER_NAME = ".devman.json"


def now_iso() -> str:
    """本地时区的 ISO 8601 时间戳（秒级）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_atomic(path: Path, text: str) -> None:
    """原子写入文本：同目录临时文件 + ``os.replace``。

    与 ``fcmd.gui.config.write_settings`` 的区别：本函数把 ``OSError`` 抛给调用方，
    由命令层决定报错文案；devman 的状态写入失败必须可见，不能静默吞掉。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        tmp.replace(path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise runtime_error(f"写入 {path} 失败：{exc}", hint="检查目录权限后重试", code="ROOT_NOT_WRITABLE") from exc


@dataclass(frozen=True, slots=True)
class Config:
    """``config.json`` 的有效值（缺失 / 非法字段已回落默认）。"""

    node_mirror: str = "auto"
    python_uv: str = "auto"
    python_mirror: str | None = None
    timeout_s: int = 120
    progress: bool = True
    # None=跟随系统，URL=显式代理，off/none=显式禁用（跳过环境变量代理）
    proxy: str | None = None

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": CONFIG_SCHEMA,
            "node": {"mirror": self.node_mirror},
            "python": {"uv": self.python_uv, "mirror": self.python_mirror},
            "network": {"timeout_s": self.timeout_s, "progress": self.progress, "proxy": self.proxy},
        }


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """读取对象型 JSON；缺失返回 ``(None, None)``，损坏返回 ``(None, 原因)``。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as exc:
        return None, f"{path} 无法解析：{exc}"
    if not isinstance(raw, dict):
        return None, f"{path} 不是 JSON 对象"
    return raw, None


def load_config(layout: Layout) -> tuple[Config, list[str]]:
    """读取配置；单字段非法只回落该字段并记录一条 ``warn``。"""
    raw, problem = _read_json(layout.config_file)
    warnings: list[str] = []
    if problem:
        return Config(), [problem]
    if raw is None:
        return Config(), warnings

    defaults = Config()
    node = raw.get("node") if isinstance(raw.get("node"), dict) else {}
    python = raw.get("python") if isinstance(raw.get("python"), dict) else {}
    network = raw.get("network") if isinstance(raw.get("network"), dict) else {}

    mirror = node.get("mirror", defaults.node_mirror)
    if not isinstance(mirror, str) or not mirror.strip():
        warnings.append("config.node.mirror 非法，已回落 auto")
        mirror = defaults.node_mirror

    uv = python.get("uv", defaults.python_uv)
    if not isinstance(uv, str) or not uv.strip():
        warnings.append("config.python.uv 非法，已回落 auto")
        uv = defaults.python_uv

    py_mirror = python.get("mirror", defaults.python_mirror)
    if py_mirror is not None and (not isinstance(py_mirror, str) or not py_mirror.strip()):
        warnings.append("config.python.mirror 非法，已回落 null")
        py_mirror = None

    timeout = network.get("timeout_s", defaults.timeout_s)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        warnings.append("config.network.timeout_s 非法，已回落 120")
        timeout = defaults.timeout_s

    progress = network.get("progress", defaults.progress)
    if not isinstance(progress, bool):
        warnings.append("config.network.progress 非法，已回落 true")
        progress = defaults.progress

    proxy = network.get("proxy", defaults.proxy)
    if proxy is not None:
        if not isinstance(proxy, str) or not proxy.strip():
            warnings.append("config.network.proxy 非法，已回落 null")
            proxy = None
        else:
            proxy = proxy.strip()

    return (
        Config(
            node_mirror=mirror,
            python_uv=uv,
            python_mirror=py_mirror,
            timeout_s=timeout,
            progress=progress,
            proxy=proxy,
        ),
        warnings,
    )


@dataclass(slots=True)
class State:
    """``state.json`` 的内存形态。"""

    active: dict[str, str] = field(default_factory=dict)
    path_added: list[str] = field(default_factory=list)
    path_previous_user_path: str | None = None

    def to_data(self) -> dict[str, Any]:
        return {
            "schema": STATE_SCHEMA,
            "active": dict(self.active),
            "path_added": list(self.path_added),
            "path_previous_user_path": self.path_previous_user_path,
            "updated_at": now_iso(),
        }


def load_state(layout: Layout) -> tuple[State, list[str]]:
    """读取状态；缺失按默认值，损坏时丢弃并给一条 ``warn``。"""
    raw, problem = _read_json(layout.state_file)
    if problem:
        return State(), [problem + "；已按「无活动版本」处理"]
    if raw is None:
        return State(), []

    warnings: list[str] = []
    active_raw = raw.get("active")
    active: dict[str, str] = {}
    if isinstance(active_raw, dict):
        for tool in TOOLS:
            value = active_raw.get(tool)
            if isinstance(value, str) and value.strip():
                active[tool] = value.strip()
    elif active_raw is not None:
        warnings.append("state.active 非法，已忽略")

    added_raw = raw.get("path_added")
    path_added = [item for item in added_raw if isinstance(item, str)] if isinstance(added_raw, list) else []
    previous = raw.get("path_previous_user_path")
    return (
        State(
            active=active,
            path_added=path_added,
            path_previous_user_path=previous if isinstance(previous, str) else None,
        ),
        warnings,
    )


def save_state(layout: Layout, state: State) -> None:
    """写入状态文件（原子替换）。"""
    write_atomic(layout.state_file, json.dumps(state.to_data(), ensure_ascii=False, indent=2) + "\n")


def active_version(layout: Layout, tool: str) -> str | None:
    """读取活动版本名；文件缺失或为空返回 ``None``。"""
    try:
        value = layout.active_file(tool).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def set_active(layout: Layout, tool: str, name: str | None) -> None:
    """原子设置（``name`` 为 ``None`` 时删除）活动版本文件。"""
    path = layout.active_file(tool)
    if name is None:
        path.unlink(missing_ok=True)
        return
    write_atomic(path, name + "\n")


def remove_active(layout: Layout, tool: str) -> None:
    """删除活动版本文件（不存在时无操作）。"""
    layout.active_file(tool).unlink(missing_ok=True)


def read_marker(version_dir: Path) -> dict[str, Any] | None:
    """读取安装标记；缺失 / 损坏 / 非对象返回 ``None``。"""
    try:
        raw = json.loads((version_dir / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def write_marker(version_dir: Path, payload: dict[str, Any]) -> None:
    """写安装标记（必须在版本目录原子落位之后调用）。"""
    write_atomic(version_dir / MARKER_NAME, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
