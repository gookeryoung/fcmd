"""缺陷参数化：药管间隙、局部脱粘、局部薄药层与端部疏松密度。

结构对齐 ``dynacalc-model/1`` 的 ``interface`` / ``density_field.local_defects``：
间隙与脱粘状态属于界面，局部缺陷以「标识 + 类型 + 位置 + 尺寸 + 严重度」表达。
端部疏松对应 ``density_field.end_loose``（``length_fraction`` + ``density``），与
轴向密度折线并列；两者的未标定状态都只是 TBD，不是错误。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from fcmd.apps.paramcad._quantity import DENSITY_UNIT, LENGTH_UNIT, RATIO_UNIT, ParamcadError, Quantity, tbd

__all__ = [
    "DEBOND_STATES",
    "DEFECT_KINDS",
    "DebondState",
    "DefectKind",
    "DefectParams",
    "LocalDefect",
    "default_defects",
    "format_defects",
]

DefectKind = Literal["void", "thin_layer", "crack", "debond"]
DEFECT_KINDS: tuple[DefectKind, ...] = ("void", "thin_layer", "crack", "debond")
DebondState = Literal["bonded", "debonded", "tbd"]
DEBOND_STATES: tuple[DebondState, ...] = ("bonded", "debonded", "tbd")


@dataclass(frozen=True, slots=True)
class LocalDefect:
    """一个局部缺陷；位置用相对量表达，尺寸用 mm，严重度由项目定义。

    ``axial`` / ``radial`` 为 0–1 的相对位置，``angle_deg`` 为周向角；
    ``size_mm`` 与 ``severity`` 允许未标定（契约里写作 ``TBD``）。
    """

    defect_id: str
    kind: DefectKind
    axial: float | None = None
    radial: float | None = None
    angle_deg: float | None = None
    size_mm: Quantity | None = None
    severity: Quantity | None = None

    def __post_init__(self) -> None:
        if not self.defect_id.strip():
            raise ParamcadError("缺陷标识不能为空")
        if self.kind not in DEFECT_KINDS:
            raise ParamcadError(f"未知缺陷类型：{self.kind!r}")
        if self.axial is not None and not 0.0 <= self.axial <= 1.0:
            raise ParamcadError(f"缺陷轴向位置必须在 0–1：{self.axial!r}")
        if self.radial is not None and not 0.0 <= self.radial <= 1.0:
            raise ParamcadError(f"缺陷径向位置必须在 0–1：{self.radial!r}")
        if self.angle_deg is not None and not 0.0 <= self.angle_deg < 360.0:
            raise ParamcadError(f"缺陷周向角必须在 [0, 360)：{self.angle_deg!r}")
        if self.size_mm is not None and self.size_mm.unit != LENGTH_UNIT:
            raise ParamcadError(f"缺陷尺寸单位固定为 {LENGTH_UNIT}")
        if self.severity is not None and self.severity.unit != RATIO_UNIT:
            raise ParamcadError("缺陷严重度是无量纲量")


def _unknown_gap() -> Quantity:
    return tbd(LENGTH_UNIT, "药管间隙未标定")


def _unknown_extent() -> Quantity:
    return tbd("deg", "脱粘周向范围未标定")


def _unknown_fraction() -> Quantity:
    return tbd(RATIO_UNIT, "端部疏松段长度占比未标定")


def _unknown_density() -> Quantity:
    return tbd(DENSITY_UNIT, "端部疏松密度未标定")


def _unknown_severity() -> Quantity:
    return tbd(RATIO_UNIT, "严重度未定义")


@dataclass(frozen=True, slots=True)
class DefectParams:
    """缺陷集合：界面状态 + 局部缺陷 + 端部疏松。

    ``debond_state`` 与 ``debond_extent_deg`` 对应契约 ``interface.debond``；
    ``end_loose_length_fraction`` 与 ``end_loose_density`` 对应
    ``density_field.end_loose``（与轴向密度折线并列的独立字段）。
    """

    gap_mm: Quantity = field(default_factory=_unknown_gap)
    debond_state: DebondState = "tbd"
    debond_extent_deg: Quantity = field(default_factory=_unknown_extent)
    local_defects: tuple[LocalDefect, ...] = ()
    end_loose_length_fraction: Quantity = field(default_factory=_unknown_fraction)
    end_loose_density: Quantity = field(default_factory=_unknown_density)

    def __post_init__(self) -> None:
        if self.gap_mm.unit != LENGTH_UNIT:
            raise ParamcadError(f"药管间隙单位固定为 {LENGTH_UNIT}")
        if self.gap_mm.value is not None and self.gap_mm.value < 0:
            raise ParamcadError("药管间隙不能为负")
        if self.debond_state not in DEBOND_STATES:
            raise ParamcadError(f"未知脱粘状态：{self.debond_state!r}")
        if self.debond_extent_deg.unit != "deg":
            raise ParamcadError("脱粘周向范围单位固定为 deg")
        extent = self.debond_extent_deg.value
        if extent is not None and not 0.0 <= extent <= 360.0:
            raise ParamcadError(f"脱粘周向范围必须在 0–360：{extent!r}")
        if self.end_loose_length_fraction.unit != RATIO_UNIT:
            raise ParamcadError("端部疏松段长度占比是无量纲量")
        fraction = self.end_loose_length_fraction.value
        if fraction is not None and (not math.isfinite(fraction) or not 0.0 < fraction <= 1.0):
            raise ParamcadError(f"端部疏松段长度占比必须在 (0, 1]：{fraction!r}")
        if self.end_loose_density.unit != DENSITY_UNIT:
            raise ParamcadError(f"端部疏松密度单位固定为 {DENSITY_UNIT}")
        density = self.end_loose_density.value
        if density is not None and (not math.isfinite(density) or density <= 0):
            raise ParamcadError("端部疏松密度必须为正")
        seen: set[str] = set()
        for item in self.local_defects:
            if item.defect_id in seen:
                raise ParamcadError(f"缺陷标识重复：{item.defect_id}")
            seen.add(item.defect_id)


def default_defects() -> DefectParams:
    """全 TBD 的缺陷集合（不内置任何「典型」数值）。"""
    return DefectParams()


def defect_severity() -> Quantity:
    """未定义的严重度占位。"""
    return _unknown_severity()


def format_defects(defects: DefectParams) -> list[str]:
    """把缺陷集合格式化为可读行。"""
    from fcmd.apps.paramcad._quantity import format_quantity

    lines = [
        f"  药管间隙：{format_quantity(defects.gap_mm)}",
        f"  脱粘状态：{defects.debond_state}",
        f"  脱粘范围：{format_quantity(defects.debond_extent_deg)}",
        f"  端部疏松段长度占比：{format_quantity(defects.end_loose_length_fraction)}",
        f"  端部疏松密度：{format_quantity(defects.end_loose_density)}",
    ]
    if not defects.local_defects:
        lines.append("  局部缺陷：无")
        return lines
    lines.append("  局部缺陷：")
    for item in defects.local_defects:
        size = "TBD" if item.size_mm is None else format_quantity(item.size_mm)
        severity = "TBD" if item.severity is None else format_quantity(item.severity)
        lines.append(
            f"    {item.defect_id} · {item.kind} · 轴向 {item.axial} · 径向 {item.radial} · "
            f"角度 {item.angle_deg} · 尺寸 {size} · 严重度 {severity}"
        )
    return lines
