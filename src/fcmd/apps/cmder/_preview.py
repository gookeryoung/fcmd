"""cmderw 预览增强的纯逻辑：编码探测 / 解码、十六进制转储与图片缩放倍率。"""

from __future__ import annotations

import math

from fcmd.infras.textio import decode_text, detect_encoding, has_text_bom

__all__ = [
    "ENCODINGS",
    "ZOOM_MAX",
    "decode_text",
    "detect_encoding",
    "fit_factor",
    "has_text_bom",
    "hex_dump",
    "zoom_in",
    "zoom_label",
    "zoom_out",
]

ENCODINGS = ("auto", "utf-8", "utf-8-sig", "gb18030", "utf-16", "latin-1")
"""快速查看可选的解码编码；``auto`` 表示自动探测。"""

ZOOM_MAX = 16
"""图片最大缩小倍率（``PhotoImage.subsample`` 的整数参数）。"""


def hex_dump(data: bytes, limit: int = 4096) -> str:
    """生成 ``offset  hex  ascii`` 三栏十六进制视图。"""
    lines: list[str] = []
    for offset in range(0, min(len(data), limit), 16):
        row = data[offset : offset + 16]
        hexpart = " ".join(f"{byte:02x}" for byte in row)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in row)
        lines.append(f"{offset:08x}  {hexpart:<47}  {ascii_part}")
    return "\n".join(lines)


def fit_factor(width: int, height: int, max_width: int, max_height: int) -> int:
    """计算适应窗口所需的 ``subsample`` 整数倍率（至少 1）。"""
    if width <= 0 or height <= 0 or max_width <= 0 or max_height <= 0:
        return 1
    return max(1, math.ceil(width / max_width), math.ceil(height / max_height))


def zoom_in(factor: int) -> int:
    """放大一档：倍率减半，最小 1。"""
    return max(1, factor // 2)


def zoom_out(factor: int) -> int:
    """缩小一档：倍率翻倍，最大 ``ZOOM_MAX``。"""
    return min(ZOOM_MAX, max(2, factor * 2))


def zoom_label(factor: int) -> str:
    """缩放倍率的显示文本。"""
    return "1:1" if factor <= 1 else f"1:{factor}"
