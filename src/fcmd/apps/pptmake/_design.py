"""pptmake 版式引擎：把模板页转换成与后端无关的绘制指令。

设计坐标固定为 **96 单位 / 英寸**（16:9 为 1280×720，4:3 为 960×720），
因此同一份绘制列表既能按比例投到 Qt 画布预览，也能用
:mod:`fcmd.apps.pptmake._pptx` 换算成英寸写入 PowerPoint。所有版式都只由
矩形、椭圆、直线与文本框四种图元组成，纯逻辑、无 GUI / 第三方依赖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fcmd.apps.pptmake._bullets import split_bullet
from fcmd.apps.pptmake._core import (
    ASPECT_DESIGN,
    BACKGROUND_DARK,
    BACKGROUND_IMAGE,
    BACKGROUND_LIGHT,
    CHART_PIE,
    DECOR_FRAME,
    DECOR_GRID,
    DECOR_MINIMAL,
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
    "Chart",
    "ChartSeries",
    "Line",
    "Oval",
    "Picture",
    "Primitive",
    "Rect",
    "Table",
    "TableCell",
    "Text",
    "build_draw_list",
    "design_size",
    "picture_crop",
]

ALIGN_LEFT = "left"
ALIGN_CENTER = "center"
ALIGN_RIGHT = "right"
ANCHOR_TOP = "top"
ANCHOR_MIDDLE = "middle"


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
class Picture:
    """图片图元：``path`` 指向本地图片，``fit`` 取 cover / contain。

    ``alt`` 写入替代文本；``caption`` 由版式在图片下方渲染为注释；
    ``focus`` 控制 cover 裁剪的焦点（center/top/bottom/left/right）；
    ``radius`` 为圆角半径；``overlay`` 为压暗强度（0–1），用于图片上的
    文字对比度，``overlay_color`` 默认取页面底色。
    """

    x: float
    y: float
    w: float
    h: float
    path: str
    fit: str = "cover"
    alt: str = ""
    caption: str = ""
    focus: str = "center"
    radius: float = 0.0
    overlay: float = 0.0
    overlay_color: str = "#000000"


def picture_crop(focus: str, image_ratio: float, box_ratio: float) -> tuple[float, float, float, float]:
    """计算 cover 裁剪比例 ``(left, right, top, bottom)``，预览与导出共用。

    图片比容器「更宽」时裁左右，``focus`` 取 left/right 决定保留哪一侧；
    更「更高」时裁上下，取 top/bottom；其余情况按居中裁剪。
    """
    left = right = top = bottom = 0.0
    if image_ratio > box_ratio:
        excess = 1 - box_ratio / image_ratio
        if focus == "left":
            right = excess
        elif focus == "right":
            left = excess
        else:
            left = right = excess / 2
    elif image_ratio < box_ratio:
        excess = 1 - image_ratio / box_ratio
        if focus == "top":
            bottom = excess
        elif focus == "bottom":
            top = excess
        else:
            top = bottom = excess / 2
    return left, right, top, bottom


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
    level: int = 0
    indent: float = 0.0
    field: str = ""
    field_suffix: str = ""


@dataclass(frozen=True, slots=True)
class TableCell:
    """表格单元格：文本、对齐与强调样式。"""

    text: str
    align: str = ALIGN_LEFT
    bold: bool = False
    color: str = ""


@dataclass(frozen=True, slots=True)
class Table:
    """原生表格图元：列宽与行高由版式算好，预览与导出共用同一几何。

    ``w`` / ``h`` 始终等于 ``col_widths`` / ``row_heights`` 之和，导出的
    ``add_table`` 与预览都按这两个数组逐格定位，保证几何差为 0。
    """

    x: float
    y: float
    w: float
    h: float
    col_widths: tuple[float, ...]
    row_heights: tuple[float, ...]
    rows: tuple[tuple[TableCell, ...], ...]
    header: bool = True
    header_fill: str = ""
    band_fill: str = ""
    row_fill: str = ""
    line_color: str = ""
    line_width: float = 1.0
    font: str = ""
    text_color: str = ""
    header_size: float = 16.0
    body_size: float = 16.0
    pad_x: float = 12.0
    pad_y: float = 6.0


@dataclass(frozen=True, slots=True)
class ChartSeries:
    """一个数据系列：名称、数值与系列色。"""

    name: str
    values: tuple[float, ...]
    color: str


@dataclass(frozen=True, slots=True)
class Chart:
    """原生图表图元：预览近似绘制，导出写 ``add_chart``。"""

    x: float
    y: float
    w: float
    h: float
    categories: tuple[str, ...]
    series: tuple[ChartSeries, ...]
    kind: str = "bar"
    unit: str = ""
    value_format: str = ""
    show_labels: bool = True
    legend: bool = True
    colors: tuple[str, ...] = ()
    font: str = ""
    size: float = 16.0
    axis_color: str = ""
    grid_color: str = ""
    text_color: str = ""
    muted_color: str = ""


Primitive = Rect | Oval | Line | Picture | Text | Table | Chart


@dataclass(frozen=True, slots=True)
class _Ctx:
    """单页绘制上下文。"""

    width: float
    height: float
    palette: Palette
    theme: Theme
    accent: str
    aspect: str = "16:9"
    hints: bool = False
    headings: bool = True

    @property
    def tokens(self) -> DesignTokens:
        """本模板的设计令牌。"""
        return self.theme.tokens

    @property
    def type_scale(self) -> TypeScale:
        """字号层级（展示级按画幅微调）。"""
        return self.tokens.type_scale.for_aspect(self.aspect)

    @property
    def spacing(self) -> SpacingScale:
        """按密度系数缩放后的间距。"""
        return self.tokens.spacing.scaled(self.tokens.density_scale)

    @property
    def radius(self) -> RadiusScale:
        """圆角体系。"""
        return self.tokens.radius

    @property
    def border(self) -> str:
        """统一描边色；令牌留空时按背景与次要色混合。"""
        stroke = self.tokens.stroke
        if stroke.border:
            return normalize_hex(stroke.border)
        return mix_hex(self.palette.background, self.palette.muted, 0.28)

    @property
    def divider(self) -> str:
        """统一分隔线色；令牌留空时与描边同口径。"""
        stroke = self.tokens.stroke
        if stroke.divider:
            return normalize_hex(stroke.divider)
        return mix_hex(self.palette.background, self.palette.muted, 0.28)

    @property
    def stroke_width(self) -> float:
        """描边与分隔线的统一线宽。"""
        return self.tokens.stroke.width

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
    space_after: float = 0.0,
    level: int = 0,
    indent: float = 0.0,
    field: str = "",
    field_suffix: str = "",
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
        space_after=space_after,
        level=level,
        indent=indent,
        field=field,
        field_suffix=field_suffix,
    )


def _slide_accent(slide: SlideSpec, palette: Palette) -> str:
    """本页强调色：合法覆盖色优先，否则用模板强调色。"""
    if slide.accent:
        try:
            return normalize_hex(slide.accent)
        except ValueError:
            return palette.accent
    return palette.accent


_LIGHT_BACKGROUND = "#F5F7FA"
_LIGHT_SURFACE = "#FFFFFF"
_LIGHT_TEXT = "#0B1020"
_LIGHT_MUTED = "#5A6473"
_DARK_BACKGROUND = "#0B1020"
_DARK_SURFACE = "#151C33"
_DARK_TEXT = "#F4F6FB"
_DARK_MUTED = "#9AA4BF"


def _page_palette(template: TemplateSpec, slide: SlideSpec) -> Palette:
    """按 ``slide.background`` 覆盖底板与文字色；强调色保持主题口径。"""
    palette = template.theme.palette
    if slide.background == BACKGROUND_LIGHT:
        return Palette(
            background=_LIGHT_BACKGROUND,
            surface=_LIGHT_SURFACE,
            accent=palette.accent,
            secondary=palette.secondary,
            text=_LIGHT_TEXT,
            muted=_LIGHT_MUTED,
        )
    if slide.background in (BACKGROUND_DARK, BACKGROUND_IMAGE):
        return Palette(
            background=_DARK_BACKGROUND,
            surface=_DARK_SURFACE,
            accent=palette.accent,
            secondary=palette.secondary,
            text=_DARK_TEXT,
            muted=_DARK_MUTED,
        )
    return palette


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


_LITERAL_NUMBER = re.compile(r"-?\d+(?:\.\d+)?%?")


def _has_number(text: str) -> bool:
    """判断文本里是否含有数字（用于区分数值列与说明文字）。"""
    return re.search(r"\d", text) is not None


def _format_number(text: str, *, unit: str = "", value_format: str = "") -> str:
    """按 ``value_format``（``0.0`` / ``#,###`` / ``0%``）与单位格式化数值。

    非数值文本原样返回；没有 ``value_format`` 时只补单位，避免改写用户输入。
    """
    raw = text.strip()
    if not raw:
        return "—"
    if not _has_number(raw):
        return raw
    fmt = value_format.strip()
    if not fmt:
        return raw + unit if unit and unit not in raw else raw
    decimals = sum(1 for char in fmt.split(".", 1)[1] if char in "0#") if "." in fmt else 0
    body = format(_parse_number(raw), ("," if "," in fmt else "") + f".{decimals}f")
    if fmt.endswith("%"):
        return body + "%"
    return body + unit if unit and unit not in raw else body


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
    if ctx.headings:
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
                line_spacing=ctx.spacing.line_heading,
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
    if ctx.headings:
        out.append(
            _text(
                ctx.margin,
                ctx.top + round(scale.title * 2.1),
                ctx.width - ctx.margin * 2,
                round(scale.subtitle * 1.45),
                slide.subtitle
                if slide.layout
                not in (LAYOUT_SECTION, LAYOUT_TWO_COLUMN, LAYOUT_PROBLEM_SOLUTION, LAYOUT_CHART_CONCLUSION)
                else "",
                scale.subtitle,
                palette.muted,
                ctx.theme.body_font,
                line_spacing=ctx.spacing.line_heading,
            )
        )
    return ctx.top + round(scale.title * 3.25)


# ---------------------------------------------------------------------- #
# 版式
# ---------------------------------------------------------------------- #
def _render_cover(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """封面：眉标、主标题、副标题与可选页脚；品牌文案全部来自主题字段。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    title = slide.title.strip() or "在此输入演示标题"
    subtitle = slide.subtitle.strip() or "副标题 / 团队 / 日期"
    eyebrow = ctx.theme.eyebrow.strip()
    if eyebrow:
        out.append(
            _text(
                margin,
                ctx.height * 0.20,
                ctx.width * 0.6,
                round(scale.small * 1.8),
                eyebrow,
                scale.small,
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                line_spacing=spacing.line_caption,
            )
        )
    out.append(Rect(margin, ctx.height * 0.29, 76, 8, fill=ctx.accent, radius=round(ctx.radius.small / 1.5)))
    size = scale.display if len(title) <= 22 else round(scale.display * 0.79)
    if ctx.headings:
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
                line_spacing=spacing.line_display,
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
                line_spacing=spacing.line_body,
            )
        )
    footer = ctx.theme.footer.strip()
    credit = ctx.theme.credit.strip()
    if footer or credit:
        out.append(Rect(margin, ctx.height - 96, ctx.width - margin * 2, ctx.stroke_width, fill=ctx.divider))
        if footer:
            out.append(
                _text(
                    margin,
                    ctx.height - 76,
                    ctx.width * 0.5,
                    24,
                    footer,
                    scale.caption,
                    palette.muted,
                    ctx.theme.body_font,
                )
            )
        if credit:
            out.append(
                _text(
                    ctx.width - margin - 220,
                    ctx.height - 76,
                    220,
                    24,
                    credit,
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
            round(scale.section_number * 1.25),
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
    body = slide.bullets[0] if slide.bullets else "一句话说明本章节要讲什么。"
    if ctx.headings:
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
                line_spacing=ctx.spacing.line_body,
            )
        )


