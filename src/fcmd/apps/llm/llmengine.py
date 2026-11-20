"""llmengine - llama.cpp 引擎探测、安装、注册、切换与量化器定位。

独立命令：``fcmd llmengine <list|releases|install|use|check|inspect>``。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import time
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import fcmd
from fcmd.apps.llm import llmenv
from fcmd.apps.llm.llmbase import (
    CHUNK_SIZE,
    CURRENT_MARKER,
    DEFAULT_ENGINE_TAG,
    ENGINE_MARKER,
    Check,
    CommandResult,
    EngineInfo,
    EngineVariant,
    GpuInfo,
    Opener,
    Runner,
    ServeError,
    format_returncode,
    human_size,
    now_iso,
    run_command,
)
from fcmd.console import Table, get_console
from fcmd.infras.jsonio import read_json_file, write_json_atomic

__all__ = [
    "ENGINE_VARIANTS",
    "EngineAsset",
    "EngineRelease",
    "ZipInspection",
    "check_engine",
    "current_engine_tag",
    "detect_gpu",
    "engine_asset_names",
    "engine_env",
    "engine_exe_name",
    "fetch_engine_releases",
    "inspect_zip",
    "inspect_zips",
    "install_engine",
    "list_engines",
    "parse_engine_releases",
    "probe_version",
    "quantize_exe",
    "resolve_engine",
    "run_with_engine",
    "use_engine",
]

ZipKind = Literal["engine", "cudart", "source", "unknown", "invalid"]
#: 下载进度回调：(归档名, 已下载字节, 总字节或 ``None``)
ProgressCallback = Callable[[str, int, int | None], None]

_SERVER_NAMES = ("llama-server.exe", "llama-server")
_QUANTIZE_NAMES = ("llama-quantize.exe", "llama-quantize")
_CONVERT_SCRIPT = "convert_hf_to_gguf.py"
_ENGINE_ARCHIVE_RE = re.compile(r"^llama-(?P<tag>b\d+)-bin-(?P<os>[A-Za-z0-9]+)-(?P<rest>.+)\.zip$", re.IGNORECASE)
_VARIANTS = ("cuda", "vulkan", "sycl", "hip", "metal", "opencl", "cpu")
_KIND_LABELS = {
    "engine": "引擎",
    "cudart": "CUDA 运行库",
    "source": "源码包",
    "unknown": "未知",
    "invalid": "无效",
}


@dataclass(frozen=True, slots=True)
class ZipInspection:
    """一个 zip 离线包的检查结果。

    Attributes
    ----------
    path:
        压缩包路径。
    kind:
        包类型：引擎 / CUDA 运行库 / 源码包 / 未知 / 无效。
    valid:
        zip 是否可正常读取。
    tag:
        从文件名或包内 ``engine.json`` 推断的引擎 tag。
    variant:
        从文件名推断的构建变体（``cuda`` / ``cpu`` / ``vulkan`` 等）。
    server:
        包内 ``llama-server`` 的相对路径。
    quantize:
        包内 ``llama-quantize`` 的相对路径。
    entries:
        条目数量。
    total_size:
        解压后总字节数。
    bad_member:
        深度校验发现的首个损坏条目。
    problems:
        阻止安装的问题。
    notes:
        类型提示（如运行库需与引擎包一起安装）。
    """

    path: Path
    kind: ZipKind
    valid: bool
    tag: str | None = None
    variant: str | None = None
    server: str | None = None
    quantize: str | None = None
    entries: int = 0
    total_size: int = 0
    bad_member: str | None = None
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """是否可直接作为引擎包安装。"""
        return self.valid and self.bad_member is None and self.kind == "engine"


@dataclass(frozen=True, slots=True)
class EngineAsset:
    """GitHub release 里的一个 Windows 预编译 zip 资产。

    ``variant`` 是构建变体（``cuda`` / ``cpu`` / ``vulkan`` / ``hip`` 等）；
    ``runtime`` 为真表示这是 CUDA 运行库包（与引擎包一起解压到同一目录）。
    """

    name: str
    url: str
    size: int
    variant: str
    arch: str
    runtime: bool = False


@dataclass(frozen=True, slots=True)
class EngineRelease:
    """一个可安装的 llama.cpp release（版本列表条目）。"""

    tag: str
    published: str
    assets: tuple[EngineAsset, ...]

    def variants(self, arch: str | None = None) -> tuple[str, ...]:
        """当前机器可用的构建变体；GPU 变体优先，CPU 兜底，未知变体按出现顺序垫后。"""
        target = arch or _host_arch()
        seen: list[str] = []
        for asset in self.assets:
            if asset.runtime or asset.arch != target or asset.variant in seen:
                continue
            seen.append(asset.variant)
        return tuple(sorted(seen, key=lambda variant: _VARIANT_ORDER.get(variant, len(_VARIANT_ORDER))))

    def install_assets(self, variant: str, arch: str | None = None) -> tuple[EngineAsset, ...]:
        """安装指定变体需要的资产：引擎包在前、运行库包在后。"""
        target = arch or _host_arch()
        picked = [asset for asset in self.assets if asset.variant == variant and asset.arch == target]
        return tuple(sorted(picked, key=lambda asset: asset.runtime))


#: Windows 预编译引擎包名：``llama-b7000-bin-win-cuda-13.5-x64.zip``。
_ENGINE_ASSET_RE = re.compile(r"^llama-(?P<tag>b\d+)-bin-win-(?P<rest>.+)-(?P<arch>x64|arm64)\.zip$", re.I)
#: CUDA 运行库包名：``cudart-llama-bin-win-cuda-13.5-x64.zip``。
_CUDART_ASSET_RE = re.compile(r"^cudart-llama-bin-win-(?P<rest>.+)-(?P<arch>x64|arm64)\.zip$", re.I)
#: 构建变体的显示名与用途说明（引擎下载 UI 与 ``fcmd llmengine releases`` 共用）。
ENGINE_VARIANTS: Final[dict[str, tuple[str, str]]] = {
    "cuda": ("CUDA", "NVIDIA 显卡；自带 CUDA 运行库，有 NVIDIA GPU 时首选"),
    "vulkan": ("Vulkan", "任意支持 Vulkan 的显卡（NVIDIA / AMD / Intel），兼容性优先"),
    "rocm": ("ROCm", "AMD 显卡（ROCm 运行时，需匹配驱动版本）"),
    "hip": ("HIP", "AMD 显卡（HIP 构建，通常随 ROCm 包提供）"),
    "sycl": ("SYCL", "Intel Arc 独显 / 集显（oneAPI / SYCL）"),
    "openvino": ("OpenVINO", "Intel CPU / GPU / NPU（OpenVINO 加速）"),
    "metal": ("Metal", "Apple Silicon（macOS Metal）"),
    "opencl": ("OpenCL", "老显卡 / 其它平台（OpenCL 兼容兜底）"),
    "cpu": ("CPU", "仅 CPU 或显卡不兼容时使用，兼容性最好但速度最慢"),
}
#: 变体下拉的默认顺序：先 GPU 加速，最后 CPU 兜底。
_VARIANT_ORDER: Final[dict[str, int]] = {variant: index for index, variant in enumerate(ENGINE_VARIANTS)}
_GITHUB_RELEASES_URL: Final[str] = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
#: 国内可用的 GitHub 镜像前缀（默认优选）；环境变量 ``FCMD_GITHUB_MIRROR`` 可覆盖，
#: 设为 ``direct`` / ``none`` / 空串时只用 GitHub 直连。
_GITHUB_MIRROR_PREFIXES: Final[tuple[str, ...]] = (
    "https://gh-proxy.com/",
    "https://gh-proxy.net/",
    "https://ghproxy.net/",
)
_GITHUB_HOSTS: Final[frozenset[str]] = frozenset({"github.com", "api.github.com", "objects.githubusercontent.com"})
_RELEASE_TIMEOUT: Final[float] = 6.0
_DOWNLOAD_TIMEOUT: Final[float] = 20.0


def _mirror_prefixes() -> tuple[str, ...]:
    """生效的镜像前缀（未设环境变量时用内置国内优选）。"""
    raw = os.environ.get("FCMD_GITHUB_MIRROR")
    if raw is None:
        return _GITHUB_MIRROR_PREFIXES
    prefixes: list[str] = []
    for item in raw.split(","):
        value = item.strip()
        if not value or value.lower() in {"direct", "none", "off"}:
            continue
        prefixes.append(value if value.endswith("/") else f"{value}/")
    return tuple(prefixes)


def _mirror_candidates(url: str) -> list[str]:
    """给 GitHub / API 地址加国内镜像前缀；非 GitHub 地址与直连原样保留。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname not in _GITHUB_HOSTS:
        return [url]
    candidates = [f"{prefix}{url}" for prefix in _mirror_prefixes()]
    candidates.append(url)
    return list(dict.fromkeys(candidates))


