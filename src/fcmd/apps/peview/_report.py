"""PE 结构文本化与标志位解读（纯逻辑）。

把 :mod:`fcmd.apps.peview._pe` 的数据模型格式化为 CLI 输出与 GUI 详情面板
共用的多行文本：头、数据目录、节表、导入 / 导出 / 资源 / 重定位 / 调试、
可编辑字段列表与一致性检查。

所有表名与标志位解读集中在本模块，解析层只保留原始数值。
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from fcmd.apps.peview._reader import compute_checksum

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.peview._pe import (
        DebugEntry,
        PEImage,
        RelocationBlock,
        ResourceEntry,
        Section,
    )

__all__ = [
    "DATA_DIRECTORY_NAMES",
    "DEBUG_TYPE_NAMES",
    "DLL_CHARACTERISTICS",
    "FILE_CHARACTERISTICS",
    "MACHINE_NAMES",
    "RELOCATION_TYPE_NAMES",
    "RESOURCE_TYPE_NAMES",
    "SECTION_ALIGNMENT_NAMES",
    "SECTION_CHARACTERISTICS",
    "SUBSYSTEM_NAMES",
    "check_problems",
    "debug_type_name",
    "directory_name",
    "format_check",
    "format_debug",
    "format_directories",
    "format_exports",
    "format_fields",
    "format_file_characteristics",
    "format_headers",
    "format_imports",
    "format_relocations",
    "format_resources",
    "format_section_characteristics",
    "format_sections",
    "format_summary",
    "format_table",
    "machine_name",
    "relocation_type_name",
    "resource_label",
    "resource_type_name",
    "subsystem_name",
]

DATA_DIRECTORY_NAMES: tuple[str, ...] = (
    "导出表 (Export)",
    "导入表 (Import)",
    "资源表 (Resource)",
    "异常表 (Exception)",
    "安全目录 (Security)",
    "基址重定位 (Base Relocation)",
    "调试目录 (Debug)",
    "架构数据 (Architecture)",
    "全局指针 (Global Ptr)",
    "TLS 表",
    "加载配置 (Load Config)",
    "绑定导入 (Bound Import)",
    "导入地址表 (IAT)",
    "延迟导入 (Delay Import)",
    "CLR 运行时 (CLR Runtime)",
    "保留 (Reserved)",
)

MACHINE_NAMES: dict[int, str] = {
    0x014C: "x86（32 位）",
    0x8664: "x64（64 位）",
    0x01C0: "ARM",
    0x01C4: "ARMv7 (Thumb-2)",
    0xAA64: "ARM64",
    0x0200: "IA64",
    0x5032: "RISC-V 32",
    0x5064: "RISC-V 64",
    0x6232: "LoongArch 32",
    0x6264: "LoongArch 64",
}

SUBSYSTEM_NAMES: dict[int, str] = {
    1: "Native",
    2: "Windows GUI",
    3: "Windows Console",
    5: "OS/2 Console",
    7: "POSIX Console",
    9: "Windows CE GUI",
    10: "EFI Application",
    11: "EFI Boot Service Driver",
    12: "EFI Runtime Driver",
    13: "EFI ROM",
    14: "Xbox",
    16: "Windows Boot Application",
}

FILE_CHARACTERISTICS: tuple[tuple[int, str], ...] = (
    (0x0001, "RELOCS_STRIPPED"),
    (0x0002, "EXECUTABLE_IMAGE"),
    (0x0004, "LINE_NUMS_STRIPPED"),
    (0x0008, "LOCAL_SYMS_STRIPPED"),
    (0x0010, "AGGRESSIVE_WS_TRIM"),
    (0x0020, "LARGE_ADDRESS_AWARE"),
    (0x0080, "BYTES_REVERSED_LO"),
    (0x0100, "32BIT_MACHINE"),
    (0x0200, "DEBUG_STRIPPED"),
    (0x0400, "REMOVABLE_RUN_FROM_SWAP"),
    (0x0800, "NET_RUN_FROM_SWAP"),
    (0x1000, "SYSTEM"),
    (0x2000, "DLL"),
    (0x4000, "UP_SYSTEM_ONLY"),
    (0x8000, "BYTES_REVERSED_HI"),
)

DLL_CHARACTERISTICS: tuple[tuple[int, str], ...] = (
    (0x0020, "HIGH_ENTROPY_VA"),
    (0x0040, "DYNAMIC_BASE"),
    (0x0080, "FORCE_INTEGRITY"),
    (0x0100, "NX_COMPAT"),
    (0x0200, "NO_ISOLATION"),
    (0x0400, "NO_SEH"),
    (0x0800, "NO_BIND"),
    (0x1000, "APPCONTAINER"),
    (0x2000, "WDM_DRIVER"),
    (0x4000, "GUARD_CF"),
    (0x8000, "TERMINAL_SERVER_AWARE"),
)

SECTION_ALIGNMENT_NAMES: dict[int, str] = {
    1: "对齐 1 字节",
    2: "对齐 2 字节",
    3: "对齐 4 字节",
    4: "对齐 8 字节",
    5: "对齐 16 字节",
    6: "对齐 32 字节",
    7: "对齐 64 字节",
    8: "对齐 128 字节",
    9: "对齐 256 字节",
    10: "对齐 512 字节",
    11: "对齐 1024 字节",
    12: "对齐 2048 字节",
    13: "对齐 4096 字节",
    14: "对齐 8192 字节",
}

SECTION_CHARACTERISTICS: tuple[tuple[int, str], ...] = (
    (0x00000020, "CNT_CODE"),
    (0x00000040, "CNT_INITIALIZED_DATA"),
    (0x00000080, "CNT_UNINITIALIZED_DATA"),
    (0x00000200, "LNK_INFO"),
    (0x00000800, "LNK_REMOVE"),
    (0x00001000, "LNK_COMDAT"),
    (0x00008000, "GPREL"),
    (0x00020000, "MEM_PURGEABLE"),
    (0x00040000, "MEM_LOCKED"),
    (0x00080000, "MEM_PRELOAD"),
    (0x01000000, "LNK_NRELOC_OVFL"),
    (0x02000000, "MEM_DISCARDABLE"),
    (0x04000000, "MEM_NOT_CACHED"),
    (0x08000000, "MEM_NOT_PAGED"),
    (0x10000000, "MEM_SHARED"),
    (0x20000000, "MEM_EXECUTE"),
    (0x40000000, "MEM_READ"),
    (0x80000000, "MEM_WRITE"),
)

RELOCATION_TYPE_NAMES: dict[int, str] = {
    0: "ABSOLUTE",
    1: "HIGH",
    2: "LOW",
    3: "HIGHLOW",
    4: "HIGHADJ",
    5: "MIPS_JMPADDR / ARM_MOV32 / RISCV_HIGH20",
    7: "THUMB_MOV32",
    9: "MIPS_JMPADDR16",
    10: "DIR64",
}

DEBUG_TYPE_NAMES: dict[int, str] = {
    0: "Unknown",
    1: "COFF",
    2: "CodeView",
    3: "FPO",
    4: "Misc",
    5: "Exception",
    6: "Fixup",
    7: "OMAP To Source",
    8: "OMAP From Source",
    9: "Borland",
    10: "Reserved10",
    11: "CLSID",
    12: "VC Feature",
    13: "POGO",
    14: "ILTCG",
    15: "MPX",
    16: "Repro",
    20: "EX DLL Characteristics",
}

RESOURCE_TYPE_NAMES: dict[int, str] = {
    1: "Cursor",
    2: "Bitmap",
    3: "Icon",
    4: "Menu",
    5: "Dialog",
    6: "String Table",
    7: "Font Directory",
    8: "Font",
    9: "Accelerator",
    10: "Raw Data",
    11: "Message Table",
    12: "Group Cursor",
    14: "Group Icon",
    16: "Version",
    17: "Dialog Include",
    19: "Plug and Play",
    20: "VXD",
    21: "Animated Cursor",
    22: "Animated Icon",
    23: "HTML",
    24: "Manifest",
}


# ---------------------------------------------------------------------- #
# 名称与标志位
# ---------------------------------------------------------------------- #
def machine_name(value: int) -> str:
    """返回机器类型的中文名称（未知保留十六进制值）。"""
    return MACHINE_NAMES.get(value, f"未知 (0x{value:04X})")


def subsystem_name(value: int) -> str:
    """返回子系统的中文名称（未知保留十进制值）。"""
    return SUBSYSTEM_NAMES.get(value, f"未知 ({value})")


def directory_name(index: int) -> str:
    """返回数据目录项名称。"""
    return DATA_DIRECTORY_NAMES[index] if 0 <= index < len(DATA_DIRECTORY_NAMES) else f"目录 {index}"


def relocation_type_name(value: int) -> str:
    """返回重定位类型名称。"""
    return RELOCATION_TYPE_NAMES.get(value, f"类型 {value}")


def debug_type_name(value: int) -> str:
    """返回调试目录类型名称。"""
    return DEBUG_TYPE_NAMES.get(value, f"类型 {value}")


def resource_type_name(type_id: int | None) -> str:
    """返回资源类型名称；未知 ID 保留数值。"""
    if type_id is None:
        return "命名类型"
    return RESOURCE_TYPE_NAMES.get(type_id, f"类型 {type_id}")


def resource_label(entry: ResourceEntry) -> str:
    """返回资源树叶子的一行可读标签：``类型/名称/语言``。"""
    type_text = entry.type_name or resource_type_name(entry.type_id)
    name_text = entry.name or (f"#{entry.name_id}" if entry.name_id is not None else "?")
    language_text = entry.language_name or (str(entry.language) if entry.language is not None else "?")
    return f"{type_text}/{name_text}/{language_text}"


def _format_flags(value: int, table: Sequence[tuple[int, str]]) -> str:
    """把位掩码翻译为「名称、名称」；未知位追加十六进制。"""
    names = [label for bit, label in table if value & bit]
    covered = 0
    for bit, _label in table:
        covered |= bit
    unknown = value & ~covered
    if unknown:
        names.append(f"未知位 0x{unknown:X}")
    return "、".join(names) if names else "无"


def format_file_characteristics(value: int) -> str:
    """解读文件头 ``Characteristics``。"""
    return _format_flags(value, FILE_CHARACTERISTICS)


def format_dll_characteristics(value: int) -> str:
    """解读可选头 ``DllCharacteristics``。"""
    return _format_flags(value, DLL_CHARACTERISTICS)


def format_section_characteristics(value: int) -> str:
    """解读节属性：先对齐码，再逐位名称。"""
    alignment = (value & 0x00F00000) >> 20
    names = [SECTION_ALIGNMENT_NAMES.get(alignment, f"对齐码 {alignment}")] if alignment else []
    flags_text = _format_flags(value & ~0x00F00000, SECTION_CHARACTERISTICS)
    if flags_text != "无":
        names.append(flags_text)
    return "、".join(names) if names else "无"


# ---------------------------------------------------------------------- #
# 表格
# ---------------------------------------------------------------------- #
def _display_width(text: str) -> int:
    """按终端显示宽度计数（中文等宽字符按 2 列计）。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int, align: str) -> str:
    """按显示宽度填充到 ``width`` 列。"""
    padding = max(0, width - _display_width(text))
    return (" " * padding + text) if align == "right" else (text + " " * padding)


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Sequence[str] | None = None) -> str:
    """按显示宽度对齐的多列表格，列间用两个空格分隔。"""
    if not rows:
        return "(无)"
    alignments = list(aligns or ["left"] * len(headers))
    widths = [
        max(_display_width(str(cell)) for cell in (headers[index], *(row[index] for row in rows)))
        for index in range(len(headers))
    ]
    line = "  ".join(_pad(str(headers[index]), widths[index], "left") for index in range(len(headers)))
    separator = "  ".join("-" * width for width in widths)
    body = [
        "  ".join(_pad(str(cell), widths[index], alignments[index]) for index, cell in enumerate(row)) for row in rows
    ]
    return "\n".join((line, separator, *body))


