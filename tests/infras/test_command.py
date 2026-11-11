"""命令执行基础件测试：CommandResult / run_command。"""

from __future__ import annotations

import subprocess

import pytest

from fcmd.infras.command import CommandResult, run_command


# ============================================================================ #
# CommandResult 测试
# ============================================================================ #
class TestCommandResult:
    """CommandResult 值对象测试。"""

    def test_construct(self) -> None:
        """构造 CommandResult。"""
        result = CommandResult(cmd=["echo", "hi"], returncode=0, stdout="hi\n", stderr="")
        assert result.cmd == ["echo", "hi"]
        assert result.returncode == 0
        assert result.stdout == "hi\n"
        assert result.stderr == ""

    def test_succeeded(self) -> None:
        """返回码 0 时 succeeded 为 True。"""
        result = CommandResult(cmd=[], returncode=0, stdout="", stderr="")
        assert result.succeeded
        assert not result.failed

    def test_failed(self) -> None:
        """返回码非 0 时 failed 为 True。"""
        result = CommandResult(cmd=[], returncode=1, stdout="", stderr="")
        assert result.failed
        assert not result.succeeded

    def test_frozen(self) -> None:
        """frozen=True 不可修改。"""
        result = CommandResult(cmd=[], returncode=0, stdout="", stderr="")
        with pytest.raises(AttributeError):
            result.returncode = 1  # type: ignore[misc]


# ============================================================================ #
# run_command 测试
# ============================================================================ #
class TestRunCommand:
    """run_command 函数测试。"""

    def test_capture_true(self) -> None:
        """capture=True 捕获输出。"""
        result = run_command(["python", "-c", "print('hello')"], capture=True)
        assert result.succeeded
        assert "hello" in result.stdout

    def test_capture_false(self) -> None:
        """capture=False 时 stdout 为空字符串（输出透传到终端，不在 result 中）。"""
        result = run_command(["python", "-c", "print('world')"])
        assert result.succeeded
        # capture=False 时 stdout 为空（输出直接到终端文件描述符）
        assert result.stdout == ""

    def test_failed_command(self) -> None:
        """命令失败时 returncode 非零，不抛异常。"""
        # 用一个不存在的命令
        result = run_command(["python", "-c", "import sys; sys.exit(1)"], capture=True)
        assert result.failed
        assert result.returncode == 1

    def test_check_true_raises(self) -> None:
        """check=True 时失败抛 CalledProcessError。"""
        with pytest.raises(subprocess.CalledProcessError):
            run_command(["python", "-c", "import sys; sys.exit(1)"], check=True)

    def test_stderr_captured(self) -> None:
        """capture=True 时 stderr 被捕获。"""
        result = run_command(
            ["python", "-c", "import sys; sys.stderr.write('err')"],
            capture=True,
        )
        assert "err" in result.stderr
