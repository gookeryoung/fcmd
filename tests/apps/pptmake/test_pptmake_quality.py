"""pptmake 质量检查：对比度分档、装饰豁免与四风格零拦截。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _design, _quality
from fcmd.apps.pptmake._design import Oval, Rect, Text
from fcmd.apps.pptmake._preview import contrast_ratio


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
