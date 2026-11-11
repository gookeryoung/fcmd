"""PE 字节读取、偏移换算与校验和（纯逻辑，不依赖 Tk / CLI）。

本模块只做三件事：

- :class:`Reader` 提供带边界检查的整数 / 字符串读取，越界统一抛
  :class:`PEError`，错误信息包含文件偏移，避免裸 ``struct.error``；
- :func:`rva_to_offset` 把节表内的 RVA 换算为文件偏移（落在节的虚拟填充区
  或任何节之外时明确报错）；
- :func:`compute_checksum` 按 PE/COFF 规范重算映像校验和，
  :func:`format_hex_dump` 生成定宽十六进制文本。

模块内不出现任何 Windows API 调用，可在任意平台单测。
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    class SectionSpan(Protocol):
        """``rva_to_offset`` 需要的节字段（与 ``_pe.Section`` 结构兼容）。"""

        @property
        def name(self) -> str: ...

        @property
        def virtual_address(self) -> int: ...

        @property
        def virtual_size(self) -> int: ...

        @property
        def size_of_raw_data(self) -> int: ...

        @property
        def pointer_to_raw_data(self) -> int: ...


__all__ = [
    "DEFAULT_HEX_LIMIT",
    "PEError",
    "Reader",
    "compute_checksum",
    "format_hex_dump",
    "rva_to_offset",
]

DEFAULT_HEX_LIMIT = 256
"""十六进制视图默认展示的字节数。"""


class PEError(ValueError):
    """PE 解析 / 读写的业务错误（CLI 直接打印，不抛堆栈）。"""


class Reader:
    """带边界检查的只读字节读取器。"""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.size = len(data)

    def require(self, offset: int, size: int, what: str = "数据") -> None:
        """确认 ``[offset, offset + size)`` 落在文件内，否则抛 :class:`PEError`。"""
        if offset < 0 or size < 0:
            raise PEError(f"{what} 的长度非法：偏移 {offset}，长度 {size}")
        if offset + size > self.size:
            raise PEError(f"{what} 越界：偏移 0x{offset:X} + {size} 字节，超出文件大小 0x{self.size:X}")

    def read(self, offset: int, size: int, what: str = "数据") -> bytes:
        """读取 ``size`` 字节。"""
        self.require(offset, size, what)
        return self.data[offset : offset + size]

    def u8(self, offset: int, what: str = "字节") -> int:
        """读取 1 字节无符号整数。"""
        self.require(offset, 1, what)
        return self.data[offset]

    def u16(self, offset: int, what: str = "16 位整数") -> int:
        """读取 2 字节小端无符号整数。"""
        self.require(offset, 2, what)
        return struct.unpack_from("<H", self.data, offset)[0]

    def u32(self, offset: int, what: str = "32 位整数") -> int:
        """读取 4 字节小端无符号整数。"""
        self.require(offset, 4, what)
        return struct.unpack_from("<I", self.data, offset)[0]

    def u64(self, offset: int, what: str = "64 位整数") -> int:
        """读取 8 字节小端无符号整数。"""
        self.require(offset, 8, what)
        return struct.unpack_from("<Q", self.data, offset)[0]

    def cstring(self, offset: int, what: str = "字符串", limit: int = 512) -> str:
        """读取以 NUL 结尾的 Latin-1 字符串，最多扫描 ``limit`` 字节。"""
        self.require(offset, 1, what)
        stop = min(self.size, offset + limit)
        end = self.data.find(b"\x00", offset, stop)
        if end < 0:
            raise PEError(f"{what} 未在 {limit} 字节内结束（偏移 0x{offset:X}）")
        return self.data[offset:end].decode("latin-1")


def rva_to_offset(
    rva: int,
    sections: Iterable[SectionSpan],
    *,
    size_of_headers: int = 0,
    size: int = 0,
    what: str = "RVA",
) -> int:
    """把 RVA 换算为文件偏移。

    Parameters
    ----------
    rva:
        相对虚拟地址。
    sections:
        节表（读取 ``virtual_address`` / ``virtual_size`` / ``size_of_raw_data`` /
        ``pointer_to_raw_data``）。
    size_of_headers:
        头部大小；RVA 小于该值时直接视为文件偏移（头部映射）。
    size:
        需要连续读取的字节数，用于确认不跨越节的原始数据边界。
    what:
        错误信息里的字段名。
    """
    if rva < 0:
        raise PEError(f"{what} 不能为负：{rva}")
    for section in sections:
        span = max(section.virtual_size, section.size_of_raw_data)
        if section.virtual_address <= rva < section.virtual_address + span:
            delta = rva - section.virtual_address
            if delta + size > section.size_of_raw_data:
                raise PEError(f"{what} 0x{rva:X} 落在节 {section.name!r} 的未初始化区域，该地址没有对应的文件字节")
            return section.pointer_to_raw_data + delta
    if rva < size_of_headers:
        return rva
    raise PEError(f"{what} 0x{rva:X} 不在任何节内")


def compute_checksum(data: bytes, checksum_offset: int) -> int:
    """按 PE/COFF 规范计算映像校验和（计算时把校验和字段视为 0）。

    Parameters
    ----------
    data:
        完整文件字节。
    checksum_offset:
        可选头中 ``CheckSum`` 字段的文件偏移（4 字节）。
    """
    if checksum_offset < 0 or checksum_offset + 4 > len(data):
        raise PEError(f"校验和字段偏移非法：0x{checksum_offset:X}")
    total = 0
    end = len(data) - (len(data) % 2)
    for index in range(0, end, 2):
        low = 0 if checksum_offset <= index < checksum_offset + 4 else data[index]
        high = 0 if checksum_offset <= index + 1 < checksum_offset + 4 else data[index + 1]
        total += low | (high << 8)
        total = (total & 0xFFFF) + (total >> 16)
    if len(data) % 2:
        last = len(data) - 1
        low = 0 if checksum_offset <= last < checksum_offset + 4 else data[last]
        total += low
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + len(data)) & 0xFFFFFFFF


def format_hex_dump(data: bytes, *, offset: int = 0, limit: int | None = DEFAULT_HEX_LIMIT) -> str:
    """把字节格式化为 ``偏移  HEX  ASCII`` 的定宽十六进制文本。

    ``limit`` 为 ``None`` 时输出全部字节；超出时截断并在末尾注明总长度。
    """
    if not data:
        return "(无数据)"
    truncated = limit is not None and len(data) > limit
    visible = data if limit is None else data[: max(limit, 0)]
    lines: list[str] = []
    for start in range(0, len(visible), 16):
        chunk = visible[start : start + 16]
        hex_part = " ".join(f"{byte:02X}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset + start:08X}  {hex_part:<47}  |{ascii_part}|")
    if truncated:
        lines.append(f"… 仅显示前 {len(visible)} 字节，共 {len(data)} 字节")
    return "\n".join(lines)
