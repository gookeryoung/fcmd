"""llmqnt - GGUF 量化工具（基于 llama-quantize）。

把 F16 GGUF 按勾选的多种量化类型批量转换为量化 GGUF，供 ``llmserve``
按档位加载。默认输出到输入文件同目录，命名规则为
``<去掉 -F16 后缀的文件名>-<量化类型>.gguf``；已存在的输出默认跳过，
可用 ``--overwrite`` 强制重跑。

示例
----
    fcmd llmqnt model-F16.gguf
    fcmd llmqnt model-F16.gguf --quants Q4_K_M Q5_K_M --overwrite
    fcmd llmqnt model.gguf --out-dir D:/models/quantized
    fcmd llmqntw
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.console import Table, get_console
from fcmd.apps.locllm import llmbase, llmengine

__all__ = [
    "DEFAULT_QUANTS",
    "QUANTIZE_TIMEOUT",
    "QUANT_TYPES",
    "QuantizeError",
    "QuantizeJob",
    "output_path",
    "pending_jobs",
    "plan_jobs",
    "resolve_quantize_exe",
    "run_job",
    "run_jobs",
    "strip_f16_suffix",
]

#: 支持的量化类型（与 llama-quantize 的量化名一致）
QUANT_TYPES: dict[str, str] = {
    "Q2_K": "极低精度，最小尺寸",
    "Q3_K_S": "低精度，小尺寸",
    "Q3_K_M": "低精度，中等尺寸",
    "Q3_K_L": "低精度，大尺寸",
    "Q4_0": "基本 4 位",
    "Q4_1": "基本 4 位（带偏移）",
    "Q4_K_S": "4 位，小尺寸",
    "Q4_K_M": "4 位，中等尺寸",
    "Q5_0": "基本 5 位",
    "Q5_1": "基本 5 位（带偏移）",
    "Q5_K_S": "5 位，小尺寸",
    "Q5_K_M": "5 位，中等尺寸",
    "Q6_K": "6 位，高质量",
    "Q8_0": "8 位，最高质量",
    "IQ1_S": "1.56 bpw 超高压缩",
    "IQ1_M": "1.75 bpw 超高压缩",
    "IQ2_XXS": "2.06 bpw 高压缩",
    "IQ2_XS": "2.31 bpw 高压缩",
    "IQ2_S": "2.5 bpw",
    "IQ2_M": "2.7 bpw",
    "IQ3_XXS": "3.06 bpw",
    "IQ3_S": "3.44 bpw",
    "IQ3_M": "3.66 bpw",
    "IQ4_NL": "4.5 bpw 非线性量化",
    "IQ4_XS": "4.25 bpw 小尺寸",
}

#: 未指定 ``--quants`` 时使用的默认量化类型
DEFAULT_QUANTS: tuple[str, ...] = ("Q4_K_M", "Q5_K_M")

#: 单个量化任务的超时秒数（与参考实现一致，1 小时）
QUANTIZE_TIMEOUT = 3600.0

#: Windows 访问冲突（llama-quantize 启动即崩溃的典型退出码）
_ACCESS_VIOLATION = 0xC0000005

_DESCRIPTION = "GGUF 量化：调用 llama-quantize 批量生成多种量化版本"


class QuantizeError(RuntimeError):
    """量化输入、引擎或转换过程失败。"""


def _tail(text: str, lines: int = 6) -> str:
    """取输出的最后若干行（用于失败摘要）。"""
    parts = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " | ".join(parts[-lines:])


@dataclass(frozen=True, slots=True)
class QuantizeJob:
    """单个量化任务。

    Attributes
    ----------
    input:
        F16 GGUF 输入文件。
    output:
        量化输出文件。
    quant:
        量化类型（:data:`QUANT_TYPES` 的键）。
    """

    input: Path
    output: Path
    quant: str


def strip_f16_suffix(stem: str) -> str:
    """移除文件名结尾的 ``-F16`` 后缀（不区分大小写）。"""
    return stem[:-4] if stem.upper().endswith("-F16") else stem


def output_path(input_file: Path, quant: str, out_dir: Path | None = None) -> Path:
    """计算某个量化类型的输出路径。"""
    directory = (out_dir or input_file.parent).expanduser()
    return directory / f"{strip_f16_suffix(input_file.stem)}-{quant}.gguf"


def plan_jobs(
    input_file: Path,
    quants: list[str] | tuple[str, ...],
    out_dir: Path | None = None,
) -> list[QuantizeJob]:
    """构造量化任务列表（不检查输出是否已存在）。

    Raises
    ------
    QuantizeError
        输入文件不存在 / 不是 ``.gguf``，或量化类型不受支持。
    """
    source = input_file.expanduser()
    if not source.is_file():
        raise QuantizeError(f"输入文件不存在: {source}")
    if source.suffix.lower() != ".gguf":
        raise QuantizeError(f"输入文件必须是 .gguf: {source}")
    unknown = [quant for quant in quants if quant not in QUANT_TYPES]
    if unknown:
        raise QuantizeError(f"不支持的量化类型: {', '.join(unknown)}；可选: {', '.join(QUANT_TYPES)}")

    jobs: list[QuantizeJob] = []
    for quant in quants:
        target = output_path(source, quant, out_dir)
        if target.exists() and target.resolve() == source.resolve():
            raise QuantizeError(f"输出文件与输入文件相同: {target}")
        jobs.append(QuantizeJob(source, target, quant))
    return jobs


def pending_jobs(
    jobs: list[QuantizeJob],
    overwrite: bool = False,
) -> tuple[list[QuantizeJob], list[QuantizeJob]]:
    """把任务拆分为「待执行」与「已存在跳过」两组。"""
    if overwrite:
        return list(jobs), []
    pending = [job for job in jobs if not job.output.is_file()]
    skipped = [job for job in jobs if job.output.is_file()]
    return pending, skipped


def resolve_quantize_exe(explicit: Path | None = None) -> Path:
    """定位 ``llama-quantize`` 可执行文件。

    查找顺序：``--quantize-exe`` → ``FCMD_LLAMA_QUANTIZE`` → 当前引擎
    同目录 → 其他已安装引擎 → ``PATH``。

    官方 release 包不一定带 ``llama-quantize``（如 LM Studio 后端），此时不能
    直接落到 ``PATH``：机器上可能装的是不兼容的旧版本，读取新版 GGUF 会以
    0xC0000005 崩溃，因此优先复用其他已安装引擎自带的量化器。

    Raises
    ------
    QuantizeError
        所有来源都找不到可执行文件。
    """
    if explicit is not None:
        exe = explicit.expanduser()
        if not exe.is_file():
            raise QuantizeError(f"--quantize-exe 指向的文件不存在: {exe}")
        return exe

    raw = os.environ.get("FCMD_LLAMA_QUANTIZE")
    if raw:
        target = Path(raw).expanduser()
        if not target.is_file():
            raise QuantizeError(f"FCMD_LLAMA_QUANTIZE 指向的文件不存在: {target}")
        return target

    engine = None
    with contextlib.suppress(llmbase.ServeError):
        engine = llmengine.resolve_engine()
    if engine is not None:
        candidate = llmengine.quantize_exe(engine)
        if candidate is not None:
            return candidate

    for other in llmengine.list_engines():
        if engine is not None and other.exe == engine.exe:
            continue
        candidate = llmengine.quantize_exe(other)
        if candidate is not None:
            return candidate

    found = shutil.which("llama-quantize")
    if found:
        return Path(found)

    raise QuantizeError(
        "未找到 llama-quantize：安装 llama.cpp 引擎（fcmd llmengine install），"
        "或用 --quantize-exe / FCMD_LLAMA_QUANTIZE 指定"
    )


def run_job(
    job: QuantizeJob,
    exe: Path,
    on_output: Callable[[str], None] | None = None,
    timeout: float = QUANTIZE_TIMEOUT,
) -> None:
    """执行单个量化任务，逐行回调 llama-quantize 的输出。

    Raises
    ------
    QuantizeError
        进程无法启动、超时或返回非零退出码。
    """
    job.output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(exe), str(job.input), str(job.output), job.quant]
    try:
        result = llmbase.stream_command(
            command,
            timeout=timeout,
            on_output=on_output,
            cwd=str(job.input.parent),
        )
    except subprocess.TimeoutExpired as exc:
        raise QuantizeError(f"{job.quant} 量化超时（{timeout:.0f}s）") from exc
    except (FileNotFoundError, PermissionError) as exc:
        raise QuantizeError(f"无法启动 llama-quantize: {exc}") from exc

    if result.returncode != 0:
        # llama-quantize 直接写目标文件，失败会留下半成品；不清掉会被后续任务当成已完成
        with contextlib.suppress(OSError):
            job.output.unlink()
        crashed = result.returncode in (_ACCESS_VIOLATION, _ACCESS_VIOLATION - 2**32)
        code = llmbase.format_returncode(result.returncode)
        if crashed and "0x" not in code:
            code += f" (0x{_ACCESS_VIOLATION:08X})"
        detail = _tail(result.stdout)
        hint = f"；{llmbase.MSVC_HINT}（{exe}）" if crashed else ""
        raise QuantizeError(f"{job.quant} 量化失败（退出码 {code}，量化器 {exe}）：{detail or '无输出'}{hint}")


def run_jobs(
    jobs: list[QuantizeJob],
    exe: Path,
    on_output: Callable[[str], None] | None = None,
    on_done: Callable[[QuantizeJob, bool, str], None] | None = None,
) -> list[str]:
    """顺序执行多个量化任务，返回失败摘要（空列表表示全部成功）。"""
    failures: list[str] = []
    for job in jobs:
        try:
            run_job(job, exe, on_output=on_output)
        except QuantizeError as exc:
            failures.append(f"{job.quant}: {exc}")
            if on_done is not None:
                on_done(job, False, str(exc))
        else:
            if on_done is not None:
                on_done(job, True, "")
    return failures


@fcmd.tool("llmqnt", help="批量量化 GGUF（默认 Q4_K_M / Q5_K_M）", description=_DESCRIPTION)
def quantize_cmd(
    input_file: Path,
    quants: list[str] | None = None,
    out_dir: Path | None = None,
    quantize_exe: Path | None = None,
    overwrite: bool = False,
) -> None:
    """把 F16 GGUF 批量转换为指定量化类型。

    Parameters
    ----------
    input_file:
        输入的 F16 GGUF 文件路径。
    quants:
        量化类型列表（空格分隔多个），如 ``Q4_K_M Q5_K_M``；默认两者。
    out_dir:
        输出目录；默认与输入文件同目录。
    quantize_exe:
        llama-quantize 可执行文件；默认按环境变量 / 引擎 / PATH 查找。
    overwrite:
        覆盖已存在的量化输出。
    """
    selected = list(quants) if quants else list(DEFAULT_QUANTS)
    jobs = plan_jobs(input_file, selected, out_dir)
    pending, skipped = pending_jobs(jobs, overwrite)
    exe = resolve_quantize_exe(quantize_exe)

    console = get_console()
    table = Table(title="量化计划", box="ascii")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("值")
    table.add_row("输入", str(jobs[0].input))
    table.add_row("引擎", str(exe))
    table.add_row("量化类型", ", ".join(job.quant for job in jobs))
    table.add_row("输出目录", str(jobs[0].output.parent))
    console.print(table)

    for job in skipped:
        console.print(f"[yellow]已存在，跳过:[/yellow] {job.output}")
    if not pending:
        console.print("全部量化文件已存在，无需转换（--overwrite 强制重跑）")
        return

    def _done(job: QuantizeJob, ok: bool, error: str) -> None:
        if ok:
            console.print(f"[green]已生成:[/green] {job.output}")
        else:
            console.print(f"[red]失败:[/red] {error}")

    failures = run_jobs(pending, exe, on_done=_done)
    if failures:
        raise QuantizeError(f"{len(failures)} 个量化任务失败：" + "；".join(failures))


@fcmd.main("llmqnt")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
