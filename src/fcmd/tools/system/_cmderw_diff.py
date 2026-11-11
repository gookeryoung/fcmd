"""cmderw 文本内容比较的纯逻辑：按行并排 diff、统计与读取上限。

Tk 层只负责把 :class:`DiffRow` 渲染成两列只读文本并着色。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["DEFAULT_MAX_LINES", "DiffResult", "DiffRow", "diff_lines", "load_lines", "summary_text"]

DEFAULT_MAX_LINES = 5000
"""单侧最多参与比较的行数，超出部分截断并在标题提示。"""

EQUAL = "equal"
ADD = "add"
DELETE = "delete"
CHANGE = "change"


@dataclass(frozen=True)
class DiffRow:
    """并排显示的一行；缺失侧的行号与文本为 ``None`` / 空串。"""

    kind: str
    left_no: int | None
    left_text: str
    right_no: int | None
    right_text: str


@dataclass(frozen=True)
class DiffResult:
    """整份比较结果与统计。"""

    rows: list[DiffRow]
    added: int
    removed: int
    changed: int
    truncated_left: bool = False
    truncated_right: bool = False

    @property
    def summary(self) -> str:
        """一行统计文本。"""
        return f"新增 {self.added} 行，删除 {self.removed} 行，修改 {self.changed} 行"


def _strip_eol(line: str) -> str:
    return line.rstrip("\r\n")


def diff_lines(
    left_lines: Sequence[str],
    right_lines: Sequence[str],
    *,
    truncated_left: bool = False,
    truncated_right: bool = False,
) -> DiffResult:
    """按行比较两侧文本，返回并排对齐后的行与增删改计数。"""
    left = [_strip_eol(line) for line in left_lines]
    right = [_strip_eol(line) for line in right_lines]
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    rows: list[DiffRow] = []
    added = removed = changed = 0
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(left_end - left_start):
                rows.append(
                    DiffRow(
                        EQUAL,
                        left_start + offset + 1,
                        left[left_start + offset],
                        right_start + offset + 1,
                        right[right_start + offset],
                    )
                )
        elif tag == "delete":
            for index in range(left_start, left_end):
                rows.append(DiffRow(DELETE, index + 1, left[index], None, ""))
                removed += 1
        elif tag == "insert":
            for index in range(right_start, right_end):
                rows.append(DiffRow(ADD, None, "", index + 1, right[index]))
                added += 1
        else:
            pairs = min(left_end - left_start, right_end - right_start)
            for offset in range(pairs):
                rows.append(
                    DiffRow(
                        CHANGE,
                        left_start + offset + 1,
                        left[left_start + offset],
                        right_start + offset + 1,
                        right[right_start + offset],
                    )
                )
                changed += 1
            for index in range(left_start + pairs, left_end):
                rows.append(DiffRow(DELETE, index + 1, left[index], None, ""))
                removed += 1
            for index in range(right_start + pairs, right_end):
                rows.append(DiffRow(ADD, None, "", index + 1, right[index]))
                added += 1
    return DiffResult(
        rows=rows,
        added=added,
        removed=removed,
        changed=changed,
        truncated_left=truncated_left,
        truncated_right=truncated_right,
    )


def load_lines(path: Path, *, max_lines: int = DEFAULT_MAX_LINES) -> tuple[list[str], bool]:
    """读取参与比较的文本行；二进制文件抛 ``ValueError``，超限截断并返回标记。

    参数:
        path: 待读取文件。
        max_lines: 单侧行数上限。

    返回:
        ``(行列表, 是否被截断)``。
    """
    from fcmd.core.shared.textio import read_text_lines

    lines = read_text_lines(path, binary_message="二进制文件无法比较")
    return lines[:max_lines], len(lines) > max_lines


def summary_text(result: DiffResult) -> str:
    """统计文本，附带截断提示。"""
    parts = [result.summary]
    if result.truncated_left:
        parts.append(f"左侧仅比较前 {DEFAULT_MAX_LINES} 行")
    if result.truncated_right:
        parts.append(f"右侧仅比较前 {DEFAULT_MAX_LINES} 行")
    return "；".join(parts)
