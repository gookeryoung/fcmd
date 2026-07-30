"""lscalc - LS-DYNA 计算工具.

运行 LS-DYNA 计算（单机 SMP / MPI 并行）与进程状态检查。

LS-DYNA 版本体系
----------------
- 精度：SP（单精度，ls-dyna_s/ls-dyna_mpp_s）、DP（双精度，ls-dyna_d/ls-dyna_mpp_d）
- 并行：SMP（单机共享内存）、MPP（分布式 MPI）、HYB（混合，仅 Linux）
- MPI：Intel MPI（mpirun/mpiexec）、MS-MPI（mpiexec -aa -a）

示例
----
    fcmd lscalc run input.k                    # SMP 单机运行（默认 SP，4 核）
    fcmd lscalc run input.k --ncpu 8           # SMP 单机 8 核
    fcmd lscalc run input.k --precision dp     # SMP 双精度
    fcmd lscalc run input.k --memory 200m      # 指定内存
    fcmd lscalc mpi input.k                    # MPI 并行（默认 Intel MPI，4 核）
    fcmd lscalc mpi input.k --mpi msmpi        # MS-MPI 并行
    fcmd lscalc mpi input.k --precision dp     # MPP 双精度
    fcmd lscalc status                         # 检查 LS-DYNA 进程状态
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fcmd
from fcmd.models import run_command

__all__ = [
    "LsDynaConfig",
    "check_ls_dyna_status",
    "get_ls_dyna_command",
    "run_ls_dyna_mpi",
    "run_ls_dyna_single",
]

_DEFAULT_NCPU: int = 4
_DEFAULT_MEMORY: str = "auto"

# LS-DYNA 求解器候选名称列表
# 不同版本/厂商的命名风格差异很大，按常见度排序，
# _find_solver 会依次探测列表中的名称，返回第一个在系统中找到的。
#
# 命名风格对照：
#   ANSYS 新版: ls-dyna_mpp_s / ls-dyna_mpp_d (横杠风格)
#   绿色版/和谐版: lsdyna_mpp / lsdyna (无横杠风格)
#   旧版: mppdyna / dynalmp (传统命名)
#
# 候选列表覆盖主流命名约定，用户亦可通过 solver_path 直接指定完整路径。
_SOLVER_CANDIDATES: dict[tuple[str, str], list[str]] = {
    ("smp", "sp"): ["lsdyna", "ls-dyna_s", "ls-dyna", "smpdyna"],
    ("smp", "dp"): ["lsdyna_d", "ls-dyna_d", "smpdyna_d"],
    ("mpp", "sp"): ["lsdyna_mpp", "ls-dyna_mpp_s", "ls-dyna_mpp", "mppdyna"],
    ("mpp", "dp"): ["lsdyna_mpp_d", "ls-dyna_mpp_d", "mppdyna_d"],
    ("hyb", "sp"): ["lsdyna_hyb", "ls-dyna_hyb_s", "ls-dyna_hyb"],
    ("hyb", "dp"): ["lsdyna_hyb_d", "ls-dyna_hyb_d"],
}

# 求解器环境变量：若设置了 LSDYNA_HOME，则在该目录下额外搜索
_LSDYNA_HOME_ENV: str = "LSDYNA_HOME"

# MPI 启动命令
_MPI_LAUNCHERS: dict[str, str] = {
    "intel": "mpirun",
    "msmpi": "mpiexec",
    "platform": "mpirun",
}

# Windows 上 MS-MPI 的特殊参数
_MSMPI_EXTRA_ARGS: list[str] = ["-aa", "-a"]


@dataclass
class LsDynaConfig:
    """LS-DYNA 计算配置。

    封装求解器选择、并行方式、精度、MPI 实现等所有配置维度，
    作为命令生成的统一输入。

    Attributes
    ----------
    input_file:
        输入文件路径（.k 文件）
    ncpu:
        CPU 核心数（默认 4）
    precision:
        精度类型：'sp'（单精度）或 'dp'（双精度）
    parallel:
        并行方式：'smp'（单机）、'mpp'（MPP 分布式）、'hyb'（混合）
    mpi_type:
        MPI 实现：'intel'、'msmpi'、'platform'，仅 MPP/HYB 模式生效
    memory:
        内存分配大小，如 '200m'、'1g'、'auto'
    solver_path:
        求解器可执行文件完整路径，为 None 时使用标准命名
    """

    input_file: str
    ncpu: int = _DEFAULT_NCPU
    precision: Literal["sp", "dp"] = "sp"
    parallel: Literal["smp", "mpp", "hyb"] = "smp"
    mpi_type: Literal["intel", "msmpi", "platform"] = "intel"
    memory: str | None = _DEFAULT_MEMORY
    solver_path: str | None = None


def _find_solver(candidates: list[str]) -> str | None:
    """在系统中查找可执行的 LS-DYNA 求解器。

    依次检查：
    1. ``LSDYNA_HOME`` 环境变量指定目录下的候选名称
    2. 系统 ``PATH`` 中的候选名称

    Parameters
    ----------
    candidates:
        候选可执行文件名列表，按优先级排序

    Returns
    -------
    str or None
        找到的第一个可执行文件的完整路径，未找到返回 None
    """
    # 1. 在 LSDYNA_HOME 目录下搜索
    lsdyna_home = os.environ.get(_LSDYNA_HOME_ENV)
    if lsdyna_home:
        home_dir = Path(lsdyna_home)
        if home_dir.is_dir():
            for name in candidates:
                # 同时检查 name 和 name.exe（Windows 可能不带扩展名）
                for suffix in ("", ".exe"):
                    candidate_path = home_dir / f"{name}{suffix}"
                    if candidate_path.is_file():
                        return str(candidate_path)

    # 2. 在 PATH 中搜索
    for name in candidates:
        resolved = shutil.which(name)
        if resolved:
            return resolved

    return None


def _get_solver_name(config: LsDynaConfig) -> str:
    """根据配置获取求解器可执行文件名（自动探测）。

    若用户通过 ``solver_path`` 指定了完整路径，直接返回；
    否则从候选名称列表中自动探测系统可用的求解器。

    Parameters
    ----------
    config:
        LS-DYNA 计算配置

    Returns
    -------
    str
        求解器可执行文件名或路径
    """
    if config.solver_path:
        return config.solver_path

    candidates = _SOLVER_CANDIDATES.get(
        (config.parallel, config.precision),
        _SOLVER_CANDIDATES.get(("smp", config.precision), []),
    )

    if not candidates:
        return f"lsdyna_{config.precision}"

    found = _find_solver(candidates)
    if found:
        return found

    # 未找到任何求解器，返回候选列表第一个（让 OS 报路径错误）
    return candidates[0]


def get_ls_dyna_command(config: LsDynaConfig) -> list[str]:
    """根据配置构造 LS-DYNA 命令行。

    Parameters
    ----------
    config:
        LS-DYNA 计算配置

    Returns
    -------
    list[str]
        完整的命令列表，包含求解器、输入文件、内存等参数

    Raises
    ------
    ValueError:
        当 parallel='smp' 且指定了 mpi_type 时
    """
    solver = _get_solver_name(config)
    input_file = config.input_file

    # SMP 模式：直接调用求解器
    if config.parallel == "smp":
        cmd: list[str] = [solver, f"i={input_file}", f"ncpu={config.ncpu}"]
        if config.memory:
            cmd.append(f"memory={config.memory}")
        return cmd

    # MPP / HYB 模式：需要 MPI 启动器
    mpi_launcher = _MPI_LAUNCHERS.get(config.mpi_type, "mpirun")

    if config.mpi_type == "msmpi":
        # MS-MPI: mpiexec -np N -aa -a solver i=input [memory=...]
        cmd = [mpi_launcher, "-np", str(config.ncpu), *_MSMPI_EXTRA_ARGS]
    elif sys.platform == "win32" and config.mpi_type == "intel":
        # Windows Intel MPI: mpiexec -localonly -np N solver ...
        cmd = [mpi_launcher, "-localonly", "-np", str(config.ncpu)]
    else:
        # Linux / Platform MPI: mpirun -np N solver ...
        cmd = [mpi_launcher, "-np", str(config.ncpu)]

    cmd.append(solver)
    cmd.append(f"i={input_file}")

    if config.memory:
        cmd.append(f"memory={config.memory}")

    return cmd


@fcmd.tool("lscalc", subcommand="run", help="运行 LS-DYNA SMP 单机计算")
def run_ls_dyna_single(
    input_file: str,
    ncpu: int = _DEFAULT_NCPU,
    precision: Literal["sp", "dp"] = "sp",
    memory: str | None = _DEFAULT_MEMORY,
    solver: str | None = None,
) -> None:
    """运行 LS-DYNA SMP 单机计算。

    Parameters
    ----------
    input_file:
        输入文件路径
    ncpu:
        CPU 核心数（默认: 4）
    precision:
        精度类型，'sp' 单精度（默认）或 'dp' 双精度
    memory:
        内存分配大小，如 '200m'、'1g'、'auto'
    solver:
        求解器可执行文件完整路径，为 None 时使用标准命名
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"输入文件不存在: {input_path}")
        return

    config = LsDynaConfig(
        input_file=input_file,
        ncpu=ncpu,
        precision=precision,
        parallel="smp",
        memory=memory,
        solver_path=solver,
    )
    cmd = get_ls_dyna_command(config)
    result = run_command(cmd)
    if result.failed:
        print(f"LS-DYNA SMP 计算失败: {input_file}")
        return
    print(f"LS-DYNA SMP 计算完成: {input_file}")


