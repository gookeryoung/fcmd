"""``fcmd env`` / ``fcmd doctor`` 共享的纯函数辅助。

供 :mod:`fcmd.cli._builtins.env_cmd` 与 :mod:`fcmd.cli._doctor_helpers`
复用。本模块以下划线开头，``ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["collect_optional_deps_status"]

# 可选依赖清单：(extra 分组, import 包名)
# extra 对应 pyproject.toml 的 [project.optional-dependencies]
# 包名用于 __import__ 探测安装状态与版本
_OPTIONAL_DEPS: tuple[tuple[str, str], ...] = (
    ("img", "PIL"),
    ("pdf", "fitz"),
    ("pdf", "pypdf"),
    ("ocr", "pytesseract"),
)


def collect_optional_deps_status() -> list[dict[str, Any]]:
    """收集可选依赖的安装状态与版本。

    返回列表，每项包含 extra / package / installed / version（已安装时）。

    用 ``__import__`` 探测而非 ``importlib.util.find_spec``，因为需要读取
    ``__version__`` 属性；导入失败即视为未安装。
    """
    deps: list[dict[str, Any]] = []
    for extra, package in _OPTIONAL_DEPS:
        try:
            mod = __import__(package)
            version = getattr(mod, "__version__", "")
            deps.append({"extra": extra, "package": package, "installed": True, "version": version})
        except ImportError:
            deps.append({"extra": extra, "package": package, "installed": False, "version": ""})
    return deps
