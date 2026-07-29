"""hashtool 工具测试。

验证 ``fcmd.cli.hashtool`` 模块：
- 工具注册与四子命令结构（md5/sha1/sha256/sha512）
- ``hash_md5``/``hash_sha1``/``hash_sha256``/``hash_sha512``
- 已知向量验证与往返一致
- CLI 子命令端到端
"""

from __future__ import annotations

import hashlib

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.hashtool import (
    hash_md5,
    hash_sha1,
    hash_sha256,
    hash_sha512,
    list_algorithms,
)

# 已知哈希向量（用 hashlib 计算确保一致）
_EMPTY_MD5 = hashlib.md5(b"").hexdigest()
_HELLO_MD5 = hashlib.md5(b"hello").hexdigest()
_HELLO_SHA1 = hashlib.sha1(b"hello").hexdigest()
_HELLO_SHA256 = hashlib.sha256(b"hello").hexdigest()
_HELLO_SHA512 = hashlib.sha512(b"hello").hexdigest()


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """hashtool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "hashtool" in list_tools()

    def test_subcommands(self) -> None:
        """hashtool 有 md5/sha1/sha256/sha512 四个子命令。"""
        subs = list_subcommands("hashtool")
        assert set(subs) == {"md5", "sha1", "sha256", "sha512"}

    def test_list_algorithms(self) -> None:
        """list_algorithms 返回支持算法列表。"""
        algos = list_algorithms()
        assert set(algos) == {"md5", "sha1", "sha256", "sha512"}


# ============================================================================ #
# hash_md5
# ============================================================================ #
class TestHashMd5:
    """hash_md5 测试。"""

    def test_known_value(self) -> None:
        """已知值验证。"""
        assert hash_md5("hello") == _HELLO_MD5

    def test_empty_string(self) -> None:
        """空字符串。"""
        assert hash_md5("") == _EMPTY_MD5

    def test_length_32(self) -> None:
        """MD5 摘要长度 32。"""
        assert len(hash_md5("test")) == 32

    def test_lowercase(self) -> None:
        """输出为小写。"""
        result = hash_md5("test")
        assert result == result.lower()

    def test_unicode(self) -> None:
        """Unicode 字符串。"""
        # 中文 UTF-8 编码后的 MD5
        assert hash_md5("中") == hashlib.md5("中".encode("utf-8")).hexdigest()

    def test_deterministic(self) -> None:
        """相同输入相同输出。"""
        assert hash_md5("hello") == hash_md5("hello")


# ============================================================================ #
# hash_sha1
# ============================================================================ #
class TestHashSha1:
    """hash_sha1 测试。"""

    def test_known_value(self) -> None:
        """已知值验证。"""
        assert hash_sha1("hello") == _HELLO_SHA1

    def test_length_40(self) -> None:
        """SHA1 摘要长度 40。"""
        assert len(hash_sha1("test")) == 40

    def test_lowercase(self) -> None:
        """输出为小写。"""
        result = hash_sha1("test")
        assert result == result.lower()


# ============================================================================ #
# hash_sha256
# ============================================================================ #
class TestHashSha256:
    """hash_sha256 测试。"""

    def test_known_value(self) -> None:
        """已知值验证。"""
        assert hash_sha256("hello") == _HELLO_SHA256

    def test_length_64(self) -> None:
        """SHA256 摘要长度 64。"""
        assert len(hash_sha256("test")) == 64

    def test_lowercase(self) -> None:
        """输出为小写。"""
        result = hash_sha256("test")
        assert result == result.lower()


# ============================================================================ #
# hash_sha512
# ============================================================================ #
class TestHashSha512:
    """hash_sha512 测试。"""

    def test_known_value(self) -> None:
        """已知值验证。"""
        assert hash_sha512("hello") == _HELLO_SHA512

    def test_length_128(self) -> None:
        """SHA512 摘要长度 128。"""
        assert len(hash_sha512("test")) == 128

    def test_lowercase(self) -> None:
        """输出为小写。"""
        result = hash_sha512("test")
        assert result == result.lower()


# ============================================================================ #
# 不同算法输出不同
# ============================================================================ #
class TestAlgorithmDifference:
    """不同算法输出不同验证。"""

    def test_different_algorithms_different_output(self) -> None:
        """同一输入不同算法输出不同。"""
        text = "hello"
        results = {hash_md5(text), hash_sha1(text), hash_sha256(text), hash_sha512(text)}
        assert len(results) == 4

    def test_different_input_different_output(self) -> None:
        """不同输入同算法输出不同。"""
        assert hash_md5("a") != hash_md5("b")


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestHashtoolCLI:
    """``hashtool`` 通过 ``run_tool`` 调用测试。"""

    def test_md5(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashtool md5 hello。"""
        code = run_tool("hashtool", ["md5", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert _HELLO_MD5 in out

    def test_sha1(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashtool sha1 hello。"""
        code = run_tool("hashtool", ["sha1", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert _HELLO_SHA1 in out

    def test_sha256(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashtool sha256 hello。"""
        code = run_tool("hashtool", ["sha256", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert _HELLO_SHA256 in out

    def test_sha512(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashtool sha512 hello。"""
        code = run_tool("hashtool", ["sha512", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert _HELLO_SHA512 in out

    def test_md5_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashtool md5 ''（空字符串）。"""
        code = run_tool("hashtool", ["md5", ""])
        assert code == 0
        out = capsys.readouterr().out
        assert _EMPTY_MD5 in out

    def test_md5_unicode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashtool md5 中文。"""
        code = run_tool("hashtool", ["md5", "中文"])
        assert code == 0
        out = capsys.readouterr().out
        expected = hashlib.md5("中文".encode("utf-8")).hexdigest()
        assert expected in out
