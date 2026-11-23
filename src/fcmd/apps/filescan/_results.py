"""filescan 扫描结果的持久化映射：任务 id → :class:`ScanResult` 的 JSON 往返。

GUI 退出后结果页要能恢复每个任务的最近一次扫描，因此在 ``ui/filescanw-results.json``
里保留完整结果（命中原文 / 上下文、跳过与错误、统计）。口径：

- **只存结果，不存配置**：任务目录、规则集、扫描选项仍在 ``filescanw.json``；
  结果文件按任务 id 关联，任务被移除 / 清空时一并删除对应条目。
- **原文与缓存一致**：与扫描缓存（``cache/filescan-cache.db``）一样保存命中原文，
  用于「导出原文」与重新渲染；结果文件因此同样按敏感数据对待，只放在用户
  自己的 ``${FCMD_HOME:-~/.fcmd}/ui`` 下，不进仓库。
- **容错读取**：字段缺失 / 类型不符 / 单条损坏时跳过该条，不整份作废；
  版本号只用于未来迁移，当前未知版本仍按兼容字段读取。

本模块只做映射与校验，不读写文件、不导入 Qt，方便单测。
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.filescan._engine import (
    FileHit,
    RuleMatch,
    ScanError,
    ScanResult,
    ScanStats,
    SkipRecord,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "RESULTS_SCHEMA_VERSION",
    "result_from_mapping",
    "result_to_mapping",
    "results_from_mapping",
    "results_to_mapping",
]

RESULTS_SCHEMA_VERSION = 1
"""结果文件结构版本；未知版本仍按兼容字段读取。"""

_STAT_CASTS: tuple[tuple[str, type], ...] = (
    ("files_seen", int),
    ("files_scanned", int),
    ("files_reused", int),
    ("files_skipped", int),
    ("files_skipped_extension", int),
    ("files_skipped_size_time", int),
    ("files_skipped_binary", int),
    ("files_skipped_other", int),
    ("files_skipped_encrypted", int),
    ("files_skipped_corrupt", int),
    ("files_skipped_no_text", int),
    ("files_skipped_unsupported", int),
    ("files_failed", int),
    ("bytes_read", int),
    ("hit_files", int),
    ("hit_count", int),
    ("elapsed", float),
    ("cancelled", bool),
)
"""统计字段 → 类型；``ScanStats`` 的属性（范围外 / 吞吐）由这些字段计算，不落盘。"""


_DEFAULT_STATS = ScanStats()
"""读取用的默认统计值（只读，避免每次构造一遍 ``ScanStats``）。"""


def result_to_mapping(result: ScanResult) -> dict[str, Any]:
    """把一次扫描结果转成可写入 JSON 的字典（保留命中原文与上下文）。"""
    return {
        "roots": [str(root) for root in result.roots],
        "hits": [
            {
                "path": str(hit.path),
                "size": hit.size,
                "encoding": hit.encoding,
                "matches": [_match_to_mapping(match) for match in hit.matches],
            }
            for hit in result.hits
        ],
        "skipped": [{"path": str(item.path), "reason": item.reason} for item in result.skipped],
        "errors": [{"path": str(item.path), "message": item.message} for item in result.errors],
        "stats": {field.name: getattr(result.stats, field.name) for field in fields(ScanStats)},
    }


def _match_to_mapping(match: RuleMatch) -> dict[str, Any]:
    return {
        "rule_id": match.rule_id,
        "rule_name": match.rule_name,
        "line_no": match.line_no,
        "start": match.start,
        "end": match.end,
        "excerpt": match.excerpt,
        "context": match.context,
        "context_start": match.context_start,
        "location": match.location,
    }


def result_from_mapping(raw: Any) -> ScanResult | None:
    """从字典恢复扫描结果；不是映射时返回 ``None``，损坏条目逐条跳过。"""
    if not isinstance(raw, dict):
        return None
    roots = raw.get("roots")
    root_paths = (
        tuple(Path(item) for item in roots if isinstance(item, str) and item.strip()) if isinstance(roots, list) else ()
    )
    return ScanResult(
        roots=root_paths,
        hits=_hits_value(raw.get("hits")),
        skipped=_skipped_value(raw.get("skipped")),
        errors=_errors_value(raw.get("errors")),
        stats=_stats_value(raw.get("stats")),
    )


def _hits_value(raw: Any) -> list[FileHit]:
    if not isinstance(raw, list):
        return []
    hits: list[FileHit] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        hits.append(
            FileHit(
                path=Path(path),
                size=_int(item.get("size")),
                encoding=_str(item.get("encoding")),
                matches=_matches_value(item.get("matches")),
            )
        )
    return hits


def _matches_value(raw: Any) -> list[RuleMatch]:
    if not isinstance(raw, list):
        return []
    matches: list[RuleMatch] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        matches.append(
            RuleMatch(
                rule_id=_str(item.get("rule_id")),
                rule_name=_str(item.get("rule_name")),
                line_no=_int(item.get("line_no")),
                start=_int(item.get("start")),
                end=_int(item.get("end")),
                excerpt=_str(item.get("excerpt")),
                context=_str(item.get("context")),
                context_start=_int(item.get("context_start")),
                location=_str(item.get("location")),
            )
        )
    return matches


def _skipped_value(raw: Any) -> list[SkipRecord]:
    if not isinstance(raw, list):
        return []
    skipped: list[SkipRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        skipped.append(SkipRecord(path=Path(path), reason=_str(item.get("reason"))))
    return skipped


def _errors_value(raw: Any) -> list[ScanError]:
    if not isinstance(raw, list):
        return []
    errors: list[ScanError] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        errors.append(ScanError(path=Path(path), message=_str(item.get("message"))))
    return errors


def _stats_value(raw: Any) -> ScanStats:
    if not isinstance(raw, dict):
        return ScanStats()
    values: dict[str, Any] = {}
    for name, cast in _STAT_CASTS:
        default = getattr(_DEFAULT_STATS, name)
        value = raw.get(name, default)
        if cast is bool:
            values[name] = value if isinstance(value, bool) else default
        elif isinstance(value, int | float) and not isinstance(value, bool):
            values[name] = cast(value)
        else:
            values[name] = default
    return ScanStats(**values)


def results_to_mapping(results: Mapping[str, ScanResult]) -> dict[str, Any]:
    """把「任务 id → 结果」整体转成结果文件字典。"""
    return {
        "version": RESULTS_SCHEMA_VERSION,
        "results": {task_id: result_to_mapping(result) for task_id, result in results.items()},
    }


def results_from_mapping(raw: Any) -> dict[str, ScanResult]:
    """从结果文件字典恢复「任务 id → 结果」；无效任务 / 条目直接跳过。"""
    if not isinstance(raw, dict):
        return {}
    stored = raw.get("results")
    if not isinstance(stored, dict):
        return {}
    results: dict[str, ScanResult] = {}
    for task_id, item in stored.items():
        if not isinstance(task_id, str) or not task_id:
            continue
        result = result_from_mapping(item)
        if result is not None:
            results[task_id] = result
    return results


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
