"""clr - 清屏（跨平台平台命令）。"""

from __future__ import annotations

import sys

import fcmd as fx

__all__ = ["clr"]


@fx.tool(
    "clr",
    help="清屏（跨平台）",
    cmd="cls" if sys.platform == "win32" else ["clear"],
)
def clr() -> None:
    pass  # pragma: no cover - cmd 任务：签名仅驱动 CLI，函数体不执行
