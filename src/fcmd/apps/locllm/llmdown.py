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
import re
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import fcmd
from fcmd.console import Table, get_console
from fcmd.apps.locllm.llmbase import human_size

if TYPE_CHECKING:
    from fcmd.infras.net import UrlProbe

__all__ = [
    "MODEL_SERIES_RANKING",
    "DownloadCancelled",
    "LocalModel",
    "ModelFile",
    "ModelInfo",
    "ModelSearch",
    "ModelSummary",
    "ModelTask",
    "ProgressEvent",
    "SourceSelection",
    "delete_local_model",
    "download_files",
    "fetch_files",
    "fetch_model_info",
    "fetch_tasks",
    "filter_by_series",
    "filter_files",
    "format_timestamp",
    "model_series",
    "scan_local_models",
    "search_models",
    "select_source",
    "series_matches",
    "series_options",
    "series_rank",
    "sort_by_popularity",
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
_MODEL_INFO_URL = "https://www.modelscope.cn/api/v1/models/{repo}"
_MODEL_SEARCH_URL = "https://www.modelscope.cn/api/v1/dolphin/models"
_MODEL_TASKS_URL = "https://www.modelscope.cn/api/v1/tasks"
_MODEL_SORTS = {
    "default": "Default",
    "modified": "GmtModified",
    "created": "GmtCreated",
    "downloads": "DownloadsCount",
    "stars": "StarsCount",
}
_MANIFEST_TIMEOUT = 30.0
_PROBE_TIMEOUT = 5.0
_STREAM_TIMEOUT = 60.0
_CHUNK_SIZE = 1024 * 1024
_SOURCE_BY_HOST = {"www.modelscope.cn": "modelscope", "hf-mirror.com": "hf-mirror"}
_OTHER_SERIES = "其他"
# 模型系列知名度排名（单一数据源：顺序即界面默认展示顺序，越靠前越知名）。
# 依据：按 2024–2026 年 Hugging Face 下载 / 点赞、Open LLM Leaderboard、LMArena
# 等国际开源社区与主流榜单的普遍认知做粗排，不追求精确名次；新增或调整系列
# 只改这张表，筛选选项、默认排序与「其他」置末都会自动跟随。
# 每项为 ``(系列显示名, 识别关键词)``，关键词以小写、无分隔符形式给出。
MODEL_SERIES_RANKING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Llama", ("llama", "codellama", "tinyllama")),
    ("GPT", ("gpt", "chatgpt")),
    ("Stable Diffusion", ("stablediffusion", "sdxl")),
    ("Qwen", ("qwen",)),
    ("DeepSeek", ("deepseek",)),
    ("Mistral", ("mistral",)),
    ("BERT", ("bert", "roberta")),
    ("Whisper", ("whisper",)),
    ("Gemma", ("gemma",)),
    ("Phi", ("phi",)),
    ("T5", ("t5", "mt5")),
    ("Claude", ("claude",)),
    ("Mixtral", ("mixtral",)),
    ("Falcon", ("falcon",)),
    ("Yi", ("yi",)),
    ("GLM", ("chatglm", "glm")),
    ("Kimi", ("kimi", "moonshot")),
    ("Baichuan", ("baichuan",)),
    ("InternLM", ("internlm",)),
    ("MiniMax", ("minimax",)),
    ("Command R", ("command",)),
    ("Grok", ("grok",)),
    ("Nemotron", ("nemotron",)),
    ("Vicuna", ("vicuna",)),
    ("Alpaca", ("alpaca",)),
    ("WizardLM", ("wizardlm",)),
    ("OLMo", ("olmo",)),
    ("DBRX", ("dbrx",)),
    ("BLOOM", ("bloom",)),
    ("SmolLM", ("smollm",)),
    ("Granite", ("granite",)),
)


class ModelToolError(RuntimeError):
    """模型仓库清单 / 下载失败。"""


class DownloadCancelled(ModelToolError):
    """用户取消下载；已下载部分保留为 ``.part`` 供下次续传。"""


