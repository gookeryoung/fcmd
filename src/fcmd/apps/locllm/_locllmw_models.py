"""locllmw「模型」页的共享逻辑：搜索行 / 详情字段 / 下载设置与本地仓库行。

Qt 子页（``_locllmw_qt_center.py``）复用这里的口径，本模块不创建窗口；
下载设置持久化到 ``locllmw.json`` 的 ``download`` 字段。
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fcmd.apps.locllm._llmgguf import detect_precision
from fcmd.apps.locllm.llmbase import human_size
from fcmd.apps.locllm.llmdown import (
    LocalModel,
    ModelInfo,
    ModelSummary,
    format_timestamp,
    model_series,
    series_rank,
)

__all__ = [
    "DEFAULT_MODEL_FILTER",
    "DEFAULT_MODEL_SORT",
    "MODEL_FILTERS",
    "MODEL_SORTS",
    "DownloadGuiSettings",
    "detail_pairs",
    "filter_by_local_fit",
    "filter_by_name",
    "filter_models",
    "format_models_updated",
    "local_row",
    "matches_filter",
    "merge_installed_items",
    "model_item_lines",
    "model_row",
    "model_score",
    "open_path",
    "parse_patterns",
    "quant_precision_label",
    "repo_for_model_path",
    "settings_from_dict",
    "settings_to_dict",
    "sort_browse_items",
    "sort_models",
]

_PAGE_SIZE = 30
_MAX_WORKERS = 16
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
MODEL_FILTERS: tuple[tuple[str, str, str], ...] = (
    ("local", "适合本机", ""),
    ("all", "全部", ""),
    ("chat", "对话", "chat"),
    ("code", "编程", "coder"),
    ("vision", "看图", "vision"),
    ("reasoning", "推理", "reasoning"),
    ("video", "视频", "video"),
    ("audio", "音频", "audio"),
)
DEFAULT_MODEL_FILTER = "local"

#: 浏览页排序选项：(key, 显示名)；默认按下载量倒排。
MODEL_SORTS: tuple[tuple[str, str], ...] = (
    ("downloads", "按下载量"),
    ("created", "按发布时间"),
    ("modified", "按更新时间"),
    ("stars", "按星标"),
    ("default", "综合评分"),
)
DEFAULT_MODEL_SORT = "downloads"

#: 分类筛选命中的关键词：用于判断已安装模型是否属于当前类别（置顶条件）。
_FILTER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "chat": ("chat", "instruct", "conversation", "对话", "文本生成"),
    "code": ("code", "coder", "coding", "编程", "代码生成"),
    "vision": ("vision", "vl", "visual", "image", "multimodal", "看图", "视觉", "图像"),
    "reasoning": ("reasoning", "think", "r1", "qwq", "推理", "思考"),
    "video": ("video", "视频"),
    "audio": ("audio", "speech", "voice", "asr", "tts", "音频", "语音"),
}
_MODEL_COLUMNS: dict[str, tuple[str, int, Literal["center", "e", "n", "ne", "nw", "s", "se", "sw", "w"]]] = {
    "repo": ("模型", 180, "w"),
    "series": ("系列", 112, "w"),
    "tasks": ("任务", 108, "w"),
    "downloads": ("下载", 78, "e"),
    "size": ("体积", 70, "e"),
}


@dataclass
class DownloadGuiSettings:
    """模型页的下载参数（持久化到 ``locllmw.json`` 的 ``download`` 字段）。

    模型目录不在本设置里：下载与本地扫描统一使用 :mod:`_llmconfig` 的
    ``models_dir``，本结构只保存下载源与并发等参数。
    """

    source: str = "modelscope"
    workers: int = 4
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
    workers = raw.get("workers")
    if isinstance(workers, int) and not isinstance(workers, bool):
        settings.workers = min(max(workers, 1), _MAX_WORKERS)
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
        "workers": settings.workers,
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


def model_item_lines(item: ModelSummary) -> tuple[str, str, str]:
    """浏览列表自定义行的关键内容：``(模型名, 发布方 / 系列 / 任务, 体积)``。

    发布机构从名称移到下方描述；名称只保留模型名，避免行首被 ``组织/`` 占满。
    """
    owner = item.owner or (item.repo.split("/", 1)[0] if "/" in item.repo else "")
    title = item.name or item.repo.rsplit("/", 1)[-1]
    series = model_series(item)
    duplicate_series = bool(owner and series.casefold() == owner.casefold())
    subtitle = " · ".join(
        part
        for part in (
            f"发布方 {owner}" if owner else "",
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


def matches_filter(item: ModelSummary, filter_key: str) -> bool:
    """模型是否属于指定用途分类；``all`` / ``local`` / 空串视为全部符合。"""
    if filter_key in ("", "all", "local"):
        return True
    keywords = _FILTER_KEYWORDS.get(filter_key, ())
    haystack = " ".join((item.repo, item.name, *item.tasks, *item.tags)).casefold()
    return any(keyword.casefold() in haystack for keyword in keywords)


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


def filter_by_local_fit(items: tuple[ModelSummary, ...], capacity_bytes: int) -> tuple[ModelSummary, ...]:
    """筛选整仓体积不超过本机可承载容量的模型；未知容量或体积时保留。"""
    if capacity_bytes <= 0:
        return items
    return tuple(item for item in items if item.storage_size <= 0 or item.storage_size <= capacity_bytes)


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
