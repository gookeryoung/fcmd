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
    assert any(isinstance(item, design.Text) and item.size == 24 for item in primitives)

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


def test_metrics_trend_semantics_and_number_format() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_METRICS,
        title="指标",
        bullets=("1200|覆盖项目|+12%", "8|缺陷数|-3"),
        unit="个",
        value_format="#,###",
    )
    primitives = design.build_draw_list(slide, template)
    texts = [item for item in primitives if isinstance(item, design.Text)]
    content = "\n".join(line for item in texts for line in item.paragraphs)
    assert "1,200个" in content
    assert "+12%" in content
    assert "-3" in content
    semantic = template.theme.tokens.semantic
    trend_colors = {line: item.color for item in texts for line in item.paragraphs if line in {"+12%", "-3"}}
    assert trend_colors["+12%"] == semantic.success
    assert trend_colors["-3"] == semantic.danger


def test_format_number_variants() -> None:
    assert design._format_number("1234.6", value_format="#,###") == "1,235"
    assert design._format_number("42", value_format="0%") == "42%"
    assert design._format_number("42", unit="%") == "42%"
    assert design._format_number("暂无") == "暂无"
    assert design._format_number("", unit="个") == "—"


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
    assert any(isinstance(item, design.Text) and item.size == 35 for item in quote)
    closing = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_CLOSING, title="A VERY LONG CLOSING TITLE"),
        template,
    )
    assert any(isinstance(item, design.Text) and item.size == 51 for item in closing)


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
    assert "图片占位" not in text

    hinted = design.build_draw_list(slide, template, hints=True)
    hinted_text = "\n".join(line for item in hinted if isinstance(item, design.Text) for line in item.paragraphs)
    assert "图片占位 · 导出后在 PowerPoint 中替换" in hinted_text
    panels = [
        item for item in primitives if isinstance(item, design.Rect) and item.fill == template.theme.palette.surface
    ]
    assert panels
    assert panels[-1].x > 1280 * 0.4
    assert panels[-1].radius == core.DesignTokens().radius.card


def test_image_layout_emits_picture_when_path_set() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_IMAGE, title="主视觉", image="assets/hero.png")
    primitives = design.build_draw_list(slide, template)
    pictures = [item for item in primitives if isinstance(item, design.Picture)]
    assert len(pictures) == 1
    assert pictures[0].path == "assets/hero.png"
    assert pictures[0].fit == core.IMAGE_FIT_COVER


@pytest.mark.parametrize("ratio", core.IMAGE_RATIOS)
def test_image_layout_ratio_stays_inside_page(ratio: str) -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_IMAGE, title="比例", image="assets/hero.png", image_ratio=ratio)
    primitives = design.build_draw_list(slide, template)
    pictures = [item for item in primitives if isinstance(item, design.Picture)]
    assert len(pictures) == 1
    picture = pictures[0]
    assert picture.x >= 0 and picture.y >= 0
    assert picture.x + picture.w <= 1280
    assert picture.y + picture.h <= 720
    texts = [item for item in primitives if isinstance(item, design.Text)]
    assert texts
    assert all(item.x >= 0 and item.x + item.w <= 1280 for item in texts)


def test_image_layout_full_bleeds_picture_and_switches_palette() -> None:
    template = _template(style=core.STYLE_PORCELAIN)
    slide = core.SlideSpec(
        layout=core.LAYOUT_IMAGE,
        title="全出血",
        image="assets/hero.png",
        image_ratio=core.IMAGE_RATIO_FULL,
    )
    primitives = design.build_draw_list(slide, template)
    picture = next(item for item in primitives if isinstance(item, design.Picture))
    assert (picture.x, picture.y, picture.w, picture.h) == (0, 0, 1280, 720)
    assert picture.overlay > 0
    title = next(item for item in primitives if isinstance(item, design.Text) and item.paragraphs == ("全出血",))
    assert title.color == design._DARK_TEXT


@pytest.mark.parametrize(("ratio", "cells"), [(core.IMAGE_RATIO_GRID_2, 2), (core.IMAGE_RATIO_GRID_3, 3)])
def test_image_layout_grid_splits_panel_into_rows(ratio: str, cells: int) -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_IMAGE, title="栅格", image="assets/hero.png", image_ratio=ratio)
    primitives = design.build_draw_list(slide, template)
    card_radius = core.DesignTokens().radius.card
    panels = [
        item
        for item in primitives
        if isinstance(item, design.Rect) and item.fill == template.theme.palette.surface and item.radius == card_radius
    ]
    assert len(panels) == cells
    assert len({panel.x for panel in panels}) == 1
    assert len({panel.w for panel in panels}) == 1
    assert panels[-1].y + panels[-1].h == panels[0].y + sum(
        panel.h for panel in panels
    ) + core.DesignTokens().spacing.gap * (cells - 1)
    picture = next(item for item in primitives if isinstance(item, design.Picture))
    assert (picture.x, picture.y, picture.w, picture.h) == (panels[0].x, panels[0].y, panels[0].w, panels[0].h)


