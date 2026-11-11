"""peview 展示层测试：名称表、标志位解读与各类文本输出。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fcmd.apps.peview import _report
from fcmd.apps.peview._pe import PEImage, SignatureInfo


class TestNames:
    """名称与标志位查表。"""

    def test_machine_name(self) -> None:
        assert "x86" in _report.machine_name(0x014C)
        assert "x64" in _report.machine_name(0x8664)
        assert _report.machine_name(0x1234) == "未知 (0x1234)"

    def test_subsystem_name(self) -> None:
        assert _report.subsystem_name(2) == "Windows GUI"
        assert _report.subsystem_name(99) == "未知 (99)"

    def test_directory_name(self) -> None:
        assert "导入表" in _report.directory_name(1)
        assert _report.directory_name(99) == "目录 99"

    def test_relocation_type_name(self) -> None:
        assert _report.relocation_type_name(3) == "HIGHLOW"
        assert _report.relocation_type_name(99) == "类型 99"

    def test_debug_type_name(self) -> None:
        assert _report.debug_type_name(2) == "CodeView"
        assert _report.debug_type_name(99) == "类型 99"

    def test_resource_type_name(self) -> None:
        assert _report.resource_type_name(1) == "Cursor"
        assert _report.resource_type_name(None) == "命名类型"
        assert _report.resource_type_name(999) == "类型 999"


class TestFlags:
    """位掩码解读。"""

    def test_file_characteristics(self) -> None:
        text = _report.format_file_characteristics(0x0102)
        assert "EXECUTABLE_IMAGE" in text
        assert "32BIT_MACHINE" in text

    def test_dll_characteristics(self) -> None:
        text = _report.format_dll_characteristics(0x8140)
        assert "TERMINAL_SERVER_AWARE" in text
        assert "NX_COMPAT" in text
        assert "DYNAMIC_BASE" in text

    def test_unknown_bits(self) -> None:
        assert "未知位 0x40" in _report.format_file_characteristics(0x40)

    def test_empty_flags(self) -> None:
        assert _report.format_file_characteristics(0) == "无"

    def test_section_characteristics(self) -> None:
        text = _report.format_section_characteristics(0x60500020)
        assert "对齐 16 字节" in text
        assert "CNT_CODE" in text
        assert "MEM_EXECUTE" in text
        assert "MEM_READ" in text

    def test_section_characteristics_empty(self) -> None:
        assert _report.format_section_characteristics(0) == "无"

    def test_section_characteristics_unknown_alignment(self) -> None:
        assert "对齐码 15" in _report.format_section_characteristics(0x00F00000)


class TestTables:
    """表格与尺寸格式化。"""

    def test_format_table_empty(self) -> None:
        assert _report.format_table(("A",), []) == "(无)"

    def test_format_table_alignment(self) -> None:
        text = _report.format_table(("名称", "值"), (("a", "1"), ("中文", "22")), ("left", "right"))
        lines = text.splitlines()
        assert lines[0].startswith("名称")
        assert lines[1].startswith("--")
        assert lines[2].endswith(" 1")
        assert lines[3].endswith("22")

    def test_format_size(self) -> None:
        assert _report.format_size(512) == "512 B"
        assert _report.format_size(2048) == "2.0 KB"
        assert _report.format_size(3 * 1024 * 1024) == "3.0 MB"


class TestFormats:
    """完整夹具上的文本输出。"""

    def test_summary(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_summary(parse_pe(pe32_data))
        assert "可执行文件（32 位）" in text
        assert "x86" in text
        assert "数字签名: 有" in text
        assert "Overlay: 0x" in text

    def test_headers(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_headers(parse_pe(pe32_data))
        assert "e_lfanew" in text
        assert "PE32" in text
        assert "Windows Console" in text
        assert "TERMINAL_SERVER_AWARE" in text

    def test_directories(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_directories(parse_pe(pe32_data))
        assert "导出表" in text
        assert "0x00002000" in text

    def test_sections(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_sections(parse_pe(pe32_data))
        assert ".text" in text
        assert ".rdata" in text
        assert "CNT_CODE" in text

    def test_imports(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_imports(parse_pe(pe32_data))
        assert "kernel32.dll" in text
        assert "ExitProcess" in text
        assert "#5" in text
        assert "延迟导入" in text

    def test_exports(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_exports(parse_pe(pe32_data))
        assert "peview_test.dll" in text
        assert "alpha" in text
        assert "KERNEL32.Sleep" in text

    def test_resources(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_resources(parse_pe(pe32_data))
        assert "Cursor" in text
        assert "#101" in text
        assert "1033" in text

    def test_relocations(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_relocations(parse_pe(pe32_data))
        assert "HIGHLOW" in text
        assert "0x010" in text

    def test_debug(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_debug(parse_pe(pe32_data))
        assert "CodeView" in text
        assert "0x900" in text

    def test_fields(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_fields(parse_pe(pe32_data))
        assert "entry_point" in text
        assert "section.0.characteristics" in text

    def test_empty_sections(self, pe64_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        text = _report.format_exports(parse_pe(pe64_data))
        assert "没有导出表" in text

    def test_empty_collections(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        empty = replace(
            pe,
            optional=replace(pe.optional, directories=()),
            imports=(),
            resources=(),
            relocations=(),
            debug=(),
        )
        assert _report.format_directories(empty) == "(没有数据目录)"
        assert _report.format_sections(replace(pe, sections=())) == "(没有节)"
        assert _report.format_imports(empty) == "(没有导入表)"
        assert _report.format_resources(empty) == "(没有资源)"
        assert _report.format_relocations(empty) == "(没有重定位)"
        assert _report.format_debug(empty) == "(没有调试目录)"

    def test_empty_import_symbols(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        text = _report.format_imports(replace(pe, imports=(replace(pe.imports[0], symbols=()),)))
        assert "(无符号)" in text

    def test_empty_export_symbols(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        assert pe.exports is not None
        text = _report.format_exports(replace(pe, exports=replace(pe.exports, symbols=())))
        assert "没有导出函数" in text

    def test_relocation_padding_only(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        text = _report.format_relocations(replace(pe, relocations=(replace(pe.relocations[0], entries=()),)))
        assert "(仅填充项)" in text

    def test_check_warnings(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        text = _report.format_check(replace(pe, optional=replace(pe.optional, checksum=0)))
        assert "警告:" in text
        assert "校验和" in text

    def test_resource_label(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        entry = parse_pe(pe32_data).resources[0]
        assert _report.resource_label(entry) == "Cursor/#101/1033"


class TestChecks:
    """一致性检查。"""

    def test_clean_file(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        assert _report.check_problems(pe) == []
        text = _report.format_check(pe)
        assert "一致" in text
        assert "结论: 未发现结构性问题" in text
        assert "数字签名: 有" in text

    def test_checksum_mismatch(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        data = bytearray(pe32_data)
        data[0x200] ^= 0xFF
        problems = _report.check_problems(parse_pe(bytes(data)))
        assert any("校验和不一致" in problem for problem in problems)

    def test_entry_point_out_of_image(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        bad = replace(pe, optional=replace(pe.optional, address_of_entry_point=pe.optional.size_of_image))
        assert any("入口点" in problem for problem in _report.check_problems(bad))

    def test_section_alignment(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        bad = replace(pe, optional=replace(pe.optional, section_alignment=0x100))
        assert any("SectionAlignment" in problem for problem in _report.check_problems(bad))

    def test_section_raw_beyond_file(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        section = replace(pe.sections[0], size_of_raw_data=pe.size)
        bad = replace(pe, sections=(section, *pe.sections[1:]))
        assert any("超出文件末尾" in problem for problem in _report.check_problems(bad))

    def test_signature_beyond_file(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        bad = replace(pe, signature=SignatureInfo(present=True, offset=pe.size - 2, size=8))
        assert any("签名目录" in problem for problem in _report.check_problems(bad))

    def test_overlay_display(self, pe32_data: bytes) -> None:
        from fcmd.apps.peview._pe import parse_pe

        pe = parse_pe(pe32_data)
        no_overlay = replace(pe, overlay_offset=0, overlay_size=0)
        assert "Overlay: 无" in _report.format_check(no_overlay)


@pytest.mark.parametrize("value", [0, 1, 0xFFFFFFFF])
def test_flags_never_raise(value: int) -> None:
    """任意位组合都应得到非空文本。"""
    assert _report.format_file_characteristics(value)
    assert _report.format_dll_characteristics(value)
    assert _report.format_section_characteristics(value)


def test_peimage_helpers(pe32_data: bytes) -> None:
    """PEImage 的 size / is_64bit / is_dll 属性。"""
    from fcmd.apps.peview._pe import parse_pe

    pe = parse_pe(pe32_data)
    assert isinstance(pe, PEImage)
    assert pe.size == len(pe32_data)
    assert not pe.is_64bit
    assert not pe.is_dll