def _host_arch() -> str:
    """当前机器架构：``arm64`` / ``x64``（官方 Windows 包只区分这两种）。"""
    return "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"


def _asset_size(raw: Any) -> int:
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def _parse_asset(raw: Any) -> EngineAsset | None:
    """把一个 GitHub asset 转成 :class:`EngineAsset`；非 Windows 预编译包返回 ``None``。"""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    url = raw.get("browser_download_url")
    if not isinstance(name, str) or not isinstance(url, str):
        return None
    engine = _ENGINE_ASSET_RE.match(name)
    runtime = _CUDART_ASSET_RE.match(name)
    match = engine or runtime
    if match is None:
        return None
    rest = match.group("rest")
    variant = rest.split("-", 1)[0].lower()
    if not variant:
        return None
    return EngineAsset(
        name=name,
        url=url,
        size=_asset_size(raw.get("size")),
        variant=variant,
        arch=match.group("arch").lower(),
        runtime=runtime is not None,
    )


def parse_engine_releases(payload: Any, *, limit: int = 10) -> tuple[EngineRelease, ...]:
    """解析 GitHub releases JSON，保留带 Windows 引擎包的版本（纯函数，供测试）。"""
    if not isinstance(payload, list):
        return ()
    releases: list[EngineRelease] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        tag = entry.get("tag_name")
        raw_assets = entry.get("assets")
        if not isinstance(tag, str) or not tag.strip() or not isinstance(raw_assets, list):
            continue
        assets = tuple(asset for asset in (_parse_asset(raw) for raw in raw_assets) if asset is not None)
        if not any(not asset.runtime for asset in assets):
            continue
        published = entry.get("published_at") or entry.get("created_at") or ""
        releases.append(EngineRelease(tag.strip(), str(published), assets))
        if len(releases) >= max(limit, 1):
            break
    return tuple(releases)


