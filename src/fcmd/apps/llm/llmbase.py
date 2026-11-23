"""llmbase - 大模型工具的公共基元：常量、错误、数据类型与通用辅助。

本模块不依赖其他 ``fcmd.apps.llm.*`` 模块，是所有上层模块的依赖终点。
只放可单测的纯逻辑与数据定义，不做终端输出、不注册 CLI。
"""

from __future__ import annotations

import codecs
import contextlib
import functools
import locale
import os
import re
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast

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
VC_RUNTIME_DLLS: Final[tuple[str, ...]] = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
"""引擎二进制依赖的 MSVC 运行库；系统自带 VC++ 2015（14.0）会让新构建启动即崩溃。"""


def vc_runtime_sources() -> tuple[Path, ...]:
    """本机可能携带新版 MSVC 运行库的目录（Visual Studio IDE / VC Redist）。"""
    roots = [Path(value) for value in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")) if value]
    patterns = (
        "Microsoft Visual Studio/*/*/Common7/IDE",
        "Microsoft Visual Studio/*/*/VC/Redist/MSVC/*/x64/Microsoft.VC*.CRT",
    )
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in patterns:
            found.extend(path for path in root.glob(pattern) if path.is_dir())
    return tuple(found)


def ensure_vc_runtime(target: Path, *, sources: Sequence[Path] | None = None) -> tuple[str, ...]:
    """确保引擎目录带新版 MSVC 运行库，返回仍缺失的 DLL 名。

    llama.cpp / stable-diffusion.cpp 的 Windows 构建用新版 MSVC；系统只有 VC++ 2015
    （``msvcp140.dll 14.0``）时进程会在加载阶段 ``c0000005`` 崩溃且日志为空。
    这里优先从本机 Visual Studio / VC Redist 目录复制同名 DLL 到引擎目录；
    找不到时返回缺失列表，由启动层提示安装最新 VC++ 运行库。
    """
    target.mkdir(parents=True, exist_ok=True)
    missing = [name for name in VC_RUNTIME_DLLS if not (target / name).is_file()]
    if not missing:
        return ()
    for directory in sources if sources is not None else vc_runtime_sources():
        if all((directory / name).is_file() for name in missing):
            for name in missing:
                with contextlib.suppress(OSError):
                    shutil.copy2(directory / name, target / name)
            return tuple(name for name in missing if not (target / name).is_file())
    return tuple(missing)


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
    """NVIDIA GPU 信息；``driver_version`` 供引擎适配判断使用。"""

    name: str
    memory_mib: int
    driver_version: str = ""


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
    parallel: int | None = None
    threads_http: int | None = None
    tuning: Tuning = Tuning()
    mmproj: Path | None = None
    backend: Backend | None = None
    engine_kind: str = ""
    launcher: str = ""

    @property
    def vision(self) -> bool:
        """是否启用视觉塔（存在 mmproj 即视为多模态）。"""
        return self.mmproj is not None

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

    @property
    def effective_parallel(self) -> int:
        """生效的并发槽（显式覆盖 > 调优策略）。"""
        return self.parallel if self.parallel is not None else self.tuning.parallel

    @property
    def effective_threads_http(self) -> int | None:
        """生效的 HTTP 线程数（显式覆盖 > 调优策略）。"""
        return self.threads_http if self.threads_http is not None else self.tuning.threads_http

    def argv(self, backend: Backend | None = None) -> list[str]:
        """按后端生成启动参数列表（计划自带后端优先，兜底 llama.cpp）。"""
        return (backend or self.backend or LlamaCppBackend()).build_argv(self)


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
    mmproj: str = ""
    vision: bool = False
    engine_kind: str = ""
    launcher: str = ""

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


@functools.lru_cache(maxsize=8)
def _flash_attn_style_cached(exe: str, _mtime: float) -> bool:
    """探测引擎的 ``--flash-attn`` 语法：``True`` 表示需要取值（on/off/auto）。

    缓存键包含可执行文件路径与修改时间，升级引擎后自动失效。探测走
    ``llama-server --help``：新版帮助是 ``--flash-attn [on|off|auto]``，
    旧版（如 b5019）是布尔开关 ``--flash-attn``。
    """
    result = run_command([exe, "--help"], timeout=15.0)
    text = f"{result.stdout}\n{result.stderr}"
    return "--flash-attn [" in text or "--flash-attn <" in text


def flash_attn_requires_value(exe: Path) -> bool:
    """引擎的 ``--flash-attn`` 是否需要取值（探测失败时按旧版布尔开关处理）。"""
    try:
        mtime = exe.stat().st_mtime
    except OSError:
        mtime = 0.0
    return _flash_attn_style_cached(str(exe), mtime)


def flash_attn_argv(exe: Path, value: str) -> list[str]:
    """按引擎语法生成 FlashAttention 参数。

    新旧 llama.cpp 对 ``--flash-attn`` 的解析不同：

    - 旧版（build ≤ 5xxx）：布尔开关，``--flash-attn``；
    - 新版（当前的官方构建）：必须取值，``--flash-attn on|off|auto``。

    传错会让引擎在参数解析阶段直接退出（``unknown value for --flash-attn``），
    因此这里按引擎探测结果生成，而不是固定一种写法。``off`` 不传参数。
    """
    if not value or value == "off":
        return []
    if flash_attn_requires_value(exe):
        return ["--flash-attn", value]
    return ["--flash-attn"]


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
        ]
        if plan.mmproj is not None:
            args += ["--mmproj", str(plan.mmproj)]
        # 视觉模式下强制单并发：多槽 + 图片在 llama.cpp 上易出现 batch 分配异常
        parallel = 1 if plan.vision else plan.effective_parallel
        args += [
            "-ngl",
            "99",
            "-c",
            str(plan.context),
            "-np",
            str(parallel),
        ]
        # --flash-attn 的语法随 llama.cpp 构建变化，按引擎探测结果生成参数
        args += flash_attn_argv(plan.engine.exe, tuning.flash_attn)
        args += [
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
        threads_http = plan.effective_threads_http
        if threads_http:
            args += ["--threads-http", str(threads_http)]
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


def stream_command(  # noqa: PLR0913, PLR0917 - 与 Popen 参数一一对应，保持扁平
    cmd: Sequence[str],
    timeout: float = 15.0,
    on_output: Callable[[str], None] | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    cancel: threading.Event | None = None,
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
    stop_watch = threading.Event()

    def watch_cancel() -> None:
        """轮询取消标记：命令长时间无输出时也能及时终止。"""
        while not stop_watch.wait(0.2):
            if cancel is not None and cancel.is_set():
                with contextlib.suppress(OSError):
                    process.kill()
                return

    watcher = threading.Thread(target=watch_cancel, daemon=True) if cancel is not None else None
    if watcher is not None:
        watcher.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        stop_watch.set()
        if watcher is not None:
            watcher.join(timeout=1.0)
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
