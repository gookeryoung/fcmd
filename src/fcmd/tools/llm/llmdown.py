"""llmdown - 大模型仓库下载工具。

面向 Qwen 等大模型权重仓库，支持两个国内可用源：

* ``modelscope`` —— 魔搭（www.modelscope.cn），国内直连，默认优先；
* ``hf-mirror`` —— HuggingFace 国内镜像（hf-mirror.com），作为兜底源。

``auto`` 模式会同时拉取两源清单，对同名大文件做两阶段实测下载吞吐
（复用 :func:`fcmd.infras.net.probe_mirrors`），按吞吐与延迟选优。
下载过程用 rich 展示单文件 / 总进度、实时速度与剩余时间，支持 HTTP Range
断点续传、已存在文件跳过与 sha256 校验。

示例
----
    fcmd llmdown files Qwen/Qwen3-Coder-30B-A3B-Instruct
    fcmd llmdown mirror Qwen/Qwen3-Coder-30B-A3B-Instruct
    fcmd llmdown download Qwen/Qwen3-Coder-30B-A3B-Instruct --dest D:/models
    fcmd llmdown download Qwen/Qwen3-Coder-30B-A3B-Instruct --include "*.safetensors"
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import fcmd
from fcmd.console import Table, get_console
from fcmd.tools.llm.llmbase import human_size

if TYPE_CHECKING:
    from fcmd.infras.net import UrlProbe

__all__ = [
    "ModelFile",
    "SourceSelection",
    "download_files",
    "fetch_files",
    "filter_files",
    "select_source",
]

SourceName = Literal["auto", "modelscope", "hf-mirror"]

_UA = "fcmd-llmdown/0.3.10"
_SOURCES: tuple[str, str] = ("modelscope", "hf-mirror")
_DEFAULT_REVISION = {"modelscope": "master", "hf-mirror": "main"}
_API_URL = {
    "modelscope": "https://www.modelscope.cn/api/v1/models/{repo}/repo/files?Revision={revision}&Recursive=true",
    "hf-mirror": "https://hf-mirror.com/api/models/{repo}/tree/{revision}?recursive=true",
}
_FILE_URL = {
    "modelscope": "https://www.modelscope.cn/models/{repo}/resolve/{revision}/{path}",
    "hf-mirror": "https://hf-mirror.com/{repo}/resolve/{revision}/{path}",
}
_MANIFEST_TIMEOUT = 30.0
_PROBE_TIMEOUT = 5.0
_STREAM_TIMEOUT = 60.0
_CHUNK_SIZE = 1024 * 1024
_SOURCE_BY_HOST = {"www.modelscope.cn": "modelscope", "hf-mirror.com": "hf-mirror"}


class ModelToolError(RuntimeError):
    """模型仓库清单 / 下载失败。"""


@dataclass(frozen=True, slots=True)
class ModelFile:
    """仓库内的单个文件。

    Attributes
    ----------
    path:
        仓库内相对路径（如 ``model-00001-of-00016.safetensors``）。
    size:
        文件字节数；清单未提供时为 ``0``。
    sha256:
        文件 sha256；镜像只提供 sha1 时为空串（不参与校验）。
    """

    path: str
    size: int
    sha256: str = ""


@dataclass(frozen=True, slots=True)
class SourceSelection:
    """源选择结果。

    Attributes
    ----------
    source:
        主源名称（``modelscope`` / ``hf-mirror``）。
    files:
        主源的文件清单。
    fallback:
        单文件下载失败时可回退的源；无可用兜底源时为 ``None``。
    probes:
        ``auto`` 模式下的测速排名（按实测吞吐与延迟降序）；显式指定源时为空。
    """

    source: str
    files: tuple[ModelFile, ...]
    fallback: str | None = None
    probes: tuple[UrlProbe, ...] = ()


# ---------------------------------------------------------------------- #
# 通用辅助
# ---------------------------------------------------------------------- #
def _normalize_repo(repo: str) -> str:
    """校验并规范化 ``<组织>/<模型名>`` 形式的仓库标识。"""
    cleaned = repo.strip().strip("/")
    parts = cleaned.split("/")
    if len(parts) != 2 or not all(parts):
        raise ModelToolError(f"仓库名应为 <组织>/<模型>，收到: {repo!r}")
    return cleaned


def _source_revision(source: str, revision: str | None) -> str:
    """返回该源使用的版本号（未显式指定时用各源默认分支）。"""
    return revision or _DEFAULT_REVISION[source]


def _api_url(repo: str, source: str, revision: str | None) -> str:
    """构造清单 API 地址。"""
    return _API_URL[source].format(
        repo=quote(repo, safe="/"),
        revision=quote(_source_revision(source, revision), safe=""),
    )


def file_url(repo: str, source: str, revision: str | None, path: str) -> str:
    """构造仓库内文件的下载地址（路径按段编码）。"""
    return _FILE_URL[source].format(
        repo=quote(repo, safe="/"),
        revision=quote(_source_revision(source, revision), safe=""),
        path=quote(path, safe="/"),
    )


def _http_json(url: str, timeout: float) -> Any:
    """请求 JSON 清单；网络 / 解析错误统一转换为 :class:`ModelToolError`。"""
    # 惰性导入 urllib 请求链，避免工具发现期给冷启动增加导入成本
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raise ModelToolError(f"清单请求失败 HTTP {exc.code}: {url}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ModelToolError(f"清单请求失败: {url}（{exc}）") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelToolError(f"清单响应不是合法 JSON: {url}") from exc


def _parse_modelscope(data: Any) -> list[ModelFile]:
    """解析 ModelScope ``repo/files`` 响应。"""
    payload = data.get("Data") if isinstance(data, dict) else None
    raw_files = payload.get("Files") if isinstance(payload, dict) else None
    if not isinstance(raw_files, list):
        message = data.get("Message") if isinstance(data, dict) else ""
        raise ModelToolError(f"ModelScope 清单格式异常{f': {message}' if message else ''}")
    files: list[ModelFile] = []
    for item in raw_files:
        if not isinstance(item, dict) or item.get("Type") == "tree":
            continue
        path = item.get("Path") or item.get("Name")
        if not path:
            continue
        files.append(ModelFile(str(path), int(item.get("Size") or 0), str(item.get("Sha256") or "")))
    return files


def _parse_hf(data: Any) -> list[ModelFile]:
    """解析 hf-mirror tree 响应（sha256 取 LFS 元数据，普通文件仅 sha1 不校验）。"""
    if not isinstance(data, list):
        raise ModelToolError("hf-mirror 清单格式异常")
    files: list[ModelFile] = []
    for item in data:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        path = item.get("path")
        if not path:
            continue
        lfs = item.get("lfs")
        sha256 = str(lfs.get("oid") or "") if isinstance(lfs, dict) else ""
        files.append(ModelFile(str(path), int(item.get("size") or 0), sha256))
    return files


def fetch_files(
    repo: str,
    source: str,
    revision: str | None = None,
    timeout: float = _MANIFEST_TIMEOUT,
) -> list[ModelFile]:
    """拉取指定源的仓库文件清单（按路径排序）。

    Parameters
    ----------
    repo:
        ``<组织>/<模型名>``。
    source:
        源名称（``modelscope`` / ``hf-mirror``）。
    revision:
        分支或版本；``None`` 时 ModelScope 用 ``master``、hf-mirror 用 ``main``。
    timeout:
        清单请求超时秒数。

    Raises
    ------
    ModelToolError
        网络失败、仓库不存在或响应格式异常。
    """
    data = _http_json(_api_url(repo, source, revision), timeout)
    files = _parse_modelscope(data) if source == "modelscope" else _parse_hf(data)
    if not files:
        raise ModelToolError(f"{source} 仓库不存在或没有文件: {repo}")
    return sorted(files, key=lambda f: f.path)


def filter_files(
    files: list[ModelFile],
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[ModelFile]:
    """按 glob 过滤文件（``*`` 可跨目录匹配，如 ``*.safetensors``）。"""
    selected = files
    if include:
        selected = [f for f in selected if any(fnmatch.fnmatch(f.path, pat) for pat in include)]
    if exclude:
        selected = [f for f in selected if not any(fnmatch.fnmatch(f.path, pat) for pat in exclude)]
    return selected


# ---------------------------------------------------------------------- #
# 源选择与测速
# ---------------------------------------------------------------------- #
def _probe_sources(
    repo: str,
    manifests: dict[str, list[ModelFile]],
    revision: str | None,
    timeout: float,
) -> list[tuple[str, UrlProbe]]:
    """对两源共同的最大文件做实测吞吐，返回按评分降序的 (源, 结果)。"""
    from fcmd.infras.net import probe_mirrors  # 惰性导入，避免冷启动连带网络模块

    sizes = {src: {f.path: f.size for f in files} for src, files in manifests.items()}
    common = set.intersection(*(set(paths) for paths in sizes.values()))
    if not common:
        return []
    path = max(common, key=lambda p: max(sizes[src][p] for src in sizes))
    urls = {src: file_url(repo, src, revision, path) for src in manifests}
    probes = probe_mirrors(list(urls.values()), timeout=timeout, user_agent=_UA)
    source_by_url = {url: src for src, url in urls.items()}
    return [(source_by_url[p.url], p) for p in probes]


def select_source(
    repo: str,
    source: SourceName = "auto",
    revision: str | None = None,
    probe_timeout: float = _PROBE_TIMEOUT,
) -> SourceSelection:
    """选择下载源并返回文件清单。

    ``auto`` 时两源清单都拉取，用共同最大文件实测下载吞吐选优；只有一源
    可用时直接使用该源，并保留另一可用源作为单文件失败时的兜底。
    显式指定源时不探测、不使用兜底。

    Raises
    ------
    ModelToolError
        指定源不可用，或 ``auto`` 下所有源均不可用。
    """
    if source != "auto":
        return SourceSelection(source, tuple(fetch_files(repo, source, revision)))

    manifests: dict[str, list[ModelFile]] = {}
    errors: list[str] = []
    for candidate in _SOURCES:
        try:
            manifests[candidate] = fetch_files(repo, candidate, revision)
        except ModelToolError as exc:
            errors.append(str(exc))
    if not manifests:
        raise ModelToolError("所有源均不可用：" + "；".join(errors))

    if len(manifests) == 1:
        only = next(iter(manifests))
        return SourceSelection(only, tuple(manifests[only]))

    probes = _probe_sources(repo, manifests, revision, probe_timeout)
    chosen = next((src for src, probe in probes if probe.ok), _SOURCES[0])
    fallback = next((src for src in _SOURCES if src != chosen and src in manifests), None)
    return SourceSelection(chosen, tuple(manifests[chosen]), fallback, tuple(p for _, p in probes))


def _print_selection(selection: SourceSelection) -> None:
    """打印测速排名与最终选源。"""
    console = get_console()
    if selection.probes:
        console.print("[bold]源自动选优[/bold]（按实测下载吞吐与延迟排序）")
        for probe in selection.probes:
            label = _SOURCE_BY_HOST.get(probe.url.split("/")[2], probe.url.split("/")[2])
            if probe.ok:
                console.print(f"  {label:<10} 延迟 {probe.latency_ms:.0f} ms  吞吐 {probe.throughput_kbps:.0f} KB/s")
            else:
                console.print(f"  {label:<10} 不可用（{probe.reason}）")
    suffix = f"（单文件失败回退 {selection.fallback}）" if selection.fallback else ""
    console.print(f"选用源: [cyan]{selection.source}[/cyan]{suffix}")


# ---------------------------------------------------------------------- #
# 下载
# ---------------------------------------------------------------------- #
def _part_path(final: Path) -> Path:
    """断点续传临时文件路径。"""
    return final.with_name(final.name + ".part")


def _sha256_file(path: Path, progress: Any = None, task: Any = None) -> str:
    """计算文件 sha256；传入 progress/task 时同步刷新校验进度。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
            if progress is not None and task is not None:
                progress.update(task, advance=len(chunk))
    return digest.hexdigest()


