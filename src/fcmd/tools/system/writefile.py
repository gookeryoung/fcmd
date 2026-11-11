"""writefile - 写入文本内容到文件。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras.system import write_text_file

__all__ = ["writefile"]


@fx.tool(
    "writefile",
    help="写入文本内容到文件",
    param_help={
        "path": "目标文件路径",
        "content": "写入内容",
        "encoding": "文件编码（默认 utf-8）",
    },
)
def writefile(path: str, content: str, encoding: str = "utf-8") -> None:
    """将文本内容写入指定路径的文件。"""
    write_text_file(path, content, encoding)
