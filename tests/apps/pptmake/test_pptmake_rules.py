"""内容约束规则测试：版式容量、超限提示与逐页检查。"""

from __future__ import annotations

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _rules as rules


def test_rule_for_known_and_unknown_layout() -> None:
    assert rules.rule_for(core.LAYOUT_CARDS).max_bullets == 4
    assert rules.rule_for(core.LAYOUT_CODE).max_bullets == 10
    assert rules.rule_for("unknown") is rules._DEFAULT_RULE


def test_check_slide_reports_too_many_bullets() -> None:
    slide = core.SlideSpec(layout=core.LAYOUT_CARDS, bullets=tuple("要点" for _ in range(6)))
    issues = rules.check_slide(slide)
    assert len(issues) == 1
    assert "超过 4 条" in issues[0].message
    assert issues[0].describe(3).startswith("第 3 页（卡片要点）")


def test_check_slide_reports_long_bullet_and_title() -> None:
    slide = core.SlideSpec(layout=core.LAYOUT_BULLETS, title="标题" * 30, bullets=("长" * 60,))
    messages = [issue.message for issue in rules.check_slide(slide)]
    assert any("标题 60 字" in message for message in messages)
    assert any("第 1 条 60 字" in message for message in messages)


def test_check_slide_passes_sample_content() -> None:
    for layout in core.LAYOUTS:
        assert rules.check_slide(core.new_slide(layout)) == ()


def test_check_template_numbers_pages() -> None:
    template = core.new_template(
        slides=(
            core.new_slide(core.LAYOUT_COVER),
            core.SlideSpec(layout=core.LAYOUT_METRICS, bullets=tuple("指标" for _ in range(5))),
        )
    )
    issues = rules.check_template(template)
    assert len(issues) == 1
    page, issue = issues[0]
    assert page == 2
    assert "超过 4 条" in issue.message
