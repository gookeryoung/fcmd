"""示例模板库测试：场景组合、风格应用与非法场景。"""

from __future__ import annotations

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _library as library


@pytest.mark.parametrize("scene", tuple(library.SCENES))
def test_build_sample_uses_scene_layout_order(scene: str) -> None:
    template = library.build_sample(scene, style=core.STYLE_CORAL)
    assert [slide.layout for slide in template.slides] == list(library.SCENES[scene])
    assert template.theme.style == core.STYLE_CORAL
    assert library.SCENE_TEXT[scene] in template.name
    assert str(len(library.SCENES[scene])) in template.description


@pytest.mark.parametrize("style", core.STYLES)
def test_build_sample_applies_style_palette(style: str) -> None:
    template = library.build_sample("launch", style=style)
    assert template.theme.palette == core.palette_for_style(style)


def test_scene_packs_cover_eight_scenes_with_page_counts() -> None:
    """场景包 8 个，每个 9–12 页，且推荐风格均为内置预设。"""
    assert len(library.SCENES) == 8
    assert set(library.SCENE_TEXT) == set(library.SCENES)
    assert set(library.SCENE_STYLE) == set(library.SCENES)
    for scene, layouts in library.SCENES.items():
        assert 9 <= len(layouts) <= 12
        assert library.scene_style(scene) in core.STYLES


def test_scene_style_rejects_unknown_scene() -> None:
    with pytest.raises(core.TemplateError, match="未知场景"):
        library.scene_style("unknown")


def test_build_sample_custom_name_and_aspect() -> None:
    template = library.build_sample("proposal", name="季度提案", aspect=core.ASPECT_4_3)
    assert template.name == "季度提案"
    assert template.aspect == core.ASPECT_4_3


def test_build_sample_rejects_unknown_scene() -> None:
    with pytest.raises(core.TemplateError, match="未知场景"):
        library.build_sample("unknown")


def test_scene_layouts_normalizes_case() -> None:
    assert library.scene_layouts(" LAUNCH ") == library.SCENES["launch"]
