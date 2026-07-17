"""textdiff 工具测试。

验证 ``fcmd.cli.textdiff`` 模块：
- 工具注册与两子命令结构（file/dir）
- ``_read_lines`` 文本读取（utf-8/回退/二进制检测）
- ``colorize_diff`` ANSI 着色
- ``compare_files`` 文件比较（相同/不同/上下文）
- ``compare_directories`` 目录比较（仅左/仅右/不同/相同/递归/模式）
- CLI 子命令端到端（含 ``--color``/``--context``/``--pattern``/``--no-recursive``）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.textdiff import (
    _read_lines,
    colorize_diff,
    compare_directories,
    compare_files,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """textdiff 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "textdiff" in list_tools()

    def test_subcommands(self) -> None:
        """textdiff 有 file/dir 两个子命令。"""
        subs = list_subcommands("textdiff")
        assert set(subs) == {"file", "dir"}


# ============================================================================ #
# _read_lines
# ============================================================================ #
class TestReadLines:
    """_read_lines 文本读取测试。"""

    def test_utf8(self, tmp_path: Path) -> None:
        """读取 utf-8 文本文件。"""
        f = tmp_path / "utf8.txt"
        f.write_text("line1\nline2\n", encoding="utf-8")
        lines = _read_lines(f)
        assert lines == ["line1\n", "line2\n"]

    def test_fallback_encoding(self, tmp_path: Path) -> None:
        """非 utf-8 字节回退为 utf-8 + errors='replace'。"""
        f = tmp_path / "gbk.txt"
        f.write_bytes("中文".encode("gbk"))  # gbk 字节非 utf-8 合法
        lines = _read_lines(f)
        assert len(lines) >= 1  # 替换字符后仍可读

    def test_binary_file_raises(self, tmp_path: Path) -> None:
        """二进制文件抛 ValueError。"""
        f = tmp_path / "bin.dat"
        f.write_bytes(b"binary\x00data")
        with pytest.raises(ValueError, match="二进制文件不支持比较"):
            _read_lines(f)


# ============================================================================ #
# colorize_diff
# ============================================================================ #
class TestColorizeDiff:
    """colorize_diff ANSI 着色测试。"""

    def test_addition_green(self) -> None:
        """新增行为绿色。"""
        result = colorize_diff("+added line\n")
        assert "\033[32m" in result
        assert "+added line" in result

    def test_deletion_red(self) -> None:
        """删除行为红色。"""
        result = colorize_diff("-removed line\n")
        assert "\033[31m" in result
        assert "-removed line" in result

    def test_hunk_cyan(self) -> None:
        """@@ 位置标记为青色。"""
        result = colorize_diff("@@ -1,2 +1,3 @@\n")
        assert "\033[36m" in result

    def test_file_header_not_colored(self) -> None:
        """---/+++ 文件头不着色。"""
        result = colorize_diff("--- old.txt\n+++ new.txt\n")
        assert "\033[" not in result

    def test_context_line_not_colored(self) -> None:
        """上下文行不着色。"""
        result = colorize_diff(" unchanged\n")
        assert "\033[" not in result


