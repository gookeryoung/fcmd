"""网格线框与视图变换（纯逻辑，供 Tk 预览与单测复用）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._mesh import Mesh

__all__ = [
    "Segment",
    "ViewMapper",
    "Wireframe",
    "fit_mapper",
    "pixel_segments",
    "wireframe",
    "zoom_at",
]


@dataclass(frozen=True, slots=True)
class Segment:
    """一条网格边（模型坐标，mm）。"""

    r1: float
    z1: float
    r2: float
    z2: float
    region: str


@dataclass(frozen=True, slots=True)
class Wireframe:
    """去重后的线框与包围盒。"""

    segments: tuple[Segment, ...]
    min_r: float
    max_r: float
    min_z: float
    max_z: float


@dataclass(frozen=True, slots=True)
class ViewMapper:
    """模型坐标（r, z）到画布像素的仿射映射。"""

    width: float
    height: float
    min_r: float
    max_r: float
    min_z: float
    max_z: float
    padding: float
    zoom: float
    focus_r: float
    focus_z: float

    @property
    def scale(self) -> float:
        span_r = max(self.max_r - self.min_r, 1e-9)
        span_z = max(self.max_z - self.min_z, 1e-9)
        usable_w = max(self.width - 2.0 * self.padding, 1.0)
        usable_h = max(self.height - 2.0 * self.padding, 1.0)
        return min(usable_w / span_r, usable_h / span_z) * self.zoom

    def to_pixel(self, r: float, z: float) -> tuple[float, float]:
        scale = self.scale
        return (
            self.width / 2.0 + (r - self.focus_r) * scale,
            self.height / 2.0 - (z - self.focus_z) * scale,
        )

    def to_model(self, x: float, y: float) -> tuple[float, float]:
        scale = self.scale
        return (
            self.focus_r + (x - self.width / 2.0) / scale,
            self.focus_z - (y - self.height / 2.0) / scale,
        )


def wireframe(mesh: Mesh) -> Wireframe:
    """提取去重线框；共享边只画一次。"""
    coordinates = {node.node_id: (node.r, node.z) for node in mesh.nodes}
    edges: dict[tuple[int, int], str] = {}
    for element in mesh.elements:
        ids = element.nodes
        for index in range(4):
            first = ids[index]
            second = ids[(index + 1) % 4]
            key = (first, second) if first < second else (second, first)
            edges.setdefault(key, element.region)
    segments: list[Segment] = []
    for first, second in sorted(edges):
        r1, z1 = coordinates[first]
        r2, z2 = coordinates[second]
        segments.append(Segment(r1=r1, z1=z1, r2=r2, z2=z2, region=edges[(first, second)]))
    if not segments:  # pragma: no cover - 网格构建保证非空
        raise ParameshError("网格为空，无法生成线框")
    return Wireframe(
        segments=tuple(segments),
        min_r=min(node.r for node in mesh.nodes),
        max_r=max(node.r for node in mesh.nodes),
        min_z=min(node.z for node in mesh.nodes),
        max_z=max(node.z for node in mesh.nodes),
    )


def fit_mapper(  # noqa: PLR0913 - 视图参数显式罗列
    frame: Wireframe,
    width: float,
    height: float,
    *,
    zoom: float = 1.0,
    focus: tuple[float, float] | None = None,
    padding: float = 16.0,
) -> ViewMapper:
    """构造适应窗口的映射；``focus`` 省略时取包围盒中心。"""
    if width <= 0.0 or height <= 0.0:
        raise ParameshError("画布尺寸必须为正")
    if zoom <= 0.0 or not math.isfinite(zoom):
        raise ParameshError("缩放倍数必须为正的有限数")
    center_r = (frame.min_r + frame.max_r) / 2.0
    center_z = (frame.min_z + frame.max_z) / 2.0
    if focus is not None:
        center_r, center_z = focus
    return ViewMapper(
        width=width,
        height=height,
        min_r=frame.min_r,
        max_r=frame.max_r,
        min_z=frame.min_z,
        max_z=frame.max_z,
        padding=padding,
        zoom=zoom,
        focus_r=center_r,
        focus_z=center_z,
    )


def zoom_at(mapper: ViewMapper, factor: float, x: float, y: float) -> ViewMapper:
    """以画布点 ``(x, y)`` 为定点缩放（该点在屏幕上的位置保持不变），限制在 0.1–80 倍。"""
    if factor <= 0.0 or not math.isfinite(factor):
        raise ParameshError("缩放因子必须为正的有限数")
    point_r, point_z = mapper.to_model(x, y)
    zoom = min(max(mapper.zoom * factor, 0.1), 80.0)
    scaled = replace(mapper, zoom=zoom)
    scale = scaled.scale
    return replace(
        scaled,
        focus_r=point_r - (x - mapper.width / 2.0) / scale,
        focus_z=point_z + (y - mapper.height / 2.0) / scale,
    )


def pixel_segments(frame: Wireframe, mapper: ViewMapper) -> list[tuple[float, float, float, float, str]]:
    """把线框映射为像素线段，供 Canvas 直接绘制。"""
    result: list[tuple[float, float, float, float, str]] = []
    for segment in frame.segments:
        x1, y1 = mapper.to_pixel(segment.r1, segment.z1)
        x2, y2 = mapper.to_pixel(segment.r2, segment.z2)
        result.append((x1, y1, x2, y2, segment.region))
    return result
