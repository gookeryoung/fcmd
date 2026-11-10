"""命令声明层的异常类型。"""

from __future__ import annotations


class CommandDeclError(ValueError):
    """命令声明非法（配置错误，非运行时错误）。"""