def _stream_to_part(  # noqa: PLR0913, PLR0917
    url: str,
    part: Path,
    start: int,
    progress: Any,
    file_task: Any,
    overall_task: Any,
) -> None:
    """把 URL 流式写入 ``.part``，支持 Range 续传与进度刷新。"""
    from urllib.request import Request, urlopen

    headers = {"User-Agent": _UA}
    if start:
        headers["Range"] = f"bytes={start}-"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=_STREAM_TIMEOUT) as resp:
        status = getattr(resp, "status", 200)
        mode = "ab" if start else "wb"
        if start and status != 206:
            # 服务端忽略 Range：从头写，并把已计入的续传偏移回退掉
            mode = "wb"
            progress.update(file_task, completed=0)
            progress.update(overall_task, advance=-start)
            start = 0
        with part.open(mode) as fh:
            while chunk := resp.read(_CHUNK_SIZE):
                fh.write(chunk)
                progress.update(file_task, advance=len(chunk))
                progress.update(overall_task, advance=len(chunk))


def _download_one(  # noqa: PLR0913, PLR0917
    repo: str,
    file: ModelFile,
    final: Path,
    part: Path,
    selection: SourceSelection,
    revision: str | None,
    verify: bool,
    progress: Any,
    file_task: Any,
    overall_task: Any,
) -> None:
    """下载单个文件：主源失败回退兜底源，完成后校验并原子替换。"""
    final.parent.mkdir(parents=True, exist_ok=True)
    urls = [file_url(repo, selection.source, revision, file.path)]
    if selection.fallback:
        urls.append(file_url(repo, selection.fallback, revision, file.path))

    last_error: Exception | None = None
    for url in urls:
        start = part.stat().st_size if part.is_file() and file.size else 0
        if file.size and start > file.size:
            part.unlink()
            start = 0
            progress.update(file_task, completed=0)
        if file.size and start == file.size:
            break
        try:
            _stream_to_part(url, part, start, progress, file_task, overall_task)
            break
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
    else:
        raise ModelToolError(f"下载失败: {last_error}")

    if file.size and part.is_file() and part.stat().st_size != file.size:
        raise ModelToolError(f"大小不符（期望 {file.size}，实际 {part.stat().st_size}）")

    if verify and file.sha256:
        hash_task = progress.add_task(f"校验 {file.path}", total=file.size or None)
        try:
            actual = _sha256_file(part, progress, hash_task)
        finally:
            progress.remove_task(hash_task)
        if actual.lower() != file.sha256.lower():
            part.unlink(missing_ok=True)
            raise ModelToolError("sha256 校验失败，已删除损坏的 .part 文件")

    part.replace(final)


