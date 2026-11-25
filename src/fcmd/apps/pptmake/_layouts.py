"""pptmake 内置版式清单：键名、中文名与默认页码抑制规则。

只依赖标准库，供 :mod:`fcmd.apps.pptmake._core` 转出；拆分目的是把
``_core`` 控制在单文件行数预算内。
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DEFAULT_PAGE_NUMBER_HIDDEN",
    "LAYOUTS",
    "LAYOUT_AGENDA",
    "LAYOUT_AGENDA_PAGES",
    "LAYOUT_APPENDIX",
    "LAYOUT_BULLETS",
    "LAYOUT_CARDS",
    "LAYOUT_CHART",
    "LAYOUT_CHART_CONCLUSION",
    "LAYOUT_CLOSING",
    "LAYOUT_CODE",
    "LAYOUT_COMPARISON",
    "LAYOUT_CONTACT",
    "LAYOUT_COVER",
    "LAYOUT_IMAGE",
    "LAYOUT_METRICS",
    "LAYOUT_PHOTO_COVER",
    "LAYOUT_PHOTO_SECTION",
    "LAYOUT_PROBLEM_SOLUTION",
    "LAYOUT_PROCESS",
    "LAYOUT_QUOTE",
    "LAYOUT_SECTION",
    "LAYOUT_TEAM",
    "LAYOUT_TESTIMONIAL",
    "LAYOUT_TEXT",
    "LAYOUT_TIMELINE",
    "LAYOUT_TWO_COLUMN",
]

LAYOUT_COVER: Final[str] = "cover"
LAYOUT_AGENDA: Final[str] = "agenda"
LAYOUT_SECTION: Final[str] = "section"
LAYOUT_BULLETS: Final[str] = "bullets"
LAYOUT_TWO_COLUMN: Final[str] = "two_column"
LAYOUT_QUOTE: Final[str] = "quote"
LAYOUT_METRICS: Final[str] = "metrics"
LAYOUT_TIMELINE: Final[str] = "timeline"
LAYOUT_CLOSING: Final[str] = "closing"
LAYOUT_IMAGE: Final[str] = "image"
LAYOUT_COMPARISON: Final[str] = "comparison"
LAYOUT_CHART: Final[str] = "chart"
LAYOUT_CONTACT: Final[str] = "contact"
LAYOUT_PHOTO_COVER: Final[str] = "photo_cover"
LAYOUT_PHOTO_SECTION: Final[str] = "photo_section"
LAYOUT_CARDS: Final[str] = "cards"
LAYOUT_PROCESS: Final[str] = "process"
LAYOUT_PROBLEM_SOLUTION: Final[str] = "problem_solution"
LAYOUT_CHART_CONCLUSION: Final[str] = "chart_conclusion"
LAYOUT_TEAM: Final[str] = "team"
LAYOUT_TESTIMONIAL: Final[str] = "testimonial"
LAYOUT_CODE: Final[str] = "code"
LAYOUT_APPENDIX: Final[str] = "appendix"
LAYOUT_AGENDA_PAGES: Final[str] = "agenda_pages"

LAYOUTS: Final[tuple[str, ...]] = (
    LAYOUT_COVER,
    LAYOUT_AGENDA,
    LAYOUT_SECTION,
    LAYOUT_BULLETS,
    LAYOUT_TWO_COLUMN,
    LAYOUT_QUOTE,
    LAYOUT_METRICS,
    LAYOUT_TIMELINE,
    LAYOUT_CLOSING,
    LAYOUT_IMAGE,
    LAYOUT_COMPARISON,
    LAYOUT_CHART,
    LAYOUT_CONTACT,
    LAYOUT_PHOTO_COVER,
    LAYOUT_PHOTO_SECTION,
    LAYOUT_CARDS,
    LAYOUT_PROCESS,
    LAYOUT_PROBLEM_SOLUTION,
    LAYOUT_CHART_CONCLUSION,
    LAYOUT_TEAM,
    LAYOUT_TESTIMONIAL,
    LAYOUT_CODE,
    LAYOUT_APPENDIX,
    LAYOUT_AGENDA_PAGES,
)
LAYOUT_TEXT: Final[dict[str, str]] = {
    LAYOUT_COVER: "封面",
    LAYOUT_AGENDA: "目录",
    LAYOUT_SECTION: "章节",
    LAYOUT_BULLETS: "要点",
    LAYOUT_TWO_COLUMN: "双栏",
    LAYOUT_QUOTE: "引用",
    LAYOUT_METRICS: "数据",
    LAYOUT_TIMELINE: "时间线",
    LAYOUT_CLOSING: "结尾",
    LAYOUT_IMAGE: "图片主视觉",
    LAYOUT_COMPARISON: "对比矩阵",
    LAYOUT_CHART: "图表",
    LAYOUT_CONTACT: "联系方式",
    LAYOUT_PHOTO_COVER: "图片封面",
    LAYOUT_PHOTO_SECTION: "图片章节",
    LAYOUT_CARDS: "卡片要点",
    LAYOUT_PROCESS: "流程步骤",
    LAYOUT_PROBLEM_SOLUTION: "问题与方案",
    LAYOUT_CHART_CONCLUSION: "数据结论",
    LAYOUT_TEAM: "团队",
    LAYOUT_TESTIMONIAL: "客户证言",
    LAYOUT_CODE: "代码",
    LAYOUT_APPENDIX: "附录",
    LAYOUT_AGENDA_PAGES: "目录页码",
}
# 封面、章节、结尾与联系页默认不编页码；可在模板里覆盖。
DEFAULT_PAGE_NUMBER_HIDDEN: Final[tuple[str, ...]] = (
    LAYOUT_COVER,
    LAYOUT_PHOTO_COVER,
    LAYOUT_SECTION,
    LAYOUT_PHOTO_SECTION,
    LAYOUT_CLOSING,
    LAYOUT_CONTACT,
)
