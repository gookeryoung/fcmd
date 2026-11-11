"""llmserve - 服务生命周期：启动计划、进程拉起、健康检查、停止与日志。

独立命令：``llmserve <plan|start|stop|status|logs>``。

另提供 ``/metrics`` 采样辅助（:func:`fetch_metrics` / :func:`parse_metrics`），
供 ``llmservew`` 窗口实时统计 token/s 等性能指标。
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.console import Table, get_console
from fcmd.tools.llm import _llmtune, llmengine, llmenv, llmmodel
from fcmd.tools.llm.llmbase import (
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    KV_SCALE,
    PROFILES,
    LaunchPlan,
    Opener,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
    format_command,
    format_returncode,
    http_base_url,
    human_size,
    now_iso,
    validate_name,
)

__all__ = [
    "ServeMetrics",
    "check_health",
    "fetch_metrics",
    "launch",
    "make_plan",
    "parse_metrics",
    "parse_prometheus",
    "process_alive",
    "process_create_time",
    "read_log",
    "start_server",
    "stop_server",
]

#: llama.cpp ``/metrics`` 中与实时性能相关的指标名
METRIC_KEYS: dict[str, str] = {
    "prompt_tokens": "llamacpp:prompt_tokens_total",
    "predicted_tokens": "llamacpp:tokens_predicted_total",
    "processing": "llamacpp:requests_processing",
    "deferred": "llamacpp:requests_deferred",
    "kv_usage": "llamacpp:kv_cache_usage_ratio",
}


@dataclass(frozen=True, slots=True)
class ServeMetrics:
    """``/metrics`` 采样结果（累计 token 数与当前请求队列状态）。"""

    prompt_tokens: float = 0.0
    predicted_tokens: float = 0.0
    processing: float = 0.0
    deferred: float = 0.0
    kv_usage: float = 0.0


def parse_prometheus(text: str) -> dict[str, float]:
    """解析 Prometheus 文本格式（忽略注释 / 标签 / 非法值）。"""
    values: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition(" ")
        if not rest.strip():
            continue
        name = name.split("{", 1)[0]
        try:
            values[name] = float(rest.split()[0])
        except ValueError:
            continue
    return values


def parse_metrics(text: str) -> ServeMetrics:
    """从 Prometheus 文本中提取服务性能指标（缺失的指标记为 0）。"""
    values = parse_prometheus(text)
    return ServeMetrics(
        prompt_tokens=values.get(METRIC_KEYS["prompt_tokens"], 0.0),
        predicted_tokens=values.get(METRIC_KEYS["predicted_tokens"], 0.0),
        processing=values.get(METRIC_KEYS["processing"], 0.0),
        deferred=values.get(METRIC_KEYS["deferred"], 0.0),
        kv_usage=values.get(METRIC_KEYS["kv_usage"], 0.0),
    )


def fetch_metrics(
    host: str,
    port: int,
    api_key: str | None = None,
    *,
    timeout: float = 2.0,
    opener: Opener | None = None,
) -> ServeMetrics:
    """请求 ``/metrics`` 读取实时性能指标（失败抛 :class:`ServeError`）。"""
    from urllib.request import Request, urlopen

    url = http_base_url(host, port) + "/metrics"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    open_fn = opener or (lambda request, seconds: urlopen(request, timeout=seconds))
    try:
        with open_fn(Request(url, headers=headers), timeout) as resp:
            status = getattr(resp, "status", 200)
            payload = resp.read()
    except OSError as exc:
        raise ServeError(f"读取性能指标失败: {exc}") from exc
    if status != 200:
        raise ServeError(f"读取性能指标失败: HTTP {status}")
    return parse_metrics(payload.decode("utf-8", errors="replace"))


def process_create_time(pid: int) -> float:
    """查询进程创建时间（拿不到时返回 ``0.0``，表示不做复用校验）。"""
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return 0.0


def process_alive(pid: int, create_time: float = 0.0) -> bool:
    """判断 PID 是否仍存活，并校验创建时间避免 PID 复用误判。"""
    import psutil

    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        return not (create_time and abs(proc.create_time() - create_time) > 2.0)
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def _terminate(pid: int, create_time: float, timeout: float) -> None:
    """终止进程树（先子后父，超时后强杀）。"""
    import psutil

    try:
        proc = psutil.Process(pid)
        if create_time and abs(proc.create_time() - create_time) > 2.0:
            raise ServeError(f"PID {pid} 已被其他进程复用，拒绝终止")
        children = proc.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied as exc:
        raise ServeError(f"终止进程 {pid} 权限不足: {exc}") from exc

    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue
    try:
        proc.terminate()
    except psutil.NoSuchProcess:
        return
    _, alive = psutil.wait_procs([proc, *children], timeout=timeout)
    for item in alive:
        try:
            item.kill()
        except psutil.NoSuchProcess:
            continue


def stop_server(
    name: str,
    home: Path | None = None,
    *,
    timeout: float = 10.0,
    terminator: Callable[[int, float, float], None] | None = None,
    alive: Callable[[int, float], bool] | None = None,
) -> StopResult:
    """停止服务并清理状态文件（进程已退出时也清理残留状态）。"""
    state = llmenv.require_state(name, home)
    if not (alive or process_alive)(state.pid, state.create_time):
        llmenv.delete_state(name, home)
        return StopResult(state, False)
    (terminator or _terminate)(state.pid, state.create_time, timeout)
    llmenv.delete_state(name, home)
    return StopResult(state, True)


def make_plan(  # noqa: PLR0913
    name: str = DEFAULT_NAME,
    profile: ProfileName = DEFAULT_PROFILE,
    *,
    model: Path | None = None,
    models_root: Path | None = None,
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    ctx: int | None = None,
    kv_type: str | None = None,
    ubatch: int | None = None,
    parallel: int | None = None,
    threads_http: int | None = None,
    engine_tag: str | None = None,
    alias: str | None = None,
    api_key: str = DEFAULT_API_KEY,
    threads: int = DEFAULT_THREADS,
    home: Path | None = None,
    extra_args: Sequence[str] = (),
    tune: str = "default",
    gpu_mib: int | None = None,
) -> LaunchPlan:
    """解析模型与引擎，构造启动计划（``alias`` 省略时取模型文件名）。"""
    validate_name(name)
    prof = PROFILES.get(profile)
    if prof is None:
        raise ServeError(f"未知档位 {profile!r}，可选: {', '.join(PROFILES)}")
    if not 1 <= port <= 65535:
        raise ServeError(f"端口不合法: {port}")
    if kv_type is not None and kv_type not in KV_SCALE:
        raise ServeError(f"不支持的 KV 类型 {kv_type!r}，可选: {', '.join(KV_SCALE)}")
    if ubatch is not None and ubatch < 1:
        raise ServeError(f"ubatch 必须为正整数: {ubatch}")
    if parallel is not None and parallel < 1:
        raise ServeError(f"并发槽必须为正整数: {parallel}")
    if threads_http is not None and threads_http < 1:
        raise ServeError(f"HTTP 线程数必须为正整数: {threads_http}")
    model_path = llmmodel.find_model(prof.quant, models_root, model)
    engine = llmengine.resolve_engine(engine_tag, home)
    context = ctx or prof.context
    if tune == "auto" and gpu_mib is None:
        gpu = llmengine.detect_gpu()
        gpu_mib = gpu.memory_mib if gpu is not None else None
    tuning = _llmtune.resolve(tune, context=context, model_bytes=model_path.stat().st_size, gpu_mib=gpu_mib)
    return LaunchPlan(
        name=name,
        model=model_path,
        profile=prof,
        context=context,
        host=host,
        port=port,
        alias=alias if alias is not None else model_path.stem,
        api_key=api_key,
        threads=threads,
        engine=engine,
        log=llmenv.log_path(name, home),
        extra_args=tuple(extra_args),
        kv_type=kv_type,
        ubatch=ubatch,
        parallel=parallel,
        threads_http=threads_http,
        tuning=tuning,
    )


def _creation_flags() -> int:
    """子进程创建标志（Windows 独立进程组 + 脱离控制台）。"""
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags | getattr(subprocess, "DETACHED_PROCESS", 0)


def _default_popen(argv: list[str], **kwargs: Any) -> Any:
    return subprocess.Popen(argv, **kwargs)


def check_health(
    host: str,
    port: int,
    api_key: str | None = None,
    *,
    timeout: float = 2.0,
    opener: Opener | None = None,
) -> bool:
    """请求 ``/health`` 判断服务是否就绪。"""
    from urllib.request import Request, urlopen

    url = http_base_url(host, port) + "/health"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    open_fn = opener or (lambda request, seconds: urlopen(request, timeout=seconds))
    try:
        with open_fn(Request(url, headers=headers), timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except OSError:
        return False


def _tail_text(path: Path, lines: int = 20) -> str:
    """读取文件末尾若干行（失败返回空串）。"""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def launch(  # noqa: PLR0913
    plan: LaunchPlan,
    *,
    wait_s: float = 180.0,
    home: Path | None = None,
    popen: Callable[..., Any] | None = None,
    health: Callable[[str, int, str], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ServeState:
    """启动引擎进程，等待健康检查通过后落盘状态。"""
    plan.log.parent.mkdir(parents=True, exist_ok=True)
    argv = plan.argv()
    kwargs: dict[str, Any] = {
        "cwd": str(plan.engine.exe.parent),
        "stderr": subprocess.STDOUT,
        "creationflags": _creation_flags(),
    }
    env = llmengine.engine_env(plan.engine)
    if env is not None:
        kwargs["env"] = env
    if os.name != "nt":
        kwargs["start_new_session"] = True
    with plan.log.open("ab") as log_file:
        kwargs["stdout"] = log_file
        try:
            proc = (popen or _default_popen)(argv, **kwargs)
        except OSError as exc:
            raise ServeError(f"启动引擎失败: {exc}（命令: {format_command(argv)}）") from exc

    pid = int(proc.pid)
    create_time = process_create_time(pid)
    probe = health or check_health
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise ServeError(
                f"引擎启动后立即退出（退出码 {format_returncode(int(proc.returncode or 0))}）。"
                f"日志尾部:\n{_tail_text(plan.log)}"
            )
        if probe(plan.host, plan.port, plan.api_key):
            state = ServeState(
                name=plan.name,
                pid=pid,
                create_time=create_time,
                host=plan.host,
                port=plan.port,
                model=str(plan.model),
                profile=plan.profile.name,
                context=plan.context,
                engine=str(plan.engine.exe),
                alias=plan.alias,
                api_key=plan.api_key,
                log=str(plan.log),
                started_at=now_iso(),
            )
            llmenv.save_state(state, home)
            return state
        sleep(1.0)

    with contextlib.suppress(ServeError):
        _terminate(pid, create_time, 5.0)
    raise ServeError(f"健康检查超时（{wait_s:.0f}s）：{http_base_url(plan.host, plan.port)}/health")


def start_server(  # noqa: PLR0913
    plan: LaunchPlan,
    *,
    wait_s: float = 180.0,
    home: Path | None = None,
    popen: Callable[..., Any] | None = None,
    health: Callable[[str, int, str], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    port_in_use: Callable[[int, str], bool] | None = None,
) -> ServeState:
    """启动服务：校验同名服务 / 端口，然后拉起引擎并等待就绪。"""
    existing = llmenv.load_state(plan.name, home, missing_ok=True)
    if existing is not None:
        if process_alive(existing.pid, existing.create_time):
            raise ServeError(
                f"服务 {plan.name!r} 已在运行（pid={existing.pid}），先执行 fcmd llmserve stop {plan.name}"
            )
        llmenv.delete_state(plan.name, home)

    if port_in_use is None:
        from fcmd.infras.net import is_port_in_use

        port_in_use = is_port_in_use
    if port_in_use(plan.port, plan.host):
        raise ServeError(
            f"端口 {plan.port} 已被占用；换端口用 --port，或先停止占用进程（fcmd portcheck c {plan.port}）"
        )

    return launch(plan, wait_s=wait_s, home=home, popen=popen, health=health, sleep=sleep)


def read_log(name: str, lines: int = 50, home: Path | None = None) -> str:
    """读取服务日志末尾若干行。"""
    path = llmenv.log_path(name, home)
    if not path.is_file():
        raise ServeError(f"日志不存在: {path}")
    return _tail_text(path, lines)


def _print_plan(plan: LaunchPlan) -> None:
    """打印启动计划与显存估算。"""
    table = Table(title=f"启动计划: {plan.name}", box="ascii")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("值")
    table.add_row("引擎", f"{plan.engine.exe}（{plan.engine.tag}）")
    table.add_row("模型", str(plan.model))
    table.add_row(
        "档位",
        f"{plan.profile.name} / {plan.profile.quant} / kv {plan.effective_kv_type} / "
        f"ctx {plan.context} / ub {plan.effective_ubatch}",
    )
    table.add_row(
        "调优",
        f"{plan.tuning.label} / b{plan.tuning.batch} / np{plan.effective_parallel} / "
        f"th{plan.effective_threads_http or '-'} / cr{plan.tuning.cache_reuse} / fa {plan.tuning.flash_attn}",
    )
    table.add_row("调优说明", plan.tuning.detail)
    table.add_row("监听", http_base_url(plan.host, plan.port))
    estimate = llmmodel.plan_vram(plan)
    table.add_row("显存估算", human_size(estimate))
    table.add_row("日志", str(plan.log))
    get_console().print(table)
    get_console().print("命令: " + format_command(plan.argv()))
    gpu = llmengine.detect_gpu()
    if gpu is not None and estimate > gpu.memory_mib * 1024 * 1024:
        get_console().print(
            f"[yellow]警告: 估算 {human_size(estimate)} 超过显存 {gpu.memory_mib} MiB，"
            "可能 OOM；可改用 --profile speed、--tune low-vram 或减小 --ctx[/yellow]"
        )


def _print_state(state: ServeState) -> None:
    """打印服务状态详情。"""
    alive = process_alive(state.pid, state.create_time)
    table = Table(title=f"服务: {state.name}", box="ascii")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("值")
    table.add_row("状态", "[green]运行中[/green]" if alive else "[red]已退出[/red]")
    table.add_row("地址", state.base_url)
    table.add_row("模型", state.model)
    table.add_row("档位", f"{state.profile} / ctx {state.context}")
    table.add_row("PID", str(state.pid))
    table.add_row("启动时间", state.started_at)
    table.add_row("日志", state.log)
    get_console().print(table)


@fcmd.tool("llmserve", subcommand="plan", help="打印启动计划（命令 + 显存估算），不启动")
def plan_cmd(  # noqa: PLR0913, PLR0917
    name: str = DEFAULT_NAME,
    profile: ProfileName = DEFAULT_PROFILE,
    model: Path | None = None,
    models_root: Path | None = None,
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    ctx: int | None = None,
    kv_type: str | None = None,
    ubatch: int | None = None,
    parallel: int | None = None,
    threads_http: int | None = None,
    engine_tag: str | None = None,
    alias: str | None = None,
    api_key: str = DEFAULT_API_KEY,
    threads: int = DEFAULT_THREADS,
    tune: str = "default",
) -> None:
    """打印启动计划，便于确认参数与显存占用。

    Parameters
    ----------
    name:
        服务名（决定状态与日志文件），默认 ``qwen``。
    profile:
        档位：``speed`` / ``balanced`` / ``long`` / ``max-context``。
    model:
        GGUF 文件或包含 GGUF 的目录；默认按档位量化在模型根目录查找。
    models_root:
        只在该目录查找；默认用 ``FCMD_MODELS``、配置文件或内置默认目录。
    port:
        监听端口（默认 8080）。
    host:
        监听地址（默认 127.0.0.1）。
    ctx:
        覆盖档位上下文长度。
    kv_type:
        覆盖 KV 缓存类型（``f16`` / ``q8_0`` / ``q4_0``）。
    ubatch:
        覆盖 ubatch。
    parallel:
        覆盖并发槽（``-np``）。
    threads_http:
        覆盖 HTTP 线程数（``--threads-http``）。
    engine_tag:
        指定引擎 tag，默认 current / 最新安装。
    alias:
        OpenAI 模型名；省略时取模型文件名（不含 ``.gguf``）。
    api_key:
        本地 API Key。
    threads:
        CPU 线程数。
    tune:
        调优策略：``auto`` / ``default`` / ``throughput`` / ``long-context`` / ``low-vram``。
    """
    plan = make_plan(
        name,
        profile,
        model=model,
        models_root=models_root,
        port=port,
        host=host,
        ctx=ctx,
        kv_type=kv_type,
        ubatch=ubatch,
        parallel=parallel,
        threads_http=threads_http,
        engine_tag=engine_tag,
        alias=alias,
        api_key=api_key,
        threads=threads,
        tune=tune,
    )
    _print_plan(plan)


@fcmd.tool("llmserve", subcommand="start", help="启动推理服务（等待健康检查通过）")
def start_cmd(  # noqa: PLR0913, PLR0917
    name: str = DEFAULT_NAME,
    profile: ProfileName = DEFAULT_PROFILE,
    model: Path | None = None,
    models_root: Path | None = None,
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    ctx: int | None = None,
    kv_type: str | None = None,
    ubatch: int | None = None,
    parallel: int | None = None,
    threads_http: int | None = None,
    engine_tag: str | None = None,
    alias: str | None = None,
    api_key: str = DEFAULT_API_KEY,
    threads: int = DEFAULT_THREADS,
    tune: str = "default",
    wait: float = 180.0,
) -> None:
    """启动服务并等待 ``/health`` 就绪。

    参数含义同 ``llmserve plan``；``wait`` 为等待模型加载的秒数。
    """
    plan = make_plan(
        name,
        profile,
        model=model,
        models_root=models_root,
        port=port,
        host=host,
        ctx=ctx,
        kv_type=kv_type,
        ubatch=ubatch,
        parallel=parallel,
        threads_http=threads_http,
        engine_tag=engine_tag,
        alias=alias,
        api_key=api_key,
        threads=threads,
        tune=tune,
    )
    _print_plan(plan)
    state = start_server(plan, wait_s=wait)
    _print_state(state)


@fcmd.tool("llmserve", subcommand="stop", help="停止推理服务并清理状态")
def stop_cmd(name: str = DEFAULT_NAME) -> None:
    """停止服务（进程已退出时清理残留状态）。

    Parameters
    ----------
    name:
        服务名，默认 ``qwen``。
    """
    result = stop_server(name)
    if result.was_running:
        get_console().print(f"已停止服务 {name}（pid={result.state.pid}）")
    else:
        get_console().print(f"服务 {name} 未在运行，已清理残留状态")


@fcmd.tool("llmserve", subcommand="status", help="查看服务状态（不指定 name 时列出全部）")
def status_cmd(name: str | None = None) -> None:
    """查看服务进程与健康状态。

    Parameters
    ----------
    name:
        服务名；省略时列出全部已登记服务。
    """
    if name:
        _print_state(llmenv.require_state(name))
        return

    states = llmenv.list_states()
    if not states:
        get_console().print("没有已登记的服务：fcmd llmserve start")
        return
    table = Table(title="服务列表", box="ascii")
    table.add_column("服务", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("地址", no_wrap=True)
    table.add_column("档位", no_wrap=True)
    table.add_column("PID", justify="right", no_wrap=True)
    for state in states:
        alive = process_alive(state.pid, state.create_time)
        table.add_row(
            state.name,
            "[green]运行中[/green]" if alive else "[red]已退出[/red]",
            state.base_url,
            f"{state.profile}/ctx{state.context}",
            str(state.pid),
        )
    get_console().print(table)


@fcmd.tool("llmserve", subcommand="logs", help="查看服务日志末尾若干行")
def logs_cmd(name: str = DEFAULT_NAME, lines: int = 50) -> None:
    """打印服务日志尾部。

    Parameters
    ----------
    name:
        服务名，默认 ``qwen``。
    lines:
        打印的行数（默认 50）。
    """
    get_console().print(read_log(name, lines), markup=False)


@fcmd.main("llmserve")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行
