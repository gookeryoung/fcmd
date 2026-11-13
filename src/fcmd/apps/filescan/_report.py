"""filescan 报告：打码、过滤与文本 / JSON / CSV 导出（纯逻辑）。

默认打码：命中文本长度 ≥ 6 时保留首尾各 2 个字符，中间以 ``*`` 替换；更短的
整体替换为 ``***``。``include_raw=True`` 时才输出原文，供调用方显式开启。

本模块只依赖标准库，不导入 Tk。
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.filescan._engine import FileHit, RuleMatch, ScanResult, ScanStats, format_size

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "REPORT_FORMATS",
    "filter_result",
    "format_text_report",
    "mask_text",
    "no_hit_hint",
    "render_context",
    "result_to_csv",
    "result_to_json",
    "result_to_mapping",
    "scan_summary",
    "write_report",
]

REPORT_FORMATS: tuple[str, ...] = ("text", "json", "csv")


def mask_text(text: str) -> str:
    """按固定口径打码：保留首尾各 2 字符，短文本整体隐藏。"""
    if not text:
        return ""
    if len(text) < 6:
        return "***"
    return f"{text[:2]}{'*' * (len(text) - 4)}{text[-2:]}"


def render_context(match: RuleMatch, include_raw: bool = False) -> str:
    """返回上下文字符串；默认把上下文中的命中区间替换为打码文本。"""
    if include_raw:
        return match.context
    masked = mask_text(match.excerpt)
    start = match.context_start
    end = start + len(match.excerpt)
    return f"{match.context[:start]}{masked}{match.context[end:]}"


def _match_to_mapping(match: RuleMatch, include_raw: bool) -> dict[str, Any]:
    """单条命中的 JSON 结构（按需打码）。"""
    return {
        "rule_id": match.rule_id,
        "rule": match.rule_name,
        "line": match.line_no,
        "column": match.start + 1,
        "location": match.location,
        "excerpt": match.excerpt if include_raw else mask_text(match.excerpt),
        "context": render_context(match, include_raw),
    }


def result_to_mapping(result: ScanResult, include_raw: bool = False) -> dict[str, Any]:
    """把扫描结果转成可序列化字典（默认打码）。"""
    stats = result.stats
    return {
        "roots": [str(root) for root in result.roots],
        "stats": {
            "files_seen": stats.files_seen,
            "files_scanned": stats.files_scanned,
            "files_reused": stats.files_reused,
            "files_skipped": stats.files_skipped,
            "files_skipped_extension": stats.files_skipped_extension,
            "files_skipped_size_time": stats.files_skipped_size_time,
            "files_out_of_scope": stats.files_out_of_scope,
            "files_skipped_after_read": stats.files_skipped_after_read,
            "files_skipped_binary": stats.files_skipped_binary,
            "files_skipped_other": stats.files_skipped_other,
            "files_skipped_encrypted": stats.files_skipped_encrypted,
            "files_skipped_corrupt": stats.files_skipped_corrupt,
            "files_skipped_no_text": stats.files_skipped_no_text,
            "files_skipped_unsupported": stats.files_skipped_unsupported,
            "files_failed": stats.files_failed,
            "bytes_read": stats.bytes_read,
            "hit_files": stats.hit_files,
            "hit_count": stats.hit_count,
            "elapsed": round(stats.elapsed, 4),
            "bytes_per_second": round(stats.bytes_per_second, 1),
            "files_per_second": round(stats.files_per_second, 2),
            "cancelled": stats.cancelled,
        },
        "hits": [
            {
                "path": str(hit.path),
                "size": hit.size,
                "encoding": hit.encoding,
                "matches": [_match_to_mapping(match, include_raw) for match in hit.matches],
            }
            for hit in result.hits
        ],
        "skipped": [{"path": str(item.path), "reason": item.reason} for item in result.skipped],
        "errors": [{"path": str(item.path), "message": item.message} for item in result.errors],
    }


def result_to_json(result: ScanResult, include_raw: bool = False) -> str:
    """序列化为 JSON 文本（含末尾换行）。"""
    return json.dumps(result_to_mapping(result, include_raw), ensure_ascii=False, indent=2) + "\n"


def result_to_csv(result: ScanResult, include_raw: bool = False) -> str:
    """序列化为 CSV 文本（带 UTF-8 BOM，便于 Excel 直接打开）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["文件", "规则", "行号", "列号", "编码", "位置", "命中内容", "上下文"])
    for hit in result.hits:
        for match in hit.matches:
            writer.writerow(
                [
                    str(hit.path),
                    match.rule_name,
                    match.line_no,
                    match.start + 1,
                    hit.encoding,
                    match.location,
                    match.excerpt if include_raw else mask_text(match.excerpt),
                    render_context(match, include_raw),
                ]
            )
    return "\ufeff" + buffer.getvalue()


def _summary_line(result: ScanResult) -> str:
    """概要与吞吐口径（CLI 文本报告的第一段）。"""
    return scan_summary(result.stats)


def _document_parts(stats: ScanStats) -> str:
    """文档专属四类只在非零时追加，保持纯文本扫描的概要不被拉长。"""
    return "".join(
        f" / {label} {value}"
        for label, value in (
            ("加密", stats.files_skipped_encrypted),
            ("损坏", stats.files_skipped_corrupt),
            ("无文本", stats.files_skipped_no_text),
            ("不支持", stats.files_skipped_unsupported),
        )
        if value
    )


