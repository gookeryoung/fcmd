"""peview GUI 纯逻辑测试：树、详情、节点定位与十六进制视图。"""

from __future__ import annotations

from dataclasses import replace

from fcmd.apps.peview import _report, _view
from fcmd.apps.peview._pe import ExportSymbol, parse_pe


def _pe(pe32_data: bytes):
    return parse_pe(pe32_data)


class TestTree:
    """分类树。"""

    def test_categories_and_children(self, pe32_data: bytes) -> None:
        tree = _view.build_tree(_pe(pe32_data))
        ids = [row.node_id for row in tree]
        assert len(ids) == len(set(ids))
        assert {
            "dos",
            "nt",
            "optional",
            "directories",
            "sections",
            "imports",
            "exports",
            "resources",
            "relocations",
            "debug",
            "signature",
            "overlay",
        } <= set(ids)

        positions = {row.node_id: index for index, row in enumerate(tree)}
        for row in tree:
            if row.parent is not None:
                assert row.parent in positions
                assert positions[row.parent] < positions[row.node_id]

        assert sum(1 for row in tree if row.parent == "directories") == 16
        assert sum(1 for row in tree if row.parent == "sections") == 2
        assert sum(1 for row in tree if row.parent == "imports") == 2
        assert sum(1 for row in tree if row.parent == "resources") == 1
        assert sum(1 for row in tree if row.parent == "relocations") == 1
        assert sum(1 for row in tree if row.parent == "debug") == 1
        assert any(row.text.endswith("（延迟）") for row in tree)

    def test_pe64_empty_categories(self, pe64_data: bytes) -> None:
        tree = _view.build_tree(parse_pe(pe64_data))
        texts = {row.node_id: row.text for row in tree}
        assert texts["signature"] == "签名（无）"
        assert texts["overlay"] == "Overlay"
        assert texts["imports"] == "导入（0）"
        assert not any(row.parent == "resources" for row in tree)


