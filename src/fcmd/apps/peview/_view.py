"""pevieww 的纯逻辑：分类树、详情行、十六进制窗口与节点定位。

Tk 窗口只负责把这里返回的数据画出来：树行 → ``Treeview``、详情对 → 只读
文本、十六进制文本 → ``Text``。所有与 PE 结构相关的判断都在本模块完成，
因此可以在无显示环境下单测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fcmd.apps.peview import _report
from fcmd.apps.peview._reader import DEFAULT_HEX_LIMIT, format_hex_dump

if TYPE_CHECKING:
    from collections.abc import Callable

    from fcmd.apps.peview._pe import PEImage

__all__ = [
    "MAX_DETAIL_ROWS",
    "TreeRow",
    "build_tree",
    "details_for",
    "hex_view",
    "node_offset",
]

MAX_DETAIL_ROWS = 200
"""详情面板单个节点最多展开的列表行数（超出时追加省略提示）。"""


@dataclass(frozen=True)
class TreeRow:
    """一个树节点：``parent`` 为 ``None`` 表示顶层分类。"""

    node_id: str
    parent: str | None
    text: str
    kind: str


def build_tree(pe: PEImage) -> tuple[TreeRow, ...]:
    """把解析结果展开为「分类 + 列表项」的扁平树行序列。"""
    rows = [
        TreeRow("dos", None, "DOS 头", "category"),
        TreeRow("nt", None, "NT 文件头", "category"),
        TreeRow("optional", None, "可选头", "category"),
        TreeRow("directories", None, f"数据目录（{len(pe.optional.directories)}）", "category"),
    ]
    for entry in pe.optional.directories:
        rows.append(
            TreeRow(
                f"directory.{entry.index}",
                "directories",
                f"{entry.index}  {_report.directory_name(entry.index)}",
                "directory",
            )
        )
    rows.append(TreeRow("sections", None, f"节表（{len(pe.sections)}）", "category"))
    for section in pe.sections:
        rows.append(TreeRow(f"section.{section.index}", "sections", section.name or f"#{section.index}", "section"))
    rows.append(TreeRow("imports", None, f"导入（{len(pe.imports)}）", "category"))
    for index, library in enumerate(pe.imports):
        suffix = "（延迟）" if library.delayed else ""
        rows.append(TreeRow(f"import.{index}", "imports", f"{library.name}{suffix}", "import"))
    rows.append(TreeRow("exports", None, "导出", "category"))
    rows.append(TreeRow("resources", None, f"资源（{len(pe.resources)}）", "category"))
    for index, resource in enumerate(pe.resources):
        rows.append(TreeRow(f"resource.{index}", "resources", _report.resource_label(resource), "resource"))
    rows.append(TreeRow("relocations", None, f"重定位（{len(pe.relocations)} 块）", "category"))
    for index, block in enumerate(pe.relocations):
        rows.append(
            TreeRow(
                f"reloc.{index}", "relocations", f"页 0x{block.page_rva:X}（{len(block.entries)} 项）", "relocation"
            )
        )
    rows.append(TreeRow("debug", None, f"调试（{len(pe.debug)}）", "category"))
    for index, entry in enumerate(pe.debug):
        rows.append(
            TreeRow(
                f"debug.{index}",
                "debug",
                f"{_report.debug_type_name(entry.debug_type)} @ 0x{entry.offset:X}",
                "debug",
            )
        )
    rows.append(TreeRow("signature", None, f"签名（{'有' if pe.signature.present else '无'}）", "category"))
    rows.append(
        TreeRow(
            "overlay",
            None,
            "Overlay" if not pe.overlay_size else f"Overlay（{_report.format_size(pe.overlay_size)}）",
            "category",
        )
    )
    return tuple(rows)


def _suffix_index(node_id: str, prefix: str) -> int | None:
    """从 ``prefix.<数字>`` 形式的节点 ID 中取出索引。"""
    marker = f"{prefix}."
    if not node_id.startswith(marker):
        return None
    text = node_id[len(marker) :]
    return int(text) if text.isdigit() else None


# ---------------------------------------------------------------------- #
# 节点 → 文件偏移（十六进制跳转）
# ---------------------------------------------------------------------- #
_NODE_OFFSETS: dict[str, Callable[[PEImage], int | None]] = {
    "dos": lambda _pe: 0,
    "nt": lambda pe: pe.file_header.offset - 4,
    "optional": lambda pe: pe.optional.offset,
    "directories": lambda pe: pe.optional.directories[0].offset if pe.optional.directories else None,
    "sections": lambda pe: pe.section_table_offset,
    "imports": lambda pe: pe.imports[0].descriptor_offset if pe.imports else None,
    "exports": lambda pe: pe.exports.offset if pe.exports is not None else None,
    "resources": lambda pe: pe.resources[0].file_offset if pe.resources else None,
    "relocations": lambda pe: pe.relocations[0].offset if pe.relocations else None,
    "debug": lambda pe: pe.debug[0].offset if pe.debug else None,
    "signature": lambda pe: pe.signature.offset if pe.signature.present else None,
    "overlay": lambda pe: pe.overlay_offset if pe.overlay_size else None,
}

_INDEXED_OFFSETS: tuple[tuple[str, Callable[[PEImage, int], int | None]], ...] = (
    (
        "directory",
        lambda pe, index: pe.optional.directories[index].offset if 0 <= index < len(pe.optional.directories) else None,
    ),
    ("section", lambda pe, index: pe.sections[index].header_offset if 0 <= index < len(pe.sections) else None),
    (
        "import",
        lambda pe, index: pe.imports[index].descriptor_offset if 0 <= index < len(pe.imports) else None,
    ),
    ("resource", lambda pe, index: pe.resources[index].file_offset if 0 <= index < len(pe.resources) else None),
    ("reloc", lambda pe, index: pe.relocations[index].offset if 0 <= index < len(pe.relocations) else None),
    ("debug", lambda pe, index: pe.debug[index].offset if 0 <= index < len(pe.debug) else None),
)


def node_offset(pe: PEImage, node_id: str) -> int | None:
    """返回节点对应的文件偏移（用于十六进制跳转），没有定位点时返回 ``None``。"""
    getter = _NODE_OFFSETS.get(node_id)
    if getter is not None:
        return getter(pe)
    for prefix, indexed in _INDEXED_OFFSETS:
        index = _suffix_index(node_id, prefix)
        if index is not None:
            return indexed(pe, index)
    return None


# ---------------------------------------------------------------------- #
# 节点 → 详情行
# ---------------------------------------------------------------------- #
def _limited(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """截断过长的详情形列表并追加省略提示。"""
    if len(rows) <= MAX_DETAIL_ROWS:
        return rows
    remaining = len(rows) - MAX_DETAIL_ROWS
    return [*rows[:MAX_DETAIL_ROWS], ("…", f"其余 {remaining} 行已省略")]


def _detail_dos(pe: PEImage) -> list[tuple[str, str]]:
    dos = pe.dos
    return [
        ("e_magic", f"0x{dos.magic:04X}"),
        ("e_lfanew", f"0x{dos.e_lfanew:X}"),
        ("e_cblp", str(dos.bytes_on_last_page)),
        ("e_cp", str(dos.pages)),
        ("e_crlc", str(dos.relocations)),
        ("e_cparhdr", str(dos.header_paragraphs)),
        ("e_minalloc", f"0x{dos.min_extra_paragraphs:X}"),
        ("e_maxalloc", f"0x{dos.max_extra_paragraphs:X}"),
        ("e_ss", f"0x{dos.ss:X}"),
        ("e_sp", f"0x{dos.sp:X}"),
        ("e_csum", f"0x{dos.checksum:04X}"),
        ("e_ip", f"0x{dos.ip:X}"),
        ("e_cs", f"0x{dos.cs:X}"),
        ("e_lfarlc", f"0x{dos.lfarlc:X}"),
        ("e_ovno", str(dos.ovno)),
    ]


def _detail_nt(pe: PEImage) -> list[tuple[str, str]]:
    header = pe.file_header
    return [
        ("Machine", f"{_report.machine_name(header.machine)}（0x{header.machine:04X}）"),
        ("NumberOfSections", str(header.number_of_sections)),
        ("TimeDateStamp", f"0x{header.timestamp:08X}"),
        ("PointerToSymbolTable", f"0x{header.pointer_to_symbol_table:X}"),
        ("NumberOfSymbols", str(header.number_of_symbols)),
        ("SizeOfOptionalHeader", str(header.size_of_optional_header)),
        ("Characteristics", f"0x{header.characteristics:04X}"),
        ("特性解读", _report.format_file_characteristics(header.characteristics)),
        ("文件偏移", f"0x{header.offset:X}"),
    ]


def _detail_optional(pe: PEImage) -> list[tuple[str, str]]:
    optional = pe.optional
    return [
        ("Magic", f"0x{optional.magic:X}（{'PE32+' if optional.is_64bit else 'PE32'}）"),
        ("LinkerVersion", f"{optional.major_linker_version}.{optional.minor_linker_version}"),
        ("AddressOfEntryPoint", f"0x{optional.address_of_entry_point:X}"),
        ("ImageBase", f"0x{optional.image_base:X}"),
        ("SectionAlignment", f"0x{optional.section_alignment:X}"),
        ("FileAlignment", f"0x{optional.file_alignment:X}"),
        ("SizeOfImage", f"0x{optional.size_of_image:X}"),
        ("SizeOfHeaders", f"0x{optional.size_of_headers:X}"),
        ("CheckSum", f"0x{optional.checksum:08X}"),
        ("Subsystem", f"{_report.subsystem_name(optional.subsystem)}（{optional.subsystem}）"),
        ("DllCharacteristics", f"0x{optional.dll_characteristics:04X}"),
        ("特性解读", _report.format_dll_characteristics(optional.dll_characteristics)),
        ("SizeOfStackReserve", f"0x{optional.size_of_stack_reserve:X}"),
        ("SizeOfStackCommit", f"0x{optional.size_of_stack_commit:X}"),
        ("SizeOfHeapReserve", f"0x{optional.size_of_heap_reserve:X}"),
        ("SizeOfHeapCommit", f"0x{optional.size_of_heap_commit:X}"),
        ("NumberOfRvaAndSizes", str(optional.number_of_rva_and_sizes)),
        ("文件偏移", f"0x{optional.offset:X}"),
    ]


def _detail_directories(pe: PEImage) -> list[tuple[str, str]]:
    return _limited(
        [
            (
                f"{entry.index}  {_report.directory_name(entry.index)}",
                f"RVA 0x{entry.virtual_address:08X} / 大小 0x{entry.size:X} / 表项 0x{entry.offset:X}",
            )
            for entry in pe.optional.directories
        ]
    )


def _detail_directory(pe: PEImage, index: int) -> list[tuple[str, str]]:
    if not 0 <= index < len(pe.optional.directories):
        return []
    entry = pe.optional.directories[index]
    return [
        ("名称", _report.directory_name(entry.index)),
        ("RVA / 文件偏移", f"0x{entry.virtual_address:08X}"),
        ("大小", f"0x{entry.size:X}（{entry.size} 字节）"),
        ("表项偏移", f"0x{entry.offset:X}"),
    ]


def _detail_sections(pe: PEImage) -> list[tuple[str, str]]:
    return _limited(
        [
            (
                f"#{section.index}  {section.name or '(无名)'}",
                f"VA 0x{section.virtual_address:X} / VSize 0x{section.virtual_size:X} / "
                f"Raw 0x{section.pointer_to_raw_data:X}+0x{section.size_of_raw_data:X} / "
                f"属性 0x{section.characteristics:08X}",
            )
            for section in pe.sections
        ]
    )


def _detail_section(pe: PEImage, index: int) -> list[tuple[str, str]]:
    if not 0 <= index < len(pe.sections):
        return []
    section = pe.sections[index]
    return [
        ("名称", section.name or "(无名)"),
        ("VirtualSize", f"0x{section.virtual_size:X}"),
        ("VirtualAddress", f"0x{section.virtual_address:X}"),
        ("SizeOfRawData", f"0x{section.size_of_raw_data:X}"),
        ("PointerToRawData", f"0x{section.pointer_to_raw_data:X}"),
        ("Characteristics", f"0x{section.characteristics:08X}"),
        ("属性解读", _report.format_section_characteristics(section.characteristics)),
        ("表项偏移", f"0x{section.header_offset:X}"),
    ]


def _detail_imports(pe: PEImage) -> list[tuple[str, str]]:
    return _limited(
        [
            (
                f"{index}  {library.name}",
                f"{'延迟导入' if library.delayed else '导入'} / {len(library.symbols)} 个符号 / "
                f"描述符 0x{library.descriptor_offset:X}",
            )
            for index, library in enumerate(pe.imports)
        ]
    )


def _detail_import(pe: PEImage, index: int) -> list[tuple[str, str]]:
    if not 0 <= index < len(pe.imports):
        return []
    library = pe.imports[index]
    rows = [
        ("DLL", library.name),
        ("类型", "延迟导入" if library.delayed else "普通导入"),
        ("描述符偏移", f"0x{library.descriptor_offset:X}"),
    ]
    rows.extend(
        (
            f"#{symbol.ordinal}" if symbol.ordinal is not None else (symbol.name or "?"),
            "按序号" if symbol.ordinal is not None else f"hint {symbol.hint} / thunk 0x{symbol.thunk_rva:X}",
        )
        for symbol in library.symbols
    )
    return _limited(rows)


def _detail_exports(pe: PEImage) -> list[tuple[str, str]]:
    table = pe.exports
    if table is None:
        return [("状态", "(没有导出表)")]
    rows = [
        ("DLL 名", table.dll_name or "—"),
        ("基序号", str(table.base_ordinal)),
        ("函数数量", str(len(table.symbols))),
        ("目录偏移", f"0x{table.offset:X}"),
    ]
    rows.extend(
        (
            f"{symbol.ordinal}  {symbol.name or '(按序号)'}",
            f"RVA 0x{symbol.rva:X}" + (f" → {symbol.forwarder}" if symbol.forwarder else ""),
        )
        for symbol in table.symbols
    )
    return _limited(rows)


def _detail_resources(pe: PEImage) -> list[tuple[str, str]]:
    return _limited(
        [
            (
                _report.resource_label(resource),
                f"RVA 0x{resource.rva:X} / {resource.size} 字节 / 目录项 0x{resource.file_offset:X}",
            )
            for resource in pe.resources
        ]
    )


def _detail_resource(pe: PEImage, index: int) -> list[tuple[str, str]]:
    if not 0 <= index < len(pe.resources):
        return []
    resource = pe.resources[index]
    return [
        ("类型", resource.type_name or _report.resource_type_name(resource.type_id)),
        ("名称", resource.name or (f"#{resource.name_id}" if resource.name_id is not None else "?")),
        (
            "语言",
            resource.language_name or (str(resource.language) if resource.language is not None else "?"),
        ),
        ("数据 RVA", f"0x{resource.rva:X}"),
        ("大小", f"{resource.size} 字节"),
        ("目录项偏移", f"0x{resource.file_offset:X}"),
    ]


def _detail_relocations(pe: PEImage) -> list[tuple[str, str]]:
    return _limited(
        [
            (
                f"页 0x{block.page_rva:X}",
                f"{len(block.entries)} 项 / 块偏移 0x{block.offset:X}",
            )
            for block in pe.relocations
        ]
    )


def _detail_relocation(pe: PEImage, index: int) -> list[tuple[str, str]]:
    if not 0 <= index < len(pe.relocations):
        return []
    block = pe.relocations[index]
    rows = [("页 RVA", f"0x{block.page_rva:X}"), ("块偏移", f"0x{block.offset:X}"), ("项数", str(len(block.entries)))]
    rows.extend(
        (
            f"{_report.relocation_type_name(entry.relocation_type)} 0x{entry.offset:03X}",
            f"目标 RVA 0x{block.page_rva + entry.offset:X}",
        )
        for entry in block.entries
    )
    return _limited(rows)


def _detail_debug(pe: PEImage) -> list[tuple[str, str]]:
    return _limited(
        [
            (
                f"{_report.debug_type_name(entry.debug_type)} @ 0x{entry.offset:X}",
                f"{entry.size_of_data} 字节 / 数据 RVA 0x{entry.address_of_raw_data:X}",
            )
            for entry in pe.debug
        ]
    )


def _detail_debug_entry(pe: PEImage, index: int) -> list[tuple[str, str]]:
    if not 0 <= index < len(pe.debug):
        return []
    entry = pe.debug[index]
    return [
        ("类型", _report.debug_type_name(entry.debug_type)),
        ("Characteristics", f"0x{entry.characteristics:X}"),
        ("TimeDateStamp", f"0x{entry.timestamp:08X}"),
        ("版本", f"{entry.major_version}.{entry.minor_version}"),
        ("SizeOfData", str(entry.size_of_data)),
        ("AddressOfRawData", f"0x{entry.address_of_raw_data:X}"),
        ("PointerToRawData", f"0x{entry.pointer_to_raw_data:X}"),
        ("表项偏移", f"0x{entry.offset:X}"),
    ]


def _detail_signature(pe: PEImage) -> list[tuple[str, str]]:
    signature = pe.signature
    if not signature.present:
        return [("状态", "无 Authenticode 签名")]
    return [
        ("状态", "存在 Authenticode 签名"),
        ("文件偏移", f"0x{signature.offset:X}"),
        ("大小", f"{signature.size} 字节"),
        ("提示", "写入任何字段都会使签名失效，GUI 会要求额外确认"),
    ]


def _detail_overlay(pe: PEImage) -> list[tuple[str, str]]:
    if not pe.overlay_size:
        return [("状态", "无 overlay")]
    return [("文件偏移", f"0x{pe.overlay_offset:X}"), ("大小", f"{pe.overlay_size} 字节")]


_DETAIL_BUILDERS: dict[str, Callable[[PEImage], list[tuple[str, str]]]] = {
    "dos": _detail_dos,
    "nt": _detail_nt,
    "optional": _detail_optional,
    "directories": _detail_directories,
    "sections": _detail_sections,
    "imports": _detail_imports,
    "exports": _detail_exports,
    "resources": _detail_resources,
    "relocations": _detail_relocations,
    "debug": _detail_debug,
    "signature": _detail_signature,
    "overlay": _detail_overlay,
}

_INDEXED_DETAIL_BUILDERS: tuple[tuple[str, Callable[[PEImage, int], list[tuple[str, str]]]], ...] = (
    ("directory", _detail_directory),
    ("section", _detail_section),
    ("import", _detail_import),
    ("resource", _detail_resource),
    ("reloc", _detail_relocation),
    ("debug", _detail_debug_entry),
)


def details_for(pe: PEImage, node_id: str) -> list[tuple[str, str]]:
    """返回节点在详情面板展示的「标签 / 值」行。"""
    builder = _DETAIL_BUILDERS.get(node_id)
    if builder is not None:
        return builder(pe)
    for prefix, indexed in _INDEXED_DETAIL_BUILDERS:
        index = _suffix_index(node_id, prefix)
        if index is not None:
            return indexed(pe, index)
    return []


def hex_view(pe: PEImage, offset: int, length: int = DEFAULT_HEX_LIMIT) -> str:
    """返回从 ``offset`` 开始的十六进制文本（超出范围时返回提示）。"""
    if offset < 0:
        return "(偏移必须是非负整数)"
    if offset >= pe.size and pe.size:
        return f"(偏移 0x{offset:X} 超出文件大小 0x{pe.size:X})"
    end = min(pe.size, offset + max(length, 0))
    return format_hex_dump(pe.data[offset:end], offset=offset)