class _ReporterProgress:
    """把 rich Progress 的最小接口转成 :class:`ProgressEvent` 回调。

    Tk 等前端无需依赖 rich，即可拿到整体 / 单文件进度。
    """

    def __init__(self, reporter: Callable[[ProgressEvent], None]) -> None:
        self._reporter = reporter
        self._tasks: dict[int, tuple[str, int, int]] = {}
        self._lock = threading.Lock()
        self._next_id = 0

    def __enter__(self) -> _ReporterProgress:
        return self

    def __exit__(self, *_exc: object) -> None:
        with self._lock:
            tasks = list(self._tasks.items())
            self._tasks.clear()
        for _, (description, completed, total) in tasks:
            self._reporter(ProgressEvent(description, "done", completed, total))

    def _emit(
        self,
        description: str,
        state: Literal["start", "update", "done"],
        completed: int,
        total: int,
    ) -> None:
        self._reporter(ProgressEvent(description, state, completed, total))

    def add_task(self, description: str, total: int | None = None, completed: int = 0) -> int:
        with self._lock:
            task_id = self._next_id
            self._next_id += 1
            self._tasks[task_id] = (description, completed, total or 0)
        self._emit(description, "start", completed, total or 0)
        return task_id

    def update(self, task: int, advance: int = 0, completed: int | None = None, **_kwargs: Any) -> None:
        with self._lock:
            description, current, total = self._tasks.get(task, ("", 0, 0))
            value = completed if completed is not None else current + advance
            if total:
                value = max(0, min(value, total))
            self._tasks[task] = (description, value, total)
        self._emit(description, "update", value, total)

    def remove_task(self, task: int) -> None:
        with self._lock:
            description, completed, total = self._tasks.pop(task, ("", 0, 0))
        self._emit(description, "done", completed, total)


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


@dataclass(frozen=True, slots=True)
class ModelSummary:
    """ModelScope 模型列表中的一条摘要。"""

    repo: str
    name: str = ""
    owner: str = ""
    chinese_name: str = ""
    downloads: int = 0
    stars: int = 0
    tasks: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    license: str = ""
    updated: int = 0
    storage_size: int = 0


@dataclass(frozen=True, slots=True)
class ModelTask:
    """ModelScope 的一个任务类别（用于界面类别筛选）。"""

    name: str
    chinese_name: str = ""
    domain: str = ""

    @property
    def label(self) -> str:
        """界面显示名：优先中文名，缺失时回落英文名。"""
        return self.chinese_name or self.name


@dataclass(frozen=True, slots=True)
class ModelSearch:
    """ModelScope 模型搜索结果（分页）。"""

    items: tuple[ModelSummary, ...]
    total: int
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """ModelScope 模型详情（含 README）。"""

    summary: ModelSummary
    description: str = ""
    readme: str = ""
    organization: str = ""
    frameworks: tuple[str, ...] = ()
    architectures: tuple[str, ...] = ()
    model_types: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    revision: str = ""
    created: int = 0
    inference: str = ""


@dataclass(frozen=True, slots=True)
class LocalModel:
    """本地目标目录下已下载（或部分下载）的一个模型目录。"""

    repo: str
    path: Path
    size: int
    files: int
    partial: int = 0

    @property
    def complete(self) -> bool:
        """没有 ``.part`` 残留即视为下载完成。"""
        return self.partial == 0


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """下载进度事件；供 Tk 等自定义前端复用同一套下载实现。"""

    task: str
    state: Literal["start", "update", "done"]
    completed: int = 0
    total: int = 0


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


