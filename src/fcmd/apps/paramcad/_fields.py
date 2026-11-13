"""分布场：轴向 / 径向 / 周向密度分布。

两种来源都必须可追溯：

- :class:`AnalyticProfile`：解析函数（常数、线性、端部疏松）；
- :class:`MeasuredProfile`：实测点线性插值。

解析函数全部是分段线性，:meth:`AnalyticProfile.vertices` 给出精确等价的折线顶点，
导出时直接写成契约里的 ``polyline`` 采样，不引入采样误差。

契约允许折线样本的 ``density`` 为 ``TBD``（文件层合法），因此实测点数值用
``float | None`` 表达：``None`` 表示未标定，采样时整段返回 ``None``；预览据此标注
「未标定」，而不是把 TBD 当解析错误。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from fcmd.apps.paramcad._quantity import DENSITY_UNIT, ParamcadError

__all__ = [
    "DENSITY_AXES",
    "AnalyticProfile",
    "DensityAxis",
    "Field",
    "MeasuredProfile",
    "Profile",
    "fields_by_axis",
]

DensityAxis = Literal["axial", "radial", "circumferential"]
DENSITY_AXES: tuple[DensityAxis, ...] = ("axial", "radial", "circumferential")
ProfileKind = Literal["constant", "linear", "end_loose"]


def _check_unit_interval(value: float, label: str) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ParamcadError(f"{label} 必须是 0–1 之间的有限数：{value!r}")
    return float(value)


def _check_finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ParamcadError(f"{label} 必须是有限数：{value!r}")
    return float(value)


@dataclass(frozen=True, slots=True)
class AnalyticProfile:
    """解析分布：``constant`` / ``linear`` / ``end_loose``。

    - ``constant``：处处等于 ``value``；
    - ``linear``：``s=0`` 处 ``value``，``s=1`` 处 ``end_value``；
    - ``end_loose``：``s <= 1 - loose_length`` 保持 ``value``，随后线性变化到
      ``s=1`` 的 ``end_value``（端部疏松型）。
    """

    kind: ProfileKind
    value: float
    end_value: float | None = None
    loose_length: float | None = None

    def __post_init__(self) -> None:
        _check_finite(self.value, "解析分布起始值")
        if self.kind == "constant":
            if self.end_value is not None or self.loose_length is not None:
                raise ParamcadError("constant 分布不接受端值或疏松长度")
            return
        if self.kind not in ("linear", "end_loose"):
            raise ParamcadError(f"未知解析分布类型：{self.kind!r}")
        if self.end_value is None:
            raise ParamcadError(f"{self.kind} 分布必须给出端值")
        _check_finite(self.end_value, "解析分布端值")
        if self.kind == "linear":
            if self.loose_length is not None:
                raise ParamcadError("linear 分布不接受疏松长度")
            return
        if self.loose_length is None:
            raise ParamcadError("end_loose 分布必须给出疏松段长度占比")
        span = _check_unit_interval(self.loose_length, "疏松段长度占比")
        if span == 0.0:
            raise ParamcadError("疏松段长度占比必须大于 0")

    @property
    def is_calibrated(self) -> bool:
        """解析分布的参数全部是具体值，始终已标定。"""
        return True

    def sample(self, position: float) -> float:
        """在归一化位置 ``position``（0–1）取值，超出范围按端点夹紧。"""
        s = min(max(float(position), 0.0), 1.0)
        if self.kind == "constant":
            return self.value
        end = self.value if self.end_value is None else self.end_value
        if self.kind == "linear":
            return self.value + (end - self.value) * s
        span = 0.0 if self.loose_length is None else self.loose_length
        start = 1.0 - span
        if s <= start:
            return self.value
        return self.value + (end - self.value) * ((s - start) / span)

    def vertices(self) -> tuple[tuple[float, float], ...]:
        """返回与解析函数逐点等价的折线顶点。"""
        if self.kind == "constant":
            return ((0.0, self.value), (1.0, self.value))
        end = self.value if self.end_value is None else self.end_value
        if self.kind == "linear":
            return ((0.0, self.value), (1.0, end))
        span = 0.0 if self.loose_length is None else self.loose_length
        return ((0.0, self.value), (1.0 - span, self.value), (1.0, end))


@dataclass(frozen=True, slots=True)
class MeasuredProfile:
    """实测点折线：``(position, density)`` 按位置严格递增，至少两点。

    ``density`` 为 ``None`` 表示该点为 ``TBD``（契约允许），采样时返回 ``None``。
    """

    points: tuple[tuple[float, float | None], ...]

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ParamcadError("实测分布至少需要 2 个点")
        previous = -1.0
        for index, (position, value) in enumerate(self.points):
            spot = _check_unit_interval(position, f"实测点[{index}] 位置")
            if spot <= previous:
                raise ParamcadError("实测点位置必须严格递增")
            if value is not None:
                _check_finite(value, f"实测点[{index}] 数值")
            previous = spot

    @property
    def is_calibrated(self) -> bool:
        """所有样本都是具体值时已标定。"""
        return all(value is not None for _, value in self.points)

    def sample(self, position: float) -> float | None:
        """线性插值；超出首尾点时按端点值夹紧。

        区间任一端为 ``TBD`` 时返回 ``None``，不做数值替代。
        """
        s = float(position)
        first_position, first_value = self.points[0]
        last_position, last_value = self.points[-1]
        if s <= first_position:
            return first_value
        if s >= last_position:
            return last_value
        for (left_position, left_value), (right_position, right_value) in zip(
            self.points[:-1], self.points[1:], strict=True
        ):
            if left_position <= s <= right_position:
                if left_value is None or right_value is None:
                    return None
                span = right_position - left_position
                if span == 0:
                    return right_value
                ratio = (s - left_position) / span
                return left_value + (right_value - left_value) * ratio
        return last_value

    def vertices(self) -> tuple[tuple[float, float | None], ...]:
        """返回实测点本身（导出时原样写入，``None`` 渲染为 ``TBD``）。"""
        return self.points


type Profile = AnalyticProfile | MeasuredProfile


@dataclass(frozen=True, slots=True)
class Field:
    """一个分布场：轴、来源与剖面。

    ``source`` 必填，用于追溯「解析假设 / 哪份实测」；契约的
    ``density_field`` 没有来源字段，因此导出时写入 ``meta.field_sources``
    （``meta`` 不参与指纹，见 ADR 0021）。
    """

    axis: DensityAxis
    profile: Profile
    source: str
    note: str = ""
    unit: str = DENSITY_UNIT

    def __post_init__(self) -> None:
        if self.axis not in DENSITY_AXES:
            raise ParamcadError(f"未知分布轴：{self.axis!r}")
        if not self.source.strip():
            raise ParamcadError("分布场必须注明来源")
        if self.unit != DENSITY_UNIT:
            raise ParamcadError(f"密度单位固定为 {DENSITY_UNIT}，收到 {self.unit!r}")

    def sample(self, position: float) -> float | None:
        """在归一化位置取值；样本未标定时返回 ``None``。"""
        return self.profile.sample(position)

    @property
    def is_calibrated(self) -> bool:
        """分布场是否全部样本已标定。"""
        return self.profile.is_calibrated

    def vertices(self) -> tuple[tuple[float, float | None], ...]:
        """等价折线顶点；``None`` 表示未标定样本。"""
        return self.profile.vertices()


def fields_by_axis(fields: tuple[Field, ...]) -> dict[str, Field]:
    """按轴索引分布场；同一轴出现多次视为输入错误。"""
    indexed: dict[str, Field] = {}
    for item in fields:
        if item.axis in indexed:
            raise ParamcadError(f"同一轴最多一个分布场：{item.axis}")
        indexed[item.axis] = item
    return indexed
