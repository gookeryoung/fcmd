"""dynacalc 求解调度：命令构造、dry-run、并发/超时/重试、``jobs.json`` 与续跑。

与 ``lscalc`` 的命令口径保持一致（``i=`` / ``ncpu=`` / ``memory=``，SMP 直起、
MPP 经 ``mpirun`` / ``mpiexec``），但不 import 其实现，避免工具域耦合。

``--fake`` 使用内置假求解器 :mod:`fcmd.apps.dynacalc._fakesolver`，在没有
LS-DYNA 的机器上也能跑通端到端；dry-run 只输出计划，不启动进程、不写状态。
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fcmd.apps.dynacalc._template import Manifest, ManifestCase

__all__ = [
    "JOBS_SCHEMA",
    "JobRecord",
    "PlannedJob",
    "RunReport",
    "SolverConfig",
    "SolverError",
    "build_command",
    "format_run_report",
    "load_jobs",
    "plan_jobs",
    "run_jobs",
    "save_jobs",
]

JOBS_SCHEMA = "dynacalc-jobs/1"

_STATUSES = ("pending", "running", "done", "failed", "skipped")


class SolverError(ValueError):
    """求解配置或调度状态不可用：消息为中文原因。"""


@dataclass(frozen=True)
class SolverConfig:
    """求解器配置（来自清单 ``run`` 段，可被 CLI 覆盖）。"""

    solver_path: str = ""
    ncpu: int = 4
    memory: str | None = "auto"
    parallel: str = "smp"
    mpi: str = "intel"
    extra_args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_s: int = 3600
    retries: int = 0
    concurrency: int = 1
    fake: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None, overrides: Mapping[str, Any] | None = None) -> SolverConfig:
        """用清单配置 + CLI 覆盖构造配置；非法值抛 :class:`SolverError`。"""
        data: dict[str, Any] = dict(raw or {})
        for key, value in (overrides or {}).items():
            if value is not None:
                data[key] = value
        config = cls(
            solver_path=str(data.get("solver_path") or "").strip(),
            ncpu=data.get("ncpu", 4),
            memory=data.get("memory", "auto"),
            parallel=str(data.get("parallel") or "smp"),
            mpi=str(data.get("mpi") or "intel"),
            extra_args=tuple(data.get("extra_args") or ()),
            env=dict(data.get("env") or {}),
            timeout_s=data.get("timeout_s", 3600),
            retries=data.get("retries", 0),
            concurrency=data.get("concurrency", 1),
            fake=bool(data.get("fake", False)),
        )
        config._validate()
        return config

    def _validate(self) -> None:
        """逐项校验，避免把非法参数带到子进程。"""
        if not isinstance(self.ncpu, int) or isinstance(self.ncpu, bool) or self.ncpu < 1:
            raise SolverError(f"ncpu 必须是 ≥1 的整数，实际为 {self.ncpu!r}")
        if not isinstance(self.timeout_s, int) or isinstance(self.timeout_s, bool) or self.timeout_s < 1:
            raise SolverError(f"timeout_s 必须是 ≥1 的整数，实际为 {self.timeout_s!r}")
        if not isinstance(self.retries, int) or isinstance(self.retries, bool) or self.retries < 0:
            raise SolverError(f"retries 必须是 ≥0 的整数，实际为 {self.retries!r}")
        if not isinstance(self.concurrency, int) or isinstance(self.concurrency, bool) or self.concurrency < 1:
            raise SolverError(f"concurrency 必须是 ≥1 的整数，实际为 {self.concurrency!r}")
        if self.parallel not in ("smp", "mpp"):
            raise SolverError(f"parallel 必须是 smp/mpp，实际为 {self.parallel!r}")
        if self.mpi not in ("intel", "msmpi", "platform"):
            raise SolverError(f"mpi 必须是 intel/msmpi/platform，实际为 {self.mpi!r}")
        if self.memory is not None and not isinstance(self.memory, str):
            raise SolverError(f"memory 必须是文本或空，实际为 {self.memory!r}")
        if not all(isinstance(item, str) for item in self.extra_args):
            raise SolverError("extra_args 必须是文本列表")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self.env.items()):
            raise SolverError("env 必须是「文本 → 文本」映射")


def _fake_command(deck_dir: Path) -> list[str]:
    """假求解器命令：独立子进程入口，便于测试超时与失败回收。"""
    return [sys.executable, "-m", "fcmd.apps.dynacalc._fakesolver", str(deck_dir)]


def build_command(config: SolverConfig, deck_dir: Path, input_name: str = "case.k") -> list[str]:
    """构造求解命令；不检查可执行文件是否存在（dry-run 也要能显示）。"""
    if config.fake:
        return _fake_command(deck_dir)
    solver = config.solver_path or ("lsdyna.exe" if os.name == "nt" else "lsdyna")
    arguments = [f"i={input_name}", f"ncpu={config.ncpu}"]
    if config.memory:
        arguments.append(f"memory={config.memory}")
    arguments.extend(config.extra_args)
    if config.parallel == "smp":
        return [solver, *arguments]
    launcher = "mpiexec" if os.name == "nt" else "mpirun"
    prefix = ["-np", str(config.ncpu)]
    if config.mpi == "msmpi" and os.name == "nt":
        prefix.append("-localonly")
    return [launcher, *prefix, solver, *arguments]


@dataclass(frozen=True)
class JobRecord:
    """``jobs.json`` 中的单条作业状态。"""

    code: str
    slug: str
    fingerprint: str
    status: str = "pending"
    attempts: int = 0
    exit_code: int | None = None
    duration_s: float = 0.0
    error: str = ""

    def to_mapping(self) -> dict[str, Any]:
        """序列化为 ``jobs.json`` 条目。"""
        return {
            "code": self.code,
            "slug": self.slug,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "attempts": self.attempts,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "error": self.error,
        }


@dataclass(frozen=True)
class PlannedJob:
    """dry-run 计划中的一条命令。"""

    case: ManifestCase
    command: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True)
class RunReport:
    """一次调度（或 dry-run）的结果。"""

    root: Path
    jobs: tuple[JobRecord, ...]
    dry_run: bool
    planned: tuple[PlannedJob, ...] = ()

    def count(self, status: str) -> int:
        """按状态统计。"""
        return sum(1 for item in self.jobs if item.status == status)

    def failed(self) -> tuple[JobRecord, ...]:
        """失败作业。"""
        return tuple(item for item in self.jobs if item.status == "failed")


# ---------------------------------------------------------------------- #
# 作业清单读写
# ---------------------------------------------------------------------- #
def _jobs_path(root: Path) -> Path:
    """``jobs.json`` 路径。"""
    return root / "jobs.json"


def load_jobs(root: Path) -> dict[str, JobRecord]:
    """读取作业清单；文件不存在返回空，损坏则报错（不静默重置续跑状态）。"""
    path = _jobs_path(root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SolverError(f"作业清单 {path} 无法读取: {exc}") from exc
    if not isinstance(raw, Mapping) or raw.get("schema") != JOBS_SCHEMA:
        raise SolverError(f"作业清单 {path} 的 schema 应为 {JOBS_SCHEMA}")
    raw_jobs = raw.get("jobs")
    if not isinstance(raw_jobs, Mapping):
        raise SolverError(f"作业清单 {path} 缺少 jobs 映射")
    jobs: dict[str, JobRecord] = {}
    for code, item in raw_jobs.items():
        if not isinstance(item, Mapping):
            raise SolverError(f"作业清单 {path} 的 {code} 不是对象")
        status = str(item.get("status") or "pending")
        if status not in _STATUSES:
            raise SolverError(f"作业清单 {path} 的 {code} 状态非法: {status}")
        jobs[str(code)] = JobRecord(
            code=str(code),
            slug=str(item.get("slug") or ""),
            fingerprint=str(item.get("fingerprint") or ""),
            status=status,
            attempts=int(item.get("attempts") or 0),
            exit_code=item.get("exit_code"),
            duration_s=float(item.get("duration_s") or 0.0),
            error=str(item.get("error") or ""),
        )
    return jobs


def save_jobs(root: Path, jobs: Mapping[str, JobRecord]) -> None:
    """原子写入 ``jobs.json``（排序、UTF-8、LF）。"""
    path = _jobs_path(root)
    payload = {
        "schema": JOBS_SCHEMA,
        "jobs": {code: jobs[code].to_mapping() for code in sorted(jobs)},
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix="jobs.", suffix=".tmp")
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


# ---------------------------------------------------------------------- #
# 计划与执行
# ---------------------------------------------------------------------- #
def _selected(manifest: Manifest, only: Sequence[str] | None) -> tuple[ManifestCase, ...]:
    """按 ``--only`` 过滤工况；未知编码报错。"""
    if not only:
        return manifest.cases
    wanted = {code.strip() for code in only if code.strip()}
    unknown = sorted(wanted - {item.code for item in manifest.cases})
    if unknown:
        raise SolverError(f"未知工况编码: {', '.join(unknown)}")
    return tuple(item for item in manifest.cases if item.code in wanted)


def plan_jobs(
    manifest: Manifest,
    config: SolverConfig,
    *,
    root: Path,
    force: bool = False,
    only: Sequence[str] | None = None,
) -> RunReport:
    """生成作业计划：指纹一致的 ``done`` 默认跳过，``failed`` 重跑。"""
    previous = load_jobs(root)
    jobs: list[JobRecord] = []
    planned: list[PlannedJob] = []
    for case in _selected(manifest, only):
        old = previous.get(case.code)
        matched = old is not None and old.fingerprint == case.fingerprint
        reusable = old is not None and matched and old.status == "done" and not force
        jobs.append(
            JobRecord(
                code=case.code,
                slug=case.slug,
                fingerprint=case.fingerprint,
                status="skipped" if reusable else "pending",
                attempts=old.attempts if matched and old is not None else 0,
                exit_code=old.exit_code if reusable and old is not None else None,
                duration_s=old.duration_s if reusable and old is not None else 0.0,
            )
        )
        planned.append(
            PlannedJob(
                case=case,
                command=tuple(build_command(config, root / case.slug, "case.k")),
                cwd=root / case.slug,
            )
        )
    return RunReport(root=root, jobs=tuple(jobs), dry_run=True, planned=tuple(planned))


def run_jobs(  # noqa: PLR0913 - 调度开关由 CLI/GUI 直接透传，显式优于配置对象
    manifest: Manifest,
    config: SolverConfig,
    *,
    root: Path,
    force: bool = False,
    only: Sequence[str] | None = None,
    dry_run: bool = False,
    progress: Callable[[JobRecord], None] | None = None,
) -> RunReport:
    """执行（或 dry-run）作业计划；返回最终状态。

    ``progress`` 在每个作业状态变化后回调，GUI 用它刷新作业表。
    """
    plan = plan_jobs(manifest, config, root=root, force=force, only=only)
    if dry_run:
        return plan
    jobs = {item.code: item for item in plan.jobs}
    pending = [item for item in plan.jobs if item.status == "pending"]
    save_jobs(root, jobs)
    lock = threading.Lock()

    def execute(case: ManifestCase) -> JobRecord:
        with lock:
            jobs[case.code] = replace(jobs[case.code], status="running")
            save_jobs(root, jobs)
        if progress is not None:
            progress(jobs[case.code])
        record = _run_one(root, case, config)
        with lock:
            jobs[case.code] = record
            save_jobs(root, jobs)
        if progress is not None:
            progress(record)
        return record

    if pending:
        by_code = {item.code: item for item in manifest.cases}
        with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
            list(pool.map(lambda job: execute(by_code[job.code]), pending))
    ordered = tuple(jobs[item.code] for item in plan.jobs)
    return RunReport(root=root, jobs=ordered, dry_run=False, planned=plan.planned)


def _run_one(root: Path, case: ManifestCase, config: SolverConfig) -> JobRecord:
    """执行单个工况，按 ``retries`` 重试；日志追加到工况目录 ``run.log``。"""
    directory = root / case.slug
    if not directory.is_dir():
        return JobRecord(
            code=case.code,
            slug=case.slug,
            fingerprint=case.fingerprint,
            status="failed",
            error=f"工况目录不存在: {directory}",
        )
    command = build_command(config, directory, "case.k")
    total_attempts = config.retries + 1
    last_error = ""
    attempts = 0
    duration = 0.0
    code = -1
    for attempt in range(1, total_attempts + 1):
        attempts = attempt
        started = time.monotonic()
        code, stdout, stderr, timed_out = _execute(command, directory, config)
        duration = time.monotonic() - started
        _append_log(directory, attempt, total_attempts, command, code, duration, stdout, stderr, timed_out)
        if code == 0 and not timed_out:
            return JobRecord(
                code=case.code,
                slug=case.slug,
                fingerprint=case.fingerprint,
                status="done",
                attempts=attempts,
                exit_code=0,
                duration_s=duration,
            )
        last_error = "超时" if timed_out else f"退出码 {code}"
        tail = _tail(stderr)
        if tail:
            last_error += f"；{tail}"
    return JobRecord(
        code=case.code,
        slug=case.slug,
        fingerprint=case.fingerprint,
        status="failed",
        attempts=attempts,
        exit_code=None if last_error.startswith("超时") else code,
        duration_s=duration,
        error=last_error,
    )


def _execute(command: Sequence[str], cwd: Path, config: SolverConfig) -> tuple[int, str, str, bool]:
    """启动子进程并等待；超时杀进程（POSIX 杀进程组，Windows 杀子进程）。"""
    env = os.environ.copy()
    env.update(config.env)
    kwargs: dict[str, Any] = {"cwd": cwd, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(list(command), text=True, encoding="utf-8", errors="replace", env=env, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=config.timeout_s)
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        _terminate(process)
        stdout, stderr = process.communicate()
        return process.returncode if process.returncode is not None else -1, stdout, stderr, True


def _terminate(process: subprocess.Popen[str]) -> None:
    """尽量结束整棵进程树：POSIX 用进程组，Windows 用 ``kill``。"""
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        with contextlib.suppress(OSError):
            process.kill()


def _append_log(  # noqa: PLR0913, PLR0917 - 日志字段与调用点一一对应，打包反而遮蔽含义
    directory: Path,
    attempt: int,
    total: int,
    command: Sequence[str],
    code: int | None,
    duration: float,
    stdout: str,
    stderr: str,
    timed_out: bool,
) -> None:
    """运行日志：带时间戳，只写工况目录，不影响 deck 复现。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"[{stamp}] attempt {attempt}/{total}",
        f"[{stamp}] command: {subprocess.list2cmdline(list(command))}",
        f"[{stamp}] exit={code} duration={duration:.3f}s timeout={timed_out}",
    ]
    if stdout:
        lines.extend(["--- stdout ---", stdout.rstrip("\n")])
    if stderr:
        lines.extend(["--- stderr ---", stderr.rstrip("\n")])
    lines.append("")
    with (directory / "run.log").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def _tail(text: str, limit: int = 300) -> str:
    """取错误输出最后一段，避免日志/状态文件被长输出撑爆。"""
    stripped = " ".join(text.strip().split())
    return stripped[-limit:]