def _http_json(
    url: str,
    timeout: float,
    *,
    method: str = "GET",
    payload: Any = None,
) -> Any:
    """请求 JSON 接口；网络 / 解析错误统一转换为 :class:`ModelToolError`。

    ``payload`` 不为 ``None`` 时以 JSON 请求体发送（ModelScope 搜索接口用 ``PUT``）。
    """
    # 惰性导入 urllib 请求链，避免工具发现期给冷启动增加导入成本
    from urllib.request import Request, urlopen

    headers = {"User-Agent": _UA, "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
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


def _as_int(value: Any) -> int:
    """把接口返回值宽松转换为非负整数（缺失 / 非数字均为 0）。"""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _as_text(value: Any) -> str:
    """把接口返回值宽松转换为字符串。"""
    return value if isinstance(value, str) else ""


def format_timestamp(value: int) -> str:
    """把 Unix 秒转换为 ``YYYY-MM-DD``；无效值返回 ``-``。"""
    if value <= 0:
        return "-"
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "-"


def _as_tuple(value: Any) -> tuple[str, ...]:
    """把字符串数组转换为去空元组；非数组返回空元组。"""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _task_names(value: Any) -> tuple[str, ...]:
    """从 ``Tasks`` 对象数组提取中文（缺失时用英文）任务名。"""
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("ChineseName")) or _as_text(item.get("Name"))
        if name:
            names.append(name)
    return tuple(names)


def _summary_from_item(item: dict[str, Any]) -> ModelSummary:
    """把 ModelScope 列表 / 详情里的模型对象转换为 :class:`ModelSummary`。"""
    name = _as_text(item.get("Name"))
    path = _as_text(item.get("Path"))
    # owner 优先取仓库路径；缺失时回落到接口可能提供的作者 / 发布方字段
    owner = path or _as_text(item.get("Owner")) or _as_text(item.get("Author")) or _as_text(item.get("Publisher"))
    repo = f"{path}/{name}" if path and name else name
    return ModelSummary(
        repo=repo,
        name=name,
        owner=owner,
        chinese_name=_as_text(item.get("ChineseName")),
        downloads=_as_int(item.get("Downloads")),
        stars=_as_int(item.get("Stars")),
        tasks=_task_names(item.get("Tasks")),
        tags=_as_tuple(item.get("Tags")),
        license=_as_text(item.get("License")),
        updated=_as_int(item.get("LastUpdatedTime")),
        storage_size=_as_int(item.get("StorageSize")),
    )


def search_models(
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
    sort: str = "default",
    timeout: float = _MANIFEST_TIMEOUT,
) -> ModelSearch:
    """搜索 ModelScope 模型（按 ``Name`` 关键字匹配组织或模型名）。

    Parameters
    ----------
    keyword:
        关键字；空串表示浏览全部模型。
    page / page_size:
        页码（从 1 开始）与每页条数（1–100）。
    sort:
        ``default`` 默认排序、``modified`` 最近更新、``created`` 最新发布、
        ``downloads`` 下载最多、``stars`` 星标最多。
    timeout:
        请求超时秒数。

    Raises
    ------
    ModelToolError
        网络失败或响应格式异常。
    """
    if page < 1 or not 1 <= page_size <= 100:
        raise ModelToolError("分页参数非法：page 需 ≥ 1，page_size 需在 1–100 之间")
    payload = {
        "PageSize": page_size,
        "PageNumber": page,
        "SortBy": _MODEL_SORTS.get(sort.casefold(), _MODEL_SORTS["default"]),
        "Target": "",
        "SingleCriterion": (),
        "Name": keyword.strip(),
    }
    data = _http_json(_MODEL_SEARCH_URL, timeout, method="PUT", payload=payload)
    payload_data = data.get("Data") if isinstance(data, dict) else None
    model = payload_data.get("Model") if isinstance(payload_data, dict) else None
    raw_items = model.get("Models") if isinstance(model, dict) else None
    if not isinstance(model, dict) or not isinstance(raw_items, list):
        message = data.get("Message") if isinstance(data, dict) else ""
        raise ModelToolError(f"ModelScope 模型列表格式异常{f': {message}' if message else ''}")
    items = tuple(_summary_from_item(item) for item in raw_items if isinstance(item, dict) and item.get("Name"))
    return ModelSearch(items, _as_int(model.get("TotalCount")), page, page_size)


