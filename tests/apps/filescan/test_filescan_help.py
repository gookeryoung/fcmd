"""filescan 帮助文档与示例：包内 Markdown、CLI 分段与 ``--help``。"""

from __future__ import annotations

import pytest

from fcmd.cli._schema import subcommand_schema
from fcmd.cli.main import FcmdApp
from fcmd.core.toolkit import get_tool
from fcmd.shared.helpdoc import load_help_doc


def test_packaged_help_covers_cli_and_gui_examples() -> None:
    """包内文档覆盖 scan / run / rules 与 GUI，并包含可复制示例。"""
    doc = load_help_doc("filescan")
    assert doc is not None
    text = doc.read_markdown()
    for command in ("filescan scan", "filescan run --config", "filescan rules --action"):
        assert command in text
    assert "filescanw" in text


def test_cli_subcommands_expose_examples_and_sections() -> None:
    """三个子命令都有示例与「相关」分段，JSON schema 同步暴露。"""
    for subcommand in ("scan", "run", "rules"):
        spec = get_tool("filescan", subcommand)
        assert spec.examples, f"{subcommand} 缺少示例"
        assert any(section.kind == "see_also" for section in spec.help_sections)
        payload = subcommand_schema(spec)
        assert payload["examples"]
        assert any(section["kind"] == "see_also" for section in payload["help_sections"])


def test_filescan_help_shows_examples_and_related(capsys: pytest.CaptureFixture[str]) -> None:
    """``filescan scan --help`` 末尾同时出现示例与相关说明。"""
    assert FcmdApp(["filescan", "scan", "--help"]).run() == 0
    out = capsys.readouterr().out
    assert "示例:" in out
    assert "filescan scan ." in out
    assert "相关:" in out
    assert "help/filescan.md" in out
