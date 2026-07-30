"""命令执行器：把 :class:`~fcmd.task.TaskSpec` 的 ``cmd`` 字段（list /
shell 字符串 / 可调用对象）转换为统一执行入口。

本模块作为纯执行逻辑集中地，``TaskSpec`` 仅持有配置，执行逻辑位于此处，
便于独立测试与维护。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, List, Union, cast

from .console import get_console
from .task import TaskSpec

__all__ = ["run_command"]


def _run_callable_command(cmd: Any, verbose: bool, cwd: Path | None) -> Any:
    """执行可调用对象，异常包装为 :class:`RuntimeError`。"""
    name = getattr(cmd, "__name__", "callable")
    if verbose:
        console = get_console()
        console.print(f"[cyan]>[/cyan] 执行可调用命令: [bold]{name}[/bold]")
        if cwd is not None:
            console.print(f"  [dim]工作目录: {cwd}[/dim]")
    try:
        return cmd()
    except Exception as e:
        raise RuntimeError(f"可调用命令执行异常: {name}: {e}") from e


def _run_subprocess_command(cmd: str | list[str], spec: TaskSpec[Any]) -> None:
    """通过 :func:`subprocess.run` 执行 list / shell 命令。

    非零返回码抛 :class:`RuntimeError`（``verbose=False`` 时附 stderr）；
    ``cwd`` / ``env`` 通过 subprocess 参数隔离。
    """
    verbose = spec.verbose
    cwd = spec.cwd
    timeout = spec.timeout

    is_list = isinstance(cmd, list)
    if is_list:
        cmd_str = " ".join(arg for arg in cmd)
        verb = "执行命令"
        label = "命令"
    else:
        cmd_str = cast(str, cmd)
        verb = "执行 Shell"
        label = "Shell 命令"

    console = get_console() if verbose else None
    if verbose and console is not None:
        console.print(f"[cyan]>[/cyan] {verb}: [bold]{cmd_str}[/bold]")
        if cwd is not None:
            console.print(f"  [dim]工作目录: {cwd}[/dim]")

    # 合并环境变量
    run_env: dict[str, str] | None = None
    if spec.env:
        run_env = dict(os.environ)
        run_env.update(spec.env)

    try:
        result = subprocess.run(
            cast(Union[str, List[str]], cmd),
            shell=not is_list,
            cwd=cwd,
            env=run_env,
            timeout=timeout,
            capture_output=not verbose,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError(f"{label}未找到: {cmd_str}") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{label}执行超时: {cmd_str} ({timeout}s)") from None
    except OSError as e:
        raise RuntimeError(f"{label}执行异常: {cmd_str}: {e}") from e

    if verbose and console is not None:
        style = "green" if result.returncode == 0 else "red"
        console.print(f"[{style}]返回码: {result.returncode}[/{style}]")

    _handle_subprocess_result(result, verbose, cmd_str, label)


def _handle_subprocess_result(
    result: subprocess.CompletedProcess[str], verbose: bool, cmd_str: str, label: str
) -> None:
    """处理 subprocess 结果：成功时透传 stdout，失败时抛 :class:`RuntimeError`。"""
    if result.returncode == 0:
        if not verbose and result.stdout:
            print(result.stdout, end="", flush=True)  # cmd 任务透传 stdout
        return

    err_msg = f"{label}执行失败: `{cmd_str}`, 返回码: {result.returncode}"
    if not verbose and result.stderr.strip():
        err_msg += f"\n{result.stderr.strip()}"
    raise RuntimeError(err_msg)


def run_command(spec: TaskSpec[Any]) -> Any:
    """执行 ``spec.cmd`` 指定的命令（list / shell 字符串 / 可调用对象）。

    - 可调用对象：直接调用，异常包装为 :class:`RuntimeError`。
    - list / str：通过 :func:`subprocess.run` 执行，非零返回码抛
      :class:`RuntimeError`（``verbose=False`` 时附 stderr）。
    - ``verbose=True`` 时通过 rich console 打印执行信息与返回码。
    - ``cwd`` / ``env`` 通过 subprocess 参数隔离（进程级状态仅在 fn 任务路径
      使用，cmd 路径不依赖 ``os.chdir`` / ``os.environ``）。
    """
    cmd = spec.cmd
    if callable(cmd) and not isinstance(cmd, (list, str)):
        return _run_callable_command(cmd, spec.verbose, spec.cwd)
    return _run_subprocess_command(cast(Union[str, List[str]], cmd), spec)