def fetch_tasks(timeout: float = _MANIFEST_TIMEOUT) -> tuple[ModelTask, ...]:
    """拉取 ModelScope 的任务类别清单（按领域顺序去重）。

    Raises
    ------
    ModelToolError
        网络失败或响应格式异常。
    """
    data = _http_json(_MODEL_TASKS_URL, timeout)
    payload = data.get("Data") if isinstance(data, dict) else None
    domains = payload.get("Domains") if isinstance(payload, dict) else None
    if not isinstance(domains, list):
        message = data.get("Message") if isinstance(data, dict) else ""
        raise ModelToolError(f"ModelScope 任务列表格式异常{f': {message}' if message else ''}")
    tasks: list[ModelTask] = []
    seen: set[str] = set()
    for domain in domains:
        if not isinstance(domain, dict):
            continue
        domain_label = _as_text(domain.get("DomainChineseName")) or _as_text(domain.get("DomainName"))
        for item in domain.get("Tasks") or ():
            if not isinstance(item, dict):
                continue
            name = _as_text(item.get("Name"))
            if not name or name in seen:
                continue
            seen.add(name)
            tasks.append(ModelTask(name, _as_text(item.get("ChineseName")), domain_label))
    return tuple(tasks)


def fetch_model_info(repo: str, timeout: float = _MANIFEST_TIMEOUT) -> ModelInfo:
    """拉取 ModelScope 模型详情（含 README、任务、框架与许可证）。

    Raises
    ------
    ModelToolError
        仓库名非法、网络失败或响应格式异常。
    """
    repo = _normalize_repo(repo)
    data = _http_json(_MODEL_INFO_URL.format(repo=quote(repo, safe="/")), timeout)
    detail = data.get("Data") if isinstance(data, dict) else None
    if not isinstance(detail, dict) or not detail.get("Name"):
        message = data.get("Message") if isinstance(data, dict) else ""
        raise ModelToolError(f"ModelScope 模型详情格式异常{f': {message}' if message else ''}")
    organization = detail.get("Organization")
    org_name = ""
    if isinstance(organization, dict):
        org_name = _as_text(organization.get("FullName")) or _as_text(organization.get("Name"))
    return ModelInfo(
        summary=_summary_from_item(detail),
        description=_as_text(detail.get("Description")),
        readme=_as_text(detail.get("ReadMeContent")),
        organization=org_name,
        frameworks=_as_tuple(detail.get("Frameworks")),
        architectures=_as_tuple(detail.get("Architectures")),
        model_types=_as_tuple(detail.get("ModelType")),
        languages=_as_tuple(detail.get("Language")),
        revision=_as_text(detail.get("Revision")),
        created=_as_int(detail.get("CreatedTime")),
        inference=_as_text(detail.get("SupportInference")),
    )


# ---------------------------------------------------------------------- #
# 模型系列识别与知名度排序
# ---------------------------------------------------------------------- #
def _series_segments(value: str) -> tuple[str, tuple[str, ...]]:
    """把一段标识拆成 (紧凑串, 词列表)：小写、只保留字母数字，按非字母数字切词。"""
    compact = "".join(char for char in value.casefold() if char.isalnum())
    tokens = tuple(token for token in re.split(r"[^0-9a-z]+", value.casefold()) if token)
    return compact, tokens


def model_series(summary: ModelSummary) -> str:
    """从 repo id / 模型名识别模型系列；识别不出归入「其他」。

    识别规则：owner 段与模型名段分别转小写、只保留字母数字后，按
    :data:`MODEL_SERIES_RANKING` 的顺序逐个系列匹配关键词——某段与关键词
    完全相同、以关键词开头（如 ``qwen3`` 命中 ``qwen``），或首个系列在
    紧凑串上整体前缀命中（如 ``stable-diffusion-xl`` 命中
    ``stablediffusion``）即算命中；先命中排行榜靠前者，全部不中为「其他」。
    """
    raw = summary.repo or summary.name
    if not raw.strip():
        return _OTHER_SERIES
    segments = [_series_segments(part) for part in raw.split("/") if part.strip()]
    for label, patterns in MODEL_SERIES_RANKING:
        for pattern in patterns:
            for compact, tokens in segments:
                if (len(pattern) >= 6 and compact.startswith(pattern)) or any(
                    token == pattern or (len(pattern) >= 3 and token.startswith(pattern)) for token in tokens
                ):
                    return label
    return _OTHER_SERIES


