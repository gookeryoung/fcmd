"""帮助分段：``--help`` epilog 与 ``fcmd info`` 的同源渲染。"""

from __future__ import annotations

import json

import pytest

from fcmd.cli._schema import subcommand_schema
from fcmd.cli.main import FcmdApp
from fcmd.core.toolkit import _build_parser_for_tool, get_tool, tool
from fcmd.shared.helpdoc import HelpSection


def test_help_epilog_renders_sections_in_canonical_order(capsys: pytest.CaptureFixture[str]) -> None:
    """示例与环境变量按固定顺序出现在 epilog，相关段排在最后。"""

    @tool(
        "help_sections_demo",
        help="演示",
        examples=["fcmd help_sections_demo ."],
        help_sections=[
            HelpSection("see_also", lines=["GUI：fcmd help_sections_demow"]),
            HelpSection("env", lines=["FCMD_HOME  配置目录"]),
        ],
    )
    def demo(path: str) -> None:
        """演示函数。"""

    with pytest.raises(SystemExit):
        _build_parser_for_tool(get_tool("help_sections_demo")).parse_args(["--help"])
    out = capsys.readouterr().out
    assert out.index("示例:") < out.index("环境变量:") < out.index("相关:")
    assert "FCMD_HOME  配置目录" in out


def test_subcommand_schema_exposes_help_sections() -> None:
    """JSON schema 携带分段，GUI / 模型可复用同一份数据。"""

    @tool(
        "help_sections_schema",
        help="演示",
        help_sections=[HelpSection("exit_codes", lines=["0  成功", "2  用法错误"])],
    )
    def demo() -> None:
        """演示函数。"""

    payload = subcommand_schema(get_tool("help_sections_schema"))
    assert payload["help_sections"] == [{"kind": "exit_codes", "title": "退出码", "lines": ["0  成功", "2  用法错误"]}]
    json.dumps(payload, ensure_ascii=False)


def test_cli_help_and_info_share_sections(capsys: pytest.CaptureFixture[str]) -> None:
    """真实工具的分段同时出现在子命令 ``--help`` 与 ``fcmd info`` 文本中。"""
    assert FcmdApp(["renamer", "preview", "--help"]).run() == 0
    help_text = capsys.readouterr().out
    assert "相关:" in help_text
    assert "fcmd renamerw" in help_text

    assert FcmdApp(["info", "renamer", "preview"]).run() == 0
    info_text = capsys.readouterr().out
    assert "相关:" in info_text
    assert "fcmd renamerw" in info_text


def test_info_json_has_help_sections(capsys: pytest.CaptureFixture[str]) -> None:
    """``fcmd info --json`` 的分段与 ``--help`` 同源。"""
    assert FcmdApp(["info", "renamer", "preview", "--json"]).run() == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(section["kind"] == "see_also" for section in payload["subcommand"]["help_sections"])
