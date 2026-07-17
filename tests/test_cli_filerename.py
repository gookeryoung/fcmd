"""filerename 工具测试。

验证 ``fcmd.cli.filerename`` 模块：
- 工具注册与三子命令结构（replace/insert/case）
- ``_safe_rename`` 安全重命名（同名跳过/目标已存在/预览模式）
- ``replace_pattern`` 正则替换（匹配/不匹配/反向引用）
- ``insert_text`` 位置插入（开头/中间/末尾/空文本/越界）
- ``change_case`` 大小写转换（lower/upper/title/无效模式）
- CLI 子命令端到端（含 ``--preview``）
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.filerename import (
    _safe_rename,
    change_case,
    insert_text,
    replace_pattern,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """filerename 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "filerename" in list_tools()

    def test_subcommands(self) -> None:
        """filerename 有 replace/insert/case 三个子命令。"""
        subs = list_subcommands("filerename")
        assert set(subs) == {"replace", "insert", "case"}


# ============================================================================ #
# _safe_rename
# ============================================================================ #
class TestSafeRename:
    """_safe_rename 安全重命名测试。"""

    def test_rename_success(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """成功重命名文件。"""
        src = tmp_path / "old.txt"
        src.write_text("content")

        result = _safe_rename(src, "new", preview=False)
        assert result is True
        assert not src.exists()
        assert (tmp_path / "new.txt").exists()
        captured = capsys.readouterr()
        assert "重命名" in captured.out

    def test_skip_same_name(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """新主干与原主干相同时跳过。"""
        src = tmp_path / "same.txt"
        src.write_text("content")

        result = _safe_rename(src, "same", preview=False)
        assert result is False
        assert src.exists()
        capsys.readouterr()  # 消费输出

    def test_skip_existing_target(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """目标文件已存在时跳过。"""
        src = tmp_path / "a.txt"
        src.write_text("a")
        (tmp_path / "b.txt").write_text("existing")

        result = _safe_rename(src, "b", preview=False)
        assert result is False
        assert src.exists()
        captured = capsys.readouterr()
        assert "跳过" in captured.out

    def test_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """预览模式不实际重命名。"""
        src = tmp_path / "old.txt"
        src.write_text("content")

        result = _safe_rename(src, "new", preview=True)
        assert result is True
        assert src.exists()  # 原文件仍在
        assert not (tmp_path / "new.txt").exists()
        captured = capsys.readouterr()
        assert "预览" in captured.out


# ============================================================================ #
# replace_pattern
# ============================================================================ #
class TestReplacePattern:
    """replace_pattern 正则替换测试。"""

    def test_replace_match(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """匹配并替换文件名主干。"""
        src = tmp_path / "hello world.txt"
        src.write_text("content")
        pattern = re.compile(r"\s+")

        result = replace_pattern(src, pattern, "_", preview=False)
        assert result is True
        assert (tmp_path / "hello_world.txt").exists()
        capsys.readouterr()

    def test_no_match(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """不匹配时跳过。"""
        src = tmp_path / "nospaces.txt"
        src.write_text("content")
        pattern = re.compile(r"\s+")

        result = replace_pattern(src, pattern, "_", preview=False)
        assert result is False
        assert src.exists()
        capsys.readouterr()

    def test_backreference(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """支持反向引用。"""
        src = tmp_path / "2024_report.txt"
        src.write_text("content")
        pattern = re.compile(r"(\d{4})_")

        result = replace_pattern(src, pattern, r"\1-", preview=False)
        assert result is True
        assert (tmp_path / "2024-report.txt").exists()
        capsys.readouterr()

    def test_delete_match(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """空替换字符串删除匹配部分。"""
        src = tmp_path / "file_copy.txt"
        src.write_text("content")
        pattern = re.compile(r"_copy")

        result = replace_pattern(src, pattern, "", preview=False)
        assert result is True
        assert (tmp_path / "file.txt").exists()
        capsys.readouterr()

    def test_preserves_extension(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """替换仅作用于文件名主干，保留扩展名。"""
        src = tmp_path / "test.tar.gz"
        src.write_text("content")
        pattern = re.compile(r"test")

        result = replace_pattern(src, pattern, "data", preview=False)
        assert result is True
        assert (tmp_path / "data.tar.gz").exists()
        capsys.readouterr()


# ============================================================================ #
# insert_text
# ============================================================================ #
class TestInsertText:
    """insert_text 位置插入测试。"""

    def test_insert_at_start(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """在开头插入文本。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        result = insert_text(src, "PRE_", 0, preview=False)
        assert result is True
        assert (tmp_path / "PRE_file.txt").exists()
        capsys.readouterr()

    def test_insert_at_middle(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """在中间插入文本。"""
        src = tmp_path / "report.txt"
        src.write_text("content")

        result = insert_text(src, "_v2", 3, preview=False)
        assert result is True
        assert (tmp_path / "rep_v2ort.txt").exists()
        capsys.readouterr()

    def test_insert_at_end(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """在末尾插入文本（position 超出长度时截断到末尾）。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        result = insert_text(src, "_end", 100, preview=False)
        assert result is True
        assert (tmp_path / "file_end.txt").exists()
        capsys.readouterr()

    def test_empty_text(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """空文本时跳过。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        result = insert_text(src, "", 0, preview=False)
        assert result is False
        assert src.exists()
        capsys.readouterr()

    def test_preserves_extension(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """插入仅作用于文件名主干，保留扩展名。"""
        src = tmp_path / "data.csv"
        src.write_text("content")

        result = insert_text(src, "new_", 0, preview=False)
        assert result is True
        assert (tmp_path / "new_data.csv").exists()
        capsys.readouterr()

    def test_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """预览模式不实际执行。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        result = insert_text(src, "PRE_", 0, preview=True)
        assert result is True
        assert src.exists()
        assert not (tmp_path / "PRE_file.txt").exists()
        capsys.readouterr()


# ============================================================================ #
# change_case
# ============================================================================ #
class TestChangeCase:
    """change_case 大小写转换测试。"""

    def test_to_lower(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """转小写。"""
        src = tmp_path / "MyFile.TXT"
        src.write_text("content")

        result = change_case(src, "lower", preview=False)
        assert result is True
        assert (tmp_path / "myfile.TXT").exists()
        capsys.readouterr()

    def test_to_upper(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """转大写。"""
        src = tmp_path / "myfile.txt"
        src.write_text("content")

        result = change_case(src, "upper", preview=False)
        assert result is True
        assert (tmp_path / "MYFILE.txt").exists()
        capsys.readouterr()

    def test_to_title(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """转标题大小写。"""
        src = tmp_path / "hello world.txt"
        src.write_text("content")

        result = change_case(src, "title", preview=False)
        assert result is True
        assert (tmp_path / "Hello World.txt").exists()
        capsys.readouterr()

    def test_invalid_mode(self, tmp_path: Path) -> None:
        """无效模式抛 ValueError。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        with pytest.raises(ValueError, match="不支持的大小写模式"):
            change_case(src, "snake", preview=False)

    def test_no_change_needed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """已经是目标大小写时跳过。"""
        src = tmp_path / "lower.txt"
        src.write_text("content")

        result = change_case(src, "lower", preview=False)
        assert result is False
        assert src.exists()
        capsys.readouterr()

    def test_preview(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """预览模式不实际执行。"""
        src = tmp_path / "MixedCase.txt"
        src.write_text("content")

        result = change_case(src, "lower", preview=True)
        assert result is True
        assert src.exists()  # 原文件仍在（未重命名）
        captured = capsys.readouterr()
        assert "预览" in captured.out


# ============================================================================ #
# CLI 子命令端到端
# ============================================================================ #
class TestCLISubcommands:
    """CLI 子命令端到端测试（通过 run_tool 调用）。"""

    def test_run_replace_success(self, tmp_path: Path) -> None:
        """run_tool 调用 replace 子命令成功。"""
        src = tmp_path / "hello world.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["replace", str(src), r"\s+", "--replacement", "_"],
        )
        assert code == 0
        assert (tmp_path / "hello_world.txt").exists()

    def test_run_replace_preview(self, tmp_path: Path) -> None:
        """replace --preview 不实际执行。"""
        src = tmp_path / "old.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["replace", str(src), "old", "--replacement", "new", "--preview"],
        )
        assert code == 0
        assert src.exists()
        assert not (tmp_path / "new.txt").exists()

    def test_run_replace_invalid_regex(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """replace 无效正则表达式时提示并返回。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["replace", str(src), "[invalid", "--replacement", "x"],
        )
        assert code == 0  # 工具内部处理，不返回错误码
        captured = capsys.readouterr()
        assert "无效的正则表达式" in captured.out

    def test_run_replace_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """replace 文件不存在时提示。"""
        code = run_tool(
            "filerename",
            ["replace", str(tmp_path / "noexist.txt"), "x"],
        )
        assert code == 0
        captured = capsys.readouterr()
        assert "文件不存在" in captured.out

    def test_run_insert_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """insert 文件不存在时提示。"""
        code = run_tool(
            "filerename",
            ["insert", str(tmp_path / "noexist.txt"), "PRE_"],
        )
        assert code == 0
        captured = capsys.readouterr()
        assert "文件不存在" in captured.out

    def test_run_case_nonexistent_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """case 文件不存在时提示。"""
        code = run_tool(
            "filerename",
            ["case", str(tmp_path / "noexist.txt"), "--mode", "lower"],
        )
        assert code == 0
        captured = capsys.readouterr()
        assert "文件不存在" in captured.out

    def test_run_insert_success(self, tmp_path: Path) -> None:
        """run_tool 调用 insert 子命令成功。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["insert", str(src), "PRE_", "--position", "0"],
        )
        assert code == 0
        assert (tmp_path / "PRE_file.txt").exists()

    def test_run_insert_default_position(self, tmp_path: Path) -> None:
        """insert 默认 position=0（开头插入）。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["insert", str(src), "PRE_"],
        )
        assert code == 0
        assert (tmp_path / "PRE_file.txt").exists()

    def test_run_case_lower(self, tmp_path: Path) -> None:
        """run_tool 调用 case --mode lower 成功。"""
        src = tmp_path / "MyFile.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["case", str(src), "--mode", "lower"],
        )
        assert code == 0
        assert (tmp_path / "myfile.txt").exists()

    def test_run_case_upper(self, tmp_path: Path) -> None:
        """run_tool 调用 case --mode upper 成功。"""
        src = tmp_path / "lower.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["case", str(src), "--mode", "upper"],
        )
        assert code == 0
        assert (tmp_path / "LOWER.txt").exists()

    def test_run_case_invalid_mode(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """case 无效模式时提示。"""
        src = tmp_path / "file.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["case", str(src), "--mode", "invalid"],
        )
        assert code == 0
        captured = capsys.readouterr()
        assert "不支持的模式" in captured.out

    def test_run_multiple_files(self, tmp_path: Path) -> None:
        """批量处理多个文件。"""
        a = tmp_path / "A.txt"
        a.write_text("a")
        b = tmp_path / "B.txt"
        b.write_text("b")

        code = run_tool(
            "filerename",
            ["case", str(a), str(b), "--mode", "lower"],
        )
        assert code == 0
        assert (tmp_path / "a.txt").exists()
        assert (tmp_path / "b.txt").exists()

    def test_run_replace_empty_replacement(self, tmp_path: Path) -> None:
        """replace 不指定 --replacement 时默认为空（删除匹配）。"""
        src = tmp_path / "file_copy.txt"
        src.write_text("content")

        code = run_tool(
            "filerename",
            ["replace", str(src), "_copy"],
        )
        assert code == 0
        assert (tmp_path / "file.txt").exists()
