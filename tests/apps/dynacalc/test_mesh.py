"""dynacalc 网格包接入：paramesh-mesh/1 校验、模板族拆分与 deck 组装。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.dynacalc import dynacalc
from fcmd.apps.dynacalc._mesh import (
    LEVEL_ORDER,
    MeshError,
    compare_levels,
    format_level_report,
    load_package,
    scan_mesh_root,
)
from fcmd.apps.dynacalc._model import load_models
from fcmd.apps.dynacalc._params import load_params
from fcmd.apps.dynacalc._template import (
    AXISYMMETRIC_TEMPLATE,
    DEFAULT_TEMPLATE,
    DeckError,
    generate_decks,
    load_manifest,
    template_for,
    template_placeholders,
)

MODEL_ID = "M001-geometry"
GEOMETRY_KEY = "g" * 64
LEVEL_ERRORS = {
    "coarse": {"propellant": 0.020, "gap": 0.015, "wall": 0.030},
    "medium": {"propellant": 0.010, "gap": 0.008, "wall": 0.015},
    "fine": {"propellant": 0.005, "gap": 0.004, "wall": 0.007},
}


def _probes() -> list[dict[str, Any]]:
    return [
        {"name": "axis_start", "node_id": 1, "r_mm": 0.0, "z_mm": 0.0},
        {"name": "axis_station_025", "node_id": 2, "r_mm": 0.0, "z_mm": 75.0},
        {"name": "axis_station_050", "node_id": 3, "r_mm": 0.0, "z_mm": 150.0},
        {"name": "axis_station_075", "node_id": 4, "r_mm": 0.0, "z_mm": 225.0},
        {"name": "axis_end", "node_id": 5, "r_mm": 0.0, "z_mm": 300.0},
        {"name": "wall_inner_start", "node_id": 6, "r_mm": 15.0, "z_mm": 0.0},
        {"name": "wall_inner_end", "node_id": 7, "r_mm": 15.0, "z_mm": 300.0},
        {"name": "wall_outer_start", "node_id": 8, "r_mm": 20.0, "z_mm": 0.0},
        {"name": "wall_outer_end", "node_id": 9, "r_mm": 20.0, "z_mm": 300.0},
        {"name": "propellant_outer_start", "node_id": 10, "r_mm": 14.5, "z_mm": 0.0},
        {"name": "propellant_outer_end", "node_id": 11, "r_mm": 14.5, "z_mm": 300.0},
    ]


def _parts(*, thickness: float = 1.0, semantics: str = "unit_out_of_plane") -> list[dict[str, Any]]:
    return [
        {
            "part_id": 1,
            "name": "propellant",
            "role": "explosive",
            "element_type": "shell",
            "section": {
                "secid": 1,
                "elform": 14,
                "thickness_mm": thickness,
                "thickness_semantics": semantics,
                "region_thickness_mm": 10.0,
            },
        },
        {
            "part_id": 2,
            "name": "gap",
            "role": "air",
            "element_type": "shell",
            "section": {
                "secid": 2,
                "elform": 14,
                "thickness_mm": thickness,
                "thickness_semantics": semantics,
                "region_thickness_mm": 0.5,
            },
        },
        {
            "part_id": 3,
            "name": "wall",
            "role": "wall",
            "element_type": "shell",
            "section": {
                "secid": 3,
                "elform": 14,
                "thickness_mm": thickness,
                "thickness_semantics": semantics,
                "region_thickness_mm": 5.0,
            },
        },
    ]


def _manifest(  # noqa: PLR0913 - 夹具一次给全字段，避免测试里反复浅拷贝
    *,
    mesh_id: str,
    level: str,
    model_digest: str,
    digest: str,
    ratio: float = 1.0,
    thickness: float = 1.0,
    semantics: str = "unit_out_of_plane",
) -> dict[str, Any]:
    analytic = 25132.741
    return {
        "schema": "paramesh-mesh/1",
        "mesh_id": mesh_id,
        "digest": digest,
        "geometry_key": GEOMETRY_KEY,
        "dimension": "axisymmetric-2d",
        "level": level,
        "element_type": "shell",
        "coordinate_system": {"symmetry_axis": "x", "x": "axial", "y": "radial", "z": "out_of_plane"},
        "units": {"length": "mm"},
        "model": {"id": MODEL_ID, "digest": model_digest, "source": "model.yaml"},
        "regions": [
            {"name": name, "part_id": index, "area_rel_error": error}
            for index, (name, error) in enumerate(LEVEL_ERRORS[level].items(), 1)
        ],
        "parts": _parts(thickness=thickness, semantics=semantics),
        "sets": {
            "nodes": {
                "MESH_AXIS": {"id": 1, "count": 5},
                "MESH_END_END": {"id": 4, "count": 3},
            },
            "parts": {"MESH_PART_ALL": {"id": 101, "part_ids": [1, 2, 3]}},
        },
        "extent": {"z_start_mm": 0.0, "z_end_mm": 300.0, "r_max_mm": 20.0},
        "detonation": {"x": 0.0, "y": 0.0, "z": 0.0, "t0": 0.0},
        "probes": _probes(),
        "counts": {"nodes": 100, "elements": 90, "shells": 90},
        "quality": {
            "mass_check": {
                "basis": "unit_density",
                "section_thickness_mm": thickness,
                "analytic_volume_mm3": analytic,
                "mesh_volume_mm3": analytic * ratio,
                "ratio": ratio,
                "region_thickness_volume_mm3": analytic * 9.855,
                "region_thickness_ratio": 9.855,
            }
        },
        "tbd": [],
        "warnings": [],
        "files": {"mesh": "mesh.k", "quality": "quality.json", "quality_text": "quality.txt"},
    }


def write_package(  # noqa: PLR0913 - 夹具开关与清单字段一一对应
    root: Path,
    *,
    level: str = "medium",
    model_digest: str,
    digest: str | None = None,
    ratio: float = 1.0,
    thickness: float = 1.0,
    semantics: str = "unit_out_of_plane",
) -> Path:
    """写一个最小合法网格包；返回目录。"""
    content = digest or f"{level[0] * 8:0<8}"
    mesh_id = f"case-{level}-{content[:8]}"
    target = root / mesh_id
    target.mkdir(parents=True)
    (target / "mesh.k").write_text("*NODE\n1, 0.0, 0.0, 0.0\n", encoding="utf-8")
    manifest = _manifest(
        mesh_id=mesh_id,
        level=level,
        model_digest=model_digest,
        digest=content,
        ratio=ratio,
        thickness=thickness,
        semantics=semantics,
    )
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return target


def mutate(package: Path, change: Any) -> None:
    """读取清单、交给 ``change`` 修改后写回。"""
    path = package / "manifest.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def model_digest(model_file: Path) -> str:
    return load_models([model_file]).models[0].digest


@pytest.fixture
def mesh_root(tmp_path: Path, model_digest: str) -> Path:
    root = tmp_path / "meshes"
    for level in LEVEL_ORDER:
        write_package(root, level=level, model_digest=model_digest)
    return root


class TestTemplateFamilies:
    """模板族拆分：3d-ale 文本不变，axisymmetric-2d 独立。"""

    def test_default_family_is_unchanged(self) -> None:
        assert template_for("3d-ale") == DEFAULT_TEMPLATE

    def test_axisymmetric_family_has_mesh_placeholders(self) -> None:
        keys = {name for name, _ in template_placeholders(AXISYMMETRIC_TEMPLATE)}
        assert {"mesh_include", "parts_block", "sections_block", "history_nodes"} <= keys
        assert {"detonation_x", "detonation_y", "detonation_z", "detonation_t0"} <= keys
        assert "*ALE_MULTI_MATERIAL_GROUP" not in AXISYMMETRIC_TEMPLATE
        assert "*SECTION_SOLID" not in AXISYMMETRIC_TEMPLATE

    def test_unknown_dimension_errors(self) -> None:
        with pytest.raises(DeckError, match="未支持的模板维度"):
            template_for("plane-strain")


class TestLoadPackage:
    """§6.1 校验项逐条拒绝。"""

    def test_valid_package(self, mesh_root: Path, model_digest: str) -> None:
        package = load_package(mesh_root / "case-medium-mmmmmmmm", model_digest=model_digest)
        assert package.level == "medium"
        assert package.geometry_key == GEOMETRY_KEY
        assert [part.role for part in package.parts] == ["explosive", "air", "wall"]
        assert [part.mid for part in package.parts] == [1, 3, 2]
        assert package.mass_check["ratio"] == 1.0
        values = package.deck_values()
        assert values["mesh_include"] == f"../meshes/{package.mesh_id}/mesh.k"
        assert values["history_nodes"] == "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11"

    def test_rejects_schema(self, mesh_root: Path) -> None:
        package = mesh_root / "case-medium-mmmmmmmm"
        mutate(package, lambda doc: doc.update(schema="paramesh-mesh/0"))
        with pytest.raises(MeshError, match="schema"):
            load_package(package)

    def test_rejects_unsupported_dimension(self, mesh_root: Path) -> None:
        package = mesh_root / "case-medium-mmmmmmmm"
        mutate(package, lambda doc: doc.update(dimension="3d"))
        with pytest.raises(MeshError, match="dimension"):
            load_package(package)

    def test_rejects_directory_rename(self, mesh_root: Path) -> None:
        package = mesh_root / "case-medium-mmmmmmmm"
        renamed = package.with_name("case-medium-ffffffff")
        package.rename(renamed)
        with pytest.raises(MeshError, match="目录名"):
            load_package(renamed)

    def test_rejects_model_digest_mismatch(self, mesh_root: Path) -> None:
        with pytest.raises(MeshError, match=r"model\.digest"):
            load_package(mesh_root / "case-medium-mmmmmmmm", model_digest="f" * 64)

    def test_rejects_coordinate_system(self, mesh_root: Path) -> None:
        package = mesh_root / "case-medium-mmmmmmmm"

        def change(doc: dict[str, Any]) -> None:
            doc["coordinate_system"]["symmetry_axis"] = "y"

        mutate(package, change)
        with pytest.raises(MeshError, match="coordinate_system"):
            load_package(package)

    def test_rejects_mass_ratio_drift(self, tmp_path: Path, model_digest: str) -> None:
        package = write_package(tmp_path, model_digest=model_digest, ratio=9.855)
        with pytest.raises(MeshError, match="质量自检比值"):
            load_package(package)

    def test_rejects_region_thickness_as_section_thickness(self, tmp_path: Path, model_digest: str) -> None:
        package = write_package(tmp_path, model_digest=model_digest, thickness=5.0)
        with pytest.raises(MeshError, match="SECTION_SHELL"):
            load_package(package)

    def test_rejects_thickness_semantics(self, tmp_path: Path, model_digest: str) -> None:
        package = write_package(tmp_path, model_digest=model_digest, semantics="region_total")
        with pytest.raises(MeshError, match="thickness_semantics"):
            load_package(package)

    def test_rejects_missing_free_end_set(self, mesh_root: Path) -> None:
        package = mesh_root / "case-medium-mmmmmmmm"

        def change(doc: dict[str, Any]) -> None:
            doc["sets"]["nodes"].pop("MESH_END_END")

        mutate(package, change)
        with pytest.raises(MeshError, match="MESH_END_END"):
            load_package(package)

    def test_rejects_missing_station_probe(self, mesh_root: Path) -> None:
        package = mesh_root / "case-medium-mmmmmmmm"

        def change(doc: dict[str, Any]) -> None:
            doc["probes"] = [item for item in doc["probes"] if item["name"] != "axis_station_050"]

        mutate(package, change)
        with pytest.raises(MeshError, match="axis_station_050"):
            load_package(package)

    def test_rejects_unmapped_role(self, mesh_root: Path) -> None:
        package = mesh_root / "case-medium-mmmmmmmm"

        def change(doc: dict[str, Any]) -> None:
            doc["parts"][0]["role"] = "inert"

        mutate(package, change)
        with pytest.raises(MeshError, match="inert"):
            load_package(package)

    def test_rejects_missing_mesh_file(self, mesh_root: Path) -> None:
        package = mesh_root / "case-medium-mmmmmmmm"
        (package / "mesh.k").unlink()
        with pytest.raises(MeshError, match=r"mesh\.k"):
            load_package(package)


class TestMeshSet:
    """网格根扫描、档位选择与重复检测。"""

    def test_requires_explicit_level(self, mesh_root: Path, model_digest: str) -> None:
        meshes = scan_mesh_root(mesh_root)
        with pytest.raises(MeshError, match="--mesh-level"):
            meshes.for_model(model_digest)

    def test_selects_level(self, mesh_root: Path, model_digest: str) -> None:
        meshes = scan_mesh_root(mesh_root)
        assert meshes.for_model(model_digest, level="fine").level == "fine"
        assert meshes.levels_for(model_digest) == LEVEL_ORDER

    def test_unknown_level(self, mesh_root: Path, model_digest: str) -> None:
        meshes = scan_mesh_root(mesh_root)
        with pytest.raises(MeshError, match="没有档位 ultra"):
            meshes.for_model(model_digest, level="ultra")

    def test_duplicate_level(self, tmp_path: Path, model_digest: str) -> None:
        write_package(tmp_path, level="coarse", model_digest=model_digest, digest="a" * 8)
        write_package(tmp_path, level="coarse", model_digest=model_digest, digest="b" * 8)
        with pytest.raises(MeshError, match="重复"):
            scan_mesh_root(tmp_path)

    def test_missing_root(self, tmp_path: Path) -> None:
        with pytest.raises(MeshError, match="网格根目录不存在"):
            scan_mesh_root(tmp_path / "nope")

    def test_empty_root(self, tmp_path: Path) -> None:
        (tmp_path / "meshes").mkdir()
        with pytest.raises(MeshError, match="没有网格包"):
            scan_mesh_root(tmp_path / "meshes")


class TestLevelGate:
    """同 geometry_key 的档间误差比较。"""

    def test_convergent_levels(self, mesh_root: Path, model_digest: str) -> None:
        report = scan_mesh_root(mesh_root).compare_levels(model_digest)
        assert report.levels == LEVEL_ORDER
        assert report.convergent
        assert "收敛" in report.detail
        assert "区域\tcoarse" in format_level_report(report)

    def test_cross_geometry_key_rejected(self, mesh_root: Path) -> None:
        packages = list(scan_mesh_root(mesh_root).packages)
        fine = mesh_root / "case-fine-ffffffff"
        mutate(fine, lambda doc: doc.update(geometry_key="h" * 64))
        packages = [item for item in packages if item.level != "fine"]
        packages.append(load_package(fine))
        with pytest.raises(MeshError, match="geometry_key"):
            compare_levels(packages)


class TestDeckAssembly:
    """deck 组装：PART / SECTION_SHELL / INCLUDE / 起爆 / 探针。"""

    def _generate(self, small_params: Path, model_file: Path, mesh_root: Path, out: Path):
        return generate_decks(
            load_params(small_params),
            out,
            models=load_models([model_file]),
            only=["M001"],
            meshes=scan_mesh_root(mesh_root),
            mesh_level="medium",
        )

    def test_deck_uses_unit_thickness_and_mesh_include(
        self, small_params: Path, model_file: Path, mesh_root: Path, tmp_path: Path
    ) -> None:
        result = self._generate(small_params, model_file, mesh_root, tmp_path / "out")
        plan = result.plans[0]
        text = plan.text
        assert plan.template_family == "axisymmetric-2d"
        include = next(line for line in text.splitlines() if line.endswith("mesh.k"))
        assert "meshes/case-medium-mmmmmmmm/mesh.k" in include
        assert (plan.directory / include).resolve() == (mesh_root / "case-medium-mmmmmmmm" / "mesh.k").resolve()
        assert "*PART\npropellant" in text
        assert "3, 3, 2, 0, 0" in text
        assert "1, 14, 1.0, 0.0, 1.0, 0.0, 0, 1" in text
        assert "1.0, 1.0, 1.0, 1.0" in text
        assert "5.0, 5.0, 5.0, 5.0" not in text
        assert (
            "*INITIAL_DETONATION\n$ x = 轴向、y = 径向、z = 0（manifest.detonation，起爆端轴线）\n0.0, 0.0, 0.0, 0.0"
            in text
        )
        assert "*DATABASE_HISTORY_NODE" in text
        assert "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11" in text

    def test_case_and_manifest_record_mesh(
        self, small_params: Path, model_file: Path, mesh_root: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out"
        result = self._generate(small_params, model_file, mesh_root, out)
        case_json = json.loads((result.plans[0].directory / "case.json").read_text(encoding="utf-8"))
        assert case_json["mesh_id"] == "case-medium-mmmmmmmm"
        assert case_json["geometry_key"] == GEOMETRY_KEY
        assert case_json["level"] == "medium"
        assert case_json["template_family"] == "axisymmetric-2d"
        manifest = load_manifest(out)
        record = manifest.case("M001")
        assert record.mesh_id == "case-medium-mmmmmmmm"
        assert record.mesh_level == "medium"
        assert record.geometry_key == GEOMETRY_KEY
        assert manifest.template_name == "axisymmetric-2d"

    def test_generation_is_reproducible(
        self, small_params: Path, model_file: Path, mesh_root: Path, tmp_path: Path
    ) -> None:
        first = self._generate(small_params, model_file, mesh_root, tmp_path / "a")
        second = self._generate(small_params, model_file, mesh_root, tmp_path / "b")
        assert first.plans[0].text == second.plans[0].text
        assert first.plans[0].case.fingerprint == second.plans[0].case.fingerprint

    def test_mesh_requires_model(self, small_params: Path, mesh_root: Path, tmp_path: Path) -> None:
        with pytest.raises(DeckError, match="没有模型描述"):
            generate_decks(
                load_params(small_params),
                tmp_path / "out",
                only=["M002"],
                meshes=scan_mesh_root(mesh_root),
            )

    def test_mesh_rejects_3d_family(
        self, small_params: Path, model_file: Path, mesh_root: Path, tmp_path: Path
    ) -> None:
        with pytest.raises(DeckError, match="网格包只支持"):
            generate_decks(
                load_params(small_params),
                tmp_path / "out",
                only=["M001"],
                models=load_models([model_file]),
                meshes=scan_mesh_root(mesh_root),
                dimension="3d-ale",
            )


class TestCliWiring:
    """CLI：模板族输出与 --mesh-root 生成。"""

    def test_template_command_family(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "axisymmetric.k"
        dynacalc.template_command(out, dimension="axisymmetric-2d")
        text = out.read_text(encoding="utf-8")
        assert "${mesh_include}" in text
        assert "${parts_block}" in text
        assert "*SECTION_SOLID" not in text
        assert capsys.readouterr().out

    def test_generate_command_with_mesh(
        self, small_params: Path, model_file: Path, mesh_root: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "case"
        dynacalc.generate_command(
            small_params,
            out,
            model=[model_file],
            only=["M001"],
            mesh_root=mesh_root,
            mesh_level="fine",
        )
        manifest = load_manifest(out)
        record = manifest.case("M001")
        assert record.mesh_level == "fine"
        assert manifest.template_name == "axisymmetric-2d"
        assert (out / record.slug / "case.k").is_file()
