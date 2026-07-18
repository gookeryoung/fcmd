"""filesearch 工具测试。

验证 ``fcmd.cli.filesearch`` 模块：
- 工具注册与两子命令结构（name/content）
- ``is_binary_file`` 二进制检测（含 OSError 回退）
- ``read_text_lines`` 文本读取（含二进制抛错、UnicodeDecodeError 回退）
- ``_should_skip_part`` 忽略目录命中（含通配模式）
- ``search_by_name`` 文件名 glob 搜索
- ``search_by_content`` 内容正则搜索
- CLI 子命令端到端
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.filesearch import (
    _should_skip_part,
    is_binary_file,
    read_text_lines,
    search_by_content,
    search_by_name,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """filesearch 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "filesearch" in list_tools()

    def test_subcommands(self) -> None:
        """filesearch 有 name/content 两个子命令。"""
        subs = list_subcommands("filesearch")
        assert set(subs) == {"name", "content"}


# ============================================================================ #
# _should_skip_part
# ============================================================================ #
class TestShouldSkipPart:
    """_should_skip_part 忽略目录命中测试。"""

    def test_exact_match(self) -> None:
        """精确命中忽略目录。"""
        assert _should_skip_part(("a", ".git", "b"), {".git"}) is True

    def test_no_match(self) -> None:
        """未命中忽略目录。"""
        assert _should_skip_part(("a", "src", "main.py"), {".git"}) is False

    def test_glob_pattern(self) -> None:
        """通配模式命中（如 *.egg-info）。"""
        assert _should_skip_part(("a", "fcmd.egg-info", "PKG-INFO"), {"*.egg-info"}) is True

    def test_glob_no_match(self) -> None:
        """通配模式未命中。"""
        assert _should_skip_part(("a", "fcmd.egg", "PKG-INFO"), {"*.egg-info"}) is False

    def test_empty_parts(self) -> None:
        """空组件元组不命中。"""
        assert _should_skip_part((), {".git"}) is False


# ============================================================================ #
# is_binary_file
# ============================================================================ #
class TestIsBinaryFile:
    """is_binary_file 二进制检测测试。"""

    def test_text_file(self, tmp_path: Path) -> None:
        """纯文本文件非二进制。"""
        f = tmp_path / "text.txt"
        f.write_text("Hello, world!", encoding="utf-8")
        assert is_binary_file(f) is False

    def test_binary_file(self, tmp_path: Path) -> None:
        """含 \\x00 字节的文件识别为二进制。"""
        f = tmp_path / "bin.dat"
        f.write_bytes(b"abc\x00def")
        assert is_binary_file(f) is True

    def test_empty_file(self, tmp_path: Path) -> None:
        """空文件非二进制。"""
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert is_binary_file(f) is False

    def test_oserror_returns_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """读取异常时保守返回 True。"""
        f = tmp_path / "unreadable.txt"
        f.write_text("data", encoding="utf-8")

        def _raise_oserror(_self: Path, *args: object, **kwargs: object) -> object:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "open", _raise_oserror)
        assert is_binary_file(f) is True


# ============================================================================ #
# read_text_lines
# ============================================================================ #
class TestReadTextLines:
    """read_text_lines 文本读取测试。"""

    def test_normal_text(self, tmp_path: Path) -> None:
        """正常文本读取含行尾换行符。"""
        f = tmp_path / "a.txt"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        lines = read_text_lines(f)
        assert lines == ["line1\n", "line2\n", "line3\n"]

    def test_no_trailing_newline(self, tmp_path: Path) -> None:
        """无行尾换行。"""
        f = tmp_path / "a.txt"
        f.write_text("single line", encoding="utf-8")
        lines = read_text_lines(f)
        assert lines == ["single line"]

    def test_binary_raises_value_error(self, tmp_path: Path) -> None:
        """二进制文件抛 ValueError。"""
        f = tmp_path / "bin.dat"
        f.write_bytes(b"abc\x00def")
        with pytest.raises(ValueError, match="二进制文件"):
            read_text_lines(f)

    def test_utf8_decode_fallback(self, tmp_path: Path) -> None:
        """utf-8 解码失败时回退为 replace（不抛异常）。"""
        f = tmp_path / "mixed.txt"
        # 写入非法 utf-8 字节序列但不含 \x00（避免被识别为二进制）
        f.write_bytes(b"\xff\xfe\xfd illegal utf-8 bytes here")
        lines = read_text_lines(f)
        # 不抛异常即通过；行内容含替换字符
        assert len(lines) >= 1