def series_rank(label: str) -> int:
    """系列在知名度排名表中的位次；「其他」与未知系列排在最后。"""
    for index, (name, _patterns) in enumerate(MODEL_SERIES_RANKING):
        if name == label:
            return index
    return len(MODEL_SERIES_RANKING)


def series_options(items: tuple[ModelSummary, ...]) -> tuple[str, ...]:
    """归集当前结果涉及的系列：按知名度排名排序，「其他」始终置末。"""
    labels = {model_series(item) for item in items}
    return tuple(sorted(labels, key=series_rank))


def series_matches(label: str, query: str) -> bool:
    """系列显示名是否匹配查询词（不区分大小写的子串，用于可搜索下拉）。"""
    text = query.strip().casefold()
    return not text or text in label.casefold()


def filter_by_series(items: tuple[ModelSummary, ...], series: str = "") -> tuple[ModelSummary, ...]:
    """按模型系列筛选；空串表示不过滤，按显示名精确匹配（大小写不敏感）。"""
    text = series.strip().casefold()
    if not text:
        return items
    return tuple(item for item in items if model_series(item).casefold() == text)


def sort_by_popularity(items: tuple[ModelSummary, ...]) -> tuple[ModelSummary, ...]:
    """按系列知名度排序（同系列保持服务端返回的原顺序），「其他」置末。"""
    return tuple(sorted(items, key=lambda item: series_rank(model_series(item))))


# ---------------------------------------------------------------------- #
# 本地模型管理
# ---------------------------------------------------------------------- #
def scan_local_models(root: Path) -> list[LocalModel]:
    """扫描 ``root/<组织>/<模型>`` 下已下载的模型目录。

    只统计真实文件，不跟随符号链接；含 ``.part`` 的目录标记为未完成。
    目录不存在或无模型时返回空列表。
    """
    if not root.is_dir():
        return []
    found: list[LocalModel] = []
    try:
        owners = sorted(entry for entry in root.iterdir() if entry.is_dir() and not entry.is_symlink())
    except OSError:
        return []
    for owner in owners:
        try:
            models = sorted(entry for entry in owner.iterdir() if entry.is_dir() and not entry.is_symlink())
        except OSError:
            continue
        for model_dir in models:
            size = 0
            files = 0
            partial = 0
            try:
                candidates = list(model_dir.rglob("*"))
            except OSError:
                continue
            for item in candidates:
                if item.is_symlink() or not item.is_file():
                    continue
                try:
                    size += item.stat().st_size
                except OSError:
                    continue
                files += 1
                if item.name.endswith(".part"):
                    partial += 1
            if files:
                found.append(LocalModel(f"{owner.name}/{model_dir.name}", model_dir, size, files, partial))
    return found


def delete_local_model(root: Path, repo: str) -> Path:
    """删除 ``root/<组织>/<模型>`` 目录（调用方必须已获得用户确认）。

    Raises
    ------
    ModelToolError
        仓库名非法、目标越出根目录或目录不存在。
    """
    owner, name = _normalize_repo(repo).split("/")
    base = root.resolve()
    target = (root / owner / name).resolve()
    if not target.is_relative_to(base):
        raise ModelToolError(f"拒绝删除目标目录之外的路径: {target}")
    if not target.is_dir():
        raise ModelToolError(f"目录不存在: {target}")
    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise ModelToolError(f"删除失败: {target}（{exc}）") from exc
    return target


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


def _check_stop(should_stop: Callable[[], bool] | None) -> None:
    """外部要求取消时抛出 :class:`DownloadCancelled`。"""
    if should_stop is not None and should_stop():
        raise DownloadCancelled("下载已取消（已保留 .part 以便续传）")


