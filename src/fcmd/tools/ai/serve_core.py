"""本地大模型推理服务的核心逻辑（引擎 / 模型 / 状态 / 进程 / HTTP）。

本模块只放**可单测的纯逻辑**，CLI 与表格输出在 :mod:`fcmd.tools.ai.servetool`。
设计目标是单卡消费级 GPU 上跑 Qwen3-Coder-30B-A3B 这类 MoE 编码模型，
优先 token 速度，供 dsh / pi 等 OpenAI 兼容客户端使用。

目录约定（``FCMD_HOME`` 默认 ``~/.fcmd``）::

    ${FCMD_HOME}/serve/<name>.json        服务状态（pid / 端口 / 模型 / 引擎）
    ${FCMD_HOME}/serve/logs/<name>.log    引擎日志（追加写）
    ${FCMD_HOME}/serve/config.json        持久化配置（模型目录列表）
    ${FCMD_HOME}/engines/llama.cpp/<tag>/ 引擎目录（含 llama-server）

模型目录解析优先级：显式 ``--models-root`` → ``FCMD_MODELS`` → ``config.json`` 的
``models_roots``（可多个）→ Windows 下存在的 ``D:/models`` → ``${FCMD_HOME}/models``。
多个根目录会被递归扫描，同名量化按文件大小取最大者。

示例
----
    from fcmd.tools.ai import serve_core as core

    plan = core.make_plan(profile="balanced")
    print(core.format_command(plan.argv()))
    state = core.start_server(plan)
    print(core.client_env(state))
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

__all__ = [
    "CONVERT_OUTTYPES",
    "DEFAULT_ALIAS",
    "DEFAULT_API_KEY",
    "DEFAULT_ENGINE_TAG",
    "DEFAULT_HOST",
    "DEFAULT_NAME",
    "DEFAULT_PORT",
    "DEFAULT_PROFILE",
    "DEFAULT_THREADS",
    "PROFILES",
    "VRAM_OVERHEAD_BYTES",
    "BenchResult",
    "Check",
    "CommandResult",
    "ConvertPlan",
    "EngineInfo",
    "GpuInfo",
    "LaunchPlan",
    "LlamaCppBackend",
    "ModelEntry",
    "Profile",
    "ProfileName",
    "ServeError",
    "ServeState",
    "StopResult",
    "add_models_root",
    "bench",
    "check_engine",
    "check_health",
    "client_env",
    "config_path",
    "configured_roots",
    "convert_model",
    "convert_source_root",
    "default_models_root",
    "delete_state",
    "detect_gpu",
    "doctor",
    "engine_exe_name",
    "engines_root",
    "ensure_convert_script",
    "estimate_vram",
    "fcmd_home",
    "find_convert_python",
    "find_model",
    "format_command",
    "human_size",
    "install_engine",
    "is_hf_safetensors_dir",
    "list_engines",
    "list_states",
    "load_config",
    "load_state",
    "log_path",
    "make_bench_prompt",
    "make_plan",
    "models_roots",
    "plan_convert",
    "process_alive",
    "quantize_exe",
    "read_log",
    "remove_models_root",
    "reset_models_roots",
    "resolve_engine",
    "run_command",
    "save_config",
    "save_state",
    "scan_models",
    "serve_dir",
    "start_server",
    "state_path",
    "stop_server",
    "use_engine",
]

ProfileName = Literal["speed", "balanced", "long", "max-context"]
EngineVariant = Literal["cuda", "cpu"]
CheckStatus = Literal["ok", "warn", "fail"]

DEFAULT_NAME = "qwen"
DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"
DEFAULT_API_KEY = "sk-fcmd-local"
DEFAULT_ALIAS = "qwen3-coder-30b-a3b"
DEFAULT_THREADS = 8
DEFAULT_ENGINE_TAG = "b12211"
DEFAULT_PROFILE: ProfileName = "balanced"
DEFAULT_PROMPT = "请用一句话解释快速排序的核心思想。"
#: bench 长上下文提示的填充片段（英文约 4 字符 1 token）
_BENCH_FILLER = (
    "The quick brown fox jumps over the lazy dog. "
    "Pack my box with five dozen liquor jugs. "
    "How vexingly quick daft zebras jump! "
)

#: 30B MoE 每 token 的 KV 缓存（FP16 约 96 KiB，q8_0 约减半）
KV_BYTES_PER_TOKEN_FP16 = 96 * 1024
#: CUDA context / 计算图 / 临时缓冲的经验开销
VRAM_OVERHEAD_BYTES = 2 * 1024**3
_GIB = 1024**3
_CHUNK_SIZE = 1024 * 1024
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ENGINE_MARKER = "engine.json"
_CURRENT_MARKER = "current.json"
_KV_SCALE = {"f16": 1.0, "q8_0": 0.5, "q4_0": 0.3}


class ServeError(RuntimeError):
    """服务编排失败（引擎 / 模型 / 进程 / 状态异常）。"""


# ---------------------------------------------------------------------- #
# 基础类型
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CommandResult:
    """外部命令执行结果。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[..., CommandResult]
Opener = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class GpuInfo:
    """NVIDIA GPU 信息。"""

    name: str
    memory_mib: int


@dataclass(frozen=True, slots=True)
class Profile:
    """推理档位：量化 + 上下文 + KV 量化 + ubatch。"""

    name: str
    quant: str
    context: int
    kv_type: str
    ubatch: int
    note: str


PROFILES: dict[str, Profile] = {
    "speed": Profile("speed", "Q4_K_M", 65536, "f16", 2048, "最大化 token 速度"),
    "balanced": Profile("balanced", "Q5_K_M", 65536, "f16", 2048, "质量与速度平衡（默认）"),
    "long": Profile("long", "Q5_K_M", 131072, "q8_0", 2048, "128K 长上下文"),
    "max-context": Profile("max-context", "Q4_K_M", 196608, "q8_0", 1024, "192K 最大上下文"),
}


