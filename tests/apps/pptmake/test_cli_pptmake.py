"""pptmake CLI 测试：注册、预设、模板生成 / 查看 / 导出 / 导入。"""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import pptmake
from fcmd.apps.pptmake._pptx import pptx_slide_count
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool


def test_tool_registration() -> None:
    assert set(_TOOL_REGISTRY["pptmake"]) == {
        "presets",
        "init",
        "inspect",
        "export",
        "import",
        "validate",
        "doctor",
        "build",
    }


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
    assert f"页面：{len(core.LAYOUTS)} 页" in output


def test_inspect_reports_load_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pptmake.inspect_command(tmp_path / "missing.json")
    assert "读取模板失败" in capsys.readouterr().out


def test_build_from_json_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "deck.json"
    data.write_text(
        json.dumps({"slides": [{"layout": "cover", "title": "批量封面"}, {"layout": "closing", "title": "谢谢"}]}),
        encoding="utf-8",
    )
    target = tmp_path / "deck.pptx"
    pptmake.build_command(data, output=target, overwrite=True)
    output = capsys.readouterr().out
    assert "已生成 PPTX" in output
    assert pptx_slide_count(target) == 2


def test_build_rejects_bad_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "deck.json"
    data.write_text("{not json", encoding="utf-8")
    pptmake.build_command(data, output=tmp_path / "deck.pptx")
    assert "读取数据失败" in capsys.readouterr().out


def test_build_refuses_overflow_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "bad.json"
    data.write_text(
        json.dumps(
            {
                "slides": [
                    {
                        "layout": "bullets",
                        "title": "溢出",
                        "bullets": ["这是一行非常长的要点文字" * 8 for _ in range(12)],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / "bad.pptx"
    pptmake.build_command(data, output=target)
    assert "导出已取消" in capsys.readouterr().out
    assert not target.exists()
    pptmake.build_command(data, output=target, force=True)
    assert target.is_file()


def test_validate_reports_tiny_font(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    template = core.new_template(slides=(core.new_slide(core.LAYOUT_BULLETS),))
    tokens = core.tokens_from_mapping({"type_scale": {"body": 10.0}})
    template = replace(template, theme=replace(template.theme, tokens=tokens))
    target = tmp_path / "tiny.pptmtpl.json"
    core.save_template(template, target)
    capsys.readouterr()
    pptmake.validate_command(target)
    assert "小于 12pt" in capsys.readouterr().out


def test_validate_passes_clean_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "clean.pptmtpl.json"
    core.save_template(core.new_template(slides=(core.new_slide(core.LAYOUT_COVER),)), target)
    capsys.readouterr()
    pptmake.validate_command(target)
    output = capsys.readouterr().out
    assert "校验通过" in output


def test_validate_reports_overflow_and_missing_image(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    template = core.new_template(
        slides=(
            core.SlideSpec(
                layout=core.LAYOUT_BULLETS,
                title="溢出",
                bullets=tuple("这是一行非常长的要点文字" * 8 for _ in range(12)),
            ),
            core.SlideSpec(layout=core.LAYOUT_IMAGE, title="图", image=str(tmp_path / "missing.png")),
        )
    )
    target = tmp_path / "bad.pptmtpl.json"
    core.save_template(template, target)
    pptmake.validate_command(target)
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "可能溢出" in output
    assert "图片不存在" in output


def test_export_refuses_quality_issues_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    template = core.new_template(
        slides=(
            core.SlideSpec(
                layout=core.LAYOUT_BULLETS,
                title="溢出",
                bullets=tuple("这是一行非常长的要点文字" * 8 for _ in range(12)),
            ),
        )
    )
    path = tmp_path / "bad.pptmtpl.json"
    core.save_template(template, path)
    target = tmp_path / "bad.pptx"
    pptmake.export_command(path, target)
    output = capsys.readouterr().out
    assert "导出已取消" in output
    assert not target.exists()
    pptmake.export_command(path, target, force=True)
    assert target.is_file()


def test_export_flat_writes_static_page_number(tmp_path: Path) -> None:
    template = core.new_template()
    path = tmp_path / "flat.pptmtpl.json"
    core.save_template(template, path)
    target = tmp_path / "flat.pptx"
    pptmake.export_command(path, target, flat=True)
    with zipfile.ZipFile(target) as archive:
        slides = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
    assert '<a:schemeClr val="bg1"/>' not in slides
    assert f"02 / {len(core.LAYOUTS)}" in slides


def test_doctor_prints_environment(capsys: pytest.CaptureFixture[str]) -> None:
    pptmake.doctor_command()
    output = capsys.readouterr().out
    assert "python-pptx" in output
    assert "LibreOffice" in output


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


def test_init_scene_writes_scene_template(tmp_path: Path) -> None:
    from fcmd.apps.pptmake import _library

    target = tmp_path / "launch.pptmtpl.json"
    pptmake.init_command(target, scene="launch", style="coral")
    template = core.load_template(target)
    assert [slide.layout for slide in template.slides] == list(_library.SCENES["launch"])
    assert template.theme.style == core.STYLE_CORAL


def test_init_rejects_unknown_scene(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "bad.pptmtpl.json"
    pptmake.init_command(target, scene="unknown")
    captured = capsys.readouterr()
    assert "未知场景" in captured.out + captured.err
    assert not target.exists()


def test_export_potx_defaults_to_potx_suffix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    template_path = tmp_path / "brand.pptmtpl.json"
    core.save_template(core.new_template(), template_path)
    pptmake.export_command(template_path, potx=True)
    target = tmp_path / "brand.potx"
    assert target.is_file()
    assert ".potx" in capsys.readouterr().out


def test_quality_reports_unlabeled_image(tmp_path: Path) -> None:
    image = tmp_path / "hero.png"
    image.write_bytes(b"not-a-real-png")
    template = core.new_template(slides=(core.SlideSpec(layout=core.LAYOUT_IMAGE, title="图片", image=str(image)),))
    report = pptmake._quality_issues(template, verbose=False)
    assert report.unlabeled == 1
    assert report.missing == 0
