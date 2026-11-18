"""JSON 文件读写：统一的 UTF-8 解码与原子落盘。

各 app 的状态 / 缓存文件原先各写各的「读文本 + json.loads」「临时文件 +
os.replace」，异常兜底与临时文件命名也不一致。本模块只提供两个动作，
失败语义保持透明：``OSError`` / ``ValueError`` 原样抛给调用方，由调用方
决定静默降级还是报错。

``fcmd.gui.config`` 不走这里——它额外维护 ``.bak`` 与 UI 专用默认值。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = [
    "read_json_file",
    "write_json_atomic",
    "write_text_atomic",
]


def read_json_file(path: Path) -> Any:
    """读取 UTF-8 JSON 文件；文件缺失或内容非法时原样抛出异常。"""
    return json.loads(path.read_text(encoding="utf-8"))


def write_text_atomic(path: Path, text: str) -> None:
    """原子写入文本：同目录临时文件 + ``os.replace``，失败时清理临时文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def write_json_atomic(
    path: Path,
    payload: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = False,
    separators: tuple[str, str] | None = None,
) -> None:
    """把 ``payload`` 序列化为 JSON 并原子写入 ``path``（末尾补换行）。

    Parameters
    ----------
    indent:
        缩进宽度；``None`` 表示单行输出（缓存类文件常用）。
    sort_keys:
        是否按键排序，便于人工 diff。
    separators:
        ``json.dumps`` 的分隔符；紧凑缓存可传 ``(",", ":")``。
    """
    text = json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=sort_keys, separators=separators)
    write_text_atomic(path, text + "\n")
