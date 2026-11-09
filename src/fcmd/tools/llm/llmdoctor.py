"""llmdoctor - 运行环境体检：目录、GPU、CUDA、引擎、模型、端口、磁盘与 VC++ 运行库。

独立命令：``llmdoctor [--port 8080]``。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import fcmd
from fcmd.console import Table, get_console
from fcmd.tools.llm import llmengine, llmenv, llmmodel, llmserve
from fcmd.tools.llm.llmbase import (
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    GIB,
    MSVC_HINT,
    PROFILES,
    Check,
    CheckStatus,
    EngineInfo,
    GpuInfo,
    Runner,
    ServeError,
    human_size,
    run_command,
)

__all__ = ["doctor"]

_MSVC_MIN_VERSION = (14, 30)


def _check_writable(home: Path | None) -> Check:
    base = llmenv.home_dir(home)
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".serve-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("状态目录", "fail", f"{base}: {exc}")
    return Check("状态目录", "ok", str(base))


def _check_gpu(runner: Runner) -> tuple[Check, GpuInfo | None]:
    gpu = llmengine.detect_gpu(runner)
    if gpu is None:
        return Check("GPU", "warn", "未检测到 nvidia-smi 或没有可用 GPU（CPU 推理会很慢）"), None
    return Check("GPU", "ok", f"{gpu.name}，显存 {gpu.memory_mib} MiB"), gpu


def _check_cuda(runner: Runner) -> Check:
    result = runner(["nvcc", "--version"])
    if result.returncode != 0:
        return Check("CUDA Toolkit", "warn", "未找到 nvcc（预编译包不需要，源码编译才需要）")
    line = (result.stdout or result.stderr).strip().splitlines()
    return Check("CUDA Toolkit", "ok", line[-1] if line else "nvcc 可用")


def _check_port(port: int, home: Path | None) -> Check:
    from fcmd.tools.net.portcheck import is_port_in_use

    if not is_port_in_use(port, "127.0.0.1"):
        return Check("端口", "ok", f"127.0.0.1:{port} 空闲")
    for state in llmenv.list_states(home):
        if state.port == port and llmserve.process_alive(state.pid, state.create_time):
            return Check("端口", "ok", f"{port} 由服务 {state.name!r} 占用（pid={state.pid}）")
    return Check("端口", "warn", f"{port} 已被其他进程占用：fcmd portcheck c {port}")


def _check_disk(models_root: Path | None, home: Path | None) -> Check:
    roots = llmenv.models_roots(models_root, home)
    root = roots[0]
    target = root if root.is_dir() else llmenv.home_dir(home)
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return Check("磁盘", "warn", f"无法读取 {target}: {exc}")
    free_gib = usage.free / GIB
    status: CheckStatus = "ok" if free_gib >= 30 else "warn"
    return Check("磁盘", status, f"{target.drive or target} 剩余 {free_gib:.1f} GiB")


def _file_version(dll: Path) -> tuple[int, int, int, int] | None:
    """读取 Windows PE 文件版本（非 Windows 或读取失败返回 ``None``）。"""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    # windll 仅 Windows 存在；标 Any 避免 Linux 下 pyrefly 把 getattr 默认值推成 NoneType
    windll: Any = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    result: tuple[int, int, int, int] | None = None
    try:
        version = windll.version
        size = version.GetFileVersionInfoSizeW(str(dll), None)
        if size:
            buffer = ctypes.create_string_buffer(size)
            if version.GetFileVersionInfoW(str(dll), 0, size, buffer):
                value = wintypes.LPVOID()
                length = wintypes.UINT()
                if version.VerQueryValueW(buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
                    info = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
                    major, minor = info[2], info[3]
                    result = (major >> 16, major & 0xFFFF, minor >> 16, minor & 0xFFFF)
    except (AttributeError, OSError, ValueError):
        result = None
    return result


def _check_msvc_runtime(engine: EngineInfo) -> Check:
    """VC++ 运行库检查：Windows 10 自带的 14.0 版本会让新版 llama.cpp 工具崩溃。"""
    local = engine.exe.parent / "msvcp140.dll"
    system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "msvcp140.dll"
    for dll in (local, system):
        if not dll.is_file():
            continue
        version = _file_version(dll)
        if version is None:
            continue
        text = ".".join(str(item) for item in version)
        if version[:2] >= _MSVC_MIN_VERSION:
            return Check("VC++ 运行库", "ok", f"MSVCP140 {text}（{dll.parent}）")
        return Check(
            "VC++ 运行库",
            "warn",
            f"MSVCP140 {text} 过旧，量化等工具可能崩溃；{MSVC_HINT} {engine.exe.parent}",
        )
    return Check("VC++ 运行库", "warn", "未找到 MSVCP140.dll，量化等工具可能无法启动；请安装最新 VC++ 运行库")


def doctor(
    *,
    home: Path | None = None,
    models_root: Path | None = None,
    port: int = DEFAULT_PORT,
    engine_tag: str | None = None,
    runner: Runner = run_command,
) -> list[Check]:
    """运行环境体检，返回检查项列表（``fail`` 表示必须处理）。"""
    checks: list[Check] = [_check_writable(home)]
    gpu_check, gpu = _check_gpu(runner)
    checks.append(gpu_check)
    checks.append(_check_cuda(runner))

    try:
        engine = llmengine.resolve_engine(engine_tag, home)
    except ServeError as exc:
        checks.append(Check("推理引擎", "fail", str(exc)))
    else:
        checks.append(llmengine.check_engine(engine, runner))
        if os.name == "nt":
            checks.append(_check_msvc_runtime(engine))

    profile = PROFILES[DEFAULT_PROFILE]
    try:
        model = llmmodel.find_model(profile.quant, models_root)
    except ServeError as exc:
        checks.append(Check("模型", "warn", str(exc)))
    else:
        estimate = llmmodel.estimate_vram(model.stat().st_size, profile.context, profile.kv_type)
        detail = f"{model}（{human_size(model.stat().st_size)}，{profile.quant}）"
        checks.append(Check("模型", "ok", detail))
        if gpu is not None:
            fits = estimate <= gpu.memory_mib * 1024 * 1024
            checks.append(
                Check(
                    "显存预算",
                    "ok" if fits else "warn",
                    f"{profile.name} 预计 {human_size(estimate)} / 可用 {human_size(gpu.memory_mib * 1024 * 1024)}",
                )
            )

    checks.append(_check_port(port, home))
    checks.append(_check_disk(models_root, home))
    return checks


_STATUS_TEXT = {"ok": "[green]ok[/green]", "warn": "[yellow]warn[/yellow]", "fail": "[red]fail[/red]"}


@fcmd.tool("llmdoctor", help="运行环境体检（GPU / CUDA / 引擎 / 模型 / 端口 / 磁盘 / VC++）")
def doctor_cmd(
    port: int = DEFAULT_PORT,
    engine_tag: str | None = None,
    models_root: Path | None = None,
) -> None:
    """运行环境体检并打印检查表。

    Parameters
    ----------
    port:
        计划使用的服务端口（默认 8080）。
    engine_tag:
        只体检指定引擎 tag；默认按 current / 最新安装解析。
    models_root:
        只体检该目录；默认用 ``FCMD_MODELS``、配置文件或内置默认目录。
    """
    checks = doctor(port=port, engine_tag=engine_tag, models_root=models_root)
    table = Table(title="llmdoctor 体检", box="ascii")
    table.add_column("检查项", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("详情")
    for check in checks:
        table.add_row(check.name, _STATUS_TEXT[check.status], check.detail)
    get_console().print(table)

    failures = [check for check in checks if check.status == "fail"]
    if failures:
        raise ServeError(f"{len(failures)} 项检查失败：" + "；".join(f"{c.name}: {c.detail}" for c in failures))


@fcmd.main("llmdoctor")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行
