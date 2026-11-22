"""依赖缺失提示（不注册工具）。

media / pdf 工具在可选依赖（resvg-py、PyMuPDF、pypdf）或常驻依赖（Pillow、
psutil 等，见 ``[project.dependencies]``）缺失时需要同一条提示文案；此处收敛为
单一实现，避免各 base 模块各写一份。

常驻依赖传 ``extra=None``：缺失说明安装不完整，提示重装主包，而不是让人去装
一个不存在的 ``fcmd[extra]`` 分组。

文件名以下划线开头，``fcmd.tools`` 的工具发现会跳过本模块。
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = ["lazy_import", "missing_dependency"]


def missing_dependency(package: str, extra: str | None) -> bool:
    """打印依赖缺失提示并返回 ``False``（供 ``_require_*`` 统一返回）。

    ``extra`` 为 ``None`` 表示该包属于主包常驻依赖：提示重新安装 fcmd。
    """
    if extra is None:
        print(f"未安装 {package} 库（主包常驻依赖，请重新安装 fcmd）")
    else:
        print(f"未安装 {package} 库，请安装: pip install fcmd[{extra}]")
    return False


def lazy_import(module: str, package: str, extra: str | None, available: bool) -> Any | None:
    """按 ``available`` 惰性导入可选依赖；不可用时打印统一提示并返回 ``None``。

    ``available`` 由调用方持有的 ``HAS_*`` 常量传入（保留可 patch 的测试接缝），
    本函数只负责「缺失提示 + 真正 import」这两步，避免各子工具各写一份。
    """
    if not available:
        missing_dependency(package, extra)
        return None
    return importlib.import_module(module)
