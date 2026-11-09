"""llmserve - 服务生命周期：启动计划、进程拉起、健康检查、停止与日志。"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from fcmd.tools.llm import llmengine, llmenv, llmmodel
from fcmd.tools.llm.llmbase import (
    DEFAULT_ALIAS,
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
    now_iso,
    validate_name,
)

__all__ = [
    "check_health",
    "launch",
    "make_plan",
    "process_alive",
    "process_create_time",
    "read_log",
    "start_server",
    "stop_server",
]


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
    engine_tag: str | None = None,
    alias: str = DEFAULT_ALIAS,
    api_key: str = DEFAULT_API_KEY,
    threads: int = DEFAULT_THREADS,
    home: Path | None = None,
    extra_args: Sequence[str] = (),
) -> LaunchPlan:
    """解析模型与引擎，构造启动计划。"""
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
    model_path = llmmodel.find_model(prof.quant, models_root, model)
    engine = llmengine.resolve_engine(engine_tag, home)
    return LaunchPlan(
        name=name,
        model=model_path,
        profile=prof,
        context=ctx or prof.context,
        host=host,
        port=port,
        alias=alias,
        api_key=api_key,
        threads=threads,
        engine=engine,
        log=llmenv.log_path(name, home),
        extra_args=tuple(extra_args),
        kv_type=kv_type,
        ubatch=ubatch,
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
            raise ServeError(f"服务 {plan.name!r} 已在运行（pid={existing.pid}），先执行 fcmd llmsrv stop {plan.name}")
        llmenv.delete_state(plan.name, home)

    if port_in_use is None:
        from fcmd.tools.net.portcheck import is_port_in_use

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
