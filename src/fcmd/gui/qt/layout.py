"""Qt 布局纯函数：中间省略、列宽分配、分栏尺寸读写。

本模块不导入 ``PySide6``，CI（无显示环境）可直接单测；控件层
:mod:`fcmd.gui.qt.widgets` 只负责把这里的计算结果套到 ``QLabel`` /
``QSplitter`` / ``QHeaderView`` 上。

设计口径：

- 测量函数由调用方注入（Qt 侧传 ``QFontMetrics.horizontalAdvance``），
  便于用固定字宽在单测里验证省略结果；
- 列宽分配保证「每列不小于下限、总和不超过可用宽度」，不足以下限时等分；
- 分栏尺寸只接受正整数列表，非法内容返回 ``None`` 由调用方回退默认布局。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "distribute_widths",
    "elide_middle",
    "parse_sizes",
    "serialize_sizes",
]

DEFAULT_MIN_COLUMN: int = 40
"""表格列的最小宽度（逻辑像素）。"""


def elide_middle(
    text: str,
    width: int,
    measure: Callable[[str], int],
    *,
    ellipsis: str = "…",
    head_ratio: float = 0.6,
) -> str:
    """按像素宽度从中间省略文本，保留开头与结尾。

    Parameters
    ----------
    text:
        原始文本（路径、URL、长文件名）。
    width:
        可用宽度（像素）；``<= 0`` 时返回空串。
    measure:
        文本宽度测量函数，例如 ``lambda s: metrics.horizontalAdvance(s)``。
    ellipsis:
        省略号；默认单字符 ``…``。
    head_ratio:
        保留部分中开头所占比例（0–1），其余留给结尾。

    返回的文本宽度不超过 ``width``；连省略号都放不下时返回空串。
    """
    if not text or width <= 0:
        return ""
    if measure(text) <= width:
        return text
    if measure(ellipsis) > width:
        return ""
    ratio = min(max(head_ratio, 0.0), 1.0)
    low, high = 0, len(text)
    best = ellipsis
    while low <= high:
        keep = (low + high) // 2
        head = int(keep * ratio)
        tail = keep - head
        candidate = text[:head] + ellipsis + (text[len(text) - tail :] if tail else "")
        if measure(candidate) <= width:
            best = candidate
            low = keep + 1
        else:
            high = keep - 1
    return best


def distribute_widths(
    total: int,
    ratios: Sequence[float],
    *,
    minimum: int = DEFAULT_MIN_COLUMN,
) -> list[int]:
    """按比例把 ``total`` 像素分给各列，返回与 ``ratios`` 等长的整数宽度。

    先给每列 ``minimum``，剩余宽度按比例分配（向下取整），零头补给权重最大的列。
    ``total`` 不足以容纳所有下限时等分 ``total``；``total <= 0`` 时全为 0。
    """
    count = len(ratios)
    if count == 0:
        return []
    if total <= 0:
        return [0] * count
    floor = max(minimum, 0)
    if floor * count >= total:
        base, leftover = divmod(total, count)
        return [base + (1 if index < leftover else 0) for index in range(count)]
    weights = [max(float(value), 0.0) for value in ratios]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        weights = [1.0] * count
        weight_sum = float(count)
    remaining = total - floor * count
    widths = [floor + int(remaining * weight / weight_sum) for weight in weights]
    leftover = total - sum(widths)
    if leftover > 0:
        widest = max(range(count), key=lambda index: weights[index])
        widths[widest] += leftover
    return widths


def parse_sizes(raw: object) -> list[int] | None:
    """解析分栏尺寸（JSON 里的整数列表）；非法内容返回 ``None``。"""
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    sizes: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        sizes.append(value)
    return sizes


def serialize_sizes(sizes: Sequence[int]) -> list[int]:
    """把分栏尺寸规整成可写入 JSON 的非负整数列表。"""
    return [max(int(value), 0) for value in sizes]
