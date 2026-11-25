"""pptmake 模板模型、风格预设与 JSON 持久化（纯逻辑，可单测）。

一个模板由三部分组成：

- **风格**：``Theme``（预设名 + 六个语义色 + 标题/正文字体 + 装饰样式）；
- **布局**：``SlideSpec.layout`` 选用内置版式（封面、目录、章节、要点、
  双栏、引用、数据、时间线、结尾）；
- **内容**：每页标题、副标题、要点列表、备注与可选强调色。

模板 JSON 是唯一无损载体（``*.pptmtpl.json``）；PPTX 渲染与导入在
:mod:`fcmd.apps.pptmake._pptx`，版式绘制在 :mod:`fcmd.apps.pptmake._design`。
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from fcmd.apps.pptmake._charts import (
    CHART_BAR,
    CHART_LINE,
    CHART_PIE,
    CHART_TYPE_TEXT,
    CHART_TYPES,
    chart_options,
)
from fcmd.apps.pptmake._image import (
    IMAGE_FIT_CONTAIN,
    IMAGE_FIT_COVER,
    IMAGE_FIT_TEXT,
    IMAGE_FITS,
    IMAGE_FOCUS_BOTTOM,
    IMAGE_FOCUS_CENTER,
    IMAGE_FOCUS_LEFT,
    IMAGE_FOCUS_RIGHT,
    IMAGE_FOCUS_TEXT,
    IMAGE_FOCUS_TOP,
    IMAGE_FOCUSES,
    IMAGE_RATIO_40_60,
    IMAGE_RATIO_50_50,
    IMAGE_RATIO_FULL,
    IMAGE_RATIO_GRID_2,
    IMAGE_RATIO_GRID_3,
    IMAGE_RATIO_MAX,
    IMAGE_RATIO_MIN,
    IMAGE_RATIO_TEXT,
    IMAGE_RATIOS,
    image_ratio_cells,
    image_ratio_full,
    image_ratio_share,
    normalize_image_fit,
    normalize_image_focus,
    normalize_image_ratio,
    normalize_images,
)
from fcmd.apps.pptmake._layouts import (
    DEFAULT_PAGE_NUMBER_HIDDEN,
    LAYOUT_AGENDA,
    LAYOUT_AGENDA_PAGES,
    LAYOUT_APPENDIX,
    LAYOUT_BULLETS,
    LAYOUT_CARDS,
    LAYOUT_CHART,
    LAYOUT_CHART_CONCLUSION,
    LAYOUT_CLOSING,
    LAYOUT_CODE,
    LAYOUT_COMPARISON,
    LAYOUT_CONTACT,
    LAYOUT_COVER,
    LAYOUT_IMAGE,
    LAYOUT_METRICS,
    LAYOUT_PHOTO_COVER,
    LAYOUT_PHOTO_SECTION,
    LAYOUT_PROBLEM_SOLUTION,
    LAYOUT_PROCESS,
    LAYOUT_QUOTE,
    LAYOUT_SECTION,
    LAYOUT_TEAM,
    LAYOUT_TESTIMONIAL,
    LAYOUT_TEXT,
    LAYOUT_TIMELINE,
    LAYOUT_TWO_COLUMN,
    LAYOUTS,
)
from fcmd.apps.pptmake._palette import (
    DEFAULT_BODY_FONT,
    DEFAULT_HEADING_FONT,
    PALETTE_FIELDS,
    PALETTES,
    STYLE_AURORA,
    STYLE_BUSINESS,
    STYLE_CORAL,
    STYLE_CUSTOM,
    STYLE_DEEPSEA,
    STYLE_GRAPHITE,
    STYLE_PORCELAIN,
    STYLE_TEXT,
    STYLES,
    Palette,
)
from fcmd.apps.pptmake._paths import TEMPLATE_SUFFIX, default_pptx_path, default_template_path
from fcmd.apps.pptmake._tokens import DesignTokens, TokenError, tokens_from_mapping, tokens_to_mapping

__all__ = [
    "ASPECTS",
    "ASPECT_4_3",
    "ASPECT_16_9",
    "ASPECT_DESIGN",
    "ASPECT_INCHES",
    "ASPECT_TEXT",
    "BACKGROUNDS",
    "BACKGROUND_DARK",
    "BACKGROUND_IMAGE",
    "BACKGROUND_INHERIT",
    "BACKGROUND_LIGHT",
    "BACKGROUND_TEXT",
    "CHART_BAR",
    "CHART_LINE",
    "CHART_PIE",
    "CHART_TYPES",
    "CHART_TYPE_TEXT",
    "DECORS",
    "DECOR_FRAME",
    "DECOR_GRID",
    "DECOR_MINIMAL",
    "DECOR_ORBS",
    "DECOR_TEXT",
    "DEFAULT_PAGE_NUMBER_HIDDEN",
    "IMAGE_FITS",
    "IMAGE_FIT_CONTAIN",
    "IMAGE_FIT_COVER",
    "IMAGE_FIT_TEXT",
    "IMAGE_FOCUSES",
    "IMAGE_FOCUS_BOTTOM",
    "IMAGE_FOCUS_CENTER",
    "IMAGE_FOCUS_LEFT",
    "IMAGE_FOCUS_RIGHT",
    "IMAGE_FOCUS_TEXT",
    "IMAGE_FOCUS_TOP",
    "IMAGE_RATIOS",
    "IMAGE_RATIO_40_60",
    "IMAGE_RATIO_50_50",
    "IMAGE_RATIO_FULL",
    "IMAGE_RATIO_GRID_2",
    "IMAGE_RATIO_GRID_3",
    "IMAGE_RATIO_MAX",
    "IMAGE_RATIO_MIN",
    "IMAGE_RATIO_TEXT",
    "LAYOUTS",
    "LAYOUT_AGENDA",
    "LAYOUT_AGENDA_PAGES",
    "LAYOUT_APPENDIX",
    "LAYOUT_BULLETS",
    "LAYOUT_CARDS",
    "LAYOUT_CHART",
    "LAYOUT_CHART_CONCLUSION",
    "LAYOUT_CLOSING",
    "LAYOUT_CODE",
    "LAYOUT_COMPARISON",
    "LAYOUT_CONTACT",
    "LAYOUT_COVER",
    "LAYOUT_IMAGE",
    "LAYOUT_METRICS",
    "LAYOUT_PHOTO_COVER",
    "LAYOUT_PHOTO_SECTION",
    "LAYOUT_PROBLEM_SOLUTION",
    "LAYOUT_PROCESS",
    "LAYOUT_QUOTE",
    "LAYOUT_SECTION",
    "LAYOUT_TEAM",
    "LAYOUT_TESTIMONIAL",
    "LAYOUT_TEXT",
    "LAYOUT_TIMELINE",
    "LAYOUT_TWO_COLUMN",
    "PALETTE_FIELDS",
    "STYLES",
    "STYLE_AURORA",
    "STYLE_BUSINESS",
    "STYLE_CORAL",
    "STYLE_DEEPSEA",
    "STYLE_GRAPHITE",
    "STYLE_PORCELAIN",
    "STYLE_TEXT",
    "TEMPLATE_SUFFIX",
    "TEMPLATE_VERSION",
    "DesignTokens",
    "Palette",
    "SlideSpec",
    "TemplateError",
    "TemplateSpec",
    "Theme",
    "default_pptx_path",
    "default_slides",
    "default_template",
    "default_template_path",
    "image_ratio_cells",
    "image_ratio_full",
    "image_ratio_share",
    "is_hex_color",
    "layout_label",
    "load_template",
    "mix_hex",
    "new_slide",
    "new_template",
    "normalize_aspect",
    "normalize_background",
    "normalize_decor",
    "normalize_hex",
    "normalize_image_fit",
    "normalize_image_focus",
    "normalize_image_ratio",
    "normalize_images",
    "normalize_layout",
    "normalize_style",
    "palette_for_style",
    "relative_luminance",
    "save_template",
    "style_label",
    "summary_lines",
    "template_from_mapping",
    "template_to_mapping",
]

TEMPLATE_VERSION: Final[int] = 4

# ---------------------------------------------------------------------- #
# 画幅
# ---------------------------------------------------------------------- #
ASPECT_16_9: Final[str] = "16:9"
ASPECT_4_3: Final[str] = "4:3"
ASPECTS: Final[tuple[str, ...]] = (ASPECT_16_9, ASPECT_4_3)
ASPECT_TEXT: Final[dict[str, str]] = {
    ASPECT_16_9: "16:9 宽屏",
    ASPECT_4_3: "4:3 经典",
}
# 设计坐标固定按 96 单位 / 英寸；4:3 的 960×720 正好等于 10×7.5 英寸。
ASPECT_INCHES: Final[dict[str, tuple[float, float]]] = {
    ASPECT_16_9: (13.333, 7.5),
    ASPECT_4_3: (10.0, 7.5),
}
ASPECT_DESIGN: Final[dict[str, tuple[int, int]]] = {
    ASPECT_16_9: (1280, 720),
    ASPECT_4_3: (960, 720),
}

# ---------------------------------------------------------------------- #
# 装饰样式
# ---------------------------------------------------------------------- #
DECOR_ORBS: Final[str] = "orbs"
DECOR_GRID: Final[str] = "grid"
DECOR_FRAME: Final[str] = "frame"
DECOR_MINIMAL: Final[str] = "minimal"
DECORS: Final[tuple[str, ...]] = (DECOR_ORBS, DECOR_GRID, DECOR_FRAME, DECOR_MINIMAL)
DECOR_TEXT: Final[dict[str, str]] = {
    DECOR_ORBS: "光斑",
    DECOR_GRID: "网格",
    DECOR_FRAME: "边框",
    DECOR_MINIMAL: "极简",
}

# ---------------------------------------------------------------------- #
# 风格预设
# ---------------------------------------------------------------------- #
_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


class TemplateError(ValueError):
    """模板读取、校验或渲染失败。"""


@dataclass(frozen=True, slots=True)
class Theme:
    """风格：预设名、调色板、字体、装饰样式与设计令牌。"""

    style: str = STYLE_AURORA
    palette: Palette = field(default_factory=lambda: PALETTES[STYLE_AURORA])
    heading_font: str = DEFAULT_HEADING_FONT
    body_font: str = DEFAULT_BODY_FONT
    decor: str = DECOR_ORBS
    tokens: DesignTokens = field(default_factory=DesignTokens)
    footer: str = ""
    show_page_number: bool = True
    logo: str = ""
    eyebrow: str = ""
    credit: str = ""
    page_number_hidden_layouts: tuple[str, ...] = DEFAULT_PAGE_NUMBER_HIDDEN


@dataclass(frozen=True, slots=True)
class SlideSpec:
    """单页内容；``layout`` 决定绘制方式，``accent`` 可覆盖本页强调色。"""

    layout: str = LAYOUT_BULLETS
    title: str = ""
    subtitle: str = ""
    bullets: tuple[str, ...] = ()
    note: str = ""
    accent: str = ""
    image: str = ""
    image_fit: str = IMAGE_FIT_COVER
    image_alt: str = ""
    image_caption: str = ""
    image_focus: str = "center"
    images: tuple[str, ...] = ()
    image_ratio: str = ""
    background: str = "inherit"
    chart_type: str = CHART_BAR
    unit: str = ""
    value_format: str = ""
    show_labels: bool = True
    legend: bool = True


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """完整模板。"""

    name: str = "未命名模板"
    description: str = ""
    author: str = ""
    aspect: str = ASPECT_16_9
    theme: Theme = field(default_factory=Theme)
    layouts: tuple[str, ...] = LAYOUTS
    slides: tuple[SlideSpec, ...] = ()
    version: int = TEMPLATE_VERSION


# ---------------------------------------------------------------------- #
# 颜色工具
# ---------------------------------------------------------------------- #
def is_hex_color(value: object) -> bool:
    """判断是否为 ``#RRGGBB`` / ``RRGGBB`` 形式。"""
    return isinstance(value, str) and _HEX_RE.fullmatch(value.strip()) is not None


