"""外部命令执行器与能力探测。

所有对 ``wsl.exe`` / ``wt.exe`` 的调用都必须经过本模块的协议，测试注入假实现，
单元测试不执行真实 ``wsl.exe``。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from fcmd.apps.wslman._decode import decode_console, parse_wsl_version

__all__ = [
    "Capabilities",
    "CommandResult",
    "Runner",
    "SubprocessRunner",
    "find_wsl",
    "is_windows",
    "probe",
]

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PROBE_TIMEOUT_S = 10.0
_LINE_BREAK = re.compile(rb"\r\n|\r|\n")
"""逐行回调时的行分隔（apt / curl 的 ``\\r`` 进度行也算一行）。"""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """一次外部命令的完整结果；stdout / stderr 保留原始字节。"""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_ms: int

    @property
    def output_text(self) -> str:
        """合并 stdout 与 stderr 后的解码文本，用于错误分类。"""
        return decode_console(self.stdout + self.stderr)


class Runner(Protocol):
    """外部命令执行器协议。"""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> CommandResult: ...


class SubprocessRunner:
    """真实执行器：不经过 shell，捕获原始字节；``stdin`` 用于把文件内容送进子进程。"""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> CommandResult:
        args = list(argv)
        child_env = os.environ.copy()
        child_env["WSL_UTF8"] = "1"
        if env:
            child_env.update(env)

        started = time.monotonic()
        completed = subprocess.run(
            args,
            capture_output=True,
            check=False,
            env=child_env,
            timeout=timeout,
            input=stdin,
            creationflags=_CREATE_NO_WINDOW,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            argv=tuple(args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=duration_ms,
        )

    def run_streaming(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """同 :meth:`run`，但 stderr 并入 stdout，并逐行回调解码后的输出。

        长命令（linuxmirrors 换源、apt 装包）只在结束时回显会让人以为卡死；
        这里通过管道边读边报，``on_line`` 在后台线程按非空行调用。回调异常只
        丢掉该行：回调层出错不能让管道停止排空，否则子进程会永久阻塞。
        超时行为与 :meth:`run` 一致（抛 ``subprocess.TimeoutExpired``）。
        """
        args = [str(item) for item in argv]
        child_env = os.environ.copy()
        child_env["WSL_UTF8"] = "1"
        if env:
            child_env.update(env)

        started = time.monotonic()
        # `stdin=None` 时显式接 DEVNULL：不能继承 GUI 进程的句柄，免得脚本等输入卡住。
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            env=child_env,
            creationflags=_CREATE_NO_WINDOW,
        )
        chunks: list[bytes] = []
        pending = b""

        def emit(raw: bytes) -> None:
            if on_line is None:
                return
            for text in decode_console(raw).splitlines():
                if not text.strip():
                    continue
                try:
                    on_line(text)
                except Exception:
                    continue

        def read_stream() -> None:
            nonlocal pending
            stream = process.stdout
            if stream is None:
                return
            read_chunk = getattr(stream, "read1", None) or stream.read
            while packet := read_chunk(4096):
                chunks.append(packet)
                pending += packet
                parts = _LINE_BREAK.split(pending)
                pending = parts.pop()
                for part in parts:
                    emit(part)
            if pending:
                emit(pending)

        if stdin is not None and process.stdin is not None:
            try:
                process.stdin.write(stdin)
                process.stdin.close()
            except OSError:
                pass
        reader = threading.Thread(target=read_stream, daemon=True)
        reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            reader.join(timeout=5.0)
            raise
        reader.join(timeout=5.0)
        return CommandResult(
            argv=tuple(args),
            returncode=returncode,
            stdout=b"".join(chunks),
            stderr=b"",
            duration_ms=int((time.monotonic() - started) * 1000),
        )


@dataclass(frozen=True, slots=True)
class Capabilities:
    """一次运行内探测到的外部能力；不做跨运行缓存。"""

    wsl_path: str | None
    version: str | None
    help_text: str
    has_install: bool
    has_install_no_launch: bool
    has_import_version: bool
    errors: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.wsl_path is not None


def is_windows() -> bool:
    return sys.platform == "win32"


def find_wsl() -> str | None:
    """在 PATH 里找 ``wsl.exe``；找不到返回 None。"""
    return shutil.which("wsl.exe") or shutil.which("wsl")


def probe(runner: Runner, *, wsl_path: str | None = None) -> Capabilities:
    """探测 ``wsl.exe`` 是否存在、版本与可用开关。

    任何一步失败都只记入 ``errors``，不抛异常：上层据此给出稳定提示。
    """
    path = find_wsl() if wsl_path is None else wsl_path
    if path is None:
        return Capabilities(
            wsl_path=None,
            version=None,
            help_text="",
            has_install=False,
            has_install_no_launch=False,
            has_import_version=False,
            errors=("未在 PATH 中找到 wsl.exe",),
        )

    errors: list[str] = []
    version: str | None = None
    try:
        version_result = runner.run([path, "--version"], timeout=_PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"执行 wsl.exe --version 失败：{exc}")
    else:
        if version_result.returncode == 0:
            version = parse_wsl_version(decode_console(version_result.stdout))
        else:
            errors.append("wsl.exe --version 返回非零退出码")

    help_text = ""
    try:
        help_result = runner.run([path, "--help"], timeout=_PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"执行 wsl.exe --help 失败：{exc}")
    else:
        help_text = decode_console(help_result.stdout or help_result.stderr)

    lowered = help_text.casefold()
    has_install = "--install" in lowered
    has_install_no_launch = has_install and "--no-launch" in lowered
    has_import_version = "--import" in lowered and "--version" in lowered

    return Capabilities(
        wsl_path=path,
        version=version,
        help_text=help_text,
        has_install=has_install,
        has_install_no_launch=has_install_no_launch,
        has_import_version=has_import_version,
        errors=tuple(errors),
    )
