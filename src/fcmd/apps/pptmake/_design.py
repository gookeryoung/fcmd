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
    LAYOUT_CLOSING,
    LAYOUT_COVER,
    LAYOUT_METRICS,
    LAYOUT_QUOTE,
    LAYOUT_SECTION,
    LAYOUT_TEXT,
    LAYOUT_TIMELINE,
    LAYOUT_TWO_COLUMN,
    Palette,
    SlideSpec,
    TemplateSpec,
    Theme,
    mix_hex,
    normalize_hex,
)

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
    def margin(self) -> float:
        """左右页边距。"""
        return round(self.width * 0.075)

    @property
    def top(self) -> float:
        """内容上边距。"""
        return round(self.height * 0.1)


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


# ---------------------------------------------------------------------- #
# 背景与装饰
# ---------------------------------------------------------------------- #
def _paint_decor(out: list[Primitive], ctx: _Ctx) -> None:
    """按模板装饰样式追加背景装饰图元。"""
    palette = ctx.palette
    width = ctx.width
    height = ctx.height
    decor = ctx.theme.decor
    if decor == DECOR_GRID:
        step_x = width / 16
        step_y = height / 9
        color = mix_hex(palette.background, palette.muted, 0.16)
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
                line=mix_hex(palette.background, palette.accent, 0.65),
                line_width=2,
                radius=6,
            )
        )
        out.append(Rect(inset, inset, 64, 8, fill=ctx.accent, radius=4))
        out.append(Rect(width - inset - 64, height - inset - 8, 64, 8, fill=ctx.accent, radius=4))
        return
    if decor == DECOR_MINIMAL:
        out.append(Rect(0, 0, width, 6, fill=ctx.accent))
        return
    # 默认光斑：两枚低对比圆形 + 一条斜线，深色底上最明显，浅色底也会淡出。
    out.append(
        Oval(
            width * 0.70,
            -height * 0.20,
            width * 0.46,
            width * 0.46,
            fill=mix_hex(palette.background, palette.accent, 0.18),
        )
    )
    out.append(
        Oval(
            -width * 0.14,
            height * 0.68,
            width * 0.38,
            width * 0.38,
            fill=mix_hex(palette.background, palette.secondary, 0.15),
        )
    )
    out.append(
        Line(
            width * 0.58,
            height * 0.12,
            width * 0.92,
            height * 0.02,
            mix_hex(palette.background, palette.accent, 0.45),
            2,
        )
    )


def _paint_heading(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> float:
    """绘制标准页标题与下划线，返回正文起始 y。"""
    palette = ctx.palette
    title = slide.title.strip() or LAYOUT_TEXT.get(slide.layout, "页面标题")
    out.append(
        _text(
            ctx.margin,
            ctx.top,
            ctx.width - ctx.margin * 2,
            58,
            title,
            round(ctx.height * 0.055),
            palette.text,
            ctx.theme.heading_font,
            bold=True,
        )
    )
    out.append(Rect(ctx.margin, ctx.top + 66, 56, 6, fill=ctx.accent, radius=3))
    out.append(
        _text(
            ctx.margin,
            ctx.top + 84,
            ctx.width - ctx.margin * 2,
            26,
            slide.subtitle if slide.layout not in (LAYOUT_SECTION, LAYOUT_TWO_COLUMN) else "",
            14,
            palette.muted,
            ctx.theme.body_font,
        )
    )
    return ctx.top + 130


# ---------------------------------------------------------------------- #
# 版式
# ---------------------------------------------------------------------- #
def _render_cover(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """封面：品牌标签、主标题、副标题与页脚。"""
    palette = ctx.palette
    margin = ctx.margin
    title = slide.title.strip() or "在此输入演示标题"
    subtitle = slide.subtitle.strip() or "副标题 / 团队 / 日期"
    out.append(
        _text(
            margin,
            ctx.height * 0.20,
            ctx.width * 0.6,
            40,
            "PPT TEMPLATE",
            18,
            ctx.accent,
            ctx.theme.heading_font,
            bold=True,
        )
    )
    out.append(Rect(margin, ctx.height * 0.29, 76, 8, fill=ctx.accent, radius=4))
    size = 58 if len(title) <= 22 else 46
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
            22,
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
            14,
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
            12,
            palette.muted,
            ctx.theme.body_font,
            align=ALIGN_RIGHT,
        )
    )


