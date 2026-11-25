"""pptmake 质量检查：溢出、缺失图片、过小字号、替代文本与对比度。

CLI 的 ``validate`` / ``export`` / ``build`` 与编辑器（pptmakew）的设计检查
面板共用本模块，保证「命令行拦截」与「界面提示」同一口径。

对比度按 WCAG AA 分两档：

- 正文（小于 18pt，或小于 24pt 的粗体）要求 4.5:1；
- 大字（≥18pt 粗体 / ≥24pt）要求 3:1；
- 纯装饰文字（章节水印编号、引号等 ``Text.decorative``）豁免。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from fcmd.apps.pptmake._core import LAYOUT_TEXT, SlideSpec, TemplateSpec
from fcmd.apps.pptmake._design import Oval, Picture, Primitive, Rect, Text, build_draw_list
from fcmd.apps.pptmake._preview import contrast_ratio, overflow_issues
from fcmd.apps.pptmake._rules import rule_for
from fcmd.console import print_error

__all__ = [
    "BODY_CONTRAST",
    "CONTRAST",
    "LARGE_CONTRAST",
    "MIN_FONT_PT",
    "MISSING",
    "OVERFLOW",
    "SMALL",
    "UNLABELED",
    "PageIssue",
    "Quality",
    "fix_slide",
    "fix_template",
    "quality_details",
    "quality_issues",
    "required_contrast",
]

OVERFLOW: Final[str] = "overflow"
MISSING: Final[str] = "missing"
SMALL: Final[str] = "small"
UNLABELED: Final[str] = "unlabeled"
CONTRAST: Final[str] = "contrast"

MIN_FONT_PT: Final[float] = 12.0
# 设计坐标 96 单位 / 英寸，1 单位 = 0.75pt。
MIN_FONT_UNITS: Final[float] = MIN_FONT_PT * 96 / 72
BODY_CONTRAST: Final[float] = 4.5
LARGE_CONTRAST: Final[float] = 3.0
# 大字口径：≥18pt 粗体，或 ≥24pt（对应 WCAG 的 large text）。
LARGE_BOLD_UNITS: Final[float] = 18 * 96 / 72
LARGE_UNITS: Final[float] = 24 * 96 / 72


@dataclass(frozen=True, slots=True)
class Quality:
    """一页或整份模板的质量检查结果。"""

    overflow: int = 0
    missing: int = 0
    small: int = 0
    unlabeled: int = 0
    contrast: int = 0

    @property
    def total(self) -> int:
        return self.overflow + self.missing + self.small + self.unlabeled + self.contrast


def required_contrast(item: Text) -> float:
    """该文本应达到的对比度：大字 3:1，正文 4.5:1。"""
    if item.size >= LARGE_UNITS or (item.bold and item.size >= LARGE_BOLD_UNITS):
        return LARGE_CONTRAST
    return BODY_CONTRAST


def _backdrop_of(primitives: tuple[Primitive, ...], text: Text, default: str) -> str:
    """取文字下方最近的实心矩形 / 椭圆颜色，找不到时用页面底色。

    背景光斑、边框等标记为 ``decorative`` 的图元不参与判断：它们只是页面
    底色的浅淡变体，文字可读性以内容卡片与页面底色为准。
    """
    center_x = text.x + text.w / 2
    center_y = text.y + text.h / 2
    backdrop = default
    for item in primitives:
        if item is text:
            break
        if isinstance(item, Rect) and item.fill and not item.decorative:
            if item.x <= center_x <= item.x + item.w and item.y <= center_y <= item.y + item.h:
                backdrop = item.fill
        elif isinstance(item, Oval) and item.fill and not item.decorative:
            radius_x = item.w / 2
            radius_y = item.h / 2
            if radius_x <= 0 or radius_y <= 0:
                continue
            dx = (center_x - item.x - radius_x) / radius_x
            dy = (center_y - item.y - radius_y) / radius_y
            if dx * dx + dy * dy <= 1:
                backdrop = item.fill
    return backdrop


@dataclass(frozen=True, slots=True)
class PageIssue:
    """设计检查面板的一行：页码、类别、问题数与可展开明细。"""

    page: int
    layout: str
    kind: str
    count: int
    message: str
    details: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """版式中文名（未知布局回落原名）。"""
        return LAYOUT_TEXT.get(self.layout, self.layout)

    @property
    def fixable(self) -> bool:
        """一键修复可机械处理的类别（溢出截断、替代文本占位）。"""
        return self.kind in (OVERFLOW, UNLABELED)

    def describe(self) -> str:
        """列表展示文案：「第 3 页（要点）：2 处可能溢出」。"""
        return f"第 {self.page} 页（{self.label}）：{self.message}"


def quality_details(spec: TemplateSpec, *, base_dir: Path | None = None) -> tuple[PageIssue, ...]:
    """逐页、逐类别列出质量问题；编辑器检查面板与 ``quality_issues`` 共用。"""
    issues: list[PageIssue] = []
    for index, slide in enumerate(spec.slides, start=1):
        image = Path(slide.image)
        if slide.image and not image.is_absolute() and base_dir is not None:
            image = base_dir / image
        if slide.image and not image.is_file():
            issues.append(PageIssue(index, slide.layout, MISSING, 1, f"图片不存在：{slide.image}"))
        primitives = build_draw_list(slide, spec, page_number=index, page_total=len(spec.slides))
        overflow = overflow_issues(primitives)
        if overflow:
            issues.append(
                PageIssue(
                    index,
                    slide.layout,
                    OVERFLOW,
                    len(overflow),
                    f"{len(overflow)} 处可能溢出",
                    tuple(item.message for item in overflow),
                )
            )
        tiny = sum(1 for primitive in primitives if isinstance(primitive, Text) and primitive.size < MIN_FONT_UNITS)
        if tiny:
            issues.append(PageIssue(index, slide.layout, SMALL, tiny, f"{tiny} 处字号小于 {MIN_FONT_PT:g}pt"))
        unlabeled = sum(1 for item in primitives if isinstance(item, Picture) and item.path and not item.alt)
        if unlabeled:
            issues.append(PageIssue(index, slide.layout, UNLABELED, unlabeled, f"{unlabeled} 张图片缺少替代文本"))
        page_background = next((item.fill for item in primitives if isinstance(item, Rect) and item.fill), "#FFFFFF")
        low = sum(
            1
            for item in primitives
            if isinstance(item, Text)
            and item.size >= MIN_FONT_UNITS
            and not item.decorative
            and contrast_ratio(item.color, _backdrop_of(primitives, item, page_background)) < required_contrast(item)
        )
        if low:
            issues.append(
                PageIssue(
                    index,
                    slide.layout,
                    CONTRAST,
                    low,
                    f"{low} 处文字对比度低于 {BODY_CONTRAST:g}:1（正文）/ {LARGE_CONTRAST:g}:1（大字）",
                )
            )
    return tuple(issues)


def quality_issues(spec: TemplateSpec, *, verbose: bool, base_dir: Path | None = None) -> Quality:
    """汇总（溢出, 缺失图片, 过小字号, 缺替代文本, 对比度不足）；``verbose`` 时逐页打印。"""
    details = quality_details(spec, base_dir=base_dir)
    counts = Quality(
        overflow=sum(item.count for item in details if item.kind == OVERFLOW),
        missing=sum(item.count for item in details if item.kind == MISSING),
        small=sum(item.count for item in details if item.kind == SMALL),
        unlabeled=sum(item.count for item in details if item.kind == UNLABELED),
        contrast=sum(item.count for item in details if item.kind == CONTRAST),
    )
    if verbose:
        for item in details:
            if item.kind == MISSING:
                print_error(f"第 {item.page} 页（{item.label}）：{item.message}")
                continue
            print(f"第 {item.page} 页（{item.label}）：{item.message}")
            if item.kind == OVERFLOW:
                for line in item.details[:3]:
                    print(f"  - {line}")
    return counts


def fix_slide(slide: SlideSpec) -> tuple[SlideSpec, tuple[str, ...]]:
    """按版式容量做机械修复：截断标题 / 要点、补替代文本占位，不改写文案。"""
    rule = rule_for(slide.layout)
    fixes: list[str] = []
    title = slide.title
    if len(title.strip()) > rule.max_title:
        title = title.strip()[: rule.max_title - 1].rstrip() + "…"
        fixes.append(f"标题截断到 {rule.max_title} 字以内")
    bullets = slide.bullets[: rule.max_bullets]
    if len(slide.bullets) > rule.max_bullets:
        fixes.append(f"要点删减至 {rule.max_bullets} 条")
    truncated = False
    capped: list[str] = []
    for item in bullets:
        if len(item) > rule.max_chars:
            capped.append(item[: rule.max_chars - 1].rstrip() + "…")
            truncated = True
        else:
            capped.append(item)
    if truncated:
        fixes.append(f"要点截断到 {rule.max_chars} 字以内")
    alt = slide.image_alt
    if slide.image.strip() and not alt.strip():
        alt = f"{title.strip() or '示意图'} 配图"
        fixes.append("补充替代文本占位")
    if not fixes:
        return slide, ()
    return replace(slide, title=title, bullets=tuple(capped), image_alt=alt), tuple(fixes)


def fix_template(spec: TemplateSpec) -> tuple[TemplateSpec, tuple[str, ...]]:
    """整份模板执行机械修复；返回新模板与逐页修复说明。"""
    slides: list[SlideSpec] = []
    notes: list[str] = []
    for index, slide in enumerate(spec.slides, start=1):
        fixed, applied = fix_slide(slide)
        if applied:
            notes.append(f"第 {index} 页：{'、'.join(applied)}")
        slides.append(fixed)
    if not notes:
        return spec, ()
    return replace(spec, slides=tuple(slides)), tuple(notes)
