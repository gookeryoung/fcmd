"""pdfsplit 工具测试。

验证 ``fcmd.tools.pdf.pdfsplit``：
- 工具注册
- 页码范围解析（含开区间、越界、非法输入）
- 按份数 / 每份页数 / 页码范围三种分割
- CLI 输出与错误分支
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.pdf import pdfsplit
from fcmd.tools.pdf.pdfsplit import (
    parse_page_ranges,
    split_by_parts,
    split_by_range,
    split_by_size,
)

PAGE_COUNT = 5


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """生成 5 页、每页带文本的测试 PDF。"""
    import fitz

    doc = fitz.open()
    for number in range(1, PAGE_COUNT + 1):
        page = doc.new_page(width=200, height=300)
        page.insert_text((20, 50), f"Page {number}", fontsize=12)
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _page_count(path: Path) -> int:
    """用 pypdf 读取页数。"""
    import pypdf

    return len(pypdf.PdfReader(str(path)).pages)


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestRegistration:
    """工具注册。"""

    def test_single_command(self) -> None:
        assert set(_TOOL_REGISTRY["pdfsplit"]) == {None}


# ============================================================================ #
# 页码范围解析
# ============================================================================ #
class TestParsePageRanges:
    """页码范围解析。"""

    def test_single_and_ranges(self) -> None:
        assert parse_page_ranges("1,3-5,7-", 10) == [1, 3, 4, 5, 7, 8, 9, 10]

    def test_open_start(self) -> None:
        assert parse_page_ranges("-3", 5) == [1, 2, 3]

    def test_open_end(self) -> None:
        assert parse_page_ranges("2-", 5) == [2, 3, 4, 5]

    def test_dedupe_and_out_of_range(self) -> None:
        assert parse_page_ranges("2,2,9,4", 5) == [2, 4]

    def test_whitespace_and_empty_tokens(self) -> None:
        assert parse_page_ranges(" 1 , , 3 ", 5) == [1, 3]

    def test_empty_expression(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            parse_page_ranges(" , ", 5)

    def test_no_pages(self) -> None:
        with pytest.raises(ValueError, match="没有可分割的页面"):
            parse_page_ranges("1", 0)

    def test_invalid_number(self) -> None:
        with pytest.raises(ValueError, match="无效的页码表达式"):
            parse_page_ranges("abc", 5)

    def test_invalid_range(self) -> None:
        with pytest.raises(ValueError, match="无效的页码表达式"):
            parse_page_ranges("1-2-3", 5)

    def test_reversed_range(self) -> None:
        with pytest.raises(ValueError, match="起点大于终点"):
            parse_page_ranges("5-1", 5)

    def test_non_positive(self) -> None:
        with pytest.raises(ValueError, match="页码必须为正整数"):
            parse_page_ranges("0", 5)


# ============================================================================ #
# 分割
# ============================================================================ #
class TestSplitByParts:
    """按份数分割。"""

    def test_even_split(self, sample_pdf: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        messages: list[tuple[int, int, str]] = []
        outputs = split_by_parts(
            sample_pdf,
            out,
            2,
            progress=lambda done, total, message: messages.append((done, total, message)),
        )
        assert [path.name for path in outputs] == ["sample_part1.pdf", "sample_part2.pdf"]
        assert [_page_count(path) for path in outputs] == [3, 2]
        assert messages[0][:2] == (1, 2)

    def test_custom_base_name(self, sample_pdf: Path, tmp_path: Path) -> None:
        outputs = split_by_parts(sample_pdf, tmp_path, 3, base_name="report")
        assert [path.name for path in outputs] == [
            "report_part1.pdf",
            "report_part2.pdf",
            "report_part3.pdf",
        ]
        assert [_page_count(path) for path in outputs] == [2, 2, 1]

    def test_too_many_parts(self, sample_pdf: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="不能超过总页数"):
            split_by_parts(sample_pdf, tmp_path, PAGE_COUNT + 1)

    def test_invalid_parts(self, sample_pdf: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="分割份数必须为正整数"):
            split_by_parts(sample_pdf, tmp_path, 0)

    def test_missing_dependency(self, sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdfsplit, "HAS_PYPDF", False)
        monkeypatch.setattr(pdfsplit, "pypdf", None)
        with pytest.raises(RuntimeError, match="pypdf"):
            split_by_parts(sample_pdf, tmp_path, 2)


class TestSplitBySize:
    """按每份页数分割。"""

    def test_chunks(self, sample_pdf: Path, tmp_path: Path) -> None:
        messages: list[tuple[int, int, str]] = []
        outputs = split_by_size(
            sample_pdf,
            tmp_path,
            2,
            progress=lambda done, total, message: messages.append((done, total, message)),
        )
        assert [_page_count(path) for path in outputs] == [2, 2, 1]
        assert [path.name for path in outputs] == [
            "sample_part1.pdf",
            "sample_part2.pdf",
            "sample_part3.pdf",
        ]
        assert messages[-1][:2] == (3, 3)

    def test_larger_than_document(self, sample_pdf: Path, tmp_path: Path) -> None:
        outputs = split_by_size(sample_pdf, tmp_path, 99)
        assert len(outputs) == 1
        assert _page_count(outputs[0]) == PAGE_COUNT

    def test_invalid_size(self, sample_pdf: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="每份页数必须为正整数"):
            split_by_size(sample_pdf, tmp_path, 0)

    def test_missing_dependency(self, sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdfsplit, "HAS_PYPDF", False)
        monkeypatch.setattr(pdfsplit, "pypdf", None)
        with pytest.raises(RuntimeError, match="pypdf"):
            split_by_size(sample_pdf, tmp_path, 2)


class TestSplitByRange:
    """页码范围提取。"""

    def test_extract_pages(self, sample_pdf: Path, tmp_path: Path) -> None:
        target = tmp_path / "out" / "picked.pdf"
        messages: list[tuple[int, int, str]] = []
        output = split_by_range(
            sample_pdf,
            target,
            "1,3-4",
            progress=lambda done, total, message: messages.append((done, total, message)),
        )
        assert output == target
        assert _page_count(target) == 3
        assert messages[0][0] == 0
        assert messages[-1][0] == 1

    def test_no_matching_pages(self, sample_pdf: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="未命中有效页面"):
            split_by_range(sample_pdf, tmp_path / "empty.pdf", "99")

    def test_missing_dependency(self, sample_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pdfsplit, "HAS_PYPDF", False)
        monkeypatch.setattr(pdfsplit, "pypdf", None)
        with pytest.raises(RuntimeError, match="pypdf"):
            split_by_range(sample_pdf, tmp_path / "x.pdf", "1")


# ============================================================================ #
# CLI
# ============================================================================ #
class TestCli:
    """CLI 入口。"""

    def test_parts(self, sample_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "out"
        code = run_tool("pdfsplit", [str(sample_pdf), "--parts", "2", "--output-dir", str(out)])
        assert code == 0
        assert sorted(path.name for path in out.iterdir()) == ["sample_part1.pdf", "sample_part2.pdf"]
        assert "分割完成" in capsys.readouterr().out

    def test_parts_with_output_path(self, sample_pdf: Path, tmp_path: Path) -> None:
        out = tmp_path / "nested"
        code = run_tool("pdfsplit", [str(sample_pdf), "--parts", "2", "--output-path", str(out / "base.pdf")])
        assert code == 0
        assert sorted(path.name for path in out.iterdir()) == ["base_part1.pdf", "base_part2.pdf"]

    def test_size(self, sample_pdf: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        code = run_tool("pdfsplit", [str(sample_pdf), "--size", "2", "--output-dir", str(out)])
        assert code == 0
        assert len(list(out.iterdir())) == 3

    def test_range_explicit_output(self, sample_pdf: Path, tmp_path: Path) -> None:
        target = tmp_path / "picked.pdf"
        code = run_tool("pdfsplit", [str(sample_pdf), "--pages", "1,3", "--output-path", str(target)])
        assert code == 0
        assert _page_count(target) == 2

    def test_range_default_output(self, sample_pdf: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        code = run_tool("pdfsplit", [str(sample_pdf), "--pages", "2-", "--output-dir", str(out)])
        assert code == 0
        assert _page_count(out / "sample_extracted.pdf") == PAGE_COUNT - 1

    def test_missing_input(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfsplit", [str(tmp_path / "missing.pdf"), "--parts", "2"])
        assert code == 0
        assert "输入文件不存在" in capsys.readouterr().out

    def test_no_mode(self, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfsplit", [str(sample_pdf)])
        assert code == 0
        assert "请且仅指定" in capsys.readouterr().out

    def test_multiple_modes(self, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfsplit", [str(sample_pdf), "--parts", "2", "--pages", "1"])
        assert code == 0
        assert "请且仅指定" in capsys.readouterr().out

    def test_invalid_range(self, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfsplit", [str(sample_pdf), "--pages", "abc"])
        assert code == 0
        assert "错误" in capsys.readouterr().out

    def test_too_many_parts(self, sample_pdf: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("pdfsplit", [str(sample_pdf), "--parts", "99"])
        assert code == 0
        assert "不能超过总页数" in capsys.readouterr().out

    def test_missing_dependency(
        self, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(pdfsplit, "HAS_PYPDF", False)
        monkeypatch.setattr(pdfsplit, "pypdf", None)
        code = run_tool("pdfsplit", [str(sample_pdf), "--parts", "2"])
        assert code == 0
        assert "未安装 pypdf" in capsys.readouterr().out
