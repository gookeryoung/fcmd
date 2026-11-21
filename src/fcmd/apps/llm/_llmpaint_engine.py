"""_llmpaint_engine - stable-diffusion.cpp 引擎的安装、切换与体检。

引擎来自 ``leejet/stable-diffusion.cpp`` 的 GitHub release：Windows x64 预编译包
``sd-master-<seq>-<hash>-bin-win-<variant>-x64.zip``；CUDA 变体额外需要
``cudart-sd-bin-win-cu12-x64.zip``。安装目录与 llama.cpp 引擎同构
（``${home}/engines/sd.cpp/<tag>`` + ``engine.json`` + ``current.json``），
下载 / 解压复用 :mod:`fcmd.apps.llm.llmengine` 的镜像与容错逻辑。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from fcmd.apps.llm import _llmconfig, llmengine
from fcmd.apps.llm.llmbase import (
    CURRENT_MARKER,
    ENGINE_MARKER,
    Check,
    GpuInfo,
    Opener,
    Runner,
    ServeError,
    format_returncode,
    now_iso,
    run_command,
)
from fcmd.infras.jsonio import read_json_file, write_json_atomic

__all__ = [
    "SD_EXE",
    "VC_RUNTIME_DLLS",
    "SdAsset",
    "SdEngine",
    "SdRelease",
    "check_sd_engine",
    "current_sd_tag",
    "ensure_vc_runtime",
    "fetch_sd_releases",
    "install_sd_engine",
    "list_sd_engines",
    "locmodel_sd_engine",
    "missing_cuda_runtime",
    "parse_sd_releases",
    "pick_default_variant",
    "resolve_sd_engine",
    "sd_engine_env",
    "use_sd_engine",
    "variant_label",
]

SD_REPO = "leejet/stable-diffusion.cpp"
SD_EXE = "sd-server"
VC_RUNTIME_DLLS: Final[tuple[str, ...]] = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
"""sd-server 依赖的 MSVC 运行库；系统自带 2015 版（14.0）会与新版构建不兼容。"""
_SD_EXE_NAMES: Final[tuple[str, ...]] = ("sd-server.exe", "sd-server")
_SD_RELEASES_URL: Final[str] = "https://api.github.com/repos/leejet/stable-diffusion.cpp/releases"
_RELEASE_TIMEOUT: Final[float] = 6.0
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: 引擎包：``sd-master-1106-532d2c1-bin-win-cuda12-x64.zip``。
_ENGINE_ASSET_RE: Final[re.Pattern[str]] = re.compile(
    # 新版资产名省略 release 序号（``sd-master-532d2c1-...``），旧版是
    # ``sd-master-1106-532d2c1-...``；两种都要能解析，否则版本列表为空。
    r"^sd-(?P<tag>master-(?:\d+-)?[0-9a-f]+)-bin-win-(?P<variant>cuda\d*|vulkan|cpu|rocm-[\d.]+)-x64\.zip$",
    re.IGNORECASE,
)
#: CUDA 运行库包：``cudart-sd-bin-win-cu12-x64.zip``。
_CUDART_ASSET_RE: Final[re.Pattern[str]] = re.compile(
    r"^cudart-sd-bin-win-(?P<variant>cu\d+)-x64\.zip$",
    re.IGNORECASE,
)

_VARIANT_ORDER: Final[dict[str, int]] = {"cuda": 0, "vulkan": 1, "rocm": 2, "cpu": 9}


def _variant_rank(variant: str) -> int:
    """变体排序权重：CUDA → Vulkan → ROCm → CPU → 其他（按前缀归并）。"""
    key = variant.lower()
    for prefix, rank in _VARIANT_ORDER.items():
        if key.startswith(prefix):
            return rank
    return 5


_VARIANT_LABELS: Final[dict[str, str]] = {
    "cuda12": "CUDA 12",
    "vulkan": "Vulkan",
    "cpu": "CPU",
}


@dataclass(frozen=True, slots=True)
class SdAsset:
    """GitHub release 里的一个 Windows 预编译 zip 资产。

    ``runtime`` 为真表示这是 CUDA 运行库包（与引擎包一起解压到同一目录）。
    """

    name: str
    url: str
    size: int
    variant: str
    runtime: bool = False


@dataclass(frozen=True, slots=True)
class SdRelease:
    """一个可安装的 stable-diffusion.cpp release（版本列表条目）。"""

    tag: str
    published: str
    assets: tuple[SdAsset, ...]

    def variants(self) -> tuple[str, ...]:
        """当前 release 可用的构建变体（GPU 变体优先，CPU 兜底）。"""
        seen: list[str] = []
        for asset in self.assets:
            if asset.runtime or asset.variant in seen:
                continue
            seen.append(asset.variant)
        return tuple(sorted(seen, key=_variant_rank))

    def install_assets(self, variant: str) -> tuple[SdAsset, ...]:
        """安装指定变体需要的资产：引擎包在前、CUDA 运行库包在后。"""
        engines = [asset for asset in self.assets if not asset.runtime and asset.variant == variant]
        runtime = [asset for asset in self.assets if asset.runtime] if variant.lower().startswith("cuda") else []
        return (*engines, *runtime)


@dataclass(frozen=True, slots=True)
class SdEngine:
    """已解析的 stable-diffusion.cpp 引擎。"""

    tag: str
    exe: Path
    source: str = ""
    backend: str = ""
    path_prepend: tuple[str, ...] = ()


def _asset_size(raw: Any) -> int:
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def _parse_asset(raw: Any) -> SdAsset | None:
    """把一个 GitHub asset 转成 :class:`SdAsset`；非 Windows 包返回 ``None``。"""
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
    return SdAsset(
        name=name,
        url=url,
        size=_asset_size(raw.get("size")),
        variant=match.group("variant").lower(),
        runtime=runtime is not None,
    )


def parse_sd_releases(payload: Any, *, limit: int = 10) -> tuple[SdRelease, ...]:
    """解析 GitHub releases JSON，保留带 Windows 引擎包的版本（纯函数）。"""
    if not isinstance(payload, list):
        return ()
    releases: list[SdRelease] = []
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
        releases.append(SdRelease(tag.strip(), str(published), assets))
        if len(releases) >= max(limit, 1):
            break
    return tuple(releases)


def fetch_sd_releases(limit: int = 10, *, opener: Opener | None = None) -> tuple[SdRelease, ...]:
    """读取 stable-diffusion.cpp 的 GitHub releases；网络失败抛 :class:`ServeError`。"""
    from urllib.request import Request, urlopen

    open_fn = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
    page_size = min(max(limit * 2, 10), 100)
    base = f"{_SD_RELEASES_URL}?per_page={page_size}"
    errors: list[str] = []
    payload: Any = None
    for candidate in llmengine.mirror_candidates(base):
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
        raise ServeError("获取画图引擎版本列表失败：" + "；".join(errors))
    return parse_sd_releases(payload, limit=limit)


def pick_default_variant(variants: Sequence[str], gpu: GpuInfo | None = None) -> str | None:
    """按硬件选择默认变体：N 卡 → CUDA，否则 Vulkan，最后 CPU。"""
    available = list(variants)
    if not available:
        return None
    if gpu is not None:
        cuda = next((variant for variant in available if variant.lower().startswith("cuda")), None)
        if cuda is not None:
            return cuda
    for preferred in ("vulkan", "cpu"):
        if preferred in available:
            return preferred
    return available[0]


def variant_label(variant: str) -> str:
    """变体的展示名（``rocm-10.1.0`` 之类的动态变体按前缀归并）。"""
    if variant in _VARIANT_LABELS:
        return _VARIANT_LABELS[variant]
    prefix = variant.split("-", 1)[0].lower()
    if prefix == "rocm":
        return f"ROCm {variant.split('-', 1)[1]}" if "-" in variant else "ROCm"
    if prefix == "cuda":
        return f"CUDA {variant[4:]}" if variant.lower().startswith("cuda") else "CUDA"
    return variant


def _validate_tag(tag: str) -> str:
    if not _TAG_RE.fullmatch(tag):
        raise ServeError(f"引擎 tag 不合法: {tag!r}")
    return tag


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
    """在引擎目录内定位 ``sd-server``（优先读 ``engine.json`` 的 ``exe``）。"""
    raw = _read_marker(directory).get("exe")
    if isinstance(raw, str) and raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = directory / candidate
        if candidate.is_file():
            return candidate
    for name in _SD_EXE_NAMES:
        direct = directory / name
        if direct.is_file():
            return direct
    matches = sorted(
        (path for name in _SD_EXE_NAMES for path in directory.rglob(name) if path.is_file()),
        key=lambda path: len(path.parts),
    )
    return matches[0] if matches else None


def _detect_path_prepend(exe: Path) -> tuple[str, ...]:
    """探测引擎目录下含 DLL 的目录（CUDA 运行库常放在 ``cudart/bin``）。"""
    base = exe.parent
    candidates = (base, base / "bin", base / "cudart", base / "cudart" / "bin")
    dirs = [str(item.resolve()) for item in candidates if item.is_dir() and any(item.glob("*.dll"))]
    return tuple(dict.fromkeys(dirs))


def _engine_info(directory: Path, tag: str | None = None) -> SdEngine | None:
    """构造引擎信息（含 marker 里记录的 ``path_prepend`` / 后端）。"""
    exe = _engine_exe_in(directory)
    if exe is None:
        return None
    marker = _read_marker(directory)
    raw = marker.get("path_prepend")
    prepend = tuple(str(item) for item in raw) if isinstance(raw, list) else ()
    source = marker.get("source")
    backend = marker.get("backend")
    return SdEngine(
        tag=tag or directory.name,
        exe=exe,
        source=str(source or directory),
        backend=str(backend or ""),
        path_prepend=prepend,
    )


def _write_engine_marker(target: Path, engine: SdEngine) -> None:
    """写入引擎标记文件（绝对 exe，便于目录被移动后仍可诊断）。"""
    payload = {
        "tag": engine.tag,
        "exe": str(engine.exe),
        "source": engine.source,
        "backend": engine.backend,
        "path_prepend": list(engine.path_prepend),
        "installed_at": now_iso(),
    }
    write_json_atomic(target / ENGINE_MARKER, payload)


def current_sd_tag(home: Path | None = None) -> str | None:
    """读取 ``current.json`` 中记录的当前引擎 tag。"""
    return llmengine.current_engine_tag(_llmconfig.paint_engines_root(home))


def use_sd_engine(tag: str, home: Path | None = None) -> SdEngine:
    """把指定 tag 设为默认画图引擎。"""
    root = _llmconfig.paint_engines_root(home)
    info = _engine_info(root / tag, tag)
    if info is None:
        raise ServeError(f"画图引擎 {tag!r} 未安装：fcmd llmpaint engine-install --tag {tag}")
    root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(root / CURRENT_MARKER, {"tag": tag, "updated_at": now_iso()})
    return info


def list_sd_engines(home: Path | None = None) -> list[SdEngine]:
    """列出已安装画图引擎（按 tag 降序，标记当前使用的 tag）。"""
    root = _llmconfig.paint_engines_root(home)
    if not root.is_dir():
        return []
    current = current_sd_tag(home)
    infos: list[SdEngine] = []
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        info = _engine_info(directory)
        if info is None:
            continue
        if directory.name == current:
            info = SdEngine(info.tag, info.exe, "current", info.backend, info.path_prepend)
        infos.append(info)
    return infos


def locmodel_sd_engine(home: Path | None = None) -> SdEngine | None:
    """探测 LocModel 已安装的 sd.cpp 引擎（``%LOCALAPPDATA%/LocModel/engines/sd``）。

    只作为**缺失运行库时的兜底**：LocModel 的引擎目录自带 cublas / cudart，
    用户无需重复下载；找不到或结构不合法时返回 ``None``。
    """
    del home
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    root = Path(local) / "LocModel" / "engines" / "sd"
    marker = root / "engine.json"
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    server = raw.get("server_path")
    if not isinstance(server, str) or not server.strip():
        return None
    exe = Path(server)
    if not exe.is_file():
        return None
    tag = str(raw.get("tag") or root.name)
    backend = str(raw.get("variant") or "")
    return SdEngine(tag, exe, "LocModel", backend, _detect_path_prepend(exe))


def resolve_sd_engine(  # noqa: PLR0911, PLR0912 - 按优先级逐层回退
    tag: str | None = None, home: Path | None = None
) -> SdEngine:
    """解析要使用的画图引擎：``FCMD_SD_SERVER`` → 指定 tag → current → 最新安装 → LocModel → PATH。

    fcmd 自己的引擎若缺少 CUDA 运行库（cublas / cudart），优先回退到 LocModel
    已安装的完整引擎，避免 sd-server 在加载阶段直接崩溃。
    """
    env_raw = os.environ.get("FCMD_SD_SERVER")
    if env_raw:
        exe = Path(env_raw).expanduser()
        if not exe.is_file():
            raise ServeError(f"FCMD_SD_SERVER 指向的文件不存在: {exe}")
        return SdEngine("env", exe, "FCMD_SD_SERVER", "", _detect_path_prepend(exe))

    root = _llmconfig.paint_engines_root(home)
    if tag:
        info = _engine_info(root / tag, tag)
        if info is None:
            raise ServeError(f"画图引擎 {tag!r} 未安装：fcmd llmpaint engine-install --tag {tag}")
        return info

    current = current_sd_tag(home)
    if current:
        info = _engine_info(root / current, current)
        if info is not None:
            return info

    broken: SdEngine | None = None
    if root.is_dir():
        for directory in sorted(root.iterdir(), reverse=True):
            if directory.is_dir():
                info = _engine_info(directory)
                if info is None:
                    continue
                if not missing_cuda_runtime(info):
                    return info
                broken = broken or info
    which = shutil.which(SD_EXE)
    if which:
        return SdEngine("system", Path(which), "PATH", "", _detect_path_prepend(Path(which)))
    fallback = locmodel_sd_engine(home)
    if fallback is not None and not missing_cuda_runtime(fallback):
        return fallback
    if broken is not None:
        return broken
    raise ServeError("未找到 stable-diffusion.cpp 引擎：先执行 fcmd llmpaint engine-install")


def sd_engine_env(engine: SdEngine) -> dict[str, str] | None:
    """为引擎构造带 ``path_prepend`` 的进程环境；无需追加时返回 ``None``。"""
    if not engine.path_prepend:
        return None
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([*engine.path_prepend, env.get("PATH", "")])
    return env


def _vc_runtime_sources() -> tuple[Path, ...]:
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

    stable-diffusion.cpp 的 Windows 构建使用 MSVC 2022；系统只有 VC++ 2015
    （``msvcp140.dll 14.0``）时 sd-server 会在加载阶段 ``c0000005`` 崩溃且日志为空。
    这里优先从本机 Visual Studio / VC Redist 目录复制同名 DLL 到引擎目录；
    找不到时返回缺失列表，由启动层提示安装最新 VC++ 运行库。
    """
    target.mkdir(parents=True, exist_ok=True)
    missing = [name for name in VC_RUNTIME_DLLS if not (target / name).is_file()]
    if not missing:
        return ()
    for directory in sources if sources is not None else _vc_runtime_sources():
        if all((directory / name).is_file() for name in missing):
            for name in missing:
                shutil.copy2(directory / name, target / name)
            return ()
    return tuple(missing)


