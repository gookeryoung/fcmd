"""llmw「模型」页的共享逻辑：搜索行 / 详情字段 / 下载设置与本地仓库行。

Qt 子页（``_llmw_qt_center.py``）复用这里的口径，本模块不创建窗口；
下载设置持久化到 ``llmw.json`` 的 ``download`` 字段。
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fcmd.apps.llm._llmgguf import detect_precision
from fcmd.apps.llm.llmbase import human_size
from fcmd.apps.llm.llmdown import (
    LocalModel,
    ModelFile,
    ModelInfo,
    ModelSummary,
    format_timestamp,
    model_series,
    series_rank,
)

__all__ = [
    "DEFAULT_MODEL_FILTER",
    "DEFAULT_MODEL_SORT",
    "FILE_STATE_DONE",
    "FILE_STATE_MISSING",
    "FILE_STATE_PARTIAL",
    "MIN_ONLINE_MODEL_BYTES",
    "MODEL_FILTERS",
    "MODEL_SORTS",
    "MULTI_SEARCH_TERMS",
    "TEXT_SEARCH_TERMS",
    "USAGE_KEYS",
    "DownloadGuiSettings",
    "dedupe_by_repo",
    "detail_pairs",
    "file_local_state",
    "files_needing_download",
    "filter_by_local_fit",
    "filter_by_name",
    "filter_models",
    "format_models_updated",
    "group_same_name",
    "is_usable_item",
    "local_row",
    "matches_category",
    "matches_filter",
    "merge_installed_items",
    "model_item_lines",
    "model_owner",
    "model_param_billions",
    "model_row",
    "model_score",
    "open_path",
    "parse_patterns",
    "quant_precision_label",
    "repo_for_model_path",
    "running_usage",
    "running_usage_counts",
    "settings_from_dict",
    "settings_to_dict",
    "sort_browse_items",
    "sort_models",
]

_PAGE_SIZE = 30
_MAX_WORKERS = 8
_MAX_PARALLEL = 4
_SOURCES = ("modelscope", "hf-mirror", "auto")
_SOURCE_LABELS = {"modelscope": "ModelScope", "hf-mirror": "hf-mirror", "auto": "自动测速"}
_SOURCE_KEYS: dict[str, Literal["modelscope", "hf-mirror", "auto"]] = {
    "ModelScope": "modelscope",
    "hf-mirror": "hf-mirror",
    "自动测速": "auto",
}
_SORT_LABELS = {
    "default": "默认排序",
    "downloads": "下载最多",
    "stars": "星标最多",
    "created": "最新发布",
    "modified": "最近更新",
}
_SORT_KEYS = {label: key for key, label in _SORT_LABELS.items()}
_ALL_TASKS = "全部类别"
_ALL_SERIES = "全部系列"

#: 浏览页顶部筛选：(key, 显示名, 服务端搜索关键字)；关键字为空表示浏览全部。
#:
#: 分类按「能力域」收敛为文本 / 图像 / 视频 / 音频四类：对话、编程、推理都是
#: 文本生成能力（细分由模型标签体现），看图与画图都是图像域（理解 / 生成），
#: 不再各占一个互相交叉的 chip。
PAINT_FILTER_QUERY = "图像生成"
"""图像分类的展示关键字（仅用于状态 / 缓存键），真正检索走多关键字合并。"""

TEXT_FILTER_QUERY = "文本对话"
"""文本分类的展示关键字（仅用于状态 / 缓存键），真正检索走多关键字合并。"""

PAINT_SEARCH_TERMS: tuple[str, ...] = (
    "Qwen-Image-2.1",
    "Qwen-Image",
    "Z-Image",
    "z_image",
    "MiniMax-H3-Image",
    "FLUX",
    "HiDream",
    "Kolors",
    "HunyuanImage",
    "Stable Diffusion",
)
"""图像分类并行检索的生成模型家族关键字；覆盖官方仓库与主流社区实现。"""

TEXT_SEARCH_TERMS: tuple[str, ...] = ("chat", "instruct", "coder", "reasoning")
"""文本分类并行检索的关键字：通用对话 / 指令 / 编程 / 推理四类代表。"""

IMAGE_SEARCH_TERMS: tuple[str, ...] = (*PAINT_SEARCH_TERMS, "vision", "Qwen-VL", "OCR")
"""图像分类并行检索：生成家族 + 视觉理解代表（看图与画图合并检索）。"""

MULTI_SEARCH_TERMS: dict[str, tuple[str, ...]] = {
    TEXT_FILTER_QUERY: TEXT_SEARCH_TERMS,
    PAINT_FILTER_QUERY: IMAGE_SEARCH_TERMS,
}
"""需要并行检索的分类：展示关键字 → 真正请求的关键字列表。"""

MODEL_FILTERS: tuple[tuple[str, str, str], ...] = (
    ("local", "适合本机", ""),
    ("all", "全部", ""),
    ("text", "文本", TEXT_FILTER_QUERY),
    ("image", "图像", PAINT_FILTER_QUERY),
    ("video", "视频", "video"),
    ("audio", "音频", "audio"),
)
DEFAULT_MODEL_FILTER = "local"

MIN_ONLINE_MODEL_BYTES = 1024 * 1024
"""在线模型的最小体积：`StorageSize` 小于 1 MiB 的条目视为个人上传的空壳 / 垃圾模型。

