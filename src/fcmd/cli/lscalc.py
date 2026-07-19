"""lscalc - LS-DYNA 计算工具.

运行 LS-DYNA 计算（单机/MPI）与进程状态检查。

示例
----
    fcmd lscalc run input.k             # 单机运行 LS-DYNA 计算
    fcmd lscalc run input.k --ncpu 8    # 指定 8 核
    fcmd lscalc mpi input.k             # MPI 并行运行（默认 4 核）
    fcmd lscalc status                  # 检查 LS-DYNA 进程状态
"""

from __future__ import annotations

import sys
from pathlib import Path

import fcmd
from fcmd.models import run_command

__all__ = [
    "check_ls_dyna_status",
    "get_ls_dyna_command",
    "run_ls_dyna_mpi",
    "run_ls_dyna_single",
]

_DEFAULT_NCPU: int = 4


def get_ls_dyna_command(input_file: str, ncpu: int) -> list[str]:
    """构造单机 LS-DYNA 命令.

    Parameters
    ----------
    input_file:
        输入文件路径
    ncpu:
        CPU 核心数

    Returns
    -------
    list[str]
        LS-DYNA 命令列表
    """
    return ["ls-dyna_mpp", f"i={input_file}", f"ncpu={ncpu}"]


@fcmd.tool("lscalc", subcommand="run", help="运行 LS-DYNA 计算（单机）")
def run_ls_dyna_single(input_file: str, ncpu: int = _DEFAULT_NCPU) -> None:
    """运行 LS-DYNA 计算（单机）。

    Parameters
    ----------
    input_file:
        输入文件路径
    ncpu:
        CPU 核心数（默认: 4）
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"输入文件不存在: {input_path}")
        return
    cmd = get_ls_dyna_command(input_file, ncpu)
    result = run_command(cmd)
    if result.failed:
        print(f"LS-DYNA 计算失败: {input_file}")
        return
    print(f"LS-DYNA 计算完成: {input_file}")


@fcmd.tool("lscalc", subcommand="mpi", help="运行 LS-DYNA MPI 并行计算")
def run_ls_dyna_mpi(input_file: str, ncpu: int = _DEFAULT_NCPU) -> None:
    """运行 LS-DYNA MPI 并行计算。

    Parameters
    ----------
    input_file:
        输入文件路径
    ncpu:
        CPU 核心数（默认: 4）
    """
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"输入文件不存在: {input_path}")
        return
    cmd = ["mpirun", "-np", str(ncpu), "ls-dyna_mpp", f"i={input_file}"]
    result = run_command(cmd)
    if result.failed:
        print(f"LS-DYNA MPI 计算失败: {input_file}")
        return
    print(f"LS-DYNA MPI 计算完成: {input_file}")


@fcmd.tool("lscalc", subcommand="status", help="检查 LS-DYNA 进程状态")
def check_ls_dyna_status() -> None:
    """检查 LS-DYNA 进程状态。

    Windows 使用 ``tasklist`` 过滤 ``ls-dyna_mpp.exe``，
    Linux/macOS 使用 ``pgrep -f ls-dyna`` 查找进程。
    ``pgrep`` 返回 1 表示无匹配进程（非错误），据此区分有无运行中进程。
    """
    if sys.platform == "win32":
        result = run_command(
            ["tasklist", "/fi", "imagename eq ls-dyna_mpp.exe"],
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
