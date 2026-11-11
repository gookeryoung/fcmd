"""文本比较纯逻辑测试：并排对齐、增删改统计、截断与二进制拒绝。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.cmder import _diff as diff


class TestDiffLines:
    def test_identical_content(self) -> None:
        result = diff.diff_lines(["a\n", "b\n"], ["a\n", "b\n"])
        assert result.summary == "新增 0 行，删除 0 行，修改 0 行"
        assert [row.kind for row in result.rows] == [diff.EQUAL, diff.EQUAL]
        assert result.rows[0].left_no == 1
        assert result.rows[0].right_no == 1

    def test_modified_line_is_paired(self) -> None:
        result = diff.diff_lines(["old\n"], ["new\n"])
        assert result.changed == 1
        assert result.added == 0
        assert result.removed == 0
        row = result.rows[0]
        assert (row.kind, row.left_text, row.right_text) == (diff.CHANGE, "old", "new")

    def test_insert_and_delete(self) -> None:
        result = diff.diff_lines(["a\n", "b\n", "c\n"], ["a\n", "c\n"])
        assert result.removed == 1
        delete_row = next(row for row in result.rows if row.kind == diff.DELETE)
        assert delete_row.left_text == "b"
        assert delete_row.right_no is None
        assert delete_row.right_text == ""

    def test_added_line_numbers(self) -> None:
        result = diff.diff_lines(["a\n"], ["a\n", "b\n"])
        assert result.added == 1
        add_row = next(row for row in result.rows if row.kind == diff.ADD)
        assert add_row.right_no == 2
        assert add_row.left_no is None

    def test_replace_block_aligns_pairs_then_leftovers(self) -> None:
        result = diff.diff_lines(["a\n", "b\n", "c\n"], ["x\n"])
        kinds = [row.kind for row in result.rows]
        assert kinds == [diff.CHANGE, diff.DELETE, diff.DELETE]
        assert result.changed == 1
        assert result.removed == 2

    def test_crlf_is_normalized(self) -> None:
        result = diff.diff_lines(["a\r\n"], ["a\n"])
        assert result.rows[0].kind == diff.EQUAL

    def test_unique_lines_without_autojunk(self) -> None:
        left = [f"line{index}\n" for index in range(300)]
        right = [f"line{index}\n" for index in range(300)]
        result = diff.diff_lines(left, right)
        assert result.added == result.removed == result.changed == 0
        assert len(result.rows) == 300

    def test_truncation_flags_in_summary(self) -> None:
        result = diff.diff_lines([], [], truncated_left=True, truncated_right=True)
        assert "左侧仅比较" in diff.summary_text(result)
        assert "右侧仅比较" in diff.summary_text(result)


class TestLoadLines:
    def test_loads_text_and_reports_truncation(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("1\n2\n3\n", encoding="utf-8")
        lines, truncated = diff.load_lines(target, max_lines=2)
        assert lines == ["1\n", "2\n"]
        assert truncated is True
        assert diff.load_lines(target)[1] is False

    def test_binary_file_is_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "a.bin"
        target.write_bytes(b"abc\x00def")
        with pytest.raises(ValueError, match="二进制文件无法比较"):
            diff.load_lines(target)

    def test_non_utf8_falls_back_to_replace(self, tmp_path: Path) -> None:
        target = tmp_path / "gbk.txt"
        target.write_bytes("中文".encode("gbk"))
        lines, _ = diff.load_lines(target)
        assert "\ufffd" in lines[0]
