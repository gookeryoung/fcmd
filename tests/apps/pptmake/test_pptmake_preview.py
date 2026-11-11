"""pptmake 预览纯逻辑测试：视口、蒙版、文本测量与溢出预检。"""

from __future__ import annotations

import pytest

from fcmd.apps.pptmake import _design as design
from fcmd.apps.pptmake import _preview as preview


def test_fit_viewport_contain_and_center() -> None:
    view = preview.fit_viewport(1280, 720, 640, 480)
    assert view.scale == pytest.approx(0.5)
    assert view.offset_x == pytest.approx(0)
    assert view.offset_y == pytest.approx(60)
    assert view.page_rect() == pytest.approx((0, 60, 640, 420))
    assert view.page_width == pytest.approx(640)
    assert view.page_height == pytest.approx(360)


def test_fit_viewport_padding_and_zoom() -> None:
    padded = preview.fit_viewport(1280, 720, 640, 360, padding=20)
    assert padded.scale == pytest.approx(min(600 / 1280, 320 / 720))

    zoomed = preview.fit_viewport(1280, 720, 1280, 720, zoom=2)
    assert zoomed.scale == pytest.approx(2.0)
    assert zoomed.offset_x == pytest.approx(-640)
    assert zoomed.offset_y == pytest.approx(-360)


def test_fit_viewport_rejects_bad_design_size() -> None:
    with pytest.raises(ValueError, match="设计尺寸"):
        preview.fit_viewport(0, 720, 800, 480)


def test_viewport_roundtrip() -> None:
    view = preview.fit_viewport(1280, 720, 800, 480)
    canvas_x, canvas_y = view.to_canvas(320, 180)
    back_x, back_y = view.to_design(canvas_x, canvas_y)
    assert back_x == pytest.approx(320)
    assert back_y == pytest.approx(180)


def test_matte_rects_cover_canvas_outside_page() -> None:
    view = preview.fit_viewport(1280, 720, 800, 480)
    area = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in view.matte_rects())
    assert area == pytest.approx(800 * 480 - view.page_width * view.page_height)


def test_matte_rects_empty_when_page_fills_canvas() -> None:
    view = preview.fit_viewport(1280, 720, 1280, 720)
    assert view.matte_rects() == ()


def test_measure_text_prefers_fullwidth() -> None:
    assert preview.measure_text("中文", 20) == pytest.approx(40)
    assert preview.measure_text("ab", 20) < preview.measure_text("中文", 20)
    assert preview.measure_text("A", 20, bold=True) > preview.measure_text("A", 20)


def test_wrap_text_breaks_by_estimate() -> None:
    assert preview.wrap_text("一二三四五", 40, 20) == ("一二", "三四", "五")
    assert preview.wrap_text("", 100, 20) == ("",)
    assert preview.wrap_text("abc", 0, 20) == ("abc",)


def test_estimate_text_height_and_overflow() -> None:
    text = design.Text(0, 0, 100, 20, ("正文",), 20, "#FFFFFF", line_spacing=1.2)
    assert preview.estimate_text_height(text) == pytest.approx(24)
    assert preview.text_overflow(text) == pytest.approx(4)


def test_estimate_text_height_counts_paragraph_spacing() -> None:
    text = design.Text(0, 0, 400, 100, ("甲", "乙"), 10, "#FFFFFF", line_spacing=1.0, space_after=6)
    assert preview.estimate_text_height(text) == pytest.approx(32)


def test_overflow_issues_filters_text_only() -> None:
    roomy = design.Text(0, 0, 400, 100, ("短句",), 20, "#FFFFFF")
    tight = design.Text(0, 0, 40, 10, ("很长的一段正文需要换行",), 20, "#FFFFFF")
    primitives: tuple[design.Primitive, ...] = (design.Rect(0, 0, 10, 10), roomy, tight)

    issues = preview.overflow_issues(primitives)
    assert len(issues) == 1
    assert issues[0].index == 2
    assert issues[0].overflow > 0
    assert "溢出" in issues[0].message
    assert preview.overflow_issues(primitives, tolerance=10_000) == ()
