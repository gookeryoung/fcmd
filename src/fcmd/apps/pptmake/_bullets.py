"""pptmake 要点层级：从纯文本行解析缩进层级与可选的列表标记。

模板 JSON 保持「一行一条要点」的简单结构，层级用缩进表达：

- 每两个空格 / 一个 Tab / 一个全角空格记为一级（最多两级）；
- 行首的 ``- `` / ``* `` / ``• `` 列表标记会被去掉，不算正文。

渲染层负责按层级调整缩进与字号，PPTX 导出写段落级别，两者共用本函数，
避免预览与导出对同一行文本出现两种解释。
"""

from __future__ import annotations

from typing import Final

__all__ = ["MAX_LEVEL", "bullet_level", "split_bullet"]

MAX_LEVEL: Final[int] = 2
"""要点最大缩进层级（0 = 顶层）。"""

_MARKERS: Final[tuple[str, ...]] = ("- ", "* ", "• ", "· ")


def split_bullet(text: str) -> tuple[int, str]:
    """把一行要点拆成 ``(level, 正文)``；空行返回 ``(0, "")``。"""
    rest = text.rstrip("\r\n")
    if not rest.strip():
        return 0, ""
    level = 0
    while True:
        if rest.startswith("\t"):
            rest = rest[1:].lstrip(" ")
        elif rest.startswith("  "):
            rest = rest[2:]
        elif rest.startswith("\u3000"):
            rest = rest[1:]
        else:
            break
        level = min(level + 1, MAX_LEVEL)
    for marker in _MARKERS:
        if rest.startswith(marker):
            rest = rest[len(marker) :]
            break
    return level, rest.strip()


def bullet_level(text: str) -> int:
    """只取层级（供渲染层判断缩进）。"""
    return split_bullet(text)[0]
