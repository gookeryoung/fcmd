"""randtool 工具测试。

验证 ``fcmd.cli.randtool`` 模块：
- 工具注册与四子命令结构（password/number/string/bytes）
- ``generate_password``/``generate_number``/``generate_string``/``generate_bytes``
- CLI 子命令端到端
"""

from __future__ import annotations

import base64
import re
import string

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.randtool import (
    generate_bytes,
    generate_number,
    generate_password,
    generate_string,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """randtool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "randtool" in list_tools()

    def test_subcommands(self) -> None:
        """randtool 有 password/number/string/bytes 四个子命令。"""
        subs = list_subcommands("randtool")
        assert set(subs) == {"password", "number", "string", "bytes"}


# ============================================================================ #
# generate_password
# ============================================================================ #
class TestGeneratePassword:
    """generate_password 测试。"""

    def test_default_length(self) -> None:
        """默认长度 16。"""
        pwd = generate_password()
        assert len(pwd) == 16

    def test_custom_length(self) -> None:
        """自定义长度。"""
        pwd = generate_password(length=24)
        assert len(pwd) == 24

    def test_contains_required_classes(self) -> None:
        """默认含大小写字母、数字、符号。"""
        pwd = generate_password()
        assert any(c in string.ascii_lowercase for c in pwd)
        assert any(c in string.ascii_uppercase for c in pwd)
        assert any(c in string.digits for c in pwd)
        assert any(c in "!@#$%^&*()-_=+[]{}<>?" for c in pwd)

    def test_no_symbols(self) -> None:
        """--no-symbols 不含符号。"""
        pwd = generate_password(symbols=False)
        symbol_set = set("!@#$%^&*()-_=+[]{}<>?")
        assert not any(c in symbol_set for c in pwd)
        # 仍含字母和数字
        assert any(c in string.ascii_letters for c in pwd)
        assert any(c in string.digits for c in pwd)

    def test_too_short_raises(self) -> None:
        """长度小于 4 抛 ValueError。"""
        with pytest.raises(ValueError, match="length 必须大于等于 4"):
            generate_password(length=3)

    def test_min_length_4(self) -> None:
        """长度 4（最小有效值）。"""
        pwd = generate_password(length=4)
        assert len(pwd) == 4

    def test_randomness(self) -> None:
        """两次调用结果不同（极大概率）。"""
        pwd1 = generate_password(length=32)
        pwd2 = generate_password(length=32)
        assert pwd1 != pwd2


# ============================================================================ #
# generate_number
# ============================================================================ #
class TestGenerateNumber:
    """generate_number 测试。"""

    def test_in_range(self) -> None:
        """结果在 [min, max] 闭区间内。"""
        for _ in range(100):
            n = generate_number(1, 100)
            assert 1 <= n <= 100

    def test_single_value_range(self) -> None:
        """min == max 时返回该值。"""
        assert generate_number(5, 5) == 5

    def test_negative_range(self) -> None:
        """负数范围。"""
        n = generate_number(-10, -1)
        assert -10 <= n <= -1

    def test_invalid_range_raises(self) -> None:
        """min > max 抛 ValueError。"""
        with pytest.raises(ValueError, match="不能大于"):
            generate_number(10, 1)

    def test_distribution_covers_both_ends(self) -> None:
        """大样本下 1 和 100 都应被命中（覆盖闭区间端点）。"""
        results = {generate_number(1, 100) for _ in range(1000)}
        assert 1 in results
        assert 100 in results


# ============================================================================ #
# generate_string
# ============================================================================ #
class TestGenerateString:
    """generate_string 测试。"""

    def test_default_length(self) -> None:
        """默认长度 16。"""
        s = generate_string()
        assert len(s) == 16

    def test_custom_length(self) -> None:
        """自定义长度。"""
        s = generate_string(length=32)
        assert len(s) == 32

    def test_default_charset(self) -> None:
        """默认字符集为字母+数字。"""
        s = generate_string(length=100)
        allowed = set(string.ascii_letters + string.digits)
        assert all(c in allowed for c in s)

    def test_custom_charset(self) -> None:
        """自定义字符集。"""
        s = generate_string(length=50, chars="AB")
        assert all(c in "AB" for c in s)

    def test_single_char_charset(self) -> None:
        """单字符字符集返回重复字符。"""
        s = generate_string(length=10, chars="X")
        assert s == "X" * 10

    def test_zero_length_raises(self) -> None:
        """长度 0 抛 ValueError。"""
        with pytest.raises(ValueError, match="length 必须大于 0"):
            generate_string(length=0)

    def test_negative_length_raises(self) -> None:
        """负长度抛 ValueError。"""
        with pytest.raises(ValueError, match="length 必须大于 0"):
            generate_string(length=-5)

    def test_randomness(self) -> None:
        """两次调用结果不同（极大概率）。"""
        s1 = generate_string(length=32)
        s2 = generate_string(length=32)
        assert s1 != s2


# ============================================================================ #
# generate_bytes
# ============================================================================ #
class TestGenerateBytes:
    """generate_bytes 测试。"""

    def test_hex_default(self) -> None:
        """默认 hex 编码。"""
        result = generate_bytes(16)
        # 16 字节 hex = 32 字符
        assert len(result) == 32
        # 全部为 hex 字符
        assert re.fullmatch(r"[0-9a-f]+", result)

    def test_base64_encoding(self) -> None:
        """base64 编码。"""
        result = generate_bytes(16, encoding="base64")
        # base64 解码后应为 16 字节
        decoded = base64.b64decode(result)
        assert len(decoded) == 16

    def test_custom_length(self) -> None:
        """自定义字节长度。"""
        result = generate_bytes(32, encoding="hex")
        assert len(result) == 64  # 32 字节 = 64 hex 字符

    def test_zero_length_raises(self) -> None:
        """长度 0 抛 ValueError。"""
        with pytest.raises(ValueError, match="length 必须大于 0"):
            generate_bytes(0)

    def test_invalid_encoding_raises(self) -> None:
        """不支持的编码抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的编码"):
            generate_bytes(16, encoding="utf8")

    def test_randomness(self) -> None:
        """两次调用结果不同（极大概率）。"""
        b1 = generate_bytes(32)
        b2 = generate_bytes(32)
        assert b1 != b2


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestRandtoolCLI:
    """``randtool`` 通过 ``run_tool`` 调用测试。"""

    def test_password_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd randtool password 生成默认密码。"""
        code = run_tool("randtool", ["password"])
        assert code == 0
        out = capsys.readouterr().out
        # 提取密码行（框架含前缀/后缀）
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        assert any(len(line) == 16 for line in lines)

    def test_password_custom_length(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--length 24。"""
        code = run_tool("randtool", ["password", "--length", "24"])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        assert any(len(line) == 24 for line in lines)

    def test_password_no_symbols(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--no-symbols。"""
        code = run_tool("randtool", ["password", "--no-symbols"])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        symbol_set = set("!@#$%^&*()-_=+[]{}<>?")
        # 至少有一行不含符号
        assert any(not any(c in symbol_set for c in line) for line in lines)

    def test_password_too_short(self, capsys: pytest.CaptureFixture[str]) -> None:
        """长度过短打印错误。"""
        code = run_tool("randtool", ["password", "--length", "2"])
        assert code == 0
        out = capsys.readouterr().out
        assert "length 必须大于等于 4" in out

    def test_number_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd randtool number 1 100。"""
        code = run_tool("randtool", ["number", "1", "100"])
        assert code == 0
        out = capsys.readouterr().out
        # 提取数字行
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        assert any(line.lstrip("-").isdigit() and 1 <= int(line) <= 100 for line in lines)

    def test_number_single_value(self, capsys: pytest.CaptureFixture[str]) -> None:
        """number 5 5 返回 5。"""
        code = run_tool("randtool", ["number", "5", "5"])
        assert code == 0
        out = capsys.readouterr().out
        assert "5" in out

    def test_number_invalid_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        """number 10 1 打印错误。"""
        code = run_tool("randtool", ["number", "10", "1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不能大于" in out

    def test_string_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd randtool string 默认。"""
        code = run_tool("randtool", ["string"])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        assert any(len(line) == 16 for line in lines)

    def test_string_custom_chars(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--chars AB 限定字符集。"""
        code = run_tool("randtool", ["string", "--length", "20", "--chars", "AB"])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        # 至少有一行全部由 A/B 组成且长度 20
        assert any(len(line) == 20 and all(c in "AB" for c in line) for line in lines)

    def test_string_zero_length(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--length 0 打印错误。"""
        code = run_tool("randtool", ["string", "--length", "0"])
        assert code == 0
        out = capsys.readouterr().out
        assert "length 必须大于 0" in out

    def test_bytes_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd randtool bytes 16 默认 hex。"""
        code = run_tool("randtool", ["bytes", "16"])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        # 16 字节 hex = 32 字符
        assert any(len(line) == 32 and re.fullmatch(r"[0-9a-f]+", line) for line in lines)

    def test_bytes_base64(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--encoding base64。"""
        code = run_tool("randtool", ["bytes", "16", "--encoding", "base64"])
        assert code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line and not line.startswith(">") and not line.startswith("OK")]
        # 至少有一行可被 base64 解码
        assert any(self._is_valid_base64(line) for line in lines)

    @staticmethod
    def _is_valid_base64(s: str) -> bool:
        try:
            base64.b64decode(s, validate=True)
            return True
        except Exception:
            return False

    def test_bytes_invalid_encoding(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--encoding utf8 打印错误。"""
        code = run_tool("randtool", ["bytes", "16", "--encoding", "utf8"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不支持的编码" in out
