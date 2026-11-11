"""版本号基础件测试：Version / BumpPart / parse_version。"""

from __future__ import annotations

import pytest

from fcmd.infras.version import BumpPart, Version, parse_version


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
