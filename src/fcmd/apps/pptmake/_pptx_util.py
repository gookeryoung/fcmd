"""python-pptx 底层工具：符号集合、单位换算与颜色/字体写入。

与 :mod:`fcmd.apps.pptmake._pptx` 共用，避免导出模块因图元增多而超出行数预算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fcmd.apps.pptmake._core import TemplateError, Theme, normalize_hex

_DESIGN_UNITS_PER_INCH = 96.0
_POINTS_PER_UNIT = 72.0 / _DESIGN_UNITS_PER_INCH


@dataclass(frozen=True, slots=True)
class _Api:
    """python-pptx 常用符号集合，避免在各处重复导入。"""

    Presentation: Any
    RGBColor: Any
    MSO_SHAPE: Any
    MSO_CONNECTOR: Any
    MSO_ANCHOR: Any
    MSO_AUTO_SIZE: Any
    MSO_THEME_COLOR: Any
    PP_ALIGN: Any
    Inches: Any
    Pt: Any
    CategoryChartData: Any
    XL_CHART_TYPE: Any
    XL_LABEL_POSITION: Any
    XL_LEGEND_POSITION: Any


def _api() -> _Api:
    """延迟导入 python-pptx；缺失时给出可操作的安装提示。"""
    try:
        from pptx import Presentation
        from pptx.chart.data import CategoryChartData
        from pptx.dml.color import RGBColor
        from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
        from pptx.enum.dml import MSO_THEME_COLOR
        from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
        from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
        from pptx.util import Inches, Pt
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
        raise TemplateError("生成 PPTX 需要 python-pptx，请安装：uv sync --extra ppt") from exc
    return _Api(
        Presentation=Presentation,
        RGBColor=RGBColor,
        MSO_SHAPE=MSO_SHAPE,
        MSO_CONNECTOR=MSO_CONNECTOR,
        MSO_ANCHOR=MSO_ANCHOR,
        MSO_AUTO_SIZE=MSO_AUTO_SIZE,
        MSO_THEME_COLOR=MSO_THEME_COLOR,
        PP_ALIGN=PP_ALIGN,
        Inches=Inches,
        Pt=Pt,
        CategoryChartData=CategoryChartData,
        XL_CHART_TYPE=XL_CHART_TYPE,
        XL_LABEL_POSITION=XL_LABEL_POSITION,
        XL_LEGEND_POSITION=XL_LEGEND_POSITION,
    )


def _emu(value: float, api: _Api) -> Any:
    """设计坐标（96 单位 / 英寸）转 EMU。"""
    return api.Inches(value / _DESIGN_UNITS_PER_INCH)


def _color(value: str, api: _Api) -> Any:
    """颜色字符串转 python-pptx RGBColor。"""
    return api.RGBColor.from_string(normalize_hex(value)[1:])


# 调色板槽位 → python-pptx 主题色枚举名；改主题色时整稿联动。
_THEME_TOKENS: dict[str, str] = {
    "tx1": "TEXT_1",
    "bg1": "BACKGROUND_1",
    "tx2": "TEXT_2",
    "bg2": "BACKGROUND_2",
    "accent1": "ACCENT_1",
    "accent2": "ACCENT_2",
}


def _scheme_map(theme: Theme) -> dict[str, str]:
    """调色板 hex → 主题槽位；不在调色板内的颜色仍写 ``srgbClr``。"""
    palette = theme.palette
    pairs = (
        (palette.text, "tx1"),
        (palette.background, "bg1"),
        (palette.muted, "tx2"),
        (palette.surface, "bg2"),
        (palette.accent, "accent1"),
        (palette.secondary, "accent2"),
    )
    return {normalize_hex(color): token for color, token in pairs}


def _apply_color(color: Any, value: str, scheme: dict[str, str], api: _Api) -> None:
    """优先写主题色，保证 PowerPoint 里换主题后整稿联动。"""
    token = scheme.get(normalize_hex(value))
    if token is None:
        color.rgb = _color(value, api)
        return
    color.theme_color = getattr(api.MSO_THEME_COLOR, _THEME_TOKENS[token])


def _set_fill_and_line(  # noqa: PLR0913, PLR0917 - 形状样式工厂
    shape: Any,
    fill: str,
    line: str,
    line_width: float,
    api: _Api,
    scheme: dict[str, str],
) -> None:
    """设置形状填充与描边；``line`` 为空时去掉描边。"""
    shape.shadow.inherit = False
    if fill:
        shape.fill.solid()
        _apply_color(shape.fill.fore_color, fill, scheme, api)
    else:
        shape.fill.background()
    if line:
        _apply_color(shape.line.color, line, scheme, api)
        shape.line.width = api.Pt(max(line_width * _POINTS_PER_UNIT, 0.75))
    else:
        shape.line.fill.background()


def _set_east_asian_font(run: Any, font_name: str) -> None:
    """同时设置东亚字体，保证中文按模板字体渲染。"""
    try:
        from lxml import etree
        from pptx.oxml.ns import qn
    except ImportError:  # pragma: no cover - lxml 随 python-pptx 安装
        return
    rpr = run._r.get_or_add_rPr()
    element = rpr.find(qn("a:ea"))
    if element is None:
        element = etree.SubElement(rpr, qn("a:ea"))
    element.set("typeface", font_name)
