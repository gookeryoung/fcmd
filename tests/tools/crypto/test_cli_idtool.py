"""idtool 工具测试。

验证 ``fcmd.tools.crypto.idtool`` 工具与 ``fcmd.infras.ids`` 实现：
- 工具注册（uuid/timestamp/random 三子命令）
- 三子命令执行语义与错误分支
"""

from __future__ import annotations

import re
import time
import uuid

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools
from tests.helpers import run_and_capture as _run_and_capture

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ====================================================================== #
# 注册
# ====================================================================== #
class TestIdtoolRegistration:
    """idtool 注册与子命令结构。"""

    def test_registered(self) -> None:
        assert "idtool" in list_tools()

    def test_subcommands(self) -> None:
        assert set(list_subcommands("idtool")) == {"uuid", "timestamp", "random"}


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