def normalize_hex(value: object, *, field_name: str = "颜色") -> str:
    """校验并规范化颜色为 ``#RRGGBB``（大写）。"""
    if not is_hex_color(value):
        raise TemplateError(f"{field_name}需为 #RRGGBB 或 RRGGBB：{value!r}")
    text = str(value).strip().lstrip("#").upper()
    return f"#{text}"


def _rgb(value: str) -> tuple[int, int, int]:
    """把颜色转成 RGB 三元组（调用方保证已规范化）。"""
    text = normalize_hex(value)
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


def mix_hex(base: str, other: str, ratio: float) -> str:
    """按 ``ratio``（0–1）把 ``other`` 混入 ``base``，用于近似透明度。"""
    amount = min(max(float(ratio), 0.0), 1.0)
    first = _rgb(base)
    second = _rgb(other)
    mixed = tuple(round(a + (b - a) * amount) for a, b in zip(first, second, strict=True))
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def relative_luminance(color: str) -> float:
    """返回 0–1 相对亮度；用于判断底色是深色还是浅色。"""
    red, green, blue = (channel / 255.0 for channel in _rgb(color))
    channels = [red, green, blue]
    linear = [channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


# ---------------------------------------------------------------------- #
# 枚举归一化
# ---------------------------------------------------------------------- #
def normalize_aspect(value: object) -> str:
    """校验画幅，仅接受 ``16:9`` / ``4:3``。"""
    text = str(value).strip()
    if text in ASPECTS:
        return text
    raise TemplateError(f"画幅需为 {' / '.join(ASPECTS)}：{value!r}")


def normalize_layout(value: object) -> str:
    """校验布局名；未知布局给出可选值列表。"""
    text = str(value).strip()
    if text in LAYOUTS:
        return text
    raise TemplateError(f"未知布局 {value!r}，可选：{' / '.join(LAYOUTS)}")


def normalize_decor(value: object) -> str:
    """校验装饰样式；未知值回落到 ``orbs``。"""
    text = str(value).strip()
    return text if text in DECORS else DECOR_ORBS


BACKGROUND_INHERIT: Final[str] = "inherit"
BACKGROUND_DARK: Final[str] = "dark"
BACKGROUND_LIGHT: Final[str] = "light"
BACKGROUND_IMAGE: Final[str] = "image"
BACKGROUNDS: Final[tuple[str, ...]] = (
    BACKGROUND_INHERIT,
    BACKGROUND_DARK,
    BACKGROUND_LIGHT,
    BACKGROUND_IMAGE,
)
BACKGROUND_TEXT: Final[dict[str, str]] = {
    BACKGROUND_INHERIT: "跟随主题",
    BACKGROUND_DARK: "深色底板",
    BACKGROUND_LIGHT: "浅色底板",
    BACKGROUND_IMAGE: "图片铺底",
}


def normalize_background(value: object) -> str:
    """校验页面底板（inherit / dark / light / image）。"""
    if value is None or value == "":
        return BACKGROUND_INHERIT
    text = str(value).strip().lower()
    if text not in BACKGROUNDS:
        raise ValueError(f"background 需为 {' / '.join(BACKGROUNDS)}：{value!r}")
    return text


def normalize_style(value: object) -> str:
    """校验风格名；``custom`` 表示用户自定义配色。"""
    text = str(value).strip().lower()
    if text in STYLES or text == STYLE_CUSTOM:
        return text
    raise TemplateError(f"未知风格 {value!r}，可选：{' / '.join(STYLES)} / {STYLE_CUSTOM}")


def palette_for_style(style: str) -> Palette:
    """返回预设风格的调色板；``custom`` 或无匹配时返回极光蓝。"""
    return PALETTES.get(style, PALETTES[STYLE_AURORA])


def style_label(style: str) -> str:
    """风格值的界面文案。"""
    return STYLE_TEXT.get(style, style)


def layout_label(layout: str) -> str:
    """布局值的界面文案。"""
    return LAYOUT_TEXT.get(layout, layout)


# ---------------------------------------------------------------------- #
# 构造默认模板
# ---------------------------------------------------------------------- #
def new_slide(layout: str) -> SlideSpec:
    """按布局生成一页带占位内容的示例页。"""
    from fcmd.apps.pptmake._samples import sample_slides

    return sample_slides()[normalize_layout(layout)]


def default_slides() -> tuple[SlideSpec, ...]:
    """返回覆盖全部内置布局的示例页。"""
    return tuple(new_slide(layout) for layout in LAYOUTS)


def new_template(  # noqa: PLR0913 - 模板工厂需要暴露完整风格参数
    *,
    name: str = "未命名模板",
    style: str = STYLE_AURORA,
    aspect: str = ASPECT_16_9,
    decor: str = DECOR_ORBS,
    slides: tuple[SlideSpec, ...] | None = None,
    description: str = "",
    author: str = "",
) -> TemplateSpec:
    """按风格 / 画幅 / 装饰生成模板；``slides`` 为空时使用内置示例页。"""
    normalized_style = normalize_style(style)
    palette = (
        palette_for_style(normalized_style) if normalized_style != STYLE_CUSTOM else palette_for_style(STYLE_AURORA)
    )
    theme = Theme(
        style=normalized_style,
        palette=palette,
        decor=normalize_decor(decor),
    )
    return TemplateSpec(
        name=name.strip() or "未命名模板",
        description=description,
        author=author,
        aspect=normalize_aspect(aspect),
        theme=theme,
        slides=default_slides() if slides is None else slides,
    )


def default_template(*, style: str = STYLE_AURORA, aspect: str = ASPECT_16_9) -> TemplateSpec:
    """生成一份可直接导出 PPTX 的完整示例模板。"""
    return new_template(
        name="品牌演示模板",
        style=style,
        aspect=aspect,
        description=(
            "由 pptmake 生成的演示模板，覆盖 24 种版式（封面、目录、章节、要点、双栏、引用、数据、"
            "时间线、结尾、图片、对比、图表、联系、图片封面、图片章节、卡片、流程、问题与方案、"
            "数据结论、团队、客户证言、代码、附录与目录页码）。"
        ),
        author="pptmake",
    )


# ---------------------------------------------------------------------- #
# JSON 序列化 / 反序列化
# ---------------------------------------------------------------------- #
def template_to_mapping(template: TemplateSpec) -> dict[str, Any]:
    """把模板转成可写入 JSON 的字典（字段顺序稳定）。"""
    palette = template.theme.palette
    return {
        "app": "pptmake",
        "version": TEMPLATE_VERSION,
        "name": template.name,
        "description": template.description,
        "author": template.author,
        "aspect": template.aspect,
        "theme": {
            "style": template.theme.style,
            "heading_font": template.theme.heading_font,
            "body_font": template.theme.body_font,
            "decor": template.theme.decor,
            "palette": {name: getattr(palette, name) for name, _label in PALETTE_FIELDS},
            "tokens": tokens_to_mapping(template.theme.tokens),
            "footer": template.theme.footer,
            "show_page_number": template.theme.show_page_number,
            "logo": template.theme.logo,
            "eyebrow": template.theme.eyebrow,
            "credit": template.theme.credit,
            "page_number_hidden_layouts": list(template.theme.page_number_hidden_layouts),
        },
        "layouts": list(template.layouts),
        "slides": [
            {
                "layout": slide.layout,
                "title": slide.title,
                "subtitle": slide.subtitle,
                "bullets": list(slide.bullets),
                "note": slide.note,
                "accent": slide.accent,
                "image": slide.image,
                "image_fit": slide.image_fit,
                "image_alt": slide.image_alt,
                "image_caption": slide.image_caption,
                "image_focus": slide.image_focus,
                "images": list(slide.images),
                "image_ratio": slide.image_ratio,
                "background": slide.background,
                "chart_type": slide.chart_type,
                "unit": slide.unit,
                "value_format": slide.value_format,
                "show_labels": slide.show_labels,
                "legend": slide.legend,
            }
            for slide in template.slides
        ],
    }


def _palette_from_mapping(raw: object, *, base: Palette, source: str) -> Palette:
    """解析调色板：缺失字段保留 ``base``，非法颜色抛出明确错误。"""
    if raw is None:
        return base
    if not isinstance(raw, dict):
        raise TemplateError(f"{source}.palette 需为对象")
    values: dict[str, str] = {}
    for name, label in PALETTE_FIELDS:
        if name not in raw:
            values[name] = getattr(base, name)
            continue
        values[name] = normalize_hex(raw[name], field_name=f"{source}.palette.{label}")
    return Palette(**values)


def _slide_from_mapping(raw: object, index: int) -> SlideSpec:
    """解析单页；未知字段忽略，非法布局 / 颜色报错。"""
    if not isinstance(raw, dict):
        raise TemplateError(f"slides[{index}] 需为对象")
    layout = normalize_layout(raw.get("layout", LAYOUT_BULLETS))
    bullets_raw = raw.get("bullets", ())
    if bullets_raw is None:
        bullets: tuple[str, ...] = ()
    elif isinstance(bullets_raw, (list, tuple)):
        bullets = tuple(str(item) for item in bullets_raw)
    else:
        raise TemplateError(f"slides[{index}].bullets 需为字符串数组")
    accent_raw = raw.get("accent", "")
    accent = "" if accent_raw in (None, "") else normalize_hex(accent_raw, field_name=f"slides[{index}].accent")
    image_raw = raw.get("image", "")
    image = image_raw.strip() if isinstance(image_raw, str) else ""
    if image_raw not in (None, "") and not isinstance(image_raw, str):
        raise TemplateError(f"slides[{index}].image 需为路径字符串")
    try:
        image_fit = normalize_image_fit(raw.get("image_fit", IMAGE_FIT_COVER))
    except ValueError as exc:
        raise TemplateError(str(exc)) from exc
    alt_raw = raw.get("image_alt", "")
    if alt_raw not in (None, "") and not isinstance(alt_raw, str):
        raise TemplateError(f"slides[{index}].image_alt 需为字符串")
    caption_raw = raw.get("image_caption", "")
    if caption_raw not in (None, "") and not isinstance(caption_raw, str):
        raise TemplateError(f"slides[{index}].image_caption 需为字符串")
    try:
        image_focus = normalize_image_focus(raw.get("image_focus", IMAGE_FOCUS_CENTER))
        image_ratio = normalize_image_ratio(raw.get("image_ratio", ""))
        images = normalize_images(raw.get("images", ()))
        background = normalize_background(raw.get("background", BACKGROUND_INHERIT))
    except ValueError as exc:
        raise TemplateError(str(exc)) from exc
    try:
        charts = chart_options(raw, source=f"slides[{index}]")
    except ValueError as exc:
        raise TemplateError(str(exc)) from exc
    return SlideSpec(
        layout=layout,
        title=str(raw.get("title", "")),
        subtitle=str(raw.get("subtitle", "")),
        bullets=bullets,
        note=str(raw.get("note", "")),
        accent=accent,
        image=image,
        image_fit=image_fit,
        image_alt=str(alt_raw or "").strip(),
        image_caption=str(caption_raw or "").strip(),
        image_focus=image_focus,
        images=images,
        image_ratio=image_ratio,
        background=background,
        chart_type=charts.chart_type,
        unit=charts.unit,
        value_format=charts.value_format,
        show_labels=charts.show_labels,
        legend=charts.legend,
    )


def _template_version(value: object) -> int:
    """解析模板版本；缺失按 v1 处理，高于当前版本时拒绝加载。"""
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int):
        raise TemplateError(f"version 需为整数：{value!r}")
    if value > TEMPLATE_VERSION:
        raise TemplateError(f"模板版本 {value} 高于当前支持的 {TEMPLATE_VERSION}，请升级 pptmake")
    return value


