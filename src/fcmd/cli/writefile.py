"""writefile - 写入文件工具。

将文本内容写入指定路径的文件，支持指定编码。

示例
----
    fcmd writefile note.txt "Hello World"
    fcmd writefile data.json '{"a": 1}' --encoding utf-8
"""

from __future__ import annotations

from pathlib import Path

import fcmd


@fcmd.tool("writefile", help="写入文本内容到文件")
def write_file_run(path: str, content: str, encoding: str = "utf-8") -> None:
    """写入文本内容到文件。

    Parameters
    ----------
    path:
        目标文件路径
    content:
        写入内容
    encoding:
        文件编码（默认 ``utf-8``）
    """
    Path(path).write_text(content, encoding=encoding)
