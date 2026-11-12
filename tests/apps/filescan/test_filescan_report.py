"""filescan 报告测试：打码、过滤、文本 / JSON / CSV 导出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.filescan._engine import FileHit, RuleMatch, ScanError, ScanResult, ScanStats, SkipRecord
from fcmd.apps.filescan._report import (
    filter_result,
    format_text_report,
    mask_text,
    render_context,
    result_to_csv,
    result_to_json,
    result_to_mapping,
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
            files_failed=0,
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
    assert lines[0].startswith("文件,规则,行号")
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
    assert text.startswith("扫描完成：文件 3")
    assert f"[命中] {Path('scan') / 'a.txt'}（utf-8，100 B）" in text
    assert "第 3 行：tel 13*******78 end" in text
    assert "跳过：" in text and "二进制文件" in text
    assert "错误：" in text and "读取失败" in text
    assert "13812345678" not in text


def test_format_text_report_raw_and_cancelled() -> None:
    text = format_text_report(_result(cancelled=True), include_raw=True)
    assert text.startswith("扫描已取消")
    assert "13812345678" in text


def test_write_report_all_formats(tmp_path: Path) -> None:
    result = _result()
    text_path = write_report(result, tmp_path / "out" / "report.txt", "text")
    assert "扫描完成" in text_path.read_text(encoding="utf-8")

    json_path = write_report(result, tmp_path / "report.json", "json")
    assert json.loads(json_path.read_text(encoding="utf-8"))["hits"]

    csv_path = write_report(result, tmp_path / "report.csv", "csv", include_raw=True)
    assert csv_path.read_text(encoding="utf-8").startswith("\ufeff")
    assert "13812345678" in csv_path.read_text(encoding="utf-8")

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