def download_files(  # noqa: PLR0913, PLR0917
    repo: str,
    files: list[ModelFile],
    dest: Path,
    selection: SourceSelection,
    revision: str | None = None,
    workers: int = 4,
    verify: bool = True,
) -> None:
    """并发下载文件列表，展示单文件与总进度、速度与剩余时间。

    Raises
    ------
    ModelToolError
        任一文件在全部源上均下载失败，或校验不通过。
    """
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    console = get_console()
    total = sum(f.size for f in files)
    pending: list[tuple[ModelFile, Path, Path, int]] = []
    done_bytes = 0
    for file in files:
        final = dest / file.path
        if final.is_file() and (not file.size or final.stat().st_size == file.size):
            done_bytes += file.size
            continue
        part = _part_path(final)
        start = part.stat().st_size if part.is_file() and file.size else 0
        if file.size and start > file.size:
            part.unlink()
            start = 0
        done_bytes += start
        pending.append((file, final, part, start))

    if not pending:
        console.print("[green]全部文件已存在，无需下载[/green]")
        return

    failures: list[str] = []
    columns = (
        TextColumn("[cyan]{task.description}", justify="left"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    )
    with Progress(*columns, console=console.rich) as progress:
        overall_task = progress.add_task("总进度", total=total, completed=done_bytes)
        file_tasks = {
            file.path: progress.add_task(file.path, total=file.size or None, completed=start)
            for file, _, _, start in pending
        }
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(
                    _download_one,
                    repo,
                    file,
                    final,
                    part,
                    selection,
                    revision,
                    verify,
                    progress,
                    file_tasks[file.path],
                    overall_task,
                ): file.path
                for file, final, part, _ in pending
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except (ModelToolError, OSError) as exc:
                    failures.append(f"{futures[future]}: {exc}")

    if failures:
        raise ModelToolError(f"{len(failures)} 个文件下载失败：" + "；".join(failures))


# ---------------------------------------------------------------------- #
# CLI 子命令
# ---------------------------------------------------------------------- #
@fcmd.tool(
    "llmdown",
    subcommand="files",
    help="列出模型仓库文件清单（auto 时两源测速选优）",
    description="大模型仓库下载：清单 / 多源测速 / 断点续传下载",
)
def list_files_cmd(
    repo: str,
    source: SourceName = "auto",
    revision: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> None:
    """列出文件清单，并按 include/exclude glob 过滤。

    Parameters
    ----------
    repo:
        仓库标识，如 ``Qwen/Qwen3-Coder-30B-A3B-Instruct``。
    source:
        ``auto`` 两源测速选优；也可指定 ``modelscope`` / ``hf-mirror``。
    revision:
        分支或版本，默认按源取 ``master`` / ``main``。
    include:
        仅保留匹配的 glob（空格分隔多个），如 ``*.safetensors`` ``*.json``。
    exclude:
        排除匹配的 glob。
    """
    repo = _normalize_repo(repo)
    selection = select_source(repo, source, revision)
    _print_selection(selection)
    selected = filter_files(list(selection.files), include, exclude)
    if not selected:
        raise ModelToolError("过滤后没有文件，请检查 --include / --exclude")

    table = Table(title=f"{repo} 文件清单", box="ascii")
    table.add_column("文件", style="cyan", no_wrap=True)
    table.add_column("大小", justify="right", no_wrap=True)
    table.add_column("SHA256", no_wrap=True)
    for file in selected:
        table.add_row(file.path, human_size(file.size), file.sha256[:12] or "-")
    get_console().print(table)
    total = sum(f.size for f in selected)
    get_console().print(f"共 {len(selected)} 个文件，合计 {human_size(total)}")


@fcmd.tool(
    "llmdown",
    subcommand="mirror",
    help="对两源同名文件实测下载吞吐并排名",
    description="大模型仓库下载：清单 / 多源测速 / 断点续传下载",
)
def mirror_cmd(repo: str, revision: str | None = None) -> None:
    """实测 ModelScope 与 hf-mirror 的下载吞吐并输出排名。

    Parameters
    ----------
    repo:
        仓库标识，如 ``Qwen/Qwen3-Coder-30B-A3B-Instruct``。
    revision:
        分支或版本，默认按源取 ``master`` / ``main``。
    """
    repo = _normalize_repo(repo)
    selection = select_source(repo, "auto", revision)
    if not selection.probes:
        get_console().print("[yellow]两源没有共同文件，无法同文件测速[/yellow]")
        return

    table = Table(title=f"{repo} 源测速", box="ascii")
    table.add_column("源", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("延迟", justify="right", no_wrap=True)
    table.add_column("吞吐", justify="right", no_wrap=True)
    for probe in selection.probes:
        host = probe.url.split("/")[2]
        source = _SOURCE_BY_HOST.get(host, host)
        status = "[green]可用[/green]" if probe.ok else f"[red]{probe.reason}[/red]"
        latency = f"{probe.latency_ms:.0f} ms" if probe.ok else "-"
        throughput = f"{probe.throughput_kbps:.0f} KB/s" if probe.ok else "-"
        table.add_row(source, status, latency, throughput)
    get_console().print(table)
    _print_selection(selection)


@fcmd.tool(
    "llmdown",
    subcommand="download",
    help="下载模型仓库（多源选优 + 进度速度 + 断点续传）",
    description="大模型仓库下载：清单 / 多源测速 / 断点续传下载",
)
def download_cmd(  # noqa: PLR0913, PLR0917
    repo: str,
    dest: Path | None = None,
    source: SourceName = "auto",
    revision: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    workers: int = 4,
    verify: bool = True,
) -> None:
    """下载仓库文件到目标目录。

    Parameters
    ----------
    repo:
        仓库标识，如 ``Qwen/Qwen3-Coder-30B-A3B-Instruct``。
    dest:
        目标目录，默认 ``./models/<组织>/<模型>``。
    source:
        ``auto`` 两源测速选优；也可指定 ``modelscope`` / ``hf-mirror``。
    revision:
        分支或版本，默认按源取 ``master`` / ``main``。
    include:
        仅下载匹配的 glob（空格分隔多个），如 ``*.safetensors``。
    exclude:
        排除匹配的 glob。
    workers:
        并发下载文件数（默认 4）。
    verify:
        有 sha256 时校验（``--no-verify`` 关闭）。
    """
    repo = _normalize_repo(repo)
    selection = select_source(repo, source, revision)
    _print_selection(selection)
    selected = filter_files(list(selection.files), include, exclude)
    if not selected:
        raise ModelToolError("过滤后没有文件，请检查 --include / --exclude")

    target = dest if dest is not None else Path("models") / repo
    total = sum(f.size for f in selected)
    get_console().print(f"目标目录: {target}\n文件数: {len(selected)}  总大小: {human_size(total)}")
    download_files(repo, selected, target, selection, revision, workers, verify)


@fcmd.main("llmdown")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
