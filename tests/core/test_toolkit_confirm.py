"""``requires_confirmation`` 确认闸门框架测试。"""

from __future__ import annotations

import copy
import json

import pytest

import fcmd
from fcmd.cli._schema import subcommand_schema
from fcmd.core.toolkit import (
    _TOOL_REGISTRY,
    _build_parser_for_tool,
    clear_tool_registry,
    get_tool,
    run_tool,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个测试前后隔离注册表，避免污染其他测试文件。"""
    saved = copy.deepcopy(_TOOL_REGISTRY)
    clear_tool_registry()
    yield
    clear_tool_registry()
    _TOOL_REGISTRY.update(saved)


def _register(requires_confirmation: bool = True) -> list[str]:
    """注册一个记录调用次数的测试工具。"""
    calls: list[str] = []

    @fcmd.tool("confirmdemo", requires_confirmation=requires_confirmation)
    def demo(value: str = "x", json_output: bool = False) -> None:
        calls.append(value)

    return calls


class TestConfirmGate:
    """未确认时的拒绝与放行。"""

    def test_rejects_without_confirm(self) -> None:
        """缺少 --confirm 时退出 2 且不执行。"""
        calls = _register()
        assert run_tool("confirmdemo", []) == 2
        assert calls == []

    def test_accepts_with_confirm(self) -> None:
        """带 --confirm 时正常执行。"""
        calls = _register()
        assert run_tool("confirmdemo", ["--confirm"]) == 0
        assert calls == ["x"]

    def test_dry_run_passes_gate(self) -> None:
        """--dry-run 视为预览，不要求确认且不执行。"""
        calls = _register()
        assert run_tool("confirmdemo", ["--dry-run"]) == 0
        assert calls == []

    def test_json_error_envelope(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 下拒绝时写 CONFIRM_REQUIRED 信封。"""
        _register()
        assert run_tool("confirmdemo", ["--json"]) == 2
        payload = json.loads(capsys.readouterr().err)
        assert payload["schema"] == "fcmd-error/1"
        assert payload["code"] == "CONFIRM_REQUIRED"

    def test_no_gate_when_not_required(self) -> None:
        """未声明 requires_confirmation 的工具不需要确认。"""
        calls = _register(requires_confirmation=False)
        assert run_tool("confirmdemo", []) == 0
        assert calls == ["x"]


class TestMetadata:
    """parser 与 schema 暴露确认需求。"""

    def test_parser_has_confirm_flag(self) -> None:
        """声明确认需求的工具 parser 含 --confirm。"""
        _register()
        parser = _build_parser_for_tool(get_tool("confirmdemo", None))
        assert "--confirm" in parser.format_help()

    def test_parser_without_confirm_flag(self) -> None:
        """未声明时 parser 不暴露 --confirm。"""
        _register(requires_confirmation=False)
        parser = _build_parser_for_tool(get_tool("confirmdemo", None))
        assert "--confirm" not in parser.format_help()

    def test_schema_flags(self) -> None:
        """schema 标记要求确认与 JSON 就绪。"""
        _register()
        schema = subcommand_schema(get_tool("confirmdemo", None))
        assert schema["requires_confirmation"] is True
        assert schema["json"] is True

    def test_schema_flags_default(self) -> None:
        """默认工具 schema 中两者均为 False。"""
        _register(requires_confirmation=False)
        schema = subcommand_schema(get_tool("confirmdemo", None))
        assert schema["requires_confirmation"] is False
