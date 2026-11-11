"""标签页锁定、选择记忆与持久化过滤器的纯逻辑。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from fcmd.apps.cmder._ops import matches_mask

MAX_TAB_TITLE = 64
MAX_MASK_LENGTH = 512
MAX_SAVED_SELECTION = 2000


@dataclass(frozen=True)
class FilterDefinition:
    """包含 / 排除掩码；掩码用 glob，分号分隔多个，空掩码表示该方向不过滤。"""

    include: str = ""
    exclude: str = ""

    @property
    def active(self) -> bool:
        """是否定义了任一方向的掩码。"""
        return bool(self.include or self.exclude)

    def to_dict(self) -> dict[str, str]:
        """序列化为设置文件中的字典。"""
        return {"include": self.include, "exclude": self.exclude}

    def describe(self) -> str:
        """状态栏可读描述。"""
        parts: list[str] = []
        if self.include:
            parts.append(f"包含 {self.include}")
        if self.exclude:
            parts.append(f"排除 {self.exclude}")
        return "；".join(parts) if parts else "未定义"


def _normalize_mask(raw: object) -> str:
    """规范化单个掩码：去空白、丢弃空段、限制总长度。"""
    if not isinstance(raw, str):
        return ""
    parts = [part.strip() for part in raw.split(";") if part.strip()]
    return ";".join(parts)[:MAX_MASK_LENGTH]


def normalize_filter_definition(raw: object) -> FilterDefinition:
    """解析持久化过滤器；非字典或字段非法时回退空掩码。"""
    if not isinstance(raw, dict):
        return FilterDefinition()
    data = cast(dict[str, Any], raw)
    return FilterDefinition(include=_normalize_mask(data.get("include")), exclude=_normalize_mask(data.get("exclude")))


def apply_filter_definition(names: Sequence[str], definition: FilterDefinition) -> list[str]:
    """先按包含掩码保留，再按排除掩码剔除；保持原顺序。"""
    result = list(names)
    if definition.include:
        result = [name for name in result if matches_mask(name, definition.include)]
    if definition.exclude:
        result = [name for name in result if not matches_mask(name, definition.exclude)]
    return result


def normalize_tab_title(raw: object) -> str:
    """标签自定义名：折叠空白并截断；非字符串或空白返回空串。"""
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())[:MAX_TAB_TITLE]


def tab_label(path: str, title: str = "") -> str:
    """标签基础名称：自定义名优先，其次目录名，最后完整路径。"""
    custom = normalize_tab_title(title)
    if custom:
        return custom
    if not path:
        return ""
    return Path(path).name or path


def tab_bar_label(path: str, title: str = "", *, locked: bool = False) -> str:
    """标签栏显示文本：超长省略前段，锁定标签加前缀。"""
    label = tab_label(path, title) or "（空）"
    if len(label) > 24:
        label = "…" + label[-23:]
    return f"[锁] {label}" if locked else label


def normalize_selection(raw: object) -> list[str]:
    """解析保存的选择：丢弃非字符串与空项，去重保序并截断。"""
    if not isinstance(raw, (list, tuple)):
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, str) and item and item not in names:
            names.append(item)
        if len(names) >= MAX_SAVED_SELECTION:
            break
    return names


def restore_selection(visible: Sequence[str], saved: Sequence[str]) -> list[str]:
    """在可见名称中恢复保存的选择，忽略已消失项。"""
    available = set(visible)
    return [name for name in saved if name in available]


def close_other_indices(count: int, active: int, locked: Sequence[int]) -> list[int]:
    """「关闭其他标签」要移除的下标（升序）：当前与锁定标签保留。"""
    if count <= 0:
        return []
    keep = {active} | {index for index in locked if 0 <= index < count}
    return [index for index in range(count) if index not in keep]