def test_image_layout_grid_fills_cells_with_multiple_images() -> None:
    """多图栅格按 ``images`` 顺序逐格填图，并自动补未标注图的替代文本。"""
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_IMAGE,
        title="多图",
        image="assets/a.png",
        images=("assets/a.png", "assets/b.png", "assets/c.png"),
        image_ratio=core.IMAGE_RATIO_GRID_3,
    )
    pictures = [item for item in design.build_draw_list(slide, template) if isinstance(item, design.Picture)]
    assert [picture.path for picture in pictures] == ["assets/a.png", "assets/b.png", "assets/c.png"]
    assert pictures[0].alt == ""
    assert pictures[1].alt.endswith("图2") and pictures[2].alt.endswith("图3")


def test_image_layout_renders_caption_below_panel() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_IMAGE,
        title="带图注",
        image="assets/hero.png",
        image_caption="产品主视觉",
    )
    primitives = design.build_draw_list(slide, template)
    caption = next(item for item in primitives if isinstance(item, design.Text) and item.paragraphs == ("产品主视觉",))
    picture = next(item for item in primitives if isinstance(item, design.Picture))
    assert caption.y >= picture.y + picture.h
    assert caption.color == template.theme.palette.muted


def test_image_layout_numeric_ratio_moves_split() -> None:
    template = _template()
    narrow = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_IMAGE, title="窄图", image="assets/hero.png", image_ratio="0.2"),
        template,
    )
    wide = design.build_draw_list(
        core.SlideSpec(layout=core.LAYOUT_IMAGE, title="宽图", image="assets/hero.png", image_ratio="0.8"),
        template,
    )
    narrow_picture = next(item for item in narrow if isinstance(item, design.Picture))
    wide_picture = next(item for item in wide if isinstance(item, design.Picture))
    assert wide_picture.w > narrow_picture.w
    assert wide_picture.x < narrow_picture.x


def test_logo_renders_top_right() -> None:
    template = _template()
    template = replace(template, theme=replace(template.theme, logo="assets/logo.png"))
    primitives = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_COVER), template)
    logos = [item for item in primitives if isinstance(item, design.Picture)]
    assert len(logos) == 1
    assert logos[0].x > 1280 / 2
    assert logos[0].fit == "contain"


def test_footer_and_page_number_only_when_requested() -> None:
    template = _template()
    template = replace(template, theme=replace(template.theme, footer="品牌页脚"))
    slide = core.SlideSpec(layout=core.LAYOUT_BULLETS, title="页脚")
    without = design.build_draw_list(slide, template)
    without_text = "\n".join(line for item in without if isinstance(item, design.Text) for line in item.paragraphs)
    assert "品牌页脚" in without_text
    assert "01 /" not in without_text and "02 /" not in without_text

    with_footer = design.build_draw_list(slide, template, page_number=2, page_total=8)
    text = "\n".join(line for item in with_footer if isinstance(item, design.Text) for line in item.paragraphs)
    assert "品牌页脚" in text
    assert "02 / 08" in text


def test_cover_and_section_hide_page_number() -> None:
    template = _template()
    for layout in (core.LAYOUT_COVER, core.LAYOUT_SECTION, core.LAYOUT_CLOSING):
        primitives = design.build_draw_list(
            core.SlideSpec(layout=layout, title="章节 01"),
            template,
            page_number=1,
            page_total=9,
        )
        content = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
        assert "01 / 09" not in content


def test_bullet_levels_indent_and_keep_text() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_BULLETS, title="层级", bullets=("顶层", "  - 子项"))
    primitives = design.build_draw_list(slide, template)
    texts = [item for item in primitives if isinstance(item, design.Text) and item.paragraphs]
    top = next(item for item in texts if item.paragraphs[0] == "顶层")
    child = next(item for item in texts if item.paragraphs[0] == "子项")
    assert child.level == 1
    assert top.level == 0
    # 缩进由 draw list 的 indent 令牌与层级共同决定，预览与导出同源。
    assert child.indent == template.theme.tokens.bullet.indent
    assert child.w < top.w
    margin = round(template.theme.tokens.spacing.margin_x * 1280)
    assert top.x == margin + round(template.theme.tokens.type_scale.body * 1.5)


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
    tables = [item for item in primitives if isinstance(item, design.Table)]
    assert len(tables) == 1
    table = tables[0]
    assert len(table.col_widths) == 3
    assert len(table.row_heights) == 3
    assert table.w == pytest.approx(sum(table.col_widths))
    assert table.h == pytest.approx(sum(table.row_heights))
    cells = [[cell.text for cell in row] for row in table.rows]
    assert cells == [
        ["对比维度", "自建", "采购"],
        ["响应速度", "小时级", "分钟级"],
        ["维护成本", "较高", "更低"],
    ]
    right = table.rows[1][2]
    assert right.color == template.theme.palette.accent
    assert right.bold is True
    content = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
    assert "分钟级" not in content


