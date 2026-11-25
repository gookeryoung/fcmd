"""pptmake 质量检查：对比度分档、装饰豁免与四风格零拦截。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _design, _quality
from fcmd.apps.pptmake._design import Oval, Rect, Text
from fcmd.apps.pptmake._preview import contrast_ratio
from fcmd.apps.pptmake._rules import rule_for


def _text(  # noqa: PLR0913 - 测试工厂逐字段复刻 Text 签名
    x: float,
    y: float,
    size: float,
    *,
    color: str = "#777777",
    bold: bool = False,
    decorative: bool = False,
) -> Text:
    return Text(
        x=x,
        y=y,
        w=100.0,
        h=30.0,
        paragraphs=("示例",),
        size=size,
        color=color,
        bold=bold,
        decorative=decorative,
    )


@pytest.mark.parametrize(
    ("size", "bold", "expected"),
    (
        (16.0, False, _quality.BODY_CONTRAST),
        (24.0, False, _quality.BODY_CONTRAST),
        (24.0, True, _quality.LARGE_CONTRAST),
        (32.0, False, _quality.LARGE_CONTRAST),
    ),
)
def test_required_contrast_tiers(size: float, bold: bool, expected: float) -> None:
    """正文 4.5:1；18pt 粗体与 24pt 以上按大字 3:1。"""
    assert _quality.required_contrast(_text(0.0, 0.0, size, bold=bold)) == expected


def test_backdrop_of_uses_nearest_solid_shape_and_skips_decorative() -> None:
    """装饰底色不参与判断，实心矩形与椭圆按中心点覆盖。"""
    text = _text(0.0, 0.0, 16.0)
    primitives = (
        Rect(0, 0, 200, 200, fill="#101010", decorative=True),
        Rect(0, 0, 100, 100, fill="#202020"),
        Oval(0, 0, 100, 100, fill="#303030"),
        text,
    )
    assert _quality._backdrop_of(primitives, text, "#FFFFFF") == "#303030"
    assert _quality._backdrop_of((text,), text, "#FFFFFF") == "#FFFFFF"


def test_ink_on_fill_reaches_body_threshold() -> None:
    """实心块上的小字号文字必须达到 4.5:1（必要时压暗填充）。"""
    for style in core.STYLES:
        palette = core.palette_for_style(style)
        color, fill = _design.ink_on_fill(palette.accent, palette)
        assert contrast_ratio(color, fill) >= 4.5


def test_quality_flags_low_contrast_body_text() -> None:
    """正文被压到与底色相同时会计入低对比度。"""
    base = core.PALETTES[core.STYLE_PORCELAIN]
    palette = replace(base, muted=base.background)
    spec = core.TemplateSpec(
        theme=replace(core.Theme(), palette=palette),
        slides=(core.SlideSpec(layout=core.LAYOUT_TIMELINE, title="路线"),),
    )
    assert _quality.quality_issues(spec, verbose=False).contrast >= 1


def test_quality_skips_decorative_text() -> None:
    """章节编号等装饰文字即使与底色相同也不计入。"""
    base = core.PALETTES[core.STYLE_PORCELAIN]
    palette = replace(base, accent=base.background)
    spec = core.TemplateSpec(
        theme=replace(core.Theme(), palette=palette),
        slides=(core.SlideSpec(layout=core.LAYOUT_SECTION, title="章节", subtitle="02"),),
    )
    assert _quality.quality_issues(spec, verbose=False).contrast == 0


@pytest.mark.parametrize("style", core.STYLES)
@pytest.mark.parametrize("aspect", (core.ASPECT_16_9, core.ASPECT_4_3))
def test_default_template_passes_all_styles(style: str, aspect: str) -> None:
    """默认模板在四风格 × 两画幅下均无质量问题（含 4.5:1 正文口径）。"""
    report = _quality.quality_issues(core.new_template(style=style, aspect=aspect), verbose=False)
    assert report.total == 0


def test_verbose_reports_contrast_detail(capsys: pytest.CaptureFixture[str]) -> None:
    """逐页输出包含对比度门槛说明。"""
    base = core.PALETTES[core.STYLE_PORCELAIN]
    palette = replace(base, muted=base.background)
    spec = core.TemplateSpec(
        theme=replace(core.Theme(), palette=palette),
        slides=(core.SlideSpec(layout=core.LAYOUT_TIMELINE, title="路线"),),
    )
    _quality.quality_issues(spec, verbose=True)
    output = capsys.readouterr().out
    assert "对比度低于 4.5:1" in output


def test_quality_details_lists_kinds_and_describe() -> None:
    """检查面板拿到逐页明细：类别、数量与可读文案。"""
    spec = core.TemplateSpec(
        slides=(
            core.SlideSpec(
                layout=core.LAYOUT_BULLETS,
                title="溢出",
                bullets=tuple("这是一行非常长的要点文字" * 8 for _ in range(12)),
            ),
            core.SlideSpec(layout=core.LAYOUT_IMAGE, title="图", image="missing.png"),
        )
    )
    details = _quality.quality_details(spec)
    kinds = {item.kind for item in details}
    assert _quality.OVERFLOW in kinds
    assert _quality.UNLABELED in kinds
    assert all(item.page >= 1 for item in details)
    assert details[0].describe().startswith("第 ")
    assert all(item.fixable == (item.kind in (_quality.OVERFLOW, _quality.UNLABELED)) for item in details)


def test_quality_issues_counts_match_details() -> None:
    """汇总计数与明细逐项求和一致。"""
    spec = core.TemplateSpec(
        slides=(
            core.SlideSpec(
                layout=core.LAYOUT_BULLETS,
                title="溢出",
                bullets=tuple("这是一行非常长的要点文字" * 8 for _ in range(12)),
            ),
        )
    )
    details = _quality.quality_details(spec)
    report = _quality.quality_issues(spec, verbose=False)
    assert report.overflow == sum(item.count for item in details if item.kind == _quality.OVERFLOW)
    assert report.total == sum(item.count for item in details)


def test_fix_slide_truncates_and_fills_alt() -> None:
    """一键修复：标题 / 要点按容量截断，图片补替代文本占位。"""
    rule = rule_for(core.LAYOUT_BULLETS)
    slide = core.SlideSpec(
        layout=core.LAYOUT_BULLETS,
        title="标" * (rule.max_title + 5),
        bullets=tuple("点" * (rule.max_chars + 5) for _ in range(rule.max_bullets + 3)),
        image="assets/hero.png",
    )
    fixed, notes = _quality.fix_slide(slide)
    assert len(fixed.title) <= rule.max_title
    assert len(fixed.bullets) == rule.max_bullets
    assert all(len(item) <= rule.max_chars for item in fixed.bullets)
    assert fixed.image_alt
    assert notes


def test_fix_template_keeps_clean_template() -> None:
    """没有可修问题时原样返回，避免制造无意义的撤销记录。"""
    template = core.new_template()
    fixed, notes = _quality.fix_template(template)
    assert fixed is template
    assert notes == ()
