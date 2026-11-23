"""llmserve - 服务生命周期：启动计划、进程拉起、健康检查、停止与日志。

独立命令：``llmserve <plan|start|stop|status|logs>``。

另提供 ``/metrics`` 采样辅助（:func:`fetch_metrics` / :func:`parse_metrics`），
供 ``llmw`` 窗口实时统计 token/s 等性能指标。
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.llm import _llmtune, llmengine, llmenv, llmmodel
from fcmd.apps.llm.llmbase import (
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    KV_SCALE,
    PROFILES,
    VC_RUNTIME_DLLS,
    LaunchPlan,
    Opener,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
    ensure_vc_runtime,
    format_command,
    format_returncode,
    http_base_url,
    human_size,
    now_iso,
    validate_name,
)
from fcmd.console import Table, get_console

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
    "spawn_child",
    "spawn_flags",
    "start_server",
    "stop_server",
    "terminate_process",
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
        _cleanup_engine(state)
        llmenv.delete_state(name, home)
        return StopResult(state, False)
    (terminator or _terminate)(state.pid, state.create_time, timeout)
    _cleanup_engine(state)
    llmenv.delete_state(name, home)
    return StopResult(state, True)


def _cleanup_engine(state: ServeState) -> None:
    """停止后的引擎侧兜底清理（vLLM 在 WSL 内可能残留在服务进程）。"""
    if getattr(state, "engine_kind", "") != "vllm":
        return
    from fcmd.apps.llm import _llmvllm

    with contextlib.suppress(Exception):
        _llmvllm.stop_engine(state)


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
    mmproj: str = "auto",
) -> LaunchPlan:
    """解析模型与引擎，构造启动计划（``alias`` 省略时取模型文件名）。

    ``mmproj`` 支持 ``auto``（按文件名/目录自动配对）、``off`` 或具体 ``.gguf`` 路径。
    """
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
    vision_path = llmmodel.resolve_mmproj(model_path, mmproj)
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
        mmproj=vision_path,
    )


BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
"""Windows：允许子进程脱离父进程所在的 Job Object。"""

_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _in_job() -> bool:  # pragma: no cover - Windows ctypes，Linux CI 不执行
    """当前进程是否在 Windows Job Object 中（终端 / IDE / 测试启动器常见）。

    必须显式声明 64 位 HANDLE 签名：``ctypes`` 默认按 C ``int`` 传参，
    ``GetCurrentProcess()`` 的伪句柄会被截断，导致明明在 Job 里却返回 ``False``，
    进而漏加 ``CREATE_BREAKAWAY_FROM_JOB``，llmw 退出时服务被 Job 一起结束。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        windll_cls: Any = getattr(ctypes, "WinDLL", None)
        if windll_cls is None:  # pragma: no cover - 非 Windows 解释器
            return False
        kernel32 = windll_cls("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        kernel32.IsProcessInJob.restype = ctypes.c_bool
        in_job = ctypes.c_int(0)
        if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)):
            return False
        return bool(in_job.value)
    except Exception:  # pragma: no cover - 非 Windows / 受限环境
        return False


def _creation_flags() -> int:
    """子进程创建标志（Windows 独立进程组 + 脱离控制台 / Job）。"""
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    # 从 IDE / 终端启动时父进程常在 Job 里：加上 breakaway 后 llmw 退出，
    # 服务不会被 Job 的「关闭即杀」策略带走；失败时 spawn_child 会退回原标志。
    if _in_job():
        flags |= BREAKAWAY_FROM_JOB
    return flags


def spawn_flags() -> int:
    """公开包装：子进程创建标志（其他服务复用）。"""
    return _creation_flags()


def spawn_child(argv: list[str], kwargs: dict[str, Any], popen: Callable[..., Any] | None = None) -> Any:
    """拉起子进程；Job 不允许 breakaway 时自动退回不带该标志的创建方式。"""
    factory = popen or _default_popen
    try:
        return factory(argv, **kwargs)
    except OSError:
        flags = int(kwargs.get("creationflags", 0))
        stripped = flags & ~BREAKAWAY_FROM_JOB
        if stripped == flags:
            raise
        kwargs = {**kwargs, "creationflags": stripped}
        return factory(argv, **kwargs)


