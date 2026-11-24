"""filescan 文件大小规则：比较条件、单位解析与展示标签（纯逻辑）。

大小值支持裸字节数（``1024``）与 ``KB`` / ``MB`` / ``GB`` / ``TB``（1024 进制，
大小写不敏感，可带空格）。区间口径与时间规则一致：``lt`` 不含边界、``gt`` 含下界、
``between`` 为半开区间 ``[start, end)``。
"""

from __future__ import annotations

import re

SIZE_LT = "lt"
SIZE_GT = "gt"
SIZE_BETWEEN = "between"
SIZE_OPS: tuple[str, ...] = (SIZE_LT, SIZE_GT, SIZE_BETWEEN)
SIZE_OP_LABELS: dict[str, str] = {
    SIZE_LT: "小于",
    SIZE_GT: "大于",
    SIZE_BETWEEN: "介于",
}

_UNITS: dict[str, int] = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")


def parse_size_value(text: str) -> int:
    """把 ``10MB`` / ``1024`` 解析成字节数；非法时抛中文 ``ValueError``。"""
    raw = text.strip()
    if not raw:
        raise ValueError("大小值不能为空")
    match = _SIZE_RE.match(raw)
    if match is None:
        raise ValueError(f"大小 {raw!r} 格式无效（例 1024、10KB、1.5MB）")
    number, unit = match.groups()
    factor = _UNITS.get(unit.lower())
    if factor is None:
        raise ValueError(f"大小单位 {unit!r} 不支持，只支持 B / KB / MB / GB / TB")
    value = float(number) * factor
    if value != int(value):
        raise ValueError(f"大小 {raw!r} 不是整数字节")
    return int(value)
