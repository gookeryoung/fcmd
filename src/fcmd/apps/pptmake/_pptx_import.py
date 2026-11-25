"""pptmake 的 PPTX 导入：把现有演示文稿尽力还原成模板 JSON。

从 :mod:`fcmd.apps.pptmake._pptx` 拆出，避免单文件超过行数预算；导出与
导入各自只依赖 ``python-pptx`` 的读取 / 写入接口，互不引用。
"""

from __future__ import annotations

import contextlib
import hashlib
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from fcmd.apps.pptmake._core import (
    ASPECT_4_3,
    ASPECT_16_9,
    CHART_BAR,
    CHART_LINE,
    CHART_PIE,
    DECOR_ORBS,
    DEFAULT_BODY_FONT,
    DEFAULT_HEADING_FONT,
    LAYOUT_BULLETS,
    LAYOUT_CHART,
    LAYOUT_CLOSING,
    LAYOUT_COMPARISON,
    LAYOUT_COVER,
    LAYOUT_IMAGE,
    LAYOUT_PHOTO_COVER,
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
from fcmd.apps.pptmake._pptx_util import _api

__all__ = ["import_pptx"]

_NAMESPACE = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_PICTURE_MIN_AREA = 0.12


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


def _slide_notes(slide: Any) -> str:
    """读取备注文本；没有备注页时返回空串。"""
    if not getattr(slide, "has_notes_slide", False):
        return ""
    frame = slide.notes_slide.notes_text_frame
    return frame.text.strip() if frame is not None else ""


def _picture_shape(slide: Any, presentation: Any) -> Any | None:
    """取面积最大的图片；占比低于阈值视为装饰图，不单独成版。"""
    page_area = max(presentation.slide_width * presentation.slide_height, 1)
    candidates = [shape for shape in slide.shapes if shape.shape_type == 13 and shape.width and shape.height]
    if not candidates:
        return None
    shape = max(candidates, key=lambda item: item.width * item.height)
    return shape if shape.width * shape.height / page_area >= _PICTURE_MIN_AREA else None


def _table_data(slide: Any) -> tuple[str, tuple[str, ...]] | None:
    """把第一个表格转成对比矩阵：表头作列名，其余行作用 ``|`` 分隔的维度。"""
    for shape in slide.shapes:
        if not getattr(shape, "has_table", False):
            continue
        rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
        rows = [row for row in rows if any(row)]
        if len(rows) < 2:
            return None
        header = rows[0]
        subtitle = "|".join(header[1:]) if len(header) > 1 else "|".join(header)
        return subtitle, tuple("|".join(row) for row in rows[1:])
    return None


def _chart_kind(chart_type: Any) -> str:
    """把 python-pptx 的图表类型归并到 bar / line / pie。"""
    text = str(chart_type).upper()
    if "PIE" in text or "DOUGHNUT" in text:
        return CHART_PIE
    if "LINE" in text or "SCATTER" in text or "XY" in text:
        return CHART_LINE
    return CHART_BAR


def _chart_data(slide: Any) -> tuple[str, tuple[str, ...], str] | None:
    """把第一个图表还原成 ``(chart_type, bullets, unit)``。"""
    for shape in slide.shapes:
        if not getattr(shape, "has_chart", False):
            continue
        chart = shape.chart
        with contextlib.suppress(Exception):
            plot = chart.plots[0]
            categories = [str(item) for item in (plot.categories or ())]
            values = [float(value) for value in plot.series[0].values]
            if not categories or not values:
                return None
            bullets = tuple(
                f"{category}|{round(value, 1):g}" for category, value in zip(categories, values, strict=False)
            )
            unit = ""
            with contextlib.suppress(Exception):
                unit = chart.value_axis.axis_title.text_frame.text.strip()
            return _chart_kind(chart.chart_type), bullets, unit
    return None


def _extract_image(shape: Any, assets_dir: Path, stem: str) -> str:
    """把图片按内容哈希写进 ``*.assets/``，返回相对模板文件的路径。"""
    image = shape.image
    digest = hashlib.sha1(image.blob).hexdigest()[:8]
    suffix = f".{image.ext}" if image.ext else ".png"
    assets_dir.mkdir(parents=True, exist_ok=True)
    name = f"{stem}-{digest}{suffix}"
    (assets_dir / name).write_bytes(image.blob)
    return f"{assets_dir.name}/{name}"


def _cap(layout: str, bullets: tuple[str, ...]) -> tuple[str, ...]:
    """按版式容量截断要点，保证导入结果能直接通过 ``validate``。"""
    from fcmd.apps.pptmake._rules import rule_for

    return bullets[: rule_for(layout).max_bullets]


def import_pptx(path: Path, *, assets_dir: Path | None = None, report: list[str] | None = None) -> TemplateSpec:
    """把现有 PPTX 尽力转换为模板，并复用主题配色与字体。

    图片按内容哈希抽取到 ``assets_dir`` 并映射为图片版式，表格还原为对比
    矩阵，图表还原为图表页，备注原样保留；``report`` 逐页记录「保留 /
    降级 / 丢弃」摘要，供 CLI 直接打印。
    """
    api = _api()
    try:
        presentation = api.Presentation(str(path))
    except Exception as exc:
        raise TemplateError(f"读取 PPTX 失败：{path}：{exc}") from exc
    palette, heading_font, body_font = _read_theme(path)
    ratio = presentation.slide_width / max(presentation.slide_height, 1)
    aspect = ASPECT_16_9 if ratio >= 1.5 else ASPECT_4_3
    slides_raw = list(presentation.slides)
    stem = "".join(char if char.isalnum() or char in "-_" else "-" for char in path.stem) or "slide"
    lines = report if report is not None else []
    slides: list[SlideSpec] = []
    for index, slide in enumerate(slides_raw):
        page = index + 1
        texts = _slide_texts(slide)
        title_shape = slide.shapes.title
        title_text = title_shape.text.strip() if title_shape is not None else ""
        if title_text and title_text in texts:
            texts.remove(title_text)
            texts.insert(0, title_text)
        note = _slide_notes(slide)
        chart = _chart_data(slide)
        table = _table_data(slide) if chart is None else None
        picture = _picture_shape(slide, presentation)
        image = ""
        image_alt = ""
        if picture is not None and assets_dir is not None:
            with contextlib.suppress(Exception):
                image = _extract_image(picture, assets_dir, stem)
                image_alt = picture._element.nvPicPr.cNvPr.get("descr", "") or ""
        if not texts:
            slides.append(SlideSpec(layout=LAYOUT_BULLETS, note=note))
            lines.append(f"第 {page} 页：丢弃（无文本）")
            continue
        title = texts[0]
        rest = texts[1:]
        suffix = "；备注保留" if note else ""
        if index == 0:
            layout = LAYOUT_PHOTO_COVER if image else LAYOUT_COVER
            slides.append(
                SlideSpec(
                    layout=layout,
                    title=title,
                    subtitle=" ".join(rest[:2]),
                    image=image,
                    image_alt=image_alt,
                    note=note,
                )
            )
            lines.append(f"第 {page} 页：保留封面{suffix}")
            continue
        if index == len(slides_raw) - 1 and any(word in title.lower() for word in ("thank", "谢谢", "感谢")):
            slides.append(SlideSpec(layout=LAYOUT_CLOSING, title=title, subtitle=" ".join(rest[:2]), note=note))
            lines.append(f"第 {page} 页：保留结尾{suffix}")
            continue
        if chart is not None:
            chart_type, bullets, unit = chart
            slides.append(
                SlideSpec(
                    layout=LAYOUT_CHART,
                    title=title,
                    bullets=_cap(LAYOUT_CHART, bullets),
                    chart_type=chart_type,
                    unit=unit,
                    note=note,
                )
            )
            lines.append(f"第 {page} 页：保留图表（{len(bullets)} 个数据点）{suffix}")
        elif table is not None:
            subtitle, bullets = table
            slides.append(
                SlideSpec(
                    layout=LAYOUT_COMPARISON,
                    title=title,
                    subtitle=subtitle,
                    bullets=_cap(LAYOUT_COMPARISON, bullets),
                    note=note,
                )
            )
            lines.append(f"第 {page} 页：保留表格 → 对比矩阵{suffix}")
        elif image:
            kept = _cap(LAYOUT_IMAGE, tuple(rest))
            slides.append(
                SlideSpec(
                    layout=LAYOUT_IMAGE,
                    title=title,
                    subtitle=" ".join(rest[:1]),
                    bullets=kept,
                    image=image,
                    image_alt=image_alt,
                    note=note,
                )
            )
            dropped = len(rest) - len(kept)
            detail = f"，{dropped} 条文本降级丢弃" if dropped > 0 else ""
            lines.append(f"第 {page} 页：保留图片 → 图片版式{detail}{suffix}")
        else:
            bullets = _cap(LAYOUT_BULLETS, tuple(rest or texts))
            slides.append(SlideSpec(layout=LAYOUT_BULLETS, title=title, bullets=bullets, note=note))
            lines.append(f"第 {page} 页：降级为要点（{len(bullets)} 条）{suffix}")
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
