"""``fcmd env`` / ``fcmd doctor`` 共享的纯函数辅助。

供 :mod:`fcmd.cli._builtins.env_cmd` 与 :mod:`fcmd.cli._doctor_helpers`
复用。本模块以下划线开头，``ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import importlib.util
from typing import Any

__all__ = ["collect_optional_deps_status"]

# 可选依赖清单：(extra 分组, import 包名, 分发包名)
# extra 对应 pyproject.toml 的 [project.optional-dependencies]
# import 包名用于 find_spec 探测；分发包名用于 metadata 读取版本
_OPTIONAL_DEPS: tuple[tuple[str, str, str], ...] = (
    ("img", "PIL", "pillow"),
    ("pdf", "fitz", "pymupdf"),
    ("pdf", "pypdf", "pypdf"),
    ("ocr", "pytesseract", "pytesseract"),
)


def collect_optional_deps_status() -> list[dict[str, Any]]:
    """收集可选依赖的安装状态与版本。

    返回列表，每项包含 extra / package / installed / version（已安装时）。

    仅用 ``find_spec`` + ``importlib.metadata`` 探测，**不真正导入模块**：
    ``fcmd env`` / ``fcmd doctor`` 属只读诊断，导入 PyMuPDF 等重型依赖会
    破坏三层懒加载（且部分原生库在导入期即崩溃）。
    """
    deps: list[dict[str, Any]] = []
    for extra, package, distribution in _OPTIONAL_DEPS:
        try:
            installed = importlib.util.find_spec(package) is not None
        except (ImportError, ValueError):
            installed = False
        version = ""
        if installed:
            with contextlib.suppress(importlib.metadata.PackageNotFoundError):
                version = importlib.metadata.version(distribution)
        deps.append({"extra": extra, "package": package, "installed": installed, "version": version})
    return deps