def _render_bullets(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """要点：方标 + 单列文本；行距与段距走令牌，条目间距不随条数漂移。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    bullet = ctx.tokens.bullet
    top = _paint_heading(out, ctx, slide)
    items = slide.bullets or ("要点一：在此补充内容", "要点二：保持一行一个观点", "要点三：用数据或案例支撑")
    count = max(len(items), 1)
    line_height = round(scale.body * spacing.line_body)
    item_height = line_height * 2
    gap = round(scale.body * spacing.space_after_item)
    available = ctx.height - top - round(scale.body * 2)
    step = item_height + gap
    if step * count > available:
        step = max(available / count, line_height)
    if step < item_height:
        item_height = round(step)
    base_x = ctx.margin + round(scale.body * 1.5)
    for index, item in enumerate(items):
        level, label = split_bullet(item)
        indent = round(bullet.indent * level)
        marker_size = bullet.size if level == 0 else round(bullet.size * 0.72)
        y = top + round(scale.body * 0.4) + index * step
        out.append(
            Rect(
                ctx.margin + indent,
                y + item_height / 2 - marker_size / 2,
                marker_size,
                marker_size,
                fill=ctx.accent if level == 0 else palette.secondary,
                radius=round(ctx.radius.small / 2),
            )
        )
        out.append(
            _text(
                base_x,
                y,
                ctx.width - ctx.margin * 2 - round(scale.body * 1.5) - indent,
                item_height,
                label or item,
                scale.body if len(label) <= 40 else scale.small,
                palette.text if level == 0 else palette.muted,
                ctx.theme.body_font,
                anchor=ANCHOR_MIDDLE,
                line_spacing=spacing.line_body,
                level=level,
                indent=bullet.indent,
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
                line=ctx.border,
                line_width=ctx.stroke_width,
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
        bullet = ctx.tokens.bullet
        for row, item in enumerate(group):
            level, label = split_bullet(item)
            indent = round(bullet.indent * level)
            y = card_top + round(scale.title * 2.5) + row * row_height
            out.append(Oval(x + pad + indent + 2, y + row_height / 2 - 4, 8, 8, fill=accent))
            out.append(
                _text(
                    x + pad + round(scale.body * 1.1),
                    y,
                    card_width - pad * 2 - round(scale.body * 1.1) - indent,
                    row_height,
                    label or item,
                    scale.small,
                    palette.text if level == 0 else palette.muted,
                    ctx.theme.body_font,
                    anchor=ANCHOR_MIDDLE,
                    line_spacing=spacing.line_body,
                    level=level,
                    indent=bullet.indent,
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
            line_spacing=1.0,
        )
    )
    if ctx.headings:
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
                line_spacing=ctx.spacing.line_body,
            )
        )
    out.append(Rect(ctx.margin + 30, ctx.height * 0.68, 80, 4, fill=ctx.accent, radius=2))
    if ctx.headings:
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
    """数据：三张指标卡，要点写成 ``值|说明|趋势``；趋势按正负取语义色。"""
    palette = ctx.palette
    scale = ctx.type_scale
    semantic = ctx.tokens.semantic
    _paint_heading(out, ctx, slide)
    raw = slide.bullets or ("98%|交付准时率|+3pt", "4.9|客户满意度|+0.2", "120+|覆盖项目")
    items = [_split_parts(item) for item in raw][:3]
    while len(items) < 3:
        items.append(("—", "待补充"))
    gap = ctx.spacing.gap
    card_width = (ctx.width - ctx.margin * 2 - gap * 2) / 3
    card_top = ctx.height * 0.36
    card_height = ctx.height * 0.32
    for index, parts in enumerate(items):
        value = _format_number(parts[0], unit=slide.unit, value_format=slide.value_format)
        label = parts[1] if len(parts) > 1 and parts[1] else "待补充"
        trend = parts[2] if len(parts) > 2 else ""
        x = ctx.margin + index * (card_width + gap)
        accent = (ctx.accent, palette.secondary, palette.accent)[index % 3]
        out.append(
            Rect(
                x,
                card_top,
                card_width,
                card_height,
                fill=palette.surface,
                line=ctx.border,
                line_width=ctx.stroke_width,
                radius=round(ctx.radius.card),
            )
        )
        out.append(Rect(x + 26, card_top + 26, 44, 4, fill=accent, radius=2))
        out.append(
            _text(
                x + 26,
                card_top + 46,
                card_width - 52,
                round(card_height * 0.34),
                value,
                scale.stat,
                accent,
                ctx.theme.heading_font,
                bold=True,
            )
        )
        if trend:
            sign = trend.lstrip()[:1]
            trend_color = semantic.success if sign == "+" else semantic.danger if sign == "-" else palette.muted
            out.append(
                _text(
                    x + 26,
                    card_top + 46 + round(card_height * 0.34),
                    card_width - 52,
                    round(scale.small * 1.4),
                    trend,
                    scale.small,
                    trend_color,
                    ctx.theme.heading_font,
                    bold=True,
                )
            )
        out.append(
            _text(
                x + 26,
                card_top + card_height - 58,
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
    out.append(Line(start, line_y, end, line_y, ctx.divider, 3))
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
    if ctx.headings:
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
    if ctx.headings:
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
    if ctx.headings:
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
    if ctx.headings:
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
            line=ctx.border,
            line_width=ctx.stroke_width,
            radius=round(ctx.radius.card),
        )
    )
    if slide.image:
        out.append(Picture(panel_x, panel_y, panel_width, panel_height, slide.image, slide.image_fit))
    else:
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
        if ctx.hints:
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
    """对比矩阵：``维度|方案A|方案B``，输出原生表格（2–4 个方案列）。"""
    palette = ctx.palette
    scale = ctx.type_scale
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    declared = list(_split_parts(slide.subtitle)) if slide.subtitle.strip() else []
    options = [name or f"方案 {chr(65 + index)}" for index, name in enumerate(declared[:4])]
    while len(options) < 2:
        options.append(f"方案 {chr(65 + len(options))}")
    raw = slide.bullets or ("响应速度|小时级|分钟级", "维护成本|较高|更低", "扩展能力|受限于插件|开放接口")
    rows: list[tuple[str, ...]] = []
    for item in raw[:4]:
        parts = _split_parts(item)
        values = [
            parts[index + 1] if index + 1 < len(parts) and parts[index + 1] else "—" for index in range(len(options))
        ]
        rows.append((parts[0] or "对比维度", *values))
    table_width = ctx.width - margin * 2
    table_top = top + round(scale.body)
    label_width = round(table_width * 0.28)
    option_width = (table_width - label_width) / len(options)
    col_widths = (float(label_width), *(option_width for _ in options))
    header_height = round(scale.body * 2.1)
    spare = ctx.height - table_top - round(scale.body * 3.2) - header_height
    body_height = max(round(scale.body * 1.9), min(round(scale.body * 2.6), spare / max(len(rows), 1)))
    row_heights = (float(header_height), *(float(body_height) for _ in rows))
    numeric = [
        all(_LITERAL_NUMBER.fullmatch(row[index + 1].replace(",", "")) for row in rows) for index in range(len(options))
    ]
    last = len(options) - 1
    header_cells = (
        TableCell("对比维度", ALIGN_LEFT, True, palette.muted),
        *tuple(
            TableCell(
                name,
                ALIGN_RIGHT if numeric[index] else ALIGN_CENTER,
                True,
                ctx.accent if index == last else palette.text,
            )
            for index, name in enumerate(options)
        ),
    )
    cells: list[tuple[TableCell, ...]] = [header_cells]
    for row in rows:
        body = [TableCell(row[0], ALIGN_LEFT, False, palette.muted)]
        for index in range(len(options)):
            body.append(
                TableCell(
                    row[index + 1],
                    ALIGN_RIGHT if numeric[index] else ALIGN_CENTER,
                    index == last,
                    ctx.accent if index == last else palette.text,
                )
            )
        cells.append(tuple(body))
    out.append(
        Table(
            x=margin,
            y=table_top,
            w=table_width,
            h=sum(row_heights),
            col_widths=col_widths,
            row_heights=row_heights,
            rows=tuple(cells),
            header=True,
            header_fill=mix_hex(palette.background, ctx.accent, 0.16),
            band_fill=mix_hex(palette.surface, ctx.accent, 0.1),
            row_fill=palette.surface,
            line_color=ctx.divider,
            line_width=ctx.stroke_width,
            font=ctx.theme.body_font,
            text_color=palette.text,
            header_size=scale.small,
            body_size=scale.small,
            pad_x=round(scale.small * 0.7),
            pad_y=round(scale.small * 0.3),
        )
    )


def _render_chart(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """图表：按 ``标签|数值`` 写原生图表，类型由 ``chart_type`` 决定。"""
    palette = ctx.palette
    scale = ctx.type_scale
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    raw = slide.bullets or ("Q1|42", "Q2|68", "Q3|54", "Q4|86")
    categories: list[str] = []
    values: list[float] = []
    for item in raw[:6]:
        label, value = _parse_pair(item)
        if not _has_number(value):
            continue
        categories.append(label or f"项{len(categories) + 1}")
        values.append(_parse_number(value))
    card_top = top + round(scale.body)
    card_height = ctx.height - card_top - round(scale.body * 4)
    card_width = ctx.width - margin * 2
    colors = tuple(ctx.tokens.chart_palette) or (ctx.accent, palette.secondary)
    kind = slide.chart_type
    if kind == CHART_PIE and any(value < 0 for value in values):
        kind = "bar"
    if categories:
        out.append(
            Chart(
                x=margin,
                y=card_top,
                w=card_width,
                h=card_height,
                categories=tuple(categories),
                series=(ChartSeries("数值", tuple(values), colors[0]),),
                kind=kind,
                unit=slide.unit,
                value_format=slide.value_format,
                show_labels=slide.show_labels,
                legend=slide.legend,
                colors=tuple(colors[index % len(colors)] for index in range(len(categories))),
                font=ctx.theme.body_font,
                size=scale.caption,
                axis_color=ctx.divider,
                grid_color=mix_hex(palette.surface, palette.muted, 0.25),
                text_color=palette.text,
                muted_color=palette.muted,
            )
        )
        return
    out.append(
        Rect(
            margin,
            card_top,
            card_width,
            card_height,
            fill=palette.surface,
            line=ctx.border,
            line_width=ctx.stroke_width,
            radius=round(ctx.radius.card),
        )
    )
    out.append(
        _text(
            margin,
            card_top + card_height / 2 - round(scale.body),
            card_width,
            round(scale.body * 2),
            "暂无数据",
            scale.body,
            palette.muted,
            ctx.theme.body_font,
            align=ALIGN_CENTER,
            anchor=ANCHOR_MIDDLE,
        )
    )


def _render_contact(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """联系方式：左侧联系信息，右侧二维码占位（仅编辑器画布提示）。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    title = slide.title.strip() or "让我们保持联系"
    if ctx.headings:
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
    if ctx.headings:
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
                line_spacing=spacing.line_body,
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
    if ctx.hints:
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
                line=ctx.border,
                line_width=ctx.stroke_width,
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


def _paint_photo_background(out: list[Primitive], ctx: _Ctx, slide: SlideSpec, *, overlay: float) -> None:
    """整页图片或合成渐变兜底；图片统一压暗保证文字对比度。"""
    palette = ctx.palette
    if slide.image:
        out.append(
            Picture(
                0,
                0,
                ctx.width,
                ctx.height,
                slide.image,
                fit="cover",
                alt=slide.image_alt,
                focus=slide.image_focus,
                overlay=overlay,
                overlay_color="#05070F",
            )
        )
        return
    out.append(Rect(0, 0, ctx.width, ctx.height, fill=mix_hex(palette.background, ctx.accent, 0.3)))
    out.append(
        Oval(
            ctx.width * 0.52,
            -ctx.height * 0.22,
            ctx.width * 0.62,
            ctx.width * 0.62,
            fill=mix_hex(palette.background, ctx.accent, 0.45),
        )
    )
    out.append(
        Oval(
            -ctx.width * 0.12,
            ctx.height * 0.58,
            ctx.width * 0.5,
            ctx.width * 0.5,
            fill=mix_hex(palette.background, palette.secondary, 0.3),
        )
    )


def _render_photo_cover(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """全出血图片封面：图片铺底 + 压暗，标题落在左下。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    _paint_photo_background(out, ctx, slide, overlay=max(ctx.tokens.image_scrim, 0.35))
    eyebrow = ctx.theme.eyebrow.strip()
    if eyebrow:
        out.append(
            _text(
                margin,
                ctx.height * 0.10,
                ctx.width * 0.6,
                round(scale.small * 1.8),
                eyebrow,
                scale.small,
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
            )
        )
    out.append(Rect(margin, ctx.height * 0.50, 76, 8, fill=ctx.accent, radius=round(ctx.radius.small / 1.5)))
    title = slide.title.strip() or "在此输入演示标题"
    subtitle = slide.subtitle.strip() or "副标题 / 团队 / 日期"
    if ctx.headings:
        size = scale.display if len(title) <= 18 else round(scale.display * 0.78)
        out.append(
            _text(
                margin,
                ctx.height * 0.55,
                ctx.width - margin * 2.6,
                ctx.height * 0.20,
                title,
                size,
                palette.text,
                ctx.theme.heading_font,
                bold=True,
                line_spacing=spacing.line_display,
            )
        )
        out.append(
            _text(
                margin,
                ctx.height * 0.78,
                ctx.width - margin * 2.2,
                round(scale.heading * 1.6),
                subtitle,
                scale.heading,
                palette.muted,
                ctx.theme.body_font,
                line_spacing=spacing.line_body,
            )
        )
    credit = ctx.theme.credit.strip()
    if credit:
        out.append(
            _text(
                ctx.width - margin - ctx.width * 0.3,
                ctx.height - 64,
                ctx.width * 0.3,
                24,
                credit,
                scale.caption,
                palette.muted,
                ctx.theme.body_font,
                align=ALIGN_RIGHT,
            )
        )


def _render_photo_section(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """图片章节页：大号编号 + 章节标题，图片强压暗。"""
    palette = ctx.palette
    scale = ctx.type_scale
    margin = ctx.margin
    _paint_photo_background(out, ctx, slide, overlay=max(ctx.tokens.image_scrim, 0.5))
    number = slide.subtitle.strip() or "02"
    out.append(
        _text(
            margin,
            ctx.top,
            ctx.width * 0.5,
            round(scale.section_number * 0.9),
            number,
            round(scale.section_number * 0.6),
            mix_hex(palette.background, palette.text, 0.22),
            ctx.theme.heading_font,
            bold=True,
            line_spacing=1.0,
        )
    )
    if ctx.headings:
        out.append(
            _text(
                margin + 38,
                ctx.height * 0.45,
                ctx.width - margin * 2 - 38,
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
                margin + 38,
                ctx.height * 0.56,
                ctx.width - margin * 2 - 38,
                80,
                body,
                scale.body,
                palette.muted,
                ctx.theme.body_font,
                line_spacing=ctx.spacing.line_body,
            )
        )


def _render_cards(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """卡片要点：3–4 张等宽卡片，编号 + 标题 + 说明。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    items = slide.bullets[:4] or (
        "更短的路径|把关键操作前置。",
        "更清楚的反馈|每一步都有状态。",
        "更一致的质感|模板化排版。",
    )
    count = len(items)
    gap = spacing.gap
    card_width = (ctx.width - margin * 2 - gap * (count - 1)) / count
    card_top = top + round(scale.body * 1.2)
    card_height = ctx.height - card_top - round(scale.body * 4)
    for index, item in enumerate(items):
        parts = _split_parts(item)
        title = parts[0] or f"要点 {index + 1}"
        body = parts[1] if len(parts) > 1 else ""
        x = margin + index * (card_width + gap)
        out.append(
            Rect(
                x,
                card_top,
                card_width,
                card_height,
                fill=palette.surface,
                line=ctx.border,
                line_width=ctx.stroke_width,
                radius=round(ctx.radius.card),
            )
        )
        out.append(Rect(x, card_top, card_width, 6, fill=ctx.accent, radius=round(ctx.radius.small)))
        badge = round(scale.heading * 1.4)
        out.append(
            Oval(
                x + spacing.block,
                card_top + spacing.block,
                badge,
                badge,
                fill=mix_hex(palette.surface, ctx.accent, 0.22),
            )
        )
        out.append(
            _text(
                x + spacing.block,
                card_top + spacing.block,
                badge,
                badge,
                f"{index + 1:02d}",
                round(scale.small * 0.9),
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )
        out.append(
            _text(
                x + spacing.block,
                card_top + spacing.block + badge + spacing.gap,
                card_width - spacing.block * 2,
                round(scale.heading * 2.4),
                title,
                scale.heading,
                palette.text,
                ctx.theme.heading_font,
                bold=True,
                line_spacing=spacing.line_heading,
            )
        )
        if body:
            out.append(
                _text(
                    x + spacing.block,
                    card_top + spacing.block + badge + spacing.gap * 3,
                    card_width - spacing.block * 2,
                    card_height - spacing.block * 2 - badge - spacing.gap * 3,
                    body,
                    scale.body,
                    palette.muted,
                    ctx.theme.body_font,
                    line_spacing=spacing.line_body,
                )
            )


def _render_process(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """流程步骤：横向时间轴 + 编号节点，最多 6 步。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    steps = slide.bullets[:6] or (
        "01|梳理|确认目标与约束",
        "02|设计|定稿结构与视觉",
        "03|实现|开发与联调",
        "04|上线|灰度与复盘",
    )
    count = len(steps)
    axis_y = top + round(scale.body * 3.6)
    out.append(Line(margin + 24, axis_y, ctx.width - margin - 24, axis_y, ctx.divider, round(ctx.stroke_width * 2)))
    step_width = (ctx.width - margin * 2) / count
    for index, item in enumerate(steps):
        parts = _split_parts(item)
        number = parts[0] or f"{index + 1:02d}"
        title = parts[1] if len(parts) > 1 else number
        body = parts[2] if len(parts) > 2 else ""
        center = margin + step_width * index + step_width / 2
        node = round(scale.heading * 1.6)
        out.append(Oval(center - node / 2, axis_y - node / 2, node, node, fill=ctx.accent))
        out.append(
            _text(
                center - node / 2,
                axis_y - node / 2,
                node,
                node,
                number,
                round(scale.small * 0.85),
                palette.background,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )
        out.append(
            _text(
                center - step_width / 2 + spacing.block,
                axis_y + node,
                step_width - spacing.block * 2,
                round(scale.heading * 2.2),
                title,
                scale.heading,
                palette.text,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
                line_spacing=spacing.line_heading,
            )
        )
        if body:
            out.append(
                _text(
                    center - step_width / 2 + spacing.block,
                    axis_y + node + round(scale.heading * 2.4),
                    step_width - spacing.block * 2,
                    round(scale.body * 3),
                    body,
                    scale.body,
                    palette.muted,
                    ctx.theme.body_font,
                    align=ALIGN_CENTER,
                    line_spacing=spacing.line_body,
                )
            )


def _render_problem_solution(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """问题 / 方案对照：左列现状，右列目标，中间箭头。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    headers = _split_parts(slide.subtitle) or ("现状", "目标")
    rows = slide.bullets[:4] or ("信息散落|统一沉淀", "重复排版|自动生成")
    gap = spacing.gap
    column_width = (ctx.width - margin * 2 - gap) / 2
    header_height = round(scale.body * 2)
    body_top = top + header_height + spacing.gap
    row_height = min(round(scale.body * 3.4), (ctx.height - body_top - margin * 2) / len(rows))
    danger = ctx.tokens.semantic.danger
    success = ctx.tokens.semantic.success
    for index, header in enumerate((headers[0] or "现状", headers[1] if len(headers) > 1 else "目标")):
        x = margin + index * (column_width + gap)
        fill = mix_hex(palette.background, danger if index == 0 else success, 0.18)
        out.append(Rect(x, top, column_width, header_height, fill=fill, radius=round(ctx.radius.small)))
        out.append(
            _text(
                x + spacing.block,
                top,
                column_width - spacing.block * 2,
                header_height,
                header,
                scale.body,
                palette.text,
                ctx.theme.heading_font,
                bold=True,
                anchor=ANCHOR_MIDDLE,
            )
        )
    for index, row in enumerate(rows):
        parts = _split_parts(row)
        y = body_top + index * row_height
        for column, text in enumerate((parts[0], parts[1] if len(parts) > 1 else "")):
            x = margin + column * (column_width + gap)
            out.append(
                Rect(
                    x,
                    y,
                    column_width,
                    row_height - spacing.gap / 2,
                    fill=palette.surface,
                    line=ctx.border,
                    line_width=ctx.stroke_width,
                    radius=round(ctx.radius.small),
                )
            )
            out.append(
                _text(
                    x + spacing.block,
                    y,
                    column_width - spacing.block * 2,
                    row_height - spacing.gap / 2,
                    text,
                    scale.body,
                    palette.text,
                    ctx.theme.body_font,
                    anchor=ANCHOR_MIDDLE,
                    line_spacing=spacing.line_body,
                )
            )
        out.append(
            _text(
                margin + column_width + gap / 2 - 16,
                y,
                32,
                row_height - spacing.gap / 2,
                "→",
                scale.heading,
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )


def _chart_categories(slide: SlideSpec) -> tuple[tuple[str, ...], tuple[float, ...]]:
    """从 ``标签|数值`` 提取图表类别与数值，跳过没有数字的条目。"""
    categories: list[str] = []
    values: list[float] = []
    for item in (slide.bullets or ("Q1|42", "Q2|68", "Q3|54", "Q4|86"))[:6]:
        label, value = _parse_pair(item)
        if not _has_number(value):
            continue
        categories.append(label or f"项{len(categories) + 1}")
        values.append(_parse_number(value))
    return tuple(categories), tuple(values)


def _render_chart_conclusion(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """数据结论：左侧图表，右侧三条结论。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    categories, values = _chart_categories(slide)
    gap = spacing.section
    chart_width = (ctx.width - margin * 2) * 0.56
    content_top = top + round(scale.body)
    content_height = ctx.height - content_top - round(scale.body * 4)
    colors = tuple(ctx.tokens.chart_palette) or (ctx.accent, palette.secondary)
    if categories:
        out.append(
            Chart(
                x=margin,
                y=content_top,
                w=chart_width,
                h=content_height,
                categories=categories,
                series=(ChartSeries("数值", values, colors[0]),),
                kind=slide.chart_type,
                unit=slide.unit,
                value_format=slide.value_format,
                show_labels=True,
                legend=False,
                colors=tuple(colors[index % len(colors)] for index in range(len(categories))),
                font=ctx.theme.body_font,
                size=scale.caption,
                axis_color=ctx.divider,
                grid_color=mix_hex(palette.surface, palette.muted, 0.25),
                text_color=palette.text,
                muted_color=palette.muted,
            )
        )
    conclusions = _split_parts(slide.subtitle) or (
        "留存每提升 1pt，季度收入增加约 2.4%",
        "老客复购贡献了 61% 的增量",
        "下一步聚焦新手引导与召回",
    )
    column_x = margin + chart_width + gap
    column_width = ctx.width - margin - column_x
    row_height = content_height / max(len(conclusions), 1)
    for index, text in enumerate(conclusions[:4]):
        y = content_top + index * row_height
        out.append(Rect(column_x, y + 8, 8, round(scale.body * 1.4), fill=ctx.accent))
        out.append(
            _text(
                column_x + spacing.block,
                y,
                column_width - spacing.block,
                row_height,
                text,
                scale.body,
                palette.text,
                ctx.theme.body_font,
                anchor=ANCHOR_MIDDLE,
                line_spacing=spacing.line_body,
            )
        )


def _render_team(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """团队：头像 / 首字母 + 姓名 + 角色 + 一句话。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    members = slide.bullets[:4] or ("林岚|产品负责人|十年 B 端经验", "周野|设计负责人|专注信息设计")
    count = len(members)
    gap = spacing.gap
    column_width = (ctx.width - margin * 2 - gap * (count - 1)) / count
    avatar = round(scale.display * 1.2)
    avatar_y = top + round(scale.body * 2)
    for index, item in enumerate(members):
        parts = _split_parts(item)
        name = parts[0] or f"成员 {index + 1}"
        role = parts[1] if len(parts) > 1 else "职位"
        bio = parts[2] if len(parts) > 2 else ""
        center = margin + column_width * index + column_width / 2
        out.append(Oval(center - avatar / 2, avatar_y, avatar, avatar, fill=mix_hex(palette.surface, ctx.accent, 0.2)))
        out.append(
            _text(
                center - avatar / 2,
                avatar_y,
                avatar,
                avatar,
                name[:1],
                round(scale.display * 0.6),
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )
        out.append(
            _text(
                center - column_width / 2,
                avatar_y + avatar + spacing.gap,
                column_width,
                round(scale.heading * 1.6),
                name,
                scale.heading,
                palette.text,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
            )
        )
        out.append(
            _text(
                center - column_width / 2,
                avatar_y + avatar + spacing.gap + round(scale.heading * 1.7),
                column_width,
                round(scale.body * 1.6),
                role,
                scale.body,
                ctx.accent,
                ctx.theme.body_font,
                align=ALIGN_CENTER,
            )
        )
        if bio:
            out.append(
                _text(
                    center - column_width / 2 + spacing.block,
                    avatar_y + avatar + spacing.gap * 2 + round(scale.heading * 2.4),
                    column_width - spacing.block * 2,
                    round(scale.body * 3),
                    bio,
                    scale.body,
                    palette.muted,
                    ctx.theme.body_font,
                    align=ALIGN_CENTER,
                    line_spacing=spacing.line_body,
                )
            )


def _render_testimonial(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """客户证言：大引号 + 金句 + 头像/署名。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    out.append(
        _text(
            margin,
            ctx.height * 0.08,
            ctx.width * 0.3,
            round(scale.quote * 1.6),
            "“",
            round(scale.quote * 1.6),
            mix_hex(palette.background, ctx.accent, 0.55),
            ctx.theme.heading_font,
            bold=True,
            line_spacing=1.0,
        )
    )
    quote = slide.title.strip() or "它把我们最费时的排版，变成了一次点击。"
    if ctx.headings:
        out.append(
            _text(
                margin,
                ctx.height * 0.26,
                ctx.width - margin * 2,
                ctx.height * 0.34,
                quote,
                round(scale.quote * (0.9 if len(quote) > 28 else 1.0)),
                palette.text,
                ctx.theme.heading_font,
                line_spacing=spacing.line_body,
            )
        )
        attribution = slide.subtitle.strip() or "— 客户"
        out.append(
            _text(
                margin,
                ctx.height * 0.72,
                ctx.width - margin * 2,
                round(scale.subtitle * 1.6),
                attribution,
                scale.subtitle,
                palette.muted,
                ctx.theme.body_font,
            )
        )
    avatar = round(scale.heading * 2.4)
    avatar_x = ctx.width - margin - avatar
    avatar_y = ctx.height * 0.68
    if slide.image:
        out.append(
            Picture(
                avatar_x,
                avatar_y,
                avatar,
                avatar,
                slide.image,
                fit="cover",
                alt=slide.image_alt or "客户头像",
                focus=slide.image_focus,
                radius=avatar / 2,
            )
        )
    else:
        out.append(Oval(avatar_x, avatar_y, avatar, avatar, fill=mix_hex(palette.surface, ctx.accent, 0.2)))
        out.append(
            _text(
                avatar_x,
                avatar_y,
                avatar,
                avatar,
                "客户",
                scale.small,
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                align=ALIGN_CENTER,
                anchor=ANCHOR_MIDDLE,
            )
        )


def _render_code(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """代码 / 终端：深色面板 + 行号 + 等宽字体。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    panel_top = top + round(scale.body)
    panel_height = ctx.height - panel_top - round(scale.body * 4)
    panel_width = ctx.width - margin * 2
    panel_fill = mix_hex(palette.background, "#000000", 0.35)
    out.append(
        Rect(
            margin,
            panel_top,
            panel_width,
            panel_height,
            fill=panel_fill,
            line=ctx.border,
            line_width=ctx.stroke_width,
            radius=round(ctx.radius.card),
        )
    )
    dot_y = panel_top + round(scale.body * 0.9)
    for index, color in enumerate(("#F87171", "#FBBF24", "#34D399")):
        out.append(Oval(margin + spacing.block + index * round(scale.small * 0.9), dot_y, 10, 10, fill=color))
    language = slide.subtitle.strip()
    if language:
        out.append(
            _text(
                margin + panel_width - spacing.block - 140,
                panel_top + 8,
                140,
                24,
                language,
                scale.caption,
                palette.muted,
                "Cascadia Mono",
                align=ALIGN_RIGHT,
            )
        )
    lines = slide.bullets[:12] or ("template: brand-2026", "aspect: 16:9", "output: dist/deck.pptx")
    line_height = round(scale.body * 1.45)
    code_top = panel_top + round(scale.body * 2.2)
    number_width = round(scale.body * 1.2)
    for index, line in enumerate(lines):
        y = code_top + index * line_height
        if y + line_height > panel_top + panel_height:
            break
        out.append(
            _text(
                margin + spacing.block,
                y,
                number_width,
                line_height,
                f"{index + 1:02d}",
                round(scale.body * 0.86),
                mix_hex(panel_fill, palette.muted, 0.6),
                "Cascadia Mono",
                align=ALIGN_RIGHT,
            )
        )
        out.append(
            _text(
                margin + spacing.block + number_width + spacing.gap,
                y,
                panel_width - spacing.block * 2 - number_width - spacing.gap,
                line_height,
                line,
                round(scale.body * 0.92),
                palette.text,
                "Cascadia Mono",
                line_spacing=1.1,
            )
        )


def _render_appendix(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """附录：条目 + 引导线 + 页码，超过 4 条自动分两栏。"""
    palette = ctx.palette
    scale = ctx.type_scale
    spacing = ctx.spacing
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    items = slide.bullets[:8] or ("指标定义与计算方式|12", "调研样本与访谈记录|14", "参考案例与致谢|16")
    columns = 2 if len(items) > 4 else 1
    per_column = (len(items) + columns - 1) // columns
    gap = spacing.section
    column_width = (ctx.width - margin * 2 - gap * (columns - 1)) / columns
    row_height = min(round(scale.body * 3.2), (ctx.height - top - round(scale.body * 4)) / per_column)
    for index, item in enumerate(items):
        column = index // per_column
        row = index % per_column
        x = margin + column * (column_width + gap)
        y = top + row * row_height
        parts = _split_parts(item)
        label = parts[0] or f"附录 {index + 1}"
        page = parts[1] if len(parts) > 1 else ""
        out.append(
            _text(
                x,
                y,
                round(scale.body * 1.4),
                row_height,
                f"{index + 1:02d}",
                round(scale.body * 0.9),
                ctx.accent,
                ctx.theme.heading_font,
                bold=True,
                anchor=ANCHOR_MIDDLE,
            )
        )
        page_width = round(scale.body * 2)
        out.append(
            _text(
                x + round(scale.body * 1.6),
                y,
                column_width - round(scale.body * 1.6) - page_width - spacing.block,
                row_height,
                label,
                scale.body,
                palette.text,
                ctx.theme.body_font,
                anchor=ANCHOR_MIDDLE,
            )
        )
        out.append(
            Line(
                x + round(scale.body * 1.6),
                y + row_height - 8,
                x + column_width - page_width - spacing.block,
                y + row_height - 8,
                mix_hex(palette.background, palette.muted, 0.28),
                1,
            )
        )
        if page:
            out.append(
                _text(
                    x + column_width - page_width,
                    y,
                    page_width,
                    row_height,
                    page,
                    scale.body,
                    palette.muted,
                    ctx.theme.body_font,
                    align=ALIGN_RIGHT,
                    anchor=ANCHOR_MIDDLE,
                )
            )


def _render_agenda_pages(out: list[Primitive], ctx: _Ctx, slide: SlideSpec) -> None:
    """目录页码：编号 + 标题 + 引导线 + 页码。"""
    palette = ctx.palette
    scale = ctx.type_scale
    margin = ctx.margin
    top = _paint_heading(out, ctx, slide)
    items = slide.bullets[:6] or ("项目背景与目标|03", "现状与挑战|06", "方案与亮点|09", "落地计划|12")
    row_height = min(round(scale.heading * 2.6), (ctx.height - top - round(scale.body * 4)) / len(items))
    page_width = round(scale.display * 1.4)
    for index, item in enumerate(items):
        parts = _split_parts(item)
        title = parts[0] or f"章节 {index + 1}"
        page = parts[1] if len(parts) > 1 else ""
        y = top + index * row_height
        accent = ctx.accent if index == 0 else mix_hex(palette.background, palette.muted, 0.6)
        out.append(
            _text(
                margin,
                y,
                round(scale.heading * 1.8),
                row_height,
                f"{index + 1:02d}",
                scale.heading,
                ctx.accent if index == 0 else palette.muted,
                ctx.theme.heading_font,
                bold=True,
                anchor=ANCHOR_MIDDLE,
            )
        )
        title_width = ctx.width - margin * 2 - round(scale.heading * 1.8) - page_width
        out.append(
            _text(
                margin + round(scale.heading * 2),
                y,
                title_width,
                row_height,
                title,
                scale.heading,
                palette.text,
                ctx.theme.heading_font,
                bold=index == 0,
                anchor=ANCHOR_MIDDLE,
            )
        )
        if page:
            out.append(
                _text(
                    ctx.width - margin - page_width,
                    y,
                    page_width,
                    row_height,
                    page,
                    scale.heading,
                    accent,
                    ctx.theme.heading_font,
                    bold=True,
                    align=ALIGN_RIGHT,
                    anchor=ANCHOR_MIDDLE,
                )
            )
        if index < len(items) - 1:
            out.append(
                Rect(
                    margin,
                    y + row_height,
                    ctx.width - margin * 2,
                    1,
                    fill=ctx.divider,
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
    LAYOUT_PHOTO_COVER: _render_photo_cover,
    LAYOUT_PHOTO_SECTION: _render_photo_section,
    LAYOUT_CARDS: _render_cards,
    LAYOUT_PROCESS: _render_process,
    LAYOUT_PROBLEM_SOLUTION: _render_problem_solution,
    LAYOUT_CHART_CONCLUSION: _render_chart_conclusion,
    LAYOUT_TEAM: _render_team,
    LAYOUT_TESTIMONIAL: _render_testimonial,
    LAYOUT_CODE: _render_code,
    LAYOUT_APPENDIX: _render_appendix,
    LAYOUT_AGENDA_PAGES: _render_agenda_pages,
}


def _paint_logo(out: list[Primitive], ctx: _Ctx) -> None:
    """品牌 Logo：模板配置了 ``theme.logo`` 时固定放右上角（contain 显示）。"""
    if not ctx.theme.logo:
        return
    width = round(ctx.type_scale.body * 4.6)
    height = round(ctx.type_scale.body * 1.9)
    out.append(
        Picture(
            ctx.width - ctx.margin - width,
            round(ctx.margin * 0.5),
            width,
            height,
            ctx.theme.logo,
            "contain",
        )
    )


def _paint_footer(
    out: list[Primitive],
    ctx: _Ctx,
    slide: SlideSpec,
    *,
    page_number: int | None,
    page_total: int | None,
) -> None:
    """页脚与页码：页码按版式抑制，页脚只在模板配置时追加。"""
    theme = ctx.theme
    body_layout_has_number = slide.layout not in theme.page_number_hidden_layouts
    show_number = theme.show_page_number and page_number is not None and body_layout_has_number
    if not theme.footer and not show_number:
        return
    size = ctx.type_scale.caption
    y = ctx.height - round(size * 2.2)
    height = round(size * 1.7)
    if theme.footer:
        out.append(
            _text(
                ctx.margin,
                y,
                ctx.width - ctx.margin * 2 - 140,
                height,
                theme.footer,
                size,
                ctx.palette.muted,
                theme.body_font,
                anchor=ANCHOR_MIDDLE,
            )
        )
    if show_number:
        label = f"{page_number:02d}"
        suffix = f" / {page_total:02d}" if page_total is not None else ""
        out.append(
            _text(
                ctx.width - ctx.margin - 120,
                y,
                120,
                height,
                label + suffix,
                size,
                ctx.palette.muted,
                theme.body_font,
                align=ALIGN_RIGHT,
                anchor=ANCHOR_MIDDLE,
                field="slidenum",
                field_suffix=suffix,
            )
        )


def build_draw_list(  # noqa: PLR0913 - 版式入口需要页码 / 画布尺寸等可选参数
    slide: SlideSpec,
    template: TemplateSpec,
    *,
    width: int | None = None,
    height: int | None = None,
    page_number: int | None = None,
    page_total: int | None = None,
    hints: bool = False,
    headings: bool = True,
) -> tuple[Primitive, ...]:
    """生成单页绘制指令。

    ``hints`` 只在编辑器画布开启，用于显示「图片 / 图表 / 二维码占位」
    之类的编辑提示；导出成品始终使用默认的 ``False``。

    ``headings`` 为 ``False`` 时跳过标题与副标题文本（含章节说明、引用
    署名等标题类文案），供「基础模板占位符」路径使用：这些文案改由
    PowerPoint 原生占位符承载，装饰与正文仍由本模块绘制。
    """
    default_width, default_height = design_size(template.aspect)
    palette = _page_palette(template, slide)
    ctx = _Ctx(
        width=float(width or default_width),
        height=float(height or default_height),
        palette=palette,
        theme=template.theme,
        accent=_slide_accent(slide, palette),
        aspect=template.aspect,
        hints=hints,
        headings=headings,
    )
    out: list[Primitive] = [Rect(0, 0, ctx.width, ctx.height, fill=palette.background)]
    if slide.background == BACKGROUND_IMAGE and slide.image:
        out.append(
            Picture(
                0,
                0,
                ctx.width,
                ctx.height,
                slide.image,
                fit="cover",
                alt=slide.image_alt,
                focus=slide.image_focus,
                overlay=ctx.tokens.image_scrim,
                overlay_color=palette.background,
            )
        )
    else:
        _paint_decor(out, ctx)
    renderer = _RENDERERS.get(slide.layout, _render_bullets)
    renderer(out, ctx, slide)
    _paint_logo(out, ctx)
    _paint_footer(out, ctx, slide, page_number=page_number, page_total=page_total)
    return tuple(out)
