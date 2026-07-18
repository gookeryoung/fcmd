"""pathtool 工具测试。

验证 ``fcmd.cli.pathtool`` 模块：
- 工具注册与四子命令结构（show/rel/norm/diff）
- ``normalize_path`` 路径规范化
- ``relative_to`` 相对路径计算（含错误分支）
- ``path_parts`` 各部分提取
- ``path_diff`` 路径差异比较
- CLI 子命令端到端
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.pathtool import (
    normalize_path,
    path_diff,
    path_parts,
    relative_to,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """pathtool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "pathtool" in list_tools()

    def test_subcommands(self) -> None:
        """pathtool 有 show/rel/norm/diff 四个子命令。"""
        subs = list_subcommands("pathtool")
        assert set(subs) == {"show", "rel", "norm", "diff"}


# ============================================================================ #
# normalize_path
# ============================================================================ #
class TestNormalizePath:
    """normalize_path 规范化测试。"""

    def test_relative_to_absolute(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """相对路径转绝对路径。"""
        monkeypatch.chdir(tmp_path)
        result = normalize_path(Path("sub/file.txt"))
        assert result == tmp_path / "sub" / "file.txt"
        assert result.is_absolute()

    def test_dotdot_resolved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """.. 组件被消除。"""
        monkeypatch.chdir(tmp_path)
        result = normalize_path(Path("a/b/../c"))
        assert result == tmp_path / "a" / "c"

    def test_dot_resolved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """. 组件被消除。"""
        monkeypatch.chdir(tmp_path)
        result = normalize_path(Path("a/./b"))
        assert result == tmp_path / "a" / "b"

    def test_user_expansion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """~ 展开为用户目录。"""
        monkeypatch.setenv("USERPROFILE", "/home/test" if Path("/").exists() else "C:/Users/test")
        result = normalize_path(Path("~/file.txt"))
        assert "~" not in str(result)
        assert result.name == "file.txt"


# ============================================================================ #
# relative_to
# ============================================================================ #
class TestRelativeTo:
    """relative_to 相对路径计算测试。"""

    def test_basic(self, tmp_path: Path) -> None:
        """基本相对路径计算。"""
        base = tmp_path / "src"
        target = tmp_path / "src" / "main.py"
        base.mkdir()
        target.touch()
        rel = relative_to(target, base)
        assert str(rel) == "main.py"

    def test_nested(self, tmp_path: Path) -> None:
        """嵌套子目录相对路径。"""
        base = tmp_path
        target = tmp_path / "a" / "b" / "c.txt"
        target.parent.mkdir(parents=True)
        target.touch()
        rel = relative_to(target, base)
        # 跨平台：用 posix 比较
        assert rel.as_posix() == "a/b/c.txt"

    def test_not_subpath_raises(self, tmp_path: Path) -> None:
        """目标不在 base 之下抛 ValueError。"""
        base = tmp_path / "src"
        target = tmp_path / "other" / "file.txt"
        base.mkdir()
        target.parent.mkdir()
        target.touch()
        with pytest.raises(ValueError):
            relative_to(target, base)

    def test_normalizes_inputs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """输入会先规范化（.. 处理）。"""
        monkeypatch.chdir(tmp_path)
        base = tmp_path / "project"
        target = tmp_path / "project" / "file.txt"
        base.mkdir()
        target.touch()
        # base 用 ./project，target 用 project/../project/file.txt
        rel = relative_to(Path("./project/../project/file.txt"), Path("./project"))
        assert str(rel) == "file.txt"


# ============================================================================ #
# path_parts
# ============================================================================ #
class TestPathParts:
    """path_parts 各部分提取测试。"""

    def test_with_extension(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """带扩展名文件路径。"""
        monkeypatch.chdir(tmp_path)
        info = path_parts(Path("src/main.py"))
        assert info["name"] == "main.py"
        assert info["stem"] == "main"
        assert info["suffix"] == ".py"
        assert info["suffixes"] == [".py"]
        assert info["parent"].endswith("src")
        assert "main.py" in info["parts"][-1]

    def test_multiple_extensions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """多扩展名路径。"""
        monkeypatch.chdir(tmp_path)
        info = path_parts(Path("archive.tar.gz"))
        assert info["name"] == "archive.tar.gz"
        assert info["stem"] == "archive.tar"
        assert info["suffix"] == ".gz"
        assert info["suffixes"] == [".tar", ".gz"]

    def test_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """目录路径无扩展名。"""
        monkeypatch.chdir(tmp_path)
        info = path_parts(Path("src/fcmd"))
        assert info["name"] == "fcmd"
        assert info["stem"] == "fcmd"
        assert info["suffix"] == ""

    def test_input_preserved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """原始输入保留。"""
        monkeypatch.chdir(tmp_path)
        info = path_parts(Path("./a/b/../c.txt"))
        assert info["input"] == str(Path("./a/b/../c.txt"))
        # 规范化后不含 ..
        assert ".." not in info["absolute"]


# ============================================================================ #
# path_diff
# ============================================================================ #
class TestPathDiff:
    """path_diff 路径差异比较测试。"""

    def test_basic_common_prefix(self, tmp_path: Path) -> None:
        """公共前缀 + 各自独有部分。"""
        p1 = tmp_path / "src" / "fcmd" / "cli.py"
        p2 = tmp_path / "src" / "tests" / "test_cli.py"
        common, only1, only2 = path_diff(p1, p2)
        # 公共前缀包含 tmp_path / src
        assert "src" in common
        assert "fcmd" in only1
        assert "tests" in only2

    def test_no_common(self, tmp_path: Path) -> None:
        """完全不同的路径（不同卷标）。"""
        # Windows 下 C:/ 和 D:/ 完全不同
        if tmp_path.drive:
            p1 = Path("C:/a/b")
            p2 = Path("D:/c/d")
        else:
            p1 = Path("/a/b")
            p2 = Path("/c/d")
        _common, only1, only2 = path_diff(p1, p2)
        # 至少 only1/only2 不为空
        assert only1
        assert only2

    def test_identical_paths(self, tmp_path: Path) -> None:
        """完全相同的路径。"""
        p = tmp_path / "src" / "file.py"
        _common, only1, only2 = path_diff(p, p)
        # 全部在 common 中
        assert only1 == []
        assert only2 == []

    def test_one_is_prefix(self, tmp_path: Path) -> None:
        """一个路径是另一个的前缀。"""
        p1 = tmp_path / "src"
        p2 = tmp_path / "src" / "deep" / "file.py"
        _common, only1, only2 = path_diff(p1, p2)
        assert only1 == []  # p1 全部在 common
        assert "deep" in only2
        assert "file.py" in only2


# ============================================================================ #
# CLI 子命令端到端
# ============================================================================ #
class TestCLISubcommands:
    """CLI 子命令端到端测试。"""

    def test_show(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """show 输出各部分信息。"""
        monkeypatch.chdir(tmp_path)
        # 创建文件确保 resolve 正常
        target = tmp_path / "src" / "main.py"
        target.parent.mkdir()
        target.touch()
        code = run_tool("pathtool", ["show", "src/main.py"])
        assert code == 0
        out = capsys.readouterr().out
        assert "main.py" in out
        assert "主干名:     main" in out
        assert "扩展名:     .py" in out

    def test_show_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """show 目录路径。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "mydir").mkdir()
        code = run_tool("pathtool", ["show", "mydir"])
        assert code == 0
        out = capsys.readouterr().out
        assert "mydir" in out
        assert "扩展名:     (无)" in out

    def test_rel(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """rel 计算相对路径。"""
        base = tmp_path / "src"
        target = tmp_path / "src" / "main.py"
        base.mkdir()
        target.touch()
        code = run_tool("pathtool", ["rel", str(target), str(base)])
        assert code == 0
        out = capsys.readouterr().out
        assert "main.py" in out

    def test_rel_not_subpath(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """rel 目标不在 base 之下时提示。"""
        base = tmp_path / "src"
        target = tmp_path / "other" / "file.txt"
        base.mkdir()
        target.parent.mkdir()
        target.touch()
        code = run_tool("pathtool", ["rel", str(target), str(base)])
        assert code == 0
        out = capsys.readouterr().out
        assert "无法计算相对路径" in out

    def test_norm(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """norm 规范化路径。"""
        monkeypatch.chdir(tmp_path)
        code = run_tool("pathtool", ["norm", "a/b/../c.txt"])
        assert code == 0
        out = capsys.readouterr().out
        # 提取实际路径行（框架前缀 "> 'norm' 开始执行..." 与后缀 "OK 'norm' 成功..."）
        lines = out.strip().splitlines()
        # 第 1 行是框架前缀，第 2 行是路径，第 3 行是框架后缀
        path_line = lines[1]
        # 规范化后不含 .. 组件
        assert ".." not in path_line
        assert "c.txt" in path_line
        # 绝对路径包含 tmp_path
        assert str(tmp_path) in path_line

    def test_norm_with_dot(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """norm 处理 . 组件。"""
        monkeypatch.chdir(tmp_path)
        code = run_tool("pathtool", ["norm", "a/./b"])
        assert code == 0
        out = capsys.readouterr().out
        # 输出绝对路径，不含 /./
        assert "/./" not in out.replace("\\", "/")

    def test_diff(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """diff 比较两路径。"""
        p1 = tmp_path / "src" / "fcmd" / "cli.py"
        p2 = tmp_path / "src" / "tests" / "test_cli.py"
        code = run_tool("pathtool", ["diff", str(p1), str(p2)])
        assert code == 0
        out = capsys.readouterr().out
        assert "公共前缀" in out
        assert "仅路径 1" in out
        assert "仅路径 2" in out
        assert "fcmd" in out
        assert "tests" in out

    def test_diff_identical(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """diff 相同路径。"""
        p = tmp_path / "src" / "file.py"
        code = run_tool("pathtool", ["diff", str(p), str(p)])
        assert code == 0
        out = capsys.readouterr().out
        assert "仅路径 1: (无)" in out
        assert "仅路径 2: (无)" in out

    def test_diff_no_common(self, capsys: pytest.CaptureFixture[str]) -> None:
        """diff 无公共前缀。"""
        if Path("C:/").exists():
            p1 = "C:/a/b"
            p2 = "D:/c/d"
        else:
            p1 = "/a/b"
            p2 = "/x/y"
        code = run_tool("pathtool", ["diff", p1, p2])
        assert code == 0
        out = capsys.readouterr().out
        assert "公共前缀: (无)" in out or "公共前缀:" in out
