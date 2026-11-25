"""图表页的选项：类型、单位、数值格式、数据标签与图例。

与 :mod:`fcmd.apps.pptmake._core` 解耦，避免模板模型继续膨胀；错误统一以
``ValueError`` 抛出，由模板加载层转换成 ``TemplateError``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "CHART_BAR",
    "CHART_LINE",
    "CHART_PIE",
    "CHART_TYPES",
    "CHART_TYPE_TEXT",
    "ChartOptions",
    "chart_options",
    "normalize_chart_type",
]

CHART_BAR: Final[str] = "bar"
CHART_LINE: Final[str] = "line"
CHART_PIE: Final[str] = "pie"
CHART_TYPES: Final[tuple[str, ...]] = (CHART_BAR, CHART_LINE, CHART_PIE)
CHART_TYPE_TEXT: Final[dict[str, str]] = {
    CHART_BAR: "柱状图",
    CHART_LINE: "折线图",
    CHART_PIE: "饼图",
}


@dataclass(frozen=True, slots=True)
class ChartOptions:
    """图表页的可选参数；默认柱状图，显示数据标签与图例。"""

    chart_type: str = CHART_BAR
    unit: str = ""
    value_format: str = ""
    show_labels: bool = True
    legend: bool = True


def normalize_chart_type(value: object) -> str:
    """校验图表类型；缺省柱状图，未知值报错。"""
    text = str(CHART_BAR if value is None or value == "" else value).strip().lower()
    if text in CHART_TYPES:
        return text
    raise ValueError(f"chart_type 需为 {' / '.join(CHART_TYPES)}：{value!r}")


def _flag(raw: object, *, default: bool, source: str, name: str) -> bool:
    """宽松解析布尔开关：接受真假值与 1/0、true/false。"""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    text = str(raw).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{source}.{name} 需为布尔值：{raw!r}")


def chart_options(raw: object, *, source: str = "slides") -> ChartOptions:
    """从单页字典里读取图表选项；非对象或非法值报错。"""
    if not isinstance(raw, dict):
        raise ValueError(f"{source} 需为对象")
    try:
        kind = normalize_chart_type(raw.get("chart_type"))
        show_labels = _flag(raw.get("show_labels"), default=True, source=source, name="show_labels")
        legend = _flag(raw.get("legend"), default=True, source=source, name="legend")
    except ValueError as exc:
        raise ValueError(f"{source}：{exc}") from exc
    return ChartOptions(
        chart_type=kind,
        unit="" if raw.get("unit") is None else str(raw["unit"]).strip(),
        value_format="" if raw.get("value_format") is None else str(raw["value_format"]).strip(),
        show_labels=show_labels,
        legend=legend,
    )