def _theme_tokens(raw: object) -> DesignTokens:
    """解析 ``theme.tokens``；非法令牌转成 :class:`TemplateError`。"""
    try:
        return tokens_from_mapping(raw)
    except TokenError as exc:
        raise TemplateError(str(exc)) from exc


def _hidden_layouts(raw: object) -> tuple[str, ...]:
    """解析页码抑制版式；缺省沿用默认集合，非法布局名报错。"""
    if raw is None:
        return DEFAULT_PAGE_NUMBER_HIDDEN
    if not isinstance(raw, (list, tuple)):
        raise TemplateError("theme.page_number_hidden_layouts 需为布局名数组")
    return tuple(dict.fromkeys(normalize_layout(item) for item in raw))


def template_from_mapping(raw: object) -> TemplateSpec:
    """把 JSON 字典解析成模板；字段缺失用默认值，非法值抛 :class:`TemplateError`。"""
    if not isinstance(raw, dict):
        raise TemplateError("模板根节点需为 JSON 对象")
    version = _template_version(raw.get("version"))
    theme_raw: Any = raw.get("theme", {})
    if theme_raw is None:
        theme_raw = dict[str, Any]()
    if not isinstance(theme_raw, dict):
        raise TemplateError("theme 需为对象")
    # v1 没有令牌层，迁移时统一补默认令牌，避免历史文件里残留的 tokens 影响结果。
    tokens_raw = None if version < 2 else theme_raw.get("tokens")
    tokens = _theme_tokens(tokens_raw)
    style = normalize_style(theme_raw.get("style", STYLE_AURORA))
    palette = _palette_from_mapping(
        theme_raw.get("palette"),
        base=palette_for_style(style),
        source="theme",
    )
    heading_font = str(theme_raw.get("heading_font") or DEFAULT_HEADING_FONT).strip()
    body_font = str(theme_raw.get("body_font") or DEFAULT_BODY_FONT).strip()
    theme = Theme(
        style=style,
        palette=palette,
        heading_font=heading_font or DEFAULT_HEADING_FONT,
        body_font=body_font or DEFAULT_BODY_FONT,
        decor=normalize_decor(theme_raw.get("decor", DECOR_ORBS)),
        tokens=tokens,
        footer=str(theme_raw.get("footer") or ""),
        show_page_number=bool(theme_raw.get("show_page_number", True)),
        logo=str(theme_raw.get("logo") or ""),
        eyebrow=str(theme_raw.get("eyebrow") or ""),
        credit=str(theme_raw.get("credit") or ""),
        page_number_hidden_layouts=_hidden_layouts(theme_raw.get("page_number_hidden_layouts")),
    )

    layouts_raw = raw.get("layouts", LAYOUTS)
    if not isinstance(layouts_raw, (list, tuple)):
        raise TemplateError("layouts 需为布局名数组")
    layouts: list[str] = []
    for item in layouts_raw:
        kind = normalize_layout(item)
        if kind not in layouts:
            layouts.append(kind)
    if not layouts:
        layouts = list(LAYOUTS)

    slides_raw = raw.get("slides", ())
    if slides_raw is None:
        slides_raw = ()
    if not isinstance(slides_raw, (list, tuple)):
        raise TemplateError("slides 需为对象数组")
    slides = tuple(_slide_from_mapping(item, index) for index, item in enumerate(slides_raw))

    return TemplateSpec(
        name=str(raw.get("name") or "未命名模板"),
        description=str(raw.get("description") or ""),
        author=str(raw.get("author") or ""),
        aspect=normalize_aspect(raw.get("aspect", ASPECT_16_9)),
        theme=theme,
        layouts=tuple(layouts),
        slides=slides,
    )


