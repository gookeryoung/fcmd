"""paramcad CLI 测试：注册、示例、摘要、导出与指纹输出。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.paramcad import _export, paramcad
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool


def test_tool_registration() -> None:
    assert set(_TOOL_REGISTRY["paramcad"]) == {"example", "inspect", "export", "digest", "contract"}


def test_example_writes_contract_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "model.yaml"
    paramcad.example(target)
    out = capsys.readouterr().out
    assert "已写入示例模型" in out
    assert target.read_text(encoding="utf-8").startswith("schema: dynacalc-model/1")


def test_example_requires_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "model.yaml"
    paramcad.example(target)
    capsys.readouterr()
    paramcad.example(target)
    out = capsys.readouterr().out
    assert "错误:" in out
    assert "--overwrite" in out


def test_inspect_prints_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "model.yaml"
    paramcad.example(target)
    capsys.readouterr()
    paramcad.inspect(target)
    out = capsys.readouterr().out
    assert "模型：example" in out
    assert "几何（文档 / 导出使用）" in out
    assert "分布场：" in out
    assert "TBD：" in out


def test_inspect_reports_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paramcad.inspect(tmp_path / "missing.yaml")
    assert "模型文件不存在" in capsys.readouterr().out


def test_export_reports_models_and_digest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "model.yaml"
    paramcad.example(source)
    capsys.readouterr()
    target = tmp_path / "out.yaml"
    paramcad.export(source, target)
    out = capsys.readouterr().out
    expected = _export.model_digest(_export.example_model())
    assert "已导出 1 个模型" in out
    assert f"digest={expected}" in out
    assert _export.load_model(target).model_id == "example"


def test_digest_prints_full_hash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "model.yaml"
    paramcad.example(source)
    capsys.readouterr()
    paramcad.digest(source)
    out = capsys.readouterr().out.strip()
    name, digest = out.split("\t")
    assert name == "example"
    assert digest == _export.model_digest(_export.example_model())
    assert len(digest) == 64


def test_contract_prints_schema_and_digest_rule(capsys: pytest.CaptureFixture[str]) -> None:
    paramcad.contract()
    out = capsys.readouterr().out
    assert out.startswith("schema: dynacalc-model/1")
    assert "digest:" in out
    assert "TBD" in out


def test_run_tool_paramcad_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "model.yaml"
    assert run_tool("paramcad", ["example", str(target)]) == 0
    assert target.is_file()
    capsys.readouterr()
    assert run_tool("paramcad", ["digest", str(target)]) == 0
    assert "example\t" in capsys.readouterr().out