# ============================================================================ #
# compare_files
# ============================================================================ #
class TestCompareFiles:
    """compare_files 文件比较测试。"""

    def test_identical_files(self, tmp_path: Path) -> None:
        """相同文件返回空字符串。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("same content\n")
        f2.write_text("same content\n")
        assert compare_files(f1, f2) == ""

    def test_different_files(self, tmp_path: Path) -> None:
        """不同文件返回 unified diff。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("line1\nline2\nline3\n")
        f2.write_text("line1\nchanged\nline3\n")
        result = compare_files(f1, f2)
        assert "---" in result
        assert "+++" in result
        assert "-line2" in result
        assert "+changed" in result

    def test_context_lines(self, tmp_path: Path) -> None:
        """context 参数控制上下文行数。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n")
        f2.write_text("1\n2\n3\n4\n5\nX\n7\n8\n9\n10\n")
        result_n0 = compare_files(f1, f2, context=0)
        result_n3 = compare_files(f1, f2, context=3)
        # context=0 应比 context=3 更短
        assert len(result_n0) < len(result_n3)

    def test_binary_raises_value_error(self, tmp_path: Path) -> None:
        """二进制文件抛 ValueError。"""
        f1 = tmp_path / "a.dat"
        f2 = tmp_path / "b.dat"
        f1.write_bytes(b"bin\x00ary1")
        f2.write_bytes(b"bin\x00ary2")
        with pytest.raises(ValueError, match="二进制文件"):
            compare_files(f1, f2)


# ============================================================================ #
# compare_directories
# ============================================================================ #
class TestCompareDirectories:
    """compare_directories 目录比较测试。"""

    def test_identical_dirs(self, tmp_path: Path) -> None:
        """相同目录返回 '目录内容相同'。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "a.txt").write_text("same\n")
        (d2 / "a.txt").write_text("same\n")
        assert compare_directories(d1, d2) == "目录内容相同"

    def test_only_left(self, tmp_path: Path) -> None:
        """仅在左目录的文件。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "only_left.txt").write_text("x")
        result = compare_directories(d1, d2)
        assert "仅在" in result
        assert "only_left.txt" in result

    def test_only_right(self, tmp_path: Path) -> None:
        """仅在右目录的文件。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d2 / "only_right.txt").write_text("x")
        result = compare_directories(d1, d2)
        assert "仅在" in result
        assert "only_right.txt" in result

    def test_different_content(self, tmp_path: Path) -> None:
        """同名文件内容不同。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "f.txt").write_text("old\n")
        (d2 / "f.txt").write_text("new\n")
        result = compare_directories(d1, d2)
        assert "内容不同" in result
        assert "f.txt" in result

    def test_recursive(self, tmp_path: Path) -> None:
        """递归比较子目录文件。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        (d1 / "sub").mkdir(parents=True)
        (d2 / "sub").mkdir(parents=True)
        (d1 / "sub" / "deep.txt").write_text("a")
        (d2 / "sub" / "deep.txt").write_text("b")
        result = compare_directories(d1, d2, recursive=True)
        assert "内容不同" in result
        assert "sub/deep.txt" in result

    def test_non_recursive(self, tmp_path: Path) -> None:
        """非递归模式忽略子目录文件。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        (d1 / "sub").mkdir(parents=True)
        (d2 / "sub").mkdir(parents=True)
        (d1 / "sub" / "deep.txt").write_text("a")
        (d2 / "sub" / "deep.txt").write_text("b")
        result = compare_directories(d1, d2, recursive=False)
        assert result == "目录内容相同"

    def test_pattern_filter(self, tmp_path: Path) -> None:
        """pattern 仅匹配 .py 文件。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "a.py").write_text("x")
        (d2 / "a.py").write_text("y")
        (d1 / "b.txt").write_text("x")
        (d2 / "b.txt").write_text("y")
        result = compare_directories(d1, d2, pattern="*.py")
        assert "a.py" in result
        assert "b.txt" not in result

    def test_oserror_handling(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """filecmp.cmp 抛 OSError 时归入 errors 列表。"""

        def raise_oserror(_a: Path, _b: Path, shallow: bool = True) -> bool:
            raise OSError("permission denied")

        import filecmp as _filecmp

        monkeypatch.setattr(_filecmp, "cmp", raise_oserror)
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "f.txt").write_text("x")
        (d2 / "f.txt").write_text("x")
        result = compare_directories(d1, d2)
        assert "无法比较" in result
        assert "f.txt" in result


