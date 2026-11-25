"""pptmake 的 PPTX 导入：把现有演示文稿尽力还原成模板 JSON。

从 :mod:`fcmd.apps.pptmake._pptx` 拆出，避免单文件超过行数预算；导出与
导入各自只依赖 ``python-pptx`` 的读取 / 写入接口，互不引用。
"""

from __future__ import annotations

import contextlib
import hashlib
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Any
from xml.etree import ElementTree

from fcmd.apps.pptmake import _tokens as tokens
from fcmd.apps.pptmake._core import (
    ASPECT_4_3,
    ASPECT_16_9,
    CHART_BAR,
    CHART_LINE,
    CHART_PIE,
    DECOR_ORBS,
    DEFAULT_BODY_FONT,
    DEFAULT_HEADING_FONT,
    IMAGE_FOCUS_CENTER,
    IMAGE_RATIO_GRID_2,
    IMAGE_RATIO_GRID_3,
    LAYOUT_BULLETS,
    LAYOUT_CHART,
    LAYOUT_CLOSING,
    LAYOUT_COMPARISON,
    LAYOUT_COVER,
    LAYOUT_IMAGE,
    LAYOUT_PHOTO_COVER,
    STYLE_CUSTOM,
    DesignTokens,
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
from fcmd.apps.pptmake._preview import contrast_ratio
from fcmd.apps.pptmake._quality import BODY_CONTRAST, fix_template
from fcmd.apps.pptmake._rules import rule_for

__all__ = ["import_pptx"]

_NAMESPACE = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_DGM_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_PICTURE_MIN_AREA = 0.12
_PICTURE_SAME_TOLERANCE = 0.05
_REPORT_KINDS = ("文本", "图片", "表格", "图表", "备注")
_UNITS_PER_POINT = 4 / 3
_BODY_POINTS = 18.0
_TITLE_POINT_CAP = 100.0
_MIN_TOKEN_SIZE = 16.0
_MAX_TOKEN_SIZE = 300.0


@dataclass
class ImportStats:
    """五类内容的「发现 / 保留」计数，用于报告末尾的保留率摘要。"""

    found: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_REPORT_KINDS, 0))
    kept: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_REPORT_KINDS, 0))

    def summary(self) -> str:
        """格式化为 ``保留率：文本 98.0%（49/50） · …``；总量为 0 时按 100% 计。"""
        parts: list[str] = []
        for kind in _REPORT_KINDS:
            total, kept = self.found[kind], self.kept[kind]
            rate = 100.0 if total == 0 else kept / total * 100
            parts.append(f"{kind} {rate:.1f}%（{kept}/{total}）")
        return "保留率：" + " · ".join(parts)


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
    muted = mix_hex(background, text, 0.45)
    if contrast_ratio(muted, background) < BODY_CONTRAST:
        muted = mix_hex(background, text, 0.65)
    if contrast_ratio(muted, background) < BODY_CONTRAST:
        muted = text
    palette = Palette(
        background=background,
        surface=mix_hex(background, text, surface_mix),
        accent=accent,
        secondary=secondary,
        text=text,
        muted=muted,
    )
    heading_font = _first_typeface(root.find(".//a:fontScheme/a:majorFont", _NAMESPACE)) or DEFAULT_HEADING_FONT
    body_font = _first_typeface(root.find(".//a:fontScheme/a:minorFont", _NAMESPACE)) or DEFAULT_BODY_FONT
    return palette, heading_font, body_font


def _iter_shapes(shapes: Any) -> Any:
    """递归遍历组合形状，按文档顺序产出叶子形状。"""
    for shape in shapes:
        nested = getattr(shape, "shapes", None)
        if nested is not None:
            yield from _iter_shapes(nested)
        else:
            yield shape


def _smartart_texts(slide: Any) -> list[str]:
    """从 ``diagramData`` 部件按节点顺序提取 SmartArt 文本。"""
    texts: list[str] = []
    with contextlib.suppress(Exception):
        for rel in slide.part.rels.values():
            if not rel.reltype.endswith("/diagramData"):
                continue
            root = ElementTree.fromstring(rel.target_part.blob)
            for point in root.iter(f"{{{_DGM_NAMESPACE}}}pt"):
                text = "".join(point.itertext()).strip()
                if text:
                    texts.append(text)
    return texts


def _slide_texts(slide: Any) -> list[str]:
    """按段落提取一页中的所有非空文本（含组合形状与 SmartArt）。"""
    texts: list[str] = []
    for shape in _iter_shapes(slide.shapes):
        if not getattr(shape, "has_text_frame", False):
            continue
        for paragraph in shape.text_frame.paragraphs:
            text = "".join(run.text for run in paragraph.runs).strip()
            if not text:
                text = paragraph.text.strip()
            if text:
                texts.append(text)
    texts.extend(_smartart_texts(slide))
    return texts