def fetch_engine_releases(limit: int = 10, *, opener: Opener | None = None) -> tuple[EngineRelease, ...]:
    """读取 llama.cpp 的 GitHub releases（版本列表）；网络失败抛 :class:`ServeError`。

    默认先试国内镜像（``gh-proxy.com`` / ``gh-proxy.net``），失败再直连 GitHub；
    所有地址都失败时一次报错，最多等待 ``_RELEASE_TIMEOUT`` × 候选数。
    """
    from urllib.request import Request, urlopen

    open_fn = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
    # GitHub 会过滤掉无 Windows 资产的 release（含源码包 / 预览版），多取一些再截断，
    # 避免 ``--limit 1`` 这类请求因首选条目被过滤而显示「没有可下载版本」。
    page_size = min(max(limit * 2, 10), 100)
    base = f"{_GITHUB_RELEASES_URL}?per_page={page_size}"
    errors: list[str] = []
    payload: Any = None
    for candidate in _mirror_candidates(base):
        req = Request(
            candidate,
            headers={"User-Agent": "fcmd-llm/0.3.10", "Accept": "application/vnd.github+json"},
        )
        try:
            with open_fn(req, _RELEASE_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            errors.append(f"{candidate}（{exc}）")
            continue
        break
    if payload is None:
        raise ServeError("获取引擎版本列表失败：" + "；".join(errors))
    return parse_engine_releases(payload, limit=limit)


@dataclass(slots=True)
class _GpuCache:
    """默认 ``run_command`` 的进程内 GPU 查询缓存。"""

    stamp: float = 0.0
    info: GpuInfo | None = None
    valid: bool = False


_GPU_CACHE_TTL_SECONDS: Final[float] = 3.0
"""``nvidia-smi`` 结果的进程内复用窗口（秒）：建窗期多条线程各查一次 GPU，
3 秒内复用同一结果可少起 2–3 个进程；窗口外照常重查，GPU 变化最多滞后 3 秒。"""

_gpu_cache = _GpuCache()


def _query_gpu(runner: Runner) -> GpuInfo | None:
    """实际执行一次 ``nvidia-smi`` 查询。"""
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


def detect_gpu(runner: Runner = run_command) -> GpuInfo | None:
    """用 ``nvidia-smi`` 查询第一块 GPU；不可用时返回 ``None``。

    默认查询在 :data:`_GPU_CACHE_TTL_SECONDS` 内复用结果：建窗时硬件检测、设置页
    与模型扫描会各查一次，缓存可少起 2–3 个 ``nvidia-smi`` 进程。传入自定义
    ``runner``（测试替身）时始终实际执行，不缓存，也不影响调用方拿到的返回值。
    """
    if runner is not run_command:
        return _query_gpu(runner)
    cache = _gpu_cache
    if cache.valid and time.monotonic() - cache.stamp < _GPU_CACHE_TTL_SECONDS:
        return cache.info
    info = _query_gpu(runner)
    cache.stamp, cache.info, cache.valid = time.monotonic(), info, True
    return info


def engine_exe_name(base: str = "llama-server") -> str:
    """引擎可执行文件名（Windows 带 ``.exe``）。"""
    return f"{base}.exe" if os.name == "nt" else base


def _shallowest_member(names: Sequence[str], candidates: Sequence[str]) -> str | None:
    """在 zip 条目中查找文件名匹配的条目（优先层级最浅的）。"""
    wanted = {name.lower() for name in candidates}
    matches = [name for name in names if not name.endswith("/") and name.rsplit("/", 1)[-1].lower() in wanted]
    return min(matches, key=lambda name: name.count("/")) if matches else None


def _archive_variant(name: str) -> str | None:
    """从归档文件名推断构建变体。"""
    lowered = name.lower()
    return next((variant for variant in _VARIANTS if variant in lowered), None)


def _archive_marker(bundle: zipfile.ZipFile) -> dict[str, Any]:
    """读取压缩包内（可带一层目录）的 ``engine.json``。"""
    marker = _shallowest_member(bundle.namelist(), (ENGINE_MARKER,))
    if marker is None:
        return {}
    try:
        info = bundle.getinfo(marker)
        if info.file_size > CHUNK_SIZE:
            return {}
        data = json.loads(bundle.read(marker).decode("utf-8"))
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def inspect_zip(archive: Path, *, deep: bool = False) -> ZipInspection:
    """检查单个 zip 离线包的类型、内容与完整性。

    Parameters
    ----------
    archive:
        要检查的压缩包。
    deep:
        逐条目做 CRC 校验（大压缩包会更慢）。
    """
    source = archive.expanduser()
    if not source.is_file():
        return ZipInspection(source, "invalid", False, problems=(f"文件不存在: {source}",))
    if not zipfile.is_zipfile(source):
        return ZipInspection(source, "invalid", False, problems=("不是有效的 zip 压缩包",))
    try:
        with zipfile.ZipFile(source) as bundle:
            infos = bundle.infolist()
            names = [info.filename for info in infos]
            marker = _archive_marker(bundle)
            bad_member = bundle.testzip() if deep else None
    except (OSError, zipfile.BadZipFile) as exc:
        return ZipInspection(source, "invalid", False, problems=(f"读取失败: {exc}",))

    server = _shallowest_member(names, _SERVER_NAMES)
    quantize = _shallowest_member(names, _QUANTIZE_NAMES)
    if server is not None:
        kind: ZipKind = "engine"
    elif _shallowest_member(names, (_CONVERT_SCRIPT,)) is not None:
        kind = "source"
    elif source.name.lower().startswith("cudart"):
        kind = "cudart"
    else:
        kind = "unknown"

    tag = marker.get("tag") if isinstance(marker.get("tag"), str) else None
    if not tag:
        matched = _ENGINE_ARCHIVE_RE.match(source.name)
        tag = matched.group("tag") if matched else None

    problems: list[str] = []
    notes: list[str] = []
    if kind == "unknown":
        problems.append(f"未找到 llama-server（{engine_exe_name()}）")
    elif kind == "source":
        notes.append("这是 llama.cpp 源码包，不能作为引擎安装")
    elif kind == "cudart":
        notes.append("CUDA 运行库包，需与引擎包一起安装")
    if bad_member is not None:
        problems.append(f"CRC 校验失败: {bad_member}")

    return ZipInspection(
        path=source,
        kind=kind,
        valid=True,
        tag=tag,
        variant=_archive_variant(source.name),
        server=server,
        quantize=quantize,
        entries=len(infos),
        total_size=sum(info.file_size for info in infos),
        bad_member=bad_member,
        problems=tuple(problems),
        notes=tuple(notes),
    )


def inspect_zips(paths: Sequence[Path], *, deep: bool = False) -> list[ZipInspection]:
    """检查多个 zip / 目录（目录展开为其中的 ``*.zip``）。"""
    archives: list[Path] = []
    for raw in paths:
        path = raw.expanduser()
        if path.is_dir():
            archives.extend(sorted(item for item in path.glob("*.zip") if item.is_file()))
        else:
            archives.append(path)
    return [inspect_zip(archive, deep=deep) for archive in archives]


def _read_marker(directory: Path) -> dict[str, Any]:
    """读取引擎目录下的 ``engine.json``（缺失或损坏返回空字典）。"""
    marker = directory / ENGINE_MARKER
    if not marker.is_file():
        return {}
    try:
        data = read_json_file(marker)
    except (OSError, ValueError):
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


def engine_env(engine: EngineInfo) -> dict[str, str] | None:
    """为引擎构造带 ``path_prepend`` 的进程环境；无需追加时返回 ``None``。"""
    if not engine.path_prepend:
        return None
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([*engine.path_prepend, env.get("PATH", "")])
    return env


def run_with_engine(runner: Runner, cmd: Sequence[str], engine: EngineInfo | None = None) -> CommandResult:
    """执行外部命令；引擎带 ``path_prepend`` 时透传环境。"""
    env = engine_env(engine) if engine is not None else None
    if env is None:
        return runner(cmd)
    return runner(cmd, env=env)


def probe_version(engine: EngineInfo, runner: Runner = run_command) -> str:
    """执行 ``llama-server --version`` 取首行版本信息。"""
    result = run_with_engine(runner, [str(engine.exe), "--version"], engine)
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        detail = output.splitlines()[0] if output else f"退出码 {format_returncode(result.returncode)}"
        return f"获取失败（{detail}）"
    return output.splitlines()[0] if output else "未知"


def current_engine_tag(root: Path) -> str | None:
    """读取 ``current.json`` 中记录的当前引擎 tag。"""
    marker = root / CURRENT_MARKER
    if not marker.is_file():
        return None
    try:
        data = read_json_file(marker)
    except (OSError, ValueError):
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

    root = llmenv.engines_root(home)
    if tag:
        info = _engine_info(root / tag, tag)
        if info is None:
            raise ServeError(f"引擎 {tag!r} 未安装：fcmd llmengine install --tag {tag}")
        return info

    current = current_engine_tag(root)
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
    raise ServeError("未找到 llama.cpp 引擎：先执行 fcmd llmengine install --tag " + DEFAULT_ENGINE_TAG)


def engine_asset_names(tag: str, variant: EngineVariant = "cuda") -> list[str]:
    """返回官方 release 资产名（仅 Windows 有预编译包）。"""
    if os.name != "nt":
        raise ServeError("非 Windows 平台没有官方预编译包：请用 --url 指定资产，或用 --path 注册已编译引擎目录")
    if variant == "cpu":
        return [f"llama-{tag}-bin-win-cpu-x64.zip"]
    return [f"llama-{tag}-bin-win-cuda-13.5-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip"]


def _release_url(tag: str, asset: str) -> str:
    return f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{asset}"


def _content_length(resp: Any) -> int | None:
    """读取响应 ``Content-Length``（缺失或非法时返回 ``None``）。"""
    headers = getattr(resp, "headers", None)
    raw = headers.get("Content-Length") if headers is not None else None
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _download_once(
    url: str,
    dest: Path,
    opener: Opener | None,
    on_progress: ProgressCallback | None,
) -> int:
    """单次下载（不重试）：失败时抛出 ``OSError`` 交由调用方换镜像。"""
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": "fcmd-llm/0.3.10"})
    open_fn = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
    with open_fn(req, _DOWNLOAD_TIMEOUT) as resp, dest.open("wb") as out:
        expected = _content_length(resp)
        if on_progress is not None:
            on_progress(dest.name, 0, expected)
        received = 0
        while chunk := resp.read(CHUNK_SIZE):
            out.write(chunk)
            received += len(chunk)
            if on_progress is not None:
                on_progress(dest.name, received, expected)
    return received


def _download(
    url: str,
    dest: Path,
    opener: Opener | None = None,
    on_progress: ProgressCallback | None = None,
) -> int:
    """下载 URL 到文件，返回字节数；GitHub 地址按国内镜像优先、失败回退直连。

    镜像来自 :data:`_GITHUB_MIRROR_PREFIXES` / ``FCMD_GITHUB_MIRROR``；每次尝试
    都有连接 / 读取超时，全部失败时抛出一次带完整地址列表的 :class:`ServeError`，
    避免界面停在「正在下载」且无法退出。
    """
    errors: list[str] = []
    for candidate in _mirror_candidates(url):
        try:
            return _download_once(candidate, dest, opener, on_progress)
        except OSError as exc:
            errors.append(f"{candidate}（{exc}）")
            dest.unlink(missing_ok=True)
    raise ServeError("下载失败: " + "；".join(errors))


def _extract_zip(archive: Path, target: Path, *, remove: bool = True) -> None:
    """解压 zip 到目标目录；``remove`` 为真时解压后删除压缩包。"""
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ServeError(f"解压失败: {archive}（{exc}）") from exc
    finally:
        if remove:
            archive.unlink(missing_ok=True)


def _install_from_archives(tag: str, archives: Sequence[Path], target: Path) -> EngineInfo:
    """把多个本地 zip（引擎包 + cudart 等）解压到同一引擎目录。"""
    sources = [item.expanduser() for item in archives]
    for source in sources:
        check = inspect_zip(source)
        if not check.valid:
            raise ServeError(f"离线包无效: {source}（{'；'.join(check.problems)}）")
        if check.kind == "source":
            raise ServeError(f"这是 llama.cpp 源码包，不能作为引擎安装: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for source in sources:
        _extract_zip(source, target, remove=False)
    exe = _engine_exe_in(target)
    if exe is None:
        joined = "、".join(str(source) for source in sources)
        raise ServeError(f"离线包内找不到 {engine_exe_name()}: {joined}")
    _write_engine_marker(target, tag, exe, "；".join(str(source) for source in sources))
    return EngineInfo(tag, exe, str(sources[0]))


def _install_from_path(tag: str, path: Path, target: Path, *, copy: bool = False) -> EngineInfo:
    """注册已编译引擎目录、复制目录，或解压单个本地 zip 离线包。"""
    source = path.expanduser()
    if source.is_file():
        if source.suffix.lower() != ".zip":
            raise ServeError(f"仅支持注册引擎目录或本地 .zip 压缩包: {source}")
        target.mkdir(parents=True, exist_ok=True)
        _extract_zip(source, target, remove=False)
        exe = _engine_exe_in(target)
        if exe is None:
            raise ServeError(f"压缩包内找不到 {engine_exe_name()}: {source}")
        _write_engine_marker(target, tag, exe, str(source))
        return EngineInfo(tag, exe, str(source))
    if not source.is_dir():
        raise ServeError(f"引擎目录不存在: {source}")
    source_exe = _engine_exe_in(source)
    if source_exe is None:
        raise ServeError(f"目录内找不到 {engine_exe_name()}: {source}")
    if copy and source.resolve() != target.resolve():
        if target.resolve().is_relative_to(source.resolve()):
            raise ServeError(f"目标引擎目录位于源目录内，无法复制: {target}")
        try:
            shutil.copytree(source, target, dirs_exist_ok=True)
        except OSError as exc:
            raise ServeError(f"复制引擎目录失败: {source} -> {target}（{exc}）") from exc
        exe = _engine_exe_in(target)
        if exe is None:
            raise ServeError(f"复制后仍找不到 {engine_exe_name()}: {target}")
    else:
        target.mkdir(parents=True, exist_ok=True)
        exe = source_exe
    prepend = _detect_path_prepend(exe)
    _write_engine_marker(target, tag, exe, str(source), prepend)
    return EngineInfo(tag, exe, str(source), prepend)


def install_engine(  # noqa: PLR0913
    tag: str = DEFAULT_ENGINE_TAG,
    *,
    variant: EngineVariant = "cuda",
    url: str | None = None,
    path: Path | None = None,
    archives: Sequence[Path] | None = None,
    asset_names: Sequence[str] | None = None,
    home: Path | None = None,
    opener: Opener | None = None,
    on_progress: ProgressCallback | None = None,
    copy: bool = False,
) -> EngineInfo:
    """安装或注册 llama.cpp 引擎。

    ``path`` 为已编译引擎目录时默认只登记不复制，``copy=True`` 时复制到引擎目录；
    为本地 ``.zip`` 压缩包时直接解压安装（压缩包保留），两种情况都不联网。
    ``archives`` 可传入多个离线包
    （引擎包 + cudart 运行库包），一次解压到同一目录。否则从 GitHub release
    下载 Windows 预编译包（``cuda`` 变体额外下载 cudart）；``asset_names`` 可指定
    release 实际存在的资产名（版本列表选择时使用），缺省按 ``variant`` 推导；
    目标目录内已有完整 zip 时直接复用。

    ``on_progress`` 仅在联网下载时回调，用于界面展示实时进度。
    """
    target = llmenv.engines_root(home) / tag

    if path is not None and archives is not None:
        raise ServeError("path 与 archives 不能同时指定")
    if archives is not None:
        if not archives:
            raise ServeError("未指定任何离线包")
        return _install_from_archives(tag, archives, target)
    if path is not None:
        return _install_from_path(tag, path, target, copy=copy)

    cached = _engine_exe_in(target) if target.exists() else None
    if cached is not None:
        return EngineInfo(tag, cached, str(target))

    target.mkdir(parents=True, exist_ok=True)
    if url:
        urls = [url]
    else:
        names = list(asset_names) if asset_names else engine_asset_names(tag, variant)
        urls = [_release_url(tag, name) for name in names]
    for item in urls:
        archive = target / (item.rsplit("/", 1)[-1] or f"{tag}.zip")
        if not (archive.is_file() and zipfile.is_zipfile(archive)):
            _download(item, archive, opener, on_progress)
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
    write_json_atomic(target / ENGINE_MARKER, payload)


def list_engines(home: Path | None = None) -> list[EngineInfo]:
    """列出已安装引擎（按 tag 降序，标记当前使用的 tag）。"""
    root = llmenv.engines_root(home)
    if not root.is_dir():
        return []
    current = current_engine_tag(root)
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
    root = llmenv.engines_root(home)
    info = _engine_info(root / tag, tag)
    if info is None:
        raise ServeError(f"引擎 {tag!r} 未安装：fcmd llmengine install --tag {tag}")
    root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(root / CURRENT_MARKER, {"tag": tag, "updated_at": now_iso()})
    return info


def check_engine(engine: EngineInfo, runner: Runner = run_command) -> Check:
    """执行 ``llama-server --version`` 验证引擎可运行（能捕获 0xC0000005 崩溃）。"""
    result = run_with_engine(runner, [str(engine.exe), "--version"], engine)
    if result.returncode == 0:
        first = (result.stdout or result.stderr).strip().splitlines()
        return Check("推理引擎", "ok", f"{engine.exe}（{first[0] if first else 'version ok'}）")
    detail = result.stderr.strip() or result.stdout.strip() or "无输出"
    return Check(
        "推理引擎",
        "fail",
        f"{engine.exe} --version 退出码 {format_returncode(result.returncode)}；{detail[:200]}",
    )


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


# ---------------------------------------------------------------------- #
# CLI：fcmd llmengine <list|install|use|check|inspect>
# ---------------------------------------------------------------------- #
@fcmd.tool("llmengine", subcommand="list", help="列出已安装引擎")
def list_cmd() -> None:
    """列出已安装的 llama.cpp 引擎与当前默认引擎。"""
    infos = list_engines()
    if not infos:
        get_console().print(f"未安装引擎：fcmd llmengine install --tag {DEFAULT_ENGINE_TAG}")
        return
    table = Table(title="llama.cpp 引擎")
    table.add_column("TAG")
    table.add_column("当前")
    table.add_column("可执行文件")
    for info in infos:
        table.add_row(info.tag, "*" if info.source == "current" else "", str(info.exe))
    get_console().print(table)


@fcmd.tool("llmengine", subcommand="install", help="安装或注册引擎")
def install_cmd(  # noqa: PLR0913, PLR0917 - CLI 参数与 install_engine 对齐
    tag: str = DEFAULT_ENGINE_TAG,
    variant: EngineVariant = "cuda",
    url: str | None = None,
    path: Path | None = None,
    zips: list[Path] | None = None,
    assets: list[str] | None = None,
) -> None:
    """从 GitHub release 安装引擎，或注册本地引擎目录 / zip。

    Parameters
    ----------
    tag:
        引擎 tag（安装目录名），默认 ``b12211``。
    variant:
        预编译包变体：``cuda`` / ``cpu``。
    url:
        直接指定压缩包地址（优先于官方 release 资产名）。
    path:
        已编译引擎目录，或单个本地 ``.zip`` 离线包。
    zips:
        本地 zip 离线包列表（如引擎包 + cudart 运行库包），一次解压安装。
    assets:
        release 里实际存在的资产名（可重复）；用 ``llmengine releases --assets``
        查到后可用于非 cuda / cpu 变体或 CUDA 版本与默认推导不同的 release。
    """
    if path is not None and zips is not None:
        raise ServeError("--path 与 --zips 不能同时使用")
    if assets and (url is not None or path is not None or zips is not None):
        raise ServeError("--asset 不能与 --url / --path / --zips 同时使用")
    info = install_engine(tag, variant=variant, url=url, path=path, archives=zips, asset_names=assets)
    get_console().print(f"引擎就绪: {info.tag} -> {info.exe}")
    if not any(item.source == "current" for item in list_engines()):
        use_engine(tag)


@fcmd.tool("llmengine", subcommand="releases", help="列出可下载的引擎版本")
def releases_cmd(limit: int = 10, assets: bool = False) -> None:
    """列出 llama.cpp 官方 release 中带 Windows 预编译包的版本。

    Parameters
    ----------
    limit:
        最多展示多少个版本（新版在前）。
    assets:
        同时列出每个版本的具体 zip 资产名，供 ``install --asset`` 使用。
    """
    try:
        releases = fetch_engine_releases(limit=limit)
    except ServeError as exc:
        get_console().print(f"[red]{exc}[/red]")
        return
    if not releases:
        get_console().print("没有可下载的引擎版本（检查网络或稍后重试）")
        return
    table = Table(title="可下载的 llama.cpp 引擎")
    table.add_column("TAG")
    table.add_column("发布")
    table.add_column("变体")
    table.add_column("大小", justify="right", no_wrap=True)
    for release in releases:
        variants = release.variants()
        first = release.install_assets(variants[0]) if variants else ()
        table.add_row(
            release.tag,
            release.published[:10] or "-",
            " / ".join(ENGINE_VARIANTS.get(variant, (variant, ""))[0] for variant in variants) or "-",
            human_size(sum(asset.size for asset in first)),
        )
    get_console().print(table)
    present = {variant for release in releases for variant in release.variants()}
    if present:
        get_console().print("变体说明：")
        for variant, (label, hint) in ENGINE_VARIANTS.items():
            if variant in present:
                get_console().print(f"  {label}：{hint}")
    if assets:
        for release in releases:
            for asset in release.assets:
                get_console().print(f"{release.tag}  {asset.name}  {human_size(asset.size)}")
        get_console().print("安装指定资产：fcmd llmengine install --tag <TAG> --asset <名称> [--asset <名称>]")
    else:
        get_console().print("查看具体资产名：fcmd llmengine releases --assets")


@fcmd.tool("llmengine", subcommand="inspect", help="检查 zip 离线包")
def inspect_cmd(paths: list[Path], deep: bool = False) -> None:
    """检查 zip 离线包的类型、内容与完整性。

    Parameters
    ----------
    paths:
        zip 文件或包含 zip 的目录（可多个）。
    deep:
        逐条目做 CRC 校验（大压缩包会更慢）。
    """
    inspections = inspect_zips(paths, deep=deep)
    if not inspections:
        get_console().print("没有找到 zip 压缩包")
        return

    table = Table(title="zip 离线包检查", box="ascii")
    table.add_column("文件", style="cyan")
    table.add_column("类型", no_wrap=True)
    table.add_column("TAG", no_wrap=True)
    table.add_column("变体", no_wrap=True)
    table.add_column("llama-server")
    table.add_column("大小", justify="right", no_wrap=True)
    table.add_column("状态")
    for item in inspections:
        table.add_row(
            item.path.name,
            _KIND_LABELS[item.kind],
            item.tag or "-",
            item.variant or "-",
            item.server or "-",
            human_size(item.total_size),
            "可用" if item.usable else "不可安装",
        )
    get_console().print(table)

    for item in inspections:
        for note in item.notes:
            get_console().print(f"[yellow]{item.path.name}: {note}[/yellow]")
        for problem in item.problems:
            get_console().print(f"[red]{item.path.name}: {problem}[/red]")


@fcmd.tool("llmengine", subcommand="use", help="切换默认引擎")
def use_cmd(tag: str) -> None:
    """把指定 tag 设为默认引擎。"""
    info = use_engine(tag)
    get_console().print(f"默认引擎: {info.tag} -> {info.exe}")


@fcmd.tool("llmengine", subcommand="check", help="验证引擎可运行")
def check_cmd(tag: str | None = None) -> None:
    """执行 ``llama-server --version`` 并输出体检结果。"""
    engine = resolve_engine(tag)
    check = check_engine(engine)
    color = {"ok": "green", "warn": "yellow", "fail": "red"}[check.status]
    get_console().print(f"[{color}]{check.status}[/{color}] {check.name}: {check.detail}")


@fcmd.main("llmengine")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行
