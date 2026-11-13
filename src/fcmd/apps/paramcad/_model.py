"""几何与拉拔工艺模型：参数语义、推导规则与 TBD 传播。

几何量分「拉拔前」与「拉拔后」两态。后态只从「前态 + 显式工艺参数」推导：

- 外径按各道次相对减径率连乘，任一减径率未标定则后态外径 TBD；
- 壁厚按各道次相对壁厚变化率连乘，任一道次缺失变化率即整体 TBD；
- 长度只在工艺显式给出时使用，**不做体积守恒等隐式假设**；
- 壁厚极值需要显式声明偏心传递方式（绝对量保持 / 偏心因子保持），否则 TBD。

所有推导结果都以 :class:`~fcmd.apps.paramcad._quantity.Quantity` 返回，未定项
自带原因，导出与预览都能直接展示，不会退化成数值 0。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from fcmd.apps.paramcad._defects import DefectParams, default_defects, format_defects
from fcmd.apps.paramcad._fields import Field, fields_by_axis
from fcmd.apps.paramcad._quantity import (
    LENGTH_UNIT,
    ParamcadError,
    Quantity,
    format_quantity,
    tbd,
)

__all__ = [
    "CONFIDENCE_DOMAINS",
    "CONFIDENCE_LEVELS",
    "CaseScope",
    "CircumferentialWall",
    "EccentricityCarry",
    "GeometryState",
    "PassSpec",
    "ProcessSpec",
    "TubeModel",
    "WallSample",
    "derive_after",
    "format_summary",
    "tbd_report",
    "validate_model",
]

CONFIDENCE_DOMAINS: tuple[str, ...] = ("geometry", "drawing", "density_field", "interface")
CONFIDENCE_LEVELS: tuple[str, ...] = ("public", "controlled", "uncalibrated")
EccentricityCarry = Literal["absolute", "factor"]
WallKind = Literal["uniform", "polyline", "tbd"]
WALL_KINDS: tuple[WallKind, ...] = ("uniform", "polyline", "tbd")
ANGLE_UNIT = "deg"


def _check_angle(number: float, label: str) -> float:
    if not math.isfinite(number) or not 0.0 <= number < 360.0:
        raise ParamcadError(f"{label} 必须在 [0, 360)：{number!r}")
    return float(number)


@dataclass(frozen=True, slots=True)
class WallSample:
    """周向壁厚样本：角度 ``[0, 360)``，壁厚单位 mm，允许未标定。"""

    angle_deg: float
    thickness: Quantity

    def __post_init__(self) -> None:
        _check_angle(self.angle_deg, "周向壁厚样本角度")
        if self.thickness.unit != LENGTH_UNIT:
            raise ParamcadError(f"周向壁厚单位固定为 {LENGTH_UNIT}")
        if self.thickness.value is not None and self.thickness.value <= 0:
            raise ParamcadError(f"周向壁厚必须为正：{self.thickness.value!r}")


@dataclass(frozen=True, slots=True)
class CircumferentialWall:
    """壁厚周向分布：``uniform`` / ``polyline`` / ``tbd`` 三种形态。

    契约允许 ``polyline`` 的样本壁厚为 ``TBD``；``uniform`` 的 ``value`` 不允许
    未标定（未标定请用 ``kind: tbd``）。角度按升序严格递增、至少 2 点。
    """

    kind: WallKind = "tbd"
    value: float | None = None
    samples: tuple[WallSample, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in WALL_KINDS:
            raise ParamcadError(f"未知周向壁厚形态：{self.kind!r}")
        if self.kind == "tbd":
            if self.value is not None or self.samples:
                raise ParamcadError("tbd 形态不接受数值或样本")
            return
        if self.kind == "uniform":
            if self.samples:
                raise ParamcadError("uniform 形态不接受样本")
            if self.value is None:
                raise ParamcadError("uniform 形态必须给出具体壁厚")
            if not math.isfinite(self.value) or self.value <= 0:
                raise ParamcadError(f"周向壁厚必须为正：{self.value!r}")
            return
        if self.value is not None:
            raise ParamcadError("polyline 形态不接受单一数值")
        if len(self.samples) < 2:
            raise ParamcadError("周向壁厚折线至少需要 2 个样本")
        previous = -1.0
        for index, item in enumerate(self.samples):
            if item.angle_deg <= previous:
                raise ParamcadError(f"周向壁厚样本[{index}] 角度必须严格递增")
            previous = item.angle_deg

    @property
    def is_calibrated(self) -> bool:
        """样本全部已标定时为真；``tbd`` 形态恒为假。"""
        if self.kind == "uniform":
            return True
        if self.kind == "polyline":
            return all(item.thickness.value is not None for item in self.samples)
        return False

    @property
    def statistics(self) -> tuple[float, float, float] | None:
        """``(min, max, mean)``；未标定或 ``tbd`` 时返回 ``None``。"""
        if self.kind == "uniform":
            assert self.value is not None
            return self.value, self.value, self.value
        if self.kind == "polyline" and self.is_calibrated:
            values = [float(item.thickness.value) for item in self.samples if item.thickness.value is not None]
            return min(values), max(values), sum(values) / len(values)
        return None


@dataclass(frozen=True, slots=True)
class GeometryState:
    """一个轴向状态下的管坯几何（长度单位统一 mm，角度 deg）。"""

    outer_diameter: Quantity
    inner_diameter: Quantity
    length: Quantity
    wall_nominal: Quantity
    wall_min: Quantity
    wall_max: Quantity
    eccentricity_radial: Quantity
    eccentricity_circumferential: Quantity
    eccentricity_angle_deg: Quantity
    wall_circumferential: CircumferentialWall | None = None

    def __post_init__(self) -> None:
        self._check_units()
        self._check_numbers()
        angle = self.eccentricity_angle_deg.value
        if angle is not None:
            _check_angle(angle, "偏心方向角")

    def _check_units(self) -> None:
        for name, item in self._length_fields().items():
            if item.unit != LENGTH_UNIT:
                raise ParamcadError(f"{name} 单位固定为 {LENGTH_UNIT}，收到 {item.unit!r}")
        if self.eccentricity_angle_deg.unit != ANGLE_UNIT:
            raise ParamcadError(f"偏心方向角单位固定为 {ANGLE_UNIT}")

    def _check_numbers(self) -> None:
        lengths = self._length_fields()
        for name in ("outer_diameter", "inner_diameter", "length", "wall_nominal", "wall_min", "wall_max"):
            item = lengths[name]
            if item.value is not None and item.value <= 0:
                raise ParamcadError(f"{name} 必须为正：{item.value!r}")
        for name in ("eccentricity_radial", "eccentricity_circumferential"):
            item = lengths[name]
            if item.value is not None and item.value < 0:
                raise ParamcadError(f"{name} 不能为负：{item.value!r}")
        outer = self.outer_diameter.value
        inner = self.inner_diameter.value
        if outer is not None and inner is not None and inner >= outer:
            raise ParamcadError(f"内径必须小于外径：{inner!r} >= {outer!r}")
        nominal = self.wall_nominal.value
        low = self.wall_min.value
        high = self.wall_max.value
        if nominal is not None and low is not None and low > nominal:
            raise ParamcadError(f"最小壁厚不能大于名义壁厚：{low!r} > {nominal!r}")
        if nominal is not None and high is not None and nominal > high:
            raise ParamcadError(f"名义壁厚不能大于最大壁厚：{nominal!r} > {high!r}")
        if low is not None and high is not None and low > high:
            raise ParamcadError(f"最小壁厚不能大于最大壁厚：{low!r} > {high!r}")
        if high is not None and outer is not None and 2.0 * high >= outer:
            raise ParamcadError("最大壁厚必须小于外径的一半")

    def _length_fields(self) -> dict[str, Quantity]:
        return {
            "outer_diameter": self.outer_diameter,
            "inner_diameter": self.inner_diameter,
            "length": self.length,
            "wall_nominal": self.wall_nominal,
            "wall_min": self.wall_min,
            "wall_max": self.wall_max,
            "eccentricity_radial": self.eccentricity_radial,
            "eccentricity_circumferential": self.eccentricity_circumferential,
        }

    @property
    def wall_mean(self) -> Quantity:
        """名义壁厚：显式给出优先，否则由外径 / 内径推导。"""
        if self.wall_nominal.value is not None:
            return self.wall_nominal
        outer = self.outer_diameter.value
        inner = self.inner_diameter.value
        if outer is None or inner is None:
            return tbd(LENGTH_UNIT, "缺少外径或内径，无法推导名义壁厚")
        return Quantity((outer - inner) / 2.0, LENGTH_UNIT)

    @property
    def offset(self) -> Quantity:
        """内孔相对外圆的径向偏移 ``(max - min) / 2``。"""
        low = self.wall_min.value
        high = self.wall_max.value
        if low is None or high is None:
            return tbd(LENGTH_UNIT, "缺少壁厚极值，无法给出偏心量")
        return Quantity((high - low) / 2.0, LENGTH_UNIT)

    def tbd_items(self) -> tuple[tuple[str, str], ...]:
        """全部未标定项 ``(字段名, 原因)``，按字段声明顺序。"""
        items = (
            ("outer_diameter", self.outer_diameter),
            ("inner_diameter", self.inner_diameter),
            ("length", self.length),
            ("wall_thickness.nominal", self.wall_mean),
            ("wall_thickness.min", self.wall_min),
            ("wall_thickness.max", self.wall_max),
            ("eccentricity.radial_mm", self.eccentricity_radial),
            ("eccentricity.circumferential_mm", self.eccentricity_circumferential),
            ("eccentricity.angle_deg", self.eccentricity_angle_deg),
        )
        return tuple((name, item.reason) for name, item in items if item.value is None)


def _unknown_state(reason: str) -> GeometryState:
    """后态整体不可推导时的全 TBD 状态。"""
    return GeometryState(
        outer_diameter=tbd(LENGTH_UNIT, reason),
        inner_diameter=tbd(LENGTH_UNIT, reason),
        length=tbd(LENGTH_UNIT, reason),
        wall_nominal=tbd(LENGTH_UNIT, reason),
        wall_min=tbd(LENGTH_UNIT, reason),
        wall_max=tbd(LENGTH_UNIT, reason),
        eccentricity_radial=tbd(LENGTH_UNIT, reason),
        eccentricity_circumferential=tbd(LENGTH_UNIT, reason),
        eccentricity_angle_deg=tbd(ANGLE_UNIT, reason),
        wall_circumferential=CircumferentialWall(kind="tbd"),
    )


@dataclass(frozen=True, slots=True)
class PassSpec:
    """一个拉拔道次：相对减径率与相对壁厚变化率（缺省表示未标定）。"""

    index: int
    diameter_reduction: float | None = None
    wall_thickness_change: float | None = None
    die_angle_deg: float | None = None

    def __post_init__(self) -> None:
        if self.index < 1:
            raise ParamcadError(f"道次序号必须 >= 1：{self.index}")
        reduction = self.diameter_reduction
        if reduction is not None and (not math.isfinite(reduction) or not 0.0 <= reduction < 1.0):
            raise ParamcadError(f"道次 {self.index} 的减径率必须在 [0, 1)：{reduction!r}")
        change = self.wall_thickness_change
        if change is not None and (not math.isfinite(change) or change <= -1.0):
            raise ParamcadError(f"道次 {self.index} 的壁厚变化率必须大于 -1：{change!r}")
        angle = self.die_angle_deg
        if angle is not None and (not math.isfinite(angle) or not 0.0 < angle < 90.0):
            raise ParamcadError(f"道次 {self.index} 的模具半角必须在 (0, 90)：{angle!r}")


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """拉拔工艺：道次列表 + 偏心传递方式 + 可选成品长度。"""

    passes: tuple[PassSpec, ...]
    cumulative_reduction: float | None = None
    eccentricity_carry: EccentricityCarry | None = None
    finished_length: Quantity | None = None

    def __post_init__(self) -> None:
        if not self.passes and self.cumulative_reduction is None:
            raise ParamcadError("拉拔工艺至少需要一个道次，或给出累计减径率")
        indices = [item.index for item in self.passes]
        if indices != sorted(indices) or len(set(indices)) != len(indices):
            raise ParamcadError("拉拔道次必须按序号严格递增且不重复")
        if self.cumulative_reduction is not None:
            value = self.cumulative_reduction
            if not math.isfinite(value) or not 0.0 <= value < 1.0:
                raise ParamcadError(f"累计减径率必须在 [0, 1)：{value!r}")
        if self.eccentricity_carry not in (None, "absolute", "factor"):
            raise ParamcadError(f"未知偏心传递方式：{self.eccentricity_carry!r}")
        if self.finished_length is not None and self.finished_length.unit != LENGTH_UNIT:
            raise ParamcadError(f"成品长度单位固定为 {LENGTH_UNIT}")

    @property
    def derived_reduction(self) -> float | None:
        """累计减径率：显式值优先，否则由各道次推导；任一道次未标定为 ``None``。"""
        if self.cumulative_reduction is not None:
            return self.cumulative_reduction
        remaining = 1.0
        for item in self.passes:
            if item.diameter_reduction is None:
                return None
            remaining *= 1.0 - item.diameter_reduction
        return 1.0 - remaining


@dataclass(frozen=True, slots=True)
class CaseScope:
    """模型绑定到哪些工况编码；``include`` 缺省表示全部，``exclude`` 之后应用。"""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, values in (("include", self.include), ("exclude", self.exclude)):
            for value in values:
                if not value.strip():
                    raise ParamcadError(f"case_scope.{name} 不能包含空编码")

    @property
    def is_empty(self) -> bool:
        """两个列表都为空时不写 ``case_scope``。"""
        return not self.include and not self.exclude


@dataclass(frozen=True, slots=True)
class TubeModel:
    """一个完整模型：最终几何、工艺描述、分布场、缺陷与工况绑定。

    ``geometry`` 是导出到契约的最终几何；``before_state`` 只在模型由「前态 +
    工艺」推导得到时记录推导输入，便于追溯，不参与导出与指纹。
    """

    model_id: str
    geometry: GeometryState
    process: ProcessSpec | None = None
    before_state: GeometryState | None = None
    density_fields: tuple[Field, ...] = ()
    defects: DefectParams = field(default_factory=default_defects)
    case_scope: CaseScope | None = None
    confidence: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ParamcadError("模型 id 不能为空")
        fields_by_axis(self.density_fields)
        for domain, level in self.confidence.items():
            if domain not in CONFIDENCE_DOMAINS:
                raise ParamcadError(f"未知置信域：{domain!r}")
            if level not in CONFIDENCE_LEVELS:
                raise ParamcadError(f"置信等级必须是 {CONFIDENCE_LEVELS}，收到 {level!r}")

    @classmethod
    def from_before(  # noqa: PLR0913 - 构造入口显式暴露全部可选字段，便于调用点自解释
        cls,
        model_id: str,
        before: GeometryState,
        process: ProcessSpec | None = None,
        *,
        density_fields: tuple[Field, ...] = (),
        defects: DefectParams | None = None,
        case_scope: CaseScope | None = None,
        confidence: dict[str, str] | None = None,
        notes: str = "",
    ) -> TubeModel:
        """由「前态 + 工艺」推导最终几何并构造模型。"""
        return cls(
            model_id=model_id,
            geometry=derive_after(before, process),
            process=process,
            before_state=before,
            density_fields=density_fields,
            defects=defects if defects is not None else default_defects(),
            case_scope=case_scope,
            confidence=confidence if confidence is not None else {},
            notes=notes,
        )

    def effective_state(self) -> tuple[GeometryState, str]:
        """预览 / 校验使用的几何与展示标签。"""
        return self.geometry, ("after" if self.process is not None else "before")


def derive_after(before: GeometryState, process: ProcessSpec | None) -> GeometryState:
    """由前态与工艺推导拉拔后几何；任何未定输入都传播为 TBD。"""
    if process is None:
        return _unknown_state("未给出拉拔工艺，后态几何未定")
    reduction = process.derived_reduction
    outer_value = before.outer_diameter.value
    if outer_value is None:
        outer_after: Quantity = tbd(LENGTH_UNIT, "前态外径未标定，无法推导后态外径")
    elif reduction is None:
        outer_after = tbd(LENGTH_UNIT, "至少一个道次的减径率未标定，无法推导后态外径")
    else:
        outer_after = Quantity(outer_value * (1.0 - reduction), LENGTH_UNIT)
    wall_after = _derive_wall_after(before, process)
    inner_after = _derive_inner(outer_after, wall_after)
    if process.finished_length is not None:
        length_after = process.finished_length
    else:
        length_after = tbd(LENGTH_UNIT, "工艺未给出成品长度（不做体积守恒假设）")
    low_after, high_after, radial_after = _derive_wall_extremes(before, process, wall_after)

    return GeometryState(
        outer_diameter=outer_after,
        inner_diameter=inner_after,
        length=length_after,
        wall_nominal=wall_after,
        wall_min=low_after,
        wall_max=high_after,
        eccentricity_radial=radial_after,
        eccentricity_circumferential=tbd(LENGTH_UNIT, "未建模周向偏移分量"),
        eccentricity_angle_deg=tbd(ANGLE_UNIT, "未建模偏心方向"),
        wall_circumferential=CircumferentialWall(kind="tbd"),
    )


def _derive_wall_after(before: GeometryState, process: ProcessSpec) -> Quantity:
    """逐道次应用相对壁厚变化率；缺道次或未声明变化率即 TBD。"""
    if not process.passes:
        return tbd(LENGTH_UNIT, "工艺未给出道次壁厚变化率，后态壁厚未定")
    wall_before = before.wall_mean
    value = wall_before.value
    reason = wall_before.reason
    for item in process.passes:
        if value is None:
            break
        change = item.wall_thickness_change
        if change is None:
            value = None
            reason = f"第 {item.index} 道次未给出壁厚变化率"
            break
        value *= 1.0 + change
    return tbd(LENGTH_UNIT, reason) if value is None else Quantity(value, LENGTH_UNIT)


def _derive_inner(outer_after: Quantity, wall_after: Quantity) -> Quantity:
    if outer_after.value is None or wall_after.value is None:
        return tbd(LENGTH_UNIT, "外径或壁厚未定，无法推导后态内径")
    inner = outer_after.value - 2.0 * wall_after.value
    if inner <= 0:
        raise ParamcadError(f"推导得到的内径非正（{inner!r} mm），工艺参数不成立")
    return Quantity(inner, LENGTH_UNIT)


def _derive_wall_extremes(
    before: GeometryState,
    process: ProcessSpec,
    wall_after: Quantity,
) -> tuple[Quantity, Quantity, Quantity]:
    """按显式偏心传递方式推导后态壁厚极值与径向偏移。"""
    offset_before = before.offset
    if offset_before.value is None:
        reason = offset_before.reason
        return tbd(LENGTH_UNIT, reason), tbd(LENGTH_UNIT, reason), tbd(LENGTH_UNIT, reason)
    if process.eccentricity_carry is None:
        reason = "工艺未声明偏心传递方式（absolute / factor）"
        return tbd(LENGTH_UNIT, reason), tbd(LENGTH_UNIT, reason), tbd(LENGTH_UNIT, reason)
    if wall_after.value is None:
        reason = "后态壁厚未定，无法给出壁厚极值"
        return tbd(LENGTH_UNIT, reason), tbd(LENGTH_UNIT, reason), tbd(LENGTH_UNIT, reason)
    offset = offset_before.value
    if process.eccentricity_carry == "factor":
        wall_before = before.wall_mean
        if wall_before.value is None:
            reason = "前态壁厚未标定，无法按偏心因子传递"
            return tbd(LENGTH_UNIT, reason), tbd(LENGTH_UNIT, reason), tbd(LENGTH_UNIT, reason)
        offset = offset * (wall_after.value / wall_before.value)
    low = wall_after.value - offset
    high = wall_after.value + offset
    if low <= 0:
        raise ParamcadError(f"推导得到的壁厚极小值非正（{low!r} mm），工艺参数不成立")
    return Quantity(low, LENGTH_UNIT), Quantity(high, LENGTH_UNIT), Quantity(offset, LENGTH_UNIT)


def tbd_report(model: TubeModel) -> list[str]:
    """列出模型里全部未标定项（TBD 是合法状态，不是错误）。"""
    state, label = model.effective_state()
    rows = [f"{label} 几何 {name} 未定：{reason}" for name, reason in state.tbd_items()]
    for name, item in (
        ("interface.gap_mm", model.defects.gap_mm),
        ("interface.debond.extent_deg", model.defects.debond_extent_deg),
        ("density_field.end_loose.length_fraction", model.defects.end_loose_length_fraction),
        ("density_field.end_loose.density", model.defects.end_loose_density),
    ):
        if item.value is None:
            rows.append(f"{name} 未定：{item.reason}")
    if model.defects.debond_state == "tbd":
        rows.append("interface.debond.state 未定：脱粘状态未标定")
    circumferential = state.wall_circumferential
    if circumferential is not None and circumferential.kind == "polyline" and not circumferential.is_calibrated:
        rows.append("geometry.wall_thickness.circumferential 未定：折线样本含 TBD")
    for defect in model.defects.local_defects:
        if defect.size_mm is not None and defect.size_mm.value is None:
            rows.append(f"local_defects[{defect.defect_id}].size_mm 未定：{defect.size_mm.reason}")
        if defect.severity is not None and defect.severity.value is None:
            rows.append(f"local_defects[{defect.defect_id}].severity 未定：{defect.severity.reason}")
    return rows


def validate_model(model: TubeModel) -> list[str]:
    """跨字段一致性校验；返回问题列表（空列表表示通过）。

    单独的 TBD 是合法状态，不进入问题列表，见 :func:`tbd_report`。
    """
    problems: list[str] = []
    state, label = model.effective_state()
    outer = state.outer_diameter.value
    inner = state.inner_diameter.value
    gap = model.defects.gap_mm.value
    if outer is not None and inner is not None and gap is not None and gap >= inner / 2.0:
        problems.append(f"药管间隙 {gap} mm 不小于内腔半径 {inner / 2.0} mm（{label}）")
    return problems


def _format_state(state: GeometryState, title: str) -> list[str]:
    return [
        f"{title}：",
        f"  外径 / 内径：{format_quantity(state.outer_diameter)} / {format_quantity(state.inner_diameter)}",
        f"  长度：{format_quantity(state.length)}",
        f"  壁厚 nominal / min / max：{format_quantity(state.wall_nominal)} / "
        f"{format_quantity(state.wall_min)} / {format_quantity(state.wall_max)}",
        f"  壁厚周向分布：{_format_circumferential(state.wall_circumferential)}",
        f"  偏心 radial / circumferential / angle：{format_quantity(state.eccentricity_radial)} / "
        f"{format_quantity(state.eccentricity_circumferential)} / {format_quantity(state.eccentricity_angle_deg)}",
    ]


def _format_circumferential(item: CircumferentialWall | None) -> str:
    if item is None or item.kind == "tbd":
        return "TBD（未给出周向分布）"
    if item.kind == "uniform" and item.value is not None:
        return f"uniform {format_quantity(Quantity(item.value, LENGTH_UNIT))}"
    samples = "、".join(f"{sample.angle_deg:g}° {format_quantity(sample.thickness)}" for sample in item.samples)
    return f"polyline（{samples}）"


def format_summary(model: TubeModel) -> str:
    """输出模型摘要（几何、工艺、分布场、缺陷与 TBD 清单）。"""
    lines = [f"模型：{model.model_id}"]
    if model.case_scope is not None and not model.case_scope.is_empty:
        lines.append(
            f"工况绑定：include={list(model.case_scope.include) or '全部'} "
            f"exclude={list(model.case_scope.exclude) or '无'}"
        )
    else:
        lines.append("工况绑定：全部工况")
    if model.confidence:
        lines.append("置信等级：" + "、".join(f"{key}={model.confidence[key]}" for key in sorted(model.confidence)))
    if model.before_state is not None:
        lines.extend(_format_state(model.before_state, "几何（拉拔前·推导输入）"))
    if model.process is None:
        lines.append("拉拔工艺：未给出")
    else:
        reduction = model.process.derived_reduction
        lines.append(
            f"拉拔工艺：{len(model.process.passes)} 道次，"
            f"累计减径 {'TBD' if reduction is None else reduction}，"
            f"偏心传递 {model.process.eccentricity_carry or 'TBD'}"
        )
        for item in model.process.passes:
            lines.append(
                f"  道次 {item.index}：减径率 {'TBD' if item.diameter_reduction is None else item.diameter_reduction}"
                f"，壁厚变化率 {'TBD' if item.wall_thickness_change is None else item.wall_thickness_change}"
                f"，模具半角 {'TBD' if item.die_angle_deg is None else item.die_angle_deg}"
            )
    lines.extend(_format_state(model.geometry, "几何（文档 / 导出使用）"))
    lines.append("分布场：")
    if not model.density_fields:
        lines.append("  无")
    for item in model.density_fields:
        lines.append(f"  {item.axis}：{type(item.profile).__name__}（来源：{item.source}）")
    lines.append("缺陷：")
    lines.extend(format_defects(model.defects))
    problems = validate_model(model)
    summary = "通过" if not problems else f"发现 {len(problems)} 项问题"
    lines.append(f"一致性校验：{summary}")
    lines.extend(f"  - {item}" for item in problems)
    tbd_rows = tbd_report(model)
    lines.append(f"TBD：{len(tbd_rows)} 项")
    lines.extend(f"  - {item}" for item in tbd_rows)
    if model.notes:
        lines.append(f"备注：{model.notes}")
    return "\n".join(lines)
