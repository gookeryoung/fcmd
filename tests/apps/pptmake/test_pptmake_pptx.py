"""pptmake PPTX 渲染 / 主题解析测试（含边界图元与损坏文件）。"""

from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _design as design
from fcmd.apps.pptmake import _pptx


def test_draw_edge_primitives_and_save(tmp_path: Path) -> None:
    api = _pptx._api()
    presentation = api.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    theme = core.Theme()
    scheme = _pptx._scheme_map(theme)

    def draw(primitive: design.Primitive) -> None:
        _pptx._draw_primitive(slide, primitive, api, theme=theme, scheme=scheme, native=True)

    draw(design.Rect(0, 0, 0, 0))
    draw(design.Oval(0, 0, 0, 0))
    draw(design.Line(1, 1, 1, 1, "#000000"))
    draw(design.Text(0, 0, 0, 0, ("x",), 12, "#000000"))
    draw(design.Rect(10, 10, 100, 50, fill=""))
    draw(design.Rect(10, 10, 100, 50, line="#FF0000", line_width=0))
    draw(design.Rect(10, 10, 100, 50, fill="#00FF00", radius=8))
    draw(design.Rect(10, 10, 100, 50, line="#00FF00", line_width=2, radius=100))
    draw(
        design.Text(
            10,
            10,
            180,
            90,
            ("a", "b", "c"),
            12,
            "#000000",
            font="",
            bold=True,
            italic=True,
            align="center",
            anchor="middle",
            space_after=4,
        )
    )
    draw(design.Text(10, 10, 180, 90, ("bottom",), 12, "#000000", align="right", anchor="bottom"))
    draw(design.Text(10, 10, 180, 90, ("01",), 12, "#000000", field="slidenum", field_suffix=" / 3"))
    _pptx._draw_primitive(
        slide, design.Text(10, 10, 180, 90, ("flat",), 12, "#000000"), api, theme=theme, scheme={}, native=False
    )
    output = tmp_path / "edge.pptx"
    presentation.save(str(output))
    assert _pptx.pptx_slide_count(output) == 1


def test_native_export_writes_theme_and_fields(tmp_path: Path) -> None:
    template = core.default_template()
    template = replace(template, theme=replace(template.theme, body_font="Arial"))
    target = tmp_path / "native.pptx"
    _pptx.export_pptx(template, target)
    with zipfile.ZipFile(target) as archive:
        theme = archive.read("ppt/theme/theme1.xml").decode("utf-8")
        slides = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
    palette = template.theme.palette
    for color in (
        palette.background,
        palette.surface,
        palette.accent,
        palette.secondary,
        palette.text,
        palette.muted,
    ):
        assert color[1:] in theme
    assert template.theme.heading_font in theme
    assert "Arial" in theme
    assert 'type="slidenum"' in slides
    assert '<a:schemeClr val="bg1"/>' in slides
    assert '<a:srgbClr val="0B1020"/>' not in slides
    assert "+mj-lt" in slides and "+mn-lt" in slides


def test_flat_export_keeps_srgb_and_static_numbers(tmp_path: Path) -> None:
    template = core.default_template()
    target = tmp_path / "flat.pptx"
    _pptx.export_pptx(template, target, native=False)
    with zipfile.ZipFile(target) as archive:
        theme = archive.read("ppt/theme/theme1.xml").decode("utf-8")
        slides = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
    assert template.theme.palette.accent[1:] not in theme
    assert '<a:schemeClr val="bg1"/>' not in slides
    assert '<a:srgbClr val="0B1020"/>' in slides
    assert 'type="slidenum"' not in slides
    assert "02 / 13" in slides