体积为 0 表示接口没给出大小（未知），保留而不是误杀；只有明确小于 1 MiB 的
在线仓库才判为无效。「隐藏无效」开启时这些条目连同无法下载的 repo 一起被过滤。
"""

#: 浏览页排序选项：(key, 显示名)；默认按下载量倒排。
MODEL_SORTS: tuple[tuple[str, str], ...] = (
    ("downloads", "按下载量"),
    ("created", "按发布时间"),
    ("modified", "按更新时间"),
    ("stars", "按星标"),
    ("size", "按体积"),
    ("name", "按名称"),
    ("default", "综合评分"),
)
DEFAULT_MODEL_SORT = "downloads"

#: 分类筛选命中的关键词：用于判断已安装模型是否属于当前类别（置顶条件）。
#: 关键词按「具体 → 宽泛」使用：媒体类（画图 / 视频 / 音频）优先于「对话」，
#: 避免 ``文本生成`` 命中 ``文本生成图片`` / ``文本生成视频`` 这类任务名。
_FILTER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "chat": ("chat", "instruct", "conversation", "对话", "assistant"),
    "code": ("code", "coder", "coding", "编程", "代码生成"),
    "vision": (
        "vision",
        "vl",
        "visual",
        "multimodal",
        "看图",
        "视觉",
        "图像理解",
        "图片理解",
        "图文",
        "ocr",
    ),
    "paint": (
        "diffusion",
        "stable-diffusion",
        "sdxl",
        "sd3",
        "flux",
        "qwen-image",
        "z-image",
        "z_image",
        "minimax-h3-image",
        "h3-image",
        "hidream",
        "kolors",
        "hunyuan-image",
        "文生图",
        "画图",
        "图像生成",
    ),
    "reasoning": ("reasoning", "think", "r1", "qwq", "推理", "思考"),
    "video": (
        "video",
        "视频",
        "wan2",
        "wan-ai",
        "cogvideo",
        "hunyuan-video",
        "ltx-video",
        "mochi",
        "seedance",
        "kling",
    ),
    "audio": (
        "audio",
        "speech",
        "voice",
        "asr",
        "tts",
        "音频",
        "语音",
        "whisper",
        "cosyvoice",
        "sensevoice",
        "funasr",
        "musicgen",
    ),
}

#: 任务名里的「准确」分类：ModelScope 的任务标签是中文短语，不能用
#: ``文本生成`` 做子串匹配（会命中 ``文本生成图片`` / ``文本生成视频``）。
_CHAT_TASKS = frozenset({"文本生成", "自然语言推理", "对话生成", "文本分类", "文本摘要", "机器翻译"})
_PAINT_TASKS = frozenset(
    {"文本生成图片", "图片生成图片", "文生图", "图生图", "图像生成", "图片生成", "图像编辑", "图片编辑", "文生图编辑"}
)
_AUDIO_TASK_HINTS = ("语音", "音频", "音乐")
_VISION_TASK_HINTS = ("图像理解", "图片理解", "视觉", "图文", "看图", "ocr")
_VIDEO_TASK_HINTS = ("视频生成", "生成视频", "视频编辑", "视频生视频", "文生视频", "图生视频")
_MODEL_COLUMNS: dict[str, tuple[str, int, Literal["center", "e", "n", "ne", "nw", "s", "se", "sw", "w"]]] = {
    "repo": ("模型", 180, "w"),
    "series": ("系列", 112, "w"),
    "tasks": ("任务", 108, "w"),
    "downloads": ("下载", 78, "e"),
    "size": ("体积", 70, "e"),
}


@dataclass
class DownloadGuiSettings:
    """模型页的下载参数（持久化到 ``llmw.json`` 的 ``download`` 字段）。

    模型目录不在本设置里：下载与本地扫描统一使用 :mod:`_llmconfig` 的
    ``models_dir``，本结构只保存下载源与并发等参数。
    """

    source: str = "auto"
    workers: int = 8
    parallel: int = 2
    verify: bool = True
    include: str = ""
    exclude: str = ""


def settings_from_dict(raw: object) -> DownloadGuiSettings:
    """从配置字典还原下载参数；缺失 / 非法字段逐项回退默认值。"""
    settings = DownloadGuiSettings()
    if not isinstance(raw, Mapping):
        return settings
    source = raw.get("source")
    if isinstance(source, str) and source in _SOURCES:
        settings.source = source
    # 旧版 GUI 默认固定 ModelScope（不测速 / 无兜底）：没有新标记时一次性迁移到
    # 自动测速，避免升级后仍被单源限速卡住。
    if "source_probe" not in raw and settings.source == "modelscope":
        settings.source = "auto"
    workers = raw.get("workers")
    if isinstance(workers, int) and not isinstance(workers, bool):
        settings.workers = min(max(workers, 1), _MAX_WORKERS)
    parallel = raw.get("parallel")
    if isinstance(parallel, int) and not isinstance(parallel, bool):
        settings.parallel = min(max(parallel, 1), _MAX_PARALLEL)
    verify = raw.get("verify")
    if isinstance(verify, bool):
        settings.verify = verify
    for field in ("include", "exclude"):
        value = raw.get(field)
        if isinstance(value, str):
            setattr(settings, field, value)
    return settings


def settings_to_dict(settings: DownloadGuiSettings) -> dict[str, Any]:
    """把下载参数转换为可写入 JSON 的字典。"""
    return {
        "source": settings.source,
        "source_probe": True,
        "workers": settings.workers,
        "parallel": settings.parallel,
        "verify": settings.verify,
        "include": settings.include,
        "exclude": settings.exclude,
    }


def parse_patterns(text: str) -> list[str] | None:
    """把空格 / 逗号 / 分号分隔的掩码文本转换为列表；空文本返回 ``None``。"""
    items = [item for item in re.split(r"[\s,;]+", text.strip()) if item]
    return items or None


def model_row(item: ModelSummary) -> tuple[str, str, str, str, str]:
    """模型列表的一行：仓库、系列、任务、下载量、体积。"""
    return (
        item.repo,
        model_series(item),
        "、".join(item.tasks) or "-",
        f"{item.downloads:,}",
        human_size(item.storage_size) if item.storage_size else "-",
    )


def model_owner(item: ModelSummary) -> str:
    """仓库发布方（优先元数据，其次从 ``组织/模型`` 推导）。"""
    return item.owner or (item.repo.split("/", 1)[0] if "/" in item.repo else "")


FILE_STATE_DONE = "已下载"
FILE_STATE_PARTIAL = "部分下载"
FILE_STATE_MISSING = "未下载"
"""文件列表「状态」列取值；本地位置未知（纯在线浏览）时用 ``-``。"""


def file_local_state(root: Path | None, relative: str) -> str:
    """仓库内单个文件在本地的下载状态（只看文件系统，不发网络请求）。

    完整文件优先：最终文件存在即「已下载」（同名的残留 ``.part`` 由下载层自行清理）；
    否则 ``<文件>.part`` 存在即「部分下载」；都没有算「未下载」。``root`` 为 ``None``
    表示本地位置未知（未配置模型目录 / 仅在线浏览），返回 ``-``。
    """
    if root is None:
        return "-"
    target = root / Path(str(relative))
    if target.is_file():
        return FILE_STATE_DONE
    if target.with_name(target.name + ".part").is_file():
        return FILE_STATE_PARTIAL
    return FILE_STATE_MISSING


def files_needing_download(root: Path | None, files: Sequence[ModelFile]) -> tuple[ModelFile, ...]:
    """``files`` 里还没下载完的部分：已下载的跳过，``.part`` 与未开始都算待下载。"""
    return tuple(file for file in files if file_local_state(root, file.path) != FILE_STATE_DONE)


def dedupe_by_repo(items: Sequence[ModelSummary]) -> tuple[ModelSummary, ...]:
    """按 ``repo`` 大小写不敏感去重，保留首次出现的摘要。"""
    seen: set[str] = set()
    result: list[ModelSummary] = []
    for item in items:
        key = item.repo.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def group_same_name(
    items: Sequence[ModelSummary],
    installed_repos: set[str],
) -> tuple[tuple[ModelSummary, int], ...]:
    """按模型名折叠同名仓库，优先已安装、其次综合评分最高。

    返回 ``(代表仓库, 同名数量)``；调用方用于「同名折叠」视图。

    **只折叠远程仓库**（``组织/模型``）：本地目录条目（``repo`` 里没有 ``/``，例如磁盘上的
    ``Qwen3.8-27B``）不是仓库 id，只是恰好同名的文件夹；把它和官方 ``Qwen/Qwen3.8-27B``
    折进一组时，代表行会按「已安装优先」选中本地目录，官方模型就被藏起来（列表里只剩
    「同名 2」的本地行）。所以本地目录各自独立成行。
    """
    installed = {repo.casefold() for repo in installed_repos}
    chosen: list[tuple[ModelSummary, int]] = []
    groups: dict[str, list[ModelSummary]] = {}
    for item in items:
        if "/" not in item.repo:
            chosen.append((item, 1))
            continue
        key = (item.name or item.repo.rsplit("/", 1)[-1]).casefold()
        groups.setdefault(key, []).append(item)
    for group in groups.values():
        group.sort(key=lambda item: (item.repo.casefold() not in installed, -model_score(item), -item.downloads))
        chosen.append((group[0], len(group)))
    return tuple(sorted(chosen, key=lambda pair: pair[0].repo.casefold()))


def model_item_lines(item: ModelSummary) -> tuple[str, str, str]:
    """浏览列表自定义行的关键内容：``(模型名, 发布方 / 系列 / 任务, 体积)``。

    发布机构从名称移到下方描述；名称只保留模型名，避免行首被 ``组织/`` 占满。
    """
    owner = model_owner(item)
    title = item.name or item.repo.rsplit("/", 1)[-1]
    series = model_series(item)
    duplicate_series = bool(owner and series.casefold() == owner.casefold())
    subtitle = " · ".join(
        part
        for part in (
            "" if duplicate_series else series,
            "、".join(item.tasks),
        )
        if part
    )
    metrics = human_size(item.storage_size) if item.storage_size else ""
    return title, subtitle or "-", metrics or "-"


def model_published_text(item: ModelSummary) -> str:
    """名称旁的发布时间标签；无有效时间时返回空串。"""
    stamp = format_timestamp(item.updated)
    return "" if stamp == "-" else f"发布 {stamp}"


def repo_for_model_path(path: Path, models_root: Path) -> str:
    """从 GGUF 路径反推所在仓库标识（与 :func:`llmdown.scan_local_models` 同口径）。

    标准下载布局 ``<root>/<组织>/<模型>/x.gguf`` 返回 ``组织/模型``；手工整理
    的 ``<root>/<模型>/x.gguf`` 返回 ``模型``；不在模型目录下或布局无法判定时
    返回空串。
    """
    try:
        relative = Path(path).resolve().relative_to(Path(models_root).resolve())
    except (OSError, ValueError):
        return ""
    parts = relative.parts
    if len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}"
    if len(parts) == 2:
        return parts[0]
    return ""


def format_models_updated(value: str | None) -> str:
    """把 ISO 时间格式化为状态栏短文本；空 / 非法值返回空串。"""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return ""
    return f"更新于 {moment:%Y-%m-%d %H:%M}"


def quant_precision_label(path: Path) -> str:
    """GGUF 的量化精度短标签（如 ``Q4_K_M``）；全量 / 未知精度返回空串。"""
    precision = detect_precision(path, tensors=False)
    if precision.quantized is not True:
        return ""
    return precision.raw_label or precision.filename_label or precision.label.removesuffix(" 量化")


def model_score(item: ModelSummary) -> int:
    """浏览排序评分：下载量 60 + 星标 40，按对数压缩到 0-100。"""
    downloads = math.log10(item.downloads + 1) if item.downloads > 0 else 0.0
    stars = math.log10(item.stars + 1) if item.stars > 0 else 0.0
    return round(min(downloads, 6.0) / 6.0 * 60 + min(stars, 4.0) / 4.0 * 40)


def is_usable_item(item: ModelSummary, usable_repos: set[str]) -> bool:
    """模型条目是否「有效」：可下载的在线仓库，或本地目录里确有可用权重。

    - 在线结果要求 ``组织/模型`` 两段式 repo（否则无法构造下载 / 缓存键）；
    - 仅本地条目要求目录内有 GGUF / safetensors 等权重文件，或仍有 ``.part``
      未完成下载（此时保留是为了让用户继续下载）。
    只有 README / 配置文件 / 空目录的「假模型」会被过滤掉。
    """
    parts = item.repo.split("/")
    if len(parts) == 2 and all(parts):
        # KB 级仓库基本都是个人上传的说明 / 空壳；0 表示接口未给体积，按未知保留。
        return not (0 < item.storage_size < MIN_ONLINE_MODEL_BYTES)
    return item.repo.casefold() in usable_repos


def _haystack(item: ModelSummary) -> str:
    """分类匹配的文本面：仓库、名称、任务与标签（全部小写）。"""
    return " ".join((item.repo, item.name, item.chinese_name, *item.tasks, *item.tags)).casefold()


def _keyword_match(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword.casefold() in text for keyword in keywords)


def _task_match(item: ModelSummary, tasks: frozenset[str]) -> bool:
    return any(task in tasks for task in item.tasks)


def _task_hint_match(item: ModelSummary, hints: Sequence[str]) -> bool:
    lowered = " ".join(item.tasks).casefold()
    return any(hint.casefold() in lowered for hint in hints)


def _is_paint(item: ModelSummary) -> bool:
    if _is_video(item) or _is_audio(item):
        return False
    return _task_match(item, _PAINT_TASKS) or _keyword_match(_haystack(item), _FILTER_KEYWORDS["paint"])


def _is_video(item: ModelSummary) -> bool:
    return _task_hint_match(item, _VIDEO_TASK_HINTS) or _keyword_match(_haystack(item), _FILTER_KEYWORDS["video"])


def _is_audio(item: ModelSummary) -> bool:
    return _task_hint_match(item, _AUDIO_TASK_HINTS) or _keyword_match(_haystack(item), _FILTER_KEYWORDS["audio"])


def _is_vision(item: ModelSummary) -> bool:
    return _task_hint_match(item, _VISION_TASK_HINTS) or _keyword_match(_haystack(item), _FILTER_KEYWORDS["vision"])


_MEDIA_MATCHERS = {"paint": _is_paint, "video": _is_video, "audio": _is_audio, "vision": _is_vision}
"""需要任务名精确判断的媒体 / 视觉分类。"""

_FILTER_GROUPS: dict[str, tuple[str, ...]] = {
    "text": ("chat", "code", "reasoning"),
    "image": ("vision", "paint"),
}
"""浏览页合并分类：能力域 → 内部细分（运行页仍按内部 key 区分用途）。"""


def _matches_single(item: ModelSummary, filter_key: str) -> bool:
    """单个内部细分（对话 / 编程 / 推理 / 看图 / 画图 / 视频 / 音频）是否命中。"""
    matcher = _MEDIA_MATCHERS.get(filter_key)
    if matcher is not None:
        return matcher(item)
    haystack = _haystack(item)
    if filter_key == "chat":
        if _is_paint(item) or _is_video(item) or _is_audio(item):
            return False
        return _task_match(item, _CHAT_TASKS) or _keyword_match(haystack, _FILTER_KEYWORDS.get("chat", ()))
    return _keyword_match(haystack, _FILTER_KEYWORDS.get(filter_key, ()))


def matches_filter(item: ModelSummary, filter_key: str) -> bool:
    """模型是否属于指定分类；``all`` / ``local`` / 空串视为全部符合。

    浏览页的 ``text`` / ``image`` 是能力域：文本 = 对话 / 编程 / 推理；
    图像 = 看图 / 画图。媒体类优先于「对话」：画图 / 视频 / 音频任务里常带
    「文本生成」前缀，只看关键词会把它们混进文本列表（Qwen-Image、Wan 视频等）。
    """
    if filter_key in ("", "all", "local"):
        return True
    group = _FILTER_GROUPS.get(filter_key)
    if group is not None:
        if filter_key == "text" and (_is_paint(item) or _is_video(item) or _is_audio(item)):
            return False
        return any(_matches_single(item, key) for key in group)
    return _matches_single(item, filter_key)


def matches_category(name: str, filter_key: str) -> bool:
    """本地模型名是否属于指定用途分类（运行页视频 / 音频候选筛选）。

    与 :func:`matches_filter` 共用同一张分类关键字表；``all`` / ``local`` / 空串
    视为全部符合，未登记的分类视为无匹配。只有名称可用，因此按关键词判断；
    「对话」同样排除画图 / 视频 / 音频关键词，避免媒体模型落到聊天页签。
    """
    if filter_key in ("", "all", "local"):
        return True
    lowered = name.casefold()
    if filter_key == "chat":
        for media in ("paint", "video", "audio"):
            if _keyword_match(lowered, _FILTER_KEYWORDS[media]):
                return False
    return _keyword_match(lowered, _FILTER_KEYWORDS.get(filter_key, ()))


USAGE_KEYS: tuple[str, ...] = ("text", "paint", "video", "audio")
"""运行页页签的用途 key（与运行中模型的数量徽标同一顺序）。"""


def running_usage(model_name: str) -> str:
    """运行中的模型名 → 用途 key（与运行页各页签的模型候选口径一致）。

    视频 / 音频先按分类关键字判定（更具体），其余（对话 / 编程 / 推理）统一算「文本」；
    图像是 sd.cpp 的独立服务，不经过这里（由调用方按画图服务存活情况单独计数）。
    """
    for usage in ("video", "audio"):
        if matches_category(model_name, usage):
            return usage
    return "text"


def running_usage_counts(models: Sequence[str], *, paint_alive: bool = False) -> dict[str, int]:
    """按用途统计运行中的模型数量；``paint_alive`` 表示 sd.cpp 画图服务在运行。"""
    counts = dict.fromkeys(USAGE_KEYS, 0)
    for name in models:
        counts[running_usage(name)] += 1
    if paint_alive:
        counts["paint"] += 1
    return counts


def merge_installed_items(
    items: tuple[ModelSummary, ...],
    installed: Mapping[str, ModelSummary],
    filter_key: str,
) -> tuple[ModelSummary, ...]:
    """把不在当前页结果里的已安装模型补进来；分类筛选只补匹配该用途的。"""
    present = {item.repo.casefold() for item in items}
    extra = tuple(
        summary
        for repo, summary in installed.items()
        if repo not in present and (filter_key in ("all", "local") or matches_filter(summary, filter_key))
    )
    return (*items, *extra)


def sort_browse_items(
    items: tuple[ModelSummary, ...],
    installed_repos: set[str],
    filter_key: str,
    sort_key: str = "default",
) -> tuple[ModelSummary, ...]:
    """已安装且匹配当前分类的模型置顶，其余按 ``sort_key`` 倒排。"""
    installed = {repo.casefold() for repo in installed_repos}

    def pinned(item: ModelSummary) -> bool:
        return item.repo.casefold() in installed and matches_filter(item, filter_key)

    def order(item: ModelSummary) -> tuple[object, ...]:
        if sort_key == "downloads":
            return (-item.downloads, -item.stars, item.repo.casefold())
        if sort_key in ("created", "modified"):
            return (-item.updated, -item.downloads, item.repo.casefold())
        if sort_key == "stars":
            return (-item.stars, -item.downloads, item.repo.casefold())
        if sort_key == "size":
            return (-item.storage_size, -item.downloads, item.repo.casefold())
        if sort_key == "name":
            return (item.name.casefold(), -item.downloads, item.repo.casefold())
        return (-model_score(item), -item.downloads, -item.stars, item.repo.casefold())

    return tuple(sorted(items, key=lambda item: (not pinned(item), *order(item))))


def filter_by_name(items: tuple[ModelSummary, ...], query: str) -> tuple[ModelSummary, ...]:
    """按名称筛选当前页结果：仓库名 / 模型名 / 中文名任一包含关键字即可。

    关键字去首尾空白、大小写不敏感；空查询原样返回，避免列表无谓重建。
    """
    text = query.strip().casefold()
    if not text:
        return items
    return tuple(
        item
        for item in items
        if text in item.repo.casefold() or text in item.name.casefold() or text in item.chinese_name.casefold()
    )


_PARAM_RE = re.compile(r"(?<![0-9A-Za-z])(\d+(?:\.\d+)?)\s*[bB](?![0-9A-Za-z])")
_MOE_PARAM_RE = re.compile(r"(?<![0-9A-Za-z])(\d+)\s*[xX]\s*(\d+(?:\.\d+)?)\s*[bB](?![0-9A-Za-z])")
"""模型名里的参数量：``8B`` / ``30B-A3B`` / ``8x7B``；用于估算单个 GGUF 的体积。"""

GGUF_BYTES_PER_PARAM = 0.65
"""GGUF 下载体积的保守估算（Q4 系）。仓库总大小是多精度之和，不能直接当单文件大小。"""


def model_param_billions(name: str) -> float | None:
    """从模型名 / 仓库名解析参数总量（十亿），解析不到返回 ``None``。

    - ``Qwen3-8B`` → 8；``Qwen3-Coder-30B-A3B`` 取总参数 30（不是激活的 3）；
    - ``Mixtral-8x7B`` → 56；``Llama-3.1-8B`` 取 8（不把版本号 3.1 当参数）。
    """
    text = name or ""
    moe = [float(count) * float(per) for count, per in _MOE_PARAM_RE.findall(text)]
    dense = [float(value) for value in _PARAM_RE.findall(text)]
    values = [value for value in (*moe, *dense) if value > 0]
    return max(values) if values else None


def local_fit_size(item: ModelSummary) -> int:
    """估算「适合本机」判定用的单模型体积（字节）。

    GGUF 仓库的总大小是 Q2–Q8 多份量化的累加（例如 Qwen3-8B-GGUF 约 32 GB），
    用它判断本机是否放得下会把几乎所有官方 GGUF 仓库误杀。这里按名字里的参数量
    估算单份 Q4 GGUF；解析不到参数量时返回 0（未知，保留）。非 GGUF 仓库仍用
    接口给出的整仓体积。
    """
    tags = {tag.casefold() for tag in item.tags}
    is_gguf = "gguf" in tags or item.name.casefold().endswith("-gguf") or item.repo.casefold().endswith("-gguf")
    if not is_gguf:
        return item.storage_size
    params = model_param_billions(item.name or item.repo.rsplit("/", 1)[-1])
    if params is None:
        return 0
    return int(params * GGUF_BYTES_PER_PARAM * 1_000_000_000)


def filter_by_local_fit(
    items: tuple[ModelSummary, ...],
    capacity_bytes: int,
    *,
    pinned: Iterable[str] = (),
) -> tuple[ModelSummary, ...]:
    """筛选单模型体积不超过本机可承载容量的模型；未知容量或体积时保留。

    GGUF 仓库按单份量化估算（见 :func:`local_fit_size`），不会因为仓库里带齐
    Q2–Q8 全部精度而被整仓体积误判为「不适合本机」。

    ``pinned`` 是**已安装模型的名称**（模型名 / 目录名，大小写不敏感）：这些名字对应的
    条目始终保留。用户已经在用这个模型时，官方同名条目（原始权重 / GGUF 变体）的整仓
    体积（几十 GB）会超过「显存 / 内存」口径的容量估算而被整条藏起来，但实际要下的只是
    其中一部分量化文件——不能因为估算把官方可下载项藏掉。
    """
    keep = {name.casefold() for name in pinned if name}
    if capacity_bytes <= 0:
        return items
    kept: list[ModelSummary] = []
    for item in items:
        last = item.repo.rsplit("/", 1)[-1].casefold()
        if item.name.casefold() in keep or last in keep:
            kept.append(item)
            continue
        size = local_fit_size(item)
        if size <= 0 or size <= capacity_bytes:
            kept.append(item)
    return tuple(kept)


def filter_models(items: tuple[ModelSummary, ...], task_label: str) -> tuple[ModelSummary, ...]:
    """按任务中文名筛选模型；``全部类别`` / 空串表示不过滤。"""
    if not task_label or task_label == _ALL_TASKS:
        return items
    return tuple(item for item in items if task_label in item.tasks)


def sort_models(
    items: tuple[ModelSummary, ...],
    column: str | None,
    descending: bool = True,
) -> tuple[ModelSummary, ...]:
    """按列表列排序（对当前页生效）；未知列保持原顺序。"""
    keys = {
        "repo": lambda item: item.repo.casefold(),
        "series": lambda item: series_rank(model_series(item)),
        "tasks": lambda item: "、".join(item.tasks).casefold(),
        "downloads": lambda item: item.downloads,
        "size": lambda item: item.storage_size,
    }
    key = keys.get(column or "")
    if key is None:
        return items
    return tuple(sorted(items, key=key, reverse=descending))


def local_row(item: LocalModel) -> tuple[str, str, str, str, str]:
    """本地模型列表的一行：仓库、大小、文件数、状态、路径。"""
    status = "完整" if item.complete else f"未完成（{item.partial} 个 .part）"
    return (item.repo, human_size(item.size), str(item.files), status, str(item.path))


def detail_pairs(info: ModelInfo) -> tuple[tuple[str, str], ...]:
    """把模型详情转换为「字段 / 值」对，供界面与测试复用。"""
    summary = info.summary
    return (
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


def open_path(path: Path) -> None:
    """用系统文件管理器打开目录（Windows / macOS / Linux）。"""
    if sys.platform == "win32":
        import os

        os.startfile(path)
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command)
