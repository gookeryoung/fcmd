"""cmderw 搜索过滤与结果面板排序的纯逻辑。

搜索本身复用 :mod:`fcmd.dsl.actions.fileops`；本模块只处理大小 / 时间过滤、
数量输入解析与结果去重排序，便于脱离 Tk 单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from typing import Any

__all__ = [
    "SORT_COLUMNS",
    "SearchFilters",
    "SearchResult",
    "collect_results",
    "filter_paths",
    "parse_datetime",
    "parse_size",
    "path_matches",
    "sort_results",
]

_SIZE_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
_SIZE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(?:i?b)?\s*$", re.IGNORECASE)

SORT_COLUMNS = {
    "name": "名称",
    "size": "大小",
    "modified": "修改时间",
    "path": "路径",
}
"""结果面板可排序的列（键为内部列名）。"""


def parse_size(text: str) -> int | None:
    """把 ``10M`` / ``1.5g`` / ``2048`` 解析为字节数；空串返回 ``None``。

    非法输入抛 ``ValueError``（由界面提示，不静默忽略）。
    """
    raw = text.strip()
    if not raw:
        return None
    match = _SIZE_PATTERN.match(raw)
    if match is None:
        raise ValueError(f"无法识别的大小：{text}")
    number = float(match.group(1))
    unit = match.group(2).lower()
    return round(number * _SIZE_UNITS[unit])


def parse_datetime(text: str, *, end_of_day: bool = False) -> float | None:
    """把 ``YYYY-MM-DD`` 解析为本地时间戳；空串返回 ``None``。

    ``end_of_day`` 为真时取当天 23:59:59，便于作为「截止日期」的闭区间。
    """
    raw = text.strip()
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"日期格式应为 YYYY-MM-DD：{text}") from None
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed.timestamp()


@dataclass(frozen=True)
class SearchFilters:
    """大小与修改时间范围（闭区间，``None`` 表示不限制）。"""

    min_size: int | None = None
    max_size: int | None = None
    modified_after: float | None = None
    modified_before: float | None = None

    @property
    def active(self) -> bool:
        """是否启用了任一条过滤。"""
        return any(
            value is not None for value in (self.min_size, self.max_size, self.modified_after, self.modified_before)
        )


def path_matches(path: Path, filters: SearchFilters) -> bool:
    """按大小 / 修改时间判断路径是否保留；``stat`` 失败时丢弃。"""
    try:
        stat = path.stat()
    except OSError:
        return False
    if filters.min_size is not None and stat.st_size < filters.min_size:
        return False
    if filters.max_size is not None and stat.st_size > filters.max_size:
        return False
    if filters.modified_after is not None and stat.st_mtime < filters.modified_after:
        return False
    return filters.modified_before is None or stat.st_mtime <= filters.modified_before


def filter_paths(paths: Iterable[Path], filters: SearchFilters) -> list[Path]:
    """按过滤条件筛选路径；未启用过滤时原样返回。"""
    items = list(paths)
    if not filters.active:
        return items
    return [path for path in items if path_matches(path, filters)]


@dataclass(frozen=True)
class SearchResult:
    """结果面板的一行（同一文件只保留一条）。"""

    path: Path
    size: int
    modified: float


def collect_results(paths: Iterable[Path]) -> list[SearchResult]:
    """按路径去重并补齐大小 / 修改时间；``stat`` 失败按 0 处理。"""
    results: list[SearchResult] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            stat = path.stat()
            size, modified = stat.st_size, stat.st_mtime
        except OSError:
            size, modified = 0, 0.0
        results.append(SearchResult(path=path, size=size, modified=modified))
    return results


def sort_results(results: Sequence[SearchResult], column: str = "name", *, reverse: bool = False) -> list[SearchResult]:
    """按指定列排序；``name`` / ``path`` 使用大小写不敏感比较。"""
    keys: dict[str, Callable[[SearchResult], Any]] = {
        "name": lambda item: (item.path.name.casefold(), str(item.path).casefold()),
        "size": lambda item: (item.size, item.path.name.casefold()),
        "modified": lambda item: (item.modified, item.path.name.casefold()),
        "path": lambda item: str(item.path).casefold(),
    }
    return sorted(results, key=keys.get(column, keys["name"]), reverse=reverse)