def scan_summary(stats: ScanStats, *, compact: bool = False) -> str:
    """扫描概要：区分「范围外（从未读取）」与「读取后跳过」。

    ``compact=True`` 时压成一行，供 GUI 状态栏使用；CLI 文本报告用两行，
    避免把后缀白名单排除的几十万文件与真正读盘后跳过的少量文件混成一个
    「跳过」数字，让人误以为扫描失败。
    """
    prefix = "扫描已取消（部分结果）" if stats.cancelled else "扫描完成"
    head = (
        f"{prefix}：文件 {stats.files_seen}"
        f"（实扫 {stats.files_scanned} / 缓存复用 {stats.files_reused}），"
        f"命中 {stats.hit_files} 个文件 / {stats.hit_count} 条"
    )
    scope = (
        f"范围外 {stats.files_out_of_scope}"
        f"（后缀白名单 {stats.files_skipped_extension} / 大小或时间 {stats.files_skipped_size_time}）；"
        f"读取后跳过 {stats.files_skipped_after_read}"
        f"（二进制 {stats.files_skipped_binary} / 其他 {stats.files_skipped_other}{_document_parts(stats)}）"
    )
    tail = (
        f"错误 {stats.files_failed}；读取 {format_size(stats.bytes_read)}，"
        f"耗时 {stats.elapsed:.2f} s（{format_size(int(stats.bytes_per_second))}/s，"
        f"{stats.files_per_second:.1f} 文件/s）"
    )
    separator = "；" if compact else "\n"
    return f"{head}{separator}{scope}；{tail}"


def no_hit_hint(stats: ScanStats) -> str | None:
    """0 命中时返回可操作诊断；有命中或扫描被取消时返回 ``None``。

    用户只看到「全是跳过」往往不是扫描失败，而是后缀白名单把大部分文件排除在
    扫描范围之外，或规则被禁用、文档本身没有文本层。这里把原因拆开说清楚。
    """
    if stats.cancelled or stats.hit_files:
        return None
    checked = stats.files_scanned + stats.files_reused
    parts = [f"已检查 {checked} 个文件（实扫 {stats.files_scanned} / 缓存复用 {stats.files_reused}）"]
    if stats.files_out_of_scope:
        parts.append(
            f"{stats.files_out_of_scope} 个文件不在扫描范围"
            f"（后缀白名单 {stats.files_skipped_extension} / 大小或时间 {stats.files_skipped_size_time}）"
        )
    read_skipped = stats.files_skipped_after_read
    if read_skipped:
        detail = f"，其中文档无文本层 {stats.files_skipped_no_text} 个" if stats.files_skipped_no_text else ""
        parts.append(f"{read_skipped} 个文件读取后跳过{detail}")
    return "未发现命中：" + "，".join(parts) + "；若预期有命中，请检查后缀白名单与规则启用状态。"


def format_text_report(result: ScanResult, include_raw: bool = False) -> str:
    """生成人类可读的文本报告。"""
    lines = [_summary_line(result)]
    hint = no_hit_hint(result.stats)
    if hint is not None:
        lines.append(hint)
    for hit in result.hits:
        lines.append(f"[命中] {hit.path}（{hit.encoding}，{format_size(hit.size)}）")
        if not hit.matches:
            lines.append("  - 表达式命中（无正向规则明细）")
            continue
        for match in hit.matches:
            where = f"{match.location} " if match.location else ""
            lines.append(f"  - {match.rule_name} @ {where}第 {match.line_no} 行：{render_context(match, include_raw)}")
    if result.skipped:
        lines.append("跳过：")
        lines.extend(f"  - {item.path}：{item.reason}" for item in result.skipped)
    if result.errors:
        lines.append("错误：")
        lines.extend(f"  - {item.path}：{item.message}" for item in result.errors)
    return "\n".join(lines)


def filter_result(
    result: ScanResult,
    *,
    rule_ids: Iterable[str] | None = None,
    path_contains: str = "",
) -> ScanResult:
    """按规则 id 与路径子串过滤命中，并重算命中计数（跳过与错误保留）。"""
    wanted = None if rule_ids is None else frozenset(rule_ids)
    needle = path_contains.strip().lower()
    hits: list[FileHit] = []
    for hit in result.hits:
        if needle and needle not in str(hit.path).lower():
            continue
        matches = [match for match in hit.matches if wanted is None or match.rule_id in wanted]
        if wanted is not None and not matches:
            continue
        hits.append(replace(hit, matches=matches))
    stats = replace(
        result.stats,
        hit_files=len(hits),
        hit_count=sum(len(hit.matches) for hit in hits),
    )
    return replace(result, hits=hits, stats=stats)


def write_report(
    result: ScanResult,
    path: Path,
    fmt: str = "text",
    include_raw: bool = False,
) -> Path:
    """按格式写报告文件；``fmt`` 非 ``text`` / ``json`` / ``csv`` 时抛 ``ValueError``。"""
    if fmt == "text":
        content = format_text_report(result, include_raw)
        encoding = "utf-8"
    elif fmt == "json":
        content = result_to_json(result, include_raw)
        encoding = "utf-8"
    elif fmt == "csv":
        content = result_to_csv(result, include_raw)
        encoding = "utf-8"
    else:
        raise ValueError(f"未知报告格式: {fmt!r}，只支持 {'/'.join(REPORT_FORMATS)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding, newline="")
    return path
