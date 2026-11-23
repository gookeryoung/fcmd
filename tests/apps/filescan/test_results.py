"""扫描结果持久化映射：完整往返、容错读取与空结果。"""

from __future__ import annotations

from pathlib import Path

from fcmd.apps.filescan._engine import (
    FileHit,
    RuleMatch,
    ScanError,
    ScanResult,
    ScanStats,
    SkipRecord,
)
from fcmd.apps.filescan._results import (
    results_from_mapping,
    results_to_mapping,
)


def _result(root: Path) -> ScanResult:
    return ScanResult(
        roots=(root,),
        hits=[
            FileHit(
                path=root / "notes.txt",
                size=42,
                encoding="utf-8",
                matches=[
                    RuleMatch(
                        rule_id="phone",
                        rule_name="手机号",
                        line_no=7,
                        start=11,
                        end=22,
                        excerpt="13800138000",
                        context="客户电话 13800138000",
                        context_start=5,
                        location="段落 2",
                    )
                ],
            )
        ],
        skipped=[SkipRecord(path=root / "bin.dat", reason="二进制")],
        errors=[ScanError(path=root / "bad.txt", message="读取失败")],
        stats=ScanStats(
            files_seen=9,
            files_scanned=4,
            files_reused=2,
            files_skipped=5,
            files_skipped_extension=1,
            files_skipped_size_time=1,
            files_skipped_binary=2,
            files_failed=1,
            bytes_read=1234,
            hit_files=1,
            hit_count=1,
            elapsed=0.5,
            cancelled=True,
        ),
    )


def test_result_roundtrip_keeps_raw_hits_and_stats(tmp_path: Path) -> None:
    """命中原文 / 上下文、跳过与错误、统计字段都能原样往返。"""
    original = _result(tmp_path)
    stored = results_to_mapping({"task-1": original})
    assert stored["version"] == 1
    restored = results_from_mapping(stored)
    assert list(restored) == ["task-1"]
    result = restored["task-1"]
    assert result.roots == original.roots
    assert result.stats == original.stats
    match = result.hits[0].matches[0]
    assert match.excerpt == "13800138000"  # 原文保留，导出「原文」仍可用
    assert match.context_start == 5
    assert match.location == "段落 2"
    assert result.skipped == original.skipped
    assert result.errors == original.errors


def test_results_from_mapping_tolerates_bad_entries() -> None:
    """单条损坏只跳过该条；非映射 / 缺 results 时返回空表。"""
    assert results_from_mapping(None) == {}
    assert results_from_mapping([]) == {}
    assert results_from_mapping({"results": []}) == {}
    restored = results_from_mapping(
        {
            "results": {
                "good": {"roots": ["/r"], "hits": [{"path": "/r/a.txt", "matches": [{"rule_id": 3}]}]},
                "": {"roots": ["/r"]},
                "bad": "not-a-dict",
            }
        }
    )
    assert list(restored) == ["good"]
    result = restored["good"]
    assert result.roots == (Path("/r"),)
    assert result.hits[0].matches[0].rule_id == ""  # 类型不符的字段回落空串
    assert result.stats == ScanStats()


def test_results_roundtrip_empty_table() -> None:
    assert results_from_mapping(results_to_mapping({})) == {}
