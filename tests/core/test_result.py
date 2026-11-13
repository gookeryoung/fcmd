"""结果信封（core.result）测试。

验证 ``fcmd-result/1`` / ``fcmd-error/1`` 的字段与两种输出流：
成功结果写 stdout，错误写 stderr；错误同时带人类可读 ``message`` 与
机器可判定的 ``code`` / ``field`` / ``expected`` / ``hint``。
"""

from __future__ import annotations

import json
import sys

import pytest

from fcmd.core.result import (
    ERROR_SCHEMA,
    RESULT_SCHEMA,
    SCHEMA_SCHEMA,
    emit_error,
    emit_json,
    emit_result,
)


def test_schema_versions() -> None:
    """信封版本号稳定（调用方按此分派）。"""
    assert RESULT_SCHEMA == "fcmd-result/1"
    assert ERROR_SCHEMA == "fcmd-error/1"
    assert SCHEMA_SCHEMA == "fcmd-schema/1"


def test_emit_json_default_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """默认写 stdout，中文不转义，缩进两格。"""
    emit_json({"名称": "文件", "total": 1})
    captured = capsys.readouterr()
    assert captured.err == ""
    assert '"名称": "文件"' in captured.out
    assert json.loads(captured.out) == {"名称": "文件", "total": 1}


def test_emit_json_explicit_stream(capsys: pytest.CaptureFixture[str]) -> None:
    """显式 stream（stderr）可写。"""
    emit_json({"ok": True}, stream=sys.stderr)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"ok": True}


def test_emit_result(capsys: pytest.CaptureFixture[str]) -> None:
    """成功信封含 schema/ok/tool/subcommand/data。"""
    emit_result("filesearch", "name", {"total": 0})
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "tool": "filesearch",
        "subcommand": "name",
        "data": {"total": 0},
    }


def test_emit_error_minimal(capsys: pytest.CaptureFixture[str]) -> None:
    """错误信封必填字段齐全，可选字段缺省时不出现。"""
    emit_error("hashfile", "f", code="FILE_NOT_FOUND", message="文件不存在: a.txt")
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["schema"] == ERROR_SCHEMA
    assert payload["ok"] is False
    assert payload["kind"] == "input"
    assert payload["code"] == "FILE_NOT_FOUND"
    assert payload["message"] == "文件不存在: a.txt"
    assert "field" not in payload
    assert "expected" not in payload
    assert "hint" not in payload


def test_emit_error_full(capsys: pytest.CaptureFixture[str]) -> None:
    """错误信封带定位与修正提示。"""
    emit_error(
        "filesearch",
        "content",
        code="INVALID_REGEX",
        message="正则表达式错误: bad",
        kind="input",
        field="pattern",
        expected="Python 正则表达式",
        hint="检查括号与转义",
    )
    payload = json.loads(capsys.readouterr().err)
    assert payload["field"] == "pattern"
    assert payload["expected"] == "Python 正则表达式"
    assert payload["hint"] == "检查括号与转义"
    assert payload["subcommand"] == "content"
