"""子进程执行协议：安装自检与 uv 调用都走这里，便于单测注入假执行器。"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from fcmd.apps.devman._errors import runtime_error

__all__ = ["CommandResult", "Runner", "SubprocessRunner"]


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_s: int = 120,
    ) -> CommandResult:
        """执行命令并捕获输出。"""


class SubprocessRunner:
    """真实执行器；不弹窗、不继承 stdin。"""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_s: int = 120,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                env=dict(env) if env is not None else None,
                stdin=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise runtime_error(f"找不到可执行文件：{argv[0]}", hint="检查安装是否完整") from exc
        except subprocess.TimeoutExpired as exc:
            raise runtime_error(f"命令超时（{timeout_s}s）：{' '.join(argv)}") from exc
        except OSError as exc:
            raise runtime_error(f"执行 {argv[0]} 失败：{exc}") from exc
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