class TestNodeOffset:
    """节点 → 文件偏移。"""

    def test_all_categories(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        assert _view.node_offset(pe, "dos") == 0
        assert _view.node_offset(pe, "nt") == 0x80
        assert _view.node_offset(pe, "optional") == 0x98
        assert _view.node_offset(pe, "directories") == 0xF8
        assert _view.node_offset(pe, "directory.0") == 0xF8
        assert _view.node_offset(pe, "sections") == 0x178
        assert _view.node_offset(pe, "section.0") == 0x178
        assert _view.node_offset(pe, "section.1") == 0x1A0
        assert _view.node_offset(pe, "imports") == 0x500
        assert _view.node_offset(pe, "import.1") == 0x900
        assert _view.node_offset(pe, "exports") == 0x400
        assert _view.node_offset(pe, "resources") == 0x648
        assert _view.node_offset(pe, "resource.0") == 0x648
        assert _view.node_offset(pe, "relocations") == 0x700
        assert _view.node_offset(pe, "reloc.0") == 0x700
        assert _view.node_offset(pe, "debug") == 0x800
        assert _view.node_offset(pe, "debug.0") == 0x800
        assert _view.node_offset(pe, "signature") == 0x9E0
        assert _view.node_offset(pe, "overlay") == 0xA00

    def test_out_of_range_and_unknown(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        for node_id in (
            "directory.99",
            "section.99",
            "import.99",
            "resource.99",
            "reloc.99",
            "debug.99",
            "unknown",
            "directory.x",
        ):
            assert _view.node_offset(pe, node_id) is None

    def test_details_out_of_range_and_unknown(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        for node_id in (
            "directory.99",
            "section.99",
            "import.99",
            "resource.99",
            "reloc.99",
            "debug.99",
            "unknown",
        ):
            assert _view.details_for(pe, node_id) == []

    def test_pe64_missing_offsets(self, pe64_data: bytes) -> None:
        pe = parse_pe(pe64_data)
        assert _view.node_offset(pe, "imports") is None
        assert _view.node_offset(pe, "exports") is None
        assert _view.node_offset(pe, "resources") is None
        assert _view.node_offset(pe, "relocations") is None
        assert _view.node_offset(pe, "debug") is None
        assert _view.node_offset(pe, "signature") is None
        assert _view.node_offset(pe, "overlay") is None


class TestDetails:
    """详情行。"""

    def test_every_tree_node_has_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        for row in _view.build_tree(pe):
            assert _view.details_for(pe, row.node_id), row.node_id

    def test_header_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        dos = dict(_view.details_for(pe, "dos"))
        assert dos["e_lfanew"] == "0x80"
        nt = dict(_view.details_for(pe, "nt"))
        assert "x86" in nt["Machine"]
        optional = dict(_view.details_for(pe, "optional"))
        assert optional["ImageBase"] == "0x400000"
        assert optional["Subsystem"].startswith("Windows Console")

    def test_directory_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        rows = dict(_view.details_for(pe, "directory.1"))
        assert "导入表" in rows["名称"]
        assert rows["RVA / 文件偏移"] == "0x00002100"
        assert len(_view.details_for(pe, "directories")) == 16

    def test_section_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        rows = dict(_view.details_for(pe, "section.0"))
        assert rows["名称"] == ".text"
        assert rows["VirtualAddress"] == "0x1000"
        assert "MEM_EXECUTE" in rows["属性解读"]

    def test_import_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        rows = dict(_view.details_for(pe, "import.0"))
        assert rows["DLL"] == "kernel32.dll"
        assert rows["类型"] == "普通导入"
        assert "ExitProcess" in rows
        assert "#5" in rows
        delayed = dict(_view.details_for(pe, "import.1"))
        assert delayed["类型"] == "延迟导入"

    def test_export_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        rows = dict(_view.details_for(pe, "exports"))
        assert rows["DLL 名"] == "peview_test.dll"
        assert "alpha" in " ".join(rows)
        assert any("KERNEL32.Sleep" in value for value in rows.values())

    def test_resource_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        rows = dict(_view.details_for(pe, "resource.0"))
        assert rows["类型"] == "Cursor"
        assert rows["名称"] == "#101"
        assert rows["语言"] == "1033"
        assert rows["数据 RVA"] == "0x25C0"

    def test_relocation_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        rows = dict(_view.details_for(pe, "reloc.0"))
        assert rows["页 RVA"] == "0x1000"
        assert "HIGHLOW 0x010" in rows
        assert rows["HIGHLOW 0x010"] == "目标 RVA 0x1010"

    def test_debug_details(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        rows = dict(_view.details_for(pe, "debug.0"))
        assert rows["类型"] == "CodeView"
        assert rows["PointerToRawData"] == "0x900"

    def test_signature_and_overlay(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        signature = dict(_view.details_for(pe, "signature"))
        assert signature["状态"] == "存在 Authenticode 签名"
        assert "失效" in signature["提示"]
        overlay = dict(_view.details_for(pe, "overlay"))
        assert overlay["文件偏移"] == "0xA00"
        assert overlay["大小"] == "16 字节"

    def test_empty_states(self, pe64_data: bytes) -> None:
        pe = parse_pe(pe64_data)
        assert _view.details_for(pe, "exports") == [("状态", "(没有导出表)")]
        assert _view.details_for(pe, "signature") == [("状态", "无 Authenticode 签名")]
        assert _view.details_for(pe, "overlay") == [("状态", "无 overlay")]
        assert _view.details_for(pe, "imports") == []

    def test_unknown_node(self, pe32_data: bytes) -> None:
        assert _view.details_for(_pe(pe32_data), "nope") == []

    def test_detail_truncation(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        assert pe.exports is not None
        symbols = tuple(
            ExportSymbol(name=f"symbol{index}", ordinal=index, rva=0x1000 + index, forwarder=None)
            for index in range(_view.MAX_DETAIL_ROWS + 5)
        )
        big = replace(pe, exports=replace(pe.exports, symbols=symbols))
        rows = _view.details_for(big, "exports")
        assert len(rows) == _view.MAX_DETAIL_ROWS + 1
        assert rows[-1][0] == "…"
        assert "已省略" in rows[-1][1]


class TestHexView:
    """十六进制窗口。"""

    def test_default(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        text = _view.hex_view(pe, 0)
        assert text.startswith("00000000  4D 5A 90 00")
        assert "|MZ" in text

    def test_length(self, pe32_data: bytes) -> None:
        text = _view.hex_view(_pe(pe32_data), 0, 4)
        assert text.count("\n") == 0

    def test_offset_inside(self, pe32_data: bytes) -> None:
        text = _view.hex_view(_pe(pe32_data), 0x9E0, 8)
        assert text.startswith("000009E0  08 00 00 00")

    def test_negative(self, pe32_data: bytes) -> None:
        assert _view.hex_view(_pe(pe32_data), -1) == "(偏移必须是非负整数)"

    def test_out_of_range(self, pe32_data: bytes) -> None:
        pe = _pe(pe32_data)
        assert _view.hex_view(pe, pe.size) == f"(偏移 0x{pe.size:X} 超出文件大小 0x{pe.size:X})"

    def test_zero_length(self, pe32_data: bytes) -> None:
        assert _view.hex_view(_pe(pe32_data), 0, 0) == "(无数据)"


def test_tree_details_use_report_names(pe32_data: bytes) -> None:
    """树标签复用报告层的资源 / 尺寸文本。"""
    pe = _pe(pe32_data)
    texts = [row.text for row in _view.build_tree(pe)]
    assert f"Overlay（{_report.format_size(pe.overlay_size)}）" in texts