def _slide_notes(slide: Any) -> str:
    """读取备注文本；没有备注页时返回空串。"""
    if not getattr(slide, "has_notes_slide", False):
        return ""
    frame = slide.notes_slide.notes_text_frame
    return frame.text.strip() if frame is not None else ""


def _picture_shapes(slide: Any, presentation: Any) -> list[Any]:
    """按从上到下、从左到右返回面积达标的图片；小于阈值视为装饰。"""
    page_area = max(presentation.slide_width * presentation.slide_height, 1)
    candidates = [
        shape
        for shape in _iter_shapes(slide.shapes)
        if shape.shape_type == 13
        and shape.width
        and shape.height
        and shape.width * shape.height / page_area >= _PICTURE_MIN_AREA
    ]
    return sorted(candidates, key=lambda item: (round(item.top or 0), round(item.left or 0)))


def _same_size(shapes: list[Any]) -> bool:
    """判断图片是否同尺寸（容差 5%），用于识别「一组并排图」。"""
    width, height = shapes[0].width, shapes[0].height
    return all(
        abs(shape.width - width) <= width * _PICTURE_SAME_TOLERANCE
        and abs(shape.height - height) <= height * _PICTURE_SAME_TOLERANCE
        for shape in shapes
    )


def _image_plan(count: int) -> list[tuple[str, int]]:
    """多图映射规则：2 张双格、3 张三格、4 张拆两页双格，更多按 3 张一页切分。"""
    if count <= 1:
        return [("", 1)]
    if count == 2:
        return [(IMAGE_RATIO_GRID_2, 2)]
    if count == 3:
        return [(IMAGE_RATIO_GRID_3, 3)]
    if count == 4:
        return [(IMAGE_RATIO_GRID_2, 2), (IMAGE_RATIO_GRID_2, 2)]
    plan: list[tuple[str, int]] = []
    remaining = count
    while remaining > 0:
        if remaining >= 3:
            plan.append((IMAGE_RATIO_GRID_3, 3))
            remaining -= 3
        elif remaining == 2:
            plan.append((IMAGE_RATIO_GRID_2, 2))
            remaining = 0
        else:
            plan.append(("", 1))
            remaining = 0
    return plan


def _picture_focus(picture: Any) -> str:
    """由源稿裁剪比例反推焦点：裁掉右侧即靠左，裁掉上方即靠下。"""
    with contextlib.suppress(Exception):
        left = float(picture.crop_left or 0)
        right = float(picture.crop_right or 0)
        top = float(picture.crop_top or 0)
        bottom = float(picture.crop_bottom or 0)
        if abs(left - right) >= abs(top - bottom):
            if right - left > 0.01:
                return "left"
            if left - right > 0.01:
                return "right"
        else:
            if bottom - top > 0.01:
                return "top"
            if top - bottom > 0.01:
                return "bottom"
    return "center"


def _extract_pictures(pictures: list[Any], assets_dir: Path, stem: str) -> list[tuple[str, str, str]]:
    """逐张抽取图片到资产目录，返回 ``(相对路径, 替代文本, 焦点)``；失败项跳过。"""
    extracted: list[tuple[str, str, str]] = []
    for picture in pictures:
        with contextlib.suppress(Exception):
            alt = picture._element.nvPicPr.cNvPr.get("descr", "") or ""
            extracted.append((_extract_image(picture, assets_dir, stem), alt, _picture_focus(picture)))
    return extracted


def _tables_data(slide: Any) -> list[tuple[str, tuple[str, ...]]]:
    """把所有表格转成对比矩阵：表头作列名，其余行作用 ``|`` 分隔的维度。"""
    tables: list[tuple[str, tuple[str, ...]]] = []
    for shape in _iter_shapes(slide.shapes):
        if not getattr(shape, "has_table", False):
            continue
        rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
        rows = [row for row in rows if any(row)]
        if len(rows) < 2:
            continue
        header = rows[0]
        subtitle = "|".join(header[1:]) if len(header) > 1 else "|".join(header)
        tables.append((subtitle, tuple("|".join(row) for row in rows[1:])))
    return tables


def _sequence_title(title: str, index: int) -> str:
    """拆页时的续页标题：保留原题并在容量内追加序号。"""
    suffix = f"（{index}）"
    return title[: max(0, rule_for(LAYOUT_COMPARISON).max_title - len(suffix))] + suffix