def terminate_process(pid: int, create_time: float, timeout: float = 10.0) -> None:
    """公开包装：终止进程树（其他服务复用）。"""
    _terminate(pid, create_time, timeout)


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


#: 视觉塔加载失败的日志特征（命中后去掉 ``--mmproj`` 重试一次）
_MMPROJ_FAILURE_MARKERS = ("mmproj", "projector", "clip vision")

_ACCESS_VIOLATION = 0xC0000005
"""Windows 访问冲突退出码：引擎启动即崩溃（空日志）常见于缺新版 MSVC 运行库。"""


_UNKNOWN_ARCH_RE = re.compile(r"unknown model architecture: '([^']+)'")
"""引擎不认识模型架构时的日志特征（旧版 llama.cpp 加载新架构模型）。"""


def _unknown_architecture(text: str) -> str | None:
    """从引擎日志里提取不支持的模型架构名（没有则返回 ``None``）。"""
    match = _UNKNOWN_ARCH_RE.search(text)
    return match.group(1) if match else None


def _engine_exit_error(plan: LaunchPlan, code: int) -> ServeError:
    """把「引擎启动后立即退出」翻译成可操作的错误信息。

    旧版 llama.cpp 遇到新架构（如 ``qwen3moe``）时会在加载阶段退出，日志里写
    ``unknown model architecture``。这时换启动参数没有意义，必须换支持该架构的
    运行时；因此错误信息带上当前引擎 tag、查看可用版本的命令与日志位置，而不是
    写死某个版本号（同一架构也可能根本不由 llama.cpp 支持）。
    """
    tail = _tail_text(plan.log, 12)
    if plan.engine_kind == "vllm":
        return ServeError(
            f"vLLM 启动后立即退出（退出码 {format_returncode(code)}）。"
            f"常见原因：显存不足、模型格式不是 HF safetensors、引擎版本与模型不匹配。"
            f"日志: {plan.log}\n日志尾部:\n{tail}"
        )
    missing = _unknown_architecture(tail)
    if missing is not None:
        return ServeError(
            f"当前引擎不支持该模型架构 {missing!r}（引擎 {plan.engine.tag}）："
            "若该架构需要独立运行时，请安装对应引擎；若新版 llama.cpp 已支持，"
            "可先用 `fcmd llmengine releases` 查看可用版本，再以 "
            "`fcmd llmengine install <版本>` 安装并用 `fcmd llmengine use <版本>` 切换；"
            f"引擎日志: {plan.log}"
        )
    return ServeError(
        f"引擎启动后立即退出（退出码 {format_returncode(code)}）。引擎日志: {plan.log}\n日志尾部:\n{tail}"
        f"{_crash_hint(plan, code)}"
    )


def _crash_hint(plan: LaunchPlan, code: int) -> str:
    """启动即崩溃且日志为空时提示 MSVC 运行库（旧版 msvcp140 会 c0000005）。"""
    if code not in (_ACCESS_VIOLATION, _ACCESS_VIOLATION - 2**32):
        return ""
    if plan.engine_kind:
        return ""
    directory = plan.engine.exe.parent
    # 非 Windows 上找不到 VC Redist 来源，复制自然是空操作；失败只影响提示内容。
    with contextlib.suppress(OSError):
        ensure_vc_runtime(directory)
    missing = [name for name in VC_RUNTIME_DLLS if not (directory / name).is_file()]
    detail = f"缺 {', '.join(missing)}" if missing else "目录内运行库版本过旧"
    return (
        f"\n引擎进程启动即崩溃（{format_returncode(code)}，常见于引擎目录的 MSVC 运行库）：{detail}；"
        f"引擎目录 {directory}；安装最新 VC++ 2015-2022 x64 运行库，"
        "或把新版 msvcp140.dll / vcruntime140*.dll 放入引擎目录"
    )


def _mmproj_failure(plan: LaunchPlan) -> bool:
    """日志是否表明视觉塔加载失败（仅在有 mmproj 时判定）。"""
    if plan.mmproj is None:
        return False
    tail = _tail_text(plan.log, 80).lower()
    return any(marker in tail for marker in _MMPROJ_FAILURE_MARKERS)