def _sha256_file(
    path: Path,
    progress: Any = None,
    task: Any = None,
    should_stop: Callable[[], bool] | None = None,
) -> str:
    """计算文件 sha256；传入 progress/task 时同步刷新校验进度。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            _check_stop(should_stop)
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
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """把 URL 流式写入 ``.part``，支持 Range 续传与进度刷新。"""
    from urllib.request import Request, urlopen

    _check_stop(should_stop)
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
                _check_stop(should_stop)
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
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """下载单个文件：主源失败回退兜底源，完成后校验并原子替换。"""
    _check_stop(should_stop)
    final.parent.mkdir(parents=True, exist_ok=True)
    urls = [file_url(repo, selection.source, revision, file.path)]
    if selection.fallback:
        urls.append(file_url(repo, selection.fallback, revision, file.path))

    last_error: Exception | None = None
    for url in urls:
        _check_stop(should_stop)
        start = part.stat().st_size if part.is_file() and file.size else 0
        if file.size and start > file.size:
            part.unlink()
            start = 0
            progress.update(file_task, completed=0)
        if file.size and start == file.size:
            break
        try:
            _stream_to_part(url, part, start, progress, file_task, overall_task, should_stop)
            break
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
    else:
        raise ModelToolError(f"下载失败: {last_error}")

    _check_stop(should_stop)
    if file.size and part.is_file() and part.stat().st_size != file.size:
        raise ModelToolError(f"大小不符（期望 {file.size}，实际 {part.stat().st_size}）")

    if verify and file.sha256:
        hash_task = progress.add_task(f"校验 {file.path}", total=file.size or None)
        try:
            actual = _sha256_file(part, progress, hash_task, should_stop)
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
    reporter: Callable[[ProgressEvent], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """并发下载文件列表。

    Parameters
    ----------
    reporter:
        自定义进度回调；传入时不再渲染 rich 进度条（供 Tk 等前端使用）。
    should_stop:
        返回 ``True`` 时尽快停止下载，已写入的 ``.part`` 保留以便续传。

    Raises
    ------
    DownloadCancelled
        被 ``should_stop`` 取消。
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
        if reporter is None:
            console.print("[green]全部文件已存在，无需下载[/green]")
        return

    failures: list[str] = []
    cancelled = False
    columns = (
        TextColumn("[cyan]{task.description}", justify="left"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    )
    progress: Any = _ReporterProgress(reporter) if reporter is not None else Progress(*columns, console=console.rich)
    with progress:
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
                    should_stop,
                ): file.path
                for file, final, part, _ in pending
            }
            for future in as_completed(futures):
                path = futures[future]
                try:
                    future.result()
                except DownloadCancelled:
                    cancelled = True
                except (ModelToolError, OSError) as exc:
                    failures.append(f"{path}: {exc}")
                finally:
                    progress.remove_task(file_tasks[path])

    if cancelled:
        raise DownloadCancelled("下载已取消（已保留 .part 以便续传）")
    if failures:
        raise ModelToolError(f"{len(failures)} 个文件下载失败：" + "；".join(failures))


# ---------------------------------------------------------------------- #
# CLI 子命令
# ---------------------------------------------------------------------- #
@fcmd.tool(
    "llmdown",
    subcommand="search",
    help="在 ModelScope 搜索模型",
    description="大模型仓库下载：搜索 / 详情 / 清单 / 多源测速 / 断点续传下载",
)
def search_cmd(
    keyword: str = "",
    page: int = 1,
    limit: int = 20,
    sort: Literal["default", "modified", "created", "downloads", "stars"] = "default",
    series: str = "",
) -> None:
    """在 ModelScope 搜索模型并列出摘要。

    Parameters
    ----------
    keyword:
        搜索关键字（组织或模型名）；留空浏览热门 / 默认排序。
    page:
        页码，从 1 开始。
    limit:
        每页条数（1–100）。
    sort:
        ``default`` 默认排序、``modified`` 最近更新、``created`` 最新发布、
        ``downloads`` 下载最多、``stars`` 星标最多。
    series:
        按模型系列筛选（只作用于当前页），如 ``Llama`` / ``Qwen`` /
        ``DeepSeek``；识别不出系列的条目归入「其他」。
    """
    result = search_models(keyword, page, limit, sort)
    items = filter_by_series(result.items, series)
    if not items:
        get_console().print("[yellow]没有匹配的模型[/yellow]")
        return
    table = Table(title=f"ModelScope 模型（第 {result.page} 页 / 共 {result.total} 条）", box="ascii")
    table.add_column("仓库", style="cyan", no_wrap=True)
    table.add_column("任务", no_wrap=True)
    table.add_column("下载", justify="right", no_wrap=True)
    table.add_column("星标", justify="right", no_wrap=True)
    table.add_column("更新", justify="right", no_wrap=True)
    table.add_column("大小", justify="right", no_wrap=True)
    for item in items:
        table.add_row(
            item.repo,
            "、".join(item.tasks) or "-",
            f"{item.downloads:,}",
            f"{item.stars:,}",
            format_timestamp(item.updated),
            human_size(item.storage_size) if item.storage_size else "-",
        )
    get_console().print(table)


