"""pptmake 设计令牌：字号、间距、圆角、密度、装饰强度、字体回退与语义色。

令牌层把「风格参数」从版式结构中抽出来：版式只按角色取字号与间距，
模板 JSON 的 ``theme.tokens`` 可整体替换，从而在不改结构的前提下调整
字号体系、松紧度与装饰强度。v1 模板在加载时自动补默认令牌。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Final

__all__ = [
    "ASPECT_TYPE_SCALE",
    "CHART_PALETTE_DEFAULT",
    "DEFAULT_FONT_FALLBACK",
    "DENSITIES",
    "DENSITY_COMFORTABLE",
    "DENSITY_COMPACT",
    "DENSITY_SPACIOUS",
    "DENSITY_TEXT",
    "DISPLAY_TYPE_FIELDS",
    "BulletScale",
    "DesignTokens",
    "RadiusScale",
    "SemanticColors",
    "SpacingScale",
    "StrokeScale",
    "TokenError",
    "TypeScale",
    "normalize_density",
    "tokens_from_mapping",
    "tokens_to_mapping",
]

DENSITY_COMPACT: Final[str] = "compact"
DENSITY_COMFORTABLE: Final[str] = "comfortable"
DENSITY_SPACIOUS: Final[str] = "spacious"
DENSITIES: Final[tuple[str, ...]] = (DENSITY_COMPACT, DENSITY_COMFORTABLE, DENSITY_SPACIOUS)
DENSITY_TEXT: Final[dict[str, str]] = {
    DENSITY_COMPACT: "紧凑",
    DENSITY_COMFORTABLE: "舒适",
    DENSITY_SPACIOUS: "宽松",
}
_DENSITY_SCALE: Final[dict[str, float]] = {
    DENSITY_COMPACT: 0.85,
    DENSITY_COMFORTABLE: 1.0,
    DENSITY_SPACIOUS: 1.2,
}

DEFAULT_FONT_FALLBACK: Final[tuple[str, ...]] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
)

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")

# 图表系列色：深色底上对比清晰，且与极光蓝主色不冲突。
CHART_PALETTE_DEFAULT: Final[tuple[str, ...]] = (
    "#5B8CFF",
    "#8B5CF6",
    "#22D3EE",
    "#34D399",
    "#FBBF24",
    "#F472B6",
)

_TYPE_FIELDS: Final[tuple[str, ...]] = (
    "display",
    "title",
    "subtitle",
    "heading",
    "body",
    "small",
    "caption",
    "stat",
    "quote",
    "section_number",
)
_TYPE_LABELS: Final[dict[str, str]] = {
    "display": "封面主标题",
    "title": "页面标题",
    "subtitle": "副标题",
    "heading": "卡片标题",
    "body": "正文",
    "small": "次要正文",
    "caption": "注释",
    "stat": "数据数字",
    "quote": "引用文字",
    "section_number": "章节编号",
}
# 展示级字号随画幅微调；正文与注释保持固定 pt，优先保证可读性。
DISPLAY_TYPE_FIELDS: Final[tuple[str, ...]] = ("display", "title", "quote", "stat", "section_number")
ASPECT_TYPE_SCALE: Final[dict[str, float]] = {"16:9": 1.0, "4:3": 0.92}
_SPACING_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "margin_x": (0.0, 0.4),
    "margin_y": (0.0, 0.4),
    "gap": (0.0, 200.0),
    "block": (0.0, 300.0),
    "section": (0.0, 400.0),
    "line": (0.8, 2.5),
    "line_display": (0.8, 2.5),
    "line_heading": (0.8, 2.5),
    "line_body": (0.8, 2.5),
    "line_caption": (0.8, 2.5),
    "space_after_body": (0.0, 3.0),
    "space_after_item": (0.0, 3.0),
    "space_before_heading": (0.0, 3.0),
}
_SPACING_LABELS: Final[dict[str, str]] = {
    "margin_x": "左右边距比例",
    "margin_y": "上边距比例",
    "gap": "基础间距",
    "block": "块间距",
    "section": "区块间距",
    "line": "行距倍数",
    "line_display": "展示级行距",
    "line_heading": "标题行距",
    "line_body": "正文行距",
    "line_caption": "注释行距",
    "space_after_body": "正文段后距（×正文）",
    "space_after_item": "条目段后距（×正文）",
    "space_before_heading": "标题段前距（×正文）",
}
_RADIUS_LABELS: Final[dict[str, str]] = {
    "card": "卡片圆角",
    "small": "小圆角",
}
_SEMANTIC_FIELDS: Final[tuple[str, ...]] = ("success", "warning", "danger", "info")
_SEMANTIC_LABELS: Final[dict[str, str]] = {
    "success": "成功",
    "warning": "警告",
    "danger": "危险",
    "info": "提示",
}


class TokenError(ValueError):
    """设计令牌缺失或非法；由模板加载层转换成 :class:`TemplateError`。"""


@dataclass(frozen=True, slots=True)
class TypeScale:
    """字号层级（设计坐标单位，96 单位 / 英寸；24 单位 = 18pt）。

    正文 24（18pt）与注释 16（12pt）是专业演示的常见下限；展示级字号
    通过 :func:`TypeScale.for_aspect` 按画幅微调，正文保持 pt 不变。
    """

    display: float = 64.0
    title: float = 48.0
    subtitle: float = 28.0
    heading: float = 32.0
    body: float = 24.0
    small: float = 20.0
    caption: float = 16.0
    stat: float = 56.0
    quote: float = 44.0
    section_number: float = 168.0

    def for_aspect(self, aspect: str) -> TypeScale:
        """按画幅缩放展示级字号；未知画幅保持原值。"""
        factor = ASPECT_TYPE_SCALE.get(aspect, 1.0)
        if factor == 1.0:
            return self
        values = {name: round(getattr(self, name) * factor, 1) for name in DISPLAY_TYPE_FIELDS}
        return replace(self, **values)


@dataclass(frozen=True, slots=True)
class SpacingScale:
    """间距体系：边距按画幅比例，其余按设计单位；行距与段距按角色区分。"""

    margin_x: float = 0.075
    margin_y: float = 0.10
    gap: float = 24.0
    block: float = 32.0
    section: float = 48.0
    line: float = 1.45
    line_display: float = 1.1
    line_heading: float = 1.25
    line_body: float = 1.45
    line_caption: float = 1.35
    space_after_body: float = 0.6
    space_after_item: float = 0.4
    space_before_heading: float = 1.0

    def scaled(self, factor: float) -> SpacingScale:
        """按密度系数缩放间距；边距比例、行距与段距系数保持不变。"""
        return replace(
            self,
            gap=round(self.gap * factor, 1),
            block=round(self.block * factor, 1),
            section=round(self.section * factor, 1),
        )


@dataclass(frozen=True, slots=True)
class RadiusScale:
    """圆角体系。"""

    card: float = 18.0
    small: float = 6.0


@dataclass(frozen=True, slots=True)
class StrokeScale:
    """描边与分隔线：颜色留空时按背景与次要色自动混合，宽度统一取本令牌。"""

    border: str = ""
    divider: str = ""
    width: float = 1.0


@dataclass(frozen=True, slots=True)
class BulletScale:
    """项目符号：缩进按设计单位逐级递增，``size`` 为方标边长，``char`` 供导出使用。"""

    indent: float = 24.0
    size: float = 12.0
    char: str = "•"


@dataclass(frozen=True, slots=True)
class SemanticColors:
    """语义色：用于状态、风险与图表标注，不参与主色板。"""

    success: str = "#3FB950"
    warning: str = "#D29922"
    danger: str = "#F85149"
    info: str = "#58A6FF"


@dataclass(frozen=True, slots=True)
class DesignTokens:
    """完整设计令牌；默认值即「舒适」密度下的现代 16:9 口径。"""

    type_scale: TypeScale = field(default_factory=TypeScale)
    spacing: SpacingScale = field(default_factory=SpacingScale)
    radius: RadiusScale = field(default_factory=RadiusScale)
    stroke: StrokeScale = field(default_factory=StrokeScale)
    bullet: BulletScale = field(default_factory=BulletScale)
    density: str = DENSITY_COMFORTABLE
    decor_intensity: float = 1.0
    image_scrim: float = 0.55
    font_fallback: tuple[str, ...] = DEFAULT_FONT_FALLBACK
    chart_palette: tuple[str, ...] = CHART_PALETTE_DEFAULT
    semantic: SemanticColors = field(default_factory=SemanticColors)

    @property
    def density_scale(self) -> float:
        """密度对应的间距系数；未知密度按舒适处理。"""
        return _DENSITY_SCALE.get(self.density, 1.0)


# ---------------------------------------------------------------------- #
# 校验与解析
# ---------------------------------------------------------------------- #
def normalize_density(value: object) -> str:
    """校验密度名；未知值抛出 :class:`TokenError`。"""
    text = str(value).strip().lower()
    if text in DENSITIES:
        return text
    raise TokenError(f"密度需为 {' / '.join(DENSITIES)}：{value!r}")


def _section(raw: object, *, path: str) -> dict[str, Any]:
    """把可选的令牌分组转成字典；``None`` 视为空，非对象报错。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TokenError(f"{path} 需为对象")
    return raw


