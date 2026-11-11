"""peview 解析层测试：完整头部、各目录、PE32+ 与损坏输入。"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fcmd.apps.peview._pe import (
    PEError,
    load_pe,
    parse_pe,
)


def put_u16(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", data, offset, value)


def put_u32(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", data, offset, value)


class TestFullPe32:
    """带完整目录的 PE32 夹具。"""

    def test_headers(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data, Path("sample.exe"))
        assert pe.path == Path("sample.exe")
        assert pe.size == len(pe32_data) == 0xA10
        assert pe.dos.magic == 0x5A4D
        assert pe.dos.e_lfanew == 0x80
        assert pe.dos.bytes_on_last_page == 0x90
        assert pe.dos.min_extra_paragraphs == 0
        assert pe.dos.max_extra_paragraphs == 0xFFFF
        assert pe.dos.sp == 0xB8
        assert pe.dos.lfarlc == 0x40
        assert pe.file_header.machine == 0x014C
        assert pe.file_header.number_of_sections == 2
        assert pe.file_header.timestamp == 0x5F5E1000
        assert pe.file_header.size_of_optional_header == 0xE0
        assert pe.file_header.characteristics == 0x0102
        assert pe.optional.magic == 0x10B
        assert not pe.optional.is_64bit
        assert pe.optional.base_of_data == 0x2000
        assert pe.optional.address_of_entry_point == 0x1000
        assert pe.optional.image_base == 0x400000
        assert pe.optional.section_alignment == 0x1000
        assert pe.optional.file_alignment == 0x200
        assert pe.optional.size_of_image == 0x3000
        assert pe.optional.size_of_headers == 0x200
        assert pe.optional.subsystem == 3
        assert pe.optional.dll_characteristics == 0x8140
        assert pe.optional.size_of_stack_reserve == 0x100000
        assert pe.optional.size_of_heap_commit == 0x1000
        assert pe.optional.loader_flags == 0
        assert pe.optional.number_of_rva_and_sizes == 16
        assert len(pe.optional.directories) == 16
        assert not pe.is_64bit
        assert not pe.is_dll

    def test_sections(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        text, rdata = pe.sections
        assert text.name == ".text"
        assert text.virtual_address == 0x1000
        assert text.virtual_size == 0x100
        assert text.size_of_raw_data == 0x200
        assert text.pointer_to_raw_data == 0x200
        assert text.characteristics == 0x60000020
        assert text.characteristics_offset == 0x19C
        assert rdata.name == ".rdata"
        assert rdata.characteristics == 0x40000040
        assert pe.section_table_offset == 0x178

    def test_directories(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        export = pe.directory(0)
        assert export is not None
        assert export.virtual_address == 0x2000
        assert export.size == 0xA0
        security = pe.directory(4)
        assert security is not None
        assert security.virtual_address == 0x9E0
        assert pe.directory(15) is None
        assert pe.directory(99) is None

    def test_imports(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        assert len(pe.imports) == 2
        normal, delayed = pe.imports
        assert normal.name == "kernel32.dll"
        assert not normal.delayed
        assert normal.descriptor_offset == 0x500
        assert len(normal.symbols) == 2
        assert normal.symbols[0].name == "ExitProcess"
        assert normal.symbols[0].hint == 0x1234
        assert normal.symbols[0].thunk_rva == 0x2160
        assert normal.symbols[1].ordinal == 5
        assert normal.symbols[1].name is None
        assert delayed.name == "user32.dll"
        assert delayed.delayed
        assert delayed.symbols[0].name == "MessageBoxW"
        assert delayed.symbols[0].hint == 0

    def test_exports(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        table = pe.exports
        assert table is not None
        assert table.dll_name == "peview_test.dll"
        assert table.base_ordinal == 1
        assert table.offset == 0x400
        assert [(symbol.name, symbol.ordinal, symbol.rva, symbol.forwarder) for symbol in table.symbols] == [
            ("alpha", 1, 0x1000, None),
            ("beta", 2, 0x1010, None),
            (None, 3, 0x2040, "KERNEL32.Sleep"),
        ]

    def test_resources(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        assert len(pe.resources) == 1
        entry = pe.resources[0]
        assert entry.type_id == 1
        assert entry.type_name is None
        assert entry.name_id == 101
        assert entry.name is None
        assert entry.language == 1033
        assert entry.language_name is None
        assert entry.rva == 0x25C0
        assert entry.size == 4
        assert entry.file_offset == 0x648

    def test_named_resource_keys(self, pe32_data: bytes) -> None:
        data = bytearray(pe32_data)
        put_u16(data, 0x658, 2)
        data[0x65A:0x65E] = "AB".encode("utf-16-le")
        put_u32(data, 0x610, 0x80000058)
        put_u32(data, 0x640, 0x80000058)
        entry = parse_pe(bytes(data)).resources[0]
        assert entry.type_id is None
        assert entry.type_name == "AB"
        assert entry.language is None
        assert entry.language_name == "AB"

    def test_relocations(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        assert len(pe.relocations) == 1
        block = pe.relocations[0]
        assert block.page_rva == 0x1000
        assert block.offset == 0x700
        assert [(entry.relocation_type, entry.offset) for entry in block.entries] == [(3, 0x10), (3, 0x24)]

    def test_debug(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        assert len(pe.debug) == 1
        entry = pe.debug[0]
        assert entry.debug_type == 2
        assert entry.timestamp == 0x5F5E1000
        assert entry.size_of_data == 0x10
        assert entry.address_of_raw_data == 0x2500
        assert entry.pointer_to_raw_data == 0x900
        assert entry.offset == 0x800

    def test_signature_and_overlay(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        assert pe.signature.present
        assert pe.signature.offset == 0x9E0
        assert pe.signature.size == 8
        assert pe.overlay_offset == 0xA00
        assert pe.overlay_size == 0x10

    def test_header_fields(self, pe32_data: bytes) -> None:
        pe = parse_pe(pe32_data)
        expected = {
            "entry_point": (0xA8, 4),
            "image_base": (0xB4, 4),
            "subsystem": (0xDC, 2),
            "dll_characteristics": (0xDE, 2),
            "timestamp": (0x88, 4),
            "stack_reserve": (0xE0, 4),
            "stack_commit": (0xE4, 4),
            "heap_reserve": (0xE8, 4),
            "heap_commit": (0xEC, 4),
            "checksum": (0xD8, 4),
            "section.0.characteristics": (0x19C, 4),
            "section.1.characteristics": (0x1C4, 4),
        }
        assert set(pe.header_fields) == set(expected)
        for name, (offset, size) in expected.items():
            assert pe.header_fields[name].offset == offset
            assert pe.header_fields[name].size == size
        assert pe.header_fields["image_base"].value == 0x400000
        assert pe.header_fields["entry_point"].value == 0x1000
        assert pe.header_fields["timestamp"].value == 0x5F5E1000

    def test_memory_path_default(self, pe32_data: bytes) -> None:
        assert parse_pe(pe32_data).path == Path("<memory>")


class TestPe32Plus:
    """PE32+（64 位）可选头。"""

    def test_parse(self, pe64_file: Path) -> None:
        pe = load_pe(pe64_file)
        assert pe.is_64bit
        assert pe.optional.magic == 0x20B
        assert pe.optional.base_of_data is None
        assert pe.optional.address_of_entry_point == 0x1000
        assert pe.optional.image_base == 0x140000000
        assert pe.optional.size_of_stack_reserve == 0x100000
        assert pe.optional.size_of_stack_commit == 0x1000
        assert pe.optional.size_of_heap_reserve == 0x100000
        assert pe.optional.size_of_heap_commit == 0x1000
        assert pe.optional.dll_characteristics == 0x8160
        assert len(pe.optional.directories) == 16
        assert all(pe.directory(index) is None for index in range(16))
        assert pe.sections[0].name == ".text"
        assert pe.imports == ()
        assert pe.exports is None
        assert pe.resources == ()
        assert pe.relocations == ()
        assert pe.debug == ()
        assert not pe.signature.present
        assert pe.overlay_size == 0
        assert pe.header_fields["image_base"].size == 8
        assert pe.header_fields["image_base"].offset == 0xB0
        assert pe.header_fields["stack_reserve"].size == 8
        assert pe.header_fields["heap_commit"].offset == 0xF8

    def test_file_characteristics(self, pe64_file: Path) -> None:
        pe = load_pe(pe64_file)
        assert pe.file_header.machine == 0x8664
        assert pe.file_header.characteristics == 0x0022


class TestLoadErrors:
    """文件读取失败。"""

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(PEError, match="文件不存在"):
            load_pe(tmp_path / "missing.exe")


class TestCorruptInput:
    """损坏输入必须抛带位置说明的 PEError。"""

    def _mutate(self, data: bytes, offset: int, fmt: str, value: int) -> bytes:
        mutated = bytearray(data)
        struct.pack_into(fmt, mutated, offset, value)
        return bytes(mutated)

    def test_truncated_dos_header(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="DOS 头 越界"):
            parse_pe(pe32_data[:0x30])

    def test_bad_mz(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="缺少 MZ 签名"):
            parse_pe(self._mutate(pe32_data, 0, "<H", 0))

    def test_bad_lfanew(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="PE 签名 越界"):
            parse_pe(self._mutate(pe32_data, 60, "<I", 0xFFFFF))

    def test_bad_pe_signature(self, pe32_data: bytes) -> None:
        data = bytearray(pe32_data)
        data[0x80:0x84] = b"NOPE"
        with pytest.raises(PEError, match="PE 签名无效"):
            parse_pe(bytes(data))

    def test_bad_optional_magic(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="可选头 Magic 无效"):
            parse_pe(self._mutate(pe32_data, 0x98, "<H", 0x999))

    def test_optional_header_too_small(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="可选头长度"):
            parse_pe(self._mutate(pe32_data, 0x94, "<H", 0x20))

    def test_too_many_sections(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="节表项数量异常"):
            parse_pe(self._mutate(pe32_data, 0x86, "<H", 97))

    def test_directory_count_overflow(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="数据目录声明"):
            parse_pe(self._mutate(pe32_data, 0x98 + 92, "<I", 17))

    def test_import_rva_outside_sections(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="不在任何节内"):
            parse_pe(self._mutate(pe32_data, 0x98 + 96 + 8, "<I", 0x9000))

    def test_import_missing_name(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="缺少 DLL 名"):
            parse_pe(self._mutate(pe32_data, 0x50C, "<I", 0))

    def test_delay_import_va_below_image_base(self, pe32_data: bytes) -> None:
        data = bytearray(pe32_data)
        put_u32(data, 0x900, 0)
        put_u32(data, 0x904, 0x100)
        with pytest.raises(PEError, match="小于镜像基址"):
            parse_pe(bytes(data))

    def test_export_count_too_large(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="导出表数量异常"):
            parse_pe(self._mutate(pe32_data, 0x414, "<I", 0x10001))

    def test_relocation_block_size_invalid(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="重定位块大小非法"):
            parse_pe(self._mutate(pe32_data, 0x704, "<I", 4))

    def test_resource_cycle(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="循环引用"):
            parse_pe(self._mutate(pe32_data, 0x614, "<I", 0x80000000))

    def test_import_thunk_outside_sections(self, pe32_data: bytes) -> None:
        with pytest.raises(PEError, match="不在任何节内"):
            parse_pe(self._mutate(pe32_data, 0x560, "<I", 0x9000))