@fcmd.tool("lscalc", subcommand="mpi", help="运行 LS-DYNA MPI 并行计算")
def run_ls_dyna_mpi(  # noqa: PLR0913
    input_file: str,
    ncpu: int = _DEFAULT_NCPU,
    precision: Literal["sp", "dp"] = "sp",
    mpi: Literal["intel", "msmpi", "platform"] = "intel",
    memory: str | None = _DEFAULT_MEMORY,
    solver: str | None = None,
) -> None:
    """运行 LS-DYNA MPI 并行计算。

    Parameters
    ----------
    input_file:
        输入文件路径
    ncpu:
        CPU 核心数（默认: 4）
    precision:
        精度类型，'sp' 单精度（默认）或 'dp' 双精度
    mpi:
        MPI 实现：'intel'（默认）、'msmpi'、'platform'
    memory:
        内存分配大小，如 '200m'、'1g'、'auto'
    solver:
        求解器可执行文件完整路径，为 None 时使用标准命名
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"输入文件不存在: {input_path}")
        return

    config = LsDynaConfig(
        input_file=input_file,
        ncpu=ncpu,
        precision=precision,
        parallel="mpp",
        mpi_type=mpi,
        memory=memory,
        solver_path=solver,
    )
    cmd = get_ls_dyna_command(config)
    result = run_command(cmd)
    if result.failed:
        print(f"LS-DYNA MPI 计算失败: {input_file}")
        return
    print(f"LS-DYNA MPI 计算完成: {input_file}")


@fcmd.tool("lscalc", subcommand="status", help="检查 LS-DYNA 进程状态")
def check_ls_dyna_status() -> None:
    """检查 LS-DYNA 进程状态。

    Windows 使用 ``tasklist`` 过滤 LS-DYNA 进程，
    Linux/macOS 使用 ``pgrep -f ls-dyna`` 查找进程。
    ``pgrep`` 返回 1 表示无匹配进程（非错误），据此区分有无运行中进程。
    """
    if sys.platform == "win32":
        result = run_command(
            ["tasklist", "/fi", "imagename eq ls-dyna*"],
            capture=True,
        )
        if result.succeeded:
            print(result.stdout)
        return

    result = run_command(["pgrep", "-f", "ls-dyna"], capture=True)
    # pgrep 返回 0 表示找到进程，返回 1 表示无匹配
    if result.returncode == 0 and result.stdout.strip():
        print(f"运行中的 LS-DYNA 进程 PID: {result.stdout.strip()}")
    else:
        print("没有运行中的 LS-DYNA 进程")


@fcmd.main("lscalc")
def main() -> None:
    """``lscalc`` 入口：等价于 ``fcmd lscalc <args>``。"""


if __name__ == "__main__":
    main()
