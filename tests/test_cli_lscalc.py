"""lscalc 工具测试。

验证 ``fcmd.cli.lscalc`` 模块：
- 工具注册
- run / mpi 子命令（命令构造 + 成功/失败分支）
- status 子命令（Windows tasklist / POSIX pgrep 跨平台分支）
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import pytest

import fcmd as fx
import fcmd.cli.lscalc
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.lscalc import (
    _DEFAULT_NCPU,
    check_ls_dyna_status,
    get_ls_dyna_command,
    run_ls_dyna_mpi,
    run_ls_dyna_single,
)
from fcmd.models import CommandResult


# ---------------------------------------------------------------------- #
# 测试辅助：构造 stub run_command
# ---------------------------------------------------------------------- #
def _make_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    """构造 CommandResult 测试替身。"""
    return CommandResult(
        cmd=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _stub_success(_cmd: list[str], **_kwargs: object) -> CommandResult:
    """始终成功的 run_command stub。"""
    return _make_result(returncode=0)


def _stub_failure(_cmd: list[str], **_kwargs: object) -> CommandResult:
    """始终失败的 run_command stub。"""
    return _make_result(returncode=1)


def _stub_failure_with_stderr(
    stderr: str,
) -> Callable[[list[str]], CommandResult]:
    """构造带 stderr 的失败 stub。"""

    def _stub(_cmd: list[str], **_kwargs: object) -> CommandResult:
        return _make_result(returncode=1, stderr=stderr)

    return _stub


def _stub_returncode_with_stdout(
    returncode: int,
    stdout: str,
) -> Callable[[list[str]], CommandResult]:
    """构造指定 returncode 与 stdout 的 stub。"""

    def _stub(_cmd: list[str], **_kwargs: object) -> CommandResult:
        return _make_result(returncode=returncode, stdout=stdout)

    return _stub


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """lscalc 注册验证。"""

    def test_all_tools_registered(self) -> None:
        """lscalc 应在 _TOOL_REGISTRY 中注册。"""
        assert "lscalc" in _TOOL_REGISTRY

    def test_lscalc_subcommands(self) -> None:
        """lscalc 应有 run / mpi / status 子命令。"""
        subs = fx.list_subcommands("lscalc")
        for name in ("run", "mpi", "status"):
            assert name in subs, f"子命令 {name!r} 未注册"


# ---------------------------------------------------------------------- #
# lscalc 工具测试
# ---------------------------------------------------------------------- #
class TestLscalcCommands:
    """``lscalc`` 工具的命令构造与子命令行为测试。"""

    def test_default_ncpu_is_4(self) -> None:
        """_DEFAULT_NCPU 默认 4。"""
        assert _DEFAULT_NCPU == 4

    def test_get_ls_dyna_command_basic(self) -> None:
        """get_ls_dyna_command 构造单机命令。"""
        cmd = get_ls_dyna_command("input.k", 4)
        assert cmd == ["ls-dyna_mpp", "i=input.k", "ncpu=4"]

    def test_get_ls_dyna_command_custom_ncpu(self) -> None:
        """get_ls_dyna_command 支持自定义 ncpu。"""
        cmd = get_ls_dyna_command("job.bin", 8)
        assert "ncpu=8" in cmd
        assert "i=job.bin" in cmd
        assert cmd[0] == "ls-dyna_mpp"

    def test_run_single_input_not_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_single 输入文件不存在时打印提示。"""
        called: list[list[str]] = []

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            called.append(cmd)
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.lscalc.run_command", fake_run)
        not_existing = tmp_path / "no_such_file.k"
        run_ls_dyna_single(str(not_existing))
        out = capsys.readouterr().out
        assert "输入文件不存在" in out
        assert called == []  # 文件不存在时不应调用 run_command

    def test_run_single_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_single 文件存在且命令成功时打印完成。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_single(str(input_file))
        out = capsys.readouterr().out
        assert "LS-DYNA 计算完成" in out
        assert "ls-dyna_mpp" in captured["cmd"]
        assert f"i={input_file}" in captured["cmd"]
        assert "ncpu=4" in captured["cmd"]  # 默认 4 核

    def test_run_single_custom_ncpu(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_single 支持自定义 ncpu。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_single(str(input_file), ncpu=8)
        assert "ncpu=8" in captured["cmd"]

    def test_run_single_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_single 命令失败时打印失败消息。"""
        monkeypatch.setattr("fcmd.cli.lscalc.run_command", _stub_failure)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_single(str(input_file))
        out = capsys.readouterr().out
        assert "LS-DYNA 计算失败" in out

    def test_run_mpi_input_not_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_mpi 输入文件不存在时打印提示。"""
        called: list[list[str]] = []

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            called.append(cmd)
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.lscalc.run_command", fake_run)
        run_ls_dyna_mpi(str(tmp_path / "no_such_file.k"))
        out = capsys.readouterr().out
        assert "输入文件不存在" in out
        assert called == []

    def test_run_mpi_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_mpi 文件存在且命令成功时打印完成。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_mpi(str(input_file))
        out = capsys.readouterr().out
        assert "LS-DYNA MPI 计算完成" in out
        cmd = captured["cmd"]
        assert cmd[0] == "mpirun"
        assert "-np" in cmd
        assert "4" in cmd  # 默认 4 核
        assert "ls-dyna_mpp" in cmd
        assert f"i={input_file}" in cmd

    def test_run_mpi_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_mpi 命令失败时打印失败消息。"""
        monkeypatch.setattr("fcmd.cli.lscalc.run_command", _stub_failure)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_mpi(str(input_file))
        out = capsys.readouterr().out
        assert "LS-DYNA MPI 计算失败" in out

    def test_run_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """fcmd lscalc run <file> 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.lscalc.run_command", _stub_success)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        code = run_tool("lscalc", ["run", str(input_file)])
        assert code == 0
        out = capsys.readouterr().out
        assert "LS-DYNA 计算完成" in out


# ---------------------------------------------------------------------- #
# lscalc status 子命令测试（跨平台分支）
# ---------------------------------------------------------------------- #
class TestLscalcStatus:
    """``lscalc status`` 子命令的跨平台行为测试。"""

    def test_status_windows_with_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Windows 上 tasklist 找到进程时打印 stdout。"""
        monkeypatch.setattr(sys, "platform", "win32")

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            assert cmd[0] == "tasklist"
            assert "/fi" in cmd
            return _make_result(
                returncode=0,
                stdout="映像名称                       PID 会话名              会话#       内存使用\n"
                "ls-dyna_mpp.exe            1234 Console                    1    100,000 K\n",
            )

        monkeypatch.setattr("fcmd.cli.lscalc.run_command", fake_run)
        check_ls_dyna_status()
        out = capsys.readouterr().out
        assert "ls-dyna_mpp.exe" in out

    def test_status_windows_tasklist_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Windows 上 tasklist 失败时不打印输出。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            "fcmd.cli.lscalc.run_command",
            _stub_failure_with_stderr("Access Denied"),
        )
        check_ls_dyna_status()
        out = capsys.readouterr().out
        assert out == ""

    def test_status_posix_with_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Linux/macOS 上 pgrep 找到进程时打印 PID。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            assert cmd == ["pgrep", "-f", "ls-dyna"]
            return _make_result(returncode=0, stdout="1234\n5678\n")

        monkeypatch.setattr("fcmd.cli.lscalc.run_command", fake_run)
        check_ls_dyna_status()
        out = capsys.readouterr().out
        assert "1234" in out
        assert "5678" in out
        assert "PID" in out

    def test_status_posix_no_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Linux/macOS 上 pgrep 无匹配时打印没有进程。"""
        monkeypatch.setattr(sys, "platform", "linux")
        # pgrep 找不到进程返回 1（非错误）
        monkeypatch.setattr(
            "fcmd.cli.lscalc.run_command",
            _stub_returncode_with_stdout(1, ""),
        )
        check_ls_dyna_status()
        out = capsys.readouterr().out
        assert "没有运行中的 LS-DYNA 进程" in out

    def test_status_posix_empty_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Linux/macOS 上 pgrep 返回 0 但 stdout 为空时也视为无进程。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            "fcmd.cli.lscalc.run_command",
            _stub_returncode_with_stdout(0, ""),
        )
        check_ls_dyna_status()
        out = capsys.readouterr().out
        assert "没有运行中的 LS-DYNA 进程" in out

    def test_status_via_run_tool_posix(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd lscalc status 通过 run_tool 调用（Linux 路径）。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            "fcmd.cli.lscalc.run_command",
            _stub_returncode_with_stdout(1, ""),
        )
        code = run_tool("lscalc", ["status"])
        assert code == 0
        out = capsys.readouterr().out
        assert "没有运行中的 LS-DYNA 进程" in out