# ============================================================================ #
# search_by_name
# ============================================================================ #
class TestSearchByName:
    """search_by_name 文件名 glob 搜索测试。"""

    def test_basic_glob(self, tmp_path: Path) -> None:
        """按扩展名 glob 匹配。"""
        (tmp_path / "a.py").touch()
        (tmp_path / "b.py").touch()
        (tmp_path / "c.txt").touch()
        results = search_by_name(tmp_path, "*.py")
        names = [p.name for p in results]
        assert sorted(names) == ["a.py", "b.py"]

    def test_recursive(self, tmp_path: Path) -> None:
        """递归搜索子目录。"""
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.py").touch()
        (tmp_path / "sub" / "b.py").touch()
        results = search_by_name(tmp_path, "*.py")
        names = [p.name for p in results]
        assert sorted(names) == ["a.py", "b.py"]

    def test_include_dirs(self, tmp_path: Path) -> None:
        """include_dirs=True 包含目录。"""
        (tmp_path / "mydir").mkdir()
        (tmp_path / "mydir" / "a.py").touch()
        (tmp_path / "file.py").touch()
        results = search_by_name(tmp_path, "mydir*", include_dirs=True)
        names = [p.name for p in results]
        assert "mydir" in names

    def test_exclude_dirs_by_default(self, tmp_path: Path) -> None:
        """默认不返回目录。"""
        (tmp_path / "mydir.py").mkdir()  # 名字像 .py 但是目录
        results = search_by_name(tmp_path, "*.py")
        # 目录不应出现
        assert all(p.is_file() for p in results)

    def test_ignore_dirs_default(self, tmp_path: Path) -> None:
        """默认跳过 _common.IGNORE_DIRS 中的目录。"""
        (tmp_path / "a.py").touch()
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "b.py").touch()
        results = search_by_name(tmp_path, "*.py")
        names = [p.name for p in results]
        assert "a.py" in names
        assert "b.py" not in names

    def test_ignore_dirs_custom(self, tmp_path: Path) -> None:
        """自定义 ignore_dirs 集合。"""
        (tmp_path / "a.py").touch()
        (tmp_path / "skipme").mkdir()
        (tmp_path / "skipme" / "b.py").touch()
        results = search_by_name(tmp_path, "*.py", ignore_dirs={"skipme"})
        names = [p.name for p in results]
        assert "a.py" in names
        assert "b.py" not in names

    def test_glob_egg_info(self, tmp_path: Path) -> None:
        """通配 *.egg-info 命中目录被跳过。"""
        (tmp_path / "a.py").touch()
        (tmp_path / "fcmd.egg-info").mkdir()
        (tmp_path / "fcmd.egg-info" / "PKG-INFO").touch()
        results = search_by_name(tmp_path, "*")
        names = [p.name for p in results]
        assert "a.py" in names
        assert "PKG-INFO" not in names

    def test_no_match(self, tmp_path: Path) -> None:
        """无匹配返回空列表。"""
        (tmp_path / "a.py").touch()
        results = search_by_name(tmp_path, "*.md")
        assert results == []

    def test_not_found_raises(self, tmp_path: Path) -> None:
        """目录不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="目录不存在"):
            search_by_name(tmp_path / "missing", "*.py")

    def test_not_directory_raises(self, tmp_path: Path) -> None:
        """非目录抛 NotADirectoryError。"""
        f = tmp_path / "file.txt"
        f.touch()
        with pytest.raises(NotADirectoryError, match="不是目录"):
            search_by_name(f, "*.py")

    def test_results_sorted(self, tmp_path: Path) -> None:
        """结果按字符串排序。"""
        (tmp_path / "c.py").touch()
        (tmp_path / "a.py").touch()
        (tmp_path / "b.py").touch()
        results = search_by_name(tmp_path, "*.py")
        names = [p.name for p in results]
        assert names == ["a.py", "b.py", "c.py"]


# ============================================================================ #
# search_by_content
# ============================================================================ #
class TestSearchByContent:
    """search_by_content 内容正则搜索测试。"""

    def test_basic_match(self, tmp_path: Path) -> None:
        """基本正则匹配。"""
        f = tmp_path / "a.py"
        f.write_text("def hello():\n    pass\n", encoding="utf-8")
        results = search_by_content(tmp_path, r"def \w+")
        assert len(results) == 1
        path, lineno, line = results[0]
        assert path == f
        assert lineno == 1
        assert "def hello" in line

    def test_multiple_matches(self, tmp_path: Path) -> None:
        """单文件多行匹配。"""
        f = tmp_path / "a.py"
        f.write_text("def foo():\n    pass\n\ndef bar():\n    pass\n", encoding="utf-8")
        results = search_by_content(tmp_path, r"def \w+")
        assert len(results) == 2
        assert results[0][1] == 1
        # 空行也算一行：foo(1) pass(2) 空(3) bar(4)
        assert results[1][1] == 4

    def test_extension_filter(self, tmp_path: Path) -> None:
        """extension 限定扩展名。"""
        (tmp_path / "a.py").write_text("TODO: fix\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("TODO: docs\n", encoding="utf-8")
        results = search_by_content(tmp_path, "TODO", extension=".py")
        paths = [r[0] for r in results]
        assert any(p.suffix == ".py" for p in paths)
        assert all(p.suffix == ".py" for p in paths)

    def test_skip_binary(self, tmp_path: Path) -> None:
        """跳过二进制文件。"""
        (tmp_path / "bin.dat").write_bytes(b"TODO\x00fix\n")
        (tmp_path / "a.txt").write_text("TODO: docs\n", encoding="utf-8")
        results = search_by_content(tmp_path, "TODO")
        paths = [r[0] for r in results]
        assert all(p.name != "bin.dat" for p in paths)
        assert any(p.name == "a.txt" for p in paths)

    def test_ignore_dirs(self, tmp_path: Path) -> None:
        """默认跳过 IGNORE_DIRS。"""
        (tmp_path / "a.py").write_text("TODO: x\n", encoding="utf-8")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "b.py").write_text("TODO: y\n", encoding="utf-8")
        results = search_by_content(tmp_path, "TODO")
        paths = [r[0] for r in results]
        assert all(p.name != "b.py" for p in paths)

    def test_ignore_dirs_custom(self, tmp_path: Path) -> None:
        """自定义 ignore_dirs 集合。"""
        (tmp_path / "a.py").write_text("TODO: x\n", encoding="utf-8")
        (tmp_path / "skipme").mkdir()
        (tmp_path / "skipme" / "b.py").write_text("TODO: y\n", encoding="utf-8")
        results = search_by_content(tmp_path, "TODO", ignore_dirs={"skipme"})
        paths = [r[0] for r in results]
        assert all(p.name != "b.py" for p in paths)
        assert any(p.name == "a.py" for p in paths)

    def test_no_match(self, tmp_path: Path) -> None:
        """无匹配返回空列表。"""
        (tmp_path / "a.py").write_text("hello world\n", encoding="utf-8")
        results = search_by_content(tmp_path, "nonexistent_pattern_xyz")
        assert results == []

    def test_not_found_raises(self, tmp_path: Path) -> None:
        """目录不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError, match="目录不存在"):
            search_by_content(tmp_path / "missing", "pattern")

    def test_not_directory_raises(self, tmp_path: Path) -> None:
        """非目录抛 NotADirectoryError。"""
        f = tmp_path / "file.txt"
        f.touch()
        with pytest.raises(NotADirectoryError, match="不是目录"):
            search_by_content(f, "pattern")

    def test_regex_error_raises(self, tmp_path: Path) -> None:
        """正则语法错误抛 re.error。"""
        import re

        with pytest.raises(re.error):
            search_by_content(tmp_path, "(*invalid")