def load_template(path: Path) -> TemplateSpec:
    """读取模板 JSON；文件缺失、JSON 损坏或字段非法时抛 :class:`TemplateError`。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"读取模板失败：{path}：{exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TemplateError(f"模板 JSON 解析失败：{exc}") from exc
    return template_from_mapping(raw)


def save_template(template: TemplateSpec, path: Path) -> None:
    """原子写入模板 JSON（先写临时文件再替换）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(template_to_mapping(template), ensure_ascii=False, indent=2) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise TemplateError(f"保存模板失败：{path}：{exc}") from exc


# ---------------------------------------------------------------------- #
# 摘要
# ---------------------------------------------------------------------- #
def summary_lines(template: TemplateSpec) -> list[str]:
    """模板摘要（CLI ``inspect`` 与 GUI 状态栏共用）。"""
    palette = template.theme.palette
    lines = [
        f"模板：{template.name}",
        f"作者：{template.author or '-'}",
        f"风格：{style_label(template.theme.style)}（{template.theme.style}）",
        f"画幅：{ASPECT_TEXT.get(template.aspect, template.aspect)}",
        f"装饰：{DECOR_TEXT.get(template.theme.decor, template.theme.decor)}",
        f"字体：标题 {template.theme.heading_font} / 正文 {template.theme.body_font}",
        (
            f"配色：背景 {palette.background} · 卡片 {palette.surface} · "
            f"强调 {palette.accent} · 辅助 {palette.secondary}"
        ),
        f"布局：{len(template.layouts)} 种（{' / '.join(layout_label(item) for item in template.layouts)}）",
        f"页面：{len(template.slides)} 页",
    ]
    for index, slide in enumerate(template.slides, start=1):
        title = slide.title.strip() or "（未命名）"
        lines.append(f"  {index:02d}. [{layout_label(slide.layout)}] {title}")
    if template.description:
        lines.append(f"说明：{template.description}")
    return lines


def replace_slide(template: TemplateSpec, index: int, slide: SlideSpec) -> TemplateSpec:
    """替换指定页并返回新模板（越界抛 :class:`IndexError`）。"""
    slides = list(template.slides)
    slides[index] = slide
    return replace(template, slides=tuple(slides))
