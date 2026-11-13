"""线框与视图变换测试。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._mesh import Mesh
from fcmd.apps.paramesh._preview import fit_mapper, pixel_segments, wireframe, zoom_at

MeshBuilder = Callable[..., Mesh]


def test_wireframe_dedups_shared_edges(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    frame = wireframe(mesh)
    edges: set[tuple[int, int]] = set()
    for element in mesh.elements:
        ids = element.nodes
        for index in range(4):
            first, second = ids[index], ids[(index + 1) % 4]
            edges.add((first, second) if first < second else (second, first))
    assert len(frame.segments) == len(edges)
    assert len(frame.segments) < 4 * mesh.element_count
    assert {segment.region for segment in frame.segments} == {"propellant", "gap", "wall"}


def test_wireframe_bounding_box(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    frame = wireframe(mesh)
    assert frame.min_r == pytest.approx(0.0)
    assert frame.max_r == pytest.approx(20.0)
    assert frame.min_z == pytest.approx(0.0)
    assert frame.max_z == pytest.approx(20.0)


def test_fit_mapper_centers_geometry(mesh_builder: MeshBuilder) -> None:
    frame = wireframe(mesh_builder())
    mapper = fit_mapper(frame, 600.0, 400.0)
    center_x, center_y = mapper.to_pixel((frame.min_r + frame.max_r) / 2.0, (frame.min_z + frame.max_z) / 2.0)
    assert center_x == pytest.approx(300.0)
    assert center_y == pytest.approx(200.0)
    assert mapper.zoom == 1.0


def test_fit_mapper_with_focus_and_padding(mesh_builder: MeshBuilder) -> None:
    frame = wireframe(mesh_builder())
    mapper = fit_mapper(frame, 600.0, 400.0, zoom=2.0, focus=(5.0, 10.0), padding=30.0)
    assert mapper.zoom == 2.0
    assert mapper.to_pixel(5.0, 10.0) == (300.0, 200.0)
    assert mapper.scale == pytest.approx(min((600.0 - 60.0) / 20.0, (400.0 - 60.0) / 20.0) * 2.0)


def test_zoom_at_keeps_cursor_point(mesh_builder: MeshBuilder) -> None:
    mapper = fit_mapper(wireframe(mesh_builder()), 600.0, 400.0)
    focus = mapper.to_model(150.0, 100.0)
    zoomed = zoom_at(mapper, 2.0, 150.0, 100.0)
    assert zoomed.to_pixel(*focus) == pytest.approx((150.0, 100.0))
    assert zoomed.zoom == pytest.approx(2.0)


def test_zoom_limits(mesh_builder: MeshBuilder) -> None:
    mapper = fit_mapper(wireframe(mesh_builder()), 600.0, 400.0)
    assert zoom_at(mapper, 1000.0, 10.0, 10.0).zoom == pytest.approx(80.0)
    assert zoom_at(mapper, 1e-6, 10.0, 10.0).zoom == pytest.approx(0.1)


def test_invalid_view_parameters(mesh_builder: MeshBuilder) -> None:
    frame = wireframe(mesh_builder())
    with pytest.raises(ParameshError, match="画布尺寸"):
        fit_mapper(frame, 0.0, 100.0)
    with pytest.raises(ParameshError, match="缩放倍数"):
        fit_mapper(frame, 100.0, 100.0, zoom=0.0)
    mapper = fit_mapper(frame, 100.0, 100.0)
    with pytest.raises(ParameshError, match="缩放因子"):
        zoom_at(mapper, -1.0, 0.0, 0.0)


def test_pixel_segments_match_wireframe(mesh_builder: MeshBuilder) -> None:
    frame = wireframe(mesh_builder())
    mapper = fit_mapper(frame, 600.0, 400.0)
    pixels = pixel_segments(frame, mapper)
    assert len(pixels) == len(frame.segments)
    assert all(item[4] in {"propellant", "gap", "wall"} for item in pixels)
    xs = [value for x1, _y1, x2, _y2, _region in pixels for value in (x1, x2)]
    ys = [value for _x1, y1, _x2, y2, _region in pixels for value in (y1, y2)]
    assert min(xs) >= 0.0
    assert max(xs) <= 600.0
    assert min(ys) >= 0.0
    assert max(ys) <= 400.0
