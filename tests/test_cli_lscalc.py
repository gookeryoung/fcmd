"""lscalc 工具测试。

验证 ``fcmd.cli.calc.lscalc`` 模块：
- 工具注册
- LsDynaConfig 数据类
- _find_solver / _get_solver_name 自动探测逻辑
- get_ls_dyna_command 命令构造（SMP/MPP × SP/DP × IntelMPI/MSMPI）
- run / mpi 子命令（命令构造 + 成功/失败分支）
- status 子命令（Windows tasklist / POSIX pgrep 跨平台分支）
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import pytest

import fcmd as fx
import fcmd.cli.calc.lscalc
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.calc.lscalc import (
    _DEFAULT_MEMORY,
    _DEFAULT_NCPU,
    LsDynaConfig,
    _find_solver,
    _get_solver_name,
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
# LsDynaConfig 测试
# ---------------------------------------------------------------------- #
class TestLsDynaConfig:
    """LsDynaConfig 数据类测试。"""

    def test_defaults(self) -> None:
        """LsDynaConfig 默认值验证。"""
        config = LsDynaConfig(input_file="test.k")
        assert config.input_file == "test.k"
        assert config.ncpu == _DEFAULT_NCPU
        assert config.precision == "sp"
        assert config.parallel == "smp"
        assert config.mpi_type == "intel"
        assert config.memory == _DEFAULT_MEMORY
        assert config.solver_path is None

    def test_custom_values(self) -> None:
        """LsDynaConfig 支持自定义值。"""
        config = LsDynaConfig(
            input_file="test.k",
            ncpu=8,
            precision="dp",
            parallel="mpp",
            mpi_type="msmpi",
            memory="200m",
            solver_path="/custom/ls-dyna",
        )
        assert config.ncpu == 8
        assert config.precision == "dp"
        assert config.parallel == "mpp"
        assert config.mpi_type == "msmpi"
        assert config.memory == "200m"
        assert config.solver_path == "/custom/ls-dyna"


# ---------------------------------------------------------------------- #
# _find_solver / _get_solver_name 自动探测测试
# ---------------------------------------------------------------------- #
class TestSolverAutoDetection:
    """LS-DYNA 求解器自动探测逻辑测试。"""

    def test_find_solver_via_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_find_solver 通过 PATH 找到求解器。"""
        monkeypatch.setattr(
            "shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "lsdyna" else None,
        )
        result = _find_solver(["lsdyna", "ls-dyna_s"])
        assert result == "/usr/bin/lsdyna"

    def test_find_solver_first_match_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_find_solver 返回第一个匹配的候选名。"""
        monkeypatch.setattr(
            "shutil.which",
            lambda name: f"/usr/bin/{name}" if name in ("lsdyna", "ls-dyna_s") else None,
        )
        result = _find_solver(["lsdyna", "ls-dyna_s"])
        assert result == "/usr/bin/lsdyna"  # 第一个匹配

    def test_find_solver_lsdyna_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """_find_solver 在 LSDYNA_HOME 目录中找到求解器。"""
        # 创建模拟的求解器文件
        lsdyna_home = tmp_path / "lsdyna_install"
        lsdyna_home.mkdir()
        (lsdyna_home / "lsdyna_mpp").write_text("", encoding="utf-8")

        monkeypatch.setenv("LSDYNA_HOME", str(lsdyna_home))

        result = _find_solver(["lsdyna_mpp", "ls-dyna_mpp"])
        assert result == str(lsdyna_home / "lsdyna_mpp")

    def test_find_solver_lsdyna_home_exe(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """_find_solver 在 Windows 下查找带 .exe 后缀的求解器。"""
        lsdyna_home = tmp_path / "lsdyna_install"
        lsdyna_home.mkdir()
        (lsdyna_home / "lsdyna.exe").write_text("", encoding="utf-8")

        monkeypatch.setenv("LSDYNA_HOME", str(lsdyna_home))

        result = _find_solver(["lsdyna", "lsdyna.exe"])
        assert result == str(lsdyna_home / "lsdyna.exe")

    def test_find_solver_lsdyna_home_priority_over_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """_find_solver 优先在 LSDYNA_HOME 中搜索，然后才是 PATH。"""
        lsdyna_home = tmp_path / "lsdyna_install"
        lsdyna_home.mkdir()
        (lsdyna_home / "lsdyna").write_text("", encoding="utf-8")

        monkeypatch.setenv("LSDYNA_HOME", str(lsdyna_home))
        monkeypatch.setattr("shutil.which", lambda _name: "/wrong/lsdyna")

        result = _find_solver(["lsdyna"])
        assert result == str(lsdyna_home / "lsdyna")  # 优先使用 LSDYNA_HOME 中的

    def test_find_solver_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_find_solver 找不到任何求解器返回 None。"""
        monkeypatch.delenv("LSDYNA_HOME", raising=False)
        monkeypatch.setattr("shutil.which", lambda _name: None)

        result = _find_solver(["lsdyna", "ls-dyna_s"])
        assert result is None

    def test_get_solver_name_with_path(self) -> None:
        """_get_solver_name 用户指定 solver_path 时直接返回。"""
        config = LsDynaConfig(
            input_file="test.k",
            solver_path="/custom/ls-dyna.exe",
        )
        result = _get_solver_name(config)
        assert result == "/custom/ls-dyna.exe"

    def test_get_solver_name_auto_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_solver_name 自动探测到系统中的求解器。"""
        monkeypatch.setattr(
            "shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "lsdyna" else None,
        )

        config = LsDynaConfig(input_file="test.k", parallel="smp", precision="sp")
        result = _get_solver_name(config)
        assert result == "/usr/bin/lsdyna"

    def test_get_solver_name_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_solver_name 找不到求解器时回退到候选列表第一个。"""
        monkeypatch.setattr("shutil.which", lambda _name: None)

        config = LsDynaConfig(input_file="test.k", parallel="mpp", precision="sp")
        result = _get_solver_name(config)
        assert result == "lsdyna_mpp"  # 候选列表第一个

    def test_get_solver_name_all_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_solver_name 遍历所有候选名直到找到匹配。"""
        # 模拟只有第二个候选名存在
        monkeypatch.setattr(
            "shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "ls-dyna_mpp_s" else None,
        )

        config = LsDynaConfig(input_file="test.k", parallel="mpp", precision="sp")
        result = _get_solver_name(config)
        assert result == "/usr/bin/ls-dyna_mpp_s"


# ---------------------------------------------------------------------- #
# get_ls_dyna_command 测试（命令构造逻辑）
# ---------------------------------------------------------------------- #
class TestGetLsDynaCommand:
    """get_ls_dyna_command 命令构造测试。"""

    # SMP 模式测试
    def test_smp_sp_default(self) -> None:
        """SMP 单精度默认命令（未找到求解器时回退到默认名）。"""
        config = LsDynaConfig(input_file="test.k", parallel="smp")
        cmd = get_ls_dyna_command(config)
        # 未指定 solver_path 且系统无 lsdyna 时，回退到候选列表第一个
        assert "lsdyna" in cmd[0]  # 候选列表第一个
        assert "i=test.k" in cmd
        assert f"ncpu={_DEFAULT_NCPU}" in cmd
        assert "memory=auto" in cmd

    def test_smp_dp(self) -> None:
        """SMP 双精度命令。"""
        config = LsDynaConfig(input_file="test.k", parallel="smp", precision="dp")
        cmd = get_ls_dyna_command(config)
        assert cmd[0] == "lsdyna_d"

    def test_smp_custom_memory(self) -> None:
        """SMP 自定义内存。"""
        config = LsDynaConfig(input_file="test.k", parallel="smp", memory="500m")
        cmd = get_ls_dyna_command(config)
        assert "memory=500m" in cmd

    def test_smp_memory_none(self) -> None:
        """SMP 不指定内存。"""
        config = LsDynaConfig(input_file="test.k", parallel="smp", memory=None)
        cmd = get_ls_dyna_command(config)
        assert "memory=" not in " ".join(cmd)

    def test_smp_custom_solver(self) -> None:
        """SMP 自定义求解器路径。"""
        config = LsDynaConfig(
            input_file="test.k",
            parallel="smp",
            solver_path="/opt/lsdyna/bin/ls-dyna_smp_d_R13.exe",
        )
        cmd = get_ls_dyna_command(config)
        assert cmd[0] == "/opt/lsdyna/bin/ls-dyna_smp_d_R13.exe"

    # MPP + Intel MPI 测试
    def test_mpp_sp_intel_mpi_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MPP 单精度 + Intel MPI（Windows）。"""
        monkeypatch.setattr(sys, "platform", "win32")
        config = LsDynaConfig(
            input_file="test.k",
            parallel="mpp",
            precision="sp",
            mpi_type="intel",
        )
        cmd = get_ls_dyna_command(config)
        assert cmd[0] == "mpirun"
        assert "-localonly" in cmd
        assert "-np" in cmd
        assert str(_DEFAULT_NCPU) in cmd
        assert "lsdyna_mpp" in cmd
        assert "i=test.k" in cmd

    def test_mpp_dp_intel_mpi_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MPP 双精度 + Intel MPI（Linux）。"""
        monkeypatch.setattr(sys, "platform", "linux")
        config = LsDynaConfig(
            input_file="test.k",
            parallel="mpp",
            precision="dp",
            mpi_type="intel",
        )
        cmd = get_ls_dyna_command(config)
        assert cmd[0] == "mpirun"
        assert "-np" in cmd
        assert "lsdyna_mpp_d" in cmd
        assert "i=test.k" in cmd

    def test_mpp_sp_msmpi(self) -> None:
        """MPP 单精度 + MS-MPI。"""
        config = LsDynaConfig(
            input_file="test.k",
            parallel="mpp",
            precision="sp",
            mpi_type="msmpi",
        )
        cmd = get_ls_dyna_command(config)
        assert cmd[0] == "mpiexec"
        assert "-np" in cmd
        assert "-aa" in cmd
        assert "-a" in cmd
        assert "lsdyna_mpp" in cmd

    def test_mpp_dp_msmpi(self) -> None:
        """MPP 双精度 + MS-MPI。"""
        config = LsDynaConfig(
            input_file="test.k",
            parallel="mpp",
            precision="dp",
            mpi_type="msmpi",
        )
        cmd = get_ls_dyna_command(config)
        assert "lsdyna_mpp_d" in cmd

    def test_mpp_platform_mpi(self) -> None:
        """MPP + Platform MPI。"""
        config = LsDynaConfig(
            input_file="test.k",
            parallel="mpp",
            mpi_type="platform",
        )
        cmd = get_ls_dyna_command(config)
        assert cmd[0] == "mpirun"
        assert "-np" in cmd

    def test_mpp_custom_memory(self) -> None:
        """MPP 自定义内存。"""
        config = LsDynaConfig(
            input_file="test.k",
            parallel="mpp",
            memory="1g",
        )
        cmd = get_ls_dyna_command(config)
        assert "memory=1g" in cmd

    def test_mpp_custom_solver(self) -> None:
        """MPP 自定义求解器路径。"""
        config = LsDynaConfig(
            input_file="test.k",
            parallel="mpp",
            solver_path="/custom/ls-dyna_mpp.exe",
        )
        cmd = get_ls_dyna_command(config)
        assert "/custom/ls-dyna_mpp.exe" in cmd

    # HYB 模式测试
    def test_hyb_sp(self) -> None:
        """HYB 单精度命令。"""
        config = LsDynaConfig(input_file="test.k", parallel="hyb", precision="sp")
        cmd = get_ls_dyna_command(config)
        assert "lsdyna_hyb" in cmd

    def test_hyb_dp(self) -> None:
        """HYB 双精度命令。"""
        config = LsDynaConfig(input_file="test.k", parallel="hyb", precision="dp")
        cmd = get_ls_dyna_command(config)
        assert "lsdyna_hyb_d" in cmd

    # 边界情况
    def test_smp_sp_with_ncpu(self) -> None:
        """SMP 自定义核心数。"""
        config = LsDynaConfig(input_file="test.k", parallel="smp", ncpu=16)
        cmd = get_ls_dyna_command(config)
        assert "ncpu=16" in cmd

    def test_mpp_sp_with_ncpu(self) -> None:
        """MPP 自定义核心数。"""
        config = LsDynaConfig(input_file="test.k", parallel="mpp", ncpu=32)
        cmd = get_ls_dyna_command(config)
        assert "-np" in cmd
        assert "32" in cmd


# ---------------------------------------------------------------------- #
# lscalc run / mpi 子命令测试
# ---------------------------------------------------------------------- #
class TestLscalcCommands:
    """``lscalc`` 工具的子命令行为测试。"""

    def test_default_ncpu_is_4(self) -> None:
        """_DEFAULT_NCPU 默认 4。"""
        assert _DEFAULT_NCPU == 4

    # run 子命令 - SMP
    def test_run_smp_input_not_exists(
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

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
        not_existing = tmp_path / "no_such_file.k"
        run_ls_dyna_single(str(not_existing))
        out = capsys.readouterr().out
        assert "输入文件不存在" in out
        assert called == []

    def test_run_smp_success(
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

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_single(str(input_file))
        out = capsys.readouterr().out
        assert "LS-DYNA SMP 计算完成" in out
        assert "lsdyna" in captured["cmd"]
        assert f"i={input_file}" in captured["cmd"]
        assert "ncpu=4" in captured["cmd"]

    def test_run_smp_precision_dp(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_single 双精度模式。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_single(str(input_file), precision="dp")
        assert "lsdyna_d" in captured["cmd"]

    def test_run_smp_custom_memory(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_single 自定义内存。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_single(str(input_file), memory="300m")
        assert "memory=300m" in captured["cmd"]

    def test_run_smp_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_single 命令失败时打印失败消息。"""
        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", _stub_failure)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_single(str(input_file))
        out = capsys.readouterr().out
        assert "LS-DYNA SMP 计算失败" in out

    # mpi 子命令 - MPP
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

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
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

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_mpi(str(input_file))
        out = capsys.readouterr().out
        assert "LS-DYNA MPI 计算完成" in out
        cmd = captured["cmd"]
        assert cmd[0] == "mpirun"
        assert "-np" in cmd
        assert "4" in cmd
        assert "lsdyna_mpp" in cmd
        assert f"i={input_file}" in cmd

    def test_run_mpi_msmpi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_mpi 使用 MS-MPI。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_mpi(str(input_file), mpi="msmpi")
        cmd = captured["cmd"]
        assert cmd[0] == "mpiexec"
        assert "-aa" in cmd
        assert "-a" in cmd

    def test_run_mpi_precision_dp(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_mpi 双精度模式。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        run_ls_dyna_mpi(str(input_file), precision="dp")
        assert "lsdyna_mpp_d" in captured["cmd"]

    def test_run_mpi_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """run_ls_dyna_mpi 命令失败时打印失败消息。"""
        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", _stub_failure)
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
        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", _stub_success)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        code = run_tool("lscalc", ["run", str(input_file)])
        assert code == 0
        out = capsys.readouterr().out
        assert "LS-DYNA SMP 计算完成" in out

    def test_mpi_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """fcmd lscalc mpi <file> 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", _stub_success)
        input_file = tmp_path / "input.k"
        input_file.write_text("dummy", encoding="utf-8")
        code = run_tool("lscalc", ["mpi", str(input_file)])
        assert code == 0
        out = capsys.readouterr().out
        assert "LS-DYNA MPI 计算完成" in out


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
                "ls-dyna_s.exe               1234 Console                    1    100,000 K\n",
            )

        monkeypatch.setattr("fcmd.cli.calc.lscalc.run_command", fake_run)
        check_ls_dyna_status()
        out = capsys.readouterr().out
        assert "ls-dyna_s.exe" in out

    def test_status_windows_tasklist_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Windows 上 tasklist 失败时不打印输出。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            "fcmd.cli.calc.lscalc.run_command",
            lambda _c, **_kw: _make_result(returncode=1),
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
        monkeypatch.setattr(
            "fcmd.cli.calc.lscalc.run_command",
            _stub_returncode_with_stdout(0, "1234\n5678\n"),
        )
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
        monkeypatch.setattr(
            "fcmd.cli.calc.lscalc.run_command",
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
            "fcmd.cli.calc.lscalc.run_command",
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
            "fcmd.cli.calc.lscalc.run_command",
            _stub_returncode_with_stdout(1, ""),
        )
        code = run_tool("lscalc", ["status"])
        assert code == 0
        out = capsys.readouterr().out
        assert "没有运行中的 LS-DYNA 进程" in out
