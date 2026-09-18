"""测试基础设施：跨测试共享的 pytest fixtures。

本文件提供 e2e 测试专用的 subprocess 执行 fixture：
- ``fcmd``  —— 子进程内执行 ``python -m fcmd ...``，返回
  :class:`CmdResult`（与 subprocess.CompletedProcess 兼容）
- ``tmp_home`` —— 隔离的 ``$HOME`` 目录，防止持久化副作用污染用户文件

fixture 工作目录默认指向 pytest ``tmp_path``，e2e 用例在其中读写文件，
不影响仓库。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest


class CmdResult:
    """命令执行结果的便捷包装。

    提供 ``returncode`` / ``stdout`` / ``stderr`` 属性，
    以及 ``check_ok()`` 方法。
    """

    def __init__(self, proc: subprocess.CompletedProcess[str]) -> None:
        self._proc = proc

    @property
    def returncode(self) -> int:
        return self._proc.returncode

    @property
    def stdout(self) -> str:
        return self._proc.stdout

    @property
    def stderr(self) -> str:
        return self._proc.stderr

    def check_ok(self) -> None:
        """断言命令成功退出（returncode=0），失败时打印完整输出。"""
        if self._proc.returncode != 0:
            raise AssertionError(
                f"命令失败 (exit {self._proc.returncode}):\nstdout:\n{self._proc.stdout}\nstderr:\n{self._proc.stderr}"
            )


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """隔离的 ``$HOME`` 目录（用于持久化 e2e 测试）。"""
    home = tmp_path / "fakehome"
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture
def fcmd(tmp_path: Path, tmp_home: Path) -> Callable[..., CmdResult]:
    """子进程内执行 fcmd 命令的 fixture。

    Parameters
    ----------
    tmp_path:
        pytest 自动注入的临时目录（作为 CWD）
    tmp_home:
        隔离的 ``$HOME``

    Returns
    -------
    callable
        ``fcmd(tool, *args, expect=0, stdin=None, env=None, timeout=30)``
        → :class:`CmdResult`
    """

    def run(
        tool: str | None,
        *args: str,
        expect: int | None = 0,
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> CmdResult:
        cmd: list[str] = [sys.executable, "-m", "fcmd"]
        if tool is not None:
            cmd.append(tool)
        cmd.extend(args)

        merged_env = dict(os.environ)
        merged_env["HOME"] = str(tmp_home)
        merged_env.setdefault("PYTHONIOENCODING", "utf-8")
        if env:
            merged_env.update(env)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=stdin,
            cwd=str(tmp_path),
            env=merged_env,
            timeout=timeout,
            check=False,
        )
        result = CmdResult(proc)
        if expect is not None and proc.returncode != expect:
            raise AssertionError(
                f"命令退出码为 {proc.returncode}，期望 {expect}:\n"
                f"命令: {' '.join(cmd)}\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )
        return result

    return run
