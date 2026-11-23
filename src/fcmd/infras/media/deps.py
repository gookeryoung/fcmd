"""``fcmd.infras.media.deps``（由 ``infras/media.py`` 拆分）。"""

from __future__ import annotations

import importlib.util
from typing import Any

__all__ = [
    "HAS_PIL",
    "HAS_RESVG",
    "Image",
    "ImageDraw",
    "ImageFont",
    "ensure_deps",
    "ensure_pil",
    "ensure_resvg",
    "resvg_py",
]

HAS_PIL = importlib.util.find_spec("PIL") is not None


HAS_RESVG = importlib.util.find_spec("resvg_py") is not None


Image: Any = None


ImageDraw: Any = None


ImageFont: Any = None


resvg_py: Any = None


def ensure_pil() -> None:
    """惰性导入 Pillow 到模块全局；未安装时抛 ``ImportError``。"""
    global Image, ImageDraw, ImageFont
    if not HAS_PIL:
        raise ImportError("Pillow")
    if Image is None:
        from PIL import Image as _image
        from PIL import ImageDraw as _image_draw
        from PIL import ImageFont as _image_font

        Image, ImageDraw, ImageFont = _image, _image_draw, _image_font


def ensure_resvg() -> None:
    """惰性导入 resvg-py 到模块全局；未安装时抛 ``ImportError``。"""
    global resvg_py  # noqa: PLW0603 - 惰性导入需注入模块全局
    if not HAS_RESVG:
        raise ImportError("resvg-py")
    if resvg_py is None:
        import resvg_py as _resvg_py

        resvg_py = _resvg_py


def _missing_deps_text() -> str:
    """缺依赖时的提示：Pillow 是常驻依赖，resvg-py 走 ``fcmd[svg]`` extra。"""
    missing: list[str] = []
    if not HAS_PIL:
        missing.append("pillow（主包常驻依赖，请重新安装 fcmd）")
    if not HAS_RESVG:
        missing.append("resvg-py（pip install fcmd[svg]）")
    return "缺少依赖: " + " / ".join(missing)


def ensure_deps() -> None:
    """确认图标转换依赖齐全；缺失时抛 ``RuntimeError``。"""
    if not HAS_PIL or not HAS_RESVG:
        raise RuntimeError(_missing_deps_text())
    ensure_pil()
    ensure_resvg()
