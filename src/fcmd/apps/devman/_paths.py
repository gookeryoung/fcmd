"""根目录解析与目录布局（所有路径的唯一出处）。

优先级：``--root`` > ``DEVMAN_ROOT`` > 平台默认。Windows 默认
``%LOCALAPPDATA%\\devman``：工具链是本机缓存，不跟着漫游配置同步。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from fcmd.apps.devman._errors import invalid_argument

__all__ = ["ROOT_ENV", "TOOLS", "Layout", "normalize_tool", "resolve_root"]

ROOT_ENV = "DEVMAN_ROOT"
TOOLS = ("node", "python")

_TOOL_ALIASES = {"node": "node", "nodejs": "node", "python": "python"}


def normalize_tool(name: str) -> str:
    """把工具名别名收敛成规范名；未知工具抛 ``INVALID_ARGUMENT``。"""
    canonical = _TOOL_ALIASES.get(name.strip().lower())
    if canonical is None:
        raise invalid_argument(
            f"不支持的工具 {name!r}",
            hint="首批支持 node（nodejs）与 python",
            field="tool",
        )
    return canonical


def _default_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "devman"
        return Path.home() / "AppData" / "Local" / "devman"
    data = os.environ.get("XDG_DATA_HOME")
    return (Path(data) if data else Path.home() / ".local" / "share") / "devman"


def resolve_root(explicit: str | Path | None = None) -> Path:
    """返回绝对根目录；显式值为空串时按未给出处理。"""
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    env_value = os.environ.get(ROOT_ENV)
    if env_value and env_value.strip():
        return Path(env_value).expanduser().resolve()
    return _default_root()


@dataclass(frozen=True, slots=True)
class Layout:
    """一次命令运行使用的根与固定子路径。"""

    root: Path

    @classmethod
    def create(cls, explicit: str | Path | None = None) -> Layout:
        return cls(resolve_root(explicit))

    # ---- 固定子路径 ----
    @property
    def node_root(self) -> Path:
        return self.root / "node"

    @property
    def python_root(self) -> Path:
        return self.root / "python"

    @property
    def shim_dir(self) -> Path:
        return self.root / "bin"

    @property
    def active_dir(self) -> Path:
        return self.root / "active"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache" / "downloads"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def lock_file(self) -> Path:
        return self.root / ".lock"

    # ---- 工具相关路径 ----
    def tool_root(self, tool: str) -> Path:
        return self.node_root if tool == "node" else self.python_root

    def version_dir(self, tool: str, name: str) -> Path:
        return self.tool_root(tool) / name

    def active_file(self, tool: str) -> Path:
        return self.active_dir / f"{tool}.txt"

    def ensure_bootstrap(self) -> None:
        """创建安装所需的最小目录集合（只在真正写入前调用）。"""
        for path in (self.root, self.node_root, self.python_root, self.shim_dir, self.active_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)
