"""pptmake PPTX 渲染 / 主题解析测试（含边界图元与损坏文件）。"""

from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from fcmd.apps.pptmake import _base, _base_asset, _pptx, _pptx_import
from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _design as design

_NS_P = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}


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
    assert f"02 / {len(core.LAYOUTS)}" in slides


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


_EMU_PER_UNIT = 9525


def test_picture_alt_focus_radius_and_scrim(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    from PIL import Image

    path = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), "#3366FF").save(path)
    api = _pptx._api()
    presentation = api.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    primitive = design.Picture(
        96,
        96,
        400,
        400,
        str(path),
        alt="产品主视觉",
        focus="left",
        radius=24,
        overlay=0.4,
    )
    _pptx._draw_picture(slide, primitive, api, scheme={})
    picture = next(shape for shape in slide.shapes if shape.shape_type == 13)
    assert picture.crop_left == pytest.approx(0.0)
    assert picture.crop_right > 0
    assert picture._element.nvPicPr.cNvPr.get("descr") == "产品主视觉"
    assert 'prst="roundRect"' in picture._element.xml
    scrim = next(shape for shape in slide.shapes if shape.shape_type != 13)
    assert "<a:alpha" in scrim._element.xml
    assert 'val="40000"' in scrim._element.xml


def test_background_image_exports_full_page_picture(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    from PIL import Image

    path = tmp_path / "hero.png"
    Image.new("RGB", (320, 160), "#0B1020").save(path)
    template = core.new_template(
        slides=(
            core.SlideSpec(
                layout=core.LAYOUT_SECTION,
                title="图片底板",
                image=str(path),
                image_alt="背景",
                background=core.BACKGROUND_IMAGE,
            ),
        )
    )
    target = tmp_path / "background.pptx"
    _pptx.export_pptx(template, target)
    with zipfile.ZipFile(target) as archive:
        assert any(name.startswith("ppt/media/") for name in archive.namelist())
        slide_xml = archive.read("ppt/slides/slide1.xml").decode("utf-8")
    assert 'descr="背景"' in slide_xml
    assert "<a:alpha" in slide_xml


def test_export_writes_native_table_with_matching_geometry(tmp_path: Path) -> None:
    template = core.default_template()
    target = tmp_path / "table.pptx"
    assert _pptx.export_pptx(template, target) == len(template.slides)
    presentation = _pptx._api().Presentation(str(target))
    frame = next(shape for shape in list(presentation.slides)[10].shapes if shape.has_table)
    table = frame.table
    primitive = next(
        item for item in design.build_draw_list(template.slides[10], template) if isinstance(item, design.Table)
    )
    assert len(table.columns) == len(primitive.col_widths)
    assert len(table.rows) == len(primitive.row_heights)
    for index, width in enumerate(primitive.col_widths):
        assert table.columns[index].width / _EMU_PER_UNIT == pytest.approx(width, abs=1.0)
    for index, height in enumerate(primitive.row_heights):
        assert table.rows[index].height / _EMU_PER_UNIT == pytest.approx(height, abs=1.0)
    assert frame.width / _EMU_PER_UNIT == pytest.approx(primitive.w, abs=1.0)
    assert frame.height / _EMU_PER_UNIT == pytest.approx(primitive.h, abs=1.0)
    assert table.first_row is True
    assert table.horz_banding is True
    assert table.cell(0, 0).text == "对比维度"


def test_export_writes_editable_chart_with_data(tmp_path: Path) -> None:
    template = core.default_template()
    target = tmp_path / "chart.pptx"
    _pptx.export_pptx(template, target)
    presentation = _pptx._api().Presentation(str(target))
    frame = next(shape for shape in list(presentation.slides)[11].shapes if shape.has_chart)
    chart = frame.chart
    plot = chart.plots[0]
    assert list(plot.categories) == ["Q1", "Q2", "Q3", "Q4"]
    assert list(chart.series[0].values) == [42, 68, 54, 86]
    assert chart.has_legend is True
    assert plot.has_data_labels is True
    primitive = next(
        item for item in design.build_draw_list(template.slides[11], template) if isinstance(item, design.Chart)
    )
    assert frame.width / _EMU_PER_UNIT == pytest.approx(primitive.w, abs=1.0)
    assert frame.height / _EMU_PER_UNIT == pytest.approx(primitive.h, abs=1.0)


def test_export_chart_types_and_negative_values(tmp_path: Path) -> None:
    template = core.new_template(
        slides=(
            core.SlideSpec(layout=core.LAYOUT_CHART, bullets=("A|1", "B|3"), chart_type=core.CHART_PIE),
            core.SlideSpec(layout=core.LAYOUT_CHART, bullets=("A|1", "B|3"), chart_type=core.CHART_LINE),
            core.SlideSpec(layout=core.LAYOUT_CHART, bullets=("A|-4", "B|2"), value_format="0"),
            core.SlideSpec(layout=core.LAYOUT_CHART, bullets=("无|暂无",)),
        )
    )
    target = tmp_path / "charts.pptx"
    _pptx.export_pptx(template, target)
    presentation = _pptx._api().Presentation(str(target))
    charts = [shape.chart for slide in presentation.slides for shape in slide.shapes if shape.has_chart]
    assert len(charts) == 3
    assert list(charts[2].series[0].values) == [-4, 2]
    assert charts[0].plots[0].categories is not None


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


def test_import_upgrades_picture_table_chart_and_notes(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    from PIL import Image
    from pptx.chart.data import CategoryChartData

    api = _pptx._api()
    presentation = api.Presentation()
    cover = presentation.slides.add_slide(presentation.slide_layouts[6])
    cover.shapes.add_textbox(0, 0, 400, 60).text_frame.text = "年度汇报"
    image_path = tmp_path / "pic.png"
    Image.new("RGB", (600, 400), "#3366FF").save(image_path)
    image_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    image_slide.shapes.add_textbox(0, 0, 400, 60).text_frame.text = "主视觉"
    image_slide.shapes.add_picture(str(image_path), 0, 100, presentation.slide_width, presentation.slide_height)
    image_slide.notes_slide.notes_text_frame.text = "备注：替换为品牌主图"
    table_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    table_slide.shapes.add_textbox(0, 0, 400, 60).text_frame.text = "方案对比"
    table = table_slide.shapes.add_table(3, 3, 0, 100, presentation.slide_width, 2000000).table
    rows = (("指标", "现状", "目标"), ("速度", "小时级", "分钟级"), ("成本", "较高", "更低"))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            table.cell(row_index, column_index).text = value
    chart_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    chart_slide.shapes.add_textbox(0, 0, 400, 60).text_frame.text = "增长趋势"
    data = CategoryChartData()
    data.categories = ["Q1", "Q2", "Q3"]
    data.add_series("收入", (42, 68, 54))
    chart_slide.shapes.add_chart(
        api.XL_CHART_TYPE.COLUMN_CLUSTERED,
        0,
        100,
        presentation.slide_width,
        3000000,
        data,
    )
    source = tmp_path / "rich.pptx"
    presentation.save(str(source))
    assets = tmp_path / "rich.assets"
    report: list[str] = []
    template = _pptx.import_pptx(source, assets_dir=assets, report=report)
    assert [slide.layout for slide in template.slides] == [
        core.LAYOUT_COVER,
        core.LAYOUT_IMAGE,
        core.LAYOUT_COMPARISON,
        core.LAYOUT_CHART,
    ]
    image_spec = template.slides[1]
    assert image_spec.image.startswith("rich.assets/")
    assert (assets / image_spec.image.split("/", 1)[1]).is_file()
    assert image_spec.note == "备注：替换为品牌主图"
    assert template.slides[2].subtitle == "现状|目标"
    assert template.slides[2].bullets[0] == "速度|小时级|分钟级"
    assert template.slides[3].bullets == ("Q1|42", "Q2|68", "Q3|54")
    assert len(report) == 4
    assert "图片" in report[1]


def test_export_potx_marks_template_content_type(tmp_path: Path) -> None:
    target = tmp_path / "brand.potx"
    assert _pptx.export_potx(core.default_template(), target) == len(core.LAYOUTS)
    with zipfile.ZipFile(target) as archive:
        content_types = archive.read("[Content_Types].xml").decode("utf-8")
    assert "presentationml.template.main+xml" in content_types
    assert "presentationml.presentation.main+xml" not in content_types


def test_export_resolves_relative_images_from_base_dir(tmp_path: Path) -> None:
    pytest.importorskip("PIL.Image")
    from PIL import Image

    assets = tmp_path / "deck.assets"
    assets.mkdir()
    Image.new("RGB", (320, 160), "#0B1020").save(assets / "hero.png")
    template = core.new_template(
        slides=(core.SlideSpec(layout=core.LAYOUT_IMAGE, title="图", image="deck.assets/hero.png"),),
    )
    target = tmp_path / "out.pptx"
    assert _pptx.export_pptx(template, target, base_dir=tmp_path) == 1
    with zipfile.ZipFile(target) as archive:
        assert any(name.startswith("ppt/media/") for name in archive.namelist())


@pytest.mark.parametrize("suffix", [".pptx", ".txt"])
def test_import_and_count_report_invalid_file(tmp_path: Path, suffix: str) -> None:
    broken = tmp_path / f"broken{suffix}"
    broken.write_text("not a pptx", encoding="utf-8")
    with pytest.raises(core.TemplateError, match="读取 PPTX 失败"):
        _pptx.import_pptx(broken)
    with pytest.raises(core.TemplateError, match="读取 PPTX 失败"):
        _pptx.pptx_slide_count(broken)


def test_theme_color_helpers() -> None:
    assert _pptx_import._theme_color(None, "lt1") is None
    root = ElementTree.fromstring(
        '<a:clrScheme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
        '<a:dk1><a:sysClr val="windowText" lastClr="101010"/></a:dk1>'
        '<a:accent1><a:sysClr val="x"/></a:accent1>'
        "</a:clrScheme>"
    )
    assert _pptx_import._theme_color(root, "lt1") == "#FFFFFF"
    assert _pptx_import._theme_color(root, "dk1") == "#101010"
    assert _pptx_import._theme_color(root, "accent1") is None
    assert _pptx_import._theme_color(root, "missing") is None

    assert _pptx_import._first_typeface(None) == ""
    fonts = ElementTree.fromstring(
        '<a:majorFont xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:latin typeface=""/><a:ea typeface="标题字体"/></a:majorFont>'
    )
    assert _pptx_import._first_typeface(fonts) == "标题字体"
    empty_fonts = ElementTree.fromstring(
        '<a:majorFont xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:latin typeface=""/></a:majorFont>'
    )
    assert _pptx_import._first_typeface(empty_fonts) == ""


def test_safe_color() -> None:
    assert _pptx_import._safe_color(None, "#123456") == "#123456"
    assert _pptx_import._safe_color("bad", "#123456") == "#123456"
    assert _pptx_import._safe_color("aabbcc", "#123456") == "#AABBCC"


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
    palette, heading_font, body_font = _pptx_import._read_theme(package)
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
    assert _pptx_import._read_theme(empty_zip) == (core.Palette(), core.DEFAULT_HEADING_FONT, core.DEFAULT_BODY_FONT)
    # 不是 zip
    broken = tmp_path / "broken.pptx"
    broken.write_text("x", encoding="utf-8")
    assert _pptx_import._read_theme(broken) == (core.Palette(), core.DEFAULT_HEADING_FONT, core.DEFAULT_BODY_FONT)


def test_base_asset_layouts_placeholders_and_size() -> None:
    path = _base.base_path(core.ASPECT_16_9)
    assert path is not None and path.is_file()
    assert _base.base_path(core.ASPECT_4_3) is None
    presentation = _pptx._api().Presentation(str(path))
    assert (presentation.slide_width, presentation.slide_height) == (12192000, 6858000)
    assert [layout.name for layout in presentation.slide_layouts] == [core.LAYOUT_TEXT[key] for key in core.LAYOUTS]
    for layout in presentation.slide_layouts:
        indices = {placeholder.placeholder_format.idx for placeholder in layout.placeholders}
        assert indices == {0, 1}
    quote_layout = _base.layout_for(presentation, core.LAYOUT_QUOTE)
    assert quote_layout is not None
    assert quote_layout.name == core.LAYOUT_TEXT[core.LAYOUT_QUOTE]
    assert _base.layout_for(presentation, "unknown") is None
    with zipfile.ZipFile(path) as archive:
        master = ElementTree.fromstring(archive.read("ppt/slideMasters/slideMaster1.xml"))
        theme = archive.read("ppt/theme/theme1.xml").decode("utf-8")
    assert len(master.findall(".//p:sldLayoutIdLst/p:sldLayoutId", _NS_P)) == len(core.LAYOUTS)
    assert core.default_template().theme.palette.accent[1:] in theme


def test_base_asset_builder_regenerates_valid_package(tmp_path: Path) -> None:
    target = tmp_path / "base.pptx"
    assert _base_asset.build(target) == len(core.LAYOUTS)
    presentation = _pptx._api().Presentation(str(target))
    assert len(presentation.slide_layouts) == len(core.LAYOUTS)
    assert (presentation.slide_width, presentation.slide_height) == (12192000, 6858000)


def test_native_export_writes_titles_into_layout_placeholders(tmp_path: Path) -> None:
    template = core.default_template()
    target = tmp_path / "native-base.pptx"
    assert _pptx.export_pptx(template, target) == len(template.slides)
    presentation = _pptx._api().Presentation(str(target))
    assert len(presentation.slide_layouts) == len(core.LAYOUTS)
    assert (presentation.slide_width, presentation.slide_height) == (12192000, 6858000)
    for spec, slide in zip(template.slides, presentation.slides, strict=True):
        heading = _base._heading(spec, template)
        assert slide.shapes.title is not None
        assert slide.shapes.title.text == heading.title
        subtitles = [ph.text for ph in slide.placeholders if ph.placeholder_format.idx == 1]
        assert subtitles == [heading.subtitle]


def test_native_export_draws_nothing_twice_for_headings(tmp_path: Path) -> None:
    template = core.new_template(slides=(core.SlideSpec(layout=core.LAYOUT_COVER, title="只出现一次"),))
    target = tmp_path / "once.pptx"
    _pptx.export_pptx(template, target)
    presentation = _pptx._api().Presentation(str(target))
    slide = next(iter(presentation.slides))
    texts = [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame]
    assert texts.count("只出现一次") == 1


def test_flat_export_keeps_blank_layout_without_placeholders(tmp_path: Path) -> None:
    target = tmp_path / "flat-base.pptx"
    _pptx.export_pptx(core.default_template(), target, native=False)
    presentation = _pptx._api().Presentation(str(target))
    for slide in presentation.slides:
        assert slide.shapes.title is None
        assert len(slide.placeholders) == 0


def test_export_unknown_layout_falls_back_to_floating_text(tmp_path: Path) -> None:
    template = core.new_template(slides=(core.SlideSpec(layout="unknown", title="未知版式回退"),))
    target = tmp_path / "unknown.pptx"
    _pptx.export_pptx(template, target)
    slide = next(iter(_pptx._api().Presentation(str(target)).slides))
    assert slide.shapes.title is None
    texts = [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame]
    assert "未知版式回退" in texts


def test_native_roundtrip_keeps_title_as_first_text(tmp_path: Path) -> None:
    template = core.new_template(
        slides=(core.SlideSpec(layout=core.LAYOUT_BULLETS, title="回环标题", bullets=("要点一",)),)
    )
    target = tmp_path / "roundtrip.pptx"
    _pptx.export_pptx(template, target)
    imported = _pptx.import_pptx(target)
    assert imported.slides[0].title == "回环标题"


def test_flat_export_geometry_matches_draw_list(tmp_path: Path) -> None:
    """golden：导出后的文本框几何与预览绘制列表一致（阈值 1 设计单位）。"""
    slide_spec = core.SlideSpec(
        layout=core.LAYOUT_CARDS,
        title="几何一致性",
        bullets=("成本|下降一成", "交付|提前两周", "质量|零缺陷"),
    )
    template = core.new_template(slides=(slide_spec,))
    target = tmp_path / "geometry.pptx"
    _pptx.export_pptx(template, target, native=False)

    primitives = design.build_draw_list(slide_spec, template, page_number=1, page_total=1, headings=True)
    expected = [item for item in primitives if isinstance(item, design.Text) and item.w > 0 and item.h > 0]
    shapes = pytest.importorskip("pptx.enum.shapes")
    api = _pptx._api()
    slide = api.Presentation(str(target)).slides[0]
    boxes = [shape for shape in slide.shapes if shape.shape_type == shapes.MSO_SHAPE_TYPE.TEXT_BOX]
    assert len(boxes) == len(expected)
    # 1 设计单位 = 1/96 英寸 = 9525 EMU。
    emu_per_unit = api.Inches(1) / 96
    for box, item in zip(boxes, expected, strict=True):
        assert abs(box.left / emu_per_unit - item.x) <= 1
        assert abs(box.top / emu_per_unit - item.y) <= 1
        assert abs(box.width / emu_per_unit - item.w) <= 1
        assert abs(box.height / emu_per_unit - item.h) <= 1
