"""跨层路径解析：``FCMD_HOME`` 与配置文件位置。

放在 :mod:`fcmd.shared` 而不是 :mod:`fcmd.infras.system`，是为了让 GUI 基座
不必依赖 infras（ADR 0001 分层规则），同时保持 ``infras.system`` 的旧导入路径
继续可用（re-export）。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["fcmd_home", "ui_config_path"]


def fcmd_home() -> Path:
    """返回 ``${FCMD_HOME:-~/.fcmd}``；环境变量里的 ``~`` 会被展开。"""
    raw = os.environ.get("FCMD_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".fcmd"


def ui_config_path(name: str) -> Path:
    """返回 GUI 设置文件路径 ``${FCMD_HOME:-~/.fcmd}/ui/<name>.json``。"""
    return fcmd_home() / "ui" / f"{name}.json"