def _render_agenda(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """目录：单列或双列编号列表。"""
    palette = ctx.palette
    top = _paint_heading(out, ctx, slide)
    items = slide.bullets or ("项目背景与目标", "现状与挑战", "方案与亮点", "计划与下一步")
    columns = 2 if len(items) > 4 else 1
    per_column = (len(items) + columns - 1) // columns
    gap = 36
    column_width = (ctx.width - ctx.margin * 2 - gap * (columns - 1)) / columns
    for index, item in enumerate(items):
        column = index // per_column
        row = index % per_column
        x = ctx.margin + column * (column_width + gap)
        y = top + 16 + row * 68
        out.append(Rect(x, y, 30, 30, fill=mix_hex(palette.background, ctx.accent, 0.22), radius=15))
        out.append(
            _text(
                x,
                y,
                30,
                30,
                f"{index + 1:02d}",
                12,
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )
        out.append(
            _text(
                x + 44,
                y - 2,
                column_width - 44,
                38,
                item,
                18,
                palette.text,
                ctx.theme.body_font,
                anchor=ANCHOR_MIDDLE,
            )
        )


def _render_section(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """章节：超大编号 + 章节标题 + 一句话说明。"""
    palette = ctx.palette
    match = re.search(r"\d{1,2}", slide.subtitle)
    number = match.group(0) if match else "01"
    out.append(
        _text(
            ctx.margin,
            ctx.height * 0.14,
            ctx.width * 0.5,
            180,
            number,
            124,
            mix_hex(palette.background, ctx.accent, 0.45),
            ctx.theme.heading_font,
            bold=True,
        )
    )
    out.append(Rect(ctx.margin + 6, ctx.height * 0.47, 6, 120, fill=ctx.accent, radius=3))
    out.append(
        _text(
            ctx.margin + 38,
            ctx.height * 0.45,
            ctx.width - ctx.margin * 2 - 38,
            64,
            slide.title.strip() or "章节标题",
            44,
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
            19,
            palette.muted,
            ctx.theme.body_font,
        )
    )


def _render_bullets(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """要点：方标 + 单列文本。"""
    palette = ctx.palette
    top = _paint_heading(out, ctx, slide)
    items = slide.bullets or ("要点一：在此补充内容", "要点二：保持一行一个观点", "要点三：用数据或案例支撑")
    available = ctx.height - top - 70
    line_height = min(70, available / max(len(items), 1))
    for index, item in enumerate(items):
        y = top + 8 + index * line_height
        out.append(Rect(ctx.margin, y + line_height / 2 - 6, 12, 12, fill=ctx.accent, radius=3))
        out.append(
            _text(
                ctx.margin + 30,
                y,
                ctx.width - ctx.margin * 2 - 30,
                line_height,
                item,
                19 if len(item) <= 40 else 16,
                palette.text,
                ctx.theme.body_font,
                anchor=ANCHOR_MIDDLE,
            )
        )


def _render_two_column(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """双栏：两张卡片，副标题用 ``|`` 分隔左右栏标题。"""
    palette = ctx.palette
    _paint_heading(out, ctx, slide)
    headers = [part.strip() for part in slide.subtitle.split("|")]
    left_header = headers[0] if headers and headers[0] else "核心要点"
    right_header = headers[1] if len(headers) > 1 and headers[1] else "延伸思考"
    items = slide.bullets or ("在此补充左栏内容", "在此补充右栏内容")
    half = (len(items) + 1) // 2
    groups = (items[:half], items[half:] or ("在此补充右栏内容",))
    gap = 24
    card_width = (ctx.width - ctx.margin * 2 - gap) / 2
    card_top = ctx.top + 118
    card_height = ctx.height - card_top - 64
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
                radius=18,
            )
        )
        out.append(Rect(x + 28, card_top + 28, 40, 4, fill=accent, radius=2))
        out.append(
            _text(
                x + 28,
                card_top + 48,
                card_width - 56,
                38,
                header,
                22,
                palette.text,
                ctx.theme.heading_font,
                bold=True,
            )
        )
        row_height = min(58, (card_height - 118) / max(len(group), 1))
        for row, item in enumerate(group):
            y = card_top + 100 + row * row_height
            out.append(Oval(x + 30, y + row_height / 2 - 4, 8, 8, fill=accent))
            out.append(
                _text(
                    x + 50,
                    y,
                    card_width - 78,
                    row_height,
                    item,
                    16,
                    palette.text,
                    ctx.theme.body_font,
                    anchor=ANCHOR_MIDDLE,
                )
            )


def _render_quote(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """引用：大引号 + 金句 + 署名。"""
    palette = ctx.palette
    quote = slide.title.strip() or "真正的设计，是让复杂的事情看起来简单。"
    attribution = slide.subtitle.strip() or "— 佚名"
    out.append(
        _text(
            ctx.margin,
            ctx.height * 0.12,
            180,
            190,
            "“",
            160,
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
            34 if len(quote) <= 36 else 27,
            palette.text,
            ctx.theme.heading_font,
            line_spacing=1.35,
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
            18,
            palette.muted,
            ctx.theme.body_font,
        )
    )


def _render_metrics(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """数据：三张指标卡，要点写成 ``值|说明``。"""
    palette = ctx.palette
    _paint_heading(out, ctx, slide)
    raw = slide.bullets or ("98%|交付准时率", "4.9|客户满意度", "120+|覆盖项目")
    items = [_parse_pair(item) for item in raw][:3]
    while len(items) < 3:
        items.append(("—", "待补充"))
    gap = 24
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
                radius=20,
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
                44,
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
                16,
                palette.muted,
                ctx.theme.body_font,
            )
        )


def _render_timeline(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """时间线：一条横线 + 最多四个节点，要点写成 ``阶段|说明``。"""
    palette = ctx.palette
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
                16,
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
                13,
                palette.muted,
                ctx.theme.body_font,
                align=ALIGN_CENTER,
            )
        )


def _render_closing(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """结尾：居中感谢语 + 副标题。"""
    palette = ctx.palette
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
    size = 64 if len(title) <= 14 else 46
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
            19,
            palette.muted,
            ctx.theme.body_font,
            align=ALIGN_CENTER,
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
