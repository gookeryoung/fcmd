"""pptmake 的 PPTX 渲染与导入（python-pptx 延迟导入，可选依赖）。"""

from __future__ import annotations

import contextlib
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
from fcmd.apps.pptmake._design import Line, Oval, Picture, Primitive, Rect, Text, build_draw_list

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
    MSO_THEME_COLOR: Any
    PP_ALIGN: Any
    Inches: Any
    Pt: Any


def _api() -> _Api:
    """延迟导入 python-pptx；缺失时给出可操作的安装提示。"""
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
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


def _style_run(run: Any, primitive: Text, api: _Api, scheme: dict[str, str]) -> None:
    """写入字号、字重、字体与颜色；主题色优先用 schemeClr。"""
    font = run.font
    font.size = api.Pt(max(primitive.size * _POINTS_PER_UNIT, 1.0))
    font.bold = primitive.bold
    font.italic = primitive.italic
    font.name = primitive.font or DEFAULT_BODY_FONT
    _apply_color(font.color, primitive.color, scheme, api)
    _set_east_asian_font(run, primitive.font or DEFAULT_BODY_FONT)


def _write_slide_field(paragraph: Any, primitive: Text, scheme: dict[str, str]) -> None:
    """把首段写成 PowerPoint 字段（如 slidenum），增删页后自动更新。"""
    try:
        from lxml import etree
        from pptx.oxml.ns import qn
    except ImportError:  # pragma: no cover - lxml 随 python-pptx 安装
        return
    field = etree.SubElement(paragraph._p, qn("a:fld"))
    field.set("id", "{A1B2C3D4-0001-4E5F-9A00-6A1B2C3D4E5F}")
    field.set("type", primitive.field)
    props = etree.SubElement(field, qn("a:rPr"))
    props.set("lang", "zh-CN")
    props.set("sz", str(round(primitive.size * _POINTS_PER_UNIT * 100)))
    if primitive.bold:
        props.set("b", "1")
    fill = etree.SubElement(props, qn("a:solidFill"))
    token = scheme.get(normalize_hex(primitive.color))
    if token:
        etree.SubElement(fill, qn("a:schemeClr")).set("val", token)
    else:
        etree.SubElement(fill, qn("a:srgbClr")).set("val", normalize_hex(primitive.color)[1:])
    element = etree.SubElement(field, qn("a:t"))
    element.text = primitive.paragraphs[0]


def _draw_text(  # noqa: PLR0913 - 需要主题/方案/native 三个导出开关
    slide: Any,
    primitive: Text,
    api: _Api,
    *,
    theme: Theme,
    scheme: dict[str, str],
    native: bool,
) -> None:
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
    heading_font = primitive.font or DEFAULT_BODY_FONT
    latin = f"+{'mj' if heading_font == theme.heading_font else 'mn'}-lt" if native else heading_font
    east_asian = f"+{'mj' if heading_font == theme.heading_font else 'mn'}-ea" if native else heading_font
    for index, paragraph_text in enumerate(primitive.paragraphs):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = alignments[primitive.align]
        paragraph.line_spacing = primitive.line_spacing
        if primitive.space_after > 0:
            paragraph.space_after = api.Pt(primitive.space_after * _POINTS_PER_UNIT)
        level = max(0, min(int(primitive.level), 8))
        paragraph.level = level
        if primitive.indent > 0 and level > 0:
            offset = round(primitive.indent * level * _POINTS_PER_UNIT * 12700)
            props = paragraph._p.get_or_add_pPr()
            props.set("marL", str(offset))
            props.set("indent", str(-offset))
        if index == 0 and primitive.field and native:
            _write_slide_field(paragraph, primitive, scheme)
            if primitive.field_suffix:
                suffix = paragraph.add_run()
                suffix.text = primitive.field_suffix
                _style_run(suffix, primitive, api, scheme)
                suffix.font.name = latin
                _set_east_asian_font(suffix, east_asian)
            continue
        run = paragraph.add_run()
        run.text = paragraph_text
        _style_run(run, primitive, api, scheme)
        run.font.name = latin
        _set_east_asian_font(run, east_asian)


def _draw_picture(slide: Any, primitive: Picture, api: _Api) -> None:
    """把本地图片写入幻灯片；cover 通过裁剪填满，contain 按比例居中。"""
    if primitive.w <= 0 or primitive.h <= 0 or not primitive.path:
        return
    path = Path(primitive.path)
    if not path.is_file():
        raise TemplateError(f"图片不存在：{path}")
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - python-pptx 通常自带 Pillow
        shape = slide.shapes.add_picture(
            str(path),
            _emu(primitive.x, api),
            _emu(primitive.y, api),
            _emu(primitive.w, api),
            _emu(primitive.h, api),
        )
        del shape
        return
    with Image.open(path) as image:
        image_width, image_height = image.size
    if image_width <= 0 or image_height <= 0:
        return
    box_ratio = primitive.w / primitive.h
    image_ratio = image_width / image_height
    if primitive.fit == "contain":
        scale = min(primitive.w / image_width, primitive.h / image_height)
        width = image_width * scale
        height = image_height * scale
        left = primitive.x + (primitive.w - width) / 2
        top = primitive.y + (primitive.h - height) / 2
        slide.shapes.add_picture(str(path), _emu(left, api), _emu(top, api), _emu(width, api), _emu(height, api))
        return
    picture = slide.shapes.add_picture(
        str(path),
        _emu(primitive.x, api),
        _emu(primitive.y, api),
        _emu(primitive.w, api),
        _emu(primitive.h, api),
    )
    if image_ratio > box_ratio:
        excess = 1 - box_ratio / image_ratio
        picture.crop_left = excess / 2
        picture.crop_right = excess / 2
    elif image_ratio < box_ratio:
        excess = 1 - image_ratio / box_ratio
        picture.crop_top = excess / 2
        picture.crop_bottom = excess / 2


