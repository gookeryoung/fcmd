"""models 包测试：command / filefilter / version。

验证 ``fcmd.models`` 包下 3 个数据模型模块的正确性。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from fcmd.models import (
    BumpPart,
    CommandResult,
    IgnoreSpec,
    Version,
    parse_version,
    run_command,
    should_ignore,
    to_shutil_ignore,
)


# ============================================================================ #
# CommandResult 测试
# ============================================================================ #
class TestCommandResult:
    """CommandResult 值对象测试。"""

    def test_construct(self) -> None:
        """构造 CommandResult。"""
        result = CommandResult(cmd=["echo", "hi"], returncode=0, stdout="hi\n", stderr="")
        assert result.cmd == ["echo", "hi"]
        assert result.returncode == 0
        assert result.stdout == "hi\n"
        assert result.stderr == ""

    def test_succeeded(self) -> None:
        """返回码 0 时 succeeded 为 True。"""
        result = CommandResult(cmd=[], returncode=0, stdout="", stderr="")
        assert result.succeeded
        assert not result.failed

    def test_failed(self) -> None:
        """返回码非 0 时 failed 为 True。"""
        result = CommandResult(cmd=[], returncode=1, stdout="", stderr="")
        assert result.failed
        assert not result.succeeded

    def test_frozen(self) -> None:
        """frozen=True 不可修改。"""
        result = CommandResult(cmd=[], returncode=0, stdout="", stderr="")
        with pytest.raises(AttributeError):
            result.returncode = 1  # type: ignore[misc]


# ============================================================================ #
# run_command 测试
# ============================================================================ #
class TestRunCommand:
    """run_command 函数测试。"""

    def test_capture_true(self) -> None:
        """capture=True 捕获输出。"""
        result = run_command(["python", "-c", "print('hello')"], capture=True)
        assert result.succeeded
        assert "hello" in result.stdout

    def test_capture_false(self) -> None:
        """capture=False 时 stdout 为空字符串（输出透传到终端，不在 result 中）。"""
        result = run_command(["python", "-c", "print('world')"])
        assert result.succeeded
        # capture=False 时 stdout 为空（输出直接到终端文件描述符）
        assert result.stdout == ""

    def test_failed_command(self) -> None:
        """命令失败时 returncode 非零，不抛异常。"""
        # 用一个不存在的命令
        result = run_command(["python", "-c", "import sys; sys.exit(1)"], capture=True)
        assert result.failed
        assert result.returncode == 1

    def test_check_true_raises(self) -> None:
        """check=True 时失败抛 CalledProcessError。"""
        with pytest.raises(subprocess.CalledProcessError):
            run_command(["python", "-c", "import sys; sys.exit(1)"], check=True)

    def test_stderr_captured(self) -> None:
        """capture=True 时 stderr 被捕获。"""
        result = run_command(
            ["python", "-c", "import sys; sys.stderr.write('err')"],
            capture=True,
        )
        assert "err" in result.stderr


# ============================================================================ #
# IgnoreSpec 测试
# ============================================================================ #
class TestIgnoreSpec:
    """IgnoreSpec 值对象测试。"""

    def test_default_empty(self) -> None:
        """默认构造为空规则。"""
        spec = IgnoreSpec()
        assert spec.dirs == frozenset()
        assert spec.patterns == ()

    def test_from_iterable_separates(self) -> None:
        """from_iterable 自动区分目录名与 glob 模式。"""
        spec = IgnoreSpec.from_iterable([".git", "__pycache__", "*.pyc", "*.egg-info"])
        assert ".git" in spec.dirs
        assert "__pycache__" in spec.dirs
        assert "*.pyc" in spec.patterns
        assert "*.egg-info" in spec.patterns

    def test_from_iterable_all_dirs(self) -> None:
        """from_iterable 全是目录名。"""
        spec = IgnoreSpec.from_iterable({".git", ".venv"})
        assert spec.dirs == frozenset({".git", ".venv"})
        assert spec.patterns == ()

    def test_from_iterable_all_patterns(self) -> None:
        """from_iterable 全是 glob 模式。"""
        spec = IgnoreSpec.from_iterable(["*.pyc", "*.pyo"])
        assert spec.dirs == frozenset()
        assert "*.pyc" in spec.patterns
        assert "*.pyo" in spec.patterns

    def test_frozen(self) -> None:
        """frozen=True 不可修改。"""
        spec = IgnoreSpec(dirs=frozenset({".git"}), patterns=("*.pyc",))
        with pytest.raises(AttributeError):
            spec.dirs = frozenset()  # type: ignore[misc]


# ============================================================================ #
# should_ignore 测试
# ============================================================================ #
class TestShouldIgnore:
    """should_ignore 函数测试。"""

    def test_ignore_dir_in_parts(self) -> None:
        """路径中包含忽略目录时返回 True。"""
        spec = IgnoreSpec(dirs=frozenset({".git"}))
        assert should_ignore(Path("project/.git/config"), spec)

    def test_ignore_dir_not_in_parts(self) -> None:
        """路径中不包含忽略目录时返回 False。"""
        spec = IgnoreSpec(dirs=frozenset({".git"}))
        assert not should_ignore(Path("project/src/main.py"), spec)

    def test_ignore_pattern_match(self) -> None:
        """文件名匹配 glob 模式时返回 True。"""
        spec = IgnoreSpec(patterns=("*.pyc",))
        assert should_ignore(Path("project/module.cpython-310.pyc"), spec)

    def test_ignore_pattern_no_match(self) -> None:
        """文件名不匹配 glob 模式时返回 False。"""
        spec = IgnoreSpec(patterns=("*.pyc",))
        assert not should_ignore(Path("project/module.py"), spec)

    def test_ignore_empty_spec(self) -> None:
        """空规则不忽略任何文件。"""
        spec = IgnoreSpec()
        assert not should_ignore(Path("project/.git/config"), spec)

    def test_ignore_combined(self) -> None:
        """组合规则：目录名 + glob 模式。"""
        spec = IgnoreSpec(dirs=frozenset({".git", "__pycache__"}), patterns=("*.pyc", "*.pyo"))
        assert should_ignore(Path("src/.git/HEAD"), spec)
        assert should_ignore(Path("src/__pycache__/module.pyc"), spec)
        assert should_ignore(Path("src/module.pyc"), spec)
        assert not should_ignore(Path("src/module.py"), spec)


# ============================================================================ #
# to_shutil_ignore 测试
# ============================================================================ #
class TestToShutilIgnore:
    """to_shutil_ignore 函数测试。"""

    def test_returns_callable(self) -> None:
        """返回可调用对象。"""
        spec = IgnoreSpec(dirs=frozenset({".git"}), patterns=("*.pyc",))
        ignore_fn = to_shutil_ignore(spec)
        assert callable(ignore_fn)

    def test_ignores_dirs(self, tmp_path: Path) -> None:
        """shutil.ignore_patterns 忽略目录。"""
        spec = IgnoreSpec(dirs=frozenset({".git", "__pycache__"}))
        src = tmp_path / "src"
        src.mkdir()
        (src / ".git").mkdir()
        (src / "main.py").write_text("code")
        (src / "__pycache__").mkdir()

        dst = tmp_path / "dst"
        shutil.copytree(src, dst, ignore=to_shutil_ignore(spec))

        assert (dst / "main.py").exists()
        assert not (dst / ".git").exists()
        assert not (dst / "__pycache__").exists()

    def test_ignores_patterns(self, tmp_path: Path) -> None:
        """shutil.ignore_patterns 忽略 glob 模式。"""
        spec = IgnoreSpec(patterns=("*.pyc",))
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("code")
        (src / "module.pyc").write_text("cache")

        dst = tmp_path / "dst"
        shutil.copytree(src, dst, ignore=to_shutil_ignore(spec))

        assert (dst / "main.py").exists()
        assert not (dst / "module.pyc").exists()


# ============================================================================ #
# Version 测试
# ============================================================================ #
class TestVersion:
    """Version 值对象测试。"""

    def test_construct_simple(self) -> None:
        """构造简单版本号。"""
        v = Version(major=1, minor=2, patch=3)
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.prerelease == ""
        assert v.buildmetadata == ""

    def test_str_simple(self) -> None:
        """简单版本号字符串表示。"""
        assert str(Version(1, 2, 3)) == "1.2.3"

    def test_str_with_prerelease(self) -> None:
        """带预发布的版本号字符串。"""
        v = Version(1, 2, 3, prerelease="alpha.1")
        assert str(v) == "1.2.3-alpha.1"

    def test_str_with_buildmetadata(self) -> None:
        """带构建元数据的版本号字符串。"""
        v = Version(1, 2, 3, buildmetadata="build.1")
        assert str(v) == "1.2.3+build.1"

    def test_str_full(self) -> None:
        """完整版本号字符串。"""
        v = Version(1, 2, 3, prerelease="alpha.1", buildmetadata="build.1")
        assert str(v) == "1.2.3-alpha.1+build.1"

    def test_bump_patch(self) -> None:
        """patch 递增。"""
        v = Version(1, 2, 3)
        assert v.bump(BumpPart.PATCH) == Version(1, 2, 4)

    def test_bump_minor(self) -> None:
        """minor 递增，patch 归零。"""
        v = Version(1, 2, 3)
        assert v.bump(BumpPart.MINOR) == Version(1, 3, 0)

    def test_bump_major(self) -> None:
        """major 递增，minor 和 patch 归零。"""
        v = Version(1, 2, 3)
        assert v.bump(BumpPart.MAJOR) == Version(2, 0, 0)

    def test_bump_clears_prerelease(self) -> None:
        """递增后预发布清零。"""
        v = Version(1, 2, 3, prerelease="alpha.1")
        new_v = v.bump(BumpPart.PATCH)
        assert new_v.prerelease == ""

    def test_bump_default_patch(self) -> None:
        """bump 默认递增 patch。"""
        v = Version(1, 2, 3)
        assert v.bump() == Version(1, 2, 4)

    def test_frozen(self) -> None:
        """frozen=True 不可修改。"""
        v = Version(1, 2, 3)
        with pytest.raises(AttributeError):
            v.major = 2  # type: ignore[misc]

    def test_equality(self) -> None:
        """值相等比较。"""
        assert Version(1, 2, 3) == Version(1, 2, 3)
        assert Version(1, 2, 3) != Version(1, 2, 4)


# ============================================================================ #
# parse_version 测试
# ============================================================================ #
class TestParseVersion:
    """parse_version 函数测试。"""

    def test_parse_simple(self) -> None:
        """解析简单版本号。"""
        v = parse_version("1.2.3")
        assert v == Version(1, 2, 3)

    def test_parse_from_text(self) -> None:
        """从文本中提取版本号。"""
        v = parse_version('version = "1.2.3"')
        assert v == Version(1, 2, 3)

    def test_parse_with_prerelease(self) -> None:
        """解析带预发布的版本号。"""
        v = parse_version("1.2.3-alpha.1")
        assert v == Version(1, 2, 3, prerelease="alpha.1")

    def test_parse_with_buildmetadata(self) -> None:
        """解析带构建元数据的版本号。"""
        v = parse_version("1.2.3+build.1")
        assert v == Version(1, 2, 3, buildmetadata="build.1")

    def test_parse_full(self) -> None:
        """解析完整版本号。"""
        v = parse_version("1.2.3-alpha.1+build.1")
        assert v == Version(1, 2, 3, prerelease="alpha.1", buildmetadata="build.1")

    def test_parse_no_match(self) -> None:
        """不匹配时返回 None。"""
        assert parse_version("not a version") is None

    def test_parse_empty(self) -> None:
        """空字符串返回 None。"""
        assert parse_version("") is None

    def test_parse_zero_version(self) -> None:
        """解析 0.0.0。"""
        v = parse_version("0.0.0")
        assert v == Version(0, 0, 0)

    def test_parse_large_version(self) -> None:
        """解析大版本号。"""
        v = parse_version("10.20.30")
        assert v == Version(10, 20, 30)

    def test_parse_from_init_py(self) -> None:
        """从 __init__.py 文本中提取版本号。"""
        text = '"""模块。"""\n__version__ = "0.1.0"\n'
        v = parse_version(text)
        assert v == Version(0, 1, 0)

    def test_parse_multiple_versions_returns_first(self) -> None:
        """文本中有多个版本号时返回首个。"""
        v = parse_version("version 1.0.0 and later 2.0.0")
        assert v == Version(1, 0, 0)
