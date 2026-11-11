"""pptmake CLI 测试：注册、预设、模板生成 / 查看 / 导出 / 导入。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import pptmake
from fcmd.apps.pptmake._pptx import pptx_slide_count
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool


def test_tool_registration() -> None:
    assert set(_TOOL_REGISTRY["pptmake"]) == {"presets", "init", "inspect", "export", "import"}


def test_presets_lists_styles_layouts_and_decors(capsys: pytest.CaptureFixture[str]) -> None:
    pptmake.presets_command()
    output = capsys.readouterr().out
    assert "风格预设" in output
    assert core.STYLE_AURORA in output
    assert "内置布局" in output
    assert "封面" in output
    assert "装饰样式" in output
    assert "16:9" in output


def test_init_creates_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "demo.pptmtpl.json"
    pptmake.init_command(target, name="测试模板", style="porcelain", aspect="4:3", decor="grid")
    output = capsys.readouterr().out
    assert target.is_file()
    assert "已生成模板" in output
    assert "测试模板" in output
    template = core.load_template(target)
    assert template.name == "测试模板"
    assert template.theme.style == core.STYLE_PORCELAIN
    assert template.aspect == core.ASPECT_4_3
    assert template.theme.decor == core.DECOR_GRID


def test_init_defaults_name_to_file_stem(tmp_path: Path) -> None:
    target = tmp_path / "品牌模板.pptmtpl.json"
    pptmake.init_command(target)
    assert core.load_template(target).name == "品牌模板"


def test_init_refuses_existing_without_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "demo.pptmtpl.json"
    target.write_text("keep", encoding="utf-8")
    pptmake.init_command(target)
    assert "模板已存在" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "keep"


def test_init_reports_invalid_aspect(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pptmake.init_command(tmp_path / "demo.json", aspect=cast(pptmake.AspectName, "21:9"))
    assert "画幅需为" in capsys.readouterr().out


def test_init_overwrites_when_allowed(tmp_path: Path) -> None:
    target = tmp_path / "demo.pptmtpl.json"
    target.write_text("old", encoding="utf-8")
    pptmake.init_command(target, style="coral", overwrite=True)
    assert core.load_template(target).theme.style == core.STYLE_CORAL


def test_inspect_prints_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "demo.pptmtpl.json"
    core.save_template(core.default_template(), target)
    pptmake.inspect_command(target)
    output = capsys.readouterr().out
    assert "品牌演示模板" in output
    assert "页面：13 页" in output


def test_inspect_reports_load_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pptmake.inspect_command(tmp_path / "missing.json")
    assert "读取模板失败" in capsys.readouterr().out


def test_export_creates_pptx(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    template_path = tmp_path / "demo.pptmtpl.json"
    output_path = tmp_path / "out.pptx"
    core.save_template(core.default_template(), template_path)
    pptmake.export_command(template_path, output_path)
    assert "已生成 PPTX" in capsys.readouterr().out
    assert output_path.is_file()
    assert pptx_slide_count(output_path) == len(core.LAYOUTS)


def test_export_defaults_to_same_name(tmp_path: Path) -> None:
    template_path = tmp_path / "demo.pptmtpl.json"
    core.save_template(core.default_template(), template_path)
    pptmake.export_command(template_path)
    assert (tmp_path / "demo.pptx").is_file()


def test_export_refuses_existing_without_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    template_path = tmp_path / "demo.pptmtpl.json"
    output_path = tmp_path / "out.pptx"
    core.save_template(core.default_template(), template_path)
    output_path.write_bytes(b"old")
    pptmake.export_command(template_path, output_path)
    assert "输出已存在" in capsys.readouterr().out
    assert output_path.read_bytes() == b"old"


def test_export_overwrites_when_allowed(tmp_path: Path) -> None:
    template_path = tmp_path / "demo.pptmtpl.json"
    output_path = tmp_path / "out.pptx"
    core.save_template(core.default_template(), template_path)
    output_path.write_bytes(b"old")
    pptmake.export_command(template_path, output_path, overwrite=True)
    assert output_path.read_bytes()[:2] == b"PK"


def test_export_reports_missing_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pptmake.export_command(tmp_path / "missing.json", tmp_path / "out.pptx")
    assert "读取模板失败" in capsys.readouterr().out


def test_import_creates_template_from_pptx(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "deck.pptx"
    core.save_template(core.default_template(), tmp_path / "demo.pptmtpl.json")
    pptmake.export_command(tmp_path / "demo.pptmtpl.json", source)
    capsys.readouterr()
    target = tmp_path / "imported.pptmtpl.json"
    pptmake.import_command(source, target)
    output = capsys.readouterr().out
    assert "已导入模板" in output
    template = core.load_template(target)
    assert len(template.slides) == len(core.LAYOUTS)
    assert template.slides[0].layout == core.LAYOUT_COVER


def test_import_defaults_output_name(tmp_path: Path) -> None:
    source = tmp_path / "deck.pptx"
    core.save_template(core.default_template(), tmp_path / "demo.pptmtpl.json")
    pptmake.export_command(tmp_path / "demo.pptmtpl.json", source)
    pptmake.import_command(source)
    assert (tmp_path / "deck.pptmtpl.json").is_file()


def test_import_refuses_existing_without_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "deck.pptx"
    core.save_template(core.default_template(), tmp_path / "demo.pptmtpl.json")
    pptmake.export_command(tmp_path / "demo.pptmtpl.json", source)
    target = tmp_path / "deck.pptmtpl.json"
    target.write_text("keep", encoding="utf-8")
    capsys.readouterr()
    pptmake.import_command(source)
    assert "输出已存在" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "keep"


def test_import_reports_invalid_pptx(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "broken.pptx"
    source.write_text("not a pptx", encoding="utf-8")
    pptmake.import_command(source)
    assert "读取 PPTX 失败" in capsys.readouterr().out


def test_import_reports_missing_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pptmake.import_command(tmp_path / "missing.pptx")
    assert "PPTX 不存在" in capsys.readouterr().out


def test_cli_entry_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    template_path = tmp_path / "cli.pptmtpl.json"
    output_path = tmp_path / "cli.pptx"
    assert run_tool("pptmake", ["presets"]) == 0
    capsys.readouterr()
    assert run_tool("pptmake", ["init", str(template_path), "--style", "graphite"]) == 0
    assert template_path.is_file()
    capsys.readouterr()
    assert run_tool("pptmake", ["export", str(template_path), "--output", str(output_path)]) == 0
    assert output_path.is_file()