@dataclass(frozen=True, slots=True)
class Check:
    """体检项。"""

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """扫描到的一个本地 GGUF 模型。"""

    root: Path
    path: Path
    size: int
    profiles: tuple[str, ...] = ()

    @property
    def relative(self) -> str:
        """相对模型根目录的路径（跨盘时退化为绝对路径）。"""
        try:
            return str(self.path.relative_to(self.root))
        except ValueError:  # pragma: no cover - path 必然位于 root 之下
            return str(self.path)


@dataclass(frozen=True, slots=True)
class EngineInfo:
    """已解析的推理引擎。

    ``path_prepend`` 用于兼容把 CUDA runtime 放在同级 ``vendor/`` 的发行包
    （如 LM Studio 后端），启动时会被前置到 ``PATH``。
    """

    tag: str
    exe: Path
    source: str = ""
    path_prepend: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """一次启动所需的全部参数。"""

    name: str
    model: Path
    profile: Profile
    context: int
    host: str
    port: int
    alias: str
    api_key: str
    threads: int
    engine: EngineInfo
    log: Path
    extra_args: tuple[str, ...] = ()
    kv_type: str | None = None
    ubatch: int | None = None

    @property
    def base_url(self) -> str:
        """OpenAI 兼容服务根地址（不含 ``/v1``）。"""
        return http_base_url(self.host, self.port)

    @property
    def effective_kv_type(self) -> str:
        """生效的 KV 缓存类型（显式覆盖优先于档位）。"""
        return self.kv_type or self.profile.kv_type

    @property
    def effective_ubatch(self) -> int:
        """生效的 ubatch（显式覆盖优先于档位）。"""
        return self.ubatch or self.profile.ubatch

    def argv(self, backend: Backend | None = None) -> list[str]:
        """按后端生成启动参数列表。"""
        return (backend or LlamaCppBackend()).build_argv(self)


@dataclass(frozen=True, slots=True)
class ServeState:
    """落盘的服务状态。"""

    name: str
    pid: int
    create_time: float
    host: str
    port: int
    model: str
    profile: str
    context: int
    engine: str
    alias: str
    api_key: str
    log: str
    started_at: str

    @property
    def base_url(self) -> str:
        """OpenAI 兼容服务根地址（不含 ``/v1``）。"""
        return http_base_url(self.host, self.port)

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StopResult:
    """停止服务的结果。"""

    state: ServeState
    was_running: bool


@dataclass(frozen=True, slots=True)
class BenchResult:
    """一次请求的实测指标。"""

    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    tokens_per_s: float


class Backend(Protocol):
    """推理引擎后端抽象：把启动计划翻译成命令行。"""

    name: str

    def build_argv(self, plan: LaunchPlan) -> list[str]:
        """生成引擎启动参数。"""
        ...


@dataclass(frozen=True, slots=True)
class LlamaCppBackend:
    """llama.cpp ``llama-server`` 后端。"""

    name: str = "llama.cpp"

    def build_argv(self, plan: LaunchPlan) -> list[str]:
        """生成 llama-server 参数（单并发 + FlashAttention + KV 量化）。"""
        return [
            str(plan.engine.exe),
            "-m",
            str(plan.model),
            "-ngl",
            "99",
            "-c",
            str(plan.context),
            "-np",
            "1",
            "--flash-attn",
            "on",
            "-ctk",
            plan.effective_kv_type,
            "-ctv",
            plan.effective_kv_type,
            "-b",
            "4096",
            "-ub",
            str(plan.effective_ubatch),
            "-t",
            str(plan.threads),
            "--jinja",
            "--cache-reuse",
            "256",
            "--no-context-shift",
            "--metrics",
            "--no-webui",
            "--alias",
            plan.alias,
            "--host",
            plan.host,
            "--port",
            str(plan.port),
            "--api-key",
            plan.api_key,
            *plan.extra_args,
        ]


# ---------------------------------------------------------------------- #
# 通用辅助
# ---------------------------------------------------------------------- #
def human_size(size: int) -> str:
    """把字节数格式化为易读大小（B/KB/MB/GB/TB）。"""
    value = float(size)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover - 循环必然返回


