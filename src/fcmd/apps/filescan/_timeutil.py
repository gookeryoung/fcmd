"""filescan 时间规则共用的时间字段、比较条件与解析 / 格式化（纯逻辑）。

时间值支持 ISO-8601 文本（``2026-01-01`` / ``2026-01-01T09:30:00`` / 带时区偏移）
与 epoch 秒；无时区文本按本机时区解释。时间规则按半开区间比较：

- ``before``：``t < value``
- ``after``：``t >= value``
- ``between``：``value <= t < value2``

本模块不依赖 GUI / 配置模块，供 ``_rules`` / ``_engine`` / GUI 共用。
"""

from __future__ import annotations

from datetime import datetime

TIME_MODIFIED = "modified"
TIME_CREATED = "created"
TIME_ACCESSED = "accessed"
TIME_TARGETS: tuple[str, ...] = (TIME_MODIFIED, TIME_CREATED, TIME_ACCESSED)
"""时间规则可选的 ``target``：修改时间 / 创建时间 / 访问时间。"""

TIME_TARGET_LABELS: dict[str, str] = {
    TIME_MODIFIED: "修改时间",
    TIME_CREATED: "创建时间",
    TIME_ACCESSED: "访问时间",
}
"""时间字段中文标签（规则摘要、结果树位置与报告共用）。"""

TIME_BEFORE = "before"
TIME_AFTER = "after"
TIME_BETWEEN = "between"
TIME_OPS: tuple[str, ...] = (TIME_BEFORE, TIME_AFTER, TIME_BETWEEN)
TIME_OP_LABELS: dict[str, str] = {
    TIME_BEFORE: "早于",
    TIME_AFTER: "晚于",
    TIME_BETWEEN: "介于",
}
"""时间比较条件；区间口径见模块 docstring。"""


def parse_time_value(value: str) -> float:
    """把规则里的时间值解析成 epoch 秒；非法时抛中文 ``ValueError``。"""
    text = value.strip()
    if not text:
        raise ValueError("时间值不能为空")
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"时间 {text!r} 不是合法的 ISO-8601 时间（例 2026-01-01 或 2026-01-01T09:30:00）") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.timestamp()


def format_time_value(value: float) -> str:
    """把 epoch 秒格式化为本机时区 ``YYYY-MM-DD HH:MM:SS``（结果树 / 报告展示）。"""
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
