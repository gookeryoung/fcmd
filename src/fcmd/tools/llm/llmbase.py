"""llmbase - 大模型工具的公共基元：常量、错误、数据类型与通用辅助。

本模块不依赖其他 ``fcmd.tools.llm.*`` 模块，是所有上层模块的依赖终点。
只放可单测的纯逻辑与数据定义，不做终端输出、不注册 CLI。
"""

from __future__ import annotations

import codecs
import locale
import re
import shlex
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

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

#: 30B MoE 每 token 的 KV 缓存（FP16 约 96 KiB，q8_0 约减半）
KV_BYTES_PER_TOKEN_FP16 = 96 * 1024
#: CUDA context / 计算图 / 临时缓冲的经验开销
VRAM_OVERHEAD_BYTES = 2 * 1024**3
GIB = 1024**3
CHUNK_SIZE = 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENGINE_MARKER = "engine.json"
CURRENT_MARKER = "current.json"
KV_SCALE = {"f16": 1.0, "q8_0": 0.5, "q4_0": 0.3}
#: llama-quantize 启动即崩溃的常见原因提示
MSVC_HINT = (
    "llama-quantize 启动即崩溃通常由系统 VC++ 运行库（MSVCP140.dll）过旧导致，"
    "请安装最新 VC++ 2015-2022 x64 运行库，或将新版 msvcp140.dll 放入引擎目录"
)


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
    tags: tuple[str, ...] = ()

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
class Tuning:
    """调优策略解析后的生效值（由 ``_llmtune`` 按预设 / 硬件选择）。"""

    name: str = "default"
    label: str = "默认（现状）"
    detail: str = "保持现有启动参数"
    batch: int = 4096
    ubatch: int | None = None
    parallel: int = 1
    cache_reuse: int = 256
    flash_attn: str = "on"
    kv_type: str | None = None
    threads_http: int | None = None
    spec_type: str | None = None


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
    tuning: Tuning = Tuning()

    @property
    def base_url(self) -> str:
        """OpenAI 兼容服务根地址（不含 ``/v1``）。"""
        return http_base_url(self.host, self.port)

    @property
    def effective_kv_type(self) -> str:
        """生效的 KV 缓存类型（显式覆盖 > 调优策略 > 档位）。"""
        return self.kv_type or self.tuning.kv_type or self.profile.kv_type

    @property
    def effective_ubatch(self) -> int:
        """生效的 ubatch（显式覆盖 > 调优策略 > 档位）。"""
        return self.ubatch or self.tuning.ubatch or self.profile.ubatch

    def argv(self, backend: Backend | None = None) -> list[str]:
        """按后端生成启动参数列表。"""
        return (backend or LlamaCppBackend()).build_argv(self)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """任意 OpenAI 兼容服务的接入点（bench 等模块只依赖它）。"""

    base_url: str
    api_key: str
    model: str


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

    @property
    def endpoint(self) -> Endpoint:
        """转换为 bench 等模块使用的接入点。"""
        return Endpoint(self.base_url, self.api_key, self.alias)

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StopResult:
    """停止服务的结果。"""

    state: ServeState
    was_running: bool


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
        """生成 llama-server 参数（调优策略 + FlashAttention + KV 量化）。"""
        tuning = plan.tuning
        args = [
            str(plan.engine.exe),
            "-m",
            str(plan.model),
            "-ngl",
            "99",
            "-c",
            str(plan.context),
            "-np",
            str(tuning.parallel),
            "--flash-attn",
            tuning.flash_attn,
            "-ctk",
            plan.effective_kv_type,
            "-ctv",
            plan.effective_kv_type,
            "-b",
            str(tuning.batch),
            "-ub",
            str(plan.effective_ubatch),
            "-t",
            str(plan.threads),
            "--jinja",
        ]
        if tuning.cache_reuse > 0:
            args += ["--cache-reuse", str(tuning.cache_reuse)]
        if tuning.threads_http:
            args += ["--threads-http", str(tuning.threads_http)]
        if tuning.spec_type:
            args += ["--spec-type", tuning.spec_type]
        args += [
            "--no-context-shift",
            "--metrics",
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
        return args


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


STREAM_CHUNK = 4096
_LINE_SPLIT = re.compile(r"\r\n|\r|\n")


def stream_command(
    cmd: Sequence[str],
    timeout: float = 15.0,
    on_output: Callable[[str], None] | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """执行外部命令并实时逐行回调输出。

    与 :func:`run_command` 一次性捕获不同，这里通过管道逐块读取，能即时收到
    tqdm 等用 ``\\r`` 刷新进度的输出；``on_output`` 按 ``\\r`` / ``\\n`` 切分后
    逐条回调（忽略空行）。全部输出仍会汇总到返回值的 ``stdout`` 中。

    Raises
    ------
    subprocess.TimeoutExpired
        命令超时，进程已被终止。
    OSError
        命令无法启动。
    """
    process = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        env=env,
    )
    decoder = codecs.getincrementaldecoder(locale.getpreferredencoding(False))("replace")
    captured: list[str] = []
    pending = ""

    def consume(text: str) -> None:
        nonlocal pending
        pending += text
        parts = _LINE_SPLIT.split(pending)
        pending = parts.pop()
        if on_output is not None:
            for part in parts:
                if part:
                    on_output(part)

    def read_stream() -> None:
        stream = process.stdout
        if stream is None:
            return
        read_chunk = cast("Callable[[int], bytes]", getattr(stream, "read1", None) or stream.read)
        while packet := read_chunk(STREAM_CHUNK):
            text = decoder.decode(packet)
            if text:
                captured.append(text)
                consume(text)
        tail = decoder.decode(b"", final=True)
        if tail:
            captured.append(tail)
            consume(tail)
        if pending and on_output is not None:
            on_output(pending)

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        reader.join()
        if process.stdout is not None:
            process.stdout.close()
    return CommandResult(returncode, "".join(captured), "")


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
    if not NAME_RE.fullmatch(name):
        raise ServeError(f"服务名不合法: {name!r}（仅允许字母数字与 . _ -，且不能以符号开头）")
    return name