def _number(
    raw: object,
    *,
    path: str,
    minimum: float,
    maximum: float,
    default: float,
) -> float:
    """解析有界数字；``None`` 保留默认值。"""
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TokenError(f"{path} 需为数字：{raw!r}")
    value = float(raw)
    if not minimum <= value <= maximum:
        raise TokenError(f"{path} 需在 {minimum}–{maximum} 之间：{raw!r}")
    return value


def _hex_color(raw: object, *, path: str) -> str:
    """解析 ``#RRGGBB`` / ``RRGGBB`` 颜色。"""
    if not isinstance(raw, str) or _HEX_RE.fullmatch(raw.strip()) is None:
        raise TokenError(f"{path} 需为 #RRGGBB 或 RRGGBB：{raw!r}")
    return f"#{raw.strip().lstrip('#').upper()}"


def _type_scale(raw: object, *, base: TypeScale) -> TypeScale:
    """解析字号层级；缺失字段沿用 ``base``。"""
    section = _section(raw, path="theme.tokens.type_scale")
    values = {
        name: _number(
            section.get(name),
            path=f"theme.tokens.type_scale.{_TYPE_LABELS[name]}",
            minimum=8.0,
            maximum=300.0,
            default=getattr(base, name),
        )
        for name in _TYPE_FIELDS
    }
    return TypeScale(**values)


