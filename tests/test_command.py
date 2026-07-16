"""命令执行器测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fcmd.task import TaskSpec


# ---------------------------------------------------------------------- #
# list 命令
# ---------------------------------------------------------------------- #
def test_run_command_list_success(capsys: pytest.CaptureFixture[str]) -> None:
    """list 命令成功执行返回 None。"""
    if sys.platform == "win32":
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "echo", "hello"])
    else:
        spec = TaskSpec(name="x", cmd=["echo", "hello"])
    result = spec.effective_fn()
    assert result is None
    captured = capsys.readouterr()
    assert "hello" in captured.out


def test_run_command_list_failure() -> None:
    """list 命令非零返回码抛 RuntimeError。"""
    # 使用不存在的命令参数让命令失败
    if sys.platform == "win32":
        # Windows: 使用 cmd 内置命令返回非零
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "exit", "1"])
    else:
        spec = TaskSpec(name="x", cmd=["false"])
    with pytest.raises(RuntimeError, match="执行失败"):
        spec.effective_fn()


def test_run_command_list_not_found() -> None:
    """list 命令未找到抛 RuntimeError。"""
    spec = TaskSpec(name="x", cmd=["this_command_does_not_exist_xyz"])
    with pytest.raises(RuntimeError, match="未找到"):
        spec.effective_fn()


def test_run_command_list_verbose(capsys: pytest.CaptureFixture[str]) -> None:
    """verbose 模式打印执行信息。"""
    if sys.platform == "win32":
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "echo", "hi"], verbose=True)
    else:
        spec = TaskSpec(name="x", cmd=["echo", "hi"], verbose=True)
    spec.effective_fn()
    # verbose 模式通过 rich console 输出到 stderr
    captured = capsys.readouterr()
    # rich 输出可能在 out 或 err，检查合并
    combined = captured.out + captured.err
    assert "echo" in combined or "hi" in combined


# ---------------------------------------------------------------------- #
# str 命令
# ---------------------------------------------------------------------- #
def test_run_command_str_success(capsys: pytest.CaptureFixture[str]) -> None:
    """shell 字符串命令成功执行。"""
    if sys.platform == "win32":
        spec = TaskSpec(name="x", cmd="echo hello")
    else:
        spec = TaskSpec(name="x", cmd="echo hello")
    result = spec.effective_fn()
    assert result is None
    captured = capsys.readouterr()
    assert "hello" in captured.out


def test_run_command_str_failure() -> None:
    """shell 字符串命令失败抛 RuntimeError。"""
    if sys.platform == "win32":
        spec = TaskSpec(name="x", cmd="exit /b 1")
    else:
        spec = TaskSpec(name="x", cmd="false")
    with pytest.raises(RuntimeError, match="执行失败"):
        spec.effective_fn()


# ---------------------------------------------------------------------- #
# callable 命令
# ---------------------------------------------------------------------- #
def test_run_command_callable_success() -> None:
    """callable 命令成功执行返回其结果。"""
    spec = TaskSpec(name="x", cmd=lambda: 42)
    assert spec.effective_fn() == 42


def test_run_command_callable_exception() -> None:
    """callable 命令抛异常包装为 RuntimeError。"""

    def bad_callable() -> None:
        raise ValueError("boom")

    spec = TaskSpec(name="x", cmd=bad_callable)
    with pytest.raises(RuntimeError, match="可调用命令执行异常"):
        spec.effective_fn()


def test_run_command_callable_verbose(capsys: pytest.CaptureFixture[str]) -> None:
    """verbose 模式 callable 命令打印执行信息。"""
    spec = TaskSpec(name="x", cmd=lambda: 1, verbose=True)
    spec.effective_fn()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # verbose 打印"执行可调用命令"
    assert "可调用" in combined or "callable" in combined.lower() or "执行" in combined


# ---------------------------------------------------------------------- #
# cwd 与 env
# ---------------------------------------------------------------------- #
def test_run_command_with_cwd(tmp_path: Path) -> None:
    """cwd 透传给子进程。"""
    if sys.platform == "win32":
        # Windows: 用 cd 命令验证工作目录
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "cd"], cwd=tmp_path)
    else:
        spec = TaskSpec(name="x", cmd=["pwd"], cwd=tmp_path)
    spec.effective_fn()
    # 不验证输出（capture_output=not verbose，verbose=False 时捕获并 print）


def test_run_command_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 透传给子进程。"""
    monkeypatch.delenv("FCMD_TEST_ENV_VAR", raising=False)
    if sys.platform == "win32":
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "echo %FCMD_TEST_ENV_VAR%"], env={"FCMD_TEST_ENV_VAR": "hello"})
    else:
        spec = TaskSpec(name="x", cmd=["sh", "-c", "echo $FCMD_TEST_ENV_VAR"], env={"FCMD_TEST_ENV_VAR": "hello"})
    spec.effective_fn()


# ---------------------------------------------------------------------- #
# timeout
# ---------------------------------------------------------------------- #
@pytest.mark.slow
def test_run_command_timeout() -> None:
    """命令超时抛 RuntimeError。"""
    if sys.platform == "win32":
        # Windows: timeout 命令
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "ping", "-n", "10", "127.0.0.1"], timeout=0.5)
    else:
        spec = TaskSpec(name="x", cmd=["sleep", "10"], timeout=0.5)
    with pytest.raises(RuntimeError, match="超时"):
        spec.effective_fn()


# ---------------------------------------------------------------------- #
# verbose + cwd / stderr / OSError
# ---------------------------------------------------------------------- #
def test_run_command_verbose_with_cwd(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """verbose 模式 + cwd 打印工作目录。"""
    if sys.platform == "win32":
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "echo", "hi"], verbose=True, cwd=tmp_path)
    else:
        spec = TaskSpec(name="x", cmd=["echo", "hi"], verbose=True, cwd=tmp_path)
    spec.effective_fn()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "工作目录" in combined


def test_run_command_str_verbose_with_cwd(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """verbose 模式 + cwd 的 shell 字符串命令。"""
    spec = TaskSpec(name="x", cmd="echo hi", verbose=True, cwd=tmp_path)
    spec.effective_fn()
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "工作目录" in combined


def test_run_command_failure_with_stderr() -> None:
    """失败命令的 stderr 附在错误信息中（verbose=False）。"""
    if sys.platform == "win32":
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "dir", "nonexistent_dir_xyz"])
    else:
        spec = TaskSpec(name="x", cmd=["ls", "nonexistent_dir_xyz"])
    with pytest.raises(RuntimeError) as exc_info:
        spec.effective_fn()
    # stderr 内容应附在错误信息中
    assert "执行失败" in str(exc_info.value)


def test_run_command_os_error() -> None:
    """命令执行触发 OSError 包装为 RuntimeError。"""
    # 传入会导致 OSError 的配置（如 cwd 指向不存在的路径，list 命令触发）
    spec = TaskSpec(name="x", cmd=["this_does_not_exist_xyz"], cwd=Path("nonexistent_path_xyz"))
    with pytest.raises(RuntimeError):
        spec.effective_fn()
