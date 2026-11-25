"""模板与放映稿的默认路径推导：``demo.pptmtpl.json`` ↔ ``demo.pptx``。

拆出本模块是为了让 :mod:`fcmd.apps.pptmake._core` 保持在单文件行数预算内；
对外仍通过 ``_core`` 重导出。
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

__all__ = ["TEMPLATE_SUFFIX", "default_pptx_path", "default_template_path"]

TEMPLATE_SUFFIX: Final[str] = ".pptmtpl.json"


def default_pptx_path(template_path: Path) -> Path:
    """由模板路径推导同名 ``.pptx``（``demo.pptmtpl.json`` → ``demo.pptx``）。"""
    name = template_path.name
    for suffix in (TEMPLATE_SUFFIX, ".json"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    else:
        name = template_path.stem
    return template_path.with_name(f"{name}.pptx")


def default_template_path(pptx_path: Path) -> Path:
    """由 PPTX 路径推导模板文件路径（``demo.pptx`` → ``demo.pptmtpl.json``）。"""
    return pptx_path.with_name(f"{pptx_path.stem}{TEMPLATE_SUFFIX}")
