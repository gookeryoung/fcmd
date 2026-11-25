"""pptmake 内容约束：每个版式的条目、字数、标题行数与图文比例。

纯逻辑、无 GUI / 第三方依赖，``pptmake validate`` 与编辑器共用同一套规则；
超限时给出可直接执行的精简建议，而不是等导出时才报溢出。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from fcmd.apps.pptmake._core import (
    LAYOUT_AGENDA,
    LAYOUT_AGENDA_PAGES,
    LAYOUT_APPENDIX,
    LAYOUT_BULLETS,
    LAYOUT_CARDS,
    LAYOUT_CHART,
    LAYOUT_CHART_CONCLUSION,
    LAYOUT_CLOSING,
    LAYOUT_CODE,
    LAYOUT_COMPARISON,
    LAYOUT_CONTACT,
    LAYOUT_COVER,
    LAYOUT_IMAGE,
    LAYOUT_METRICS,
    LAYOUT_PHOTO_COVER,
    LAYOUT_PHOTO_SECTION,
    LAYOUT_PROBLEM_SOLUTION,
    LAYOUT_PROCESS,
    LAYOUT_QUOTE,
    LAYOUT_SECTION,
    LAYOUT_TEAM,
    LAYOUT_TESTIMONIAL,
    LAYOUT_TEXT,
    LAYOUT_TIMELINE,
    LAYOUT_TWO_COLUMN,
    SlideSpec,
    TemplateSpec,
)

__all__ = ["RULES", "LayoutRule", "RuleIssue", "check_slide", "check_template", "rule_for"]


@dataclass(frozen=True, slots=True)
class LayoutRule:
    """单版式容量：要点条数、单条字数、标题字数与建议图文比例。"""

    max_bullets: int
    max_chars: int
    max_title: int = 40
    image_ratio: str = ""


@dataclass(frozen=True, slots=True)
class RuleIssue:
    """一条容量超限提示；页码由调用方补进 :meth:`describe`。"""

    layout: str
    message: str

    def describe(self, page: int) -> str:
        label = LAYOUT_TEXT.get(self.layout, self.layout)
        return f"第 {page} 页（{label}）：{self.message}"


# 版式容量按「汇报时一屏能读清」定：标题一行、要点 3–6 条、单条不超过 48 字。
RULES: Final[dict[str, LayoutRule]] = {
    LAYOUT_COVER: LayoutRule(0, 50, 40),
    LAYOUT_AGENDA: LayoutRule(8, 40, 30),
    LAYOUT_SECTION: LayoutRule(1, 60, 30),
    LAYOUT_BULLETS: LayoutRule(6, 48, 40),
    LAYOUT_TWO_COLUMN: LayoutRule(8, 40, 40),
    LAYOUT_QUOTE: LayoutRule(0, 60, 44),
    LAYOUT_METRICS: LayoutRule(4, 30, 30),
    LAYOUT_TIMELINE: LayoutRule(6, 32, 32),
    LAYOUT_CLOSING: LayoutRule(0, 60, 24),
    LAYOUT_IMAGE: LayoutRule(4, 48, 30, "40-60"),
    LAYOUT_COMPARISON: LayoutRule(6, 30, 32),
    LAYOUT_CHART: LayoutRule(8, 24, 32),
    LAYOUT_CONTACT: LayoutRule(4, 40, 32),
    LAYOUT_PHOTO_COVER: LayoutRule(1, 50, 34, "full"),
    LAYOUT_PHOTO_SECTION: LayoutRule(1, 60, 30, "full"),
    LAYOUT_CARDS: LayoutRule(4, 48, 36),
    LAYOUT_PROCESS: LayoutRule(6, 32, 36),
    LAYOUT_PROBLEM_SOLUTION: LayoutRule(5, 48, 40),
    LAYOUT_CHART_CONCLUSION: LayoutRule(8, 24, 40, "50-50"),
    LAYOUT_TEAM: LayoutRule(4, 32, 32),
    LAYOUT_TESTIMONIAL: LayoutRule(0, 60, 44),
    LAYOUT_CODE: LayoutRule(10, 48, 32),
    LAYOUT_APPENDIX: LayoutRule(8, 40, 32),
    LAYOUT_AGENDA_PAGES: LayoutRule(8, 40, 32),
}

_DEFAULT_RULE: Final[LayoutRule] = LayoutRule(6, 48, 40)


def rule_for(layout: str) -> LayoutRule:
    """返回版式容量；未知版式取通用上限。"""
    return RULES.get(layout, _DEFAULT_RULE)


def check_slide(slide: SlideSpec) -> tuple[RuleIssue, ...]:
    """检查单页容量，返回可直接展示的超限提示。"""
    rule = rule_for(slide.layout)
    issues: list[RuleIssue] = []
    title = slide.title.strip()
    if len(title) > rule.max_title:
        issues.append(
            RuleIssue(
                slide.layout,
                f"标题 {len(title)} 字，超过 {rule.max_title} 字，建议拆两行或精简",
            )
        )
    count = len(slide.bullets)
    if count > rule.max_bullets:
        issues.append(
            RuleIssue(
                slide.layout,
                f"要点 {count} 条，超过 {rule.max_bullets} 条，建议删减到 {rule.max_bullets} 条以内",
            )
        )
    for index, bullet in enumerate(slide.bullets, start=1):
        if len(bullet) > rule.max_chars:
            issues.append(
                RuleIssue(
                    slide.layout,
                    f"第 {index} 条 {len(bullet)} 字，超过 {rule.max_chars} 字，建议精简或拆成两条",
                )
            )
    return tuple(issues)


def check_template(template: TemplateSpec) -> tuple[tuple[int, RuleIssue], ...]:
    """逐页检查模板，返回 ``(页码, 提示)``；页码从 1 开始。"""
    return tuple((index, issue) for index, slide in enumerate(template.slides, start=1) for issue in check_slide(slide))