@fcmd.tool(
    "llmdown",
    subcommand="info",
    help="查看 ModelScope 模型详情",
    description="大模型仓库下载：搜索 / 详情 / 清单 / 多源测速 / 断点续传下载",
)
def info_cmd(repo: str, readme: bool = False, limit: int = 2000) -> None:
    """查看模型详情（任务、框架、许可证、README）。

    Parameters
    ----------
    repo:
        仓库标识，如 ``Qwen/Qwen3-Coder-30B-A3B-Instruct``。
    readme:
        额外输出 README 正文（``--readme``）。
    limit:
        README 最大输出字符数（默认 2000）。
    """
    info = fetch_model_info(repo)
    summary = info.summary
    table = Table(title=summary.repo, box="ascii", show_header=False)
    table.add_column("字段", style="cyan", no_wrap=True)
    table.add_column("值")
    rows = (
        ("中文名", summary.chinese_name or "-"),
        ("组织", info.organization or summary.owner or "-"),
        ("任务", "、".join(summary.tasks) or "-"),
        ("框架", "、".join(info.frameworks) or "-"),
        ("架构", "、".join(info.architectures) or "-"),
        ("模型类型", "、".join(info.model_types) or "-"),
        ("语言", "、".join(info.languages) or "-"),
        ("许可证", summary.license or "-"),
        ("下载", f"{summary.downloads:,}"),
        ("星标", f"{summary.stars:,}"),
        ("大小", human_size(summary.storage_size) if summary.storage_size else "-"),
        ("版本", info.revision or "-"),
        ("更新时间", format_timestamp(summary.updated)),
        ("推理支持", info.inference or "-"),
    )
    for name, value in rows:
        table.add_row(name, value)
    get_console().print(table)
    if info.description:
        get_console().print(info.description)
    if readme and info.readme:
        text = info.readme
        if len(text) > limit:
            text = text[:limit] + f"\n…（已截断，共 {len(info.readme)} 字符）"
        get_console().print(text)


@fcmd.tool(
    "llmdown",
    subcommand="local",
    help="列出本地已下载的模型目录",
    description="大模型仓库下载：搜索 / 详情 / 清单 / 多源测速 / 断点续传下载",
)
def local_cmd(root: Path | None = None) -> None:
    """列出 ``root`` 下已下载的模型及占用空间。

    Parameters
    ----------
    root:
        模型根目录，默认 ``./models``。
    """
    base = root if root is not None else Path("models")
    models = scan_local_models(base)
    if not models:
        get_console().print(f"[yellow]{base} 下没有已下载的模型[/yellow]")
        return
    table = Table(title=f"本地模型（{base}）", box="ascii")
    table.add_column("仓库", style="cyan", no_wrap=True)
    table.add_column("大小", justify="right", no_wrap=True)
    table.add_column("文件", justify="right", no_wrap=True)
    table.add_column("状态", no_wrap=True)
    table.add_column("路径")
    for model in models:
        status = "[green]完整[/green]" if model.complete else f"[yellow]未完成（{model.partial} 个 .part）[/yellow]"
        table.add_row(model.repo, human_size(model.size), str(model.files), status, str(model.path))
    get_console().print(table)


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
