"""pptmake 的 PPTX 渲染与导入（python-pptx 延迟导入，可选依赖）。"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from fcmd.apps.pptmake._core import (
    ASPECT_4_3,
    ASPECT_16_9,
    ASPECT_INCHES,
    DECOR_ORBS,
    DEFAULT_BODY_FONT,
    DEFAULT_HEADING_FONT,
    LAYOUT_BULLETS,
    LAYOUT_CLOSING,
    LAYOUT_COVER,
    STYLE_CUSTOM,
    Palette,
    SlideSpec,
    TemplateError,
    TemplateSpec,
    Theme,
    mix_hex,
    normalize_hex,
    relative_luminance,
)
from fcmd.apps.pptmake._design import Line, Oval, Primitive, Rect, Text, build_draw_list

__all__ = ["export_pptx", "import_pptx", "pptx_slide_count"]

_DESIGN_UNITS_PER_INCH = 96.0
_POINTS_PER_UNIT = 72.0 / _DESIGN_UNITS_PER_INCH
_NAMESPACE = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


@dataclass(frozen=True, slots=True)
class _Api:
    """python-pptx 常用符号集合，避免在各处重复导入。"""

    Presentation: Any
    RGBColor: Any
    MSO_SHAPE: Any
    MSO_CONNECTOR: Any
    MSO_ANCHOR: Any
    MSO_AUTO_SIZE: Any
    PP_ALIGN: Any
    Inches: Any
    Pt: Any


def _api() -> _Api:
    """延迟导入 python-pptx；缺失时给出可操作的安装提示。"""
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
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
        PP_ALIGN=PP_ALIGN,
        Inches=Inches,
        Pt=Pt,
    )


def _emu(value: float, api: _Api) -> Any:
    """设计坐标（96 单位 / 英寸）转 EMU。"""
    return api.Inches(value / _DESIGN_UNITS_PER_INCH)


def _color(value: str, api: _Api) -> Any:
    """颜色字符串转 python-pptx RGBColor。"""
    return api.RGBColor.from_string(normalize_hex(value)[1:])


def _set_fill_and_line(shape: Any, fill: str, line: str, line_width: float, api: _Api) -> None:
    """设置形状填充与描边；``line`` 为空时去掉描边。"""
    shape.shadow.inherit = False
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _color(fill, api)
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = _color(line, api)
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


def _draw_text(slide: Any, primitive: Text, api: _Api) -> None:
    """绘制文本框，并把段落、对齐与字体写入。"""
    if primitive.w <= 0 or primitive.h <= 0:
        return
    box = slide.shapes.add_textbox(
        _emu(primitive.x, api),
        _emu(primitive.y, api),
        _emu(primitive.w, api),
        _emu(primitive.h, api),
    )
    frame = box.text_frame
    frame.word_wrap = True
    frame.auto_size = api.MSO_AUTO_SIZE.NONE
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.vertical_anchor = {
        "top": api.MSO_ANCHOR.TOP,
        "middle": api.MSO_ANCHOR.MIDDLE,
        "bottom": api.MSO_ANCHOR.BOTTOM,
    }[primitive.anchor]
    alignments = {
        "left": api.PP_ALIGN.LEFT,
        "center": api.PP_ALIGN.CENTER,
        "right": api.PP_ALIGN.RIGHT,
    }
    for index, paragraph_text in enumerate(primitive.paragraphs):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = alignments[primitive.align]
        paragraph.line_spacing = primitive.line_spacing
        if primitive.space_after > 0:
            paragraph.space_after = api.Pt(primitive.space_after * _POINTS_PER_UNIT)
        run = paragraph.add_run()
        run.text = paragraph_text
        font = run.font
        font.size = api.Pt(max(primitive.size * _POINTS_PER_UNIT, 1.0))
        font.bold = primitive.bold
        font.italic = primitive.italic
        font.name = primitive.font or DEFAULT_BODY_FONT
        font.color.rgb = _color(primitive.color, api)
        _set_east_asian_font(run, primitive.font or DEFAULT_BODY_FONT)


def _draw_primitive(slide: Any, primitive: Primitive, api: _Api) -> None:
    """把一个绘制图元写入幻灯片。"""
    if isinstance(primitive, Rect):
        if primitive.w <= 0 or primitive.h <= 0:
            return
        shape_type = api.MSO_SHAPE.ROUNDED_RECTANGLE if primitive.radius > 0 else api.MSO_SHAPE.RECTANGLE
        shape = slide.shapes.add_shape(
            shape_type,
            _emu(primitive.x, api),
            _emu(primitive.y, api),
            _emu(primitive.w, api),
            _emu(primitive.h, api),
        )
        if primitive.radius > 0:
            shape.adjustments[0] = min(0.5, primitive.radius / max(min(primitive.w, primitive.h), 1))
        _set_fill_and_line(shape, primitive.fill, primitive.line, primitive.line_width, api)
        return
    if isinstance(primitive, Oval):
        if primitive.w <= 0 or primitive.h <= 0:
            return
        shape = slide.shapes.add_shape(
            api.MSO_SHAPE.OVAL,
            _emu(primitive.x, api),
            _emu(primitive.y, api),
            _emu(primitive.w, api),
            _emu(primitive.h, api),
        )
        _set_fill_and_line(shape, primitive.fill, primitive.line, primitive.line_width, api)
        return
    if isinstance(primitive, Line):
        if primitive.x1 == primitive.x2 and primitive.y1 == primitive.y2:
            return
        connector = slide.shapes.add_connector(
            api.MSO_CONNECTOR.STRAIGHT,
            _emu(primitive.x1, api),
            _emu(primitive.y1, api),
            _emu(primitive.x2, api),
            _emu(primitive.y2, api),
        )
        connector.line.color.rgb = _color(primitive.color, api)
        connector.line.width = api.Pt(max(primitive.width * _POINTS_PER_UNIT, 0.75))
        return
    _draw_text(slide, primitive, api)


def export_pptx(template: TemplateSpec, path: Path) -> int:
    """按模板渲染 ``.pptx``，返回写入的页数。"""
    api = _api()
    try:
        presentation = api.Presentation()
    except Exception as exc:  # pragma: no cover - 默认模板损坏属于环境问题
        raise TemplateError(f"创建 PPTX 失败：{exc}") from exc
    width_inches, height_inches = ASPECT_INCHES.get(template.aspect, ASPECT_INCHES[ASPECT_16_9])
    presentation.slide_width = api.Inches(width_inches)
    presentation.slide_height = api.Inches(height_inches)
    blank_layout = presentation.slide_layouts[6]
    for slide_spec in template.slides:
        slide = presentation.slides.add_slide(blank_layout)
        for primitive in build_draw_list(slide_spec, template):
            _draw_primitive(slide, primitive, api)
        if slide_spec.note.strip():
            slide.notes_slide.notes_text_frame.text = slide_spec.note
    properties = presentation.core_properties
    properties.title = template.name
    properties.author = template.author
    properties.subject = "pptmake template"
    properties.comments = template.description
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        presentation.save(str(path))
    except OSError as exc:
        raise TemplateError(f"保存 PPTX 失败：{path}：{exc}") from exc
    return len(template.slides)


def _theme_color(colors: ElementTree.Element | None, tag: str) -> str | None:
    """从 ``a:clrScheme`` 中读取颜色，支持 srgbClr / sysClr。"""
    if colors is None:
        return None
    node = colors.find(f"a:{tag}", _NAMESPACE)
    if node is None:
        return None
    srgb = node.find("a:srgbClr", _NAMESPACE)
    if srgb is not None and srgb.get("val"):
        return f"#{srgb.get('val')}"
    system = node.find("a:sysClr", _NAMESPACE)
    if system is not None and system.get("lastClr"):
        return f"#{system.get('lastClr')}"
    return None


def _first_typeface(node: ElementTree.Element | None) -> str:
    """读取字体方案中的 latin / ea 字体名。"""
    if node is None:
        return ""
    for tag in ("latin", "ea", "cs"):
        child = node.find(f"a:{tag}", _NAMESPACE)
        if child is not None and child.get("typeface"):
            return str(child.get("typeface"))
    return ""


def _safe_color(value: str | None, fallback: str) -> str:
    """主题颜色非法时回落到默认值。"""
    if value is None:
        return fallback
    try:
        return normalize_hex(value)
    except ValueError:
        return fallback


def _read_theme(path: Path) -> tuple[Palette, str, str]:
    """从 PPTX 包的 ``theme1.xml`` 中提取配色与字体。"""
    default = Palette()
    try:
        with zipfile.ZipFile(path) as archive:
            themes = sorted(
                name for name in archive.namelist() if name.startswith("ppt/theme/theme") and name.endswith(".xml")
            )
            if not themes:
                return default, DEFAULT_HEADING_FONT, DEFAULT_BODY_FONT
            root = ElementTree.fromstring(archive.read(themes[0]))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return default, DEFAULT_HEADING_FONT, DEFAULT_BODY_FONT

    colors = root.find(".//a:clrScheme", _NAMESPACE)
    light = _safe_color(_theme_color(colors, "lt1"), "#FFFFFF")
    dark = _safe_color(_theme_color(colors, "dk1"), "#000000")
    background, text = (light, dark) if relative_luminance(light) >= relative_luminance(dark) else (dark, light)
    accent = _safe_color(_theme_color(colors, "accent1"), default.accent)
    secondary = _safe_color(_theme_color(colors, "accent2"), default.secondary)
    surface_mix = 0.05 if relative_luminance(background) >= 0.5 else 0.10
    palette = Palette(
        background=background,
        surface=mix_hex(background, text, surface_mix),
        accent=accent,
        secondary=secondary,
        text=text,
        muted=mix_hex(background, text, 0.45),
    )
    heading_font = _first_typeface(root.find(".//a:fontScheme/a:majorFont", _NAMESPACE)) or DEFAULT_HEADING_FONT
    body_font = _first_typeface(root.find(".//a:fontScheme/a:minorFont", _NAMESPACE)) or DEFAULT_BODY_FONT
    return palette, heading_font, body_font


def _slide_texts(slide: Any) -> list[str]:
    """按段落提取一页中的所有非空文本。"""
    texts: list[str] = []
    for shape in slide.shapes:
        if not getattr(shape, "has_text_frame", False):
            continue
        for paragraph in shape.text_frame.paragraphs:
            text = "".join(run.text for run in paragraph.runs).strip()
            if not text:
                text = paragraph.text.strip()
            if text:
                texts.append(text)
    return texts


def import_pptx(path: Path) -> TemplateSpec:
    """把现有 PPTX 尽力转换为模板：封面 + 内容页，并复用主题配色与字体。"""
    api = _api()
    try:
        presentation = api.Presentation(str(path))
    except Exception as exc:
        raise TemplateError(f"读取 PPTX 失败：{path}：{exc}") from exc
    palette, heading_font, body_font = _read_theme(path)
    ratio = presentation.slide_width / max(presentation.slide_height, 1)
    aspect = ASPECT_16_9 if ratio >= 1.5 else ASPECT_4_3
    slides_raw = list(presentation.slides)
    slides: list[SlideSpec] = []
    for index, slide in enumerate(slides_raw):
        texts = _slide_texts(slide)
        if not texts:
            slides.append(SlideSpec(layout=LAYOUT_BULLETS))
            continue
        title = texts[0]
        rest = texts[1:]
        if index == 0:
            slides.append(SlideSpec(layout=LAYOUT_COVER, title=title, subtitle=" ".join(rest[:2])))
        elif index == len(slides_raw) - 1 and any(word in title.lower() for word in ("thank", "谢谢", "感谢")):
            slides.append(SlideSpec(layout=LAYOUT_CLOSING, title=title, subtitle=" ".join(rest[:2])))
        else:
            slides.append(SlideSpec(layout=LAYOUT_BULLETS, title=title, bullets=tuple(rest or texts)))
    theme = Theme(
        style=STYLE_CUSTOM,
        palette=palette,
        heading_font=heading_font,
        body_font=body_font,
        decor=DECOR_ORBS,
    )
    return TemplateSpec(
        name=path.stem,
        description=f"从 {path.name} 导入",
        author="",
        aspect=aspect,
        theme=theme,
        slides=tuple(slides),
    )


def pptx_slide_count(path: Path) -> int:
    """读取 PPTX 页数；主要供测试与 CLI 校验使用。"""
    api = _api()
    try:
        presentation = api.Presentation(str(path))
    except Exception as exc:
        raise TemplateError(f"读取 PPTX 失败：{path}：{exc}") from exc
    return len(presentation.slides)
