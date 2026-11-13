"""网格配置测试：默认值、显式零值、三维拒绝与未知字段警告。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fcmd.apps.paramesh._config import (
    DEFAULT_DIVISORS,
    LEVELS,
    MeshConfig,
    default_config,
    parse_config,
)
from fcmd.apps.paramesh._config import (
    config_text as shipped_config_text,
)
from fcmd.apps.paramesh._errors import ParameshError

ConfigText = Callable[..., str]


def test_default_config_has_expected_divisors() -> None:
    config = default_config()
    assert config.divisors == DEFAULT_DIVISORS
    assert config.divisor("coarse") == 8
    assert config.divisor("fine") == 32
    assert config.wall_layers_min == 6
    assert config.wall_layers_max == 10
    assert config.dimension == "axisymmetric-2d"


def test_parse_overrides_levels_and_wall_layers(config_text: ConfigText) -> None:
    config, warnings = parse_config(
        config_text(levels={"coarse": 3, "medium": 5, "fine": 9}, wall_layers={"min": 5, "max": 7})
    )
    assert config.divisors == {"coarse": 3, "medium": 5, "fine": 9}
    assert (config.wall_layers_min, config.wall_layers_max) == (5, 7)
    assert warnings == ()


def test_explicit_zero_growth_is_rejected(config_text: ConfigText) -> None:
    """显式 0 不得被静默替换为默认值。"""
    with pytest.raises(ParameshError, match="growth"):
        parse_config(config_text(growth=0))


def test_explicit_zero_refinement_is_rejected(config_text: ConfigText) -> None:
    with pytest.raises(ParameshError, match="refinement_factor"):
        parse_config(config_text(refinement_factor=0))


def test_missing_level_falls_back_to_default() -> None:
    text = """\
schema: paramesh/1
reaction_zone_mm: 2.0
levels: {coarse: 2, medium: 4}
"""
    config, warnings = parse_config(text)
    assert config.divisors == {"coarse": 2, "medium": 4, "fine": 32}
    assert warnings == ()


def test_unknown_level_warns(config_text: ConfigText) -> None:
    config, warnings = parse_config(config_text(levels={"extra": 3}))
    assert set(config.divisors) == set(LEVELS)
    assert any("未知档位" in warning for warning in warnings)


def test_unknown_root_field_warns(config_text: ConfigText) -> None:
    _config, warnings = parse_config(config_text(unexpected=1))
    assert any("未知字段" in warning for warning in warnings)


def test_three_dimensional_rejected() -> None:
    with pytest.raises(ParameshError, match="二维轴对称"):
        MeshConfig(dimension="cartesian-3d")


def test_wall_layers_range_checked() -> None:
    with pytest.raises(ParameshError, match=r"wall_layers\.max"):
        MeshConfig(wall_layers_min=8, wall_layers_max=4)


def test_section_angle_range_checked(config_text: ConfigText) -> None:
    with pytest.raises(ParameshError, match="section_angle_deg"):
        parse_config(config_text(section_angle_deg=360))


def test_divisor_unknown_level() -> None:
    with pytest.raises(ParameshError, match="未知网格档位"):
        default_config().divisor("ultra")


def test_shipped_config_text_parses() -> None:
    config, warnings = parse_config(shipped_config_text())
    assert config.reaction_zone_mm == 6.0
    assert warnings == ()
    assert "schema: paramesh/1" in shipped_config_text()


def test_non_mapping_top_level_rejected() -> None:
    with pytest.raises(ParameshError, match="顶层"):
        parse_config("- 1\n")