def _spacing(raw: object, *, base: SpacingScale) -> SpacingScale:
    """解析间距体系；边距比例限制在 0–0.4，行距限制在 0.8–2.5。"""
    section = _section(raw, path="theme.tokens.spacing")
    values = {
        name: _number(
            section.get(name),
            path=f"theme.tokens.spacing.{_SPACING_LABELS[name]}",
            minimum=bounds[0],
            maximum=bounds[1],
            default=getattr(base, name),
        )
        for name, bounds in _SPACING_BOUNDS.items()
    }
    return SpacingScale(**values)


def _radius(raw: object, *, base: RadiusScale) -> RadiusScale:
    """解析圆角体系。"""
    section = _section(raw, path="theme.tokens.radius")
    values = {
        name: _number(
            section.get(name),
            path=f"theme.tokens.radius.{_RADIUS_LABELS[name]}",
            minimum=0.0,
            maximum=200.0,
            default=getattr(base, name),
        )
        for name in _RADIUS_LABELS
    }
    return RadiusScale(**values)


def _optional_hex(raw: object, *, path: str, default: str) -> str:
    """解析可留空的颜色；``None`` / 空串表示自动混合。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    return _hex_color(raw, path=path)


def _stroke(raw: object, *, base: StrokeScale) -> StrokeScale:
    """解析描边与分隔线令牌。"""
    section = _section(raw, path="theme.tokens.stroke")
    return StrokeScale(
        border=_optional_hex(section.get("border"), path="theme.tokens.stroke.border", default=base.border),
        divider=_optional_hex(section.get("divider"), path="theme.tokens.stroke.divider", default=base.divider),
        width=_number(
            section.get("width"),
            path="theme.tokens.stroke.width",
            minimum=0.0,
            maximum=20.0,
            default=base.width,
        ),
    )


def _bullet(raw: object, *, base: BulletScale) -> BulletScale:
    """解析项目符号令牌；``char`` 只接受 1–2 个字符。"""
    section = _section(raw, path="theme.tokens.bullet")
    char_raw = section.get("char")
    if char_raw is None:
        char = base.char
    elif isinstance(char_raw, str) and 0 < len(char_raw.strip()) <= 2:
        char = char_raw.strip()
    else:
        raise TokenError(f"theme.tokens.bullet.项目符号需为 1–2 个字符：{char_raw!r}")
    return BulletScale(
        indent=_number(
            section.get("indent"),
            path="theme.tokens.bullet.indent",
            minimum=0.0,
            maximum=200.0,
            default=base.indent,
        ),
        size=_number(
            section.get("size"),
            path="theme.tokens.bullet.size",
            minimum=0.0,
            maximum=100.0,
            default=base.size,
        ),
        char=char,
    )


def _font_fallback(raw: object, *, base: tuple[str, ...]) -> tuple[str, ...]:
    """解析字体回退链：非空字符串数组，去重并保持顺序。"""
    if raw is None:
        return base
    if not isinstance(raw, (list, tuple)) or not raw:
        raise TokenError("theme.tokens.font_fallback 需为非空字符串数组")
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise TokenError(f"theme.tokens.font_fallback 含非法字体名：{item!r}")
        name = item.strip()
        if name not in names:
            names.append(name)
    return tuple(names)


def _chart_palette(raw: object, *, base: tuple[str, ...]) -> tuple[str, ...]:
    """解析图表系列色：2–8 个 ``#RRGGBB``；``None`` 沿用 ``base``。"""
    if raw is None:
        return base
    if not isinstance(raw, (list, tuple)) or not 2 <= len(raw) <= 8:
        raise TokenError("theme.tokens.chart_palette 需为 2–8 个颜色")
    return tuple(_hex_color(item, path=f"theme.tokens.chart_palette[{index}]") for index, item in enumerate(raw))