def test_comparison_pads_missing_fields_and_caps_rows() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_COMPARISON,
        bullets=("只有维度", "维度|仅左", "甲|一|二", "乙|一|二", "丙|一|二"),
    )
    table = next(item for item in design.build_draw_list(slide, template) if isinstance(item, design.Table))
    assert len(table.row_heights) == 5
    labels = [row[0].text for row in table.rows]
    assert labels == ["对比维度", "只有维度", "维度", "甲", "乙"]
    assert table.rows[1][1].text == "—"
    assert table.rows[1][2].text == "—"
    assert "丙" not in labels


def test_comparison_aligns_numeric_columns_right() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_COMPARISON,
        subtitle="方案 A|方案 B|方案 C|方案 D",
        bullets=("销量|1,200|980|1,500|1,300", "成本|300|280|260|240"),
    )
    table = next(item for item in design.build_draw_list(slide, template) if isinstance(item, design.Table))
    assert len(table.col_widths) == 5
    assert all(cell.align == design.ALIGN_RIGHT for cell in table.rows[1][1:])
    assert table.rows[1][0].align == design.ALIGN_LEFT


def test_chart_emits_editable_chart_with_values() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_CHART, title="季度", bullets=("Q1|50", "Q2|100"))
    primitives = design.build_draw_list(slide, template)
    charts = [item for item in primitives if isinstance(item, design.Chart)]
    assert len(charts) == 1
    chart = charts[0]
    assert chart.categories == ("Q1", "Q2")
    assert chart.series[0].values == (50.0, 100.0)
    assert chart.kind == "bar"
    assert chart.colors[0] == template.theme.tokens.chart_palette[0]
    labels = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
    assert "图表占位" not in "\n".join(labels)
    hinted = design.build_draw_list(slide, template, hints=True)
    assert not any("图表占位" in line for item in hinted if isinstance(item, design.Text) for line in item.paragraphs)


def test_chart_options_pie_line_and_negative_fallback() -> None:
    template = _template()
    pie = core.SlideSpec(
        layout=core.LAYOUT_CHART,
        bullets=("A|1", "B|3"),
        chart_type=core.CHART_PIE,
        unit="%",
        show_labels=False,
        legend=False,
    )
    chart = next(item for item in design.build_draw_list(pie, template) if isinstance(item, design.Chart))
    assert chart.kind == "pie"
    assert chart.unit == "%"
    assert chart.show_labels is False
    assert chart.legend is False
    assert len(chart.colors) == 2
    line = replace(pie, chart_type=core.CHART_LINE)
    assert (
        next(item for item in design.build_draw_list(line, template) if isinstance(item, design.Chart)).kind == "line"
    )
    negative = replace(pie, bullets=("A|-5", "B|10"))
    assert (
        next(item for item in design.build_draw_list(negative, template) if isinstance(item, design.Chart)).kind
        == "bar"
    )


def test_chart_without_numbers_shows_empty_state() -> None:
    template = _template()
    slide = core.SlideSpec(layout=core.LAYOUT_CHART, title="空数据", bullets=("无|暂无",))
    primitives = design.build_draw_list(slide, template)
    assert not any(isinstance(item, design.Chart) for item in primitives)
    content = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
    assert "暂无数据" in content


def test_contact_defaults_and_qr_placeholder() -> None:
    template = _template()
    primitives = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_CONTACT), template)
    content = [line for item in primitives if isinstance(item, design.Text) for line in item.paragraphs]
    assert "让我们保持联系" in content
    assert "邮箱" in content
    assert "hello@example.com" in content
    assert "二维码占位" not in content
    assert not any("PPTMAKE" in line for line in content)

    hinted = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_CONTACT), template, hints=True)
    hinted_content = [line for item in hinted if isinstance(item, design.Text) for line in item.paragraphs]
    assert "二维码占位" in hinted_content


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


@pytest.mark.parametrize("layout", core.LAYOUTS)
def test_headings_flag_skips_title_text_but_keeps_decoration(layout: str) -> None:
    template = _template()
    slide = core.SlideSpec(layout=layout, title="标题占位", subtitle="副标题占位", bullets=("要点",))
    with_headings = design.build_draw_list(slide, template)
    without = design.build_draw_list(slide, template, headings=False)
    assert len(without) < len(with_headings)
    assert all(
        isinstance(
            item, (design.Rect, design.Oval, design.Line, design.Picture, design.Table, design.Chart, design.Text)
        )
        for item in without
    )
    assert not any("标题占位" in item.paragraphs for item in without if isinstance(item, design.Text))
    assert any(isinstance(item, (design.Rect, design.Oval, design.Line)) for item in without)