def _table_slides(title: str, tables: list[tuple[str, tuple[str, ...]]], note: str) -> tuple[list[SlideSpec], bool]:
    """多表映射：同表头且总行数可容纳时合并，否则每表一页（拆页）。"""
    if len(tables) == 1:
        subtitle, bullets = tables[0]
        return [
            SlideSpec(
                layout=LAYOUT_COMPARISON,
                title=title,
                subtitle=subtitle,
                bullets=_cap(LAYOUT_COMPARISON, bullets),
                note=note,
            )
        ], False
    headers = {subtitle for subtitle, _rows in tables}
    merged_rows = tuple(row for _subtitle, rows in tables for row in rows)
    if len(headers) == 1 and len(merged_rows) <= rule_for(LAYOUT_COMPARISON).max_bullets:
        return [
            SlideSpec(
                layout=LAYOUT_COMPARISON,
                title=title,
                subtitle=tables[0][0],
                bullets=merged_rows,
                note=note,
            )
        ], True
    pages: list[SlideSpec] = []
    for order, (subtitle, bullets) in enumerate(tables):
        pages.append(
            SlideSpec(
                layout=LAYOUT_COMPARISON,
                title=title if order == 0 else _sequence_title(title, order + 1),
                subtitle=subtitle,
                bullets=_cap(LAYOUT_COMPARISON, bullets),
                note=note if order == 0 else "",
            )
        )
    return pages, False


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
    for shape in _iter_shapes(slide.shapes):
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
    return bullets[: rule_for(layout).max_bullets]


def _collect_typography(slide: Any, font_sizes: list[float], line_spacings: list[float]) -> None:
    """收集一页的字号（pt）与倍数行距，供导入后还原字号阶梯。"""
    for shape in _iter_shapes(slide.shapes):
        if not getattr(shape, "has_text_frame", False):
            continue
        for paragraph in shape.text_frame.paragraphs:
            spacing = paragraph.line_spacing
            if isinstance(spacing, float):
                line_spacings.append(spacing)
            for run in paragraph.runs:
                size = run.font.size
                if size is None:
                    continue
                with contextlib.suppress(Exception):
                    font_sizes.append(float(size.pt))


def _clamp_size(value: float) -> float:
    """把设计单位字号收进令牌允许区间，并保留一位小数。"""
    return round(min(_MAX_TOKEN_SIZE, max(_MIN_TOKEN_SIZE, value)), 1)


def _typography_tokens(base: DesignTokens, sizes: list[float], spacings: list[float]) -> DesignTokens:
    """按源稿字号与行距中位数还原字号阶梯与正文行距。

    以正文中位数字号相对默认 18pt 的比例整体缩放令牌；标题另取不超过
    100pt 的最大字号换算设计单位，避免封面大字或章节编号把阶梯拉到上限。
    """
    body_pt = median(sizes) if sizes else _BODY_POINTS
    ratio = body_pt / _BODY_POINTS
    scaled = {name: _clamp_size(getattr(base.type_scale, name) * ratio) for name in tokens.TYPE_FIELDS}
    headlines = [size for size in sizes if size <= _TITLE_POINT_CAP]
    if headlines:
        title = _clamp_size(max(headlines) * _UNITS_PER_POINT)
        scaled["title"] = title
        scaled["display"] = _clamp_size(max(title * 1.3, scaled["display"]))
    spacing = base.spacing
    if spacings:
        spacing = replace(spacing, line_body=round(min(2.5, max(0.8, median(spacings))), 2))
    return replace(base, type_scale=replace(base.type_scale, **scaled), spacing=spacing)


def _grid_slides(  # noqa: PLR0913, PLR0917 - 页面字段与栅格计划一一对应
    title: str,
    subtitle: str,
    bullets: tuple[str, ...],
    images: list[tuple[str, str, str]],
    plan: list[tuple[str, int]],
    note: str = "",
) -> list[SlideSpec]:
    """按栅格映射生成图片页；首图写入 ``image``，同组其余进入 ``images``。"""
    pages: list[SlideSpec] = []
    offset = 0
    for order, (ratio, count) in enumerate(plan):
        chunk = images[offset : offset + count]
        offset += count
        paths = tuple(path for path, _alt, _focus in chunk)
        pages.append(
            SlideSpec(
                layout=LAYOUT_IMAGE,
                title=title,
                subtitle=subtitle if order == 0 else "",
                bullets=bullets if order == 0 else (),
                image=paths[0] if paths else "",
                images=paths if ratio else (),
                image_alt=chunk[0][1] if chunk else "",
                image_focus=chunk[0][2] if chunk else IMAGE_FOCUS_CENTER,
                image_ratio=ratio,
                note=note if order == 0 else "",
            )
        )
    return pages


