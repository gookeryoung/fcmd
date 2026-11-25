"""随包基础模板：按版式名取 slideLayout，并把标题/副标题写进原生占位符。

基础模板由 :mod:`fcmd.apps.pptmake._base_asset` 生成并随包分发；缺失时
导出会退回「空白版式 + 浮动文本框」的旧路径，不影响可用性。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.pptmake._core import (
    ASPECT_16_9,
    LAYOUT_CLOSING,
    LAYOUT_CONTACT,
    LAYOUT_COVER,
    LAYOUT_IMAGE,
    LAYOUT_QUOTE,
    LAYOUT_SECTION,
    LAYOUT_TEXT,
    LAYOUT_TWO_COLUMN,
    SlideSpec,
    TemplateSpec,
)
from fcmd.apps.pptmake._pptx_util import (
    _POINTS_PER_UNIT,
    _Api,
    _apply_color,
    _scheme_map,
    _set_east_asian_font,
)

__all__ = ["base_path", "drop_placeholders", "layout_for", "raise_placeholders", "write_heading"]

_ASSET_DIR = Path(__file__).with_name("assets")
_BASE_FILES = {ASPECT_16_9: "pptmake-base-16x9.pptx"}


def base_path(aspect: str) -> Path | None:
    """返回画幅对应的基础模板路径；没有随包资源时返回 ``None``。"""
    name = _BASE_FILES.get(aspect)
    if name is None:
        return None
    path = _ASSET_DIR / name
    return path if path.is_file() else None


def layout_for(presentation: Any, key: str) -> Any | None:
    """按模板版式名（中文名）查找 slideLayout。"""
    name = LAYOUT_TEXT.get(key)
    for layout in presentation.slide_layouts:
        if layout.name == name:
            return layout
    return None


@dataclass(frozen=True, slots=True)
class _Heading:
    """一页标题与副标题的文案与样式。"""

    title: str
    title_size: float
    title_spacing: float
    subtitle: str
    subtitle_size: float
    subtitle_spacing: float
    align: str
    anchor: str


def _heading(slide: SlideSpec, template: TemplateSpec) -> _Heading:  # noqa: PLR0911 - 按版式分派文案
    """按版式给出占位符文案与字号，口径与 :mod:`fcmd.apps.pptmake._design` 一致。"""
    theme = template.theme
    scale = theme.tokens.type_scale.for_aspect(template.aspect)
    title = slide.title.strip()
    subtitle = slide.subtitle.strip()
    if slide.layout == LAYOUT_COVER:
        return _Heading(
            title or "在此输入演示标题",
            scale.display if len(title) <= 22 else round(scale.display * 0.79),
            theme.tokens.spacing.line_display,
            subtitle or "副标题 / 团队 / 日期",
            scale.heading,
            theme.tokens.spacing.line_body,
            "left",
            "top",
        )
    if slide.layout == LAYOUT_SECTION:
        body = slide.bullets[0] if slide.bullets else "一句话说明本章节要讲什么。"
        return _Heading(
            title or "章节标题",
            round(scale.title * 1.1),
            theme.tokens.spacing.line_heading,
            body,
            scale.body,
            theme.tokens.spacing.line_body,
            "left",
            "top",
        )
    if slide.layout == LAYOUT_QUOTE:
        quote = title or "真正的设计，是让复杂的事情看起来简单。"
        return _Heading(
            quote,
            scale.quote if len(quote) <= 36 else round(scale.quote * 0.8),
            theme.tokens.spacing.line_body,
            subtitle or "— 佚名",
            scale.subtitle,
            theme.tokens.spacing.line_body,
            "left",
            "top",
        )
    if slide.layout == LAYOUT_CLOSING:
        closing = title or "THANK YOU"
        return _Heading(
            closing,
            scale.display if len(closing) <= 14 else round(scale.display * 0.79),
            theme.tokens.spacing.line_display,
            subtitle or "期待与你一起，把下一次表达做得更好。",
            scale.body,
            theme.tokens.spacing.line_body,
            "center",
            "middle",
        )
    if slide.layout == LAYOUT_IMAGE:
        return _Heading(
            title or "让主视觉先说话",
            round(scale.display * 0.82),
            1.1,
            subtitle or "右侧留出大图区域，文字保持克制。",
            scale.body,
            theme.tokens.spacing.line,
            "left",
            "top",
        )
    if slide.layout == LAYOUT_CONTACT:
        return _Heading(
            title or "让我们保持联系",
            round(scale.display * 0.85),
            1.15,
            subtitle or "期待下一次合作，也欢迎随时交流。",
            scale.body,
            theme.tokens.spacing.line_body,
            "left",
            "top",
        )
    return _Heading(
        title or LAYOUT_TEXT.get(slide.layout, "页面标题"),
        scale.title,
        theme.tokens.spacing.line_heading,
        "" if slide.layout == LAYOUT_TWO_COLUMN else subtitle,
        scale.subtitle,
        theme.tokens.spacing.line_body,
        "left",
        "top",
    )


def _write_text(  # noqa: PLR0913 - 占位符文本样式参数
    shape: Any,
    text: str,
    *,
    size: float,
    spacing: float,
    bold: bool,
    color: str,
    align: str,
    anchor: str,
    latin: str,
    east_asian: str,
    api: _Api,
    scheme: dict[str, str],
) -> None:
    """把一段文本写进占位符，并设为与 draw list 相同的字体/字号/对齐。"""
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.auto_size = api.MSO_AUTO_SIZE.NONE
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    frame.vertical_anchor = api.MSO_ANCHOR.MIDDLE if anchor == "middle" else api.MSO_ANCHOR.TOP
    paragraph = frame.paragraphs[0]
    paragraph.alignment = api.PP_ALIGN.CENTER if align == "center" else api.PP_ALIGN.LEFT
    paragraph.line_spacing = spacing
    run = paragraph.add_run()
    run.text = text
    run.font.size = api.Pt(max(size * _POINTS_PER_UNIT, 1.0))
    run.font.bold = bold
    run.font.name = latin
    _set_east_asian_font(run, east_asian)
    _apply_color(run.font.color, color, scheme, api)


def _subtitle_shape(slide: Any) -> Any | None:
    """找出副标题占位符；基础模板固定用 ``subTitle`` 类型。"""
    for shape in slide.placeholders:
        if shape.placeholder_format.idx == 1:
            return shape
    return None


def write_heading(slide: Any, spec: SlideSpec, template: TemplateSpec, api: _Api) -> None:
    """把标题与副标题写入占位符（调用方随后用 ``headings=False`` 生成图元）。"""
    heading = _heading(spec, template)
    theme = template.theme
    palette = theme.palette
    scheme = _scheme_map(theme)
    title_shape = slide.shapes.title
    if title_shape is not None:
        _write_text(
            title_shape,
            heading.title,
            size=heading.title_size,
            spacing=heading.title_spacing,
            bold=True,
            color=palette.text,
            align=heading.align,
            anchor=heading.anchor,
            latin="+mj-lt",
            east_asian="+mj-ea",
            api=api,
            scheme=scheme,
        )
    subtitle_shape = _subtitle_shape(slide)
    if subtitle_shape is not None:
        _write_text(
            subtitle_shape,
            heading.subtitle,
            size=heading.subtitle_size,
            spacing=heading.subtitle_spacing,
            bold=False,
            color=palette.muted,
            align=heading.align,
            anchor=heading.anchor,
            latin="+mn-lt",
            east_asian="+mn-ea",
            api=api,
            scheme=scheme,
        )


def raise_placeholders(slide: Any) -> None:
    """把占位符移到 spTree 末尾，避免被背景与装饰图元盖住。"""
    tree = slide.shapes._spTree
    for shape in list(slide.placeholders):
        element = shape._element
        tree.remove(element)
        tree.append(element)


def drop_placeholders(slide: Any) -> None:
    """删除版式自带的占位符；未知版式退回浮动文本框时避免留下空占位。"""
    tree = slide.shapes._spTree
    for shape in list(slide.placeholders):
        tree.remove(shape._element)
