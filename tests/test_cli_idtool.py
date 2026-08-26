"""idtool 工具测试。

验证 ``fcmd.cli.crypto.idtool`` 模块：
- 工具注册与子命令结构
- generate_uuid（v1/v4/非法版本）
- generate_timestamp（iso/unix/非法格式）
- generate_random_string（默认/自定义长度/非法长度/字符集）
- 通过 run_tool 调用 uuid/timestamp/random 子命令
"""

from __future__ import annotations

import string
import time
import uuid as uuid_mod
from datetime import datetime

import pytest

import fcmd as fx
import fcmd.cli.crypto.idtool
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.crypto.idtool import generate_random_string, generate_timestamp, generate_uuid

# UUID 正则：8-4-4-4-12 共 36 字符
_UUID_LENGTH = 36
_RANDOM_CHARS: str = string.ascii_letters + string.digits


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """idtool 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """idtool 应在 _TOOL_REGISTRY 中注册。"""
        assert "idtool" in _TOOL_REGISTRY, "工具 'idtool' 未注册"

    def test_idtool_subcommands(self) -> None:
        """idtool 应有 uuid / timestamp / random 子命令。"""
        subs = fx.list_subcommands("idtool")
        assert "uuid" in subs
        assert "timestamp" in subs
        assert "random" in subs


# ---------------------------------------------------------------------- #
# generate_uuid
# ---------------------------------------------------------------------- #
class TestGenerateUuid:
    """``generate_uuid`` 测试。"""

    def test_v4_format(self) -> None:
        """v4 UUID 格式正确。"""
        result = generate_uuid(4)
        assert len(result) == _UUID_LENGTH
        assert result.count("-") == 4
        assert result[14] == "4"  # 版本位

    def test_v1_format(self) -> None:
        """v1 UUID 格式正确。"""
        result = generate_uuid(1)
        assert len(result) == _UUID_LENGTH
        assert result.count("-") == 4
        assert result[14] == "1"  # 版本位

    def test_default_v4(self) -> None:
        """默认版本为 v4。"""
        result = generate_uuid()
        assert result[14] == "4"

    def test_v4_uniqueness(self) -> None:
        """两次生成 v4 UUID 应不同。"""
        a = generate_uuid(4)
        b = generate_uuid(4)
        assert a != b

    def test_valid_uuid_object(self) -> None:
        """生成的字符串可被 uuid.UUID 解析。"""
        result = generate_uuid(4)
        parsed = uuid_mod.UUID(result)
        assert str(parsed) == result.lower()

    def test_invalid_version_raises(self) -> None:
        """非法版本应抛出 ValueError。"""
        with pytest.raises(ValueError, match="不支持的 UUID 版本"):
            generate_uuid(2)


# ---------------------------------------------------------------------- #
# generate_timestamp
# ---------------------------------------------------------------------- #
class TestGenerateTimestamp:
    """``generate_timestamp`` 测试。"""

    def test_iso_format(self) -> None:
        """ISO 格式时间戳。"""
        result = generate_timestamp("iso")
        # 可被 datetime.fromisoformat 解析
        dt = datetime.fromisoformat(result)
        assert isinstance(dt, datetime)

    def test_unix_format(self) -> None:
        """Unix 格式时间戳。"""
        result = generate_timestamp("unix")
        ts = int(result)
        # 与当前时间差不超过 2 秒
        assert abs(ts - int(time.time())) < 2

    def test_default_iso(self) -> None:
        """默认格式为 iso。"""
        result = generate_timestamp()
        datetime.fromisoformat(result)  # 不抛异常即说明是 ISO 格式

    def test_iso_contains_t(self) -> None:
        """ISO 格式包含 'T' 分隔符。"""
        result = generate_timestamp("iso")
        assert "T" in result

    def test_invalid_format_raises(self) -> None:
        """非法格式应抛出 ValueError。"""
        with pytest.raises(ValueError, match="不支持的格式"):
            generate_timestamp("invalid")


# ---------------------------------------------------------------------- #
# generate_random_string
# ---------------------------------------------------------------------- #
class TestGenerateRandomString:
    """``generate_random_string`` 测试。"""

    def test_default_length(self) -> None:
        """默认长度 16。"""
        assert len(generate_random_string()) == 16

    def test_custom_length(self) -> None:
        """自定义长度。"""
        assert len(generate_random_string(32)) == 32
        assert len(generate_random_string(1)) == 1

    def test_char_set(self) -> None:
        """字符在字母+数字范围内。"""
        result = generate_random_string(100)
        for ch in result:
            assert ch in _RANDOM_CHARS

    def test_uniqueness(self) -> None:
        """两次生成结果不同。"""
        a = generate_random_string(16)
        b = generate_random_string(16)
        assert a != b

    def test_zero_length_raises(self) -> None:
        """长度 0 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="必须大于 0"):
            generate_random_string(0)

    def test_negative_length_raises(self) -> None:
        """负长度应抛出 ValueError。"""
        with pytest.raises(ValueError, match="必须大于 0"):
            generate_random_string(-5)


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
class TestIdtoolCLI:
    """``idtool`` 通过 ``run_tool`` 调用测试。"""

    def test_uuid_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd idtool uuid 生成 UUID v4。"""
        code = run_tool("idtool", ["uuid"])
        assert code == 0
        out = capsys.readouterr().out
        # 输出中应包含 36 字符的 UUID
        assert len(out.strip()) >= _UUID_LENGTH

    def test_uuid_v1_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd idtool uuid --version 1 生成 UUID v1。"""
        code = run_tool("idtool", ["uuid", "--version", "1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "1" in out  # 版本位为 1

    def test_uuid_invalid_version_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """非法版本应打印错误。"""
        code = run_tool("idtool", ["uuid", "--version", "3"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不支持的 UUID 版本" in out

    def test_timestamp_iso_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd idtool timestamp 生成 ISO 时间戳。"""
        code = run_tool("idtool", ["timestamp"])
        assert code == 0
        out = capsys.readouterr().out
        assert "T" in out  # ISO 格式包含 'T'

    def test_timestamp_unix_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd idtool timestamp --fmt unix 生成 Unix 时间戳。"""
        code = run_tool("idtool", ["timestamp", "--fmt", "unix"])
        assert code == 0
        out = capsys.readouterr().out
        # 框架包裹输出，实际时间戳是纯数字行
        assert any(line.strip().isdigit() and len(line.strip()) >= 9 for line in out.split("\n"))

    def test_timestamp_invalid_fmt_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """非法格式应打印错误。"""
        code = run_tool("idtool", ["timestamp", "--fmt", "invalid"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不支持的格式" in out

    def test_random_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd idtool random 生成 16 位随机字符串。"""
        code = run_tool("idtool", ["random"])
        assert code == 0
        out = capsys.readouterr().out
        # 框架包裹输出，实际随机字符串为 16 位字母数字行
        assert any(len(line.strip()) == 16 and line.strip().isalnum() for line in out.split("\n"))

    def test_random_custom_length_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd idtool random --length 32 生成 32 位随机字符串。"""
        code = run_tool("idtool", ["random", "--length", "32"])
        assert code == 0
        out = capsys.readouterr().out
        # 框架包裹输出，实际随机字符串为 32 位字母数字行
        assert any(len(line.strip()) == 32 and line.strip().isalnum() for line in out.split("\n"))

    def test_random_zero_length_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """长度 0 应打印错误。"""
        code = run_tool("idtool", ["random", "--length", "0"])
        assert code == 0
        out = capsys.readouterr().out
        assert "必须大于 0" in out
