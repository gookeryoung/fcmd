"""质量报告、LS-DYNA 片段与网格包测试。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._kfile import (
    NODE_SET_ORDER,
    PART_SET_ORDER,
    node_set_id,
    part_set_id,
    render_keyword,
)
from fcmd.apps.paramesh._mesh import (
    SET_AXIS,
    SET_END_START,
    SET_OUTER,
    SET_PART_ALL,
    SET_PART_WALL,
    SET_REGION_PROP,
    SET_WALL_INNER,
    Mesh,
)
from fcmd.apps.paramesh._package import (
    MESH_SCHEMA,
    build_manifest,
    geometry_key,
    load_manifest,
    load_quality,
    mesh_digest,
    mesh_id,
    read_package,
    write_package,
)
from fcmd.apps.paramesh._quality import format_quality, quality_report

MeshBuilder = Callable[..., Mesh]


def test_quality_report_covers_all_metrics(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    report = quality_report(mesh)
    assert report.element_count == mesh.element_count
    assert report.node_count == mesh.node_count
    assert sum(item.count for item in report.aspect_bins) == report.element_count
    assert report.aspect_min > 0
    assert report.aspect_max >= report.aspect_min
    assert report.size_min_mm > 0
    assert report.size_max_mm >= report.size_min_mm
    assert report.negative_volume == 0
    assert report.degenerate == 0
    assert report.wall_layers_actual == mesh.plan.wall_layers
    assert [item.name for item in report.regions] == ["propellant", "gap", "wall"]
    assert all(abs(item.rel_error) < 1e-9 for item in report.regions)


def test_quality_to_dict_round_trips(mesh_builder: MeshBuilder) -> None:
    report = quality_report(mesh_builder())
    payload = report.to_dict()
    assert payload["element_count"] == report.element_count
    assert isinstance(payload["aspect_ratio"], dict)
    layers = payload["wall_layers"]
    assert isinstance(layers, dict)
    assert layers["target_min"] == report.wall_layers_target_min
    json.dumps(payload)


def test_format_quality_text(mesh_builder: MeshBuilder) -> None:
    text = format_quality(quality_report(mesh_builder()))
    assert "单元 / 节点：" in text
    assert "坐标约定：x=轴向" in text
    assert "长宽比分布：" in text
    assert "壁厚层数：实际" in text
    assert "区域面积对照：" in text


def test_keyword_fragment_structure(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    text = render_keyword(mesh, "demo-coarse-00000000")
    assert text.startswith("$ paramesh 网格片段")
    assert "*NODE" in text
    assert "*ELEMENT_SHELL" in text
    assert not any(line.startswith("*PART") for line in text.splitlines())
    assert not any(line.startswith("*SECTION_SHELL") for line in text.splitlines())
    assert "*SET_NODE_LIST_TITLE" in text
    assert "*SET_PART_LIST_TITLE" in text
    assert not any(line.startswith("*KEYWORD") for line in text.splitlines())
    assert not any(line.startswith("*END") for line in text.splitlines())
    assert "*MAT_" not in text
    assert "*BOUNDARY_NON_REFLECTING" not in text
    assert text.endswith("\n")
    assert render_keyword(mesh, "demo-coarse-00000000") == text


def test_keyword_node_and_element_counts(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    lines = render_keyword(mesh, "demo").splitlines()
    node_start = lines.index("*NODE") + 1
    element_start = lines.index("*ELEMENT_SHELL") + 1
    next_keyword = next(index for index in range(element_start, len(lines)) if lines[index].startswith("*"))
    node_lines = lines[node_start : element_start - 1]
    element_lines = lines[element_start:next_keyword]
    assert len(node_lines) == mesh.node_count
    assert len(element_lines) == mesh.element_count
    first = node_lines[0].split(", ")
    assert len(first) == 4
    assert [float(value) for value in first] == [mesh.nodes[0].node_id, mesh.nodes[0].z, mesh.nodes[0].r, 0.0]


def test_keyword_set_cards_use_sid_then_members(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    lines = render_keyword(mesh, "demo").splitlines()
    index = 0
    seen_nodes: set[str] = set()
    seen_parts: set[str] = set()
    while index < len(lines):
        line = lines[index]
        if line == "*SET_NODE_LIST_TITLE":
            name = lines[index + 1]
            header = lines[index + 2]
            sid = int(header.split(", ")[0])
            assert header == f"{sid}, 0, 0, 0, 0"
            assert sid == node_set_id(name)
            index += 3
            members: list[int] = []
            while index < len(lines) and not lines[index].startswith("*"):
                member_line = lines[index].split(", ")
                assert len(member_line) <= 8
                members.extend(int(value) for value in member_line)
                index += 1
            assert len(members) == len(mesh.node_sets[name])
            seen_nodes.add(name)
            continue
        if line == "*SET_PART_LIST_TITLE":
            name = lines[index + 1]
            header = lines[index + 2]
            sid = int(header.split(", ")[0])
            assert header == f"{sid}, 0, 0, 0, 0"
            assert sid == part_set_id(name)
            index += 3
            members = []
            while index < len(lines) and not lines[index].startswith("*"):
                members.extend(int(value) for value in lines[index].split(", "))
                index += 1
            assert members == list(next(item.part_ids for item in mesh.part_sets if item.name == name))
            seen_parts.add(name)
            continue
        index += 1
    assert seen_nodes == set(mesh.node_sets)
    assert seen_parts == set(PART_SET_ORDER)


def test_keyword_sets_use_stable_names(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    text = render_keyword(mesh, "demo")
    for name in NODE_SET_ORDER:
        if name in mesh.node_sets:
            assert name in text
    for name in PART_SET_ORDER:
        assert name in text


def test_manifest_fields(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    report = quality_report(mesh)
    manifest = build_manifest(mesh, report)
    assert manifest["schema"] == MESH_SCHEMA
    assert manifest["digest"] == mesh_digest(mesh)
    assert manifest["geometry_key"] == geometry_key(mesh)
    assert manifest["dimension"] == "axisymmetric-2d"
    assert manifest["level"] == "coarse"
    assert manifest["model"]["digest"] == mesh.model.digest
    assert manifest["counts"] == {
        "nodes": mesh.node_count,
        "elements": mesh.element_count,
        "shells": mesh.element_count,
    }
    assert [item["name"] for item in manifest["regions"]] == ["propellant", "gap", "wall"]
    assert manifest["mesh_id"] == mesh_id(mesh)
    assert manifest["size_range_mm"]["min"] == pytest.approx(report.size_min_mm)
    assert manifest["coordinate_system"] == {
        "symmetry_axis": "x",
        "x": "axial",
        "y": "radial",
        "z": "out_of_plane",
    }
    assert "tbd" in manifest
    assert "warnings" in manifest


def test_manifest_parts_roles_and_sections(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    manifest = build_manifest(mesh, quality_report(mesh))
    parts = {item["name"]: item for item in manifest["parts"]}
    assert parts["propellant"]["role"] == "explosive"
    assert parts["gap"]["role"] == "air"
    assert parts["wall"]["role"] == "wall"
    for name, region in (("propellant", 1), ("gap", 2), ("wall", 3)):
        section = parts[name]["section"]
        assert section["secid"] == region
        assert section["elform"] == 14
    assert parts["propellant"]["section"]["thickness_mm"] == pytest.approx(mesh.plan.propellant_radius_mm)
    assert parts["gap"]["section"]["thickness_mm"] == pytest.approx(mesh.plan.gap_mm)
    assert parts["wall"]["section"]["thickness_mm"] == pytest.approx(mesh.plan.section_wall_mm)


def test_manifest_sets_have_numeric_sids(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    manifest = build_manifest(mesh, quality_report(mesh))
    nodes = manifest["sets"]["nodes"]
    parts = manifest["sets"]["parts"]
    assert nodes[SET_AXIS]["id"] == node_set_id(SET_AXIS) == 1
    assert nodes[SET_OUTER]["id"] == node_set_id(SET_OUTER) == 2
    assert nodes[SET_END_START]["id"] == node_set_id(SET_END_START) == 3
    assert nodes[SET_WALL_INNER]["id"] == node_set_id(SET_WALL_INNER) == 9
    assert nodes[SET_REGION_PROP]["count"] == len(mesh.node_sets[SET_REGION_PROP])
    assert parts[SET_PART_ALL]["id"] == part_set_id(SET_PART_ALL) == 101
    assert parts[SET_PART_WALL]["id"] == part_set_id(SET_PART_WALL) == 103
    assert parts[SET_PART_ALL]["part_ids"] == [1, 2, 3]


def test_manifest_extent_detonation_and_probes(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    manifest = build_manifest(mesh, quality_report(mesh))
    assert manifest["extent"] == {"z_start_mm": 0.0, "z_end_mm": 20.0, "r_max_mm": 20.0}
    assert manifest["detonation"] == {"x": 0.0, "y": 0.0, "z": 0.0, "t0": 0.0}
    nodes = {node.node_id: node for node in mesh.nodes}
    probes = {item["name"]: item for item in manifest["probes"]}
    assert set(probes) == {
        "axis_start",
        "axis_end",
        "wall_inner_start",
        "wall_inner_end",
        "wall_outer_start",
        "wall_outer_end",
        "propellant_outer_start",
        "propellant_outer_end",
    }
    for probe in probes.values():
        node = nodes[probe["node_id"]]
        assert probe["r_mm"] == pytest.approx(node.r)
        assert probe["z_mm"] == pytest.approx(node.z)


def test_probes_keep_same_physical_positions_across_levels(mesh_builder: MeshBuilder) -> None:
    coordinates: list[dict[str, tuple[float, float]]] = []
    for level in ("coarse", "medium", "fine"):
        mesh = mesh_builder(level)
        manifest = build_manifest(mesh, quality_report(mesh))
        coordinates.append({item["name"]: (item["r_mm"], item["z_mm"]) for item in manifest["probes"]})
    assert coordinates[0] == coordinates[1] == coordinates[2]


def test_manifest_records_end_loose_and_circumferential(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder(
        model={
            "density_field": {"end_loose": {"length_fraction": 0.25, "density": 1.45}},
            "geometry": {
                "wall_thickness": {
                    "circumferential": {
                        "kind": "polyline",
                        "samples": [
                            {"angle_deg": 0, "thickness": 9.8},
                            {"angle_deg": 180, "thickness": 10.2},
                        ],
                    }
                }
            },
        }
    )
    manifest = build_manifest(mesh, quality_report(mesh))
    assert manifest["model"]["end_loose"] == {
        "declared": True,
        "length_fraction": 0.25,
        "density": 1.45,
        "refined_band_mm": pytest.approx(5.0),
    }
    assert manifest["model"]["circumferential_wall"] == {"kind": "polyline", "declared": True}


def test_mesh_id_is_safe_and_deterministic(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder()
    ident = mesh_id(mesh)
    assert ident.startswith("m1-coarse-")
    assert len(ident.split("-")[-1]) == 8
    assert ident == mesh_id(mesh, mesh_digest(mesh))


def test_mesh_id_sanitizes_model_name(mesh_builder: MeshBuilder) -> None:
    mesh = mesh_builder(model={"id": " Case 01/A "})
    assert mesh_id(mesh).startswith("Case_01_A-coarse-")


def test_write_package_layout_and_round_trip(mesh_builder: MeshBuilder, tmp_path: Path) -> None:
    mesh = mesh_builder()
    report = quality_report(mesh)
    target = write_package(mesh, report, tmp_path)
    assert target.parent == tmp_path
    assert {item.name for item in target.iterdir()} == {
        "manifest.json",
        "mesh.k",
        "quality.json",
        "quality.txt",
    }
    manifest, quality = read_package(target)
    assert manifest["mesh_id"] == target.name
    assert quality["element_count"] == mesh.element_count
    assert (target / "mesh.k").read_text(encoding="utf-8") == render_keyword(mesh, target.name)


def test_write_package_is_byte_stable(mesh_builder: MeshBuilder, tmp_path: Path) -> None:
    mesh = mesh_builder()
    report = quality_report(mesh)
    first = write_package(mesh, report, tmp_path / "a")
    second = write_package(mesh, report, tmp_path / "b")
    assert (first / "manifest.json").read_text(encoding="utf-8") == (second / "manifest.json").read_text(
        encoding="utf-8"
    )
    assert (first / "mesh.k").read_text(encoding="utf-8") == (second / "mesh.k").read_text(encoding="utf-8")


def test_write_package_refuses_overwrite(mesh_builder: MeshBuilder, tmp_path: Path) -> None:
    mesh = mesh_builder()
    report = quality_report(mesh)
    target = write_package(mesh, report, tmp_path)
    with pytest.raises(ParameshError, match="已存在"):
        write_package(mesh, report, tmp_path)
    assert target.is_dir()
    again = write_package(mesh, report, tmp_path, overwrite=True)
    assert again == target


def test_load_manifest_accepts_file_or_directory(mesh_builder: MeshBuilder, tmp_path: Path) -> None:
    mesh = mesh_builder()
    target = write_package(mesh, quality_report(mesh), tmp_path)
    assert load_manifest(target) == load_manifest(target / "manifest.json")
    assert load_quality(target) == load_quality(target / "quality.json")


def test_load_manifest_rejects_wrong_schema(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema": "other/1"}', encoding="utf-8")
    with pytest.raises(ParameshError, match="schema"):
        load_manifest(path)


def test_load_manifest_rejects_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ParameshError, match="JSON"):
        load_manifest(path)
