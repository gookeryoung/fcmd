"""setenv - 设置环境变量（仅影响当前 fcmd 进程及其子任务）。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras.system import set_environment_variable

__all__ = ["setenv"]


@fx.tool(
    "setenv",
    help="设置环境变量",
    param_help={
        "name": "环境变量名",
        "value": "环境变量值",
        "default": "为 True 时使用 setdefault 不覆盖已有值",
    },
)
def setenv(name: str, value: str, default: bool = False) -> None:
    """设置当前进程环境变量（仅影响 fcmd 进程及其后续子任务）。"""
    set_environment_variable(name, value, default)
    print(f"环境变量 {name} 已设置")
