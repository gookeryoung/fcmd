"""Tk 预览器的非界面逻辑测试：设置持久化、区域配色与工具注册。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fcmd.apps.paramesh import parameshw
from fcmd.core.toolkit import _TOOL_REGISTRY


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "paramesh.json"
    settings = parameshw.Settings(
        geometry="1200x800",
        model_path="model.yaml",
        config_path="model.mesh.yaml",
        level="fine",
    )
    parameshw.save_settings(settings, path)
    assert parameshw.load_settings(path) == settings


def test_settings_missing_file_defaults(tmp_path: Path) -> None:
    assert parameshw.load_settings(tmp_path / "missing.json") == parameshw.Settings()


def test_settings_invalid_level_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "paramesh.json"
    parameshw.save_settings(parameshw.Settings(level="ultra"), path)
    assert parameshw.load_settings(path).level == "coarse"


def test_settings_path_uses_app_name(tmp_path: Path) -> None:
    assert parameshw.settings_path().name == "paramesh.json"


def test_region_color_mapping() -> None:
    palette = SimpleNamespace(warning="#warn", accent="#accent", border_strong="#wall", muted="#muted")
    assert parameshw.region_color("propellant", palette) == "#warn"
    assert parameshw.region_color("gap", palette) == "#accent"
    assert parameshw.region_color("wall", palette) == "#wall"
    assert parameshw.region_color("unknown", palette) == "#muted"


def test_default_config_placeholder() -> None:
    config = parameshw._cli_default_config()
    assert config.dimension == "axisymmetric-2d"
    assert config.reaction_zone_mm is None


def test_tool_registration() -> None:
    assert set(_TOOL_REGISTRY["parameshw"]) == {None}
