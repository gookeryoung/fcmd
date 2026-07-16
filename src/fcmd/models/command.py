"""命令执行模型。

提供统一的命令执行接口与结果封装，替代各工具中重复的 ``_run`` 函数。

示例
----
    from fcmd.models import run_command

    result = run_command(["git", "status"], capture=True)
    if result.succeeded:
        print(result.stdout)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

__all__ = ["CommandResult", "run_command"]


@dataclass(frozen=True)
class CommandResult:
    """命令执行结果（不可变值对象）。

    封装 ``subprocess.run`` 的返回值，提供类型安全的访问接口。

    Attributes
    ----------
    cmd:
        执行的命令列表
    returncode:
        退出码（``0`` 表示成功）
    stdout:
        标准输出（``capture=False`` 时为空字符串）
    stderr:
        标准错误（``capture=False`` 时为空字符串）
    """

    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        """命令是否成功（返回码为 0）。"""
        return self.returncode == 0

    @property
    def failed(self) -> bool:
        """命令是否失败（返回码非 0）。"""
        return self.returncode != 0


def run_command(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """执行命令并返回结果。

    统一封装 ``subprocess.run``，默认不捕获输出（透传到终端）、不抛异常。

    Parameters
    ----------
    cmd:
        命令列表
    capture:
        是否捕获 stdout/stderr（``True`` 时不透传到终端）
    check:
        返回码非零时是否抛 ``subprocess.CalledProcessError``

    Returns
    -------
    CommandResult
        命令执行结果
    """
    result = subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )
    return CommandResult(
        cmd=list(cmd),
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )
