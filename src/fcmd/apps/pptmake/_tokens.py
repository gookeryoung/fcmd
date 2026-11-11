"""pptmake 设计令牌：字号、间距、圆角、密度、装饰强度、字体回退与语义色。

令牌层把「风格参数」从版式结构中抽出来：版式只按角色取字号与间距，
模板 JSON 的 ``theme.tokens`` 可整体替换，从而在不改结构的前提下调整
字号体系、松紧度与装饰强度。v1 模板在加载时自动补默认令牌。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "DEFAULT_FONT_FALLBACK",
    "DENSITIES",
    "DENSITY_COMFORTABLE",
    "DENSITY_COMPACT",
    "DENSITY_SPACIOUS",
    "DENSITY_TEXT",
    "DesignTokens",
    "RadiusScale",
    "SemanticColors",
    "SpacingScale",
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
_SPACING_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "margin_x": (0.0, 0.4),
    "margin_y": (0.0, 0.4),
    "gap": (0.0, 200.0),
    "block": (0.0, 300.0),
    "section": (0.0, 400.0),
    "line": (0.8, 2.5),
}
_SPACING_LABELS: Final[dict[str, str]] = {
    "margin_x": "左右边距比例",
    "margin_y": "上边距比例",
    "gap": "基础间距",
    "block": "块间距",
    "section": "区块间距",
    "line": "行距倍数",
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
    """字号层级（设计坐标单位，96 单位 / 英寸；20 单位 = 15pt）。"""

    display: float = 58.0
    title: float = 40.0
    subtitle: float = 18.0
    heading: float = 22.0
    body: float = 20.0
    small: float = 16.0
    caption: float = 13.0
    stat: float = 44.0
    quote: float = 34.0
    section_number: float = 124.0


@dataclass(frozen=True, slots=True)
class SpacingScale:
    """间距体系：边距按画幅比例，其余按设计单位。"""

    margin_x: float = 0.075
    margin_y: float = 0.10
    gap: float = 24.0
    block: float = 32.0
    section: float = 48.0
    line: float = 1.25


@dataclass(frozen=True, slots=True)
class RadiusScale:
    """圆角体系。"""

    card: float = 18.0
    small: float = 6.0


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
    density: str = DENSITY_COMFORTABLE
    decor_intensity: float = 1.0
    font_fallback: tuple[str, ...] = DEFAULT_FONT_FALLBACK
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
        density=fallback.density if density_raw is None else normalize_density(density_raw),
        decor_intensity=_number(
            raw.get("decor_intensity"),
            path="theme.tokens.装饰强度",
            minimum=0.0,
            maximum=1.0,
            default=fallback.decor_intensity,
        ),
        font_fallback=_font_fallback(raw.get("font_fallback"), base=fallback.font_fallback),
        semantic=_semantic(raw.get("semantic"), base=fallback.semantic),
    )


def tokens_to_mapping(tokens: DesignTokens) -> dict[str, Any]:
    """把令牌转成可写入 JSON 的字典（字段顺序稳定）。"""
    return {
        "density": tokens.density,
        "decor_intensity": tokens.decor_intensity,
        "type_scale": {name: getattr(tokens.type_scale, name) for name in _TYPE_FIELDS},
        "spacing": {name: getattr(tokens.spacing, name) for name in _SPACING_BOUNDS},
        "radius": {name: getattr(tokens.radius, name) for name in _RADIUS_LABELS},
        "font_fallback": list(tokens.font_fallback),
        "semantic": {name: getattr(tokens.semantic, name) for name in _SEMANTIC_FIELDS},
    }
