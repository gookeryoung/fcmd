"""paramesh 测试共用的模型 / 配置构造器（全部数值为占位）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import yaml

from fcmd.apps.paramesh._config import MeshConfig, parse_config
from fcmd.apps.paramesh._mesh import Mesh, build_mesh
from fcmd.apps.paramesh._model import parse_models

ModelText = Callable[..., str]
ConfigText = Callable[..., str]
MeshBuilder = Callable[..., Mesh]

_BASE_MODEL: dict[str, Any] = {
    "schema": "dynacalc-model/1",
    "models": [
        {
            "id": "m1",
            "units": {"length": "mm", "density": "g/cm^3", "velocity": "m/s"},
            "geometry": {
                "outer_diameter": 40.0,
                "inner_diameter": 20.0,
                "length": 20.0,
                "wall_thickness": {"nominal": 10.0, "min": 9.5, "max": 10.5},
            },
            "density_field": {"axial": {"kind": "uniform", "value": 1.6}},
            "interface": {"gap_mm": 0.2},
        }
    ],
}

_BASE_CONFIG: dict[str, Any] = {
    "schema": "paramesh/1",
    "dimension": "axisymmetric-2d",
    "reaction_zone_mm": 2.0,
    "levels": {"coarse": 2, "medium": 4, "fine": 8},
    "wall_layers": {"min": 4, "max": 8},
    "growth": 1.2,
    "refinement_factor": 0.5,
    "initiation_zone_mm": 4.0,
    "thin_layer_threshold_mm": 1.0,
}


def _merge(target: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _merge(current, value)
        else:
            target[key] = value
    return target


def _dump(document: dict[str, Any]) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


@pytest.fixture
def model_text() -> ModelText:
    """生成单模型 YAML；关键字参数递归合并进 ``models[0]``。"""

    def factory(**overrides: Any) -> str:
        document = _dump({"schema": _BASE_MODEL["schema"], "models": _BASE_MODEL["models"]})
        model = yaml.safe_load(document)["models"][0]
        _merge(model, overrides)
        return _dump({"schema": _BASE_MODEL["schema"], "models": [model]})

    return factory


@pytest.fixture
def config_text() -> ConfigText:
    """生成网格配置 YAML；关键字参数合并进根映射。"""

    def factory(**overrides: Any) -> str:
        document = yaml.safe_load(_dump(_BASE_CONFIG))
        _merge(document, overrides)
        return _dump(document)

    return factory


@pytest.fixture
def mesh_builder(model_text: ModelText, config_text: ConfigText) -> MeshBuilder:
    """按需生成网格：``mesh_builder(level, model={...}, config={...})``。"""

    def factory(
        level: str = "coarse",
        *,
        model: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> Mesh:
        parsed_model = parse_models(model_text(**(model or {})))[0]
        parsed_config, _warnings = parse_config(config_text(**(config or {})))
        assert isinstance(parsed_config, MeshConfig)
        return build_mesh(parsed_model, parsed_config, level)

    return factory
