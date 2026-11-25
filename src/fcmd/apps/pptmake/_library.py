"""内置示例模板库：六个风格 × 八个场景，供 ``pptmake init --scene`` 一键取用。

只组合既有版式与示例页，不引入新的视觉规则；场景差异体现在页面构成与
推荐风格，风格差异沿用 :func:`fcmd.apps.pptmake._core.new_template` 的调色板。
"""

from __future__ import annotations

from typing import Final

from fcmd.apps.pptmake._core import (
    ASPECT_16_9,
    DECOR_ORBS,
    STYLE_AURORA,
    STYLE_BUSINESS,
    STYLE_CORAL,
    STYLE_DEEPSEA,
    STYLE_GRAPHITE,
    STYLE_PORCELAIN,
    TemplateError,
    TemplateSpec,
    new_slide,
    new_template,
    normalize_aspect,
    normalize_decor,
    normalize_style,
    style_label,
)
from fcmd.apps.pptmake._layouts import (
    LAYOUT_AGENDA,
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
    LAYOUT_METRICS,
    LAYOUT_PHOTO_COVER,
    LAYOUT_PROBLEM_SOLUTION,
    LAYOUT_PROCESS,
    LAYOUT_SECTION,
    LAYOUT_TEAM,
    LAYOUT_TESTIMONIAL,
    LAYOUT_TIMELINE,
    LAYOUT_TWO_COLUMN,
)

__all__ = ["SCENES", "SCENE_STYLE", "SCENE_TEXT", "build_sample", "scene_layouts", "scene_style"]

SCENES: Final[dict[str, tuple[str, ...]]] = {
    "launch": (
        LAYOUT_COVER,
        LAYOUT_AGENDA,
        LAYOUT_SECTION,
        LAYOUT_PHOTO_COVER,
        LAYOUT_CARDS,
        LAYOUT_PROCESS,
        LAYOUT_METRICS,
        LAYOUT_CHART,
        LAYOUT_CLOSING,
    ),
    "quarterly": (
        LAYOUT_COVER,
        LAYOUT_AGENDA,
        LAYOUT_METRICS,
        LAYOUT_CHART,
        LAYOUT_CHART_CONCLUSION,
        LAYOUT_COMPARISON,
        LAYOUT_TEAM,
        LAYOUT_APPENDIX,
        LAYOUT_CLOSING,
    ),
    "proposal": (
        LAYOUT_COVER,
        LAYOUT_AGENDA,
        LAYOUT_PROBLEM_SOLUTION,
        LAYOUT_PROCESS,
        LAYOUT_CARDS,
        LAYOUT_TESTIMONIAL,
        LAYOUT_TEAM,
        LAYOUT_CONTACT,
        LAYOUT_CLOSING,
    ),
    "financing": (
        LAYOUT_COVER,
        LAYOUT_AGENDA,
        LAYOUT_PROBLEM_SOLUTION,
        LAYOUT_METRICS,
        LAYOUT_CHART,
        LAYOUT_COMPARISON,
        LAYOUT_TEAM,
        LAYOUT_TESTIMONIAL,
        LAYOUT_CONTACT,
        LAYOUT_CLOSING,
    ),
    "retrospective": (
        LAYOUT_COVER,
        LAYOUT_AGENDA,
        LAYOUT_SECTION,
        LAYOUT_TIMELINE,
        LAYOUT_PROCESS,
        LAYOUT_PROBLEM_SOLUTION,
        LAYOUT_METRICS,
        LAYOUT_CHART_CONCLUSION,
        LAYOUT_APPENDIX,
        LAYOUT_CLOSING,
    ),
    "training": (
        LAYOUT_COVER,
        LAYOUT_AGENDA,
        LAYOUT_SECTION,
        LAYOUT_BULLETS,
        LAYOUT_TWO_COLUMN,
        LAYOUT_CARDS,
        LAYOUT_PROCESS,
        LAYOUT_CODE,
        LAYOUT_APPENDIX,
        LAYOUT_CLOSING,
    ),
    "academic": (
        LAYOUT_COVER,
        LAYOUT_AGENDA,
        LAYOUT_SECTION,
        LAYOUT_BULLETS,
        LAYOUT_CHART,
        LAYOUT_CHART_CONCLUSION,
        LAYOUT_COMPARISON,
        LAYOUT_METRICS,
        LAYOUT_APPENDIX,
        LAYOUT_CLOSING,
    ),
    "government": (
        LAYOUT_COVER,
        LAYOUT_AGENDA,
        LAYOUT_SECTION,
        LAYOUT_METRICS,
        LAYOUT_PROCESS,
        LAYOUT_PROBLEM_SOLUTION,
        LAYOUT_COMPARISON,
        LAYOUT_TIMELINE,
        LAYOUT_APPENDIX,
        LAYOUT_CLOSING,
    ),
}
SCENE_TEXT: Final[dict[str, str]] = {
    "launch": "产品发布",
    "quarterly": "季度汇报",
    "proposal": "方案提案",
    "financing": "融资路演",
    "retrospective": "项目复盘",
    "training": "产品培训",
    "academic": "学术汇报",
    "government": "政务汇报",
}
SCENE_STYLE: Final[dict[str, str]] = {
    "launch": STYLE_AURORA,
    "quarterly": STYLE_BUSINESS,
    "proposal": STYLE_PORCELAIN,
    "financing": STYLE_DEEPSEA,
    "retrospective": STYLE_GRAPHITE,
    "training": STYLE_CORAL,
    "academic": STYLE_BUSINESS,
    "government": STYLE_PORCELAIN,
}


def scene_layouts(scene: str) -> tuple[str, ...]:
    """返回场景的版式顺序；未知场景抛出 :class:`TemplateError`。"""
    key = scene.strip().lower()
    if key not in SCENES:
        raise TemplateError(f"未知场景 {scene!r}，可选：{' / '.join(SCENES)}")
    return SCENES[key]


def scene_style(scene: str) -> str:
    """返回场景的推荐风格；未知场景抛出 :class:`TemplateError`。"""
    key = scene.strip().lower()
    if key not in SCENE_STYLE:
        raise TemplateError(f"未知场景 {scene!r}，可选：{' / '.join(SCENES)}")
    return SCENE_STYLE[key]


def build_sample(
    scene: str,
    *,
    style: str = STYLE_AURORA,
    aspect: str = ASPECT_16_9,
    decor: str = DECOR_ORBS,
    name: str = "",
) -> TemplateSpec:
    """按场景与风格组合一份示例模板，页序固定为 :data:`SCENES`。"""
    layouts = scene_layouts(scene)
    label = SCENE_TEXT[scene.strip().lower()]
    normalized_style = normalize_style(style)
    return new_template(
        name=name.strip() or f"{label} · {style_label(normalized_style)}",
        style=normalized_style,
        aspect=normalize_aspect(aspect),
        decor=normalize_decor(decor),
        description=f"{label}场景示例，{style_label(normalized_style)}风格，共 {len(layouts)} 页。",
        slides=tuple(new_slide(layout) for layout in layouts),
    )
