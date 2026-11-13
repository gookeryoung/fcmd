"""paramcad 测试共用的几何 / 工艺构造器。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fcmd.apps.paramcad._model import EccentricityCarry, GeometryState, PassSpec, ProcessSpec, TubeModel
from fcmd.apps.paramcad._quantity import LENGTH_UNIT, Quantity, quantity

StateFactory = Callable[..., GeometryState]
ProcessFactory = Callable[..., ProcessSpec]
ModelFactory = Callable[..., TubeModel]


def _length(value: float | Quantity | None, default: float) -> Quantity:
    if isinstance(value, Quantity):
        return value
    return quantity(default if value is None else value, LENGTH_UNIT)


@pytest.fixture
def make_state() -> StateFactory:
    """构造带默认值的几何状态；传入 :class:`Quantity` 可表达 TBD。"""

    def factory(  # noqa: PLR0913 - 测试构造器，每个几何量都可单独覆盖
        *,
        outer: float | Quantity | None = 30.0,
        inner: float | Quantity | None = 24.0,
        length: float | Quantity | None = 200.0,
        wall: float | Quantity | None = 3.0,
        wall_min: float | Quantity | None = 2.6,
        wall_max: float | Quantity | None = 3.4,
        radial: float | Quantity | None = 0.4,
        circumferential: float | Quantity | None = 0.0,
        angle: float | Quantity | None = 0.0,
    ) -> GeometryState:
        return GeometryState(
            outer_diameter=_length(outer, 30.0),
            inner_diameter=_length(inner, 24.0),
            length=_length(length, 200.0),
            wall_nominal=_length(wall, 3.0),
            wall_min=_length(wall_min, 2.6),
            wall_max=_length(wall_max, 3.4),
            eccentricity_radial=_length(radial, 0.4),
            eccentricity_circumferential=_length(circumferential, 0.0),
            eccentricity_angle_deg=quantity(0.0 if angle is None else angle, "deg")
            if not isinstance(angle, Quantity)
            else angle,
        )

    return factory


@pytest.fixture
def make_process() -> ProcessFactory:
    """默认两道次（减径 10% / 8%，壁厚变化 -2%），偏心按因子传递。"""

    def factory(
        *,
        passes: tuple[PassSpec, ...] | None = None,
        cumulative: float | None = None,
        carry: EccentricityCarry | None = "factor",
        finished: Quantity | None = None,
    ) -> ProcessSpec:
        return ProcessSpec(
            passes=passes if passes is not None else (PassSpec(1, 0.10, -0.02, 12.0), PassSpec(2, 0.08, -0.02, 10.0)),
            cumulative_reduction=cumulative,
            eccentricity_carry=carry,
            finished_length=finished,
        )

    return factory


@pytest.fixture
def make_model(make_state: StateFactory, make_process: ProcessFactory) -> ModelFactory:
    """由前态 + 工艺构造模型。"""

    def factory(
        model_id: str = "m1",
        *,
        before: GeometryState | None = None,
        process: ProcessSpec | None = None,
    ) -> TubeModel:
        return TubeModel.from_before(
            model_id,
            before if before is not None else make_state(),
            process if process is not None else make_process(),
        )

    return factory
