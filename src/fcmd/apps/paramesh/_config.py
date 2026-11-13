"""paramesh 网格配置（``paramesh/1``）：尺度分级、壁厚层数与局部加密参数。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn, cast

import yaml

from fcmd.apps.paramesh._errors import ParameshError

__all__ = [
    "DEFAULT_DIVISORS",
    "LEVELS",
    "SCHEMA",
    "MeshConfig",
    "config_text",
    "default_config",
    "load_config",
    "parse_config",
]

SCHEMA = "paramesh/1"
DIMENSION = "axisymmetric-2d"
LEVELS = ("coarse", "medium", "fine")
DEFAULT_DIVISORS: dict[str, int] = {"coarse": 8, "medium": 16, "fine": 32}

_KNOWN_KEYS = frozenset(
    {
        "schema",
        "dimension",
        "reaction_zone_mm",
        "levels",
        "wall_layers",
        "growth",
        "refinement_factor",
        "initiation_zone_mm",
        "thin_layer_threshold_mm",
        "section_angle_deg",
    }
)


def _fail(message: str) -> NoReturn:
    raise ParameshError(message)


@dataclass(frozen=True, slots=True)
class MeshConfig:
    """网格生成参数；未给出的项走默认值。"""

    dimension: str = DIMENSION
    reaction_zone_mm: float | None = None
    divisors: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_DIVISORS))
    wall_layers_min: int = 6
    wall_layers_max: int = 10
    growth: float = 1.2
    refinement_factor: float = 0.5
    initiation_zone_mm: float | None = None
    thin_layer_threshold_mm: float | None = None
    section_angle_deg: float | None = None

    def divisor(self, level: str) -> int:
        """取某一档的全局尺寸分母；未知档位直接报错。"""
        try:
            return self.divisors[level]
        except KeyError as exc:
            raise ParameshError(f"未知网格档位：{level!r}，可选 {', '.join(LEVELS)}") from exc

    def __post_init__(self) -> None:  # noqa: PLR0912 - 逐项校验配置，线性罗列更直观
        if self.dimension != DIMENSION:
            raise ParameshError(f"v1 仅支持 dimension={DIMENSION}（二维轴对称）；三维接口预留，尚未实现")
        missing = [level for level in LEVELS if level not in self.divisors]
        if missing:
            _fail(f"levels 缺少档位：{', '.join(missing)}")
        unknown = sorted(set(self.divisors) - set(LEVELS))
        if unknown:
            _fail(f"levels 含未知档位：{', '.join(unknown)}")
        for level, divisor in self.divisors.items():
            if isinstance(divisor, bool) or not isinstance(divisor, int) or divisor <= 0:
                _fail(f"levels.{level} 必须是正整数")
        if self.wall_layers_min < 1:
            _fail("wall_layers.min 必须 ≥ 1")
        if self.wall_layers_max < self.wall_layers_min:
            _fail("wall_layers.max 必须 ≥ wall_layers.min")
        if not math.isfinite(self.growth) or self.growth <= 1.0:
            _fail("growth 必须 > 1")
        if not 0.0 < self.refinement_factor < 1.0:
            _fail("refinement_factor 必须在 (0, 1) 内")
        if self.reaction_zone_mm is not None and (
            not math.isfinite(self.reaction_zone_mm) or self.reaction_zone_mm <= 0.0
        ):
            _fail("reaction_zone_mm 必须 > 0")
        if self.initiation_zone_mm is not None and (
            not math.isfinite(self.initiation_zone_mm) or self.initiation_zone_mm <= 0.0
        ):
            _fail("initiation_zone_mm 必须 > 0")
        if self.thin_layer_threshold_mm is not None and (
            not math.isfinite(self.thin_layer_threshold_mm) or self.thin_layer_threshold_mm <= 0.0
        ):
            _fail("thin_layer_threshold_mm 必须 > 0")
        if self.section_angle_deg is not None and not 0.0 <= self.section_angle_deg < 360.0:
            _fail("section_angle_deg 必须在 [0, 360) 内")


def default_config() -> MeshConfig:
    """默认配置；``reaction_zone_mm`` 不在默认值里，必须由调用方给出。"""
    return MeshConfig()


def _mapping(value: Any, path: str, *, required: bool) -> dict[str, Any] | None:
    if value is None:
        if required:
            _fail(f"{path} 缺失")
        return None
    if not isinstance(value, dict):
        _fail(f"{path} 必须是映射，实际为 {type(value).__name__}")
    return cast("dict[str, Any]", value)


def _number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{path} 必须是数值，实际为 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{path} 必须是有限数值")
    return number


def _integer(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{path} 必须是整数，实际为 {type(value).__name__}")
    return value


def parse_config(text: str, source: str = "<memory>") -> tuple[MeshConfig, tuple[str, ...]]:  # noqa: PLR0912
    """解析 ``paramesh/1`` 配置；未知字段忽略并警告，已知字段非法则报错。"""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParameshError(f"{source}: YAML 解析失败：{exc}") from exc
    if not isinstance(document, dict):
        raise ParameshError(f"{source}: 顶层必须是映射")
    root = cast("dict[str, Any]", document)
    if root.get("schema") != SCHEMA:
        raise ParameshError(f"{source}: schema 必须是 {SCHEMA}，实际为 {root.get('schema')!r}")
    warnings: list[str] = []
    unknown = sorted(set(root) - _KNOWN_KEYS)
    if unknown:
        warnings.append(f"{source}: 含未知字段，已忽略：{', '.join(unknown)}")

    dimension = root.get("dimension", DIMENSION)
    if not isinstance(dimension, str):
        _fail(f"{source}: dimension 必须是文本")

    levels_raw = _mapping(root.get("levels"), f"{source}: levels", required=False)
    divisors = dict(DEFAULT_DIVISORS)
    if levels_raw is not None:
        for level in LEVELS:
            value = _integer(levels_raw.get(level), f"{source}: levels.{level}")
            if value is not None:
                divisors[level] = value
        extra = sorted(set(levels_raw) - set(LEVELS))
        if extra:
            warnings.append(f"{source}: levels 含未知档位，已忽略：{', '.join(extra)}")

    wall_raw = _mapping(root.get("wall_layers"), f"{source}: wall_layers", required=False)
    wall_min = 6
    wall_max = 10
    if wall_raw is not None:
        low = _integer(wall_raw.get("min"), f"{source}: wall_layers.min")
        high = _integer(wall_raw.get("max"), f"{source}: wall_layers.max")
        if low is not None:
            wall_min = low
        if high is not None:
            wall_max = high
        extra = sorted(set(wall_raw) - {"min", "max"})
        if extra:
            warnings.append(f"{source}: wall_layers 含未知字段，已忽略：{', '.join(extra)}")

    growth = _number(root.get("growth"), f"{source}: growth")
    refinement = _number(root.get("refinement_factor"), f"{source}: refinement_factor")
    config = MeshConfig(
        dimension=dimension,
        reaction_zone_mm=_number(root.get("reaction_zone_mm"), f"{source}: reaction_zone_mm"),
        divisors=divisors,
        wall_layers_min=wall_min,
        wall_layers_max=wall_max,
        growth=1.2 if growth is None else growth,
        refinement_factor=0.5 if refinement is None else refinement,
        initiation_zone_mm=_number(root.get("initiation_zone_mm"), f"{source}: initiation_zone_mm"),
        thin_layer_threshold_mm=_number(root.get("thin_layer_threshold_mm"), f"{source}: thin_layer_threshold_mm"),
        section_angle_deg=_number(root.get("section_angle_deg"), f"{source}: section_angle_deg"),
    )
    return config, tuple(warnings)


def load_config(path: Path | None) -> tuple[MeshConfig, tuple[str, ...]]:
    """读取配置文件；``None`` 返回默认配置。"""
    if path is None:
        return default_config(), ()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameshError(f"无法读取网格配置 {path}：{exc}") from exc
    return parse_config(text, str(path))


def config_text() -> str:
    """示例配置文件文本（全部标为占位，不是真实配方或产品尺寸）。"""
    return """\
# paramesh 网格配置（占位示例：所有数值都不是真实配方或产品尺寸）
schema: paramesh/1
dimension: axisymmetric-2d
reaction_zone_mm: 6.0        # 占位：反应区尺度，必须由使用方给出
levels:
  coarse: 8
  medium: 16
  fine: 32
wall_layers:
  min: 6
  max: 10
growth: 1.2
refinement_factor: 0.5
initiation_zone_mm: 4.0      # 占位：起爆端成长区长度
thin_layer_threshold_mm: 2.0 # 占位：小于该厚度时加密药层
section_angle_deg: null      # null = 自动选择最薄壁 / 最薄药层截面
"""