def _draw_primitive(  # noqa: PLR0911, PLR0913 - 图元分派
    slide: Any,
    primitive: Primitive,
    api: _Api,
    *,
    theme: Theme,
    scheme: dict[str, str],
    native: bool,
) -> None:
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
        _set_fill_and_line(shape, primitive.fill, primitive.line, primitive.line_width, api, scheme)
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
        _set_fill_and_line(shape, primitive.fill, primitive.line, primitive.line_width, api, scheme)
        return
    if isinstance(primitive, Picture):
        _draw_picture(slide, primitive, api)
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
        _apply_color(connector.line.color, primitive.color, scheme, api)
        connector.line.width = api.Pt(max(primitive.width * _POINTS_PER_UNIT, 0.75))
        return
    _draw_text(slide, primitive, api, theme=theme, scheme=scheme, native=native)


def _rewrite_theme_xml(data: bytes, template: TemplateSpec) -> bytes:
    """把模板配色与字体写进 ``theme1.xml`` 的 clrScheme / fontScheme。"""
    palette = template.theme.palette
    colors = {
        "dk1": palette.text,
        "lt1": palette.background,
        "dk2": palette.muted,
        "lt2": palette.surface,
        "accent1": palette.accent,
        "accent2": palette.secondary,
        "accent3": palette.accent,
        "accent4": palette.secondary,
        "accent5": palette.muted,
        "accent6": palette.text,
        "hlink": palette.accent,
        "folHlink": palette.secondary,
    }
    namespace = _NAMESPACE["a"]
    ElementTree.register_namespace("a", namespace)
    root = ElementTree.fromstring(data)
    scheme = root.find(f".//{{{namespace}}}clrScheme")
    if scheme is not None:
        for name, value in colors.items():
            node = scheme.find(f"{{{namespace}}}{name}")
            if node is None:
                continue
            for child in list(node):
                node.remove(child)
            srgb = ElementTree.SubElement(node, f"{{{namespace}}}srgbClr")
            srgb.set("val", normalize_hex(value)[1:])
    fonts = root.find(f".//{{{namespace}}}fontScheme")
    if fonts is not None:
        pairs = (("majorFont", template.theme.heading_font), ("minorFont", template.theme.body_font))
        for tag, typeface in pairs:
            node = fonts.find(f"{{{namespace}}}{tag}")
            if node is None:
                continue
            latin = node.find(f"{{{namespace}}}latin")
            if latin is None:
                latin = ElementTree.Element(f"{{{namespace}}}latin")
                node.insert(0, latin)
            latin.set("typeface", typeface)
            east_asian = node.find(f"{{{namespace}}}ea")
            if east_asian is None:
                east_asian = ElementTree.Element(f"{{{namespace}}}ea")
                node.insert(1, east_asian)
            east_asian.set("typeface", typeface)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _apply_theme_package(path: Path, template: TemplateSpec) -> None:
    """导出后原地重写主题部件；失败时保留原包，不影响成品可用性。"""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    except (OSError, zipfile.BadZipFile):
        return
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for info, data in entries:
                payload = data
                if info.filename == "ppt/theme/theme1.xml":
                    with contextlib.suppress(ElementTree.ParseError):
                        payload = _rewrite_theme_xml(data, template)
                archive.writestr(info, payload)
    except OSError:
        return


def export_pptx(template: TemplateSpec, path: Path, *, native: bool = True) -> int:
    """按模板渲染 ``.pptx``，返回写入的页数。

    ``native`` 为真（默认）时写入主题色/主题字体与 ``slidenum`` 字段；
    ``--flat`` 会关闭这些后处理，保留纯 ``srgbClr`` + 静态页码的旧行为。
    """
    api = _api()
    try:
        presentation = api.Presentation()
    except Exception as exc:  # pragma: no cover - 默认模板损坏属于环境问题
        raise TemplateError(f"创建 PPTX 失败：{exc}") from exc
    width_inches, height_inches = ASPECT_INCHES.get(template.aspect, ASPECT_INCHES[ASPECT_16_9])
    presentation.slide_width = api.Inches(width_inches)
    presentation.slide_height = api.Inches(height_inches)
    scheme = _scheme_map(template.theme) if native else {}
    blank_layout = presentation.slide_layouts[6]
    total = len(template.slides)
    for index, slide_spec in enumerate(template.slides, start=1):
        slide = presentation.slides.add_slide(blank_layout)
        primitives = build_draw_list(slide_spec, template, page_number=index, page_total=total)
        for primitive in primitives:
            _draw_primitive(slide, primitive, api, theme=template.theme, scheme=scheme, native=native)
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
    if native:
        _apply_theme_package(path, template)
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
