"""llmengine - llama.cpp 引擎探测、安装、注册、切换与量化器定位。"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fcmd.tools.llm import llmenv
from fcmd.tools.llm.llmbase import (
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
    now_iso,
    run_command,
)

__all__ = [
    "check_engine",
    "current_engine_tag",
    "detect_gpu",
    "engine_asset_names",
    "engine_env",
    "engine_exe_name",
    "install_engine",
    "list_engines",
    "quantize_exe",
    "resolve_engine",
    "run_with_engine",
    "use_engine",
]


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
    marker = directory / ENGINE_MARKER
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


def current_engine_tag(root: Path) -> str | None:
    """读取 ``current.json`` 中记录的当前引擎 tag。"""
    marker = root / CURRENT_MARKER
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

    root = llmenv.engines_root(home)
    if tag:
        info = _engine_info(root / tag, tag)
        if info is None:
            raise ServeError(f"引擎 {tag!r} 未安装：fcmd llmsrv engine install --tag {tag}")
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
    raise ServeError("未找到 llama.cpp 引擎：先执行 fcmd llmsrv engine install --tag " + DEFAULT_ENGINE_TAG)


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

    req = Request(url, headers={"User-Agent": "fcmd-llmsrv/0.3.10"})
    open_fn = opener or (lambda request, timeout: urlopen(request, timeout=timeout))
    try:
        with open_fn(req, 60.0) as resp, dest.open("wb") as out:
            total = 0
            while chunk := resp.read(CHUNK_SIZE):
                out.write(chunk)
                total += len(chunk)
    except OSError as exc:
        raise ServeError(f"下载失败: {url}（{exc}）") from exc
    return total


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

    ``path`` 为已编译引擎目录时只登记不复制；为本地 ``.zip`` 压缩包时直接解压
    安装（压缩包保留），两种情况都不联网。否则从 GitHub release 下载 Windows
    预编译包（``cuda`` 变体额外下载 cudart）；目标目录内已有完整 zip 时直接复用。
    """
    target = llmenv.engines_root(home) / tag
    cached = _engine_exe_in(target) if target.exists() else None
    if cached is not None:
        return EngineInfo(tag, cached, str(target))

    if path is not None:
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
        if not (archive.is_file() and zipfile.is_zipfile(archive)):
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
    (target / ENGINE_MARKER).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        raise ServeError(f"引擎 {tag!r} 未安装：fcmd llmsrv engine install --tag {tag}")
    root.mkdir(parents=True, exist_ok=True)
    (root / CURRENT_MARKER).write_text(
        json.dumps({"tag": tag, "updated_at": now_iso()}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
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
