"""跨层格式化函数：人类可读的字节数等纯函数。"""

from __future__ import annotations

__all__ = ["format_bytes"]


def format_bytes(size: int, *, binary: bool = False) -> str:
    """将字节数格式化为人类可读字符串（形如 ``1.5 GB`` / ``1.5 GiB``）。

    Parameters
    ----------
    size:
        字节数；小于 1024 时按整数显示（``512 B``）。
    binary:
        为 ``True`` 时使用 IEC 单位（``KiB`` / ``MiB`` / ``GiB`` …），
        否则使用 ``KB`` / ``MB`` / ``GB`` …；两者都按 1024 进制换算。
    """
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB") if binary else ("B", "KB", "MB", "GB", "TB", "PB")
    value: float = float(size)
    for unit in units[:-1]:
        if value < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {units[-1]}"
