"""colortool - 颜色换算工具。

提供 HEX、RGB、HSL 三种颜色表示之间的换算。所有换算基于纯标准库实现。

- HEX: ``#RRGGBB`` 或 ``RRGGBB``（6 位十六进制，大小写不敏感）
- RGB: ``(r, g, b)``，各分量 0-255 整数
- HSL: ``(h, s, l)``，h 为 0-360 度，s/l 为 0-100 百分比

示例
----
    fcmd colortool hex2rgb "#ff5733"          # HEX 转 RGB
    fcmd colortool rgb2hex 255 87 51          # RGB 转 HEX
    fcmd colortool rgb2hsl 255 87 51          # RGB 转 HSL
    fcmd colortool hsl2rgb 11 100 60          # HSL 转 RGB
"""

from __future__ import annotations

import fcmd

__all__ = [
    "hex_to_rgb",
    "hsl_to_rgb",
    "rgb_to_hex",
    "rgb_to_hsl",
]


# ============================================================================
# 公共函数
# ============================================================================


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """十六进制颜色转 RGB。

    Parameters
    ----------
    hex_color:
        十六进制颜色字符串（``#RRGGBB`` 或 ``RRGGBB``，大小写不敏感）

    Returns
    -------
    tuple[int, int, int]
        ``(r, g, b)``，各分量 0-255 整数

    Raises
    ------
    ValueError
        格式无效或非十六进制字符时
    """
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"HEX 颜色须为 6 位十六进制（#RRGGBB），收到: {hex_color!r}")
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
    except ValueError as exc:
        raise ValueError(f"HEX 颜色含非十六进制字符: {hex_color!r}") from exc
    return (r, g, b)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """RGB 转十六进制颜色。

    Parameters
    ----------
    r:
        红色分量（0-255）
    g:
        绿色分量（0-255）
    b:
        蓝色分量（0-255）

    Returns
    -------
    str
        ``#RRGGBB`` 格式（小写）

    Raises
    ------
    ValueError
        分量超出 0-255 范围时
    """
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    return f"#{r:02x}{g:02x}{b:02x}"


def rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """RGB 转 HSL。

    Parameters
    ----------
    r:
        红色分量（0-255）
    g:
        绿色分量（0-255）
    b:
        蓝色分量（0-255）

    Returns
    -------
    tuple[float, float, float]
        ``(h, s, l)``，h 为 0-360 度，s/l 为 0-100 百分比

    Raises
    ------
    ValueError
        分量超出 0-255 范围时
    """
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")

    # 归一化到 [0, 1]
    rn, gn, bn = r / 255, g / 255, b / 255
    cmax = max(rn, gn, bn)
    cmin = min(rn, gn, bn)
    delta = cmax - cmin

    # 亮度
    light = (cmax + cmin) / 2

    # 饱和度
    if delta == 0:
        s = 0.0
    else:
        s = delta / (1 - abs(2 * light - 1)) if (1 - abs(2 * light - 1)) != 0 else 0.0

    # 色相
    if delta == 0:
        h = 0.0
    elif cmax == rn:
        h = 60 * (((gn - bn) / delta) % 6)
    elif cmax == gn:
        h = 60 * (((bn - rn) / delta) + 2)
    else:  # cmax == bn
        h = 60 * (((rn - gn) / delta) + 4)

    if h < 0:
        h += 360

    return (round(h, 2), round(s * 100, 2), round(light * 100, 2))


def hsl_to_rgb(h: float, s: float, light: float) -> tuple[int, int, int]:
    """HSL 转 RGB。

    Parameters
    ----------
    h:
        色相（0-360 度）
    s:
        饱和度（0-100 百分比）
    light:
        亮度（0-100 百分比）

    Returns
    -------
    tuple[int, int, int]
        ``(r, g, b)``，各分量 0-255 整数

    Raises
    ------
    ValueError
        ``h``/``s``/``light`` 超出范围时
    """
    if not 0 <= h <= 360:
        raise ValueError(f"h 须在 0-360 范围: {h}")
    if not 0 <= s <= 100:
        raise ValueError(f"s 须在 0-100 范围: {s}")
    if not 0 <= light <= 100:
        raise ValueError(f"l 须在 0-100 范围: {light}")

    # 归一化到 [0, 1]
    hn = h / 360
    sn = s / 100
    ln = light / 100

    c = (1 - abs(2 * ln - 1)) * sn
    x = c * (1 - abs((hn * 6) % 2 - 1))
    m = ln - c / 2

    if hn < 1 / 6:
        r1, g1, b1 = c, x, 0
    elif hn < 2 / 6:
        r1, g1, b1 = x, c, 0
    elif hn < 3 / 6:
        r1, g1, b1 = 0, c, x
    elif hn < 4 / 6:
        r1, g1, b1 = 0, x, c
    elif hn < 5 / 6:
        r1, g1, b1 = x, 0, c
    else:
        r1, g1, b1 = c, 0, x

    r = round((r1 + m) * 255)
    g = round((g1 + m) * 255)
    b = round((b1 + m) * 255)
    return (r, g, b)


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("colortool", subcommand="hex2rgb", help="HEX 转 RGB")
def hex2rgb_cmd(hex_color: str) -> None:
    """十六进制颜色转 RGB。

    Parameters
    ----------
    hex_color:
        十六进制颜色字符串（``#RRGGBB`` 或 ``RRGGBB``）
    """
    try:
        r, g, b = hex_to_rgb(hex_color)
    except ValueError as exc:
        print(str(exc))
        return
    print(f"{r} {g} {b}")


@fcmd.tool("colortool", subcommand="rgb2hex", help="RGB 转 HEX")
def rgb2hex_cmd(r: int, g: int, b: int) -> None:
    """RGB 转十六进制颜色。

    用法：``fcmd colortool rgb2hex <r> <g> <b>``

    Parameters
    ----------
    r:
        红色分量（0-255）
    g:
        绿色分量（0-255）
    b:
        蓝色分量（0-255）
    """
    try:
        result = rgb_to_hex(r, g, b)
    except ValueError as exc:
        print(str(exc))
        return
    print(result)


@fcmd.tool("colortool", subcommand="rgb2hsl", help="RGB 转 HSL")
def rgb2hsl_cmd(r: int, g: int, b: int) -> None:
    """RGB 转 HSL。

    用法：``fcmd colortool rgb2hsl <r> <g> <b>``

    Parameters
    ----------
    r:
        红色分量（0-255）
    g:
        绿色分量（0-255）
    b:
        蓝色分量（0-255）
    """
    try:
        h, s, light = rgb_to_hsl(r, g, b)
    except ValueError as exc:
        print(str(exc))
        return
    print(f"{h} {s} {light}")


@fcmd.tool("colortool", subcommand="hsl2rgb", help="HSL 转 RGB")
def hsl2rgb_cmd(h: float, s: float, light: float) -> None:
    """HSL 转 RGB。

    用法：``fcmd colortool hsl2rgb <h> <s> <light>``

    Parameters
    ----------
    h:
        色相（0-360 度）
    s:
        饱和度（0-100 百分比）
    light:
        亮度（0-100 百分比）
    """
    try:
        r, g, b = hsl_to_rgb(h, s, light)
    except ValueError as exc:
        print(str(exc))
        return
    print(f"{r} {g} {b}")


@fcmd.main("colortool")
def main() -> None:
    """``colortool`` 入口：等价于 ``fcmd colortool <args>``。"""


if __name__ == "__main__":
    main()
