"""基础副作用动作：环境变量设置与文件写入。"""

from __future__ import annotations

import os
from pathlib import Path

from fcmd.dsl.actions import action

__all__: list[str] = []


@action(
    "setenv",
    param_help={
        "name": "环境变量名",
        "value": "环境变量值",
        "default": "为 True 时使用 setdefault 不覆盖已有值",
    },
)
def _setenv(name: str, value: str, default: bool = False) -> None:
    """设置当前进程环境变量（仅影响 fcmd 进程及其后续子任务）。"""
    if default:
        os.environ.setdefault(name, value)
    else:
        os.environ[name] = value


@action(
    "writefile",
    param_help={
        "path": "目标文件路径",
        "content": "写入内容",
        "encoding": "文件编码（默认 utf-8）",
    },
)
def _writefile(path: str, content: str, encoding: str = "utf-8") -> None:
    """将文本内容写入指定路径的文件。"""
    Path(path).write_text(content, encoding=encoding)
