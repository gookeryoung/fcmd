"""idtool 工具测试（DSL 内建动作声明 commands/idtool.toml）。

验证 ``fcmd idtool`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：__dsl_action__ 标记、无 cmd（fn 任务形态）
- uuid/timestamp/random 三子命令执行语义与错误分支
"""

from __future__ import annotations

import re
import time
import uuid

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _run_and_capture(name: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = run_tool(name, argv)
    raw = capsys.readouterr().out
    # 过滤掉控制台帧（以 > 或 OK/FAILED 开头的行），保留动作 print 输出
    lines = [
        ln
        for ln in raw.splitlines()
        if not ln.startswith("> ") and not ln.startswith("OK ") and not ln.startswith("FAILED ")
    ]
    return code, "\n".join(lines)


# ====================================================================== #
# 注册与声明契约
# ====================================================================== #
class TestIdtoolRegistration:
    """idtool 经内置 DSL（action 原语）注册。"""

    def test_subcommands(self) -> None:
        assert set(_TOOL_REGISTRY["idtool"]) == {"uuid", "timestamp", "random"}

    @pytest.mark.parametrize(
        "sub, action", [("uuid", "idtool_uuid"), ("timestamp", "idtool_timestamp"), ("random", "idtool_random")]
    )
    def test_action_contract(self, sub: str, action: str) -> None:
        spec: ToolSpec = _TOOL_REGISTRY["idtool"][sub]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == action


# ====================================================================== #
# uuid 子命令
# ====================================================================== #
class TestIdtoolUuid:
    def test_v4_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("idtool", ["uuid"], capsys)
        assert code == 0
        assert UUID_RE.match(out.strip())
        assert uuid.UUID(out.strip(), version=4)

    def test_v1(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("idtool", ["uuid", "--version", "1"], capsys)
        assert code == 0
        assert UUID_RE.match(out.strip())
        assert uuid.UUID(out.strip(), version=1)

    def test_invalid_version_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("idtool", ["uuid", "--version", "99"], capsys)
        assert code == 0
        assert "不支持的 UUID 版本" in out


# ====================================================================== #
# timestamp 子命令
# ====================================================================== #
class TestIdtoolTimestamp:
    def test_iso_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        from datetime import datetime

        before = time.time()
        code, out = _run_and_capture("idtool", ["timestamp"], capsys)
        assert code == 0
        after = time.time()
        iso_str = out.strip()
        assert "T" in iso_str
        dt = datetime.fromisoformat(iso_str)
        assert before - 1 <= dt.timestamp() <= after + 1

    def test_unix(self, capsys: pytest.CaptureFixture[str]) -> None:
        now = int(time.time())
        code, out = _run_and_capture("idtool", ["timestamp", "--fmt", "unix"], capsys)
        assert code == 0
        ts = int(out.strip())
        assert now - 1 <= ts <= now + 1

    def test_invalid_fmt(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("idtool", ["timestamp", "--fmt", "bogus"], capsys)
        assert code == 0
        assert "不支持的格式" in out


# ====================================================================== #
# random 子命令
# ====================================================================== #
class TestIdtoolRandom:
    def test_default_length_16(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("idtool", ["random"], capsys)
        assert code == 0
        text = out.strip()
        assert len(text) == 16
        assert text.isalnum()

    def test_custom_length(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("idtool", ["random", "--length", "32"], capsys)
        assert code == 0
        assert len(out.strip()) == 32

    def test_zero_length_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("idtool", ["random", "--length", "0"], capsys)
        assert code == 0
        assert "length 必须大于 0" in out

    def test_negative_length_shows_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("idtool", ["random", "--length", "-5"], capsys)
        assert code == 0
        assert "length 必须大于 0" in out

    def test_two_runs_differ(self, capsys: pytest.CaptureFixture[str]) -> None:
        """两次随机输出不应相同。"""
        _, a = _run_and_capture("idtool", ["random"], capsys)
        _, b = _run_and_capture("idtool", ["random"], capsys)
        assert a.strip() != b.strip()
