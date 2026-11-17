"""locllmw「模型」页的共享逻辑：搜索行 / 详情字段 / 下载设置与本地仓库行。

Qt 子页（``_locllmw_qt_center.py``）复用这里的口径，本模块不创建窗口；
下载设置持久化到 ``locllmw.json`` 的 ``download`` 字段。
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
    "DEFAULT_MODEL_TAB",
    "MODEL_TAB_NAMES",
    "DownloadGuiSettings",
    "detail_pairs",
    "filter_models",
    "local_row",
    "model_item_lines",
    "model_row",
    "open_path",
    "parse_patterns",
    "settings_from_dict",
    "settings_to_dict",
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
_MODEL_COLUMNS: dict[str, tuple[str, int, Literal["center", "e", "n", "ne", "nw", "s", "se", "sw", "w"]]] = {
    "repo": ("模型", 180, "w"),
    "series": ("系列", 112, "w"),
    "tasks": ("任务", 108, "w"),
    "downloads": ("下载", 78, "e"),
    "size": ("体积", 70, "e"),
}

# 子页顺序是唯一来源：控件创建、默认选中与测试都从这里派生，避免散落的索引常量。
MODEL_TAB_NAMES: tuple[str, ...] = ("浏览", "下载", "已安装", "转换", "量化")
DEFAULT_MODEL_TAB = "浏览"


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
    """浏览列表自定义行的关键内容：``(仓库名, 系列 / 任务, 下载 / 体积)``。"""
    subtitle = " · ".join(part for part in (model_series(item), "、".join(item.tasks)) if part)
    metrics = " · ".join(
        part
        for part in (
            f"下载 {item.downloads:,}" if item.downloads else "",
            human_size(item.storage_size) if item.storage_size else "",
        )
        if part
    )
    return item.repo, subtitle or "-", metrics or "-"


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
