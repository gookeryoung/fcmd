"""paramcad 基础量：带单位与 TBD 语义的标量。

模型里每个数值都分成「已冻结」与「未标定（TBD）」两种状态。TBD 必须携带原因，
避免导出时被空值或零值悄悄顶替；单位在构造期就固定，不做任何隐式换算。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "DENSITY_UNIT",
    "LENGTH_UNIT",
    "RATIO_UNIT",
    "TBD_TEXT",
    "VELOCITY_UNIT",
    "ParamcadError",
    "Quantity",
    "format_quantity",
    "quantity",
    "tbd",
]

LENGTH_UNIT = "mm"
DENSITY_UNIT = "g/cm^3"
VELOCITY_UNIT = "m/s"
RATIO_UNIT = "1"
TBD_TEXT = "TBD"


class ParamcadError(ValueError):
    """参数化建模输入不成立（非法值、单位不符、TBD 传播失败等）。"""


@dataclass(frozen=True, slots=True)
class Quantity:
    """一个带单位的标量；``value is None`` 表示未标定。

    Attributes
    ----------
    value:
        已冻结数值；未标定时为 ``None``。
    unit:
        量纲单位，长度固定 ``mm``、密度固定 ``g/cm^3``、角度固定 ``deg``。
    reason:
        仅 TBD 使用：为什么未标定。已冻结数值不得携带原因。
    """

    value: float | None
    unit: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.unit:
            raise ParamcadError("量的单位不能为空")
        if self.value is None:
            if not self.reason:
                raise ParamcadError("TBD 值必须给出原因")
            return
        if not math.isfinite(self.value):
            raise ParamcadError(f"数值必须是有限数：{self.value!r}")
        if self.reason:
            raise ParamcadError("已冻结的数值不应携带 TBD 原因")

    @property
    def is_tbd(self) -> bool:
        """是否未标定。"""
        return self.value is None

    def number(self, label: str) -> float:
        """取数值；未标定时抛出带标签与原因的 :class:`ParamcadError`。"""
        if self.value is None:
            raise ParamcadError(f"{label} 未标定（{self.reason}）")
        return self.value


def quantity(value: float, unit: str) -> Quantity:
    """构造已冻结的量（整数会被转成 ``float``，保证指纹口径一致）。"""
    return Quantity(value=float(value), unit=unit)


def tbd(unit: str, reason: str) -> Quantity:
    """构造未标定的量。"""
    return Quantity(value=None, unit=unit, reason=reason)


def format_quantity(item: Quantity, *, digits: int = 4) -> str:
    """把量格式化为 ``数值 单位`` 或 ``TBD（原因）``。"""
    if item.value is None:
        return f"{TBD_TEXT}（{item.reason}）"
    text = f"{item.value:.{digits}f}".rstrip("0").rstrip(".")
    return f"{text} {item.unit}".strip()