def _launch_once(  # noqa: PLR0913
    plan: LaunchPlan,
    *,
    wait_s: float = 180.0,
    home: Path | None = None,
    popen: Callable[..., Any] | None = None,
    health: Callable[[str, int, str], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ServeState:
    """拉起一次引擎进程并等待健康检查通过。"""
    plan.log.parent.mkdir(parents=True, exist_ok=True)
    argv = plan.argv()
    flags = _creation_flags()
    if plan.launcher.startswith("wsl:") and os.name == "nt":
        # wsl.exe 在 DETACHED_PROCESS 下不转发 Linux 子进程的 stdout，
        # 服务日志会一直停在 0 字节；换成 CREATE_NO_WINDOW：同样不弹控制台，
        # 但保留输出管道（日志展示与失败诊断都依赖它）。
        flags = (flags & ~_DETACHED_PROCESS) | _CREATE_NO_WINDOW
    kwargs: dict[str, Any] = {
        "cwd": str(plan.engine.exe.parent),
        # 与服务进程解耦：不继承父进程控制台 / stdin，避免窗口关闭后子进程被 EOF 带走。
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
        "creationflags": flags,
    }
    env = llmengine.engine_env(plan.engine)
    if env is not None:
        kwargs["env"] = env
    if os.name != "nt":
        kwargs["start_new_session"] = True
    with plan.log.open("ab") as log_file:
        kwargs["stdout"] = log_file
        try:
            proc = spawn_child(argv, kwargs, popen)
        except OSError as exc:
            raise ServeError(f"启动引擎失败: {exc}（命令: {format_command(argv)}）") from exc

    pid = int(proc.pid)
    create_time = process_create_time(pid)
    probe = health or check_health
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise _engine_exit_error(plan, int(proc.returncode or 0))
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
                engine=(f"vLLM {plan.engine.tag}" if plan.engine_kind == "vllm" else str(plan.engine.exe)),
                alias=plan.alias,
                api_key=plan.api_key,
                log=str(plan.log),
                started_at=now_iso(),
                mmproj=str(plan.mmproj or ""),
                vision=plan.vision,
                engine_kind=plan.engine_kind,
                launcher=plan.launcher,
            )
            llmenv.save_state(state, home)
            return state
        sleep(1.0)

    with contextlib.suppress(ServeError):
        _terminate(pid, create_time, 5.0)
    raise ServeError(
        f"健康检查超时（{wait_s:.0f}s）：{http_base_url(plan.host, plan.port)}/health。"
        f"模型可能仍在加载，可加大等待秒数；引擎日志: {plan.log}"
    )


def launch(  # noqa: PLR0913
    plan: LaunchPlan,
    *,
    wait_s: float = 180.0,
    home: Path | None = None,
    popen: Callable[..., Any] | None = None,
    health: Callable[[str, int, str], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    allow_mmproj_fallback: bool = True,
) -> ServeState:
    """启动引擎；视觉塔加载失败时自动去掉 ``--mmproj`` 重试一次。"""
    try:
        return _launch_once(plan, wait_s=wait_s, home=home, popen=popen, health=health, sleep=sleep)
    except ServeError:
        if not allow_mmproj_fallback or not _mmproj_failure(plan):
            raise
        get_console().print("[yellow]视觉塔加载失败，已改为纯文本模式重试一次[/yellow]")
        return _launch_once(
            replace(plan, mmproj=None),
            wait_s=wait_s,
            home=home,
            popen=popen,
            health=health,
            sleep=sleep,
        )


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
    table.add_row("视觉", str(plan.mmproj) if plan.mmproj is not None else "未启用（纯文本）")
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
    table.add_row("视觉", str(state.mmproj) if state.vision else "未启用（纯文本）")
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
    mmproj: str = "auto",
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
        覆盖并发槽（``-np``；视觉模式下强制 1）。
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
    mmproj:
        视觉塔：``auto`` 自动配对、``off`` 关闭、或指定 ``.gguf`` 路径。
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
        mmproj=mmproj,
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
    mmproj: str = "auto",
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
        mmproj=mmproj,
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