def _rows(*pairs: tuple[str, str]) -> str:
    """把「字段 / 值」对列表格式化为对齐文本。"""
    return format_table(("字段", "值"), [(label, value) for label, value in pairs])


def format_size(size: int) -> str:
    """把字节数格式化为 ``N B`` / ``N.N KB`` / ``N.N MB``。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------- #
# 各视图
# ---------------------------------------------------------------------- #
def format_summary(pe: PEImage) -> str:
    """概览：文件、架构、数量统计、签名与 overlay。"""
    kind = "DLL" if pe.is_dll else "可执行文件"
    bits = "64 位" if pe.is_64bit else "32 位"
    import_symbols = sum(len(library.symbols) for library in pe.imports)
    export_count = len(pe.exports.symbols) if pe.exports is not None else 0
    relocation_count = sum(len(block.entries) for block in pe.relocations)
    lines = [
        f"文件: {pe.path.name}",
        f"路径: {pe.path}",
        f"大小: {format_size(pe.size)}（{pe.size} 字节）",
        f"类型: {kind}（{bits}）",
        f"架构: {machine_name(pe.file_header.machine)}",
        f"子系统: {subsystem_name(pe.optional.subsystem)}",
        f"镜像基址: 0x{pe.optional.image_base:X}",
        f"入口点: 0x{pe.optional.address_of_entry_point:X}",
        f"节数量: {len(pe.sections)}",
        f"导入: {len(pe.imports)} 个库 / {import_symbols} 个符号",
        f"导出: {export_count} 个函数",
        f"资源: {len(pe.resources)} 项",
        f"重定位: {len(pe.relocations)} 块 / {relocation_count} 项",
        f"调试项: {len(pe.debug)}",
        f"校验和: 0x{pe.optional.checksum:08X}",
        f"数字签名: {'有' if pe.signature.present else '无'}",
        f"Overlay: {'无' if not pe.overlay_size else f'0x{pe.overlay_offset:X}（{format_size(pe.overlay_size)}）'}",
    ]
    return "\n".join(lines)


def format_headers(pe: PEImage) -> str:
    """DOS 头、COFF 文件头与可选头。"""
    dos = pe.dos
    file_header = pe.file_header
    optional = pe.optional
    parts = [
        "== DOS 头 ==",
        _rows(
            ("e_magic", f"0x{dos.magic:04X}"),
            ("e_lfanew", f"0x{dos.e_lfanew:X}"),
            ("e_cblp / e_cp", f"{dos.bytes_on_last_page} / {dos.pages}"),
            ("e_crlc / e_cparhdr", f"{dos.relocations} / {dos.header_paragraphs}"),
            ("e_minalloc / e_maxalloc", f"0x{dos.min_extra_paragraphs:X} / 0x{dos.max_extra_paragraphs:X}"),
            ("e_ss / e_sp", f"0x{dos.ss:X} / 0x{dos.sp:X}"),
            ("e_csum", f"0x{dos.checksum:04X}"),
            ("e_ip / e_cs", f"0x{dos.ip:X} / 0x{dos.cs:X}"),
            ("e_lfarlc / e_ovno", f"0x{dos.lfarlc:X} / {dos.ovno}"),
        ),
        "",
        "== COFF 文件头 ==",
        _rows(
            ("Machine", f"{machine_name(file_header.machine)}（0x{file_header.machine:04X}）"),
            ("NumberOfSections", str(file_header.number_of_sections)),
            ("TimeDateStamp", f"0x{file_header.timestamp:08X}"),
            ("PointerToSymbolTable", f"0x{file_header.pointer_to_symbol_table:X}"),
            ("NumberOfSymbols", str(file_header.number_of_symbols)),
            ("SizeOfOptionalHeader", str(file_header.size_of_optional_header)),
            (
                "Characteristics",
                f"0x{file_header.characteristics:04X}（{format_file_characteristics(file_header.characteristics)}）",
            ),
        ),
        "",
        "== 可选头 ==",
        _rows(
            ("Magic", f"0x{optional.magic:X}（{'PE32+' if optional.is_64bit else 'PE32'}）"),
            ("LinkerVersion", f"{optional.major_linker_version}.{optional.minor_linker_version}"),
            ("SizeOfCode", f"0x{optional.size_of_code:X}"),
            ("SizeOfInitializedData", f"0x{optional.size_of_initialized_data:X}"),
            ("SizeOfUninitializedData", f"0x{optional.size_of_uninitialized_data:X}"),
            ("AddressOfEntryPoint", f"0x{optional.address_of_entry_point:X}"),
            ("BaseOfCode", f"0x{optional.base_of_code:X}"),
            (
                "BaseOfData",
                "—" if optional.base_of_data is None else f"0x{optional.base_of_data:X}",
            ),
            ("ImageBase", f"0x{optional.image_base:X}"),
            ("SectionAlignment", f"0x{optional.section_alignment:X}"),
            ("FileAlignment", f"0x{optional.file_alignment:X}"),
            (
                "OperatingSystemVersion",
                f"{optional.major_operating_system_version}.{optional.minor_operating_system_version}",
            ),
            ("ImageVersion", f"{optional.major_image_version}.{optional.minor_image_version}"),
            (
                "SubsystemVersion",
                f"{optional.major_subsystem_version}.{optional.minor_subsystem_version}",
            ),
            ("Win32VersionValue", f"0x{optional.win32_version_value:X}"),
            ("SizeOfImage", f"0x{optional.size_of_image:X}"),
            ("SizeOfHeaders", f"0x{optional.size_of_headers:X}"),
            ("CheckSum", f"0x{optional.checksum:08X}"),
            ("Subsystem", f"{subsystem_name(optional.subsystem)}（{optional.subsystem}）"),
            (
                "DllCharacteristics",
                f"0x{optional.dll_characteristics:04X}（{format_dll_characteristics(optional.dll_characteristics)}）",
            ),
            ("SizeOfStackReserve", f"0x{optional.size_of_stack_reserve:X}"),
            ("SizeOfStackCommit", f"0x{optional.size_of_stack_commit:X}"),
            ("SizeOfHeapReserve", f"0x{optional.size_of_heap_reserve:X}"),
            ("SizeOfHeapCommit", f"0x{optional.size_of_heap_commit:X}"),
            ("LoaderFlags", f"0x{optional.loader_flags:X}"),
            ("NumberOfRvaAndSizes", str(optional.number_of_rva_and_sizes)),
        ),
    ]
    return "\n".join(parts)


def format_directories(pe: PEImage) -> str:
    """数据目录表。"""
    if not pe.optional.directories:
        return "(没有数据目录)"
    rows = [
        (
            str(entry.index),
            directory_name(entry.index),
            f"0x{entry.virtual_address:08X}",
            f"0x{entry.size:X}",
            f"0x{entry.offset:X}",
        )
        for entry in pe.optional.directories
    ]
    return format_table(
        ("#", "名称", "RVA / 文件偏移", "大小", "表项偏移"), rows, ("right", "left", "right", "right", "right")
    )


def _section_rows(sections: Sequence[Section]) -> list[tuple[str, ...]]:
    return [
        (
            str(section.index),
            section.name or f"#{section.index}",
            f"0x{section.virtual_size:X}",
            f"0x{section.virtual_address:X}",
            f"0x{section.size_of_raw_data:X}",
            f"0x{section.pointer_to_raw_data:X}",
            f"0x{section.characteristics:08X}（{format_section_characteristics(section.characteristics)}）",
        )
        for section in sections
    ]


def format_sections(pe: PEImage) -> str:
    """节表。"""
    if not pe.sections:
        return "(没有节)"
    return format_table(
        ("#", "名称", "VirtualSize", "VirtualAddress", "RawSize", "RawPtr", "属性"),
        _section_rows(pe.sections),
        ("right", "left", "right", "right", "right", "right", "left"),
    )


def format_imports(pe: PEImage) -> str:
    """导入与延迟导入。"""
    if not pe.imports:
        return "(没有导入表)"
    lines: list[str] = []
    for library in pe.imports:
        kind = "延迟导入" if library.delayed else "导入"
        lines.append(
            f"[{kind}] {library.name}（{len(library.symbols)} 个符号，描述符 0x{library.descriptor_offset:X}）"
        )
        if not library.symbols:
            lines.append("  (无符号)")
        for symbol in library.symbols:
            if symbol.ordinal is not None:
                lines.append(f"  #{symbol.ordinal}（按序号）")
            else:
                lines.append(f"  {symbol.name}（hint {symbol.hint}）")
    return "\n".join(lines)


def format_exports(pe: PEImage) -> str:
    """导出表。"""
    table = pe.exports
    if table is None:
        return "(没有导出表)"
    if not table.symbols:
        return f"DLL 名: {table.dll_name or '—'}\n(没有导出函数)"
    rows = [
        (
            str(symbol.ordinal),
            symbol.name or "(按序号)",
            f"0x{symbol.rva:X}",
            symbol.forwarder or "",
        )
        for symbol in table.symbols
    ]
    table_text = format_table(("序号", "名称", "RVA", "转发"), rows, ("right", "left", "right", "left"))
    return f"DLL 名: {table.dll_name or '—'}\n基序号: {table.base_ordinal}\n{table_text}"


def format_resources(pe: PEImage) -> str:
    """资源树叶子表。"""
    if not pe.resources:
        return "(没有资源)"
    rows = [
        (
            resource_type_name(entry.type_id) if entry.type_name is None else entry.type_name,
            entry.name or (f"#{entry.name_id}" if entry.name_id is not None else "?"),
            entry.language_name or (str(entry.language) if entry.language is not None else "?"),
            f"0x{entry.rva:X}",
            str(entry.size),
            f"0x{entry.file_offset:X}",
        )
        for entry in pe.resources
    ]
    return format_table(
        ("类型", "名称", "语言", "数据 RVA", "大小", "目录项偏移"),
        rows,
        ("left", "left", "right", "right", "right", "right"),
    )


def _relocation_rows(block: RelocationBlock) -> list[tuple[str, ...]]:
    return [
        (f"0x{block.page_rva:X}", relocation_type_name(entry.relocation_type), f"0x{entry.offset:03X}")
        for entry in block.entries
    ]


def format_relocations(pe: PEImage) -> str:
    """基址重定位块与条目。"""
    if not pe.relocations:
        return "(没有重定位)"
    lines: list[str] = []
    for block in pe.relocations:
        lines.append(f"页 0x{block.page_rva:X}（{len(block.entries)} 项，块偏移 0x{block.offset:X}）")
        if block.entries:
            lines.append(
                format_table(("页 RVA", "类型", "页内偏移"), _relocation_rows(block), ("right", "left", "right"))
            )
        else:
            lines.append("  (仅填充项)")
    return "\n".join(lines)


def _debug_rows(entries: Sequence[DebugEntry]) -> list[tuple[str, ...]]:
    return [
        (
            str(entry.offset),
            debug_type_name(entry.debug_type),
            str(entry.size_of_data),
            f"0x{entry.address_of_raw_data:X}",
            f"0x{entry.pointer_to_raw_data:X}",
            f"0x{entry.timestamp:08X}",
        )
        for entry in entries
    ]


def format_debug(pe: PEImage) -> str:
    """调试目录。"""
    if not pe.debug:
        return "(没有调试目录)"
    return format_table(("表项偏移", "类型", "大小", "数据 RVA", "数据文件偏移", "时间戳"), _debug_rows(pe.debug))


def format_fields(pe: PEImage) -> str:
    """可编辑字段白名单及其当前值。"""
    rows = [
        (
            field.name,
            field.label,
            f"0x{field.offset:X}",
            str(field.size),
            f"0x{field.value:X}",
            str(field.value),
        )
        for field in pe.header_fields.values()
    ]
    return format_table(("字段", "说明", "文件偏移", "宽度", "值（十六进制）", "值（十进制）"), rows)


def check_problems(pe: PEImage) -> list[str]:
    """返回结构一致性告警（空列表表示未发现问题）。"""
    problems: list[str] = []
    expected = compute_checksum(pe.data, pe.optional.offset + 64)
    if expected != pe.optional.checksum:
        problems.append(f"校验和不一致：文件内 0x{pe.optional.checksum:08X}，按当前内容应为 0x{expected:08X}")
    if (
        pe.optional.section_alignment
        and pe.optional.file_alignment
        and pe.optional.section_alignment < pe.optional.file_alignment
    ):
        problems.append(
            f"SectionAlignment（0x{pe.optional.section_alignment:X}）小于 FileAlignment"
            f"（0x{pe.optional.file_alignment:X}）"
        )
    if pe.optional.size_of_image and pe.optional.address_of_entry_point >= pe.optional.size_of_image:
        problems.append(
            f"入口点 0x{pe.optional.address_of_entry_point:X} 超出 SizeOfImage 0x{pe.optional.size_of_image:X}"
        )
    for section in pe.sections:
        raw_end = section.pointer_to_raw_data + section.size_of_raw_data
        if section.size_of_raw_data and raw_end > pe.size:
            problems.append(
                f"节 {section.name or '#' + str(section.index)} 的原始数据超出文件末尾（0x{raw_end:X} > 0x{pe.size:X}）"
            )
    if pe.signature.present and pe.signature.offset + pe.signature.size > pe.size:
        problems.append("签名目录指向的数据超出文件末尾")
    return problems


def format_check(pe: PEImage) -> str:
    """输出校验和、对齐、入口点、签名与 overlay 的检查结论。"""
    expected = compute_checksum(pe.data, pe.optional.offset + 64)
    checksum_state = "一致" if expected == pe.optional.checksum else "不一致"
    problems = check_problems(pe)
    lines = [
        f"文件大小: {pe.size} 字节",
        f"校验和: 文件内 0x{pe.optional.checksum:08X}，计算值 0x{expected:08X}（{checksum_state}）",
        f"节对齐: SectionAlignment=0x{pe.optional.section_alignment:X}，FileAlignment=0x{pe.optional.file_alignment:X}",
        f"入口点: 0x{pe.optional.address_of_entry_point:X} / SizeOfImage 0x{pe.optional.size_of_image:X}",
        (
            "数字签名: 无"
            if not pe.signature.present
            else f"数字签名: 有（文件偏移 0x{pe.signature.offset:X}，{pe.signature.size} 字节）"
        ),
        (
            "Overlay: 无"
            if not pe.overlay_size
            else f"Overlay: 0x{pe.overlay_offset:X}（{format_size(pe.overlay_size)}）"
        ),
    ]
    if problems:
        lines.append("警告:")
        lines.extend(f"  - {problem}" for problem in problems)
    else:
        lines.append("结论: 未发现结构性问题")
    return "\n".join(lines)
