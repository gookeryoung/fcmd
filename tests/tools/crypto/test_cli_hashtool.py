"""hashtool 工具测试。

验证 ``fcmd.tools.crypto.hashtool`` 工具与 ``fcmd.infras.hashing`` 实现：
- 工具注册（md5/sha1/sha256/sha512 四子命令）
- 四算法 hashlib 已知向量
- CLI 子命令端到端（run_tool 退出码 + capsys 捕获输出）
"""

from __future__ import annotations

import hashlib

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools
from tests.helpers import run_and_capture as _run_and_capture

# 已知哈希向量（用 hashlib 计算确保一致）
_EMPTY_MD5 = hashlib.md5(b"").hexdigest()
_HELLO_MD5 = hashlib.md5(b"hello").hexdigest()
_HELLO_SHA1 = hashlib.sha1(b"hello").hexdigest()
_HELLO_SHA256 = hashlib.sha256(b"hello").hexdigest()
_HELLO_SHA512 = hashlib.sha512(b"hello").hexdigest()


# ====================================================================== #
# 注册
# ====================================================================== #
class TestHashtoolRegistration:
    """hashtool 注册与子命令结构。"""

    def test_registered(self) -> None:
        """hashtool 已注册到工具表。"""
        assert "hashtool" in list_tools()

    def test_subcommands(self) -> None:
        """hashtool 有 md5/sha1/sha256/sha512 四个子命令。"""
        assert set(list_subcommands("hashtool")) == {"md5", "sha1", "sha256", "sha512"}


# ====================================================================== #
# 动作逻辑 —— 已知向量验证
# ====================================================================== #
class TestActionKnownVectors:
    """四算法输出与 hashlib 原生计算一致。"""

    def test_md5_known(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("hashtool", ["md5", "hello"], capsys)
        assert code == 0
        assert out.strip() == _HELLO_MD5

    def test_md5_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("hashtool", ["md5", ""], capsys)
        assert code == 0
        assert out.strip() == _EMPTY_MD5

    def test_sha1_known(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("hashtool", ["sha1", "hello"], capsys)
        assert code == 0
        assert out.strip() == _HELLO_SHA1

    def test_sha256_known(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("hashtool", ["sha256", "hello"], capsys)
        assert code == 0
        assert out.strip() == _HELLO_SHA256

    def test_sha512_known(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("hashtool", ["sha512", "hello"], capsys)
        assert code == 0
        assert out.strip() == _HELLO_SHA512

    def test_all_algorithms_differ(self, capsys: pytest.CaptureFixture[str]) -> None:
        """四算法对同一输入输出均不同。"""
        outputs: list[str] = []
        for sub in ("md5", "sha1", "sha256", "sha512"):
            _, out = _run_and_capture("hashtool", [sub, "test"], capsys)
            outputs.append(out.strip())
        assert len(set(outputs)) == 4

    def test_output_lowercase_hex(self, capsys: pytest.CaptureFixture[str]) -> None:
        """所有算法输出均为小写十六进制。"""
        for sub in ("md5", "sha1", "sha256", "sha512"):
            _, out = _run_and_capture("hashtool", [sub, "ABC"], capsys)
            hex_str = out.strip()
            assert hex_str == hex_str.lower()
            int(hex_str, 16)  # 全部是合法 hex
