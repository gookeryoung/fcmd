"""peview 测试夹具：合成 PE32 / PE32+ 文件。

夹具不依赖 Windows 工具链，直接按 PE/COFF 规范拼字节，覆盖 DOS/NT/可选头、
两个节、导入 / 延迟导入 / 导出（含转发器）/ 资源 / 重定位 / 调试 / 签名与
overlay。损坏输入由各测试在 ``bytearray`` 上就地修改。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

OPT32 = 0x98
OPT64 = 0x98


def put_u16(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", data, offset, value)


def put_u32(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", data, offset, value)


def put_u64(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<Q", data, offset, value)


def put_str(data: bytearray, offset: int, text: str) -> None:
    raw = text.encode("latin-1") + b"\x00"
    data[offset : offset + len(raw)] = raw


def pe_checksum(data: bytes, checksum_offset: int) -> int:
    """独立实现的 PE 校验和，避免夹具与被测代码共用同一份算法。"""
    total = 0
    for index in range(0, len(data) - (len(data) % 2), 2):
        if checksum_offset <= index < checksum_offset + 4:
            word = 0
        else:
            word = data[index] | (data[index + 1] << 8)
        total = (total + word) & 0xFFFFFFFF
        total = (total & 0xFFFF) + (total >> 16)
    if len(data) % 2:
        last = len(data) - 1
        total += 0 if checksum_offset <= last < checksum_offset + 4 else data[last]
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + len(data)) & 0xFFFFFFFF


def _dos_and_signature(data: bytearray) -> None:
    put_u16(data, 0, 0x5A4D)
    put_u16(data, 2, 0x90)
    put_u16(data, 4, 3)
    put_u16(data, 6, 0)
    put_u16(data, 8, 4)
    put_u16(data, 10, 0)
    put_u16(data, 12, 0xFFFF)
    put_u16(data, 14, 0)
    put_u16(data, 16, 0xB8)
    put_u16(data, 18, 0)
    put_u16(data, 20, 0)
    put_u16(data, 22, 0)
    put_u16(data, 24, 0x40)
    put_u16(data, 26, 0)
    put_u32(data, 60, 0x80)
    data[0x80:0x84] = b"PE\x00\x00"


def build_pe32() -> bytes:
    """构造一个带完整目录的 PE32 映像（0xA10 字节，含 16 字节 overlay）。"""
    data = bytearray(0xA10)
    _dos_and_signature(data)
    struct.pack_into("<HHIIIHH", data, 0x84, 0x014C, 2, 0x5F5E1000, 0, 0, 0xE0, 0x0102)

    opt = OPT32
    put_u16(data, opt, 0x10B)
    data[opt + 2] = 14
    data[opt + 3] = 0
    put_u32(data, opt + 4, 0x200)
    put_u32(data, opt + 8, 0x600)
    put_u32(data, opt + 12, 0)
    put_u32(data, opt + 16, 0x1000)
    put_u32(data, opt + 20, 0x1000)
    put_u32(data, opt + 24, 0x2000)
    put_u32(data, opt + 28, 0x400000)
    put_u32(data, opt + 32, 0x1000)
    put_u32(data, opt + 36, 0x200)
    put_u16(data, opt + 40, 6)
    put_u16(data, opt + 42, 0)
    put_u16(data, opt + 44, 0)
    put_u16(data, opt + 46, 0)
    put_u16(data, opt + 48, 6)
    put_u16(data, opt + 50, 0)
    put_u32(data, opt + 52, 0)
    put_u32(data, opt + 56, 0x3000)
    put_u32(data, opt + 60, 0x200)
    put_u32(data, opt + 64, 0)
    put_u16(data, opt + 68, 3)
    put_u16(data, opt + 70, 0x8140)
    put_u32(data, opt + 72, 0x100000)
    put_u32(data, opt + 76, 0x1000)
    put_u32(data, opt + 80, 0x100000)
    put_u32(data, opt + 84, 0x1000)
    put_u32(data, opt + 88, 0)
    put_u32(data, opt + 92, 16)

    directories = (
        (0x2000, 0xA0),  # 导出
        (0x2100, 0x28),  # 导入
        (0x2200, 0x60),  # 资源
        (0, 0),  # 异常
        (0x9E0, 8),  # 安全目录（文件偏移）
        (0x2300, 12),  # 重定位
        (0x2400, 28),  # 调试
        (0, 0),
        (0, 0),
        (0, 0),
        (0, 0),
        (0, 0),
        (0, 0),
        (0x2500, 0x40),  # 延迟导入
        (0, 0),
        (0, 0),
    )
    for index, (rva, size) in enumerate(directories):
        put_u32(data, opt + 96 + index * 8, rva)
        put_u32(data, opt + 100 + index * 8, size)

    section_table = opt + 0xE0
    data[section_table : section_table + 8] = b".text\x00\x00\x00"
    put_u32(data, section_table + 8, 0x100)
    put_u32(data, section_table + 12, 0x1000)
    put_u32(data, section_table + 16, 0x200)
    put_u32(data, section_table + 20, 0x200)
    put_u32(data, section_table + 36, 0x60000020)
    second = section_table + 40
    data[second : second + 8] = b".rdata\x00\x00"
    put_u32(data, second + 8, 0x600)
    put_u32(data, second + 12, 0x2000)
    put_u32(data, second + 16, 0x600)
    put_u32(data, second + 20, 0x400)
    put_u32(data, second + 36, 0x40000040)

    _write_exports(data)
    _write_imports(data)
    _write_resources(data)
    _write_relocations(data)
    _write_debug(data)
    _write_delay_imports(data)

    put_u32(data, 0x9E0, 8)
    put_u16(data, 0x9E4, 0x0200)
    put_u16(data, 0x9E6, 2)
    data[0xA00:0xA10] = b"\xcc" * 16

    put_u32(data, opt + 64, pe_checksum(bytes(data), opt + 64))
    return bytes(data)


def _write_exports(data: bytearray) -> None:
    put_u32(data, 0x400 + 12, 0x2060)
    put_u32(data, 0x400 + 16, 1)
    put_u32(data, 0x400 + 20, 3)
    put_u32(data, 0x400 + 24, 2)
    put_u32(data, 0x400 + 28, 0x2070)
    put_u32(data, 0x400 + 32, 0x2080)
    put_u32(data, 0x400 + 36, 0x2090)
    put_str(data, 0x440, "KERNEL32.Sleep")
    put_str(data, 0x460, "peview_test.dll")
    put_u32(data, 0x470, 0x1000)
    put_u32(data, 0x474, 0x1010)
    put_u32(data, 0x478, 0x2040)
    put_u32(data, 0x480, 0x20A0)
    put_u32(data, 0x484, 0x20B0)
    put_u16(data, 0x490, 0)
    put_u16(data, 0x492, 1)
    put_str(data, 0x4A0, "alpha")
    put_str(data, 0x4B0, "beta")


def _write_imports(data: bytearray) -> None:
    put_u32(data, 0x500, 0x2160)
    put_u32(data, 0x50C, 0x2150)
    put_u32(data, 0x510, 0x2180)
    put_str(data, 0x550, "kernel32.dll")
    put_u32(data, 0x560, 0x21A0)
    put_u32(data, 0x564, 0x80000005)
    put_u32(data, 0x580, 0x21A0)
    put_u32(data, 0x584, 0x80000005)
    put_u16(data, 0x5A0, 0x1234)
    put_str(data, 0x5A2, "ExitProcess")


def _write_resources(data: bytearray) -> None:
    put_u16(data, 0x600 + 14, 1)
    put_u32(data, 0x610, 1)
    put_u32(data, 0x614, 0x80000018)
    put_u16(data, 0x618 + 14, 1)
    put_u32(data, 0x628, 101)
    put_u32(data, 0x62C, 0x80000030)
    put_u16(data, 0x630 + 14, 1)
    put_u32(data, 0x640, 1033)
    put_u32(data, 0x644, 0x48)
    put_u32(data, 0x648, 0x25C0)
    put_u32(data, 0x64C, 4)
    data[0x9C0:0x9C4] = b"\x01\x02\x03\x04"


def _write_relocations(data: bytearray) -> None:
    put_u32(data, 0x700, 0x1000)
    put_u32(data, 0x704, 12)
    put_u16(data, 0x708, 0x3010)
    put_u16(data, 0x70A, 0x3024)


def _write_debug(data: bytearray) -> None:
    put_u32(data, 0x800 + 4, 0x5F5E1000)
    put_u32(data, 0x800 + 12, 2)
    put_u32(data, 0x800 + 16, 0x10)
    put_u32(data, 0x800 + 20, 0x2500)
    put_u32(data, 0x800 + 24, 0x900)


def _write_delay_imports(data: bytearray) -> None:
    put_u32(data, 0x900, 1)
    put_u32(data, 0x904, 0x2550)
    put_u32(data, 0x90C, 0x2580)
    put_u32(data, 0x910, 0x2560)
    put_str(data, 0x950, "user32.dll")
    put_u32(data, 0x960, 0x25A0)
    put_u32(data, 0x980, 0x25A0)
    put_u16(data, 0x9A0, 0)
    put_str(data, 0x9A2, "MessageBoxW")


def build_pe32_plus() -> bytes:
    """构造一个最小的 PE32+ 映像（0x400 字节，无数据目录）。"""
    data = bytearray(0x400)
    _dos_and_signature(data)
    struct.pack_into("<HHIIIHH", data, 0x84, 0x8664, 1, 0x5F5E1000, 0, 0, 0xF0, 0x0022)

    opt = OPT64
    put_u16(data, opt, 0x20B)
    data[opt + 2] = 14
    data[opt + 3] = 0
    put_u32(data, opt + 4, 0x200)
    put_u32(data, opt + 16, 0x1000)
    put_u32(data, opt + 20, 0x1000)
    put_u64(data, opt + 24, 0x140000000)
    put_u32(data, opt + 32, 0x1000)
    put_u32(data, opt + 36, 0x200)
    put_u16(data, opt + 40, 6)
    put_u16(data, opt + 48, 6)
    put_u32(data, opt + 56, 0x2000)
    put_u32(data, opt + 60, 0x200)
    put_u16(data, opt + 68, 3)
    put_u16(data, opt + 70, 0x8160)
    put_u64(data, opt + 72, 0x100000)
    put_u64(data, opt + 80, 0x1000)
    put_u64(data, opt + 88, 0x100000)
    put_u64(data, opt + 96, 0x1000)
    put_u32(data, opt + 108, 16)

    table = opt + 0xF0
    data[table : table + 8] = b".text\x00\x00\x00"
    put_u32(data, table + 8, 0x100)
    put_u32(data, table + 12, 0x1000)
    put_u32(data, table + 16, 0x200)
    put_u32(data, table + 20, 0x200)
    put_u32(data, table + 36, 0x60000020)

    put_u32(data, opt + 64, pe_checksum(bytes(data), opt + 64))
    return bytes(data)


@pytest.fixture
def pe32_data() -> bytes:
    """完整 PE32 字节。"""
    return build_pe32()


@pytest.fixture
def pe32_file(tmp_path: Path, pe32_data: bytes) -> Path:
    """写入临时目录的完整 PE32 文件。"""
    path = tmp_path / "sample.exe"
    path.write_bytes(pe32_data)
    return path


@pytest.fixture
def pe64_data() -> bytes:
    """最小 PE32+ 字节。"""
    return build_pe32_plus()


@pytest.fixture
def pe64_file(tmp_path: Path, pe64_data: bytes) -> Path:
    """写入临时目录的 PE32+ 文件。"""
    path = tmp_path / "sample64.exe"
    path.write_bytes(pe64_data)
    return path
