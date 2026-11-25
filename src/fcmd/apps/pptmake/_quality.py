"""pptmake 质量检查：溢出、缺失图片、过小字号、替代文本与对比度。

CLI 的 ``validate`` / ``export`` / ``build`` 与编辑器（pptmakew）的设计检查
面板共用本模块，保证「命令行拦截」与「界面提示」同一口径。

对比度按 WCAG AA 分两档：

- 正文（小于 18pt，或小于 24pt 的粗体）要求 4.5:1；
- 大字（≥18pt 粗体 / ≥24pt）要求 3:1；
- 纯装饰文字（章节水印编号、引号等 ``Text.decorative``）豁免。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fcmd.apps.pptmake._core import LAYOUT_TEXT, TemplateSpec
from fcmd.apps.pptmake._design import Oval, Picture, Primitive, Rect, Text, build_draw_list
from fcmd.apps.pptmake._preview import contrast_ratio, overflow_issues
from fcmd.console import print_error

__all__ = [
    "BODY_CONTRAST",
    "LARGE_CONTRAST",
    "MIN_FONT_PT",
    "Quality",
    "quality_issues",
    "required_contrast",
]

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


def quality_issues(spec: TemplateSpec, *, verbose: bool, base_dir: Path | None = None) -> Quality:
    """汇总（溢出, 缺失图片, 过小字号, 缺替代文本, 对比度不足）；``verbose`` 时逐页打印。"""
    counts = Quality()
    for index, slide in enumerate(spec.slides, start=1):
        image = Path(slide.image)
        if slide.image and not image.is_absolute() and base_dir is not None:
            image = base_dir / image
        missing = 1 if slide.image and not image.is_file() else 0
        if missing and verbose:
            print_error(f"第 {index} 页图片不存在：{slide.image}")
        primitives = build_draw_list(slide, spec, page_number=index, page_total=len(spec.slides))
        issues = overflow_issues(primitives)
        if issues and verbose:
            print(f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{len(issues)} 处可能溢出")
            for issue in issues[:3]:
                print(f"  - {issue.message}")
        tiny = sum(1 for primitive in primitives if isinstance(primitive, Text) and primitive.size < MIN_FONT_UNITS)
        if tiny and verbose:
            print(
                f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{tiny} 处字号小于 {MIN_FONT_PT:g}pt"
            )
        page_background = next((item.fill for item in primitives if isinstance(item, Rect) and item.fill), "#FFFFFF")
        unlabeled = sum(1 for item in primitives if isinstance(item, Picture) and item.path and not item.alt)
        if unlabeled and verbose:
            print(f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{unlabeled} 张图片缺少替代文本")
        low = sum(
            1
            for item in primitives
            if isinstance(item, Text)
            and item.size >= MIN_FONT_UNITS
            and not item.decorative
            and contrast_ratio(item.color, _backdrop_of(primitives, item, page_background)) < required_contrast(item)
        )
        if low and verbose:
            print(
                f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{low} 处文字对比度低于 "
                f"{BODY_CONTRAST:g}:1（正文）/ {LARGE_CONTRAST:g}:1（大字）"
            )
        counts = Quality(
            overflow=counts.overflow + len(issues),
            missing=counts.missing + missing,
            small=counts.small + tiny,
            unlabeled=counts.unlabeled + unlabeled,
            contrast=counts.contrast + low,
        )
    return counts