@pytest.mark.parametrize(
    ("focus", "crop"),
    [
        ("center", (0.25, 0.25)),
        ("left", (0.0, 0.5)),
        ("right", (0.5, 0.0)),
        ("top", (0.25, 0.25)),
        ("bottom", (0.25, 0.25)),
    ],
)
def test_picture_crop_horizontal_focus(focus: str, crop: tuple[float, float]) -> None:
    left, right, top, bottom = design.picture_crop(focus, image_ratio=2.0, box_ratio=1.0)
    assert (left, right) == pytest.approx(crop)
    assert (top, bottom) == (0.0, 0.0)


@pytest.mark.parametrize(
    ("focus", "crop"),
    [("top", (0.0, 0.5)), ("bottom", (0.5, 0.0)), ("left", (0.25, 0.25)), ("center", (0.25, 0.25))],
)
def test_picture_crop_vertical_focus(focus: str, crop: tuple[float, float]) -> None:
    assert design.picture_crop(focus, image_ratio=0.5, box_ratio=1.0)[2:] == pytest.approx(crop)


def test_picture_crop_same_ratio_keeps_full_image() -> None:
    assert design.picture_crop("left", image_ratio=1.0, box_ratio=1.0) == (0.0, 0.0, 0.0, 0.0)


def test_light_background_switches_to_neutral_palette() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_BULLETS, title="浅色", bullets=("要点",), background=core.BACKGROUND_LIGHT
    )
    primitives = design.build_draw_list(slide, template)
    background = next(item for item in primitives if isinstance(item, design.Rect))
    assert background.fill == design._LIGHT_BACKGROUND
    text = next(item for item in primitives if isinstance(item, design.Text) and item.paragraphs[0] == "浅色")
    assert text.color == design._LIGHT_TEXT


def test_dark_background_switches_to_neutral_palette() -> None:
    template = _template(style=core.STYLE_PORCELAIN)
    slide = core.SlideSpec(layout=core.LAYOUT_BULLETS, title="深色", bullets=("要点",), background=core.BACKGROUND_DARK)
    primitives = design.build_draw_list(slide, template)
    background = next(item for item in primitives if isinstance(item, design.Rect))
    assert background.fill == design._DARK_BACKGROUND


def test_background_image_fills_page_with_scrim() -> None:
    template = _template()
    slide = core.SlideSpec(
        layout=core.LAYOUT_SECTION,
        title="图片底板",
        image="/tmp/hero.png",
        image_alt="主视觉",
        image_focus="top",
        background=core.BACKGROUND_IMAGE,
    )
    primitives = design.build_draw_list(slide, template)
    picture = next(item for item in primitives if isinstance(item, design.Picture))
    assert (picture.x, picture.y, picture.w, picture.h) == (0, 0, 1280, 720)
    assert picture.alt == "主视觉"
    assert picture.focus == "top"
    assert picture.overlay == pytest.approx(core.DesignTokens().image_scrim)


def test_readable_on_raises_accent_on_tinted_surface() -> None:
    palette = core.palette_for_style(core.STYLE_CORAL)
    tint = core.mix_hex(palette.background, palette.accent, 0.22)
    assert design._contrast(palette.accent, tint) < 3.0
    adjusted = design.readable_on(palette.accent, tint, palette.text)
    assert design._contrast(adjusted, tint) >= 3.0
    # 已经达标的颜色原样返回。
    assert design.readable_on(palette.text, palette.background, palette.accent) == palette.text


def test_photo_layout_uses_dark_palette_for_light_style() -> None:
    template = _template(style=core.STYLE_PORCELAIN)
    slide = core.SlideSpec(layout=core.LAYOUT_PHOTO_COVER, title="图片封面")
    primitives = design.build_draw_list(slide, template)
    title = next(item for item in primitives if isinstance(item, design.Text) and item.paragraphs == ("图片封面",))
    assert design._contrast(title.color, design._DARK_BACKGROUND) >= 3.0


def test_background_decor_primitives_marked_decorative() -> None:
    primitives = design.build_draw_list(core.SlideSpec(layout=core.LAYOUT_BULLETS), _template())
    marks = [bool(getattr(item, "decorative", False)) for item in primitives]
    assert any(marks)
    assert not all(marks)


def test_section_watermark_number_is_decorative() -> None:
    slide = core.SlideSpec(layout=core.LAYOUT_SECTION, subtitle="03", title="章节标题")
    primitives = design.build_draw_list(slide, _template())
    numbers = [item for item in primitives if isinstance(item, design.Text) and item.paragraphs == ("03",)]
    assert numbers
    assert all(item.decorative for item in numbers)