def test_cover_fit_crops_image_both_ratios(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    from PIL import Image

    wide = tmp_path / "wide.png"
    tall = tmp_path / "tall.png"
    Image.new("RGB", (300, 100), "#3366FF").save(wide)
    Image.new("RGB", (100, 300), "#FF6633").save(tall)
    template = core.new_template(
        slides=(
            core.SlideSpec(layout=core.LAYOUT_IMAGE, title="宽图", image=str(wide)),
            core.SlideSpec(layout=core.LAYOUT_IMAGE, title="高图", image=str(tall)),
        )
    )
    target = tmp_path / "cover.pptx"
    assert _pptx.export_pptx(template, target) == 2
    with zipfile.ZipFile(target) as archive:
        assert any(name.startswith("ppt/media/") for name in archive.namelist())


def test_export_embeds_picture_and_paragraph_levels(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    from PIL import Image

    image_path = tmp_path / "hero.png"
    Image.new("RGB", (200, 100), "#3366FF").save(image_path)
    template = core.new_template(
        slides=(
            core.SlideSpec(
                layout=core.LAYOUT_IMAGE,
                title="主视觉",
                image=str(image_path),
                image_fit=core.IMAGE_FIT_CONTAIN,
            ),
            core.SlideSpec(layout=core.LAYOUT_BULLETS, title="层级", bullets=("顶层", "  - 子项")),
        )
    )
    target = tmp_path / "image.pptx"
    assert _pptx.export_pptx(template, target) == 2
    api = _pptx._api()
    presentation = api.Presentation(str(target))
    slides = list(presentation.slides)
    assert any(shape.shape_type == 13 for shape in slides[0].shapes)  # MSO_SHAPE_TYPE.PICTURE
    levels = [
        paragraph.level
        for shape in slides[1].shapes
        if shape.has_text_frame
        for paragraph in shape.text_frame.paragraphs
        if paragraph.text in ("顶层", "子项")
    ]
    assert 1 in levels


def test_export_missing_picture_reports_error(tmp_path: Path) -> None:
    template = core.new_template(
        slides=(core.SlideSpec(layout=core.LAYOUT_IMAGE, title="缺图", image=str(tmp_path / "missing.png")),)
    )
    with pytest.raises(core.TemplateError, match="图片不存在"):
        _pptx.export_pptx(template, tmp_path / "missing.pptx")


def test_export_reports_save_error(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(core.TemplateError, match="保存 PPTX 失败"):
        _pptx.export_pptx(core.default_template(), blocker / "demo.pptx")


def test_import_empty_and_closing_slides(tmp_path: Path) -> None:
    api = _pptx._api()
    presentation = api.Presentation()
    blank = presentation.slides.add_slide(presentation.slide_layouts[6])
    blank.shapes.add_textbox(0, 0, 100, 100)  # 空文本框，不产生文本
    closing = presentation.slides.add_slide(presentation.slide_layouts[6])
    closing.shapes.add_textbox(0, 0, 100, 100).text_frame.text = "THANK YOU"
    source = tmp_path / "mixed.pptx"
    presentation.save(str(source))
    template = _pptx.import_pptx(source)
    assert template.slides[0].layout == core.LAYOUT_BULLETS
    assert template.slides[-1].layout == core.LAYOUT_CLOSING
    assert template.name == "mixed"


@pytest.mark.parametrize("suffix", [".pptx", ".txt"])
def test_import_and_count_report_invalid_file(tmp_path: Path, suffix: str) -> None:
    broken = tmp_path / f"broken{suffix}"
    broken.write_text("not a pptx", encoding="utf-8")
    with pytest.raises(core.TemplateError, match="读取 PPTX 失败"):
        _pptx.import_pptx(broken)
    with pytest.raises(core.TemplateError, match="读取 PPTX 失败"):
        _pptx.pptx_slide_count(broken)


def test_theme_color_helpers() -> None:
    assert _pptx._theme_color(None, "lt1") is None
    root = ElementTree.fromstring(
        '<a:clrScheme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
        '<a:dk1><a:sysClr val="windowText" lastClr="101010"/></a:dk1>'
        '<a:accent1><a:sysClr val="x"/></a:accent1>'
        "</a:clrScheme>"
    )
    assert _pptx._theme_color(root, "lt1") == "#FFFFFF"
    assert _pptx._theme_color(root, "dk1") == "#101010"
    assert _pptx._theme_color(root, "accent1") is None
    assert _pptx._theme_color(root, "missing") is None

    assert _pptx._first_typeface(None) == ""
    fonts = ElementTree.fromstring(
        '<a:majorFont xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:latin typeface=""/><a:ea typeface="标题字体"/></a:majorFont>'
    )
    assert _pptx._first_typeface(fonts) == "标题字体"
    empty_fonts = ElementTree.fromstring(
        '<a:majorFont xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:latin typeface=""/></a:majorFont>'
    )
    assert _pptx._first_typeface(empty_fonts) == ""


def test_safe_color() -> None:
    assert _pptx._safe_color(None, "#123456") == "#123456"
    assert _pptx._safe_color("bad", "#123456") == "#123456"
    assert _pptx._safe_color("aabbcc", "#123456") == "#AABBCC"


def test_read_theme_from_custom_package(tmp_path: Path) -> None:
    package = tmp_path / "theme.pptx"
    theme_xml = (
        '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<a:themeElements>"
        '<a:clrScheme name="custom">'
        '<a:dk1><a:sysClr val="windowText" lastClr="101010"/></a:dk1>'
        '<a:lt1><a:srgbClr val="FAFAFA"/></a:lt1>'
        '<a:accent1><a:srgbClr val="112233"/></a:accent1>'
        '<a:accent2><a:srgbClr val="445566"/></a:accent2>'
        "</a:clrScheme>"
        '<a:fontScheme name="custom">'
        '<a:majorFont><a:latin typeface=""/><a:ea typeface="标题字体"/></a:majorFont>'
        '<a:minorFont><a:latin typeface="BodyFont"/></a:minorFont>'
        "</a:fontScheme>"
        "</a:themeElements>"
        "</a:theme>"
    )
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("ppt/theme/theme1.xml", theme_xml)
    palette, heading_font, body_font = _pptx._read_theme(package)
    assert palette.background == "#FAFAFA"
    assert palette.text == "#101010"
    assert palette.accent == "#112233"
    assert palette.secondary == "#445566"
    assert heading_font == "标题字体"
    assert body_font == "BodyFont"


def test_read_theme_falls_back_for_missing_or_broken_package(tmp_path: Path) -> None:
    # 没有 theme 的 zip
    empty_zip = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty_zip, "w") as archive:
        archive.writestr("ppt/presentation.xml", "<x/>")
    assert _pptx._read_theme(empty_zip) == (core.Palette(), core.DEFAULT_HEADING_FONT, core.DEFAULT_BODY_FONT)
    # 不是 zip
    broken = tmp_path / "broken.pptx"
    broken.write_text("x", encoding="utf-8")
    assert _pptx._read_theme(broken) == (core.Palette(), core.DEFAULT_HEADING_FONT, core.DEFAULT_BODY_FONT)
