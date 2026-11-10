"""可选依赖缺失提示（不注册工具）。

media / pdf 工具在可选依赖（Pillow、resvg-py、PyMuPDF、pypdf）缺失时需要同一条
提示文案；此处收敛为单一实现，避免各 base 模块各写一份。

文件名以下划线开头，``fcmd.tools`` 的工具发现会跳过本模块。
"""

from __future__ import annotations

__all__ = ["missing_dependency"]


def missing_dependency(package: str, extra: str) -> bool:
    """打印可选依赖缺失提示并返回 ``False``（供 ``_require_*`` 统一返回）。"""
    print(f"未安装 {package} 库，请安装: pip install fcmd[{extra}]")
    return False
