"""退出码契约（core.exit_codes）测试。

验证 P1 契约：码值唯一、``kind`` 与码值一一对应、错误码到 ``kind`` 的映射
互斥且完整，以及 ``emit_error`` / ``print_error`` 两条路径给出一致退出码。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from fcmd.console import print_error
from fcmd.core.exit_codes import ERROR_CODE_KINDS, ERROR_KINDS, ExitCode, exit_code_for, kind_for
from fcmd.core.result import (
    emit_error,
    has_pending_error,
    pending_exit_code,
    record_error,
    report_error,
    reset_error_state,
    take_pending_exit_code,
)


@pytest.fixture(autouse=True)
def _clean_error_state() -> Iterator[None]:
    """每个用例前后清空登记状态，避免跨用例污染。"""
    reset_error_state()
    yield
    reset_error_state()


def test_exit_code_values_are_unique() -> None:
    """码值互不重复（同一数字不得承载两种语义）。"""
    values = [int(code) for code in ExitCode]
    assert len(values) == len(set(values))
    assert int(ExitCode.SUCCESS) == 0
    assert int(ExitCode.FAILURE) == 1
    assert int(ExitCode.USAGE) == 2
    assert int(ExitCode.INVALID_INPUT) == 3
    assert int(ExitCode.NOT_FOUND) == 4
    assert int(ExitCode.CHECK_FAILED) == 5
    assert int(ExitCode.INTERRUPTED) == 130


def test_kind_to_code_is_bijective() -> None:
    """``kind`` → 码值一一对应（6 个 kind，6 个不同码）。"""
    codes = [int(code) for code in ERROR_KINDS.values()]
    assert len(codes) == len(set(codes))
    assert set(ERROR_KINDS) == {"runtime", "usage", "input", "not_found", "check", "interrupted"}


def test_every_error_code_maps_to_known_kind() -> None:
    """每个登记的错误码都指向已定义的 kind。"""
    assert all(kind in ERROR_KINDS for kind in ERROR_CODE_KINDS.values())


def test_kind_for_known_and_unknown() -> None:
    """已登记码按表映射；未登记码回退 input（不静默成功）。"""
    assert kind_for("FILE_NOT_FOUND") == "not_found"
    assert kind_for("UNKNOWN_TOOL") == "usage"
    assert kind_for("INVALID_REGEX") == "input"
    assert kind_for("RUNTIME_ERROR") == "runtime"
    assert kind_for("CHECK_FAILED") == "check"
    assert kind_for("INTERRUPTED") == "interrupted"
    assert kind_for("SOMETHING_NEW") == "input"


def test_exit_code_for_uses_kind_or_default() -> None:
    """``exit_code_for`` 支持显式 kind 覆盖，未知 kind 回退 3。"""
    assert exit_code_for("FILE_NOT_FOUND") == ExitCode.NOT_FOUND
    assert exit_code_for("WHATEVER") == ExitCode.INVALID_INPUT
    assert exit_code_for("WHATEVER", kind="runtime") == ExitCode.FAILURE
    assert exit_code_for("WHATEVER", kind="made-up") == ExitCode.INVALID_INPUT


def test_emit_error_returns_code_and_matches_body(capsys: pytest.CaptureFixture[str]) -> None:
    """机读错误体：返回码 == 进程码 == body.exit_code，kind 由码推导。"""
    code = emit_error(
        "hashfile",
        "f",
        code="FILE_NOT_FOUND",
        message="文件不存在",
        field="path",
        hint="确认路径",
    )
    payload = json.loads(capsys.readouterr().err)
    assert code == int(ExitCode.NOT_FOUND)
    assert payload["exit_code"] == code
    assert payload["kind"] == "not_found"
    assert payload["ok"] is False


def test_print_error_registers_same_code() -> None:
    """人读路径：``print_error`` 与机读走同一张表，退出码相同。"""
    print_error("文件不存在", code="FILE_NOT_FOUND", hint="确认路径")
    assert pending_exit_code() == int(ExitCode.NOT_FOUND)
    assert has_pending_error()
    assert take_pending_exit_code() == int(ExitCode.NOT_FOUND)
    assert not has_pending_error()


def test_first_error_wins() -> None:
    """一次调用只保留根因（第一个错误），后续错误不覆盖。"""
    record_error("INVALID_ARGUMENT")
    record_error("RUNTIME_ERROR")
    assert take_pending_exit_code() == int(ExitCode.INVALID_INPUT)


def test_report_error_human_path(capsys: pytest.CaptureFixture[str]) -> None:
    """``report_error`` 人读路径输出错误 + 提示，并返回退出码。"""
    code = report_error(
        "textdiff",
        "file",
        code="FILE_NOT_FOUND",
        message="文件不存在: a.txt",
        hint="确认路径存在",
        json_output=False,
    )
    captured = capsys.readouterr()
    assert code == int(ExitCode.NOT_FOUND)
    assert "错误: 文件不存在: a.txt" in captured.out
    assert "提示: 确认路径存在" in captured.out
    assert pending_exit_code() == code


def test_report_error_json_path(capsys: pytest.CaptureFixture[str]) -> None:
    """``report_error`` 机读路径写 stderr 错误体，返回同一码。"""
    code = report_error(
        "textdiff",
        "file",
        code="NOT_TEXT",
        message="不是文本文件",
        hint="换文本文件",
        json_output=True,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert captured.out == ""
    assert code == int(ExitCode.INVALID_INPUT)
    assert payload["code"] == "NOT_TEXT"
    assert payload["exit_code"] == code


def test_reset_error_state_clears_both_paths() -> None:
    """``reset_error_state`` 后不再有残留错误（跨工具调用不串味）。"""
    record_error("RUNTIME_ERROR")
    assert has_pending_error()
    reset_error_state()
    assert not has_pending_error()
    assert take_pending_exit_code() is None
