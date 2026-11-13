"""预览几何：二维截面、轴向剖面与分布曲线（纯逻辑，不依赖 Tk）。

截面由两个偏心圆确定：外圆半径 ``R``，内圆半径 ``r_i = R - 名义壁厚``，内圆圆心
沿 ``-x`` 方向偏移 ``c = (max - min) / 2``。由此沿任意径向的角度 ``θ`` 的壁厚为
``R - s(θ)``，且 ``θ=0`` 取到最大壁厚、``θ=π`` 取到最小壁厚，与几何极值一致。

未标定的量不做静默假设：偏心未标定按同心绘制并给出提示，间隙未标定则不画药层。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fcmd.apps.paramcad._fields import DENSITY_AXES, Field
from fcmd.apps.paramcad._model import TubeModel

__all__ = [
    "Annotation",
    "AxialSection",
    "CrossSection",
    "Curve",
    "Point",
    "axial_section",
    "distribution_curves",
    "section_at",
]

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class Annotation:
    """截面上的周向缺陷标注（角度区间 + 文案）。"""

    kind: str
    start_deg: float
    end_deg: float
    label: str


@dataclass(frozen=True, slots=True)
class CrossSection:
    """二维截面几何；``available`` 为假时只有 ``note`` 有意义。"""

    available: bool
    state_label: str
    note: str
    half_extent_mm: float
    outer: tuple[Point, ...] = ()
    inner: tuple[Point, ...] = ()
    propellant: tuple[Point, ...] | None = None
    thickness: tuple[tuple[float, float], ...] = ()
    annotations: tuple[Annotation, ...] = ()


@dataclass(frozen=True, slots=True)
class AxialAnnotation:
    """轴向剖面上的标注（位置为 0–1 相对轴向 + 文案）。"""

    kind: str
    position: float
    label: str


@dataclass(frozen=True, slots=True)
class AxialSection:
    """轴向剖面几何：外壁 / 内壁 / 药层边界沿轴向的折线。"""

    available: bool
    tab_label: str
    note: str
    length_mm: float
    half_extent_mm: float
    outer_top: tuple[Point, ...] = ()
    outer_bottom: tuple[Point, ...] = ()
    inner_top: tuple[Point, ...] = ()
    inner_bottom: tuple[Point, ...] = ()
    propellant_top: tuple[Point, ...] | None = None
    propellant_bottom: tuple[Point, ...] | None = None
    annotations: tuple[AxialAnnotation, ...] = ()


@dataclass(frozen=True, slots=True)
class Curve:
    """一条分布曲线：横轴已换算成物理量或相对位置。"""

    title: str
    role: str
    x_label: str
    y_label: str
    points: tuple[Point, ...]
    source: str
    note: str = ""


def _circle(radius: float, center_x: float, samples: int) -> tuple[Point, ...]:
    if samples < 12:
        raise ValueError("截面采样数至少 12")
    return tuple(
        (
            center_x + radius * math.cos(math.radians(360.0 * i / samples)),
            radius * math.sin(math.radians(360.0 * i / samples)),
        )
        for i in range(samples)
    )


def _wall_at(angle_deg: float, outer_radius: float, inner_radius: float, offset: float) -> float:
    """沿 ``angle_deg`` 方向的径向壁厚（两偏心圆的精确交点）。"""
    theta = math.radians(angle_deg)
    discriminant = inner_radius * inner_radius - offset * offset * math.sin(theta) ** 2
    if discriminant < 0:
        return 0.0
    inner_surface = -offset * math.cos(theta) + math.sqrt(discriminant)
    return outer_radius - inner_surface


def _section_radii(model: TubeModel) -> tuple[float, float, float, str, list[str]] | None:
    """返回 ``(R, r_i, c, 状态标签, 提示)``；几何不足时返回 ``None``。"""
    state, label = model.effective_state()
    outer = state.outer_diameter.value
    inner = state.inner_diameter.value
    wall = state.wall_mean
    if outer is None or inner is None or wall.value is None:
        return None
    radius = outer / 2.0
    inner_radius = radius - wall.value
    if inner_radius <= 0:
        return None
    offset = state.offset
    notes: list[str] = []
    if offset.value is None:
        notes.append("偏心量未标定，按同心预览")
        offset_value = 0.0
    else:
        offset_value = offset.value
    return radius, inner_radius, offset_value, label, notes


def section_at(model: TubeModel, *, samples: int = 180) -> CrossSection:
    """生成二维截面；几何不足时返回 ``available=False`` 与原因。"""
    radii = _section_radii(model)
    if radii is None:
        return CrossSection(
            available=False,
            state_label="unknown",
            note="外径 / 内径 / 壁厚未标定，无法绘制截面",
            half_extent_mm=0.0,
        )
    radius, inner_radius, offset, label, notes = radii
    gap = model.defects.gap_mm.value
    propellant: tuple[Point, ...] | None = None
    if gap is None:
        notes.append("药管间隙未标定，不绘制药层")
    else:
        propellant_radius = inner_radius - gap
        if propellant_radius <= 0:
            notes.append("药管间隙不小于内腔半径，不绘制药层")
        else:
            propellant = _circle(propellant_radius, -offset, samples)

    thickness = tuple((degree, _wall_at(degree, radius, inner_radius, offset)) for degree in range(0, 361, 5))
    annotations = _section_annotations(model)
    return CrossSection(
        available=True,
        state_label=label,
        note="；".join(notes),
        half_extent_mm=radius,
        outer=_circle(radius, 0.0, samples),
        inner=_circle(inner_radius, -offset, samples),
        propellant=propellant,
        thickness=thickness,
        annotations=annotations,
    )


def _section_annotations(model: TubeModel) -> tuple[Annotation, ...]:
    extent = model.defects.debond_extent_deg.value
    annotations: list[Annotation] = []
    for defect in model.defects.local_defects:
        if defect.kind not in ("debond", "thin_layer") or defect.angle_deg is None:
            continue
        span = extent if defect.kind == "debond" and extent is not None else 0.0
        start = defect.angle_deg - span / 2.0
        labels = {"debond": "局部脱粘", "thin_layer": "局部薄药层"}
        annotations.append(
            Annotation(
                kind=defect.kind,
                start_deg=start % 360.0,
                end_deg=(start + span) % 360.0,
                label=labels[defect.kind],
            )
        )
    return tuple(annotations)


def axial_section(model: TubeModel) -> AxialSection:
    """生成轴向剖面；长度未标定时返回 ``available=False``。"""
    radii = _section_radii(model)
    state, label = model.effective_state()
    length = state.length.value
    if radii is None:
        return AxialSection(
            available=False,
            tab_label=label,
            note="外径 / 内径 / 壁厚未标定，无法绘制轴向剖面",
            length_mm=0.0,
            half_extent_mm=0.0,
        )
    radius, inner_radius, offset, _, notes = radii
    if length is None:
        return AxialSection(
            available=False,
            tab_label=label,
            note="长度未标定（工艺未给出成品长度），无法绘制轴向剖面",
            length_mm=0.0,
            half_extent_mm=radius,
        )
    gap = model.defects.gap_mm.value
    propellant_radius: float | None = None
    if gap is None:
        notes.append("药管间隙未标定，不绘制药层边界")
    elif inner_radius - gap > 0:
        propellant_radius = inner_radius - gap
    else:
        notes.append("药管间隙不小于内腔半径，不绘制药层边界")

    def line(value: float) -> tuple[Point, ...]:
        return ((0.0, value), (length, value))

    annotations: list[AxialAnnotation] = []
    for defect in model.defects.local_defects:
        if defect.axial is None:
            continue
        annotations.append(
            AxialAnnotation(kind=defect.kind, position=defect.axial, label=f"{defect.defect_id} · {defect.kind}")
        )
    anchor = model.defects.end_loose_density
    fraction = model.defects.end_loose_length_fraction.value
    if anchor.value is not None or fraction is not None:
        parts: list[str] = []
        if fraction is not None:
            parts.append(f"段长 {fraction:g}·L")
        if anchor.value is not None:
            parts.append("密度已给出")
        annotations.append(
            AxialAnnotation(
                kind="end_loose",
                position=1.0 if fraction is None else 1.0 - fraction,
                label=f"端部疏松（{'，'.join(parts)}）",
            )
        )

    return AxialSection(
        available=True,
        tab_label=label,
        note="；".join(notes),
        length_mm=length,
        half_extent_mm=radius + max(offset, 0.0),
        outer_top=line(radius),
        outer_bottom=line(-radius),
        inner_top=line(inner_radius),
        inner_bottom=line(-inner_radius),
        propellant_top=None if propellant_radius is None else line(propellant_radius),
        propellant_bottom=None if propellant_radius is None else line(-propellant_radius),
        annotations=tuple(annotations),
    )


def _x_axis(model: TubeModel, field: Field) -> tuple[str, str, float, float]:
    """返回 ``(x 标签, 说明, 起点, 终点)``。"""
    if field.axis == "axial":
        state, _ = model.effective_state()
        length = state.length.value
        if length is None:
            return "轴向相对位置", "长度未标定，横轴用 0–1 相对位置", 0.0, 1.0
        return "z（mm）", "", 0.0, length
    if field.axis == "radial":
        return "径向相对位置（0=内表面）", "", 0.0, 1.0
    return "周向角（deg）", "", 0.0, 360.0


def _sample_curve(model: TubeModel, field: Field, samples: int) -> Curve:
    x_label, note, start, end = _x_axis(model, field)
    if not field.is_calibrated:
        return Curve(
            title=f"{field.axis} 密度",
            role=field.axis,
            x_label=x_label,
            y_label="g/cm^3",
            points=(),
            source=field.source,
            note="样本含未标定值（TBD），预览标注为未标定",
        )
    points = tuple(
        (
            start + (end - start) * i / (samples - 1),
            field.sample(i / (samples - 1)),
        )
        for i in range(samples)
    )
    calibrated = tuple((x, float(y)) for x, y in points if y is not None)
    return Curve(
        title=f"{field.axis} 密度",
        role=field.axis,
        x_label=x_label,
        y_label="g/cm^3",
        points=calibrated,
        source=field.source,
        note=field.note or note,
    )


def _circumferential_wall_curve(model: TubeModel) -> Curve | None:
    """显式给出的周向壁厚分布优先于由偏心双圆推导的曲线。"""
    state, _ = model.effective_state()
    item = state.wall_circumferential
    if item is None or item.kind == "tbd":
        return None
    if item.kind == "uniform" and item.value is not None:
        return Curve(
            title="壁厚（周向）",
            role="wall_thickness",
            x_label="周向角（deg）",
            y_label="mm",
            points=((0.0, item.value), (360.0, item.value)),
            source="模型给出的周向壁厚分布（uniform）",
        )
    if not item.is_calibrated:
        return Curve(
            title="壁厚（周向）",
            role="wall_thickness",
            x_label="周向角（deg）",
            y_label="mm",
            points=(),
            source="模型给出的周向壁厚分布（polyline）",
            note="周向壁厚样本含未标定值（TBD）",
        )
    points: list[Point] = []
    for sample in item.samples:
        value = sample.thickness.value
        if value is None:
            continue
        points.append((sample.angle_deg, value))
    return Curve(
        title="壁厚（周向）",
        role="wall_thickness",
        x_label="周向角（deg）",
        y_label="mm",
        points=tuple(points),
        source="模型给出的周向壁厚分布（polyline）",
    )


def distribution_curves(model: TubeModel, *, samples: int = 51) -> tuple[Curve, ...]:
    """汇总分布曲线：显式密度场 + 周向壁厚分布 + 药层厚度。"""
    if samples < 2:
        raise ValueError("曲线采样数至少 2")
    curves = [_sample_curve(model, field, samples) for field in model.density_fields]

    radii = _section_radii(model)
    if radii is not None:
        radius, inner_radius, offset, _, _ = radii
        explicit_wall = _circumferential_wall_curve(model)
        if explicit_wall is not None:
            curves.append(explicit_wall)
        else:
            curves.append(
                Curve(
                    title="壁厚（周向）",
                    role="wall_thickness",
                    x_label="周向角（deg）",
                    y_label="mm",
                    points=tuple(
                        (degree, _wall_at(degree, radius, inner_radius, offset)) for degree in range(0, 361, 5)
                    ),
                    source="由几何与偏心推导",
                    note="由外径 / 内径 / 壁厚极值推导" if offset else "偏心未标定，按同心推导",
                )
            )
        gap = model.defects.gap_mm.value
        if gap is not None and inner_radius - gap > 0:
            curves.append(
                Curve(
                    title="药层厚度（周向）",
                    role="propellant_thickness",
                    x_label="周向角（deg）",
                    y_label="mm",
                    points=((0.0, inner_radius - gap), (360.0, inner_radius - gap)),
                    source="由内腔半径与药管间隙推导",
                    note="未建模周向变化，按常量显示",
                )
            )
    return tuple(curves)


def field_axes(fields: tuple[Field, ...]) -> tuple[str, ...]:
    """返回模型实际给出的密度轴（按契约固定顺序）。"""
    present = {item.axis for item in fields}
    return tuple(axis for axis in DENSITY_AXES if axis in present)
