"""setenv - 设置环境变量工具。

设置当前进程的环境变量，支持不覆盖已有值的 ``default`` 模式。

示例
----
    fcmd setenv NAME value             # 设置环境变量
    fcmd setenv NAME value --default   # 仅在未设置时设置
"""

from __future__ import annotations

import os

import fcmd

__all__ = ["setenv_run"]


@fcmd.tool("setenv", help="设置环境变量")
def setenv_run(name: str, value: str, default: bool = False) -> None:
    """设置环境变量（仅影响当前进程）。

    Parameters
    ----------
    name:
        环境变量名
    value:
        环境变量值
    default:
        为 ``True`` 时使用 ``setdefault`` 不覆盖已有值（默认 ``False``）
    """
    if default:
        os.environ.setdefault(name, value)
    else:
        os.environ[name] = value
    print(f"环境变量 {name} 已设置")
