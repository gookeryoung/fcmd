"""pptmake 版式引擎测试：十三种布局、装饰、边界与坐标口径。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _design as design


def _template(
    *,
    style: str = core.STYLE_AURORA,
    aspect: str = core.ASPECT_16_9,
    decor: str = core.DECOR_ORBS,
) -> core.TemplateSpec:
    return core.new_template(name="测试模板", style=style, aspect=aspect, decor=decor, slides=())


def test_design_size() -> None:
    assert design.design_size(core.ASPECT_16_9) == (1280, 720)
    assert design.design_size(core.ASPECT_4_3) == (960, 720)
    assert design.design_size("unknown") == (1280, 720)


def test_parse_pair() -> None:
    assert design._parse_pair("98%|交付准时率") == ("98%", "交付准时率")
    assert design._parse_pair("只有值") == ("只有值", "")
    assert design._parse_pair("|说明") == ("—", "说明")


def _with_tokens(tokens: core.DesignTokens | None = None) -> core.TemplateSpec:
    """在默认模板上替换设计令牌，用于验证版式是否真正消费令牌。"""
    template = _template()
    return replace(template, theme=replace(template.theme, tokens=tokens or core.DesignTokens()))


def test_bullets_use_type_scale_body() -> None:
    primitives = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_BULLETS, title="字号", bullets=("短要点",)),
        _with_tokens(),
    )
    assert any(isinstance(item, design.Text) and item.size == 20 for item in primitives)

    larger = _with_tokens(replace(core.DesignTokens(), type_scale=replace(core.DesignTokens().type_scale, body=26)))
    primitives = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_BULLETS, bullets=("短要点",)),
        larger,
    )
    assert any(isinstance(item, design.Text) and item.size == 26 for item in primitives)


def test_density_changes_agenda_row_step() -> None:
    slide = core.SlideSpec(layout=core.LAYOUT_AGENDA, title="目录", bullets=("甲", "乙", "丙", "丁"))

    def row_gap(template: core.TemplateSpec) -> float:
        positions = sorted(
            item.y
            for item in design.build_draw_list(slide, template)
            if isinstance(item, design.Text) and item.paragraphs and item.paragraphs[0] in ("甲", "乙")
        )
        return positions[1] - positions[0]

    base = core.DesignTokens()
    assert row_gap(_with_tokens(replace(base, density="compact"))) < row_gap(
        _with_tokens(replace(base, density="spacious"))
    )


def test_margins_follow_spacing_tokens() -> None:
    base = core.DesignTokens()
    tokens = replace(base, spacing=replace(base.spacing, margin_x=0.2, margin_y=0.2))
    primitives = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_BULLETS, title="边距"),
        _with_tokens(tokens),
    )
    title = next(item for item in primitives if isinstance(item, design.Text) and item.paragraphs == ("边距",))
    assert title.x == round(1280 * 0.2)
    assert title.y == round(720 * 0.2)


def test_decor_intensity_zero_fades_ornaments() -> None:
    template = _with_tokens(replace(core.DesignTokens(), decor_intensity=0.0))
    primitives = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_COVER), template)
    ovals = [item for item in primitives if isinstance(item, design.Oval)]
    assert ovals
    assert all(item.fill == template.theme.palette.background for item in ovals)


def test_card_radius_follows_radius_token() -> None:
    base = core.DesignTokens()
    template = _with_tokens(replace(base, radius=replace(base.radius, card=4)))
    primitives = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_TWO_COLUMN), template)
    cards = [
        item for item in primitives if isinstance(item, design.Rect) and item.fill == template.theme.palette.surface
    ]
    assert cards
    assert all(item.radius == 4 for item in cards)


def test_all_layouts_render_with_default_content() -> None:
    template = core.default_template()
    for slide in template.slides:
        primitives = design.build_draw_list(slide, template)
        assert isinstance(primitives[0], design.Rect)
        assert primitives[0].x == 0
        assert primitives[0].y == 0
        assert primitives[0].w == 1280
        assert primitives[0].h == 720
        assert any(isinstance(item, design.Text) for item in primitives)
        assert any(isinstance(item, (design.Rect, design.Oval, design.Line)) for item in primitives)


@pytest.mark.parametrize("layout", core.LAYOUTS)
def test_layouts_render_with_empty_content(layout: str) -> None:
    template = _template()
    slide = core.SlideSpec(layout=layout)
    primitives = design.build_draw_list(slide, template)
    assert any(isinstance(item, design.Text) for item in primitives)


@pytest.mark.parametrize("decor", core.DECORS)
def test_decor_variants(decor: str) -> None:
    template = _template(decor=decor)
    primitives = design.build_draw_list(core.new_slide(core.LAYOUT_COVER), template)
    assert len(primitives) > 2
    if decor == core.DECOR_GRID:
        assert sum(isinstance(item, design.Line) for item in primitives) >= 20
    if decor == core.DECOR_FRAME:
        assert sum(isinstance(item, design.Rect) for item in primitives) >= 3
    if decor == core.DECOR_MINIMAL:
        assert any(isinstance(item, design.Rect) and item.w == 1280 and item.h == 6 for item in primitives)


def test_unknown_layout_falls_back_to_bullets() -> None:
    template = _template()
    primitives = design.build_draw_list(core.SlideSpec(layout="unknown", title="回退"), template)
    text = "\n".join("\n".join(item.paragraphs) for item in primitives if isinstance(item, design.Text))
    assert "回退" in text


def test_invalid_accent_falls_back_to_template_accent() -> None:
    template = _template(style=core.STYLE_PORCELAIN)
    slide = core.SlideSpec(layout=core.LAYOUT_BULLETS, title="颜色回退", accent="not-a-color")
    primitives = design.build_draw_list(slide, template)
    assert any(isinstance(item, design.Rect) and item.fill == template.theme.palette.accent for item in primitives)


def test_accent_override_is_used() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_BULLETS, title="强调色", accent="ff8800")
    primitives = design.build_draw_list(slide, template)
    assert any(isinstance(item, design.Rect) and item.fill == "#FF8800" for item in primitives)


def test_agenda_two_columns_for_many_items() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_AGENDA,
        title="长目录",
        bullets=tuple(f"议题 {index}" for index in range(6)),
    )
    primitives = design.build_draw_list(slide, template)
    texts = [item for item in primitives if isinstance(item, design.Text)]
    assert len(texts) >= 8


def test_cover_long_title_uses_smaller_font() -> None:
    template = _template()
    short = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_COVER, title="短标题"), template)
    long = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_COVER, title="这是一个明显超过二十二个字符的演示主标题用于测试字号回退"),
        template,
    )
    short_sizes = [item.size for item in short if isinstance(item, design.Text)]
    long_sizes = [item.size for item in long if isinstance(item, design.Text)]
    assert max(long_sizes) < max(short_sizes)


def test_two_column_headers_and_groups() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_TWO_COLUMN,
        title="双栏",
        subtitle="左|右",
        bullets=("a", "b", "c"),
    )
    primitives = design.build_draw_list(slide, template)
    text = "\n".join(line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs)
    assert "左" in text
    assert "右" in text
    assert "c" in text


def test_two_column_without_subtitle_uses_defaults() -> None:
    template = _template()
    primitives = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_TWO_COLUMN), template)
    text = "\n".join(line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs)
    assert "核心要点" in text
    assert "延伸思考" in text


def test_metrics_with_plain_values_and_padding() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_METRICS, title="指标", bullets=("42",))
    primitives = design.build_draw_list(slide, template)
    text = "\n".join(line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs)
    assert "42" in text
    assert "待补充" in text


def test_timeline_with_single_item() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_TIMELINE, title="单节点", bullets=("01|启动",))
    primitives = design.build_draw_list(slide, template)
    assert any(isinstance(item, design.Oval) for item in primitives)


def test_quote_and_closing_long_text() -> None:
    template = _template()
    quote = design.build_draw_list(
        core.SlideSpec(
            layout=core.LAYOUT_QUOTE,
            title="这是一段明显超过三十六个字符的长引用文字，用来验证引用页会自动缩小字号的展示效果",
            subtitle="— 某人",
        ),
        template,
    )
    assert any(isinstance(item, design.Text) and item.size == 27 for item in quote)
    closing = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_CLOSING, title="A VERY LONG CLOSING TITLE"),
        template,
    )
    assert any(isinstance(item, design.Text) and item.size == 46 for item in closing)


def test_custom_design_size_and_aspect() -> None:
    template = _template(aspect=core.ASPECT_4_3)
    primitives = design.build_draw_list(core.new_slide(core.LAYOUT_COVER), template)
    assert isinstance(primitives[0], design.Rect)
    assert primitives[0].w == 960
    custom = design.build_draw_list(core.new_slide(core.LAYOUT_COVER), template, width=800, height=600)
    assert isinstance(custom[0], design.Rect)
    assert custom[0].w == 800
    assert custom[0].h == 600


def test_section_number_extraction() -> None:
    template = _template()
    primitives = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_SECTION, title="第二章", subtitle="第 02 章"),
        template,
    )
    text = "\n".join(line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs)
    assert "02" in text
    fallback = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_SECTION), template)
    fallback_text = "\n".join(line for item in fallback if isinstance(item, design.Text) for line in item.paragraphs)
    assert "01" in fallback_text


def test_split_parts_keeps_field_order() -> None:
    assert design._split_parts(" 维度 | 方案A | 方案B ") == ("维度", "方案A", "方案B")
    assert design._split_parts("只有一列") == ("只有一列",)
    assert design._split_parts("维度||方案B") == ("维度", "", "方案B")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("42", 42.0), ("+12%", 12.0), ("1,024", 1024.0), ("3.5 万", 3.5), ("-8", -8.0), ("暂无", 0.0)],
)
def test_parse_number(raw: str, expected: float) -> None:
    assert design._parse_number(raw) == expected


def test_image_layout_has_placeholder_panel() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_IMAGE, title="主视觉", bullets=("要点一", "要点二"))
    primitives = design.build_draw_list(slide, template)
    text = "\n".join(line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs)
    assert "主视觉" in text
    assert "要点一" in text
    assert "图片占位 · 导出后在 PowerPoint 中替换" in text
    panels = [
        item for item in primitives if isinstance(item, design.Rect) and item.fill == template.theme.palette.surface
    ]
    assert panels
    assert panels[-1].x > 1280 / 2
    assert panels[-1].radius == core.DesignTokens().radius.card


def test_image_layout_falls_back_to_defaults() -> None:
    template = _template()
    primitives = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_IMAGE), template)
    text = "\n".join(line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs)
    assert "让主视觉先说话" in text
    assert "主视觉占据右侧近一半版面" in text


def test_comparison_parses_dimension_and_two_options() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_COMPARISON,
        title="方案对比",
        subtitle="自建|采购",
        bullets=("响应速度|小时级|分钟级", "维护成本|较高|更低"),
    )
    primitives = design.build_draw_list(slide, template)
    texts = [item for item in primitives if isinstance(item, design.Text)]
    content = "\n".join(line for item in texts for line in item.paragraphs)
    for expected in ("对比维度", "自建", "采购", "响应速度", "小时级", "分钟级", "维护成本", "较高", "更低"):
        assert expected in content
    right_cells = [item for item in texts if item.paragraphs == ("分钟级",)]
    assert right_cells
    assert right_cells[0].color == template.theme.palette.accent
    assert right_cells[0].bold is True


def test_comparison_pads_missing_fields_and_caps_rows() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_COMPARISON,
        bullets=("只有维度", "维度|仅左", "甲|一|二", "乙|一|二", "丙|一|二"),
    )
    primitives = design.build_draw_list(slide, template)
    content = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
    assert "只有维度" in content
    assert content.count("—") >= 2
    assert "丙" not in content


def test_chart_bar_heights_follow_values() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_CHART, title="季度", bullets=("Q1|50", "Q2|100"))
    primitives = design.build_draw_list(slide, template)
    bars = [
        item
        for item in primitives
        if isinstance(item, design.Rect) and item.radius == core.DesignTokens().radius.small / 2 and item.w > 100
    ]
    assert len(bars) == 2
    assert bars[0].h == pytest.approx(bars[1].h / 2)
    assert bars[0].fill == template.theme.palette.accent
    assert bars[1].fill == template.theme.palette.secondary
    labels = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
    assert "50" in labels
    assert "100" in labels
    assert "图表占位 · 导出后可在 PowerPoint 中替换为真实图表" in labels


def test_chart_handles_non_numeric_values() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_CHART, title="空数据", bullets=("无|暂无",))
    primitives = design.build_draw_list(slide, template)
    bars = [
        item
        for item in primitives
        if isinstance(item, design.Rect) and item.radius == core.DesignTokens().radius.small / 2 and item.w > 100
    ]
    assert len(bars) == 1
    assert bars[0].h == 2


def test_contact_defaults_and_qr_placeholder() -> None:
    template = _template()
    primitives = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_CONTACT), template)
    content = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
    assert "让我们保持联系" in content
    assert "邮箱" in content
    assert "hello@example.com" in content
    assert "二维码占位" in content
    assert "PPTMAKE · 联系方式" in content


def test_contact_uses_provided_rows() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_CONTACT,
        title="联系我们",
        subtitle="随时沟通",
        bullets=("官网|fcmd.dev",),
    )
    primitives = design.build_draw_list(slide, template)
    content = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
    assert "联系我们" in content
    assert "随时沟通" in content
    assert "官网" in content
    assert "fcmd.dev" in content
