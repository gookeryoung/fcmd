"""文件过滤基础件测试：IgnoreSpec / should_ignore / 默认规则 / to_shutil_ignore。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fcmd.infras.filefilter import (
    DEFAULT_IGNORE_DIRS,
    DEFAULT_IGNORE_EXTS,
    DEFAULT_IGNORE_SPEC,
    IgnoreSpec,
    should_ignore,
    should_skip_parts,
    to_shutil_ignore,
)


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

    def test_ignore_glob_dir_in_parts(self) -> None:
        """glob 模式对目录组件生效（如 *.egg-info 目录下的文件）。"""
        spec = IgnoreSpec.from_iterable(["*.egg-info"])
        assert should_ignore(Path("proj/fcmd.egg-info/PKG-INFO"), spec)
        assert not should_ignore(Path("proj/fcmd.egg/PKG-INFO"), spec)


# ============================================================================ #
# 默认忽略规则测试
# ============================================================================ #
class TestDefaultIgnore:
    """DEFAULT_IGNORE_* 常量与 should_skip_parts 测试。"""

    def test_default_dirs(self) -> None:
        """默认目录规则包含常见构建/缓存目录与 *.egg-info 通配。"""
        assert ".git" in DEFAULT_IGNORE_DIRS
        assert "__pycache__" in DEFAULT_IGNORE_DIRS
        assert "*.egg-info" in DEFAULT_IGNORE_DIRS
        assert "*.egg-info" in DEFAULT_IGNORE_SPEC.patterns

    def test_default_exts(self) -> None:
        """默认扩展名包含压缩包与字节码。"""
        assert {".zip", ".gz", ".pyc", ".pyo"} <= DEFAULT_IGNORE_EXTS

    def test_skip_parts_default_spec(self) -> None:
        """未传 spec 时使用默认规则。"""
        assert should_skip_parts(("proj", ".git", "config"))
        assert should_skip_parts(("proj", "fcmd.egg-info", "PKG-INFO"))
        assert not should_skip_parts(("proj", "src", "main.py"))


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
