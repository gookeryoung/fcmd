"""pptmake 核心模型测试：校验、序列化、持久化与摘要。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.pptmake import _core as core


def test_hex_color_helpers() -> None:
    assert core.is_hex_color("#a1B2c3")
    assert core.is_hex_color("A1B2C3")
    assert not core.is_hex_color("red")
    assert not core.is_hex_color(None)
    assert core.normalize_hex("#a1b2c3") == "#A1B2C3"
    assert core.normalize_hex("a1b2c3") == "#A1B2C3"
    with pytest.raises(core.TemplateError, match="需为 #RRGGBB"):
        core.normalize_hex("abc")


def test_mix_hex_and_luminance() -> None:
    assert core.mix_hex("#000000", "#FFFFFF", 0.5) == "#808080"
    assert core.mix_hex("#000000", "#FFFFFF", -1) == "#000000"
    assert core.mix_hex("#000000", "#FFFFFF", 2) == "#FFFFFF"
    assert core.relative_luminance("#FFFFFF") > core.relative_luminance("#000000")
    assert core.relative_luminance("#808080") > 0


def test_normalize_enums() -> None:
    assert core.normalize_aspect("16:9") == core.ASPECT_16_9
    assert core.normalize_aspect("4:3") == core.ASPECT_4_3
    with pytest.raises(core.TemplateError, match="画幅需为"):
        core.normalize_aspect("21:9")

    for layout in core.LAYOUTS:
        assert core.normalize_layout(layout) == layout
    with pytest.raises(core.TemplateError, match="未知布局"):
        core.normalize_layout("timeline_x")

    assert core.normalize_decor("grid") == core.DECOR_GRID
    assert core.normalize_decor("unknown") == core.DECOR_ORBS

    for style in core.STYLES:
        assert core.normalize_style(style.upper()) == style
    assert core.normalize_style("custom") == core.STYLE_CUSTOM
    with pytest.raises(core.TemplateError, match="未知风格"):
        core.normalize_style("neon")


def test_palette_and_labels() -> None:
    assert core.palette_for_style(core.STYLE_PORCELAIN) == core.Palette(
        background="#F7F4EE",
        surface="#FFFFFF",
        accent="#1F8A7A",
        secondary="#D97757",
        text="#1D2A2A",
        muted="#6B7A78",
    )
    assert core.palette_for_style("missing") == core.palette_for_style(core.STYLE_AURORA)
    assert core.style_label(core.STYLE_AURORA) == core.STYLE_TEXT[core.STYLE_AURORA]
    assert core.style_label("unknown") == "unknown"
    assert core.layout_label(core.LAYOUT_COVER) == "封面"
    assert core.layout_label("unknown") == "unknown"


def test_new_slide_and_default_template() -> None:
    for layout in core.LAYOUTS:
        slide = core.new_slide(layout)
        assert slide.layout == layout
        assert slide.title
    with pytest.raises(core.TemplateError):
        core.new_slide("unknown")

    template = core.default_template(style=core.STYLE_CORAL, aspect=core.ASPECT_4_3)
    assert template.name == "品牌演示模板"
    assert template.aspect == core.ASPECT_4_3
    assert template.theme.style == core.STYLE_CORAL
    assert len(template.slides) == len(core.LAYOUTS)
    assert template.layouts == core.LAYOUTS


def test_new_template_custom_and_empty_slides() -> None:
    template = core.new_template(
        name="  ",
        style=core.STYLE_CUSTOM,
        aspect=core.ASPECT_4_3,
        decor="unknown",
        slides=(),
        description="d",
        author="a",
    )
    assert template.name == "未命名模板"
    assert template.theme.style == core.STYLE_CUSTOM
    assert template.theme.palette == core.palette_for_style(core.STYLE_AURORA)
    assert template.theme.decor == core.DECOR_ORBS
    assert template.slides == ()
    assert template.description == "d"
    assert template.author == "a"


def test_mapping_roundtrip() -> None:
    template = core.default_template()
    mapping = core.template_to_mapping(template)
    assert mapping["app"] == "pptmake"
    assert mapping["version"] == core.TEMPLATE_VERSION
    assert len(mapping["slides"]) == len(template.slides)
    assert core.template_from_mapping(mapping) == template


def test_mapping_tolerates_missing_optional_fields() -> None:
    template = core.template_from_mapping(
        {
            "name": "手写模板",
            "theme": None,
            "slides": None,
        }
    )
    assert template.name == "手写模板"
    assert template.aspect == core.ASPECT_16_9
    assert template.layouts == core.LAYOUTS
    assert template.slides == ()
    assert template.theme.palette == core.palette_for_style(core.STYLE_AURORA)


def test_mapping_overrides_palette_and_fonts() -> None:
    template = core.template_from_mapping(
        {
            "theme": {
                "style": "graphite",
                "palette": {"accent": "ff0000", "text": "#FFFFFF"},
                "heading_font": "",
                "body_font": "  ",
                "decor": "frame",
            },
            "layouts": ["cover", "cover", "bullets"],
            "slides": [
                {
                    "layout": "cover",
                    "title": 123,
                    "subtitle": None,
                    "bullets": None,
                    "note": 456,
                    "accent": "",
                },
                {"layout": "bullets", "bullets": ("a", 2), "accent": "00ff00"},
            ],
        }
    )
    assert template.theme.palette.accent == "#FF0000"
    assert template.theme.palette.text == "#FFFFFF"
    assert template.theme.heading_font == core.DEFAULT_HEADING_FONT
    assert template.theme.body_font == core.DEFAULT_BODY_FONT
    assert template.theme.decor == core.DECOR_FRAME
    assert template.layouts == (core.LAYOUT_COVER, core.LAYOUT_BULLETS)
    assert template.slides[0].title == "123"
    assert template.slides[0].subtitle == "None"
    assert template.slides[0].note == "456"
    assert template.slides[0].bullets == ()
    assert template.slides[1].bullets == ("a", "2")
    assert template.slides[1].accent == "#00FF00"


def test_mapping_empty_layouts_falls_back() -> None:
    template = core.template_from_mapping({"layouts": []})
    assert template.layouts == core.LAYOUTS


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "根节点"),
        ({"theme": []}, "theme 需为对象"),
        ({"theme": {"palette": []}}, r"theme\.palette 需为对象"),
        ({"theme": {"palette": {"accent": "bad"}}}, r"theme\.palette\.强调"),
        ({"layouts": "cover"}, "layouts 需为布局名数组"),
        ({"layouts": ["unknown"]}, "未知布局"),
        ({"slides": {}}, "slides 需为对象数组"),
        ({"slides": [1]}, r"slides\[0\] 需为对象"),
        ({"slides": [{"bullets": "x"}]}, r"slides\[0\]\.bullets 需为字符串数组"),
        ({"slides": [{"accent": "bad"}]}, r"slides\[0\]\.accent"),
        ({"aspect": "21:9"}, "画幅需为"),
        ({"theme": {"style": "neon"}}, "未知风格"),
    ],
)
def test_mapping_rejects_invalid_values(raw: object, message: str) -> None:
    with pytest.raises(core.TemplateError, match=message):
        core.template_from_mapping(raw)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    template = core.default_template()
    target = tmp_path / "nested" / "demo.pptmtpl.json"
    core.save_template(template, target)
    assert target.is_file()
    assert core.load_template(target) == template
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["app"] == "pptmake"


def test_load_reports_missing_and_broken(tmp_path: Path) -> None:
    with pytest.raises(core.TemplateError, match="读取模板失败"):
        core.load_template(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(core.TemplateError, match="JSON 解析失败"):
        core.load_template(broken)


def test_save_reports_io_error(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(core.TemplateError, match="保存模板失败"):
        core.save_template(core.default_template(), blocker / "demo.json")


def test_default_paths() -> None:
    assert core.default_pptx_path(Path("demo.pptmtpl.json")) == Path("demo.pptx")
    assert core.default_pptx_path(Path("demo.JSON")) == Path("demo.pptx")
    assert core.default_pptx_path(Path("demo.template")) == Path("demo.pptx")
    assert core.default_template_path(Path("demo.pptx")) == Path("demo.pptmtpl.json")


def test_summary_lines() -> None:
    template = core.new_template(
        name="摘要模板",
        slides=(core.new_slide(core.LAYOUT_COVER),),
        description="一句话说明",
        author="作者",
    )
    text = "\n".join(core.summary_lines(template))
    assert "摘要模板" in text
    assert "作者" in text
    assert "一句话说明" in text
    assert "[封面]" in text
    assert "页面：1 页" in text


def test_summary_lines_without_author_or_description() -> None:
    text = "\n".join(core.summary_lines(core.new_template(name="空模板", slides=())))
    assert "作者：-" in text
    assert "页面：0 页" in text


def test_replace_slide() -> None:
    template = core.default_template()
    updated = core.replace_slide(template, 0, core.new_slide(core.LAYOUT_QUOTE))
    assert updated.slides[0].layout == core.LAYOUT_QUOTE
    assert template.slides[0].layout == core.LAYOUT_COVER
    with pytest.raises(IndexError):
        core.replace_slide(template, 99, core.new_slide(core.LAYOUT_QUOTE))
