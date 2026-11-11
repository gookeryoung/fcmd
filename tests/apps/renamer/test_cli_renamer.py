"""renamer CLI 测试：命令注册、预览输出、@规则文件与实际改名。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.renamer import renamer
from fcmd.apps.renamer._core import RuleError
from fcmd.core.toolkit import _TOOL_REGISTRY


def _touch(root: Path, name: str, data: str = "x") -> Path:
    path = root / name
    path.write_text(data, encoding="utf-8")
    return path


def test_tool_registration() -> None:
    """renamer 注册 preview / apply 两个子命令。"""
    assert set(_TOOL_REGISTRY["renamer"]) == {"preview", "apply"}


def test_load_rules_inline_and_file(tmp_path: Path) -> None:
    rules = renamer.load_rules('[{"kind": "case", "mode": "upper"}]')
    assert rules[0].mode == "upper"
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{"kind": "replace", "pattern": "a", "replacement": "b"}]), encoding="utf-8")
    assert renamer.load_rules(f"@{path}")[0].replacement == "b"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("", "未提供规则"),
        ("{broken", "规则 JSON 解析失败"),
        ("@missing.json", "读取规则文件失败"),
    ],
)
def test_load_rules_errors(payload: str, message: str) -> None:
    with pytest.raises(RuleError, match=message):
        renamer.load_rules(payload)


def test_preview_command_outputs_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _touch(tmp_path, "a.txt")
    second = _touch(tmp_path, "b.txt")
    rules = json.dumps([{"kind": "replace", "pattern": "^a$", "replacement": "x"}])
    renamer.preview_command([first, second], rules)
    output = capsys.readouterr().out
    assert "可执行 1" in output
    assert "a.txt -> x.txt" in output
    assert "b.txt" in output
    assert first.exists()


def test_preview_reports_rule_and_path_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _touch(tmp_path, "a.txt")
    renamer.preview_command([path], "")
    assert "未提供规则" in capsys.readouterr().out

    renamer.preview_command([path], '[{"kind": "replace", "pattern": "("}]')
    assert "正则表达式无效" in capsys.readouterr().out

    renamer.preview_command([tmp_path / "missing"], '[{"kind": "case"}]')
    assert "路径不存在" in capsys.readouterr().out


def test_apply_command_renames_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _touch(tmp_path, "IMG_001.txt")
    second = _touch(tmp_path, "IMG_002.txt")
    rules = json.dumps([{"kind": "replace", "pattern": r"^IMG_"}])
    renamer.apply_command([first, second], rules)
    output = capsys.readouterr().out
    assert (tmp_path / "001.txt").exists()
    assert (tmp_path / "002.txt").exists()
    assert "已完成 2 项" in output


def test_apply_command_from_rule_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _touch(tmp_path, "2024-report.txt")
    rule_file = tmp_path / "rules.json"
    rule_file.write_text(
        json.dumps([{"kind": "reorder", "pattern": r"^(\d{4})-(.+)$", "replacement": "{2}-{1}"}]),
        encoding="utf-8",
    )
    renamer.apply_command([source], f"@{rule_file}")
    assert (tmp_path / "report-2024.txt").exists()
    assert "已完成 1 项" in capsys.readouterr().out


def test_apply_command_without_changes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _touch(tmp_path, "a.txt")
    renamer.apply_command([source], '[{"kind": "case", "mode": "lower"}]')
    assert "没有可执行的改名" in capsys.readouterr().out


def test_apply_command_recursive_and_dirs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = tmp_path / "sub"
    folder.mkdir()
    _touch(folder, "a.txt")
    rules = json.dumps([{"kind": "case", "mode": "upper"}])
    renamer.apply_command([tmp_path], rules, recursive=True)
    assert (folder / "A.txt").exists()
    assert "已完成 1 项" in capsys.readouterr().out