# ============================================================================ #
# CLI 子命令端到端
# ============================================================================ #
class TestCLISubcommands:
    """CLI 子命令端到端测试（通过 run_tool 调用）。"""

    def test_run_file_same(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """file 子命令比较相同文件。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("same\n")
        f2.write_text("same\n")
        code = run_tool("textdiff", ["file", str(f1), str(f2)])
        assert code == 0
        captured = capsys.readouterr()
        assert "文件内容相同" in captured.out

    def test_run_file_different(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """file 子命令比较不同文件。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("old\n")
        f2.write_text("new\n")
        code = run_tool("textdiff", ["file", str(f1), str(f2)])
        assert code == 0
        captured = capsys.readouterr()
        assert "---" in captured.out
        assert "-old" in captured.out
        assert "+new" in captured.out

    def test_run_file_context(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """file --context 控制上下文行数。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("1\n2\n3\n4\n5\n")
        f2.write_text("1\n2\nX\n4\n5\n")
        code = run_tool("textdiff", ["file", str(f1), str(f2), "--context", "0"])
        assert code == 0
        captured = capsys.readouterr()
        assert "@@ -3 +3 @@" in captured.out

    def test_run_file_color(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """file --color 启用 ANSI 着色。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("old\n")
        f2.write_text("new\n")
        code = run_tool("textdiff", ["file", str(f1), str(f2), "--color"])
        assert code == 0
        captured = capsys.readouterr()
        assert "\033[31m" in captured.out  # 红色
        assert "\033[32m" in captured.out  # 绿色

    def test_run_file_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """file 文件不存在时提示。"""
        code = run_tool("textdiff", ["file", str(tmp_path / "no1.txt"), str(tmp_path / "no2.txt")])
        assert code == 0
        captured = capsys.readouterr()
        assert "文件不存在" in captured.out

    def test_run_file_second_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """file 第二个文件不存在时提示。"""
        f1 = tmp_path / "a.txt"
        f1.write_text("x")
        code = run_tool("textdiff", ["file", str(f1), str(tmp_path / "no.txt")])
        assert code == 0
        captured = capsys.readouterr()
        assert "文件不存在" in captured.out

    def test_run_file_binary(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """file 二进制文件时提示。"""
        f1 = tmp_path / "a.dat"
        f2 = tmp_path / "b.dat"
        f1.write_bytes(b"bin\x00ary1")
        f2.write_bytes(b"bin\x00ary2")
        code = run_tool("textdiff", ["file", str(f1), str(f2)])
        assert code == 0
        captured = capsys.readouterr()
        assert "二进制文件" in captured.out

    def test_run_dir_same(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """dir 子命令比较相同目录。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "a.txt").write_text("x")
        (d2 / "a.txt").write_text("x")
        code = run_tool("textdiff", ["dir", str(d1), str(d2)])
        assert code == 0
        captured = capsys.readouterr()
        assert "目录内容相同" in captured.out

    def test_run_dir_different(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """dir 子命令列出差异文件。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "only.txt").write_text("x")
        code = run_tool("textdiff", ["dir", str(d1), str(d2)])
        assert code == 0
        captured = capsys.readouterr()
        assert "仅在" in captured.out
        assert "only.txt" in captured.out

    def test_run_dir_pattern(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """dir --pattern 过滤文件。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "a.py").write_text("x")
        (d2 / "a.py").write_text("y")
        (d1 / "b.txt").write_text("x")
        (d2 / "b.txt").write_text("y")
        code = run_tool("textdiff", ["dir", str(d1), str(d2), "--pattern", "*.py"])
        assert code == 0
        captured = capsys.readouterr()
        assert "a.py" in captured.out
        assert "b.txt" not in captured.out

    def test_run_dir_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """dir 目录不存在时提示。"""
        code = run_tool("textdiff", ["dir", str(tmp_path / "nodir1"), str(tmp_path / "nodir2")])
        assert code == 0
        captured = capsys.readouterr()
        assert "目录不存在" in captured.out

    def test_run_dir_second_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """dir 第二个目录不存在时提示。"""
        d1 = tmp_path / "d1"
        d1.mkdir()
        code = run_tool("textdiff", ["dir", str(d1), str(tmp_path / "nodir2")])
        assert code == 0
        captured = capsys.readouterr()
        assert "目录不存在" in captured.out

    def test_run_dir_no_recursive(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """dir --no-recursive 仅比较顶层。"""
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        (d1 / "sub").mkdir(parents=True)
        (d2 / "sub").mkdir(parents=True)
        (d1 / "sub" / "deep.txt").write_text("a")
        (d2 / "sub" / "deep.txt").write_text("b")
        code = run_tool("textdiff", ["dir", str(d1), str(d2), "--no-recursive"])
        assert code == 0
        captured = capsys.readouterr()
        assert "目录内容相同" in captured.out