def format_run_report(report: RunReport, config: SolverConfig) -> str:
    """文本调度报告；dry-run 时逐条列出命令。"""
    counts = {status: report.count(status) for status in _STATUSES}
    header = (
        f"求解器：{'假求解器' if config.fake else config.solver_path or '自动探测'}"
        f"（{config.parallel}/{config.mpi}，ncpu={config.ncpu}，并发={config.concurrency}，"
        f"超时={config.timeout_s}s，重试={config.retries}）"
    )
    lines = [header]
    if report.dry_run:
        lines.append(f"dry-run：共 {len(report.planned)} 个工况，未启动任何进程")
        for item in report.planned:
            lines.append(f"[{item.case.code}] {' '.join(item.command)}")
        return "\n".join(lines) + "\n"
    lines.append(
        "结果："
        f"完成 {counts['done']}，失败 {counts['failed']}，跳过 {counts['skipped']}，"
        f"待运行 {counts['pending']}，运行中 {counts['running']}"
    )
    for item in report.jobs:
        suffix = f"；{item.error}" if item.error else ""
        lines.append(f"[{item.code}] {item.status}（尝试 {item.attempts} 次，{item.duration_s:.3f}s）{suffix}")
    return "\n".join(lines) + "\n"


def config_for_manifest(manifest: Manifest, overrides: Mapping[str, Any] | None = None) -> SolverConfig:
    """从清单 ``run`` 段构造配置（CLI / GUI 共用）。"""
    return SolverConfig.from_mapping(manifest.run, overrides)
