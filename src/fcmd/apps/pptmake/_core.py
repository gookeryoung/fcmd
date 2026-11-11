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

from fcmd.apps.pptmake._tokens import DesignTokens, TokenError, tokens_from_mapping, tokens_to_mapping

__all__ = [
    "ASPECTS",
    "ASPECT_4_3",
    "ASPECT_16_9",
    "ASPECT_DESIGN",
    "ASPECT_INCHES",
    "ASPECT_TEXT",
    "DECORS",
    "DECOR_FRAME",
    "DECOR_GRID",
    "DECOR_MINIMAL",
    "DECOR_ORBS",
    "DECOR_TEXT",
    "LAYOUTS",
    "LAYOUT_AGENDA",
    "LAYOUT_BULLETS",
    "LAYOUT_CHART",
    "LAYOUT_CLOSING",
    "LAYOUT_COMPARISON",
    "LAYOUT_CONTACT",
    "LAYOUT_COVER",
    "LAYOUT_IMAGE",
    "LAYOUT_METRICS",
    "LAYOUT_QUOTE",
    "LAYOUT_SECTION",
    "LAYOUT_TEXT",
    "LAYOUT_TIMELINE",
    "LAYOUT_TWO_COLUMN",
    "PALETTE_FIELDS",
    "STYLES",
    "STYLE_AURORA",
    "STYLE_CORAL",
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
    "is_hex_color",
    "layout_label",
    "load_template",
    "mix_hex",
    "new_slide",
    "new_template",
    "normalize_aspect",
    "normalize_decor",
    "normalize_hex",
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

TEMPLATE_VERSION: Final[int] = 2
TEMPLATE_SUFFIX: Final[str] = ".pptmtpl.json"

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
# 内置布局
# ---------------------------------------------------------------------- #
LAYOUT_COVER: Final[str] = "cover"
LAYOUT_AGENDA: Final[str] = "agenda"
LAYOUT_SECTION: Final[str] = "section"
LAYOUT_BULLETS: Final[str] = "bullets"
LAYOUT_TWO_COLUMN: Final[str] = "two_column"
LAYOUT_QUOTE: Final[str] = "quote"
LAYOUT_METRICS: Final[str] = "metrics"
LAYOUT_TIMELINE: Final[str] = "timeline"
LAYOUT_CLOSING: Final[str] = "closing"
LAYOUT_IMAGE: Final[str] = "image"
LAYOUT_COMPARISON: Final[str] = "comparison"
LAYOUT_CHART: Final[str] = "chart"
LAYOUT_CONTACT: Final[str] = "contact"

LAYOUTS: Final[tuple[str, ...]] = (
    LAYOUT_COVER,
    LAYOUT_AGENDA,
    LAYOUT_SECTION,
    LAYOUT_BULLETS,
    LAYOUT_TWO_COLUMN,
    LAYOUT_QUOTE,
    LAYOUT_METRICS,
    LAYOUT_TIMELINE,
    LAYOUT_CLOSING,
    LAYOUT_IMAGE,
    LAYOUT_COMPARISON,
    LAYOUT_CHART,
    LAYOUT_CONTACT,
)
LAYOUT_TEXT: Final[dict[str, str]] = {
    LAYOUT_COVER: "封面",
    LAYOUT_AGENDA: "目录",
    LAYOUT_SECTION: "章节",
    LAYOUT_BULLETS: "要点",
    LAYOUT_TWO_COLUMN: "双栏",
    LAYOUT_QUOTE: "引用",
    LAYOUT_METRICS: "数据",
    LAYOUT_TIMELINE: "时间线",
    LAYOUT_CLOSING: "结尾",
    LAYOUT_IMAGE: "图片主视觉",
    LAYOUT_COMPARISON: "对比矩阵",
    LAYOUT_CHART: "图表",
    LAYOUT_CONTACT: "联系方式",
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
STYLE_AURORA: Final[str] = "aurora"
STYLE_PORCELAIN: Final[str] = "porcelain"
STYLE_GRAPHITE: Final[str] = "graphite"
STYLE_CORAL: Final[str] = "coral"
STYLE_CUSTOM: Final[str] = "custom"
STYLES: Final[tuple[str, ...]] = (STYLE_AURORA, STYLE_PORCELAIN, STYLE_GRAPHITE, STYLE_CORAL)
STYLE_TEXT: Final[dict[str, str]] = {
    STYLE_AURORA: "极光蓝",
    STYLE_PORCELAIN: "素瓷青",
    STYLE_GRAPHITE: "石墨金",
    STYLE_CORAL: "珊瑚暖",
    STYLE_CUSTOM: "自定义",
}

PALETTE_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("background", "背景"),
    ("surface", "卡片"),
    ("accent", "强调"),
    ("secondary", "辅助"),
    ("text", "正文"),
    ("muted", "次要"),
)

DEFAULT_HEADING_FONT: Final[str] = "Microsoft YaHei UI"
DEFAULT_BODY_FONT: Final[str] = "Microsoft YaHei UI"

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


class TemplateError(ValueError):
    """模板读取、校验或渲染失败。"""


@dataclass(frozen=True, slots=True)
class Palette:
    """模板调色板：背景 / 卡片 / 强调 / 辅助 / 正文 / 次要。"""

    background: str = "#0B1020"
    surface: str = "#151C33"
    accent: str = "#5B8CFF"
    secondary: str = "#8B5CF6"
    text: str = "#F4F6FB"
    muted: str = "#9AA4BF"


_PRESETS: Final[dict[str, Palette]] = {
    STYLE_AURORA: Palette(
        background="#0B1020",
        surface="#151C33",
        accent="#5B8CFF",
        secondary="#8B5CF6",
        text="#F4F6FB",
        muted="#9AA4BF",
    ),
    STYLE_PORCELAIN: Palette(
        background="#F7F4EE",
        surface="#FFFFFF",
        accent="#1F8A7A",
        secondary="#D97757",
        text="#1D2A2A",
        muted="#6B7A78",
    ),
    STYLE_GRAPHITE: Palette(
        background="#17181C",
        surface="#22242B",
        accent="#E9B44C",
        secondary="#6C8EBF",
        text="#F2F2F0",
        muted="#9A9CA5",
    ),
    STYLE_CORAL: Palette(
        background="#FFF7F2",
        surface="#FFFFFF",
        accent="#E4572E",
        secondary="#2A6F97",
        text="#2B211D",
        muted="#8A746B",
    ),
}


@dataclass(frozen=True, slots=True)
class Theme:
    """风格：预设名、调色板、字体、装饰样式与设计令牌。"""

    style: str = STYLE_AURORA
    palette: Palette = field(default_factory=lambda: _PRESETS[STYLE_AURORA])
    heading_font: str = DEFAULT_HEADING_FONT
    body_font: str = DEFAULT_BODY_FONT
    decor: str = DECOR_ORBS
    tokens: DesignTokens = field(default_factory=DesignTokens)


@dataclass(frozen=True, slots=True)
class SlideSpec:
    """单页内容；``layout`` 决定绘制方式，``accent`` 可覆盖本页强调色。"""

    layout: str = LAYOUT_BULLETS
    title: str = ""
    subtitle: str = ""
    bullets: tuple[str, ...] = ()
    note: str = ""
    accent: str = ""


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


def normalize_style(value: object) -> str:
    """校验风格名；``custom`` 表示用户自定义配色。"""
    text = str(value).strip().lower()
    if text in STYLES or text == STYLE_CUSTOM:
        return text
    raise TemplateError(f"未知风格 {value!r}，可选：{' / '.join(STYLES)} / {STYLE_CUSTOM}")


def palette_for_style(style: str) -> Palette:
    """返回预设风格的调色板；``custom`` 或无匹配时返回极光蓝。"""
    return _PRESETS.get(style, _PRESETS[STYLE_AURORA])


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
    kind = normalize_layout(layout)
    samples: dict[str, SlideSpec] = {
        LAYOUT_COVER: SlideSpec(
            kind,
            title="让想法拥有舞台",
            subtitle="PPTMAKE 模板设计器 · 2026",
            note="封面：标题 + 副标题，适合品牌发布会与方案汇报。",
        ),
        LAYOUT_AGENDA: SlideSpec(
            kind,
            title="本次分享",
            bullets=("项目背景与目标", "现状与挑战", "方案与亮点", "落地计划"),
        ),
        LAYOUT_SECTION: SlideSpec(
            kind,
            title="从问题出发",
            subtitle="01",
            bullets=("先定义真正要解决的问题，再讨论方案。",),
        ),
        LAYOUT_BULLETS: SlideSpec(
            kind,
            title="三个关键洞察",
            bullets=(
                "用户需要的不是更多功能，而是更短的路径。",
                "设计的价值在于降低理解成本，而不是增加装饰。",
                "可复用的模板，让每一次表达都保持一致的质感。",
            ),
        ),
        LAYOUT_TWO_COLUMN: SlideSpec(
            kind,
            title="对照与取舍",
            subtitle="现状|方案",
            bullets=("信息分散，风格不统一", "模板集中管理，随时复用", "手工排版耗时且易错", "版式引擎自动完成排版"),
        ),
        LAYOUT_QUOTE: SlideSpec(
            kind,
            title="好的设计不是添加，而是删减到刚刚好。",
            subtitle="— 设计原则",
        ),
        LAYOUT_METRICS: SlideSpec(
            kind,
            title="用数据说话",
            bullets=("98%|交付准时率", "4.9|客户满意度", "120+|覆盖项目"),
        ),
        LAYOUT_TIMELINE: SlideSpec(
            kind,
            title="四步交付",
            bullets=("01|调研", "02|设计", "03|实现", "04|交付"),
        ),
        LAYOUT_CLOSING: SlideSpec(
            kind,
            title="THANK YOU",
            subtitle="期待与你一起，把下一次表达做得更好。",
        ),
        LAYOUT_IMAGE: SlideSpec(
            kind,
            title="让主视觉先说话",
            subtitle="右侧留出大图区域，文字保持克制，适合品牌与产品发布。",
            bullets=("主视觉占据右侧约一半版面", "标题与正文左对齐，层级清晰", "导出后可将占位区域替换为真实图片"),
        ),
        LAYOUT_COMPARISON: SlideSpec(
            kind,
            title="两种方案的取舍",
            subtitle="现状|目标",
            bullets=("响应速度|小时级|分钟级", "维护成本|较高|更低", "扩展能力|受限于插件|开放接口"),
        ),
        LAYOUT_CHART: SlideSpec(
            kind,
            title="关键指标走势",
            bullets=("Q1|42", "Q2|68", "Q3|54", "Q4|86"),
        ),
        LAYOUT_CONTACT: SlideSpec(
            kind,
            title="让我们保持联系",
            subtitle="期待下一次合作，也欢迎随时交流。",
            bullets=("邮箱|hello@example.com", "电话|+86 138 0000 0000", "地址|上海市 · 徐汇区"),
        ),
    }
    return samples[kind]


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
        description="由 pptmake 生成的演示模板，覆盖封面、目录、章节、要点、双栏、引用、数据、时间线、结尾、图片、对比、图表与联系十三种版式。",
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
    return SlideSpec(
        layout=layout,
        title=str(raw.get("title", "")),
        subtitle=str(raw.get("subtitle", "")),
        bullets=bullets,
        note=str(raw.get("note", "")),
        accent=accent,
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
# 默认路径与摘要
# ---------------------------------------------------------------------- #
def default_pptx_path(template_path: Path) -> Path:
    """由模板路径推导同名 ``.pptx``（``demo.pptmtpl.json`` → ``demo.pptx``）。"""
    name = template_path.name
    for suffix in (TEMPLATE_SUFFIX, ".json"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    else:
        name = template_path.stem
    return template_path.with_name(f"{name}.pptx")


def default_template_path(pptx_path: Path) -> Path:
    """由 PPTX 路径推导模板文件路径（``demo.pptx`` → ``demo.pptmtpl.json``）。"""
    return pptx_path.with_name(f"{pptx_path.stem}{TEMPLATE_SUFFIX}")


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