def _semantic(raw: object, *, base: SemanticColors) -> SemanticColors:
    """解析语义色；缺失字段沿用 ``base``。"""
    section = _section(raw, path="theme.tokens.semantic")
    values = {
        name: (
            getattr(base, name)
            if section.get(name) is None
            else _hex_color(section[name], path=f"theme.tokens.semantic.{_SEMANTIC_LABELS[name]}")
        )
        for name in _SEMANTIC_FIELDS
    }
    return SemanticColors(**values)


def tokens_from_mapping(raw: object, *, base: DesignTokens | None = None) -> DesignTokens:
    """把 ``theme.tokens`` 字典解析成令牌；缺失字段沿用 ``base``（默认令牌）。"""
    fallback = base if base is not None else DesignTokens()
    if raw is None:
        return fallback
    if not isinstance(raw, dict):
        raise TokenError("theme.tokens 需为对象")
    density_raw = raw.get("density")
    return DesignTokens(
        type_scale=_type_scale(raw.get("type_scale"), base=fallback.type_scale),
        spacing=_spacing(raw.get("spacing"), base=fallback.spacing),
        radius=_radius(raw.get("radius"), base=fallback.radius),
        stroke=_stroke(raw.get("stroke"), base=fallback.stroke),
        bullet=_bullet(raw.get("bullet"), base=fallback.bullet),
        density=fallback.density if density_raw is None else normalize_density(density_raw),
        decor_intensity=_number(
            raw.get("decor_intensity"),
            path="theme.tokens.装饰强度",
            minimum=0.0,
            maximum=1.0,
            default=fallback.decor_intensity,
        ),
        image_scrim=_number(
            raw.get("image_scrim"),
            path="theme.tokens.图片压暗强度",
            minimum=0.0,
            maximum=0.8,
            default=fallback.image_scrim,
        ),
        font_fallback=_font_fallback(raw.get("font_fallback"), base=fallback.font_fallback),
        chart_palette=_chart_palette(raw.get("chart_palette"), base=fallback.chart_palette),
        semantic=_semantic(raw.get("semantic"), base=fallback.semantic),
    )


def tokens_to_mapping(tokens: DesignTokens) -> dict[str, Any]:
    """把令牌转成可写入 JSON 的字典（字段顺序稳定）。"""
    return {
        "density": tokens.density,
        "decor_intensity": tokens.decor_intensity,
        "image_scrim": tokens.image_scrim,
        "type_scale": {name: getattr(tokens.type_scale, name) for name in _TYPE_FIELDS},
        "spacing": {name: getattr(tokens.spacing, name) for name in _SPACING_BOUNDS},
        "radius": {name: getattr(tokens.radius, name) for name in _RADIUS_LABELS},
        "stroke": {
            "border": tokens.stroke.border,
            "divider": tokens.stroke.divider,
            "width": tokens.stroke.width,
        },
        "bullet": {
            "indent": tokens.bullet.indent,
            "size": tokens.bullet.size,
            "char": tokens.bullet.char,
        },
        "font_fallback": list(tokens.font_fallback),
        "chart_palette": list(tokens.chart_palette),
        "semantic": {name: getattr(tokens.semantic, name) for name in _SEMANTIC_FIELDS},
    }
