"""pptmake 版式引擎：把模板页转换成与后端无关的绘制指令。

设计坐标固定为 **96 单位 / 英寸**（16:9 为 1280×720，4:3 为 960×720），
因此同一份绘制列表既能按比例投到 Tk Canvas 预览，也能用
:mod:`fcmd.apps.pptmake._pptx` 换算成英寸写入 PowerPoint。所有版式都只由
矩形、椭圆、直线与文本框四种图元组成，纯逻辑、无 GUI / 第三方依赖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fcmd.apps.pptmake._core import (
    ASPECT_DESIGN,
    DECOR_FRAME,
    DECOR_GRID,
    DECOR_MINIMAL,
    LAYOUT_AGENDA,
    LAYOUT_BULLETS,
    LAYOUT_CHART,
    LAYOUT_CLOSING,
    LAYOUT_COMPARISON,
    LAYOUT_CONTACT,
    LAYOUT_COVER,
    LAYOUT_IMAGE,
    LAYOUT_METRICS,
    LAYOUT_QUOTE,
    LAYOUT_SECTION,
    LAYOUT_TEXT,
    LAYOUT_TIMELINE,
    LAYOUT_TWO_COLUMN,
    DesignTokens,
    Palette,
    SlideSpec,
    TemplateSpec,
    Theme,
    mix_hex,
    normalize_hex,
)
from fcmd.apps.pptmake._tokens import RadiusScale, SpacingScale, TypeScale

__all__ = [
    "Line",
    "Oval",
    "Primitive",
    "Rect",
    "Text",
    "build_draw_list",
    "design_size",
]

ALIGN_LEFT = "left"
ALIGN_CENTER = "center"
ALIGN_RIGHT = "right"
ANCHOR_TOP = "top"
ANCHOR_MIDDLE = "middle"
ANCHOR_BOTTOM = "bottom"


@dataclass(frozen=True, slots=True)
class Rect:
    """矩形图元；``radius`` > 0 时渲染为圆角矩形。"""

    x: float
    y: float
    w: float
    h: float
    fill: str = ""
    line: str = ""
    line_width: float = 0.0
    radius: float = 0.0


@dataclass(frozen=True, slots=True)
class Oval:
    """椭圆图元。"""

    x: float
    y: float
    w: float
    h: float
    fill: str = ""
    line: str = ""
    line_width: float = 0.0


@dataclass(frozen=True, slots=True)
class Line:
    """直线图元。"""

    x1: float
    y1: float
    x2: float
    y2: float
    color: str
    width: float = 1.0


@dataclass(frozen=True, slots=True)
class Text:
    """文本框图元；``paragraphs`` 的每一项是一段。"""

    x: float
    y: float
    w: float
    h: float
    paragraphs: tuple[str, ...]
    size: float
    color: str
    font: str = ""
    bold: bool = False
    italic: bool = False
    align: str = ALIGN_LEFT
    anchor: str = ANCHOR_TOP
    line_spacing: float = 1.2
    space_after: float = 0.0


Primitive = Rect | Oval | Line | Text


@dataclass(frozen=True, slots=True)
class _Ctx:
    """单页绘制上下文。"""

    width: float
    height: float
    palette: Palette
    theme: Theme
    accent: str

    @property
    def tokens(self) -> DesignTokens:
        """本模板的设计令牌。"""
        return self.theme.tokens

    @property
    def type_scale(self) -> TypeScale:
        """字号层级。"""
        return self.tokens.type_scale

    @property
    def spacing(self) -> SpacingScale:
        """按密度系数缩放后的间距。"""
        return self.tokens.spacing.scaled(self.tokens.density_scale)

    @property
    def radius(self) -> RadiusScale:
        """圆角体系。"""
        return self.tokens.radius

    @property
    def margin(self) -> float:
        """左右页边距。"""
        return round(self.width * self.spacing.margin_x)

    @property
    def top(self) -> float:
        """内容上边距。"""
        return round(self.height * self.spacing.margin_y)


def design_size(aspect: str) -> tuple[int, int]:
    """返回画幅对应的设计坐标尺寸。"""
    return ASPECT_DESIGN.get(aspect, ASPECT_DESIGN["16:9"])


def _text(  # noqa: PLR0913, PLR0917 - 图元工厂，参数与 Text 字段一一对应
    x: float,
    y: float,
    w: float,
    h: float,
    content: str,
    size: float,
    color: str,
    font: str,
    *,
    bold: bool = False,
    italic: bool = False,
    align: str = ALIGN_LEFT,
    anchor: str = ANCHOR_TOP,
    line_spacing: float = 1.2,
) -> Text:
    """创建文本框；``content`` 中的换行拆成多段。"""
    paragraphs = tuple(content.split("\n")) if content else ("",)
    return Text(
        x=x,
        y=y,
        w=w,
        h=h,
        paragraphs=paragraphs,
        size=size,
        color=color,
        font=font,
        bold=bold,
        italic=italic,
        align=align,
        anchor=anchor,
        line_spacing=line_spacing,
    )


def _slide_accent(slide: SlideSpec, palette: Palette) -> str:
    """本页强调色：合法覆盖色优先，否则用模板强调色。"""
    if slide.accent:
        try:
            return normalize_hex(slide.accent)
        except ValueError:
            return palette.accent
    return palette.accent


def _parse_pair(text: str) -> tuple[str, str]:
    """把 ``值|说明`` 拆成两段；没有分隔符时说明为空。"""
    left, sep, right = text.partition("|")
    left = left.strip()
    right = right.strip()
    if not sep:
        return left, ""
    return (left or "—"), right


def _split_parts(text: str) -> tuple[str, ...]:
    """按 ``|`` 拆成多段并保留空段，便于表格类版式对齐。"""
    return tuple(part.strip() for part in text.split("|"))


def _parse_number(text: str) -> float:
    """从 ``42`` / ``+12%`` / ``3.5 万`` 中提取数值，失败返回 0。"""
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group(0)) if match else 0.0


# ---------------------------------------------------------------------- #
# 背景与装饰
# ---------------------------------------------------------------------- #
def _paint_decor(out: list[Primitive], ctx: _Ctx) -> None:
    """按模板装饰样式追加背景装饰图元。"""
    palette = ctx.palette
    width = ctx.width
    height = ctx.height
    decor = ctx.theme.decor
    intensity = ctx.tokens.decor_intensity
    if decor == DECOR_GRID:
        step_x = width / 16
        step_y = height / 9
        color = mix_hex(palette.background, palette.muted, 0.16 * intensity)
        for index in range(1, 16):
            x = step_x * index
            out.append(Line(x, 0, x, height, color, 1))
        for index in range(1, 9):
            y = step_y * index
            out.append(Line(0, y, width, y, color, 1))
        return
    if decor == DECOR_FRAME:
        inset = round(min(width, height) * 0.035)
        out.append(
            Rect(
                inset,
                inset,
                width - inset * 2,
                height - inset * 2,
                fill="",
                line=mix_hex(palette.background, palette.accent, 0.65 * intensity),
                line_width=2,
                radius=round(ctx.radius.small),
            )
        )
        out.append(Rect(inset, inset, 64, 8, fill=ctx.accent, radius=4))
        out.append(Rect(width - inset - 64, height - inset - 8, 64, 8, fill=ctx.accent, radius=4))
        return
    if decor == DECOR_MINIMAL:
        out.append(Rect(0, 0, width, round(ctx.radius.small * intensity), fill=ctx.accent))
        return
    # 默认光斑：两枚低对比圆形 + 一条斜线，深色底上最明显，浅色底也会淡出。
    out.append(
        Oval(
            width * 0.70,
            -height * 0.20,
            width * 0.46,
            width * 0.46,
            fill=mix_hex(palette.background, palette.accent, 0.18 * intensity),
        )
    )
    out.append(
        Oval(
            -width * 0.14,
            height * 0.68,
            width * 0.38,
            width * 0.38,
            fill=mix_hex(palette.background, palette.secondary, 0.15 * intensity),
        )
    )
    out.append(
        Line(
            width * 0.58,
            height * 0.12,
            width * 0.92,
            height * 0.02,
            mix_hex(palette.background, palette.accent, 0.45 * intensity),
            2,
        )
    )


def _paint_heading(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> float:
    """绘制标准页标题与下划线，返回正文起始 y。"""
    palette = ctx.palette
    scale = ctx.type_scale
    title = slide.title.strip() or LAYOUT_TEXT.get(slide.layout, "页面标题")
    out.append(
        _text(
            ctx.margin,
            ctx.top,
            ctx.width - ctx.margin * 2,
            round(scale.title * 1.45),
            title,
            scale.title,
            palette.text,
            ctx.theme.heading_font,
            bold=True,
        )
    )
    out.append(
        Rect(
            ctx.margin,
            ctx.top + round(scale.title * 1.65),
            round(scale.title * 1.4),
            round(scale.title * 0.15),
            fill=ctx.accent,
            radius=round(ctx.radius.small / 2),
        )
    )
    out.append(
        _text(
            ctx.margin,
            ctx.top + round(scale.title * 2.1),
            ctx.width - ctx.margin * 2,
            round(scale.subtitle * 1.45),
            slide.subtitle if slide.layout not in (LAYOUT_SECTION, LAYOUT_TWO_COLUMN) else "",
            scale.subtitle,
            palette.muted,
            ctx.theme.body_font,
        )
    )
    return ctx.top + round(scale.title * 3.25)


# ---------------------------------------------------------------------- #
# 版式
# ---------------------------------------------------------------------- #
def _render_cover(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """封面：品牌标签、主标题、副标题与页脚。"""
    palette = ctx.palette
    scale = ctx.type_scale
    margin = ctx.margin
    title = slide.title.strip() or "在此输入演示标题"
    subtitle = slide.subtitle.strip() or "副标题 / 团队 / 日期"
    out.append(
        _text(
            margin,
            ctx.height * 0.20,
            ctx.width * 0.6,
            round(scale.small * 2.5),
            "PPT TEMPLATE",
            scale.small,
            ctx.accent,
            ctx.theme.heading_font,
            bold=True,
        )
    )
    out.append(Rect(margin, ctx.height * 0.29, 76, 8, fill=ctx.accent, radius=round(ctx.radius.small / 1.5)))
    size = scale.display if len(title) <= 22 else round(scale.display * 0.79)
    out.append(
        _text(
            margin,
            ctx.height * 0.34,
            ctx.width - margin * 2.6,
            ctx.height * 0.24,
            title,
            size,
            palette.text,
            ctx.theme.heading_font,
            bold=True,
            line_spacing=1.1,
        )
    )
    out.append(
        _text(
            margin,
            ctx.height * 0.61,
            ctx.width - margin * 2.2,
            ctx.height * 0.12,
            subtitle,
            scale.heading,
            palette.muted,
            ctx.theme.body_font,
        )
    )
    line_color = mix_hex(palette.background, palette.muted, 0.32)
    out.append(Rect(margin, ctx.height - 96, ctx.width - margin * 2, 1, fill=line_color))
    out.append(
        _text(
            margin,
            ctx.height - 76,
            ctx.width * 0.5,
            24,
            slide.note.strip() or "PPTMAKE · 模板",
            scale.caption,
            palette.muted,
            ctx.theme.body_font,
        )
    )
    out.append(
        _text(
            ctx.width - margin - 220,
            ctx.height - 76,
            220,
            24,
            "DESIGNED WITH PPTMAKE",
            scale.caption,
            palette.muted,
            ctx.theme.body_font,
            align=ALIGN_RIGHT,
        )
    )


def _render_agenda(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """目录：单列或双列编号列表。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    top = _paint_heading(out, ctx, slide)
    items = slide.bullets or ("项目背景与目标", "现状与挑战", "方案与亮点", "计划与下一步")
    columns = 2 if len(items) > 4 else 1
    per_column = (len(items) + columns - 1) // columns
    gap = spacing.gap
    column_width = (ctx.width - ctx.margin * 2 - gap * (columns - 1)) / columns
    marker = round(scale.body * 1.5)
    for index, item in enumerate(items):
        column = index // per_column
        row = index % per_column
        x = ctx.margin + column * (column_width + gap)
        y = top + round(scale.small) + row * (spacing.section + spacing.gap)
        out.append(Rect(x, y, marker, marker, fill=mix_hex(palette.background, ctx.accent, 0.22), radius=marker / 2))
        out.append(
            _text(
                x,
                y,
                marker,
                marker,
                f"{index + 1:02d}",
                scale.caption,
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )
        out.append(
            _text(
                x + round(marker * 1.47),
                y - 2,
                column_width - round(marker * 1.47),
                round(scale.body * 1.9),
                item,
                scale.body,
                palette.text,
                ctx.theme.body_font,
                anchor=ANCHOR_MIDDLE,
            )
        )


def _render_section(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """章节：超大编号 + 章节标题 + 一句话说明。"""
    palette = ctx.palette
    scale = ctx.type_scale
    match = re.search(r"\d{1,2}", slide.subtitle)
    number = match.group(0) if match else "01"
    out.append(
        _text(
            ctx.margin,
            ctx.height * 0.14,
            ctx.width * 0.5,
            180,
            number,
            scale.section_number,
            mix_hex(palette.background, ctx.accent, 0.45),
            ctx.theme.heading_font,
            bold=True,
        )
    )
    out.append(
        Rect(
            ctx.margin + round(ctx.radius.small),
            ctx.height * 0.47,
            round(ctx.radius.small),
            120,
            fill=ctx.accent,
            radius=round(ctx.radius.small / 2),
        )
    )
    out.append(
        _text(
            ctx.margin + 38,
            ctx.height * 0.45,
            ctx.width - ctx.margin * 2 - 38,
            64,
            slide.title.strip() or "章节标题",
            round(scale.title * 1.1),
            palette.text,
            ctx.theme.heading_font,
            bold=True,
        )
    )
    body = slide.bullets[0] if slide.bullets else "一句话说明本章节要讲什么。"
    out.append(
        _text(
            ctx.margin + 38,
            ctx.height * 0.56,
            ctx.width - ctx.margin * 2 - 38,
            80,
            body,
            scale.body,
            palette.muted,
            ctx.theme.body_font,
            line_spacing=ctx.spacing.line,
        )
    )


def _render_bullets(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """要点：方标 + 单列文本。"""
    palette = ctx.palette
    scale = ctx.type_scale
    top = _paint_heading(out, ctx, slide)
    items = slide.bullets or ("要点一：在此补充内容", "要点二：保持一行一个观点", "要点三：用数据或案例支撑")
    max_line_height = round(scale.body * 3.5)
    available = ctx.height - top - max_line_height
    line_height = min(max_line_height, available / max(len(items), 1))
    marker = round(scale.body * 0.6)
    for index, item in enumerate(items):
        y = top + 8 + index * line_height
        out.append(
            Rect(
                ctx.margin,
                y + line_height / 2 - marker / 2,
                marker,
                marker,
                fill=ctx.accent,
                radius=round(ctx.radius.small / 2),
            )
        )
        out.append(
            _text(
                ctx.margin + round(scale.body * 1.5),
                y,
                ctx.width - ctx.margin * 2 - round(scale.body * 1.5),
                line_height,
                item,
                scale.body if len(item) <= 40 else scale.small,
                palette.text,
                ctx.theme.body_font,
                anchor=ANCHOR_MIDDLE,
                line_spacing=ctx.spacing.line,
            )
        )


def _render_two_column(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """双栏：两张卡片，副标题用 ``|`` 分隔左右栏标题。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    _paint_heading(out, ctx, slide)
    headers = [part.strip() for part in slide.subtitle.split("|")]
    left_header = headers[0] if headers and headers[0] else "核心要点"
    right_header = headers[1] if len(headers) > 1 and headers[1] else "延伸思考"
    items = slide.bullets or ("在此补充左栏内容", "在此补充右栏内容")
    half = (len(items) + 1) // 2
    groups = (items[:half], items[half:] or ("在此补充右栏内容",))
    gap = spacing.gap
    card_width = (ctx.width - ctx.margin * 2 - gap) / 2
    card_top = ctx.top + round(scale.title * 2.95)
    card_height = ctx.height - card_top - round(scale.body * 3.2)
    pad = round(scale.body * 1.4)
    for index, (header, group) in enumerate(zip((left_header, right_header), groups, strict=True)):
        x = ctx.margin + index * (card_width + gap)
        accent = ctx.accent if index == 0 else palette.secondary
        out.append(
            Rect(
                x,
                card_top,
                card_width,
                card_height,
                fill=palette.surface,
                line=mix_hex(palette.background, palette.muted, 0.28),
                line_width=1,
                radius=round(ctx.radius.card),
            )
        )
        out.append(
            Rect(
                x + pad,
                card_top + pad,
                round(scale.heading * 1.8),
                round(scale.body * 0.2),
                fill=accent,
                radius=2,
            )
        )
        out.append(
            _text(
                x + pad,
                card_top + pad + round(scale.heading * 0.9),
                card_width - pad * 2,
                round(scale.heading * 1.75),
                header,
                scale.heading,
                palette.text,
                ctx.theme.heading_font,
                bold=True,
            )
        )
        row_height = min(round(scale.body * 2.9), (card_height - round(scale.title * 2.95)) / max(len(group), 1))
        for row, item in enumerate(group):
            y = card_top + round(scale.title * 2.5) + row * row_height
            out.append(Oval(x + pad + 2, y + row_height / 2 - 4, 8, 8, fill=accent))
            out.append(
                _text(
                    x + pad + round(scale.body * 1.1),
                    y,
                    card_width - pad * 2 - round(scale.body * 1.1),
                    row_height,
                    item,
                    scale.small,
                    palette.text,
                    ctx.theme.body_font,
                    anchor=ANCHOR_MIDDLE,
                    line_spacing=spacing.line,
                )
            )


def _render_quote(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """引用：大引号 + 金句 + 署名。"""
    palette = ctx.palette
    scale = ctx.type_scale
    quote = slide.title.strip() or "真正的设计，是让复杂的事情看起来简单。"
    attribution = slide.subtitle.strip() or "— 佚名"
    out.append(
        _text(
            ctx.margin,
            ctx.height * 0.12,
            180,
            190,
            "“",
            round(scale.display * 2.75),
            mix_hex(palette.background, ctx.accent, 0.55),
            ctx.theme.heading_font,
            bold=True,
        )
    )
    out.append(
        _text(
            ctx.margin + 30,
            ctx.height * 0.30,
            ctx.width - ctx.margin * 2 - 60,
            ctx.height * 0.34,
            quote,
            scale.quote if len(quote) <= 36 else round(scale.quote * 0.8),
            palette.text,
            ctx.theme.heading_font,
            line_spacing=round(ctx.spacing.line + 0.1, 2),
        )
    )
    out.append(Rect(ctx.margin + 30, ctx.height * 0.68, 80, 4, fill=ctx.accent, radius=2))
    out.append(
        _text(
            ctx.margin + 30,
            ctx.height * 0.72,
            ctx.width - ctx.margin * 2 - 60,
            40,
            attribution,
            scale.subtitle,
            palette.muted,
            ctx.theme.body_font,
        )
    )


def _render_metrics(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """数据：三张指标卡，要点写成 ``值|说明``。"""
    palette = ctx.palette
    scale = ctx.type_scale
    _paint_heading(out, ctx, slide)
    raw = slide.bullets or ("98%|交付准时率", "4.9|客户满意度", "120+|覆盖项目")
    items = [_parse_pair(item) for item in raw][:3]
    while len(items) < 3:
        items.append(("—", "待补充"))
    gap = ctx.spacing.gap
    card_width = (ctx.width - ctx.margin * 2 - gap * 2) / 3
    card_top = ctx.height * 0.36
    card_height = ctx.height * 0.32
    for index, (value, label) in enumerate(items):
        x = ctx.margin + index * (card_width + gap)
        accent = (ctx.accent, palette.secondary, palette.accent)[index % 3]
        out.append(
            Rect(
                x,
                card_top,
                card_width,
                card_height,
                fill=palette.surface,
                line=mix_hex(palette.background, palette.muted, 0.24),
                line_width=1,
                radius=round(ctx.radius.card),
            )
        )
        out.append(Rect(x + 26, card_top + 26, 44, 4, fill=accent, radius=2))
        out.append(
            _text(
                x + 26,
                card_top + 54,
                card_width - 52,
                card_height * 0.42,
                value,
                scale.stat,
                accent,
                ctx.theme.heading_font,
                bold=True,
            )
        )
        out.append(
            _text(
                x + 26,
                card_top + card_height - 62,
                card_width - 52,
                36,
                label,
                scale.small,
                palette.muted,
                ctx.theme.body_font,
            )
        )


def _render_timeline(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """时间线：一条横线 + 最多四个节点，要点写成 ``阶段|说明``。"""
    palette = ctx.palette
    scale = ctx.type_scale
    _paint_heading(out, ctx, slide)
    raw = slide.bullets or ("01|调研", "02|设计", "03|实现", "04|交付")
    items = [_parse_pair(item) for item in raw][:4]
    count = max(len(items), 1)
    start = ctx.margin + 50
    end = ctx.width - ctx.margin - 50
    line_y = ctx.height * 0.55
    out.append(Line(start, line_y, end, line_y, mix_hex(palette.background, palette.muted, 0.42), 3))
    step = (end - start) / (count - 1) if count > 1 else 0
    for index, (phase, detail) in enumerate(items):
        x = start + index * step
        out.append(Oval(x - 10, line_y - 10, 20, 20, fill=ctx.accent))
        out.append(Oval(x - 4, line_y - 4, 8, 8, fill=palette.background))
        out.append(
            _text(
                x - 80,
                line_y - 74,
                160,
                36,
                phase,
                scale.small,
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
            )
        )
        out.append(
            _text(
                x - 90,
                line_y + 30,
                180,
                84,
                detail,
                scale.caption,
                palette.muted,
                ctx.theme.body_font,
                align=ALIGN_CENTER,
            )
        )


def _render_closing(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """结尾：居中感谢语 + 副标题。"""
    palette = ctx.palette
    scale = ctx.type_scale
    title = slide.title.strip() or "THANK YOU"
    subtitle = slide.subtitle.strip() or "期待与你一起，把下一次表达做得更好。"
    out.append(
        Oval(
            ctx.width * 0.72,
            -ctx.height * 0.12,
            ctx.width * 0.38,
            ctx.width * 0.38,
            fill=mix_hex(palette.background, ctx.accent, 0.16),
        )
    )
    size = scale.display if len(title) <= 14 else round(scale.display * 0.79)
    out.append(
        _text(
            ctx.margin,
            ctx.height * 0.34,
            ctx.width - ctx.margin * 2,
            ctx.height * 0.2,
            title,
            size,
            palette.text,
            ctx.theme.heading_font,
            bold=True,
            align=ALIGN_CENTER,
            anchor=ANCHOR_MIDDLE,
        )
    )
    out.append(
        Rect(
            ctx.width / 2 - 40,
            ctx.height * 0.55,
            80,
            4,
            fill=ctx.accent,
            radius=2,
        )
    )
    out.append(
        _text(
            ctx.margin,
            ctx.height * 0.60,
            ctx.width - ctx.margin * 2,
            60,
            subtitle,
            scale.body,
            palette.muted,
            ctx.theme.body_font,
            align=ALIGN_CENTER,
        )
    )


def _render_image(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """图片主视觉：左侧文字、右侧大图占位，导出后可替换为真实图片。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    text_width = (ctx.width - margin * 2) * 0.46
    out.append(
        _text(
            margin,
            ctx.top,
            text_width,
            round(scale.small * 2),
            "VISUAL STORY",
            scale.caption,
            ctx.accent,
            ctx.theme.heading_font,
            bold=True,
        )
    )
    title = slide.title.strip() or "让主视觉先说话"
    out.append(
        _text(
            margin,
            ctx.top + round(scale.title * 1.2),
            text_width,
            round(scale.display * 2.4),
            title,
            round(scale.display * 0.82),
            palette.text,
            ctx.theme.heading_font,
            bold=True,
            line_spacing=1.1,
        )
    )
    out.append(
        Rect(
            margin,
            ctx.top + round(scale.display * 2.6),
            round(scale.title * 1.2),
            round(scale.title * 0.12),
            fill=ctx.accent,
            radius=round(ctx.radius.small / 2),
        )
    )
    out.append(
        _text(
            margin,
            ctx.top + round(scale.display * 3.1),
            text_width,
            round(scale.body * 3.2),
            slide.subtitle.strip() or "右侧留出大图区域，文字保持克制。",
            scale.body,
            palette.muted,
            ctx.theme.body_font,
            line_spacing=spacing.line,
        )
    )
    bullets = slide.bullets[:3] or ("主视觉占据右侧近一半版面", "标题与正文保持左对齐", "导出后可替换为真实图片")
    bullet_top = ctx.top + round(scale.display * 4.4)
    for index, item in enumerate(bullets):
        y = bullet_top + index * (spacing.block + spacing.gap)
        out.append(Oval(margin, y + round(scale.small * 0.4), 8, 8, fill=ctx.accent))
        out.append(
            _text(
                margin + round(scale.body * 1.1),
                y,
                text_width - round(scale.body * 1.1),
                round(scale.body * 1.8),
                item,
                scale.small,
                palette.text,
                ctx.theme.body_font,
                line_spacing=spacing.line,
            )
        )
    panel_x = margin + text_width + spacing.section
    panel_y = ctx.top
    panel_width = ctx.width - margin - panel_x
    panel_height = ctx.height - ctx.top - round(scale.body * 3)
    out.append(
        Rect(
            panel_x,
            panel_y,
            panel_width,
            panel_height,
            fill=palette.surface,
            line=mix_hex(palette.background, palette.muted, 0.24),
            line_width=1,
            radius=round(ctx.radius.card),
        )
    )
    out.append(
        Oval(
            panel_x + panel_width * 0.62,
            panel_y + panel_height * 0.18,
            panel_width * 0.16,
            panel_width * 0.16,
            fill=mix_hex(palette.surface, ctx.accent, 0.45),
        )
    )
    horizon = mix_hex(palette.surface, palette.muted, 0.45)
    out.append(
        Line(
            panel_x + panel_width * 0.1,
            panel_y + panel_height * 0.74,
            panel_x + panel_width * 0.42,
            panel_y + panel_height * 0.42,
            horizon,
            3,
        )
    )
    out.append(
        Line(
            panel_x + panel_width * 0.42,
            panel_y + panel_height * 0.42,
            panel_x + panel_width * 0.74,
            panel_y + panel_height * 0.74,
            horizon,
            3,
        )
    )
    out.append(
        Rect(
            panel_x + panel_width * 0.1,
            panel_y + panel_height * 0.74,
            panel_width * 0.8,
            1,
            fill=horizon,
        )
    )
    out.append(
        _text(
            panel_x,
            panel_y + panel_height - round(scale.small * 2.4),
            panel_width,
            round(scale.small * 1.6),
            "图片占位 · 导出后在 PowerPoint 中替换",
            scale.caption,
            palette.muted,
            ctx.theme.body_font,
            align=ALIGN_CENTER,
        )
    )


def _render_comparison(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """对比矩阵：``维度|方案A|方案B``，右列用强调色标出推荐项。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    headers = _split_parts(slide.subtitle) if slide.subtitle.strip() else ()
    left_header = headers[0] if headers and headers[0] else "方案 A"
    right_header = headers[1] if len(headers) > 1 and headers[1] else "方案 B"
    raw = slide.bullets or ("响应速度|小时级|分钟级", "维护成本|较高|更低", "扩展能力|受限于插件|开放接口")
    rows: list[tuple[str, str, str]] = []
    for item in raw[:4]:
        parts = _split_parts(item)
        label = parts[0] or "对比维度"
        left = parts[1] if len(parts) > 1 and parts[1] else "—"
        right = parts[2] if len(parts) > 2 and parts[2] else "—"
        rows.append((label, left, right))
    table_top = top + round(scale.body)
    header_height = round(scale.body * 2.2)
    label_width = (ctx.width - margin * 2) * 0.3
    option_width = (ctx.width - margin * 2 - label_width - spacing.gap * 2) / 2
    left_x = margin + label_width + spacing.gap
    right_x = left_x + option_width + spacing.gap
    out.append(
        Rect(
            margin,
            table_top,
            ctx.width - margin * 2,
            header_height,
            fill=mix_hex(palette.background, ctx.accent, 0.16),
            radius=round(ctx.radius.small),
        )
    )
    out.append(
        _text(
            margin + spacing.gap,
            table_top,
            label_width - spacing.gap,
            header_height,
            "对比维度",
            scale.small,
            palette.muted,
            ctx.theme.body_font,
            anchor=ANCHOR_MIDDLE,
        )
    )
    out.append(
        _text(
            left_x,
            table_top,
            option_width,
            header_height,
            left_header,
            scale.body,
            palette.text,
            ctx.theme.heading_font,
            bold=True,
            align=ALIGN_CENTER,
            anchor=ANCHOR_MIDDLE,
        )
    )
    out.append(
        _text(
            right_x,
            table_top,
            option_width,
            header_height,
            right_header,
            scale.body,
            ctx.accent,
            ctx.theme.heading_font,
            bold=True,
            align=ALIGN_CENTER,
            anchor=ANCHOR_MIDDLE,
        )
    )
    available = ctx.height - table_top - header_height - round(scale.body * 3)
    row_height = min(round(scale.body * 3), available / max(len(rows), 1))
    for index, (label, left, right) in enumerate(rows):
        y = table_top + header_height + index * row_height
        out.append(
            Rect(
                right_x - spacing.gap / 2,
                y + 4,
                option_width + spacing.gap,
                row_height - 8,
                fill=mix_hex(palette.surface, ctx.accent, 0.1),
                radius=round(ctx.radius.small),
            )
        )
        if index:
            out.append(Line(margin, y, ctx.width - margin, y, mix_hex(palette.background, palette.muted, 0.24), 1))
        out.append(
            _text(
                margin + spacing.gap,
                y,
                label_width - spacing.gap,
                row_height,
                label,
                scale.small,
                palette.muted,
                ctx.theme.body_font,
                anchor=ANCHOR_MIDDLE,
            )
        )
        out.append(
            _text(
                left_x,
                y,
                option_width,
                row_height,
                left,
                scale.small,
                palette.text,
                ctx.theme.body_font,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )
        out.append(
            _text(
                right_x,
                y,
                option_width,
                row_height,
                right,
                scale.small,
                ctx.accent,
                ctx.theme.body_font,
                bold=True,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )


def _render_chart(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """图表占位：按 ``标签|数值`` 画柱状图，导出后可替换为真实图表。"""
    palette = ctx.palette
    scale = ctx.type_scale
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    raw = slide.bullets or ("Q1|42", "Q2|68", "Q3|54", "Q4|86")
    points: list[tuple[str, float]] = []
    for item in raw[:5]:
        label, value = _parse_pair(item)
        points.append((label or f"项{len(points) + 1}", _parse_number(value)))
    if not points:
        points = [("A", 1.0)]
    max_value = max((value for _label, value in points), default=0.0) or 1.0
    card_top = top + round(scale.body)
    card_height = ctx.height - card_top - round(scale.body * 4)
    card_width = ctx.width - margin * 2
    out.append(
        Rect(
            margin,
            card_top,
            card_width,
            card_height,
            fill=palette.surface,
            line=mix_hex(palette.background, palette.muted, 0.24),
            line_width=1,
            radius=round(ctx.radius.card),
        )
    )
    plot_x = margin + round(scale.body * 2.2)
    plot_y = card_top + round(scale.body * 1.8)
    plot_width = card_width - round(scale.body * 4.4)
    plot_height = card_height - round(scale.body * 6)
    for step in range(5):
        y = plot_y + plot_height * step / 4
        out.append(Line(plot_x, y, plot_x + plot_width, y, mix_hex(palette.surface, palette.muted, 0.2), 1))
    out.append(
        Line(
            plot_x,
            plot_y,
            plot_x,
            plot_y + plot_height,
            mix_hex(palette.background, palette.muted, 0.4),
            2,
        )
    )
    slot = plot_width / len(points)
    bar_width = slot * 0.44
    for index, (label, value) in enumerate(points):
        bar_height = max(plot_height * value / max_value, 2)
        x = plot_x + slot * index + (slot - bar_width) / 2
        y = plot_y + plot_height - bar_height
        color = ctx.accent if index < len(points) - 1 else palette.secondary
        out.append(Rect(x, y, bar_width, bar_height, fill=color, radius=round(ctx.radius.small / 2)))
        out.append(
            _text(
                x - 20,
                y - round(scale.small * 1.9),
                bar_width + 40,
                round(scale.small * 1.6),
                f"{value:g}",
                scale.caption,
                palette.text,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
            )
        )
        out.append(
            _text(
                x - 20,
                plot_y + plot_height + 8,
                bar_width + 40,
                round(scale.small * 1.6),
                label,
                scale.caption,
                palette.muted,
                ctx.theme.body_font,
                align=ALIGN_CENTER,
            )
        )
    out.append(
        _text(
            margin,
            card_top + card_height - round(scale.body * 1.9),
            card_width,
            round(scale.small * 1.6),
            "图表占位 · 导出后可在 PowerPoint 中替换为真实图表",
            scale.caption,
            palette.muted,
            ctx.theme.body_font,
            align=ALIGN_CENTER,
        )
    )


def _render_contact(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """联系方式：左侧联系信息，右侧二维码占位与页脚。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    title = slide.title.strip() or "让我们保持联系"
    out.append(
        _text(
            margin,
            ctx.height * 0.18,
            ctx.width * 0.5,
            round(scale.display * 1.6),
            title,
            round(scale.display * 0.85),
            palette.text,
            ctx.theme.heading_font,
            bold=True,
            line_spacing=1.15,
        )
    )
    out.append(
        Rect(
            margin,
            ctx.height * 0.18 + round(scale.display * 1.7),
            round(scale.title * 1.2),
            round(scale.title * 0.12),
            fill=ctx.accent,
            radius=round(ctx.radius.small / 2),
        )
    )
    out.append(
        _text(
            margin,
            ctx.height * 0.18 + round(scale.display * 2.1),
            ctx.width * 0.46,
            round(scale.body * 2.4),
            slide.subtitle.strip() or "期待下一次合作，也欢迎随时交流。",
            scale.body,
            palette.muted,
            ctx.theme.body_font,
            line_spacing=spacing.line,
        )
    )
    rows = slide.bullets[:4] or ("邮箱|hello@example.com", "电话|+86 138 0000 0000", "地址|上海市 · 徐汇区")
    start_y = ctx.height * 0.52
    row_step = scale.body * 3
    for index, item in enumerate(rows):
        label, value = _parse_pair(item)
        y = start_y + index * row_step
        out.append(Oval(margin, y + round(scale.small * 0.4), 8, 8, fill=ctx.accent))
        out.append(
            _text(
                margin + round(scale.body * 1.1),
                y,
                ctx.width * 0.4,
                round(scale.caption * 1.8),
                label or "联系方式",
                scale.caption,
                palette.muted,
                ctx.theme.body_font,
            )
        )
        out.append(
            _text(
                margin + round(scale.body * 1.1),
                y + round(scale.caption * 1.8),
                ctx.width * 0.4,
                round(scale.body * 1.8),
                value or "—",
                scale.body,
                palette.text,
                ctx.theme.body_font,
                bold=True,
            )
        )
    qr_side = round(min(ctx.height * 0.3, ctx.width * 0.18))
    qr_x = ctx.width - margin - qr_side
    qr_y = ctx.height * 0.3
    out.append(
        Rect(
            qr_x,
            qr_y,
            qr_side,
            qr_side,
            fill=palette.surface,
            line=mix_hex(palette.background, palette.muted, 0.32),
            line_width=1,
            radius=round(ctx.radius.small),
        )
    )
    for ratio_x, ratio_y in ((0.14, 0.14), (0.62, 0.14), (0.14, 0.62)):
        out.append(
            Rect(
                qr_x + qr_side * ratio_x,
                qr_y + qr_side * ratio_y,
                qr_side * 0.24,
                qr_side * 0.24,
                fill=palette.text,
            )
        )
    out.append(
        _text(
            qr_x,
            qr_y + qr_side + 6,
            qr_side,
            round(scale.caption * 1.6),
            "二维码占位",
            scale.caption,
            palette.muted,
            ctx.theme.body_font,
            align=ALIGN_CENTER,
        )
    )
    footer_y = ctx.height - round(scale.body * 2.8)
    out.append(
        Line(margin, footer_y, ctx.width - margin, footer_y, mix_hex(palette.background, palette.muted, 0.32), 1)
    )
    out.append(
        _text(
            margin,
            footer_y + 8,
            ctx.width - margin * 2,
            round(scale.caption * 1.8),
            "PPTMAKE · 联系方式",
            scale.caption,
            palette.muted,
            ctx.theme.body_font,
        )
    )


_RENDERERS = {
    LAYOUT_COVER: _render_cover,
    LAYOUT_AGENDA: _render_agenda,
    LAYOUT_SECTION: _render_section,
    LAYOUT_BULLETS: _render_bullets,
    LAYOUT_TWO_COLUMN: _render_two_column,
    LAYOUT_QUOTE: _render_quote,
    LAYOUT_METRICS: _render_metrics,
    LAYOUT_TIMELINE: _render_timeline,
    LAYOUT_CLOSING: _render_closing,
    LAYOUT_IMAGE: _render_image,
    LAYOUT_COMPARISON: _render_comparison,
    LAYOUT_CHART: _render_chart,
    LAYOUT_CONTACT: _render_contact,
}


def build_draw_list(
    slide: SlideSpec,
    template: TemplateSpec,
    *,
    width: int | None = None,
    height: int | None = None,
) -> tuple[Primitive, ...]:
    """生成单页绘制指令；未知布局回落到「要点」版式。"""
    default_width, default_height = design_size(template.aspect)
    ctx = _Ctx(
        width=float(width or default_width),
        height=float(height or default_height),
        palette=template.theme.palette,
        theme=template.theme,
        accent=_slide_accent(slide, template.theme.palette),
    )
    out: list[Primitive] = [Rect(0, 0, ctx.width, ctx.height, fill=ctx.palette.background)]
    _paint_decor(out, ctx)
    renderer = _RENDERERS.get(slide.layout, _render_bullets)
    renderer(out, ctx, slide)
    return tuple(out)
