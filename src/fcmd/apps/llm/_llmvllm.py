"""_llmvllm - vLLM 引擎：平台探测、安装、切换、HF 模型扫描与启动参数。

vLLM 官方只提供 Linux 轮子：本机 Linux 直接跑；Windows 走 WSL2（GPU 由
``/usr/lib/wsl/lib/nvidia-smi`` 直通）。引擎沿用 llama.cpp / sd.cpp 的目录约定::

    ${home}/engines/vllm/<tag>/engine.json    Windows 侧镜像（tag / 解释器 / 发行版）
    ${home}/engines/vllm/current.json         默认引擎

真实 venv 建在 WSL 内 ``~/.fcmd/engines/vllm/<tag>/venv``（drvfs 上跑 torch
既慢又容易踩到 inotify / 权限问题），Windows 只保留元数据，列表无需调用 WSL。

模型侧只接受 HF safetensors 目录（``config.json`` + ``*.safetensors``）：GGUF
继续走 llama.cpp，vLLM 的 GGUF 路径不是其性能路径，也不在本次支持范围。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from fcmd.apps.llm import _llmconfig, llmenv
from fcmd.apps.llm.llmbase import (
    CURRENT_MARKER,
    ENGINE_MARKER,
    PROFILES,
    EngineInfo,
    LaunchPlan,
    ServeError,
    ServeState,
    format_returncode,
    now_iso,
    run_command,
    stream_command,
)
from fcmd.infras.jsonio import read_json_file, write_json_atomic

__all__ = [
    "ENGINE_KIND",
    "HfModel",
    "VllmBackend",
    "VllmEngine",
    "VllmInterpreter",
    "VllmPlatform",
    "current_tag",
    "detect_platform",
    "engines_root",
    "fetch_versions",
    "install_command",
    "install_engine",
    "install_script",
    "is_hf_model_dir",
    "list_engines",
    "make_plan",
    "parse_probe",
    "parse_versions",
    "parse_wsl_distros",
    "parse_wsl_rows",
    "python_supported",
    "reset_platform_cache",
    "resolve_engine",
    "scan_hf_models",
    "stop_engine",
    "use_engine",
    "wsl_path",
]

ENGINE_KIND = "vllm"
"""服务状态里的引擎类型标记；停止时据此追加 WSL 侧清理。"""


_PYPI_URL: Final[str] = "https://pypi.org/pypi/vllm/json"
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?$")
_SKIP_DIRS: Final[frozenset[str]] = frozenset({".git", "__pycache__", "node_modules"})


# ---------------------------------------------------------------------- #
# 平台探测（实现在 :mod:`fcmd.apps.llm._llmvllm_platform`，这里 re-export 保持既有导入路径）
# ---------------------------------------------------------------------- #
from fcmd.apps.llm._llmvllm_platform import (  # noqa: E402 - 放在常量之后以保持文件结构
    _WSL_EXE,
    VllmInterpreter,
    VllmPlatform,
    _version_tuple,
    detect_platform,
    parse_probe,
    parse_wsl_distros,
    parse_wsl_rows,
    python_supported,
    reset_platform_cache,
)


# ---------------------------------------------------------------------- #
# 引擎目录
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class VllmEngine:
    """已安装的 vLLM 引擎（解释器在 WSL / 本机内的绝对路径）。"""

    tag: str
    python: str
    distro: str = ""
    platform: str = "linux"
    venv: str = ""
    home_dir: str = ""
    source: str = ""

    @property
    def in_wsl(self) -> bool:
        """是否需要经 ``wsl.exe`` 启动。"""
        return self.platform == "wsl" and bool(self.distro)

    @property
    def launcher(self) -> str:
        """服务状态里的启动器标记（停止时据此清理 WSL 进程）。"""
        return f"wsl:{self.distro}" if self.in_wsl else "linux"

    @property
    def display_exe(self) -> str:
        """给 UI 展示的解释器路径（WSL 内路径）。"""
        return self.python

    def translate(self, path: Path) -> str:
        """把 Windows 路径转成引擎所在环境可读的路径。"""
        return wsl_path(path) if self.in_wsl else str(path)

    def starmap(self) -> list[str]:
        """引擎前缀命令：``wsl.exe -d <distro> -- python`` 或本机 python。"""
        if self.in_wsl:
            return [_WSL_EXE, "-d", self.distro, "--", self.python]
        return [self.python]


def engines_root(home: Path | None = None) -> Path:
    """vLLM 引擎根目录（FCMD_VLLM_ENGINES → 配置 → ``${home}/engines/vllm``）。"""
    return _llmconfig.vllm_engines_root(home)


def _read_marker(directory: Path) -> dict[str, Any]:
    marker = directory / ENGINE_MARKER
    if not marker.is_file():
        return {}
    try:
        data = read_json_file(marker)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _engine_in(directory: Path) -> VllmEngine | None:
    """从一个引擎目录读取 ``engine.json``；非 vLLM 引擎返回 ``None``。"""
    data = _read_marker(directory)
    if data.get("kind") != ENGINE_KIND:
        return None
    tag = data.get("tag")
    python = data.get("python")
    if not isinstance(tag, str) or not isinstance(python, str) or not tag or not python:
        return None
    return VllmEngine(
        tag=tag,
        python=python,
        distro=str(data.get("distro") or ""),
        platform=str(data.get("platform") or "linux"),
        venv=str(data.get("venv") or ""),
        home_dir=str(data.get("home_dir") or ""),
        source=str(data.get("source") or ""),
    )


def current_tag(home: Path | None = None) -> str | None:
    """读取 ``current.json`` 里的默认 vLLM 引擎 tag。"""
    marker = engines_root(home) / CURRENT_MARKER
    if not marker.is_file():
        return None
    try:
        data = read_json_file(marker)
    except (OSError, ValueError):
        return None
    tag = data.get("tag") if isinstance(data, dict) else None
    return str(tag) if tag else None


def list_engines(home: Path | None = None) -> list[VllmEngine]:
    """已安装的 vLLM 引擎（按 tag 降序，默认引擎 ``source`` 为 ``current``）。"""
    root = engines_root(home)
    if not root.is_dir():
        return []
    current = current_tag(home)
    engines: list[VllmEngine] = []
    for directory in sorted(root.iterdir(), key=lambda item: _version_tuple(item.name), reverse=True):
        if not directory.is_dir():
            continue
        engine = _engine_in(directory)
        if engine is None:
            continue
        if directory.name == current:
            engine = VllmEngine(
                engine.tag,
                engine.python,
                engine.distro,
                engine.platform,
                engine.venv,
                engine.home_dir,
                "current",
            )
        engines.append(engine)
    return engines


def use_engine(tag: str, home: Path | None = None) -> VllmEngine:
    """把指定 tag 设为默认 vLLM 引擎。"""
    root = engines_root(home)
    engine = _engine_in(root / tag)
    if engine is None:
        raise ServeError(f"vLLM 引擎 {tag!r} 未安装：在设置页「推理引擎（vLLM）」中安装")
    root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(root / CURRENT_MARKER, {"tag": tag, "updated_at": now_iso()})
    return engine


def resolve_engine(tag: str | None = None, home: Path | None = None) -> VllmEngine:
    """解析 vLLM 引擎：指定 tag → current → 最新安装。"""
    root = engines_root(home)
    if tag:
        engine = _engine_in(root / tag)
        if engine is None:
            raise ServeError(f"vLLM 引擎 {tag!r} 未安装：在设置页「推理引擎（vLLM）」中安装")
        return engine
    current = current_tag(home)
    if current:
        engine = _engine_in(root / current)
        if engine is not None:
            return engine
    engines = list_engines(home)
    if engines:
        return engines[0]
    raise ServeError("未找到 vLLM 引擎：先在设置页「推理引擎（vLLM）」中安装")


# ---------------------------------------------------------------------- #
# 安装
# ---------------------------------------------------------------------- #
def install_script(tag: str, target_dir: str, python: str) -> str:
    """生成安装脚本（在目标环境内执行）：建 venv + 升级 pip + 安装 vllm。

    Ubuntu / Debian 默认不带 ``ensurepip``（``python3-venv``），直接建 venv 会
    失败；这里在失败时用 apt 补装一次（root 或 sudo），再重试。其它发行版
    不擅自改系统包，保持原始报错。
    """
    quote = shlex.quote
    venv = f"{target_dir}/venv"
    return "\n".join(
        (
            "set -eu",
            f"mkdir -p {quote(target_dir)}",
            f"if ! {quote(python)} -m venv {quote(venv)}; then",
            "  if command -v apt-get >/dev/null 2>&1; then",
            '    if [ "$(id -u)" -eq 0 ]; then',
            "      apt-get update && apt-get install -y python3-venv",
            "    else",
            "      sudo apt-get update && sudo apt-get install -y python3-venv",
            "    fi",
            f"    {quote(python)} -m venv {quote(venv)}",
            "  else",
            "    echo '创建 venv 失败：请先安装 python3-venv（或对应发行版的 venv 包）' >&2",
            "    exit 1",
            "  fi",
            "fi",
            f"{quote(venv)}/bin/python -m pip install --upgrade pip",
            f"{quote(venv)}/bin/python -m pip install 'vllm=={tag}'",
            f"echo FCMD_VLLM_READY {tag}",
        )
    )


def install_command(interpreter: VllmInterpreter, tag: str, target_dir: str) -> list[str]:
    """安装命令：WSL 经 ``wsl.exe -d … -- bash -lc``，Linux 直接 ``bash -lc``。"""
    script = install_script(tag, target_dir, interpreter.python)
    if interpreter.distro:
        return [_WSL_EXE, "-d", interpreter.distro, "--", "bash", "-lc", script]
    return ["bash", "-lc", script]


def _tail(text: str, lines: int = 12) -> str:
    parts = [item for item in text.replace("\r", "\n").split("\n") if item.strip()]
    return "\n".join(parts[-lines:])


def install_engine(  # noqa: PLR0913 - 可注入平台 / 执行器，便于设置页与测试复用
    tag: str,
    *,
    interpreter: VllmInterpreter | None = None,
    home: Path | None = None,
    on_output: Any = None,
    timeout: float = 7200.0,
    execute: Any = None,
) -> VllmEngine:
    """在 WSL / 本机安装指定版本的 vLLM，并登记为引擎。

    ``on_output`` 接收逐行安装日志（设置页安装对话框用）；``execute`` 可注入
    命令执行器，便于测试。安装成功后若还没有默认引擎，自动设为默认。
    """
    if not _TAG_RE.fullmatch(tag):
        raise ServeError(f"vLLM 版本号不合法: {tag!r}")
    # 安装必然要在实例里跑命令（等于启动它）：调用方已确认，允许探测已停止实例。
    platform = detect_platform(use_cache=False, allow_start=True)
    if not platform.supported:
        raise ServeError(platform.reason)
    chosen = interpreter or platform.primary
    if chosen is None:
        raise ServeError(platform.reason)

    if chosen.distro:
        target = f"{chosen.home.rstrip('/')}/.fcmd/engines/vllm/{tag}"
    else:
        target = str(Path(chosen.home or str(Path.home())) / ".fcmd" / "engines" / "vllm" / tag)
    command = install_command(chosen, tag, target)
    runner = execute or (lambda cmd, seconds, sink: stream_command(cmd, timeout=seconds, on_output=sink))
    try:
        result = runner(command, timeout, on_output)
    except OSError as exc:
        raise ServeError(f"启动安装命令失败: {exc}") from exc
    except Exception as exc:  # stream_command 超时 / 取消
        raise ServeError(f"安装 vLLM {tag} 失败: {exc}") from exc
    if result.returncode != 0:
        raise ServeError(f"安装 vLLM {tag} 失败（{format_returncode(result.returncode)}）：\n{_tail(result.stdout)}")

    engine = VllmEngine(
        tag=tag,
        python=f"{target}/venv/bin/python",
        distro=chosen.distro,
        platform="wsl" if chosen.distro else "linux",
        venv=f"{target}/venv",
        home_dir=target,
        source="pip",
    )
    _write_marker(engine, home)
    if not any(item.source == "current" for item in list_engines(home)):
        use_engine(tag, home)
    return engine


def _write_marker(engine: VllmEngine, home: Path | None = None) -> None:
    """写入 Windows 侧镜像 ``engine.json``（真实 venv 在 WSL 内）。"""
    directory = engines_root(home) / engine.tag
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": ENGINE_KIND,
        "tag": engine.tag,
        "python": engine.python,
        "distro": engine.distro,
        "platform": engine.platform,
        "venv": engine.venv,
        "home_dir": engine.home_dir,
        "source": engine.source,
        "installed_at": now_iso(),
    }
    write_json_atomic(directory / ENGINE_MARKER, payload)


# ---------------------------------------------------------------------- #
# 版本列表
# ---------------------------------------------------------------------- #
def parse_versions(payload: Any, *, limit: int = 12) -> tuple[str, ...]:
    """从 PyPI JSON 提取稳定版本，按版本号降序。"""
    releases = payload.get("releases") if isinstance(payload, dict) else None
    if not isinstance(releases, dict):
        return ()
    stable: list[str] = []
    for tag, files in releases.items():
        if not isinstance(tag, str) or _VERSION_RE.match(tag) is None:
            continue
        if not isinstance(files, list) or not files:
            continue
        if any(isinstance(item, dict) and item.get("yanked") for item in files):
            continue
        stable.append(tag)
    stable.sort(key=_version_tuple, reverse=True)
    return tuple(stable[: max(limit, 1)])


def fetch_versions(limit: int = 12, *, opener: Any = None) -> tuple[str, ...]:
    """读取 PyPI 上的 vLLM 版本列表；网络失败抛 :class:`ServeError`。"""
    from urllib.request import Request, urlopen

    open_fn = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
    url = os.environ.get("FCMD_VLLM_PYPI", "").strip() or _PYPI_URL
    try:
        with open_fn(Request(url, headers={"User-Agent": "fcmd-llm"}), 15.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ServeError(f"获取 vLLM 版本列表失败：{exc}") from exc
    versions = parse_versions(payload, limit=limit)
    if not versions:
        raise ServeError("vLLM 版本列表为空：请检查网络或 PyPI 源")
    return versions


# ---------------------------------------------------------------------- #
# HF 模型
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class HfModel:
    """一个 HF safetensors 模型目录。"""

    root: Path
    path: Path
    size: int = 0


def is_hf_model_dir(path: Path) -> bool:
    """目录是否是 HF 模型（``config.json`` + 至少一个 ``*.safetensors``）。"""
    if not path.is_dir():
        return False
    if not (path / "config.json").is_file():
        return False
    return any(path.glob("*.safetensors"))


def _safetensors_size(directory: Path) -> int:
    total = 0
    for item in directory.glob("*.safetensors"):
        with contextlib.suppress(OSError):
            total += item.stat().st_size
    return total


def scan_hf_models(
    roots: list[Path] | None = None,
    home: Path | None = None,
    *,
    max_depth: int = 4,
) -> list[HfModel]:
    """扫描模型目录中的 HF safetensors 模型（按大小降序）。"""
    targets = roots if roots is not None else _llmconfig.models_dirs(home=home)
    found: list[HfModel] = []
    seen: set[str] = set()
    for root in targets:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            try:
                depth = len(current.relative_to(root).parts)
            except ValueError:
                continue
            if depth > max_depth:
                dirnames.clear()
                continue
            dirnames[:] = [item for item in dirnames if item not in _SKIP_DIRS]
            if "config.json" in filenames and any(name.endswith(".safetensors") for name in filenames):
                key = _llmconfig.path_key(current)
                dirnames.clear()
                if key in seen:
                    continue
                seen.add(key)
                found.append(HfModel(root=root, path=current, size=_safetensors_size(current)))
    found.sort(key=lambda item: (-item.size, str(item.path)))
    return found


# ---------------------------------------------------------------------- #
# 启动 / 停止
# ---------------------------------------------------------------------- #
_MOUNT_RE: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z]):[\\/]")


def wsl_path(path: Path) -> str:
    """Windows 路径转 WSL 路径：``D:\\models\\x`` → ``/mnt/d/models/x``。"""
    text = str(path)
    match = _MOUNT_RE.match(text)
    if match is None:
        return text.replace("\\", "/")
    drive = match.group(1).lower()
    rest = text[2:].replace("\\", "/").lstrip("/")
    return f"/mnt/{drive}/{rest}"


@dataclass(slots=True)
class VllmBackend:
    """把 :class:`LaunchPlan` 翻译成 vLLM OpenAI 服务命令。"""

    engine: VllmEngine
    name: str = "vLLM"

    def build_argv(self, plan: LaunchPlan) -> list[str]:
        """生成 ``vllm serve`` 命令行（官方入口，兼容旧版模块入口）。"""
        bind = "0.0.0.0" if self.engine.in_wsl else plan.host
        if self.engine.venv:
            # ``venv/bin/vllm serve`` 是官方文档入口；``python -u <script>``
            # 跳过 shebang 以保留无缓冲输出。
            entry = [str(PurePosixPath(self.engine.venv) / "bin" / "vllm"), "serve"]
        else:
            entry = ["-m", "vllm.entrypoints.openai.api_server"]
        args = [
            *self.engine.starmap(),
            # ``-u`` 关掉 Python 输出缓冲：日志立即落到服务日志文件，
            # 否则 vLLM 的启动日志会攒在缓冲区里，失败时日志为空难以排查。
            "-u",
            *entry,
            "--model",
            self.engine.translate(plan.model),
            "--served-model-name",
            plan.alias,
            "--host",
            bind,
            "--port",
            str(plan.port),
            "--max-model-len",
            str(plan.context),
        ]
        if plan.api_key:
            args += ["--api-key", plan.api_key]
        args += list(plan.extra_args)
        return args


def make_plan(  # noqa: PLR0913 - 与 llmserve.make_plan 对齐的启动参数
    name: str,
    model: Path,
    engine: VllmEngine,
    *,
    host: str,
    port: int,
    context: int,
    alias: str,
    api_key: str,
    gpu_memory_utilization: float | None = None,
    max_num_seqs: int | None = None,
    extra_args: tuple[str, ...] = (),
    home: Path | None = None,
) -> LaunchPlan:
    """构造 vLLM 启动计划（复用 llmserve 的启动 / 健康检查 / 停止流程）。"""
    if not is_hf_model_dir(model):
        raise ServeError(f"vLLM 需要 HF safetensors 模型目录（config.json + *.safetensors）: {model}")
    if not 1 <= port <= 65535:
        raise ServeError(f"端口不合法: {port}")
    if context < 1:
        raise ServeError(f"上下文必须为正整数: {context}")
    extra = list(extra_args)
    if gpu_memory_utilization is not None:
        if not 0 < gpu_memory_utilization <= 1:
            raise ServeError(f"显存占比需在 (0, 1] 之间: {gpu_memory_utilization}")
        extra[:0] = ["--gpu-memory-utilization", f"{gpu_memory_utilization:.2f}"]
    if max_num_seqs is not None:
        if max_num_seqs < 1:
            raise ServeError(f"最大并发序列必须为正整数: {max_num_seqs}")
        extra[:0] = ["--max-num-seqs", str(max_num_seqs)]
    profile = PROFILES["balanced"]
    exe = Path(_WSL_EXE) if engine.in_wsl else Path(engine.python)
    return LaunchPlan(
        name=name,
        model=model.resolve(),
        profile=profile,
        context=context,
        host=host,
        port=port,
        alias=alias,
        api_key=api_key,
        threads=0,
        engine=EngineInfo(engine.tag, exe, f"vllm:{engine.platform}"),
        log=llmenv.log_path(name, home),
        extra_args=tuple(extra),
        backend=VllmBackend(engine),
        engine_kind=ENGINE_KIND,
        launcher=engine.launcher,
    )


def stop_engine(state: ServeState) -> None:
    """停止 WSL 内残留的 vLLM 进程（Windows 侧 wsl.exe 已终止时兜底）。

    ``wsl.exe`` 被结束后 Linux 侧不一定同步退出；按端口定位进程再 ``pkill``，
    避免端口一直占用。找不到进程时静默返回。
    """
    if state.engine_kind != ENGINE_KIND:
        return
    launcher = state.launcher or ""
    if not launcher.startswith("wsl:"):
        return
    distro = launcher[4:]
    if not distro:
        return
    pattern = f"vllm.entrypoints.openai.api_server.*--port {state.port}"
    with contextlib.suppress(OSError):
        run_command([_WSL_EXE, "-d", distro, "--", "pkill", "-f", pattern], 10.0)