# ============================================================================ #
# CLI 子命令端到端
# ============================================================================ #
class TestCLISubcommands:
    """CLI 子命令端到端测试。"""

    def test_name_basic(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """name 子命令输出匹配文件。"""
        (tmp_path / "a.py").touch()
        (tmp_path / "b.py").touch()
        (tmp_path / "c.txt").touch()
        code = run_tool("filesearch", ["name", str(tmp_path), "*.py"])
        assert code == 0
        out = capsys.readouterr().out
        assert "a.py" in out
        assert "b.py" in out
        assert "c.txt" not in out

    def test_name_no_match(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """name 无匹配输出提示。"""
        (tmp_path / "a.py").touch()
        code = run_tool("filesearch", ["name", str(tmp_path), "*.md"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无匹配" in out

    def test_name_include_dirs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """name --include-dirs 包含目录。"""
        (tmp_path / "mydir").mkdir()
        code = run_tool("filesearch", ["name", str(tmp_path), "mydir", "--include-dirs"])
        assert code == 0
        out = capsys.readouterr().out
        assert "mydir" in out

    def test_name_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """name 不存在目录提示。"""
        code = run_tool("filesearch", ["name", str(tmp_path / "missing"), "*.py"])
        assert code == 0
        out = capsys.readouterr().out
        assert "目录不存在" in out

    def test_name_not_directory(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """name 非目录提示。"""
        f = tmp_path / "file.txt"
        f.touch()
        code = run_tool("filesearch", ["name", str(f), "*.py"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不是目录" in out

    def test_content_basic(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """content 子命令输出 文件:行号:行内容。"""
        f = tmp_path / "a.py"
        f.write_text("def hello():\n    pass\n", encoding="utf-8")
        code = run_tool("filesearch", ["content", str(tmp_path), r"def \w+"])
        assert code == 0
        out = capsys.readouterr().out
        assert "a.py" in out
        assert "1" in out
        assert "def hello" in out

    def test_content_extension(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """content --extension 限定扩展名。"""
        (tmp_path / "a.py").write_text("TODO: fix\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("TODO: docs\n", encoding="utf-8")
        code = run_tool("filesearch", ["content", str(tmp_path), "TODO", "--extension", ".py"])
        assert code == 0
        out = capsys.readouterr().out
        assert "a.py" in out
        assert "b.txt" not in out

    def test_content_no_match(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """content 无匹配输出提示。"""
        (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")
        code = run_tool("filesearch", ["content", str(tmp_path), "nonexistent_xyz"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无匹配" in out

    def test_content_regex_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """content 正则错误提示。"""
        code = run_tool("filesearch", ["content", str(tmp_path), "(*invalid"])
        assert code == 0
        out = capsys.readouterr().out
        assert "正则表达式错误" in out

    def test_content_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """content 不存在目录提示。"""
        code = run_tool("filesearch", ["content", str(tmp_path / "missing"), "pattern"])
        assert code == 0
        out = capsys.readouterr().out
        assert "目录不存在" in out
