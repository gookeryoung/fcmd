"""网格生成测试：确定性、共节点、边界集合、区域面积与三档一致性。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._mesh import (
    SET_AXIS,
    SET_END_END,
    SET_END_START,
    SET_GAP_WALL,
    SET_IF_PROP_WALL,
    SET_NRB,
    SET_OUTER,
    SET_PART_ALL,
    SET_PART_FLUID,
    SET_PART_WALL,
    SET_PROP_GAP,
    SET_REGION_GAP,
    SET_REGION_PROP,
    SET_REGION_WALL,
    SET_WALL_INNER,
    Mesh,
)
from fcmd.apps.paramesh._package import geometry_key, mesh_digest

MeshBuilder = Callable[..., Mesh]


def _coordinates(mesh: Mesh) -> dict[int, tuple[float, float]]:
    return {node.node_id: (node.r, node.z) for node in mesh.nodes}


def _ids(values: tuple[int, ...]) -> set[int]:
    return set(values)


def test_generation_is_deterministic(mesh_builder: MeshBuilder) -> None:
    first = mesh_builder()
    second = mesh_builder()
    assert mesh_digest(first) == mesh_digest(second)
    assert first.nodes == second.nodes
    assert first.elements == second.elements
    assert first.node_sets == second.node_sets


def test_region_order_parts_and_sets(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    assert [region.name for region in mesh.regions] == ["propellant", "gap", "wall"]
    assert [region.part_id for region in mesh.regions] == [1, 2, 3]
    assert mesh.node_sets[SET_REGION_PROP]
    assert mesh.node_sets[SET_REGION_GAP]
    assert mesh.node_sets[SET_REGION_WALL]
    parts = {item.name: item.part_ids for item in mesh.part_sets}
    assert parts[SET_PART_ALL] == (1, 2, 3)
    assert parts[SET_PART_FLUID] == (1, 2)
    assert parts[SET_PART_WALL] == (3,)


def test_interfaces_share_nodes(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    propellant = _ids(mesh.node_sets[SET_REGION_PROP])
    gap = _ids(mesh.node_sets[SET_REGION_GAP])
    wall = _ids(mesh.node_sets[SET_REGION_WALL])
    assert propellant & gap == _ids(mesh.node_sets[SET_PROP_GAP])
    assert gap & wall == _ids(mesh.node_sets[SET_GAP_WALL])


def test_boundary_sets(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    coordinates = _coordinates(mesh)
    assert all(coordinates[node][0] == 0.0 for node in mesh.node_sets[SET_AXIS])
    assert all(coordinates[node][0] == pytest.approx(20.0) for node in mesh.node_sets[SET_OUTER])
    assert all(coordinates[node][1] == 0.0 for node in mesh.node_sets[SET_END_START])
    assert all(coordinates[node][1] == pytest.approx(20.0) for node in mesh.node_sets[SET_END_END])
    assert mesh.node_sets[SET_NRB] == mesh.node_sets[SET_END_END]
    assert all(coordinates[node][0] == pytest.approx(10.0) for node in mesh.node_sets[SET_WALL_INNER])


def test_wall_layers_match_plan(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    coordinates = _coordinates(mesh)
    radii = {round(coordinates[node][0], 9) for node in mesh.node_sets[SET_REGION_WALL]}
    assert len(radii) == mesh.plan.wall_layers + 1
    assert 4 <= mesh.plan.wall_layers <= 8


def test_nodes_sorted_and_unique(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    ordered = [(node.z, node.r) for node in mesh.nodes]
    assert ordered == sorted(ordered)
    assert len(set(ordered)) == len(ordered)
    assert [node.node_id for node in mesh.nodes] == list(range(1, len(mesh.nodes) + 1))


def test_region_areas_match_analytic(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    for region in mesh.regions:
        assert region.area_mm2 > 0
        assert region.rel_error == pytest.approx(0.0, abs=1e-9)


def test_all_elements_positive_area(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    coordinates = _coordinates(mesh)
    for element in mesh.elements:
        corners = [coordinates[node_id] for node_id in element.nodes]
        total = 0.0
        for index in range(4):
            r0, z0 = corners[index]
            r1, z1 = corners[(index + 1) % 4]
            total += r0 * z1 - r1 * z0
        assert total / 2.0 > 1e-12


def test_levels_share_geometry_key_but_not_digest(mesh_builder: MeshBuilder) -> None:
    coarse = mesh_builder("coarse")
    medium = mesh_builder("medium")
    fine = mesh_builder("fine")
    assert geometry_key(coarse) == geometry_key(medium) == geometry_key(fine)
    assert len({mesh_digest(mesh) for mesh in (coarse, medium, fine)}) == 3
    assert coarse.element_count < medium.element_count < fine.element_count
    assert coarse.plan.global_h_mm > medium.plan.global_h_mm > fine.plan.global_h_mm


def test_end_loose_refinement_applied(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder(model={"density_field": {"end_loose": {"length_fraction": 0.25, "density": 1.45}}})
    zones = {item.zone: item for item in mesh.refinements}
    assert zones["end_loose"].applied is True
    assert zones["end_loose"].factor == pytest.approx(0.5)
    assert mesh.plan.end_loose_band_mm == pytest.approx(5.0)
    assert "MESH_IF_PROP_GAP" in mesh.node_sets


def test_end_loose_absent_is_marked_not_applied(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    zones = {item.zone: item for item in mesh.refinements}
    assert zones["end_loose"].applied is False
    assert zones["end_loose"].factor is None


def test_thin_layer_defect_refines(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder(
        model={"density_field": {"local_defects": [{"id": "D1", "kind": "thin_layer", "size_mm": 0.4}]}}
    )
    zones = {item.zone: item for item in mesh.refinements}
    assert zones["thin_layer"].applied is True
    assert zones["propellant_interface"].applied is True


def test_eccentric_section_recorded(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder(model={"geometry": {"eccentricity": {"radial_mm": 0.2}}})
    zones = {item.zone: item for item in mesh.refinements}
    assert zones["eccentric_near_wall"].applied is True
    assert "θ=" in zones["eccentric_near_wall"].note
    assert mesh.plan.section_source == "eccentricity"


def test_circumferential_polyline_drives_section(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder(
        model={
            "geometry": {
                "inner_diameter": 34.0,
                "wall_thickness": {
                    "nominal": 3.0,
                    "min": 2.6,
                    "max": 3.4,
                    "circumferential": {
                        "kind": "polyline",
                        "samples": [
                            {"angle_deg": 0, "thickness": 2.8},
                            {"angle_deg": 180, "thickness": 3.2},
                        ],
                    },
                },
            }
        }
    )
    assert mesh.plan.section_angle_deg == 0.0
    assert mesh.plan.section_wall_mm == pytest.approx(2.8)
    assert mesh.plan.section_source == "circumferential.polyline"
    assert mesh.plan.wall_layers == 4


def test_zero_gap_merges_interface(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder(model={"interface": None})
    assert [region.name for region in mesh.regions] == ["propellant", "wall"]
    assert SET_IF_PROP_WALL in mesh.node_sets
    assert SET_PROP_GAP not in mesh.node_sets
    assert any("无间隙" in warning for warning in mesh.warnings)
    assert "interface.gap_mm" in mesh.plan.tbd


def test_tbd_gap_keeps_mesh_generatable(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder(model={"interface": {"gap_mm": "TBD"}})
    assert [region.name for region in mesh.regions] == ["propellant", "wall"]
    assert mesh.element_count > 0


def test_length_zero_rejected(mesh_builder: MeshBuilder) -> None:
    with pytest.raises(ParameshError, match="length"):
        mesh_builder(model={"geometry": {"length": 0.0}})


def test_unknown_level_rejected(mesh_builder: MeshBuilder) -> None:
    with pytest.raises(ParameshError, match="未知网格档位"):
        mesh_builder("ultra")