def now_iso() -> str:
    """当前本地时间（ISO 8601，秒级）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_command(cmd: Sequence[str], timeout: float = 15.0, env: dict[str, str] | None = None) -> CommandResult:
    """执行外部命令并捕获输出；超时 / 不存在统一返回非零结果。"""
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        return CommandResult(-1, "", f"命令超时: {cmd[0]}")
    except OSError as exc:
        return CommandResult(-1, "", str(exc))
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def format_returncode(code: int) -> str:
    """把退出码格式化为便于搜索的形式（Windows 负值转 0xXXXXXXXX）。"""
    if code < 0:
        return f"{code} (0x{code & 0xFFFFFFFF:08X})"
    return str(code)


def client_host(host: str) -> str:
    """客户端应连接的地址（监听 ``0.0.0.0`` 时回落到回环）。"""
    return "127.0.0.1" if host in ("", "0.0.0.0", "::") else host


def http_base_url(host: str, port: int) -> str:
    """构造 HTTP 根地址。"""
    return f"http://{client_host(host)}:{port}"


def format_command(argv: Sequence[str]) -> str:
    """把参数列表格式化为可复制的命令行。"""
    return shlex.join(list(argv))


def validate_name(name: str) -> str:
    """校验服务名（避免路径穿越，限制为字母数字与 ``._-``）。"""
    if not _NAME_RE.fullmatch(name):
        raise ServeError(f"服务名不合法: {name!r}（仅允许字母数字与 . _ -，且不能以符号开头）")
    return name


# ---------------------------------------------------------------------- #
# 目录与状态文件
# ---------------------------------------------------------------------- #
def fcmd_home() -> Path:
    """``FCMD_HOME``（默认 ``~/.fcmd``）。"""
    raw = os.environ.get("FCMD_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".fcmd"


def _home(home: Path | None) -> Path:
    return home if home is not None else fcmd_home()


def serve_dir(home: Path | None = None) -> Path:
    """服务状态目录。"""
    return _home(home) / "serve"


def state_path(name: str, home: Path | None = None) -> Path:
    """状态文件路径。"""
    return serve_dir(home) / f"{validate_name(name)}.json"


def log_path(name: str, home: Path | None = None) -> Path:
    """日志文件路径。"""
    return serve_dir(home) / "logs" / f"{validate_name(name)}.log"


def engines_root(home: Path | None = None) -> Path:
    """llama.cpp 引擎根目录。"""
    return _home(home) / "engines" / "llama.cpp"


def default_models_root() -> Path:
    """兜底模型根目录：Windows 的 ``D:/models``，否则 ``${FCMD_HOME}/models``。"""
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/models")
    return fcmd_home() / "models"


def config_path(home: Path | None = None) -> Path:
    """持久化配置文件路径（``${FCMD_HOME}/serve/config.json``）。"""
    return serve_dir(home) / "config.json"


def load_config(home: Path | None = None) -> dict[str, Any]:
    """读取配置；缺失或损坏时返回空字典。"""
    path = config_path(home)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any], home: Path | None = None) -> Path:
    """写入配置文件（自动创建目录）。"""
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _path_key(path: Path) -> str:
    """用于去重的路径键（Windows 大小写不敏感）。"""
    return os.path.normcase(str(Path(path).resolve()))


def configured_roots(home: Path | None = None) -> list[Path]:
    """配置文件中登记的模型目录（保持登记顺序并去重）。"""
    raw = load_config(home).get("models_roots")
    if not isinstance(raw, list):
        return []
    roots: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item.strip()).expanduser()
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def models_roots(explicit: Path | None = None, home: Path | None = None) -> list[Path]:
    """解析实际使用的模型目录列表。

    优先级：``explicit``（``--models-root``）→ ``FCMD_MODELS`` → 配置文件 → 默认目录。
    """
    if explicit is not None:
        return [explicit.expanduser()]
    raw = os.environ.get("FCMD_MODELS")
    if raw:
        return [Path(raw).expanduser()]
    roots = configured_roots(home)
    return roots or [default_models_root()]


def _save_roots(roots: Sequence[Path], home: Path | None) -> list[Path]:
    save_config({"models_roots": [str(p) for p in roots]}, home)
    return list(roots)


def add_models_root(path: Path, home: Path | None = None) -> list[Path]:
    """新增一个模型目录并落盘（重复登记忽略）。"""
    roots = configured_roots(home)
    key = _path_key(path)
    if any(_path_key(item) == key for item in roots):
        return roots
    return _save_roots([*roots, path.expanduser()], home)


def remove_models_root(path: Path, home: Path | None = None) -> list[Path]:
    """移除一个已登记的模型目录。"""
    roots = configured_roots(home)
    key = _path_key(path)
    remaining = [item for item in roots if _path_key(item) != key]
    if len(remaining) == len(roots):
        raise ServeError(f"配置中没有模型目录: {path}")
    return _save_roots(remaining, home)


def reset_models_roots(home: Path | None = None) -> list[Path]:
    """清空配置中的模型目录，回到默认解析。"""
    path = config_path(home)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    return [default_models_root()]


def save_state(state: ServeState, home: Path | None = None) -> Path:
    """写入状态文件（自动创建目录）。"""
    path = state_path(state.name, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_state(name: str, home: Path | None = None, *, missing_ok: bool = False) -> ServeState | None:
    """读取状态文件；缺失时按 ``missing_ok`` 返回 ``None`` 或抛错。"""
    path = state_path(name, home)
    if not path.is_file():
        if missing_ok:
            return None
        raise ServeError(f"服务 {name!r} 未启动（状态文件不存在: {path}）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ServeState(
            name=str(data["name"]),
            pid=int(data["pid"]),
            create_time=float(data["create_time"]),
            host=str(data["host"]),
            port=int(data["port"]),
            model=str(data["model"]),
            profile=str(data["profile"]),
            context=int(data["context"]),
            engine=str(data["engine"]),
            alias=str(data["alias"]),
            api_key=str(data["api_key"]),
            log=str(data["log"]),
            started_at=str(data["started_at"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ServeError(f"状态文件损坏: {path}（{exc}）") from exc


def require_state(name: str, home: Path | None = None) -> ServeState:
    """读取状态文件；不存在时抛 :class:`ServeError`。"""
    state = load_state(name, home)
    if state is None:  # pragma: no cover - missing_ok=False 时不会返回 None
        raise ServeError(f"服务 {name!r} 未启动")
    return state


def delete_state(name: str, home: Path | None = None) -> bool:
    """删除状态文件，返回是否实际删除。"""
    path = state_path(name, home)
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_states(home: Path | None = None) -> list[ServeState]:
    """列出全部可解析的服务状态（损坏文件跳过）。"""
    directory = serve_dir(home)
    if not directory.is_dir():
        return []
    states: list[ServeState] = []
    for path in sorted(directory.glob("*.json")):
        try:
            state = load_state(path.stem, home)
        except ServeError:
            continue
        if state is not None:
            states.append(state)
    return states


# ---------------------------------------------------------------------- #
# 进程
# ---------------------------------------------------------------------- #
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
    state = require_state(name, home)
    if not (alive or process_alive)(state.pid, state.create_time):
        delete_state(name, home)
        return StopResult(state, False)
    (terminator or _terminate)(state.pid, state.create_time, timeout)
    delete_state(name, home)
    return StopResult(state, True)


# ---------------------------------------------------------------------- #
# 模型与显存估算
# ---------------------------------------------------------------------- #
def scan_models(
    roots: Sequence[Path] | None = None,
    home: Path | None = None,
) -> list[ModelEntry]:
    """递归扫描模型目录，返回 GGUF 清单（忽略 mmproj、跨目录去重、按大小降序）。"""
    entries: list[ModelEntry] = []
    seen: set[str] = set()
    for raw_root in models_roots(home=home) if roots is None else roots:
        root = raw_root.expanduser()
        if not root.is_dir():
            continue
        for path in root.rglob("*.gguf"):
            if "mmproj" in path.name.lower():
                continue
            try:
                size = path.stat().st_size
            except OSError:  # pragma: no cover - 扫描期间文件被删除
                continue
            resolved = path.resolve()
            key = _path_key(resolved)
            if key in seen:
                continue
            seen.add(key)
            profiles = tuple(name for name, prof in PROFILES.items() if prof.quant.lower() in path.name.lower())
            entries.append(ModelEntry(root=root, path=resolved, size=size, profiles=profiles))
    entries.sort(key=lambda entry: (-entry.size, str(entry.path)))
    return entries


def find_model(
    quant: str,
    models_root: Path | None = None,
    model: Path | None = None,
    home: Path | None = None,
) -> Path:
    """定位指定量化档位的 GGUF 文件。

    ``model`` 可为 GGUF 文件或目录（覆盖模型目录配置）；未指定时在所有已配置的
    模型目录中递归查找文件名包含 ``quant`` 的 GGUF（忽略 mmproj），同名取最大。
    """
    if model is not None:
        target = model.expanduser()
        if target.is_file():
            if target.suffix.lower() != ".gguf":
                raise ServeError(f"模型文件不是 .gguf: {target}")
            return target.resolve()
        if not target.is_dir():
            raise ServeError(f"模型路径不存在: {target}")
        roots = [target]
    else:
        roots = models_roots(models_root, home)
        existing = [root for root in roots if root.is_dir()]
        if not existing:
            raise ServeError(
                f"模型目录不存在: {', '.join(str(root) for root in roots)}；先下载模型："
                f"fcmd mdl download unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF --include *{quant}* --dest {roots[0]}"
            )
        roots = existing

    entries = scan_models(roots)
    matched = [entry for entry in entries if quant.lower() in entry.path.name.lower()]
    if not matched:
        available = sorted({entry.path.name for entry in entries})[:5]
        hint = f"；已发现: {', '.join(available)}" if available else ""
        raise ServeError(
            f"在 {', '.join(str(root) for root in roots)} 未找到量化 {quant} 的 GGUF{hint}；"
            f"可执行 fcmd mdl download unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF --include *{quant}* "
            f"--dest {roots[0]}"
        )
    return max(matched, key=lambda entry: entry.size).path


def estimate_vram(model_bytes: int, context: int, kv_type: str = "q8_0") -> int:
    """估算显存占用（模型权重 + KV 缓存 + 固定开销）。"""
    kv = int(context * KV_BYTES_PER_TOKEN_FP16 * _KV_SCALE.get(kv_type, 1.0))
    return model_bytes + kv + VRAM_OVERHEAD_BYTES


def plan_vram(plan: LaunchPlan) -> int:
    """估算启动计划的显存占用。"""
    try:
        size = plan.model.stat().st_size
    except OSError:
        size = 0
    return estimate_vram(size, plan.context, plan.effective_kv_type)


# ---------------------------------------------------------------------- #
# GPU / 引擎
# ---------------------------------------------------------------------- #
def detect_gpu(runner: Runner = run_command) -> GpuInfo | None:
    """用 ``nvidia-smi`` 查询第一块 GPU；不可用时返回 ``None``。"""
    result = runner(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    line = result.stdout.strip().splitlines()[0]
    name, _, memory = line.rpartition(",")
    if not name:
        return None
    try:
        memory_mib = int(float(memory.strip()))
    except ValueError:
        return None
    return GpuInfo(name.strip(), memory_mib)


def engine_exe_name(base: str = "llama-server") -> str:
    """引擎可执行文件名（Windows 带 ``.exe``）。"""
    return f"{base}.exe" if os.name == "nt" else base


def _read_marker(directory: Path) -> dict[str, Any]:
    """读取引擎目录下的 ``engine.json``（缺失或损坏返回空字典）。"""
    marker = directory / _ENGINE_MARKER
    if not marker.is_file():
        return {}
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _engine_exe_in(directory: Path) -> Path | None:
    """在引擎目录内定位 llama-server（优先读 ``engine.json`` 的 ``exe``）。"""
    raw = _read_marker(directory).get("exe")
    if isinstance(raw, str) and raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = directory / candidate
        if candidate.is_file():
            return candidate
    direct = directory / engine_exe_name()
    if direct.is_file():
        return direct
    matches = sorted(directory.rglob(engine_exe_name()), key=lambda p: len(p.parts))
    return matches[0] if matches else None


def _engine_info(directory: Path, tag: str | None = None) -> EngineInfo | None:
    """构造引擎信息（含 marker 里记录的 ``path_prepend``）。"""
    exe = _engine_exe_in(directory)
    if exe is None:
        return None
    marker = _read_marker(directory)
    raw = marker.get("path_prepend")
    prepend = tuple(str(item) for item in raw) if isinstance(raw, list) else ()
    source = marker.get("source")
    return EngineInfo(tag or directory.name, exe, str(source or directory), prepend)


def _detect_path_prepend(exe: Path) -> tuple[str, ...]:
    """探测同级 ``vendor/`` 下的 DLL 目录（LM Studio 等发行包的 CUDA runtime）。"""
    vendor = exe.parent.parent / "vendor"
    if not vendor.is_dir():
        return ()
    dirs = [str(d.resolve()) for d in sorted(vendor.iterdir()) if d.is_dir() and any(d.glob("*.dll"))]
    return tuple(dirs)


def _engine_env(engine: EngineInfo) -> dict[str, str] | None:
    """为引擎构造带 ``path_prepend`` 的进程环境；无需追加时返回 ``None``。"""
    if not engine.path_prepend:
        return None
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([*engine.path_prepend, env.get("PATH", "")])
    return env


def _run(runner: Runner, cmd: Sequence[str], engine: EngineInfo | None = None) -> CommandResult:
    """执行外部命令；引擎带 ``path_prepend`` 时透传环境。"""
    env = _engine_env(engine) if engine is not None else None
    if env is None:
        return runner(cmd)
    return runner(cmd, env=env)


def _current_tag(root: Path) -> str | None:
    """读取 ``current.json`` 中记录的当前引擎 tag。"""
    marker = root / _CURRENT_MARKER
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tag = data.get("tag") if isinstance(data, dict) else None
    return str(tag) if tag else None


def resolve_engine(tag: str | None = None, home: Path | None = None) -> EngineInfo:
    """解析要使用的引擎：``FCMD_LLAMA_SERVER`` → 指定 tag → current → 最新安装 → PATH。"""
    env_raw = os.environ.get("FCMD_LLAMA_SERVER")
    if env_raw:
        exe = Path(env_raw).expanduser()
        if not exe.is_file():
            raise ServeError(f"FCMD_LLAMA_SERVER 指向的文件不存在: {exe}")
        return EngineInfo("env", exe, "FCMD_LLAMA_SERVER", _detect_path_prepend(exe))

    root = engines_root(home)
    if tag:
        info = _engine_info(root / tag, tag)
        if info is None:
            raise ServeError(f"引擎 {tag!r} 未安装：fcmd srv engine install --tag {tag}")
        return info

    current = _current_tag(root)
    if current:
        info = _engine_info(root / current, current)
        if info is not None:
            return info

    if root.is_dir():
        for directory in sorted(root.iterdir(), reverse=True):
            if directory.is_dir():
                info = _engine_info(directory)
                if info is not None:
                    return info

    which = shutil.which("llama-server")
    if which:
        return EngineInfo("system", Path(which), "PATH")
    raise ServeError("未找到 llama.cpp 引擎：先执行 fcmd srv engine install --tag " + DEFAULT_ENGINE_TAG)


def engine_asset_names(tag: str, variant: EngineVariant = "cuda") -> list[str]:
    """返回官方 release 资产名（仅 Windows 有预编译包）。"""
    if os.name != "nt":
        raise ServeError("非 Windows 平台没有官方预编译包：请用 --url 指定资产，或用 --path 注册已编译引擎目录")
    if variant == "cpu":
        return [f"llama-{tag}-bin-win-cpu-x64.zip"]
    return [f"llama-{tag}-bin-win-cuda-13.5-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip"]


def _release_url(tag: str, asset: str) -> str:
    return f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{asset}"


def _download(url: str, dest: Path, opener: Opener | None = None) -> int:
    """下载 URL 到文件，返回字节数。"""
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": "fcmd-servetool/0.3.10"})
    open_fn = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
    try:
        with open_fn(req, 60.0) as resp, dest.open("wb") as out:
            total = 0
            while chunk := resp.read(_CHUNK_SIZE):
                out.write(chunk)
                total += len(chunk)
    except OSError as exc:
        raise ServeError(f"下载失败: {url}（{exc}）") from exc
    return total


def _extract_zip(archive: Path, target: Path) -> None:
    """解压 zip 到目标目录并删除压缩包。"""
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ServeError(f"解压失败: {archive}（{exc}）") from exc
    finally:
        archive.unlink(missing_ok=True)


def install_engine(  # noqa: PLR0913
    tag: str = DEFAULT_ENGINE_TAG,
    *,
    variant: EngineVariant = "cuda",
    url: str | None = None,
    path: Path | None = None,
    home: Path | None = None,
    opener: Opener | None = None,
) -> EngineInfo:
    """安装或注册 llama.cpp 引擎。

    ``path`` 用于注册本地已编译引擎目录（不复制文件）；否则从 GitHub release
    下载 Windows 预编译包（``cuda`` 变体额外下载 cudart）。
    """
    target = engines_root(home) / tag
    cached = _engine_exe_in(target) if target.exists() else None
    if cached is not None:
        return EngineInfo(tag, cached, str(target))

    if path is not None:
        source = path.expanduser()
        if not source.is_dir():
            raise ServeError(f"引擎目录不存在: {source}")
        exe = _engine_exe_in(source)
        if exe is None:
            raise ServeError(f"目录内找不到 {engine_exe_name()}: {source}")
        target.mkdir(parents=True, exist_ok=True)
        prepend = _detect_path_prepend(exe)
        _write_engine_marker(target, tag, exe, str(source), prepend)
        return EngineInfo(tag, exe, str(source), prepend)

    target.mkdir(parents=True, exist_ok=True)
    urls = [url] if url else [_release_url(tag, name) for name in engine_asset_names(tag, variant)]
    for item in urls:
        archive = target / (item.rsplit("/", 1)[-1] or f"{tag}.zip")
        _download(item, archive, opener)
        _extract_zip(archive, target)

    exe = _engine_exe_in(target)
    if exe is None:
        raise ServeError(f"安装后仍找不到 {engine_exe_name()}: {target}")
    _write_engine_marker(target, tag, exe, urls[0])
    return EngineInfo(tag, exe, urls[0])


def _write_engine_marker(target: Path, tag: str, exe: Path, source: str, prepend: tuple[str, ...] = ()) -> None:
    """写入引擎标记文件。"""
    payload = {
        "tag": tag,
        "exe": str(exe),
        "source": source,
        "path_prepend": list(prepend),
        "installed_at": now_iso(),
    }
    (target / _ENGINE_MARKER).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_engines(home: Path | None = None) -> list[EngineInfo]:
    """列出已安装引擎（按 tag 降序，标记当前使用的 tag）。"""
    root = engines_root(home)
    if not root.is_dir():
        return []
    current = _current_tag(root)
    infos: list[EngineInfo] = []
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        info = _engine_info(directory)
        if info is not None:
            infos.append(
                EngineInfo(info.tag, info.exe, "current", info.path_prepend) if directory.name == current else info
            )
    return infos


def use_engine(tag: str, home: Path | None = None) -> EngineInfo:
    """把指定 tag 设为默认引擎。"""
    root = engines_root(home)
    info = _engine_info(root / tag, tag)
    if info is None:
        raise ServeError(f"引擎 {tag!r} 未安装：fcmd srv engine install --tag {tag}")
    root.mkdir(parents=True, exist_ok=True)
    (root / _CURRENT_MARKER).write_text(
        json.dumps({"tag": tag, "updated_at": now_iso()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return info


def check_engine(engine: EngineInfo, runner: Runner = run_command) -> Check:
    """执行 ``llama-server --version`` 验证引擎可运行（能捕获 0xC0000005 崩溃）。"""
    result = _run(runner, [str(engine.exe), "--version"], engine)
    if result.returncode == 0:
        first = (result.stdout or result.stderr).strip().splitlines()
        return Check("推理引擎", "ok", f"{engine.exe}（{first[0] if first else 'version ok'}）")
    detail = result.stderr.strip() or result.stdout.strip() or "无输出"
    return Check(
        "推理引擎",
        "fail",
        f"{engine.exe} --version 退出码 {format_returncode(result.returncode)}；{detail[:200]}",
    )


# ---------------------------------------------------------------------- #
# 模型格式转换（HF safetensors → GGUF）
# ---------------------------------------------------------------------- #
CONVERT_SCRIPT = "convert_hf_to_gguf.py"
CONVERT_REPO_URL = "https://github.com/ggml-org/llama.cpp"
CONVERT_OUTTYPES = ("f32", "f16", "bf16", "q8_0")
CONVERT_TIMEOUT = 6 * 3600.0
QUANTIZE_TIMEOUT = 2 * 3600.0
CLONE_TIMEOUT = 900.0
_BUILD_TAG_RE = re.compile(r"^b\d+$")


@dataclass(frozen=True, slots=True)
class ConvertPlan:
    """一次 ``safetensors → GGUF`` 转换所需的全部参数。"""

    source: Path
    output: Path
    outtype: str
    script: Path
    python: str
    quant: str | None = None
    quantize_exe: Path | None = None

    @property
    def intermediate(self) -> str:
        """转换脚本直接产出的文件（量化时作为临时中间文件）。"""
        if self.quant is None:
            return str(self.output)
        return str(self.output.with_name(f"{self.output.stem}-{self.outtype}.gguf"))

    def convert_argv(self) -> list[str]:
        """转换脚本命令行。"""
        return [
            self.python,
            str(self.script),
            str(self.source),
            "--outfile",
            self.intermediate,
            "--outtype",
            self.outtype,
        ]

    def quantize_argv(self) -> list[str]:
        """量化命令行。"""
        if self.quant is None or self.quantize_exe is None:
            raise ServeError("未指定量化类型或缺少 llama-quantize")
        return [str(self.quantize_exe), self.intermediate, str(self.output), self.quant]


def is_hf_safetensors_dir(path: Path) -> bool:
    """判断目录是否为 HuggingFace safetensors 模型目录。"""
    target = path.expanduser()
    return target.is_dir() and (target / "config.json").is_file() and any(target.glob("*.safetensors"))


def convert_source_root(tag: str, home: Path | None = None) -> Path:
    """llama.cpp 源码目录（按引擎 tag 隔离，内含转换脚本与 ``gguf-py``）。"""
    return engines_root(home) / tag / "llama.cpp"


def ensure_convert_script(
    tag: str | None = None,
    script: Path | None = None,
    home: Path | None = None,
    runner: Runner = run_command,
) -> Path:
    """定位转换脚本：显式 ``--script`` → ``FCMD_CONVERT_SCRIPT`` → 本地源码 → 浅克隆。

    现代 llama.cpp 的 ``convert_hf_to_gguf.py`` 依赖仓库内的 ``conversion`` 包与
    ``gguf-py``，因此不能单独下载脚本，必须保留完整源码目录。
    """
    raw = script or (
        Path(os.environ["FCMD_CONVERT_SCRIPT"]).expanduser() if os.environ.get("FCMD_CONVERT_SCRIPT") else None
    )
    if raw is not None:
        target = Path(raw).expanduser()
        if not target.is_file():
            raise ServeError(f"转换脚本不存在: {target}")
        return target

    root = engines_root(home)
    resolved = tag or _current_tag(root) or DEFAULT_ENGINE_TAG
    cached = convert_source_root(resolved, home) / CONVERT_SCRIPT
    if cached.is_file():
        return cached

    ref = resolved if _BUILD_TAG_RE.fullmatch(resolved) else "master"
    repo = cached.parent
    repo.parent.mkdir(parents=True, exist_ok=True)
    result = runner(
        ["git", "clone", "--depth", "1", "--branch", ref, CONVERT_REPO_URL, str(repo)],
        timeout=CLONE_TIMEOUT,
    )
    if result.returncode != 0 or not cached.is_file():
        raise ServeError(
            f"拉取 llama.cpp 源码失败（{format_returncode(result.returncode)}）："
            f"{_tail(result.stderr or result.stdout) or '无输出'}；"
            f"可手动 clone {CONVERT_REPO_URL} 后用 --script 指定 convert_hf_to_gguf.py"
        )
    return cached


def quantize_exe(engine: EngineInfo) -> Path | None:
    """定位 ``llama-quantize``：``FCMD_LLAMA_QUANTIZE`` → 引擎同目录。"""
    raw = os.environ.get("FCMD_LLAMA_QUANTIZE")
    if raw:
        target = Path(raw).expanduser()
        if not target.is_file():
            raise ServeError(f"FCMD_LLAMA_QUANTIZE 指向的文件不存在: {target}")
        return target
    candidate = engine.exe.parent / engine_exe_name("llama-quantize")
    return candidate if candidate.is_file() else None


def plan_convert(  # noqa: PLR0913, PLR0917
    source: Path,
    output: Path | None = None,
    outtype: str = "f16",
    quant: str | None = None,
    script: Path | None = None,
    python: str | None = None,
    models_root: Path | None = None,
    tag: str | None = None,
    home: Path | None = None,
) -> ConvertPlan:
    """构造转换计划。

    默认输出到模型目录：``<models_root>/<源目录名>/<源目录名>[-<量化>].gguf``。
    """
    src = source.expanduser()
    if not is_hf_safetensors_dir(src):
        raise ServeError(f"不是 HF safetensors 目录（需要 config.json 与 *.safetensors）: {src}")
    if outtype not in CONVERT_OUTTYPES:
        raise ServeError(f"不支持的 outtype {outtype!r}：可选 {', '.join(CONVERT_OUTTYPES)}")

    if output is None:
        root = models_roots(models_root, home)[0].expanduser()
        suffix = f"-{quant}" if quant else ""
        target = root / src.name / f"{src.name}{suffix}.gguf"
    else:
        target = output.expanduser()
    if target.suffix.lower() != ".gguf":
        raise ServeError(f"输出文件必须是 .gguf: {target}")

    quant_exe: Path | None = None
    if quant:
        # Check if script is explicitly provided and we should avoid engine resolution
        # This avoids unnecessary engine resolution when user provides custom script
        # but engine is not installed
        try:
            quant_exe = quantize_exe(resolve_engine(tag, home))
        except ServeError as e:
            # If engine resolution fails and user provided explicit --script,
            # let's provide a more informative error message
            if script is not None:
                # User provided script, but we still can't resolve engine for quantization
                raise ServeError(
                    "指定量化时无法解析引擎：安装官方 llama.cpp release 引擎，或用 FCMD_LLAMA_QUANTIZE 指定，"
                    "或使用 --script 指定不依赖引擎的脚本"
                ) from e
            else:
                raise e
        if quant_exe is None:
            raise ServeError("未找到 llama-quantize：安装官方 llama.cpp release 引擎，或用 FCMD_LLAMA_QUANTIZE 指定")

    return ConvertPlan(
        source=src.resolve(),
        output=target.resolve(),
        outtype=outtype,
        script=ensure_convert_script(tag=tag, script=script, home=home),
        python=python or os.environ.get("FCMD_CONVERT_PYTHON") or sys.executable,
        quant=quant,
        quantize_exe=quant_exe,
    )


def _python_candidates() -> list[str]:
    """候选解释器：当前解释器 + PATH 上的 python（去重）。

    ``uv run`` 会把虚拟环境放到 PATH 首位，仅看 ``shutil.which`` 找不到系统 conda
    里的 torch，因此遍历 PATH 中的每个目录。
    """
    names = ("python.exe", "python3.exe") if os.name == "nt" else ("python3", "python")
    raw = [sys.executable, shutil.which("python"), shutil.which("python3")]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        raw.extend(str(Path(entry) / name) for name in names if (Path(entry) / name).is_file())
    seen: set[str] = set()
    candidates: list[str] = []
    for candidate in raw:
        if not candidate:
            continue
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def find_convert_python(runner: Runner = run_command) -> str:
    """找到一个已安装 ``torch`` / ``safetensors`` / ``numpy`` 的 Python 解释器。"""
    for candidate in _python_candidates():
        result = runner([candidate, "-c", "import torch, safetensors, numpy"], timeout=120.0)
        if result.returncode == 0:
            return candidate
    raise ServeError("未找到已安装 torch / safetensors / numpy 的 Python；请用 --python 指定")


def _tail(text: str, lines: int = 6) -> str:
    """取输出的最后若干行（用于错误摘要）。"""
    parts = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " | ".join(parts[-lines:])


def convert_model(plan: ConvertPlan, runner: Runner = run_command) -> list[Path]:
    """执行转换（可选再量化），返回最终产出的 GGUF 列表。"""
    plan.output.parent.mkdir(parents=True, exist_ok=True)
    result = runner(plan.convert_argv(), timeout=CONVERT_TIMEOUT)
    if result.returncode != 0:
        output = f"{result.stderr}\n{result.stdout}"
        hint = ""
        if "ModuleNotFoundError" in output:
            hint = "；请用 --python 指定已安装 torch / safetensors / numpy 的解释器"
        raise ServeError(
            f"safetensors 转 GGUF 失败（{format_returncode(result.returncode)}）：{_tail(output) or '无输出'}{hint}"
        )
    if plan.quant is None:
        return [plan.output]

    if not Path(plan.intermediate).is_file():
        raise ServeError(f"转换命令已结束但未生成中间文件: {plan.intermediate}")
    result = runner(plan.quantize_argv(), timeout=QUANTIZE_TIMEOUT)
    if result.returncode != 0:
        raise ServeError(
            f"量化失败（{format_returncode(result.returncode)}）：{_tail(result.stderr or result.stdout) or '无输出'}"
        )
    if not plan.output.is_file():
        raise ServeError(f"量化命令已结束但未生成: {plan.output}")
    with contextlib.suppress(OSError):
        Path(plan.intermediate).unlink()
    return [plan.output]


# ---------------------------------------------------------------------- #
# 计划与启动
# ---------------------------------------------------------------------- #
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
    if kv_type is not None and kv_type not in _KV_SCALE:
        raise ServeError(f"不支持的 KV 类型 {kv_type!r}，可选: {', '.join(_KV_SCALE)}")
    if ubatch is not None and ubatch < 1:
        raise ServeError(f"ubatch 必须为正整数: {ubatch}")
    model_path = find_model(prof.quant, models_root, model)
    engine = resolve_engine(engine_tag, home)
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
        log=log_path(name, home),
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
    env = _engine_env(plan.engine)
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
            save_state(state, home)
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
    existing = load_state(plan.name, home, missing_ok=True)
    if existing is not None:
        if process_alive(existing.pid, existing.create_time):
            raise ServeError(f"服务 {plan.name!r} 已在运行（pid={existing.pid}），先执行 fcmd srv stop {plan.name}")
        delete_state(plan.name, home)

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
    path = log_path(name, home)
    if not path.is_file():
        raise ServeError(f"日志不存在: {path}")
    return _tail_text(path, lines)


# ---------------------------------------------------------------------- #
# 客户端接入 / 实测
# ---------------------------------------------------------------------- #
def client_env(state: ServeState) -> dict[str, str]:
    """dsh / pi 等 OpenAI 兼容客户端需要的环境变量。"""
    return {
        "OPENAI_BASE_URL": state.base_url + "/v1",
        "OPENAI_API_KEY": state.api_key,
        "OPENAI_MODEL": state.alias,
    }


def _post_json(url: str, payload: dict[str, Any], api_key: str, timeout: float, opener: Opener | None) -> Any:
    """POST JSON 并解析响应（网络 / 解析错误统一转 :class:`ServeError`）。"""
    from urllib.request import Request, urlopen

    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    open_fn = opener or (lambda request, seconds: urlopen(request, timeout=seconds))
    try:
        with open_fn(Request(url, data=body, headers=headers, method="POST"), timeout) as resp:
            raw = resp.read()
    except OSError as exc:
        raise ServeError(f"请求失败: {url}（{exc}）") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ServeError(f"响应不是合法 JSON: {url}") from exc


def make_bench_prompt(tokens: int) -> str:
    """生成约 ``tokens`` 个 token 的测试提示，用于模拟 dsh 这类长上下文会话。"""
    if tokens <= 0:
        return DEFAULT_PROMPT
    repeats = max(1, tokens * 4 // len(_BENCH_FILLER))
    return _BENCH_FILLER * repeats + "\n请用一句话总结上面的内容。"


def bench(  # noqa: PLR0913
    state: ServeState,
    *,
    prompt: str = DEFAULT_PROMPT,
    prompt_tokens: int | None = None,
    max_tokens: int = 128,
    timeout: float = 180.0,
    opener: Opener | None = None,
) -> BenchResult:
    """对运行中的服务发一次非流式请求，返回延迟与吞吐。

    ``prompt_tokens`` 非空时忽略 ``prompt``，改用按目标 token 数生成的填充提示，
    以免短提示测出的吞吐（200+ tok/s）明显高于真实长会话。
    """
    text = make_bench_prompt(prompt_tokens) if prompt_tokens else prompt
    payload: dict[str, Any] = {
        "model": state.alias,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    start = time.monotonic()
    data = _post_json(state.base_url + "/v1/chat/completions", payload, state.api_key, timeout, opener)
    elapsed = max(time.monotonic() - start, 1e-6)
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    if not completion_tokens and isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                completion_tokens = max(len(content) // 2, 1)
    # llama-server 的 timings.predicted_per_second 只衡量 decode，不含 prefill；
    # 长上下文下用它才与 dsh 的实测吞吐可比。
    timings = data.get("timings") if isinstance(data, dict) else None
    predicted = timings.get("predicted_per_second") if isinstance(timings, dict) else None
    rate = float(predicted) if isinstance(predicted, int | float) and predicted > 0 else completion_tokens / elapsed
    return BenchResult(elapsed, prompt_tokens, completion_tokens, rate)


# ---------------------------------------------------------------------- #
# 体检
# ---------------------------------------------------------------------- #
def _check_writable(home: Path | None) -> Check:
    base = _home(home)
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".serve-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("状态目录", "fail", f"{base}: {exc}")
    return Check("状态目录", "ok", str(base))


def _check_gpu(runner: Runner) -> tuple[Check, GpuInfo | None]:
    gpu = detect_gpu(runner)
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
    for state in list_states(home):
        if state.port == port and process_alive(state.pid, state.create_time):
            return Check("端口", "ok", f"{port} 由服务 {state.name!r} 占用（pid={state.pid}）")
    return Check("端口", "warn", f"{port} 已被其他进程占用：fcmd portcheck c {port}")


def _check_disk(models_root: Path | None, home: Path | None) -> Check:
    roots = models_roots(models_root, home)
    root = roots[0]
    target = root if root.is_dir() else _home(home)
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return Check("磁盘", "warn", f"无法读取 {target}: {exc}")
    free_gib = usage.free / _GIB
    status: CheckStatus = "ok" if free_gib >= 30 else "warn"
    return Check("磁盘", status, f"{target.drive or target} 剩余 {free_gib:.1f} GiB")


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
        engine = resolve_engine(engine_tag, home)
    except ServeError as exc:
        checks.append(Check("推理引擎", "fail", str(exc)))
    else:
        checks.append(check_engine(engine, runner))

    profile = PROFILES[DEFAULT_PROFILE]
    try:
        model = find_model(profile.quant, models_root)
    except ServeError as exc:
        checks.append(Check("模型", "warn", str(exc)))
    else:
        estimate = estimate_vram(model.stat().st_size, profile.context, profile.kv_type)
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
