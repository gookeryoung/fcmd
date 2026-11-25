"""内置示例模板库：四款风格 × 三个场景，供 ``pptmake init --scene`` 一键取用。

只组合既有版式与示例页，不引入新的视觉规则；场景差异体现在页面构成，
风格差异沿用 :func:`fcmd.apps.pptmake._core.new_template` 的调色板。
"""

from __future__ import annotations

from typing import Final

from fcmd.apps.pptmake._core import (
    ASPECT_16_9,
    DECOR_ORBS,
    STYLE_AURORA,
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
    LAYOUT_CARDS,
    LAYOUT_CHART,
    LAYOUT_CHART_CONCLUSION,
    LAYOUT_CLOSING,
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
)

__all__ = ["SCENES", "SCENE_TEXT", "build_sample", "scene_layouts"]

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
}
SCENE_TEXT: Final[dict[str, str]] = {
    "launch": "产品发布",
    "quarterly": "季度汇报",
    "proposal": "方案提案",
}


def scene_layouts(scene: str) -> tuple[str, ...]:
    """返回场景的版式顺序；未知场景抛出 :class:`TemplateError`。"""
    key = scene.strip().lower()
    if key not in SCENES:
        raise TemplateError(f"未知场景 {scene!r}，可选：{' / '.join(SCENES)}")
    return SCENES[key]


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
