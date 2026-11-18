"""`--json` 模式下的用法错误信封测试。

覆盖：未知工具 / 缺子命令 / 未知子命令 / 未知选项四条机读路径。要求
stdout 为空、stderr 恰好是一条合法 ``fcmd-error/1`` 信封、退出码为 2；
同时确认不带 ``--json`` 时仍走原有文本路径。
"""

from __future__ import annotations

import json

import pytest

from fcmd.cli.main import FcmdApp


def _run_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    """运行 CLI 并返回 (退出码, 解析后的 stderr 信封)。"""
    code = FcmdApp(argv).run()
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["schema"] == "fcmd-error/1"
    assert payload["ok"] is False
    assert captured.out == ""
    return code, payload


def test_unknown_tool_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run_json(["nosuchtool", "--json"], capsys)
    assert code == 2
    assert payload["code"] == "UNKNOWN_TOOL"
    assert payload["tool"] == "nosuchtool"


def test_missing_subcommand_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run_json(["casetool", "--json"], capsys)
    assert code == 2
    assert payload["code"] == "USAGE"
    assert payload["tool"] == "casetool"


def test_unknown_subcommand_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run_json(["casetool", "nosuchsub", "--json"], capsys)
    assert code == 2
    assert payload["code"] == "UNKNOWN_SUBCOMMAND"


def test_unknown_option_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run_json(["textdiff", "file", "a.txt", "b.txt", "--json", "--nope"], capsys)
    assert code == 2
    assert payload["code"] == "USAGE"
    assert payload["subcommand"] == "file"
    assert "参数解析失败" in str(payload["message"])


def test_usage_error_without_json_stays_text(capsys: pytest.CaptureFixture[str]) -> None:
    code = FcmdApp(["casetool"]).run()
    captured = capsys.readouterr()
    assert code == 2
    assert "需要指定子命令" in captured.out
    assert "fcmd-error/1" not in captured.out + captured.err


def test_import_failure_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """工具模块导入失败时，`--json` 也输出错误信封。"""
    from fcmd.cli import _discovery as discovery_mod

    def _raise_import_error(_name: str) -> None:
        raise ImportError("simulated load failure")

    monkeypatch.setitem(discovery_mod._TOOL_ALIASES, "broken", "broken_tool")
    monkeypatch.setitem(discovery_mod._TOOL_MODULES, "broken_tool", "fcmd.cli.broken_tool")
    monkeypatch.setattr(discovery_mod.importlib, "import_module", _raise_import_error)

    code, payload = _run_json(["broken", "--json"], capsys)
    assert code == 1
    assert payload["code"] == "RUNTIME_ERROR"
    assert "加载工具" in str(payload["message"])
