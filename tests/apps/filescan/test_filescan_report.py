"""filescan 报告测试：打码、过滤、文本 / JSON / CSV 导出。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from fcmd.apps.filescan._engine import FileHit, RuleMatch, ScanError, ScanResult, ScanStats, SkipRecord
from fcmd.apps.filescan._report import (
    context_segments,
    filter_result,
    format_text_report,
    mask_text,
    match_note,
    no_hit_hint,
    render_context,
    result_to_csv,
    result_to_json,
    result_to_mapping,
    result_to_sarif,
    scan_summary,
    write_report,
)


def _match(  # noqa: PLR0913 - 测试构造器，参数各自独立
    rule_id: str = "cn_phone",
    *,
    line: int = 3,
    excerpt: str = "13812345678",
    context: str = "tel 13812345678 end",
    context_start: int = 4,
    location: str = "",
    severity: str = "",
) -> RuleMatch:
    return RuleMatch(
        rule_id=rule_id,
        rule_name=f"规则{rule_id}",
        line_no=line,
        start=context_start,
        end=context_start + len(excerpt),
        excerpt=excerpt,
        context=context,
        context_start=context_start,
        location=location,
        severity=severity,
    )


def _result(
    *,
    hits: list[FileHit] | None = None,
    skipped: list[SkipRecord] | None = None,
    errors: list[ScanError] | None = None,
    cancelled: bool = False,
) -> ScanResult:
    return ScanResult(
        roots=(Path("scan"),),
        hits=hits if hits is not None else [FileHit(Path("scan") / "a.txt", 100, "utf-8", [_match()])],
        skipped=skipped or [],
        errors=errors or [],
        stats=ScanStats(
            files_seen=3,
            files_scanned=2,
            files_skipped=1,
            files_failed=len(errors or []),
            bytes_read=2048,
            hit_files=1,
            hit_count=1,
            elapsed=0.5,
            cancelled=cancelled,
        ),
    )


# ---------------------------------------------------------------------- #
# 打码与上下文
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("abc", "***"),
        ("abcde", "***"),
        ("abcdef", "ab**ef"),
        ("13812345678", "13*******78"),
    ],
)
def test_mask_text(text: str, expected: str) -> None:
    assert mask_text(text) == expected


def test_render_context_masks_only_match_span() -> None:
    match = _match()
    assert render_context(match) == "tel 13*******78 end"
    assert render_context(match, include_raw=True) == "tel 13812345678 end"


def test_render_context_handles_short_match() -> None:
    match = _match(excerpt="ab", context="xx ab yy", context_start=3)
    assert render_context(match) == "xx *** yy"


def test_context_segments_split_match_and_keep_mask() -> None:
    match = _match()
    before, excerpt, after = context_segments(match)
    assert (before, excerpt, after) == ("tel ", "13*******78", " end")
    assert f"{before}{excerpt}{after}" == render_context(match)
    assert context_segments(match, include_raw=True) == ("tel ", "13812345678", " end")


def test_context_segments_handle_short_match() -> None:
    match = _match(excerpt="ab", context="xx ab yy", context_start=3)
    assert context_segments(match) == ("xx ", "***", " yy")


def test_match_note_uses_rule_name_and_masked_excerpt() -> None:
    match = _match()
    assert match_note(match) == "【规则：规则cn_phone，匹配项：13*******78】"
    assert match_note(match, include_raw=True) == "【规则：规则cn_phone，匹配项：13812345678】"


# ---------------------------------------------------------------------- #
# 结构化导出
# ---------------------------------------------------------------------- #
def test_result_to_mapping_defaults_to_masked() -> None:
    payload = result_to_mapping(_result())
    match = payload["hits"][0]["matches"][0]
    assert match["excerpt"] == "13*******78"
    assert match["context"] == "tel 13*******78 end"
    assert match["column"] == 5
    assert payload["stats"]["hit_files"] == 1
    assert payload["stats"]["cancelled"] is False


def test_result_to_mapping_raw_switch() -> None:
    payload = result_to_mapping(_result(), include_raw=True)
    match = payload["hits"][0]["matches"][0]
    assert match["excerpt"] == "13812345678"
    assert match["context"] == "tel 13812345678 end"


def test_result_to_json_is_parseable() -> None:
    payload = json.loads(result_to_json(_result()))
    assert payload["roots"] == [str(Path("scan"))]
    assert payload["hits"][0]["path"].endswith("a.txt")
    assert payload["skipped"] == [] and payload["errors"] == []


def test_result_to_csv_has_bom_header_and_rows() -> None:
    text = result_to_csv(_result())
    assert text.startswith("\ufeff")
    lines = text.lstrip("\ufeff").splitlines()
    assert lines[0].startswith("文件,规则,严重级,行号")
    assert "13*******78" in lines[1]
    raw = result_to_csv(_result(), include_raw=True)
    assert "13812345678" in raw
    assert "13*******78" not in raw


def test_expression_only_hit_exports_without_rows() -> None:
    result = _result(hits=[FileHit(Path("scan") / "clean.txt", 10, "utf-8", [])])
    assert len(result_to_csv(result).strip().splitlines()) == 1
    assert "表达式命中" in format_text_report(result)


# ---------------------------------------------------------------------- #
# 过滤
# ---------------------------------------------------------------------- #
def test_filter_result_by_rule_and_path() -> None:
    hits = [
        FileHit(Path("scan") / "a.txt", 10, "utf-8", [_match("a"), _match("b")]),
        FileHit(Path("scan") / "b.md", 10, "utf-8", [_match("a")]),
    ]
    result = _result(hits=hits)
    by_rule = filter_result(result, rule_ids=["b"])
    assert [hit.path.name for hit in by_rule.hits] == ["a.txt"]
    assert by_rule.stats.hit_files == 1 and by_rule.stats.hit_count == 1

    by_path = filter_result(result, path_contains=".MD")
    assert [hit.path.name for hit in by_path.hits] == ["b.md"]
    assert by_path.stats.hit_count == 1

    none = filter_result(result, rule_ids=["missing"])
    assert none.hits == []
    assert none.stats.hit_files == 0


def test_severity_appears_in_json_csv_text_and_filters() -> None:
    hit = FileHit(Path("scan") / "a.txt", 10, "utf-8", [_match("a", severity="high")])
    result = _result(hits=[hit])
    payload = result_to_mapping(result)
    assert payload["hits"][0]["matches"][0]["severity"] == "high"
    assert "高" in result_to_csv(result).splitlines()[1]
    assert "【高】" in format_text_report(result)
    assert filter_result(result, severities=["high"]).stats.hit_count == 1
    assert filter_result(result, severities=["low"]).hits == []


def test_filter_result_keeps_expression_only_hit_by_path() -> None:
    result = _result(hits=[FileHit(Path("scan") / "clean.txt", 10, "utf-8", [])])
    filtered = filter_result(result, path_contains="clean")
    assert len(filtered.hits) == 1


# ---------------------------------------------------------------------- #
# 文本报告与写文件
# ---------------------------------------------------------------------- #
def test_format_text_report_sections() -> None:
    result = _result(
        skipped=[SkipRecord(Path("scan") / "x.bin", "二进制文件")],
        errors=[ScanError(Path("scan") / "y", "读取失败")],
    )
    text = format_text_report(result)
    # 有读取错误时整体状态是「部分成功」，而不是笼统的「完成」。
    assert text.startswith("扫描部分成功（有错误）：文件 3")
    assert "通过 " in text
    assert f"[命中] {Path('scan') / 'a.txt'}（utf-8，100 B，风险 " in text
    assert "引擎 —，耗时 0.0 ms" in text
    assert "第 3 行：tel 13*******78 end" in text
    assert "跳过：" in text and "二进制文件" in text
    assert "错误：" in text and "读取失败" in text
    assert "13812345678" not in text


def test_format_text_report_raw_and_cancelled() -> None:
    text = format_text_report(_result(cancelled=True), include_raw=True)
    assert text.startswith("扫描已取消")
    assert "13812345678" in text


def test_scan_summary_reports_passed_and_engine() -> None:
    stats = ScanStats(files_seen=9, files_scanned=4, files_reused=2, hit_files=1, hit_count=2, elapsed=0.5)
    summary = scan_summary(stats, compact=True)
    assert "通过 5" in summary
    assert "命中 1 个文件 / 2 条" in summary

    hit = FileHit(Path("scan") / "a.txt", 10, "utf-8", [_match()], engine="cache", elapsed=0.0)
    text = format_text_report(_result(hits=[hit]))
    assert "引擎 缓存" in text

    stats.files_failed = 2
    assert scan_summary(stats, compact=True).startswith("扫描部分成功（有错误）")


def test_text_report_filename_match_and_missing_encoding() -> None:
    match = _match(line=0, excerpt="secret", context="secret.txt", context_start=0, location="文件名")
    hit = FileHit(Path("scan") / "secret.txt", 12, "", [match])
    text = format_text_report(_result(hits=[hit]))
    assert "（12 B" in text
    assert "@ 文件名：se**et.txt" in text
    assert "第 0 行" not in text


# ---------------------------------------------------------------------- #
# SARIF 2.1.0
# ---------------------------------------------------------------------- #
def test_sarif_structure_and_region() -> None:
    match = _match(severity="high")
    result = _result(hits=[FileHit(Path("scan") / "a.txt", 100, "utf-8", [match])])
    payload = json.loads(result_to_sarif(result))

    assert payload["version"] == "2.1.0"
    assert payload["$schema"].endswith("sarif-2.1.0.json")
    run = payload["runs"][0]
    assert run["tool"]["driver"]["name"] == "filescan"
    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == ["cn_phone"]
    assert run["tool"]["driver"]["rules"][0]["defaultConfiguration"]["level"] == "error"
    assert run["invocations"][0]["executionSuccessful"] is True

    entry = run["results"][0]
    assert entry["ruleId"] == "cn_phone"
    assert entry["ruleIndex"] == 0
    assert entry["level"] == "error"
    region = entry["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] == 3
    assert region["startColumn"] == 5
    assert region["endColumn"] == 5 + len("13812345678")
    assert region["snippet"]["text"] == "13*******78"
    assert entry["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "scan/a.txt"


def test_sarif_level_mapping_and_raw_snippet() -> None:
    levels = {}
    for severity in ("info", "low", "medium", "high", "critical", ""):
        match = _match(severity=severity)
        result = _result(hits=[FileHit(Path("scan") / "a.txt", 1, "", [match])])
        payload = json.loads(result_to_sarif(result, include_raw=True))
        levels[severity] = payload["runs"][0]["results"][0]["level"]
        assert payload["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]["snippet"]["text"] == (
            "13812345678"
        )
    assert levels == {
        "info": "note",
        "low": "note",
        "medium": "warning",
        "high": "error",
        "critical": "error",
        "": "warning",
    }


def test_sarif_expression_hit_and_errors() -> None:
    result = _result(
        hits=[FileHit(Path("scan") / "b.txt", 20, "", [])],
        errors=[ScanError(Path("scan") / "c.txt", "权限不足")],
        cancelled=True,
    )
    payload = json.loads(result_to_sarif(result))
    run = payload["runs"][0]
    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == ["expression"]
    assert run["results"][0]["ruleId"] == "expression"
    assert run["invocations"][0]["executionSuccessful"] is False
    assert "权限不足" in run["invocations"][0]["toolExecutionNotifications"][0]["message"]["text"]
    assert run["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 1


def test_write_report_all_formats(tmp_path: Path) -> None:
    result = _result()
    text_path = write_report(result, tmp_path / "out" / "report.txt", "text")
    assert "扫描完成" in text_path.read_text(encoding="utf-8")

    json_path = write_report(result, tmp_path / "report.json", "json")
    assert json.loads(json_path.read_text(encoding="utf-8"))["hits"]

    csv_path = write_report(result, tmp_path / "report.csv", "csv", include_raw=True)
    assert csv_path.read_text(encoding="utf-8").startswith("\ufeff")
    assert "13812345678" in csv_path.read_text(encoding="utf-8")

    sarif_path = write_report(result, tmp_path / "report.sarif", "sarif")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["version"] == "2.1.0"

    with pytest.raises(ValueError, match="未知报告格式"):
        write_report(result, tmp_path / "report.bin", "binary")


# ---------------------------------------------------------------------- #
# 文档位置与新增统计字段（向后兼容）
# ---------------------------------------------------------------------- #
def test_plain_text_location_stays_empty() -> None:
    mapping = result_to_mapping(_result(), include_raw=True)
    assert mapping["hits"][0]["matches"][0]["location"] == ""


def test_document_location_in_json_csv_and_text() -> None:
    hit = FileHit(Path("scan") / "a.docx", 2048, "docx", [_match(location="段落 3")])
    result = _result(hits=[hit])

    mapping = result_to_mapping(result, include_raw=True)
    assert mapping["hits"][0]["matches"][0]["location"] == "段落 3"

    csv_text = result_to_csv(result, include_raw=True)
    lines = csv_text.splitlines()
    assert "位置" in lines[0]
    assert "段落 3" in lines[1]

    text = format_text_report(result, include_raw=True)
    assert "段落 3 第 3 行" in text


def test_document_skip_stats_are_separate_fields() -> None:
    result = _result()
    result.stats.files_skipped_encrypted = 1
    result.stats.files_skipped_corrupt = 2
    result.stats.files_skipped_no_text = 3
    result.stats.files_skipped_unsupported = 4

    stats = result_to_mapping(result)["stats"]
    assert stats["files_skipped_encrypted"] == 1
    assert stats["files_skipped_corrupt"] == 2
    assert stats["files_skipped_no_text"] == 3
    assert stats["files_skipped_unsupported"] == 4
    # 既有字段语义不变
    assert stats["files_skipped"] == 1
    assert stats["files_skipped_other"] == 0

    text = format_text_report(result)
    assert "加密 1" in text and "损坏 2" in text and "无文本 3" in text and "不支持 4" in text


# ---------------------------------------------------------------------- #
# 范围外 / 读取后跳过 的聚合口径
# ---------------------------------------------------------------------- #
def _scope_stats() -> ScanStats:
    """复现「扫 D:/devspace 全是跳过」的真实统计形态：白名单排除绝大多数文件。"""
    return ScanStats(
        files_seen=4909,
        files_scanned=24,
        files_skipped=4885,
        files_skipped_extension=4881,
        files_skipped_size_time=0,
        files_skipped_no_text=4,
        hit_files=0,
        hit_count=0,
        elapsed=1.5,
    )


def test_scope_split_properties() -> None:
    stats = _scope_stats()
    assert stats.files_out_of_scope == 4881
    assert stats.files_skipped_after_read == 4


def test_scan_summary_separates_out_of_scope_from_read_skips() -> None:
    summary = scan_summary(_scope_stats())
    assert "范围外 4881（后缀白名单 4881 / 大小或时间 0）" in summary
    assert "读取后跳过 4（二进制 0 / 其他 0 / 无文本 4）" in summary
    # 不再把所有范围外文件笼统称作「跳过」
    assert "跳过 4885" not in summary
    assert "跳过 4881" not in summary


def test_scan_summary_compact_is_single_line() -> None:
    compact = scan_summary(_scope_stats(), compact=True)
    assert "\n" not in compact
    assert compact.startswith("扫描完成：文件 4909")


def test_no_hit_hint_explains_scope_and_textless_documents() -> None:
    hint = no_hit_hint(_scope_stats())
    assert hint is not None
    assert "未发现命中" in hint
    assert "已检查 24 个文件（实扫 24 / 缓存复用 0）" in hint
    assert "4881 个文件不在扫描范围" in hint
    assert "其中文档无文本层 4 个" in hint
    assert "后缀白名单" in hint and "规则启用状态" in hint

    assert no_hit_hint(replace(_scope_stats(), hit_files=1)) is None
    assert no_hit_hint(replace(_scope_stats(), cancelled=True)) is None


def test_text_report_shows_hint_only_when_no_hits() -> None:
    with_hits = _result()
    assert "未发现命中" not in format_text_report(with_hits)

    empty = replace(
        _result(hits=[]),
        stats=replace(
            _result(hits=[]).stats,
            hit_files=0,
            hit_count=0,
            files_skipped_extension=7,
            files_skipped=7,
            files_seen=9,
        ),
    )
    text = format_text_report(empty)
    assert "未发现命中" in text
    assert "范围外 7（后缀白名单 7 / 大小或时间 0）" in text


def test_json_stats_include_scope_split() -> None:
    stats = result_to_mapping(replace(_result(hits=[]), stats=_scope_stats()))["stats"]
    assert stats["files_skipped_size_time"] == 0
    assert stats["files_out_of_scope"] == 4881
    assert stats["files_skipped_after_read"] == 4


def test_time_match_is_not_masked_in_reports() -> None:
    from fcmd.apps.filescan._engine import RuleMatch

    timestamp = "2026-01-01 00:00:00"
    match = RuleMatch(
        rule_id="old",
        rule_name="旧文件",
        line_no=0,
        start=0,
        end=0,
        excerpt=timestamp,
        context=timestamp,
        context_start=0,
        location="修改时间",
        sensitive=False,
    )
    assert context_segments(match) == ("", timestamp, "")
    assert match_note(match) == f"【规则：旧文件，匹配项：{timestamp}】"
    result = ScanResult(
        roots=(Path("scan"),),
        stats=ScanStats(hit_files=1, hit_count=1),
        hits=[FileHit(Path("scan") / "old.txt", 10, "", [match])],
    )
    payload = result_to_mapping(result, include_raw=False)["hits"][0]["matches"][0]
    assert payload["excerpt"] == timestamp
    assert payload["context"] == timestamp
