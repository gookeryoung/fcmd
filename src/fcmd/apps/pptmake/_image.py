"""pptmake 图片口径：填充方式、裁剪焦点与图文比例的枚举与归一化。

纯逻辑模块，供 :mod:`fcmd.apps.pptmake._core` 的模板序列化与
:mod:`fcmd.apps.pptmake._design` 的版式绘制共用，不依赖 GUI / 第三方库。
"""

from __future__ import annotations

import math
from typing import Final

from fcmd.apps.pptmake._palette import (
    IMAGE_FIT_CONTAIN,
    IMAGE_FIT_COVER,
    IMAGE_FIT_TEXT,
    IMAGE_FITS,
)

__all__ = [
    "IMAGE_FITS",
    "IMAGE_FIT_CONTAIN",
    "IMAGE_FIT_COVER",
    "IMAGE_FIT_TEXT",
    "IMAGE_FOCUSES",
    "IMAGE_FOCUS_BOTTOM",
    "IMAGE_FOCUS_CENTER",
    "IMAGE_FOCUS_LEFT",
    "IMAGE_FOCUS_RIGHT",
    "IMAGE_FOCUS_TEXT",
    "IMAGE_FOCUS_TOP",
    "IMAGE_RATIOS",
    "IMAGE_RATIO_40_60",
    "IMAGE_RATIO_50_50",
    "IMAGE_RATIO_FULL",
    "IMAGE_RATIO_GRID_2",
    "IMAGE_RATIO_GRID_3",
    "IMAGE_RATIO_MAX",
    "IMAGE_RATIO_MIN",
    "IMAGE_RATIO_TEXT",
    "image_ratio_cells",
    "image_ratio_full",
    "image_ratio_share",
    "normalize_image_fit",
    "normalize_image_focus",
    "normalize_image_ratio",
]

IMAGE_FOCUS_CENTER: Final[str] = "center"
IMAGE_FOCUS_TOP: Final[str] = "top"
IMAGE_FOCUS_BOTTOM: Final[str] = "bottom"
IMAGE_FOCUS_LEFT: Final[str] = "left"
IMAGE_FOCUS_RIGHT: Final[str] = "right"
IMAGE_FOCUSES: Final[tuple[str, ...]] = (
    IMAGE_FOCUS_CENTER,
    IMAGE_FOCUS_TOP,
    IMAGE_FOCUS_BOTTOM,
    IMAGE_FOCUS_LEFT,
    IMAGE_FOCUS_RIGHT,
)
IMAGE_FOCUS_TEXT: Final[dict[str, str]] = {
    IMAGE_FOCUS_CENTER: "居中",
    IMAGE_FOCUS_TOP: "靠上",
    IMAGE_FOCUS_BOTTOM: "靠下",
    IMAGE_FOCUS_LEFT: "靠左",
    IMAGE_FOCUS_RIGHT: "靠右",
}

# ---------------------------------------------------------------------- #
# 图文比例
# ---------------------------------------------------------------------- #
IMAGE_RATIO_FULL: Final[str] = "full"
IMAGE_RATIO_40_60: Final[str] = "40-60"
IMAGE_RATIO_50_50: Final[str] = "50-50"
IMAGE_RATIO_GRID_2: Final[str] = "grid-2"
IMAGE_RATIO_GRID_3: Final[str] = "grid-3"
IMAGE_RATIOS: Final[tuple[str, ...]] = (
    IMAGE_RATIO_FULL,
    IMAGE_RATIO_40_60,
    IMAGE_RATIO_50_50,
    IMAGE_RATIO_GRID_2,
    IMAGE_RATIO_GRID_3,
)
IMAGE_RATIO_TEXT: Final[dict[str, str]] = {
    IMAGE_RATIO_FULL: "全出血大图",
    IMAGE_RATIO_40_60: "文 40 / 图 60",
    IMAGE_RATIO_50_50: "图文各半",
    IMAGE_RATIO_GRID_2: "双图栅格",
    IMAGE_RATIO_GRID_3: "三图栅格",
}
# 数值微调的合法区间（图片区占内容宽度的比例）。
IMAGE_RATIO_MIN: Final[float] = 0.2
IMAGE_RATIO_MAX: Final[float] = 0.8
_DEFAULT_IMAGE_SHARE: Final[float] = 0.6


def normalize_image_fit(value: object) -> str:
    """校验图片填充方式（cover / contain），缺省返回裁剪填满。"""
    if value is None or value == "":
        return IMAGE_FIT_COVER
    text = str(value).strip().lower()
    if text not in IMAGE_FITS:
        raise ValueError(f"image_fit 需为 {IMAGE_FIT_COVER} / {IMAGE_FIT_CONTAIN}：{value!r}")
    return text


def normalize_image_focus(value: object) -> str:
    """校验图片裁剪焦点（center / top / bottom / left / right）。"""
    if value is None or value == "":
        return IMAGE_FOCUS_CENTER
    text = str(value).strip().lower()
    if text not in IMAGE_FOCUSES:
        raise ValueError(f"image_focus 需为 {' / '.join(IMAGE_FOCUSES)}：{value!r}")
    return text


def _ratio_text(number: float) -> str:
    """把数值比例 clamp 到合法区间并格式化为两位小数。"""
    if not math.isfinite(number):
        raise ValueError(f"image_ratio 需为有限数值：{number!r}")
    clamped = min(max(number, IMAGE_RATIO_MIN), IMAGE_RATIO_MAX)
    return f"{clamped:.2f}"


def normalize_image_ratio(value: object) -> str:
    """校验图文比例：5 个预设名，或 0.20–0.80 的数值（超界按 clamp 处理）。"""
    if value is None or value == "":
        return ""
    expected = f"{' / '.join(IMAGE_RATIOS)}，或 {IMAGE_RATIO_MIN:.2f}–{IMAGE_RATIO_MAX:.2f}"
    if isinstance(value, bool):
        raise ValueError(f"image_ratio 需为 {expected}：{value!r}")
    if isinstance(value, (int, float)):
        return _ratio_text(float(value))
    text = str(value).strip().lower()
    if text in IMAGE_RATIOS:
        return text
    try:
        return _ratio_text(float(text))
    except ValueError as exc:
        raise ValueError(f"image_ratio 需为 {expected}：{value!r}") from exc


def image_ratio_share(value: object) -> float:
    """图片区占内容宽度的比例；``full`` 为 1.0，未知值回落 0.6。"""
    text = str(value or "").strip().lower()
    if text == IMAGE_RATIO_FULL:
        return 1.0
    if text == IMAGE_RATIO_50_50:
        return 0.5
    if text == IMAGE_RATIO_40_60 or text in (IMAGE_RATIO_GRID_2, IMAGE_RATIO_GRID_3) or not text:
        return _DEFAULT_IMAGE_SHARE
    try:
        number = float(text)
    except ValueError:
        return _DEFAULT_IMAGE_SHARE
    if not math.isfinite(number):
        return _DEFAULT_IMAGE_SHARE
    return min(max(number, IMAGE_RATIO_MIN), IMAGE_RATIO_MAX)


def image_ratio_cells(value: object) -> int:
    """图片栅格格数：``grid-2`` / ``grid-3`` 为 2 / 3，其余为 0。"""
    text = str(value or "").strip().lower()
    if text == IMAGE_RATIO_GRID_2:
        return 2
    if text == IMAGE_RATIO_GRID_3:
        return 3
    return 0


def image_ratio_full(value: object) -> bool:
    """是否为全出血大图比例。"""
    return str(value or "").strip().lower() == IMAGE_RATIO_FULL
