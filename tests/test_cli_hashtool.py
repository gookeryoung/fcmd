"""hashtool 工具测试（DSL 内建动作声明 commands/hashtool.toml）。

验证 ``fcmd hashtool`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：__dsl_action__ 标记、无 cmd（fn 任务形态）
- 动作实现逻辑（四算法 hashlib 计算，已知向量验证）
- CLI 子命令端到端（run_tool 退出码 + capsys 捕获输出）
"""

from __future__ import annotations

import hashlib

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令

# 已知哈希向量（用 hashlib 计算确保一致）
_EMPTY_MD5 = hashlib.md5(b"").hexdigest()
_HELLO_MD5 = hashlib.md5(b"hello").hexdigest()
_HELLO_SHA1 = hashlib.sha1(b"hello").hexdigest()
_HELLO_SHA256 = hashlib.sha256(b"hello").hexdigest()
_HELLO_SHA512 = hashlib.sha512(b"hello").hexdigest()


def _run_and_capture(name: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """运行工具并返回 (退出码, 输出)。"""
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
class TestHashtoolRegistration:
    """hashtool 经内置 DSL（action 原语）注册。"""

    def test_subcommands(self) -> None:
        """hashtool 有 md5/sha1/sha256/sha512 四个子命令。"""
        assert set(_TOOL_REGISTRY["hashtool"]) == {"md5", "sha1", "sha256", "sha512"}

    @pytest.mark.parametrize(
        "sub, action_name",
        [
            ("md5", "hashtool_md5"),
            ("sha1", "hashtool_sha1"),
            ("sha256", "hashtool_sha256"),
            ("sha512", "hashtool_sha512"),
        ],
    )
    def test_action_contract(self, sub: str, action_name: str) -> None:
        """子命令携带 __dsl_action__ 标记，cmd 为 None（fn 任务形态）。"""
        spec: ToolSpec = _TOOL_REGISTRY["hashtool"][sub]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == action_name


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
