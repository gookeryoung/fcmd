"""fcmd 基础冒烟测试."""

from __future__ import annotations

import fcmd


def test_version_is_string() -> None:
    """__version__ 应为非空字符串."""
    assert isinstance(fcmd.__version__, str)
    assert fcmd.__version__


def test_package_importable() -> None:
    """包应可正常导入."""
    assert hasattr(fcmd, "__all__")
    assert "__version__" in fcmd.__all__