def import_pptx(  # noqa: PLR0912 - 按内容类型分派，分支数随支持类型增长
    path: Path, *, assets_dir: Path | None = None, report: list[str] | None = None
) -> TemplateSpec:
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
    stats = ImportStats()
    font_sizes: list[float] = []
    line_spacings: list[float] = []
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
        tables = _tables_data(slide) if chart is None else []
        pictures = _picture_shapes(slide, presentation)
        extracted = _extract_pictures(pictures, assets_dir, stem) if assets_dir is not None else []
        image = extracted[0][0] if extracted else ""
        image_alt = extracted[0][1] if extracted else ""
        image_focus = extracted[0][2] if extracted else IMAGE_FOCUS_CENTER
        stats.found["文本"] += len(texts)
        stats.found["图片"] += len(pictures)
        stats.found["表格"] += len(tables)
        stats.found["图表"] += 1 if chart is not None else 0
        stats.found["备注"] += 1 if note else 0
        stats.kept["备注"] += 1 if note else 0
        _collect_typography(slide, font_sizes, line_spacings)
        if not texts:
            suffix = "；备注保留" if note else ""
            if extracted:
                plan = _image_plan(len(extracted)) if len(extracted) > 1 else [("", 1)]
                slides.extend(_grid_slides("", "", (), extracted, plan, note))
                stats.kept["图片"] += len(extracted)
                lines.append(f"第 {page} 页：保留图片 → 图片版式{suffix}")
            else:
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
                    image_focus=image_focus,
                    note=note,
                )
            )
            lines.append(f"第 {page} 页：保留封面{suffix}")
            stats.kept["文本"] += 1 + min(2, len(rest))
            stats.kept["图片"] += 1 if image else 0
            continue
        if index == len(slides_raw) - 1 and any(word in title.lower() for word in ("thank", "谢谢", "感谢")):
            slides.append(SlideSpec(layout=LAYOUT_CLOSING, title=title, subtitle=" ".join(rest[:2]), note=note))
            stats.kept["文本"] += 1 + min(2, len(rest))
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
            stats.kept["文本"] += 1
            stats.kept["图表"] += 1
        elif tables:
            pages, merged = _table_slides(title, tables, note)
            slides.extend(pages)
            stats.kept["文本"] += 1
            stats.kept["表格"] += len(tables)
            if len(tables) == 1:
                lines.append(f"第 {page} 页：保留表格 → 对比矩阵{suffix}")
            else:
                mode = "合并" if merged else f"拆 {len(pages)} 页"
                lines.append(f"第 {page} 页：保留 {len(tables)} 个表格 → 对比矩阵（{mode}）{suffix}")
        elif extracted:
            kept = _cap(LAYOUT_IMAGE, tuple(rest))
            if len(pictures) > 1 and len(extracted) == len(pictures) and _same_size(pictures):
                plan = _image_plan(len(extracted))
            else:
                largest = max(range(len(extracted)), key=lambda item: pictures[item].width * pictures[item].height)
                extracted = [extracted[largest]]
                plan = [("", 1)]
            slides.extend(_grid_slides(title, " ".join(rest[:1]), kept, extracted, plan, note))
            stats.kept["文本"] += min(len(texts), 1 + len(kept))
            stats.kept["图片"] += len(extracted)
            dropped = len(rest) - len(kept)
            detail = f"，{dropped} 条文本降级丢弃" if dropped > 0 else ""
            extra = f"，多图拆 {len(plan)} 页" if len(plan) > 1 else ""
            lines.append(f"第 {page} 页：保留图片 → 图片版式{extra}{detail}{suffix}")
        else:
            bullets = _cap(LAYOUT_BULLETS, tuple(rest or texts))
            slides.append(SlideSpec(layout=LAYOUT_BULLETS, title=title, bullets=bullets, note=note))
            stats.kept["文本"] += min(len(texts), 1 + len(bullets))
            lines.append(f"第 {page} 页：降级为要点（{len(bullets)} 条）{suffix}")
    if report is not None:
        lines.append("")
        lines.append(stats.summary())
    theme = Theme(
        style=STYLE_CUSTOM,
        palette=palette,
        heading_font=heading_font,
        body_font=body_font,
        decor=DECOR_ORBS,
        tokens=_typography_tokens(DesignTokens(), font_sizes, line_spacings),
    )
    spec = TemplateSpec(
        name=path.stem,
        description=f"从 {path.name} 导入",
        author="",
        aspect=aspect,
        theme=theme,
        slides=tuple(slides),
    )
    # 导入产物直接过一遍机械修复（截断超限文案、补图片替代文本），保证 validate 干净。
    fixed, _notes = fix_template(spec)
    return fixed
