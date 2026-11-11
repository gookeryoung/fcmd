"""peview 读取层测试：边界检查、RVA 换算、校验和与十六进制。"""

from __future__ import annotations

import pytest

from fcmd.apps.peview._pe import Section
from fcmd.apps.peview._reader import (
    PEError,
    Reader,
    compute_checksum,
    format_hex_dump,
    rva_to_offset,
)


class TestReader:
    """``Reader`` 的类型读取与边界检查。"""

    def test_integer_reads(self) -> None:
        reader = Reader(b"\x01\x02\x03\x04\x05\x06\x07\x08")
        assert reader.u8(0) == 1
        assert reader.u16(0) == 0x0201
        assert reader.u32(0) == 0x04030201
        assert reader.u64(0) == 0x0807060504030201
        assert reader.read(0, 2) == b"\x01\x02"

    def test_negative_and_overflow(self) -> None:
        reader = Reader(b"\x00\x00")
        with pytest.raises(PEError, match="长度非法"):
            reader.read(-1, 1)
        with pytest.raises(PEError, match="长度非法"):
            reader.read(0, -1)
        with pytest.raises(PEError, match="越界"):
            reader.u32(0)
        with pytest.raises(PEError, match="越界"):
            reader.u64(0)

    def test_cstring(self) -> None:
        reader = Reader(b"abc\x00def\x00")
        assert reader.cstring(0) == "abc"
        assert reader.cstring(4) == "def"

    def test_cstring_unterminated(self) -> None:
        reader = Reader(b"abcdef")
        with pytest.raises(PEError, match="未在"):
            reader.cstring(0, limit=3)

    def test_cstring_out_of_range(self) -> None:
        with pytest.raises(PEError, match="越界"):
            Reader(b"a").cstring(5)


def _section() -> Section:
    return Section(
        index=0,
        name=".text",
        virtual_size=0x1000,
        virtual_address=0x1000,
        size_of_raw_data=0x200,
        pointer_to_raw_data=0x400,
        characteristics=0,
        header_offset=0,
    )


class TestRvaToOffset:
    """RVA 换算：节内、头部、未初始化区与节外。"""

    def test_inside_section(self) -> None:
        assert rva_to_offset(0x1000, [_section()]) == 0x400
        assert rva_to_offset(0x1100, [_section()], size=0x100) == 0x500

    def test_header_rva(self) -> None:
        assert rva_to_offset(0x100, [_section()], size_of_headers=0x400) == 0x100

    def test_uninitialized_tail(self) -> None:
        with pytest.raises(PEError, match="未初始化区域"):
            rva_to_offset(0x1300, [_section()])

    def test_size_crosses_raw_end(self) -> None:
        with pytest.raises(PEError, match="未初始化区域"):
            rva_to_offset(0x11F0, [_section()], size=0x20)

    def test_outside_sections(self) -> None:
        with pytest.raises(PEError, match="不在任何节内"):
            rva_to_offset(0x9000, [_section()])

    def test_negative(self) -> None:
        with pytest.raises(PEError, match="不能为负"):
            rva_to_offset(-1, [_section()])


class TestChecksum:
    """PE 校验和计算。"""

    def test_zero_data(self) -> None:
        assert compute_checksum(bytes(64), 0) == 64

    def test_matches_independent_implementation(self) -> None:
        data = bytes(range(64))
        assert compute_checksum(data, 24) == _independent(data, 24)

    def test_odd_length(self) -> None:
        data = bytes(range(1, 10))
        assert compute_checksum(data, 0) == _independent(data, 0)

    def test_checksum_field_is_excluded(self) -> None:
        base = bytes(32)
        with_value = bytearray(base)
        with_value[8:12] = b"\xff\xff\xff\xff"
        assert compute_checksum(bytes(with_value), 8) == compute_checksum(base, 8)

    def test_invalid_offset(self) -> None:
        with pytest.raises(PEError, match="偏移非法"):
            compute_checksum(b"\x00" * 4, 2)
        with pytest.raises(PEError, match="偏移非法"):
            compute_checksum(b"\x00" * 4, -1)


def _independent(data: bytes, checksum_offset: int) -> int:
    total = 0
    for index in range(0, len(data) - len(data) % 2, 2):
        word = 0 if checksum_offset <= index < checksum_offset + 4 else data[index] | (data[index + 1] << 8)
        total = (total + word) & 0xFFFFFFFF
        total = (total & 0xFFFF) + (total >> 16)
    if len(data) % 2:
        last = len(data) - 1
        total += 0 if checksum_offset <= last < checksum_offset + 4 else data[last]
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + len(data)) & 0xFFFFFFFF


class TestHexDump:
    """十六进制文本格式。"""

    def test_basic_row(self) -> None:
        text = format_hex_dump(b"MZ\x90\x00")
        assert text.startswith("00000000  4D 5A 90 00")
        assert "|MZ..|" in text

    def test_offset_included(self) -> None:
        assert format_hex_dump(b"\x01", offset=0x10).startswith("00000010  01")

    def test_empty(self) -> None:
        assert format_hex_dump(b"") == "(无数据)"

    def test_truncated_note(self) -> None:
        text = format_hex_dump(bytes(40), limit=16)
        assert "共 40 字节" in text
        assert text.count("\n") == 1

    def test_limit_zero(self) -> None:
        assert "共 8 字节" in format_hex_dump(bytes(8), limit=0)

    def test_no_limit(self) -> None:
        text = format_hex_dump(bytes(20), limit=None)
        assert "仅显示" not in text
