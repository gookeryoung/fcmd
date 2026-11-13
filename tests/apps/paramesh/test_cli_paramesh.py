"""paramesh CLI 测试：注册、示例、检查、生成、报告与契约输出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.paramesh import paramesh
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool

GOLDEN_DIGEST = "ce29bedba34f6bc6fcc3916ba9912c46da3f008b7cd4e8c23b6ae8098f5cf5b1"


def test_tool_registration() -> None:
    assert set(_TOOL_REGISTRY["paramesh"]) == {"example", "check", "generate", "report", "contract"}


def test_example_writes_model_and_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "model.yaml"
    paramesh.example(target)
    out = capsys.readouterr().out
    assert "已写入示例模型" in out
    assert "已写入网格配置" in out
    assert "schema: dynacalc-model/1" in target.read_text(encoding="utf-8")
    config = tmp_path / "model.mesh.yaml"
    assert config.is_file()
    assert "schema: paramesh/1" in config.read_text(encoding="utf-8")


def test_example_requires_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "model.yaml"
    paramesh.example(target)
    capsys.readouterr()
    paramesh.example(target)
    out = capsys.readouterr().out
    assert "错误:" in out
    assert "overwrite" in out


def test_check_prints_three_levels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "model.yaml"
    paramesh.example(target)
    capsys.readouterr()
    paramesh.check(target, config=tmp_path / "model.mesh.yaml")
    out = capsys.readouterr().out
    for level in ("coarse", "medium", "fine"):
        assert f"--- {level} ---" in out
    assert "驱动：最小几何缺陷" in out
    assert "检查完成：未写入任何文件。" in out
    assert not (tmp_path / "meshes").exists()


def test_check_requires_config_reaction_zone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramesh.example(model)
    (tmp_path / "model.mesh.yaml").unlink()
    capsys.readouterr()
    paramesh.check(model)
    assert "reaction_zone_mm" in capsys.readouterr().out


def test_generate_selected_level(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramesh.example(model)
    capsys.readouterr()
    output = tmp_path / "meshes"
    paramesh.generate(model, output, levels="coarse", config=tmp_path / "model.mesh.yaml")
    out = capsys.readouterr().out
    assert "coarse: mesh_id=" in out
    assert "壁厚层数" in out
    packages = [item for item in output.iterdir() if item.is_dir()]
    assert len(packages) == 1
    assert (packages[0] / "manifest.json").is_file()


def test_generate_all_levels_share_geometry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramesh.example(model)
    capsys.readouterr()
    output = tmp_path / "meshes"
    paramesh.generate(model, output, levels="all", config=tmp_path / "model.mesh.yaml")
    out = capsys.readouterr().out
    for level in ("coarse", "medium", "fine"):
        assert f"{level}: mesh_id=" in out
    manifests = [json.loads((item / "manifest.json").read_text(encoding="utf-8")) for item in output.iterdir()]
    assert len(manifests) == 3
    assert len({item["geometry_key"] for item in manifests}) == 1
    assert len({item["model"]["digest"] for item in manifests}) == 1
    assert all(item["model"]["digest"] == GOLDEN_DIGEST for item in manifests)


def test_generate_rejects_unknown_level(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramesh.example(model)
    capsys.readouterr()
    paramesh.generate(model, tmp_path / "meshes", levels="ultra", config=tmp_path / "model.mesh.yaml")
    assert "未知档位" in capsys.readouterr().out


def test_generate_requires_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramesh.example(model)
    capsys.readouterr()
    output = tmp_path / "meshes"
    paramesh.generate(model, output, levels="coarse", config=tmp_path / "model.mesh.yaml")
    capsys.readouterr()
    paramesh.generate(model, output, levels="coarse", config=tmp_path / "model.mesh.yaml")
    out = capsys.readouterr().out
    assert "已存在" in out
    assert "overwrite" in out


def test_report_text_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    paramesh.example(model)
    capsys.readouterr()
    output = tmp_path / "meshes"
    paramesh.generate(model, output, levels="coarse", config=tmp_path / "model.mesh.yaml")
    package = next(item for item in output.iterdir() if item.is_dir())
    capsys.readouterr()
    paramesh.report(package)
    text = capsys.readouterr().out
    assert "网格包：" in text
    assert "几何指纹：" in text
    assert "单元 / 节点：" in text
    paramesh.report(package, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest"]["mesh_id"] == package.name
    assert payload["quality"]["element_count"] > 0


def test_report_missing_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paramesh.report(tmp_path / "missing")
    assert "无法读取网格清单" in capsys.readouterr().out


def test_contract_draft_mentions_handoff(capsys: pytest.CaptureFixture[str]) -> None:
    paramesh.contract()
    out = capsys.readouterr().out
    assert "paramesh-mesh/1" in out
    assert "manifest.json" in out
    assert "geometry_key" in out
    assert "不变量" in out


def test_run_tool_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = tmp_path / "model.yaml"
    assert run_tool("paramesh", ["example", str(model)]) == 0
    capsys.readouterr()
    assert run_tool("paramesh", ["check", str(model), "--config", str(tmp_path / "model.mesh.yaml")]) == 0
    assert "--- fine ---" in capsys.readouterr().out
