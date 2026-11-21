"""_llmpaint_server - sd-server 的启动、健康检查、状态与文生图客户端。

单实例模型：状态固定写 ``${FCMD_HOME}/paint/server.json``，日志追加到
``paint/logs/sd-server.log``。进程拉起 / 终止复用 :mod:`fcmd.apps.llm.llmserve`
的创建标志与进程树终止逻辑；网络请求全部支持注入 ``opener``，便于离网测试。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.llm import _llmconfig, _llmpaint, llmserve
from fcmd.apps.llm._llmpaint_engine import SdEngine, resolve_sd_engine, sd_engine_env
from fcmd.apps.llm.llmbase import (
    Opener,
    ServeError,
    format_command,
    format_returncode,
    http_base_url,
    now_iso,
)
from fcmd.infras.jsonio import read_json_file, write_json_atomic

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "PaintPlan",
    "PaintState",
    "PaintStopResult",
    "check_paint_health",
    "default_out_dir",
    "fetch_progress",
    "fingerprint",
    "interrupt",
    "launch_paint",
    "load_state",
    "log_path",
    "make_paint_plan",
    "plan_argv",
    "progress_value",
    "save_state",
    "start_paint",
    "state_path",
    "status",
    "stop_paint",
    "txt2img",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1234
SERVER_STATE_FILE = "server.json"
SERVER_LOG_FILE = "logs/sd-server.log"


@dataclass(frozen=True, slots=True)
class PaintPlan:
    """一次启动所需的全部参数。"""

    engine: SdEngine
    components: _llmpaint.PaintComponents
    log: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    wait_s: float = 300.0


@dataclass(frozen=True, slots=True)
class PaintState:
    """落盘的画图服务状态。"""

    pid: int
    create_time: float
    host: str
    port: int
    engine_tag: str
    exe: str
    model: str
    components: dict[str, str]
    fingerprint: tuple[str, ...]
    log: str
    started_at: str

    @property
    def base_url(self) -> str:
        return http_base_url(self.host, self.port)


@dataclass(frozen=True, slots=True)
class PaintStopResult:
    """停止结果：``stopped`` 为假表示本来就不在运行。"""

    state: PaintState | None
    stopped: bool


def state_path(home: Path | None = None) -> Path:
    return _llmconfig.paint_dir(home) / SERVER_STATE_FILE


def log_path(home: Path | None = None) -> Path:
    return _llmconfig.paint_dir(home) / SERVER_LOG_FILE


def default_out_dir(home: Path | None = None) -> Path:
    return _llmconfig.paint_dir(home) / "out"


def fingerprint(plan: PaintPlan) -> tuple[str, ...]:
    """组件 / 参数指纹：变化即需要重启服务。"""
    components = plan.components
    optional = {role: "" if path is None else str(path) for role, path in _optional_paths(components)}
    flags = _llmpaint.server_flags(components)
    return (
        plan.engine.tag,
        str(plan.engine.exe),
        plan.host,
        str(plan.port),
        _llmpaint.effective_mode(components),
        "" if components.model is None else str(components.model),
        *optional.values(),
        ",".join(components.extra_args),
        ",".join(flags),
    )


def _optional_paths(components: _llmpaint.PaintComponents) -> tuple[tuple[str, Path | None], ...]:
    return (
        ("vae", components.vae),
        ("clip_l", components.clip_l),
        ("clip_g", components.clip_g),
        ("t5xxl", components.t5xxl),
        ("llm", components.llm),
    )


def plan_argv(plan: PaintPlan) -> list[str]:
    """构造 ``sd-server`` 启动命令（模型组件在启动时加载）。"""
    return [
        str(plan.engine.exe),
        "--listen-ip",
        plan.host,
        "--listen-port",
        str(plan.port),
        *_llmpaint.build_model_args(plan.components),
    ]


def make_paint_plan(  # noqa: PLR0913 - 与 make_plan 对齐的入口参数
    components: _llmpaint.PaintComponents,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    engine_tag: str | None = None,
    home: Path | None = None,
    wait_s: float = 300.0,
) -> PaintPlan:
    """解析引擎并构造启动计划。"""
    if not 1 <= port <= 65535:
        raise ServeError(f"端口不合法: {port}")
    return PaintPlan(
        engine=resolve_sd_engine(engine_tag, home),
        components=components,
        log=log_path(home),
        host=host,
        port=port,
        wait_s=wait_s,
    )


def _state_dict(state: PaintState) -> dict[str, Any]:
    return {
        "pid": state.pid,
        "create_time": state.create_time,
        "host": state.host,
        "port": state.port,
        "engine_tag": state.engine_tag,
        "exe": state.exe,
        "model": state.model,
        "components": state.components,
        "fingerprint": list(state.fingerprint),
        "log": state.log,
        "started_at": state.started_at,
    }


def _state_from(raw: Any) -> PaintState | None:
    if not isinstance(raw, dict):
        return None
    pid = raw.get("pid")
    create_time = raw.get("create_time")
    host = raw.get("host")
    port = raw.get("port")
    if not isinstance(pid, int) or isinstance(pid, bool) or not isinstance(create_time, (int, float)):
        return None
    if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
        return None
    components = raw.get("components")
    fingerprint_raw = raw.get("fingerprint")
    return PaintState(
        pid=pid,
        create_time=float(create_time),
        host=host,
        port=port,
        engine_tag=str(raw.get("engine_tag") or ""),
        exe=str(raw.get("exe") or ""),
        model=str(raw.get("model") or ""),
        components={str(key): str(value) for key, value in components.items()} if isinstance(components, dict) else {},
        fingerprint=tuple(str(item) for item in fingerprint_raw) if isinstance(fingerprint_raw, list) else (),
        log=str(raw.get("log") or ""),
        started_at=str(raw.get("started_at") or ""),
    )


def load_state(home: Path | None = None, *, missing_ok: bool = True) -> PaintState | None:
    """读取画图服务状态；缺失 / 损坏时返回 ``None``（或抛错）。"""
    path = state_path(home)
    try:
        raw = read_json_file(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ServeError(f"画图服务状态不存在: {path}") from None
    except (OSError, ValueError):
        if missing_ok:
            return None
        raise ServeError(f"画图服务状态损坏: {path}") from None
    state = _state_from(raw)
    if state is None and not missing_ok:
        raise ServeError(f"画图服务状态损坏: {path}")
    return state


def save_state(state: PaintState, home: Path | None = None) -> Path:
    path = state_path(home)
    write_json_atomic(path, _state_dict(state))
    return path


def delete_state(home: Path | None = None) -> None:
    state_path(home).unlink(missing_ok=True)


def _tail_text(path: Path, lines: int = 20) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _http_status(resp: Any) -> int:
    raw = getattr(resp, "status", None)
    if raw is None:
        raw = getattr(resp, "code", 200)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 200


def _open(opener: Opener | None) -> Callable[..., Any]:
    if opener is not None:
        return opener
    from urllib.request import urlopen

    return lambda request, seconds: urlopen(request, timeout=seconds)


def _get_status(url: str, opener: Opener | None, timeout: float) -> int | None:
    from urllib.request import Request

    try:
        with _open(opener)(Request(url), timeout) as resp:
            return _http_status(resp)
    except OSError:
        return None


def check_paint_health(host: str, port: int, opener: Opener | None = None, *, timeout: float = 2.0) -> bool:
    """探测服务是否就绪：``/health`` 失败时回退 ``/sdapi/v1/sd-models``。"""
    base = http_base_url(host, port)
    for path in ("/health", "/sdapi/v1/sd-models"):
        status = _get_status(base + path, opener, timeout)
        if status is not None and 200 <= status < 300:
            return True
    return False


def _engine_exit_error(plan: PaintPlan, code: int) -> ServeError:
    tail = _tail_text(plan.log, 12)
    return ServeError(
        f"画图引擎启动后立即退出（退出码 {format_returncode(code)}）。引擎日志: {plan.log}\n日志尾部:\n{tail}"
    )


def launch_paint(
    plan: PaintPlan,
    *,
    home: Path | None = None,
    popen: Callable[..., Any] | None = None,
    health: Callable[[str, int], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> PaintState:
    """拉起 ``sd-server`` 并等待健康检查通过。"""
    plan.log.parent.mkdir(parents=True, exist_ok=True)
    argv = plan_argv(plan)
    kwargs: dict[str, Any] = {
        "cwd": str(plan.engine.exe.parent),
        "stderr": subprocess.STDOUT,
        "creationflags": llmserve.spawn_flags(),
    }
    env = sd_engine_env(plan.engine)
    if env is not None:
        kwargs["env"] = env
    if os.name != "nt":
        kwargs["start_new_session"] = True
    with plan.log.open("ab") as log_file:
        kwargs["stdout"] = log_file
        try:
            proc = (popen or subprocess.Popen)(argv, **kwargs)
        except OSError as exc:
            raise ServeError(f"启动画图引擎失败: {exc}（命令: {format_command(argv)}）") from exc

    pid = int(proc.pid)
    create_time = llmserve.process_create_time(pid)
    probe = health or check_paint_health
    deadline = time.monotonic() + plan.wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise _engine_exit_error(plan, int(proc.returncode or 0))
        if probe(plan.host, plan.port):
            state = PaintState(
                pid=pid,
                create_time=create_time,
                host=plan.host,
                port=plan.port,
                engine_tag=plan.engine.tag,
                exe=str(plan.engine.exe),
                model="" if plan.components.model is None else str(plan.components.model),
                components={role: str(path) for role, path in _optional_paths(plan.components) if path is not None},
                fingerprint=fingerprint(plan),
                log=str(plan.log),
                started_at=now_iso(),
            )
            save_state(state, home)
            return state
        sleep(1.0)

    with contextlib.suppress(ServeError):
        llmserve.terminate_process(pid, create_time, 5.0)
    raise ServeError(
        f"画图服务健康检查超时（{plan.wait_s:.0f}s）：{http_base_url(plan.host, plan.port)}/health。"
        f"模型可能仍在加载，可加大等待秒数；引擎日志: {plan.log}"
    )


def start_paint(  # noqa: PLR0913 - 与 start_server 对齐的入口参数
    plan: PaintPlan,
    *,
    home: Path | None = None,
    popen: Callable[..., Any] | None = None,
    health: Callable[[str, int], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    port_in_use: Callable[[int, str], bool] | None = None,
) -> PaintState:
    """启动服务：校验已有状态与端口，然后拉起引擎并等待就绪。"""
    existing = load_state(home)
    if existing is not None:
        if llmserve.process_alive(existing.pid, existing.create_time):
            raise ServeError(f"画图服务已在运行（pid={existing.pid}），先执行 fcmd llmpaint stop")
        delete_state(home)

    if port_in_use is None:
        from fcmd.infras.net import is_port_in_use

        port_in_use = is_port_in_use
    if port_in_use(plan.port, plan.host):
        raise ServeError(
            f"端口 {plan.port} 已被占用；换端口用 --port，或先停止占用进程（fcmd portcheck c {plan.port}）"
        )
    return launch_paint(plan, home=home, popen=popen, health=health, sleep=sleep)


def stop_paint(
    home: Path | None = None,
    *,
    timeout: float = 10.0,
    terminator: Callable[[int, float, float], None] | None = None,
    alive: Callable[[int, float], bool] | None = None,
) -> PaintStopResult:
    """停止画图服务并清理状态（进程已退出时也清理残留状态）。"""
    state = load_state(home)
    if state is None:
        return PaintStopResult(None, False)
    if not (alive or llmserve.process_alive)(state.pid, state.create_time):
        delete_state(home)
        return PaintStopResult(state, False)
    (terminator or llmserve.terminate_process)(state.pid, state.create_time, timeout)
    delete_state(home)
    return PaintStopResult(state, True)


def status(home: Path | None = None) -> tuple[PaintState | None, bool]:
    """返回（状态, 是否存活）；状态缺失时返回 ``(None, False)``。"""
    state = load_state(home)
    if state is None:
        return None, False
    return state, llmserve.process_alive(state.pid, state.create_time)


def _post(
    url: str,
    payload: dict[str, object],
    opener: Opener | None,
    timeout: float,
) -> tuple[int, str]:
    """POST JSON，返回（状态码, 响应文本）；连接失败抛 :class:`ServeError`。"""
    from urllib.error import HTTPError
    from urllib.request import Request

    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with _open(opener)(request, timeout) as resp:
            return _http_status(resp), resp.read().decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        raise ServeError(f"画图服务请求失败: {url}（{exc}）") from exc


def _retry_without_sampler(status_code: int, body: str) -> bool:
    text = body.lower()
    return status_code in {400, 422} and any(
        word in text for word in ("sampler", "scheduler", "unknown field", "unexpected")
    )


def txt2img(
    host: str,
    port: int,
    payload: dict[str, object],
    *,
    timeout: float = 600.0,
    opener: Opener | None = None,
) -> list[str]:
    """调用 ``POST /sdapi/v1/txt2img``，返回 base64 图片列表。"""
    url = http_base_url(host, port) + "/sdapi/v1/txt2img"
    status_code, body = _post(url, payload, opener, timeout)
    if (
        not 200 <= status_code < 300
        and _retry_without_sampler(status_code, body)
        and ("sampler_name" in payload or "scheduler" in payload)
    ):
        minimal = {key: value for key, value in payload.items() if key not in {"sampler_name", "scheduler"}}
        status_code, body = _post(url, minimal, opener, timeout)
    if not 200 <= status_code < 300:
        raise ServeError(f"画图接口返回 HTTP {status_code}: {body.strip()[:300]}")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ServeError(f"画图接口返回的不是 JSON：{body.strip()[:200]}") from exc
    images = data.get("images") if isinstance(data, dict) else None
    if not isinstance(images, list) or not images:
        raise ServeError("画图接口没有返回 images")
    result = [item for item in images if isinstance(item, str) and item.strip()]
    if not result:
        raise ServeError("画图接口返回的 images 为空")
    return result


def _get_json(url: str, opener: Opener | None, timeout: float) -> Any | None:
    from urllib.request import Request

    try:
        with _open(opener)(Request(url), timeout) as resp:
            if not 200 <= _http_status(resp) < 300:
                return None
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None


def fetch_progress(
    host: str, port: int, opener: Opener | None = None, *, timeout: float = 2.0
) -> dict[str, Any] | None:
    """读取 ``/sdapi/v1/progress``；服务不支持时返回 ``None``。"""
    payload = _get_json(http_base_url(host, port) + "/sdapi/v1/progress", opener, timeout)
    return payload if isinstance(payload, dict) else None


def progress_value(payload: Any) -> float | None:
    """从进度响应里取 0–1 的进度值（兼容 ``progress`` / ``percent``）。"""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("progress", payload.get("percent"))
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    value = float(raw)
    if value > 1:
        value = value / 100
    return min(max(value, 0.0), 1.0)


def interrupt(host: str, port: int, opener: Opener | None = None, *, timeout: float = 5.0) -> bool:
    """请求中断当前生成；服务不支持时返回 ``False``。"""
    status_code, _ = _post(http_base_url(host, port) + "/sdapi/v1/interrupt", {}, opener, timeout)
    return 200 <= status_code < 300
