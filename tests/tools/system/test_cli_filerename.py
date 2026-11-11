"""filerename 工具测试。

验证 ``fcmd filerename``：
- 工具注册（replace/insert/case 三子命令）
- 三模式执行、--preview、冲突跳过、多文件批量
- 无效正则/无效模式 → 任务失败汇总 + 退出码 1
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool


class TestRegistration:
    """filerename 注册为多子命令工具。"""

    def test_registered(self) -> None:
        """filerename 已注册且含 replace/insert/case 子命令。"""
        assert "filerename" in list_tools()
        assert set(list_subcommands("filerename")) == {"replace", "insert", "case"}


class TestFilerenameReplaceRun:
    """``fcmd filerename replace`` 执行语义。"""

    def test_replace_success(self, tmp_path: Path) -> None:
        """正则替换主干，保留扩展名。"""
        f = tmp_path / "my old file.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), r"\s+", "--replacement", "_"]) == 0
        assert (tmp_path / "my_old_file.txt").exists()
        assert not f.exists()

    def test_replace_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--preview 仅预览不执行。"""
        f = tmp_path / "old.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), "old", "--replacement", "new", "--preview"]) == 0
        assert "[预览]" in capsys.readouterr().out
        assert f.exists()

    def test_replace_no_match_keeps_file(self, tmp_path: Path) -> None:
        """主干不匹配时不执行重命名。"""
        f = tmp_path / "plain.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), r"\d+", "--replacement", "x"]) == 0
        assert f.exists()

    def test_replace_backreference(self, tmp_path: Path) -> None:
        """替换字符串支持反向引用。"""
        f = tmp_path / "2026_report.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), r"(\d+)_(\w+)", "--replacement", r"\2_\1"]) == 0
        assert (tmp_path / "report_2026.txt").exists()

    def test_replace_delete_match(self, tmp_path: Path) -> None:
        """replacement 缺省为空串即删除匹配部分。"""
        f = tmp_path / "data_draft.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), "_draft"]) == 0
        assert (tmp_path / "data.txt").exists()

    def test_replace_skip_existing_target(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """目标已存在时跳过并提示（不覆盖）。"""
        f1 = tmp_path / "a old.txt"
        f2 = tmp_path / "a_old.txt"
        f1.write_text("1", encoding="utf-8")
        f2.write_text("2", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f1), r"\s+", "--replacement", "_"]) == 0
        assert f1.exists()
        assert f2.read_text(encoding="utf-8") == "2"
        assert "跳过" in capsys.readouterr().out

    def test_replace_missing_file_prints_hint(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在时提示后继续，退出码 0。"""
        assert run_tool("filerename", ["replace", str(tmp_path / "missing.txt"), "x"]) == 0
        assert "文件不存在" in capsys.readouterr().out

    def test_replace_multiple_files(self, tmp_path: Path) -> None:
        """多文件批量替换。"""
        f1 = tmp_path / "a b.txt"
        f2 = tmp_path / "c d.txt"
        f1.write_text("x", encoding="utf-8")
        f2.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f1), str(f2), r"\s+", "--replacement", "_"]) == 0
        assert (tmp_path / "a_b.txt").exists()
        assert (tmp_path / "c_d.txt").exists()

    def test_replace_invalid_regex_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效正则 → 任务失败汇总 + 退出码 1。"""
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["replace", str(f), "[invalid"]) == 1
        assert "失败" in capsys.readouterr().out
        assert f.exists()


class TestFilerenameInsertRun:
    """``fcmd filerename insert`` 执行语义。"""

    def test_insert_at_start(self, tmp_path: Path) -> None:
        """开头插入前缀。"""
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), "NEW_", "--position", "0"]) == 0
        assert (tmp_path / "NEW_file.txt").exists()

    def test_insert_in_middle(self, tmp_path: Path) -> None:
        """中间位置插入。"""
        f = tmp_path / "abcd.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), "_v2", "--position", "2"]) == 0
        assert (tmp_path / "ab_v2cd.txt").exists()

    def test_insert_position_clamped(self, tmp_path: Path) -> None:
        """位置超出范围自动截断到边界。"""
        f = tmp_path / "abc.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), "_end", "--position", "100"]) == 0
        assert (tmp_path / "abc_end.txt").exists()

    def test_insert_empty_text_noop(self, tmp_path: Path) -> None:
        """空文本不执行重命名。"""
        f = tmp_path / "keep.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), ""]) == 0
        assert f.exists()

    def test_insert_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--preview 仅预览不执行。"""
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["insert", str(f), "P_", "--preview"]) == 0
        assert "[预览]" in capsys.readouterr().out
        assert f.exists()


class TestFilerenameCaseRun:
    """``fcmd filerename case`` 执行语义。"""

    def test_case_lower(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """转小写（默认模式），扩展名保留。"""
        f = tmp_path / "File.TXT"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f)]) == 0
        assert "重命名: File.TXT -> file.TXT" in capsys.readouterr().out
        assert (tmp_path / "file.TXT").exists()

    def test_case_upper(self, tmp_path: Path) -> None:
        """--mode upper 转大写。"""
        f = tmp_path / "abc.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "upper"]) == 0
        assert (tmp_path / "ABC.txt").exists()

    def test_case_title(self, tmp_path: Path) -> None:
        """--mode title 转标题大小写。"""
        f = tmp_path / "my report.pdf"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "title"]) == 0
        assert (tmp_path / "My Report.pdf").exists()

    def test_case_no_change_needed(self, tmp_path: Path) -> None:
        """已是目标大小写时静默跳过。"""
        f = tmp_path / "abc.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "lower"]) == 0
        assert f.exists()

    def test_case_invalid_mode_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效模式 → 任务失败汇总 + 退出码 1。"""
        f = tmp_path / "abc.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "snake"]) == 1
        assert "失败" in capsys.readouterr().out

    def test_case_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--preview 仅预览不执行。"""
        f = tmp_path / "ABC.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("filerename", ["case", str(f), "--mode", "lower", "--preview"]) == 0
        assert "[预览]" in capsys.readouterr().out
        assert f.exists()