def missing_cuda_runtime(engine: SdEngine) -> tuple[str, ...]:
    """CUDA 构建缺少的 runtime DLL：返回缺失文件名（非 CUDA 引擎返回空）。

    stable-diffusion.cpp 的 CUDA zip 与 ``cudart-sd-bin-win-cu12-x64.zip`` 是两个
    资产；只解压引擎包时 ``cublas64_12.dll`` / ``cudart64_12.dll`` 缺失，进程会在
    加载阶段直接崩溃且日志为空，很难排查。安装 / 启动前显式检查并给出可操作提示。
    """
    directory = engine.exe.parent
    if not (directory / "ggml-cuda.dll").is_file():
        return ()
    return tuple(
        name for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll") if not (directory / name).is_file()
    )


def check_sd_engine(engine: SdEngine, runner: Runner = run_command) -> Check:
    """执行 ``sd-server --help`` 验证引擎可运行。"""
    argv = [str(engine.exe), "--help"]
    env = sd_engine_env(engine)
    result = runner(argv, env=env) if env is not None else runner(argv)
    output = (result.stdout or result.stderr).strip()
    if result.returncode == 0 or "usage" in output.lower():
        first = output.splitlines()[0] if output else "help ok"
        return Check("画图引擎", "ok", f"{engine.exe}（{first}）")
    detail = output.splitlines()[0] if output else f"退出码 {format_returncode(result.returncode)}"
    return Check("画图引擎", "fail", f"{engine.exe} --help 失败：{detail}")


def _tag_from_source(path: Path | None, url: str | None, archives: Sequence[Path] | None) -> str:
    """从本地路径 / URL / 离线包推断默认 tag。"""
    if path is not None:
        return path.expanduser().stem if path.is_file() else path.expanduser().name
    if archives:
        return archives[0].expanduser().stem
    if url:
        return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".zip") or "sd-local"
    return ""


def _with_sibling_cudart(archives: Sequence[Path]) -> list[Path]:
    """本地 CUDA 引擎 zip 旁边如果有 cudart 包，自动一起解压。"""
    resolved = [item.expanduser() for item in archives]
    names = {item.name.casefold() for item in resolved}
    if any("cudart" in name for name in names):
        return resolved
    if not any("cuda" in item.name.casefold() for item in resolved):
        return resolved
    for directory in {item.parent for item in resolved}:
        for candidate in directory.glob("cudart-sd-bin-win-*-x64.zip"):
            if candidate.is_file():
                resolved.append(candidate)
                break
    return resolved


def _install_from_archives(
    tag: str,
    archives: Sequence[Path],
    target: Path,
    *,
    backend: str,
) -> SdEngine:
    """把多个本地 zip（引擎包 + cudart 等）解压到同一引擎目录。"""
    sources = [item.expanduser() for item in archives]
    if not sources:
        raise ServeError("未指定任何离线包")
    for source in sources:
        if not source.is_file() or not zipfile.is_zipfile(source):
            raise ServeError(f"离线包无效（不是 zip）: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for source in sources:
        llmengine.extract_zip(source, target, remove=False)
    exe = _engine_exe_in(target)
    if exe is None:
        joined = "、".join(str(source) for source in sources)
        raise ServeError(f"离线包内找不到 {SD_EXE}: {joined}")
    engine = SdEngine(tag, exe, "；".join(str(source) for source in sources), backend, _detect_path_prepend(exe))
    _write_engine_marker(target, engine)
    return engine


def _install_from_path(tag: str, path: Path, target: Path, *, backend: str) -> SdEngine:
    """注册已编译引擎目录，或解压单个本地 zip 离线包。"""
    source = path.expanduser()
    if source.is_file():
        if source.suffix.lower() != ".zip":
            raise ServeError(f"仅支持注册引擎目录或本地 .zip 压缩包: {source}")
        return _install_from_archives(tag, _with_sibling_cudart([source]), target, backend=backend)
    if not source.is_dir():
        raise ServeError(f"引擎目录不存在: {source}")
    source_exe = _engine_exe_in(source)
    if source_exe is None:
        raise ServeError(f"目录内找不到 {SD_EXE}: {source}")
    target.mkdir(parents=True, exist_ok=True)
    prepend = _detect_path_prepend(source_exe)
    engine = SdEngine(tag, source_exe, str(source), backend, prepend)
    _write_engine_marker(target, engine)
    return engine


def _download_and_extract(  # noqa: PLR0913 - 安装路径参数集中传递
    tag: str,
    assets: Sequence[SdAsset],
    target: Path,
    *,
    backend: str,
    opener: Opener | None,
    on_progress: llmengine.ProgressCallback | None,
) -> SdEngine:
    """下载并解压 release 资产（目标目录内已有完整 zip 时直接复用）。"""
    target.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        archive = target / asset.name
        if not (archive.is_file() and zipfile.is_zipfile(archive)):
            llmengine.download_file(asset.url, archive, opener, on_progress)
        llmengine.extract_zip(archive, target)
    exe = _engine_exe_in(target)
    if exe is None:
        raise ServeError(f"安装后仍找不到 {SD_EXE}: {target}")
    engine = SdEngine(tag, exe, assets[0].url if assets else str(target), backend, _detect_path_prepend(exe))
    _write_engine_marker(target, engine)
    return engine


def install_sd_engine(  # noqa: PLR0913 - 安装入口参数较多，与 llmengine.install_engine 对齐
    tag: str | None = None,
    *,
    variant: str = "auto",
    url: str | None = None,
    path: Path | None = None,
    archives: Sequence[Path] | None = None,
    home: Path | None = None,
    opener: Opener | None = None,
    on_progress: llmengine.ProgressCallback | None = None,
    use: bool = False,
    gpu: GpuInfo | None = None,
) -> SdEngine:
    """安装或注册 stable-diffusion.cpp 引擎。

    ``path`` 为已编译引擎目录时只登记不复制；为本地 ``.zip`` 时直接解压安装。
    ``archives`` 可传多个离线包（引擎包 + cudart）。否则按 release 下载
    ``variant`` 对应的 Windows 预编译包（``auto`` 按 GPU 选择）。
    """
    if path is not None and archives is not None:
        raise ServeError("path 与 archives 不能同时指定")
    resolved_tag = _validate_tag(tag or _tag_from_source(path, url, archives))
    root = _llmconfig.paint_engines_root(home)
    target = root / resolved_tag
    backend = "" if variant == "auto" else variant

    if archives is not None:
        engine = _install_from_archives(resolved_tag, _with_sibling_cudart(archives), target, backend=backend)
    elif path is not None:
        engine = _install_from_path(resolved_tag, path, target, backend=backend)
    elif url:
        name = url.rstrip("/").rsplit("/", 1)[-1] or f"{resolved_tag}.zip"
        engine = _download_and_extract(
            resolved_tag,
            (SdAsset(name, url, 0, backend or "custom"),),
            target,
            backend=backend or "custom",
            opener=opener,
            on_progress=on_progress,
        )
    else:
        release = next((item for item in fetch_sd_releases(limit=20, opener=opener) if item.tag == resolved_tag), None)
        if release is None:
            raise ServeError(f"release {resolved_tag!r} 不存在或没有 Windows 预编译包")
        chosen = variant
        if chosen == "auto":
            chosen = pick_default_variant(release.variants(), gpu if gpu is not None else llmengine.detect_gpu()) or ""
        assets = release.install_assets(chosen)
        if not assets:
            raise ServeError(
                f"release {resolved_tag!r} 没有变体 {chosen!r}；可选: {', '.join(release.variants()) or '无'}"
            )
        engine = _download_and_extract(
            resolved_tag,
            assets,
            target,
            backend=chosen,
            opener=opener,
            on_progress=on_progress,
        )

    if use:
        use_sd_engine(resolved_tag, home)
    return engine
