"""PE 只读解析（纯逻辑）。

用标准库 ``struct`` 从字节解析 DOS 头、NT 头、可选头、数据目录、节表，以及
导入 / 延迟导入 / 导出 / 资源 / 重定位 / 调试目录与 Authenticode 签名存在性。

所有读取都经过 :mod:`fcmd.apps.peview._reader` 的边界检查；递归层级与表项
数量设上限并检测循环，损坏文件统一抛 :class:`PEError`，错误信息带文件偏移。
本模块不做任何写入，Tk 与 CLI 只消费这里的数据模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.apps.peview._reader import PEError, Reader, rva_to_offset

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "MAX_DIRECTORY_ENTRIES",
    "PE32_MAGIC",
    "PE32_PLUS_MAGIC",
    "DataDirectory",
    "DebugEntry",
    "DosHeader",
    "ExportSymbol",
    "ExportTable",
    "Field",
    "FileHeader",
    "ImportLibrary",
    "ImportSymbol",
    "OptionalHeader",
    "PEError",
    "PEImage",
    "Relocation",
    "RelocationBlock",
    "ResourceEntry",
    "Section",
    "SignatureInfo",
    "load_pe",
    "parse_pe",
]

PE32_MAGIC = 0x10B
PE32_PLUS_MAGIC = 0x20B

MAX_DIRECTORY_ENTRIES = 16
"""PE/COFF 规范定义的数据目录项上限。"""

MAX_SECTIONS = 96
MAX_RESOURCE_ENTRIES = 4096
MAX_RESOURCE_DEPTH = 3
MAX_IMPORT_DESCRIPTORS = 4096
MAX_IMPORT_SYMBOLS = 8192
MAX_EXPORT_SYMBOLS = 65536
MAX_RELOCATION_BLOCKS = 65536
MAX_RELOCATION_ENTRIES = 1_048_576
MAX_DEBUG_ENTRIES = 64


@dataclass(frozen=True)
class Field:
    """可选头 / 文件头中的一个可定位标量字段。"""

    name: str
    label: str
    offset: int
    size: int
    value: int


@dataclass(frozen=True)
class DosHeader:
    """DOS 头（``IMAGE_DOS_HEADER``）中用于展示与定位的字段。"""

    magic: int
    bytes_on_last_page: int
    pages: int
    relocations: int
    header_paragraphs: int
    min_extra_paragraphs: int
    max_extra_paragraphs: int
    ss: int
    sp: int
    checksum: int
    ip: int
    cs: int
    lfarlc: int
    ovno: int
    e_lfanew: int


@dataclass(frozen=True)
class FileHeader:
    """COFF 文件头（``IMAGE_FILE_HEADER``）。"""

    machine: int
    number_of_sections: int
    timestamp: int
    pointer_to_symbol_table: int
    number_of_symbols: int
    size_of_optional_header: int
    characteristics: int
    offset: int


@dataclass(frozen=True)
class DataDirectory:
    """一个数据目录项。"""

    index: int
    virtual_address: int
    size: int
    offset: int


@dataclass(frozen=True)
class OptionalHeader:
    """可选头（PE32 / PE32+）。"""

    magic: int
    major_linker_version: int
    minor_linker_version: int
    size_of_code: int
    size_of_initialized_data: int
    size_of_uninitialized_data: int
    address_of_entry_point: int
    base_of_code: int
    base_of_data: int | None
    image_base: int
    section_alignment: int
    file_alignment: int
    major_operating_system_version: int
    minor_operating_system_version: int
    major_image_version: int
    minor_image_version: int
    major_subsystem_version: int
    minor_subsystem_version: int
    win32_version_value: int
    size_of_image: int
    size_of_headers: int
    checksum: int
    subsystem: int
    dll_characteristics: int
    size_of_stack_reserve: int
    size_of_stack_commit: int
    size_of_heap_reserve: int
    size_of_heap_commit: int
    loader_flags: int
    number_of_rva_and_sizes: int
    directories: tuple[DataDirectory, ...]
    offset: int

    @property
    def is_64bit(self) -> bool:
        """是否为 PE32+（64 位）可选头。"""
        return self.magic == PE32_PLUS_MAGIC


@dataclass(frozen=True)
class Section:
    """节表项。"""

    index: int
    name: str
    virtual_size: int
    virtual_address: int
    size_of_raw_data: int
    pointer_to_raw_data: int
    characteristics: int
    header_offset: int

    @property
    def characteristics_offset(self) -> int:
        """节属性字段（4 字节）的文件偏移。"""
        return self.header_offset + 36


@dataclass(frozen=True)
class ImportSymbol:
    """导入符号：按名称记录 ``hint``，按序号记录 ``ordinal``。"""

    name: str | None
    ordinal: int | None
    hint: int | None
    thunk_rva: int


@dataclass(frozen=True)
class ImportLibrary:
    """一个导入描述符（普通或延迟导入）。"""

    name: str
    delayed: bool
    symbols: tuple[ImportSymbol, ...]
    descriptor_offset: int


@dataclass(frozen=True)
class ExportSymbol:
    """导出符号。"""

    name: str | None
    ordinal: int
    rva: int
    forwarder: str | None


@dataclass(frozen=True)
class ExportTable:
    """导出目录。"""

    dll_name: str | None
    base_ordinal: int
    symbols: tuple[ExportSymbol, ...]
    offset: int


@dataclass(frozen=True)
class ResourceEntry:
    """资源树叶子（类型 / 名称 / 语言三级）。"""

    type_id: int | None
    type_name: str | None
    name_id: int | None
    name: str | None
    language: int | None
    language_name: str | None
    rva: int
    size: int
    file_offset: int


@dataclass(frozen=True)
class Relocation:
    """基址重定位项。"""

    relocation_type: int
    offset: int


@dataclass(frozen=True)
class RelocationBlock:
    """一个重定位块（按 4K 页）。"""

    page_rva: int
    entries: tuple[Relocation, ...]
    offset: int


@dataclass(frozen=True)
class DebugEntry:
    """调试目录项。"""

    characteristics: int
    timestamp: int
    major_version: int
    minor_version: int
    debug_type: int
    size_of_data: int
    address_of_raw_data: int
    pointer_to_raw_data: int
    offset: int


@dataclass(frozen=True)
class SignatureInfo:
    """Authenticode 签名（Security 目录）存在性。"""

    present: bool
    offset: int
    size: int


@dataclass(frozen=True)
class PEImage:
    """一次解析得到的完整只读映像。"""

    path: Path
    data: bytes = field(repr=False, compare=False)
    dos: DosHeader
    file_header: FileHeader
    optional: OptionalHeader
    sections: tuple[Section, ...]
    imports: tuple[ImportLibrary, ...]
    exports: ExportTable | None
    resources: tuple[ResourceEntry, ...]
    relocations: tuple[RelocationBlock, ...]
    debug: tuple[DebugEntry, ...]
    signature: SignatureInfo
    overlay_offset: int
    overlay_size: int
    header_fields: dict[str, Field] = field(repr=False, compare=False)
    section_table_offset: int = 0

    @property
    def size(self) -> int:
        """文件字节数。"""
        return len(self.data)

    @property
    def is_64bit(self) -> bool:
        """是否为 64 位映像。"""
        return self.optional.is_64bit

    @property
    def is_dll(self) -> bool:
        """是否为 DLL（``IMAGE_FILE_DLL``）。"""
        return bool(self.file_header.characteristics & 0x2000)

    def directory(self, index: int) -> DataDirectory | None:
        """返回第 ``index`` 个非空数据目录项，缺失或为空时返回 ``None``。"""
        if 0 <= index < len(self.optional.directories):
            entry = self.optional.directories[index]
            if entry.virtual_address or entry.size:
                return entry
        return None


def load_pe(path: Path) -> PEImage:
    """读取并解析 PE 文件。"""
    if not path.is_file():
        raise PEError(f"文件不存在: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PEError(f"读取文件失败: {path}: {exc}") from exc
    return parse_pe(data, path)


def parse_pe(data: bytes, path: Path | None = None) -> PEImage:
    """解析 PE 字节，返回只读数据模型。"""
    reader = Reader(data)
    reader.require(0, 64, "DOS 头")
    magic = reader.u16(0, "DOS 头 e_magic")
    if magic != 0x5A4D:
        raise PEError(f"不是 PE 文件：缺少 MZ 签名（e_magic=0x{magic:04X}）")

    dos = DosHeader(
        magic=magic,
        bytes_on_last_page=reader.u16(2, "DOS 头 e_cblp"),
        pages=reader.u16(4, "DOS 头 e_cp"),
        relocations=reader.u16(6, "DOS 头 e_crlc"),
        header_paragraphs=reader.u16(8, "DOS 头 e_cparhdr"),
        min_extra_paragraphs=reader.u16(10, "DOS 头 e_minalloc"),
        max_extra_paragraphs=reader.u16(12, "DOS 头 e_maxalloc"),
        ss=reader.u16(14, "DOS 头 e_ss"),
        sp=reader.u16(16, "DOS 头 e_sp"),
        checksum=reader.u16(18, "DOS 头 e_csum"),
        ip=reader.u16(20, "DOS 头 e_ip"),
        cs=reader.u16(22, "DOS 头 e_cs"),
        lfarlc=reader.u16(24, "DOS 头 e_lfarlc"),
        ovno=reader.u16(26, "DOS 头 e_ovno"),
        e_lfanew=reader.u32(60, "DOS 头 e_lfanew"),
    )

    signature_offset = dos.e_lfanew
    signature = reader.read(signature_offset, 4, "PE 签名")
    if signature != b"PE\x00\x00":
        raise PEError(f"PE 签名无效：偏移 0x{signature_offset:X} 处不是 'PE\\0\\0'")

    file_header_offset = signature_offset + 4
    file_header = FileHeader(
        machine=reader.u16(file_header_offset, "文件头 Machine"),
        number_of_sections=reader.u16(file_header_offset + 2, "文件头 NumberOfSections"),
        timestamp=reader.u32(file_header_offset + 4, "文件头 TimeDateStamp"),
        pointer_to_symbol_table=reader.u32(file_header_offset + 8, "文件头 PointerToSymbolTable"),
        number_of_symbols=reader.u32(file_header_offset + 12, "文件头 NumberOfSymbols"),
        size_of_optional_header=reader.u16(file_header_offset + 16, "文件头 SizeOfOptionalHeader"),
        characteristics=reader.u16(file_header_offset + 18, "文件头 Characteristics"),
        offset=file_header_offset,
    )
    if file_header.number_of_sections > MAX_SECTIONS:
        raise PEError(f"节表项数量异常：{file_header.number_of_sections}（上限 {MAX_SECTIONS}）")

    optional_offset = file_header_offset + 20
    reader.require(optional_offset, file_header.size_of_optional_header, "可选头")
    optional_magic = reader.u16(optional_offset, "可选头 Magic")
    if optional_magic not in (PE32_MAGIC, PE32_PLUS_MAGIC):
        raise PEError(f"可选头 Magic 无效：0x{optional_magic:X}（应为 PE32 0x10B 或 PE32+ 0x20B）")
    is_64bit = optional_magic == PE32_PLUS_MAGIC
    fixed_size = 112 if is_64bit else 96
    if file_header.size_of_optional_header < fixed_size:
        raise PEError(
            f"可选头长度 {file_header.size_of_optional_header} 小于最小长度 {fixed_size}"
            f"（{'PE32+' if is_64bit else 'PE32'}）"
        )

    directories_offset = optional_offset + fixed_size
    declared = reader.u32(optional_offset + (108 if is_64bit else 92), "可选头 NumberOfRvaAndSizes")
    capacity = (file_header.size_of_optional_header - fixed_size) // 8
    if declared > capacity:
        raise PEError(f"数据目录声明 {declared} 项，超出可选头剩余空间可容纳的 {capacity} 项")
    directory_count = min(declared, MAX_DIRECTORY_ENTRIES, capacity)
    directories = tuple(
        DataDirectory(
            index=index,
            virtual_address=reader.u32(directories_offset + index * 8, f"数据目录[{index}] RVA"),
            size=reader.u32(directories_offset + index * 8 + 4, f"数据目录[{index}] Size"),
            offset=directories_offset + index * 8,
        )
        for index in range(directory_count)
    )

    optional = OptionalHeader(
        magic=optional_magic,
        major_linker_version=reader.u8(optional_offset + 2, "可选头 MajorLinkerVersion"),
        minor_linker_version=reader.u8(optional_offset + 3, "可选头 MinorLinkerVersion"),
        size_of_code=reader.u32(optional_offset + 4, "可选头 SizeOfCode"),
        size_of_initialized_data=reader.u32(optional_offset + 8, "可选头 SizeOfInitializedData"),
        size_of_uninitialized_data=reader.u32(optional_offset + 12, "可选头 SizeOfUninitializedData"),
        address_of_entry_point=reader.u32(optional_offset + 16, "可选头 AddressOfEntryPoint"),
        base_of_code=reader.u32(optional_offset + 20, "可选头 BaseOfCode"),
        base_of_data=None if is_64bit else reader.u32(optional_offset + 24, "可选头 BaseOfData"),
        image_base=reader.u64(optional_offset + 24, "可选头 ImageBase")
        if is_64bit
        else reader.u32(optional_offset + 28, "可选头 ImageBase"),
        section_alignment=reader.u32(optional_offset + 32, "可选头 SectionAlignment"),
        file_alignment=reader.u32(optional_offset + 36, "可选头 FileAlignment"),
        major_operating_system_version=reader.u16(optional_offset + 40, "可选头 MajorOperatingSystemVersion"),
        minor_operating_system_version=reader.u16(optional_offset + 42, "可选头 MinorOperatingSystemVersion"),
        major_image_version=reader.u16(optional_offset + 44, "可选头 MajorImageVersion"),
        minor_image_version=reader.u16(optional_offset + 46, "可选头 MinorImageVersion"),
        major_subsystem_version=reader.u16(optional_offset + 48, "可选头 MajorSubsystemVersion"),
        minor_subsystem_version=reader.u16(optional_offset + 50, "可选头 MinorSubsystemVersion"),
        win32_version_value=reader.u32(optional_offset + 52, "可选头 Win32VersionValue"),
        size_of_image=reader.u32(optional_offset + 56, "可选头 SizeOfImage"),
        size_of_headers=reader.u32(optional_offset + 60, "可选头 SizeOfHeaders"),
        checksum=reader.u32(optional_offset + 64, "可选头 CheckSum"),
        subsystem=reader.u16(optional_offset + 68, "可选头 Subsystem"),
        dll_characteristics=reader.u16(optional_offset + 70, "可选头 DllCharacteristics"),
        size_of_stack_reserve=reader.u64(optional_offset + 72, "可选头 SizeOfStackReserve")
        if is_64bit
        else reader.u32(optional_offset + 72, "可选头 SizeOfStackReserve"),
        size_of_stack_commit=reader.u64(optional_offset + (80 if is_64bit else 76), "可选头 SizeOfStackCommit")
        if is_64bit
        else reader.u32(optional_offset + 76, "可选头 SizeOfStackCommit"),
        size_of_heap_reserve=reader.u64(optional_offset + (88 if is_64bit else 80), "可选头 SizeOfHeapReserve")
        if is_64bit
        else reader.u32(optional_offset + 80, "可选头 SizeOfHeapReserve"),
        size_of_heap_commit=reader.u64(optional_offset + (96 if is_64bit else 84), "可选头 SizeOfHeapCommit")
        if is_64bit
        else reader.u32(optional_offset + 84, "可选头 SizeOfHeapCommit"),
        loader_flags=reader.u32(optional_offset + (104 if is_64bit else 88), "可选头 LoaderFlags"),
        number_of_rva_and_sizes=declared,
        directories=directories,
        offset=optional_offset,
    )

    section_table_offset = optional_offset + file_header.size_of_optional_header
    reader.require(section_table_offset, file_header.number_of_sections * 40, "节表")
    sections = tuple(
        _parse_section(reader, section_table_offset + index * 40, index)
        for index in range(file_header.number_of_sections)
    )

    imports = _parse_imports(reader, optional, sections, delayed=False)
    delay_imports = _parse_imports(reader, optional, sections, delayed=True)
    exports = _parse_exports(reader, optional, sections)
    resources = _parse_resources(reader, optional, sections)
    relocations = _parse_relocations(reader, optional, sections)
    debug = _parse_debug(reader, optional, sections)
    signature_info = _parse_signature(optional, len(data))
    overlay_offset, overlay_size = _parse_overlay(optional, sections, len(data))

    return PEImage(
        path=path or Path("<memory>"),
        data=data,
        dos=dos,
        file_header=file_header,
        optional=optional,
        sections=sections,
        imports=imports + delay_imports,
        exports=exports,
        resources=resources,
        relocations=relocations,
        debug=debug,
        signature=signature_info,
        overlay_offset=overlay_offset,
        overlay_size=overlay_size,
        header_fields=_build_fields(optional, file_header, sections),
        section_table_offset=section_table_offset,
    )


# ---------------------------------------------------------------------- #
# 各目录解析
# ---------------------------------------------------------------------- #
def _parse_section(reader: Reader, offset: int, index: int) -> Section:
    """解析一个 40 字节的节表项。"""
    raw_name = reader.read(offset, 8, f"节表[{index}] Name")
    name = raw_name.split(b"\x00", 1)[0].decode("latin-1")
    return Section(
        index=index,
        name=name,
        virtual_size=reader.u32(offset + 8, f"节表[{index}] VirtualSize"),
        virtual_address=reader.u32(offset + 12, f"节表[{index}] VirtualAddress"),
        size_of_raw_data=reader.u32(offset + 16, f"节表[{index}] SizeOfRawData"),
        pointer_to_raw_data=reader.u32(offset + 20, f"节表[{index}] PointerToRawData"),
        characteristics=reader.u32(offset + 36, f"节表[{index}] Characteristics"),
        header_offset=offset,
    )


def _directory(optional: OptionalHeader, index: int) -> DataDirectory | None:
    """按索引取非空数据目录项。"""
    if 0 <= index < len(optional.directories):
        entry = optional.directories[index]
        if entry.virtual_address or entry.size:
            return entry
    return None


def _rva_offset(optional: OptionalHeader, sections: Iterable[Section], rva: int, size: int, what: str) -> int:
    """把目录 / 字符串 RVA 换算为文件偏移。"""
    return rva_to_offset(
        rva,
        sections,
        size_of_headers=optional.size_of_headers,
        size=size,
        what=what,
    )


def _parse_imports(
    reader: Reader,
    optional: OptionalHeader,
    sections: tuple[Section, ...],
    *,
    delayed: bool,
) -> tuple[ImportLibrary, ...]:
    """解析导入表（``delayed`` 为真时解析延迟导入描述符）。"""
    directory = _directory(optional, 13 if delayed else 1)
    if directory is None:
        return ()
    descriptor_size = 32 if delayed else 20
    offset = _rva_offset(optional, sections, directory.virtual_address, descriptor_size, "导入目录")
    width = 8 if optional.is_64bit else 4
    libraries: list[ImportLibrary] = []
    for index in range(MAX_IMPORT_DESCRIPTORS):
        position = offset + index * descriptor_size
        reader.require(position, descriptor_size, "导入描述符")
        raw = reader.read(position, descriptor_size, "导入描述符")
        if not any(raw):
            break
        if delayed:
            attributes = int.from_bytes(raw[0:4], "little")
            name_rva = int.from_bytes(raw[4:8], "little")
            thunk_rva = int.from_bytes(raw[16:20], "little")
            if not attributes & 1:
                name_rva -= optional.image_base
                thunk_rva -= optional.image_base
                if name_rva < 0 or thunk_rva < 0:
                    raise PEError(f"延迟导入描述符 VA 小于镜像基址（偏移 0x{position:X}）")
        else:
            name_rva = int.from_bytes(raw[12:16], "little")
            thunk_rva = int.from_bytes(raw[0:4], "little") or int.from_bytes(raw[16:20], "little")
        if not name_rva:
            raise PEError(f"导入描述符缺少 DLL 名（偏移 0x{position:X}）")
        name_offset = _rva_offset(optional, sections, name_rva, 1, "导入 DLL 名")
        name = reader.cstring(name_offset, "导入 DLL 名")
        symbols = _parse_thunks(reader, optional, sections, thunk_rva, width) if thunk_rva else ()
        libraries.append(ImportLibrary(name=name, delayed=delayed, symbols=symbols, descriptor_offset=position))
    else:
        raise PEError(f"导入描述符数量超过上限 {MAX_IMPORT_DESCRIPTORS}")
    return tuple(libraries)


def _parse_thunks(
    reader: Reader,
    optional: OptionalHeader,
    sections: tuple[Section, ...],
    table_rva: int,
    width: int,
) -> tuple[ImportSymbol, ...]:
    """解析 IAT / ILT 的 thunk 数组。"""
    symbols: list[ImportSymbol] = []
    flag = 1 << (63 if width == 8 else 31)
    table_offset = _rva_offset(optional, sections, table_rva, width, "导入 thunk 表")
    for index in range(MAX_IMPORT_SYMBOLS):
        position = table_offset + index * width
        entry = reader.u64(position, "导入 thunk") if width == 8 else reader.u32(position, "导入 thunk")
        if entry == 0:
            break
        thunk_rva = table_rva + index * width
        if entry & flag:
            symbols.append(ImportSymbol(name=None, ordinal=entry & 0xFFFF, hint=None, thunk_rva=thunk_rva))
            continue
        name_rva = entry & (flag - 1)
        name_offset = _rva_offset(optional, sections, name_rva, 2, "导入符号名")
        hint = reader.u16(name_offset, "导入符号 Hint")
        name = reader.cstring(name_offset + 2, "导入符号名")
        symbols.append(ImportSymbol(name=name, ordinal=None, hint=hint, thunk_rva=thunk_rva))
    if len(symbols) >= MAX_IMPORT_SYMBOLS:
        raise PEError(f"导入符号数量超过上限 {MAX_IMPORT_SYMBOLS}")
    return tuple(symbols)


def _parse_exports(
    reader: Reader,
    optional: OptionalHeader,
    sections: tuple[Section, ...],
) -> ExportTable | None:
    """解析导出目录（含转发器）。"""
    directory = _directory(optional, 0)
    if directory is None:
        return None
    offset = _rva_offset(optional, sections, directory.virtual_address, 40, "导出目录")
    name_rva = reader.u32(offset + 12, "导出目录 Name")
    base_ordinal = reader.u32(offset + 16, "导出目录 Base")
    number_of_functions = reader.u32(offset + 20, "导出目录 NumberOfFunctions")
    number_of_names = reader.u32(offset + 24, "导出目录 NumberOfNames")
    functions_rva = reader.u32(offset + 28, "导出目录 AddressOfFunctions")
    names_rva = reader.u32(offset + 32, "导出目录 AddressOfNames")
    ordinals_rva = reader.u32(offset + 36, "导出目录 AddressOfNameOrdinals")
    if number_of_functions > MAX_EXPORT_SYMBOLS or number_of_names > MAX_EXPORT_SYMBOLS:
        raise PEError(
            f"导出表数量异常：函数 {number_of_functions}，名称 {number_of_names}（上限 {MAX_EXPORT_SYMBOLS}）"
        )

    names: dict[int, str] = {}
    if number_of_names:
        names_offset = _rva_offset(optional, sections, names_rva, number_of_names * 4, "导出名称表")
        ordinals_offset = _rva_offset(optional, sections, ordinals_rva, number_of_names * 2, "导出序号表")
        for index in range(number_of_names):
            symbol_rva = reader.u32(names_offset + index * 4, "导出名称 RVA")
            ordinal = reader.u16(ordinals_offset + index * 2, "导出名称序号")
            if symbol_rva == 0 or ordinal in names:
                continue
            symbol_offset = _rva_offset(optional, sections, symbol_rva, 1, "导出符号名")
            names[ordinal] = reader.cstring(symbol_offset, "导出符号名")

    symbols: list[ExportSymbol] = []
    if number_of_functions:
        functions_offset = _rva_offset(optional, sections, functions_rva, number_of_functions * 4, "导出函数表")
        directory_end = directory.virtual_address + directory.size
        for index in range(number_of_functions):
            function_rva = reader.u32(functions_offset + index * 4, "导出函数 RVA")
            if function_rva == 0:
                continue
            forwarder = None
            if directory.virtual_address <= function_rva < directory_end:
                forwarder_offset = _rva_offset(optional, sections, function_rva, 1, "导出转发器")
                forwarder = reader.cstring(forwarder_offset, "导出转发器")
            symbols.append(
                ExportSymbol(
                    name=names.get(index),
                    ordinal=base_ordinal + index,
                    rva=function_rva,
                    forwarder=forwarder,
                )
            )

    dll_name = None
    if name_rva:
        dll_name_offset = _rva_offset(optional, sections, name_rva, 1, "导出 DLL 名")
        dll_name = reader.cstring(dll_name_offset, "导出 DLL 名")
    return ExportTable(
        dll_name=dll_name,
        base_ordinal=base_ordinal,
        symbols=tuple(symbols),
        offset=offset,
    )


def _parse_resources(
    reader: Reader,
    optional: OptionalHeader,
    sections: tuple[Section, ...],
) -> tuple[ResourceEntry, ...]:
    """遍历资源目录树，收集三级叶子。"""
    directory = _directory(optional, 2)
    if directory is None:
        return ()
    base_offset = _rva_offset(optional, sections, directory.virtual_address, directory.size, "资源目录")
    entries: list[ResourceEntry] = []
    _walk_resource_dir(reader, base_offset, base_offset, 0, (), entries, set())
    return tuple(entries)


def _resource_key(raw: int, reader: Reader, base_offset: int, what: str) -> tuple[int | None, str | None]:
    """解析资源目录项的名称字段：高位为 1 表示字符串偏移，否则为整数 ID。"""
    if raw & 0x80000000:
        name_offset = base_offset + (raw & 0x7FFFFFFF)
        reader.require(name_offset, 2, what)
        length = reader.u16(name_offset, what)
        data = reader.read(name_offset + 2, length * 2, what)
        return None, data.decode("utf-16-le", errors="replace")
    return raw & 0xFFFF, None


def _walk_resource_dir(  # noqa: PLR0913, PLR0917 - 递归遍历需要携带基址 / 层级 / 路径 / 输出
    reader: Reader,
    base_offset: int,
    directory_offset: int,
    depth: int,
    keys: tuple[tuple[int | None, str | None], ...],
    out: list[ResourceEntry],
    active: set[int],
) -> None:
    """递归遍历资源目录；``active`` 记录当前路径上的目录偏移以检测循环。"""
    if depth >= MAX_RESOURCE_DEPTH:
        raise PEError(f"资源目录层级超过 {MAX_RESOURCE_DEPTH} 层（偏移 0x{directory_offset:X}）")
    if directory_offset in active:
        raise PEError(f"资源目录存在循环引用（偏移 0x{directory_offset:X}）")
    active.add(directory_offset)
    try:
        named = reader.u16(directory_offset + 12, "资源目录 NamedEntries")
        ids = reader.u16(directory_offset + 14, "资源目录 IdEntries")
        count = named + ids
        if count > MAX_RESOURCE_ENTRIES:
            raise PEError(f"资源目录项数量异常：{count}（上限 {MAX_RESOURCE_ENTRIES}）")
        entries_offset = directory_offset + 16
        for index in range(count):
            position = entries_offset + index * 8
            raw_name = reader.u32(position, "资源目录项 Name")
            raw_offset = reader.u32(position + 4, "资源目录项 Offset")
            key = _resource_key(raw_name, reader, base_offset, "资源名称")
            child_keys = (*keys, key)
            if raw_offset & 0x80000000:
                child_offset = base_offset + (raw_offset & 0x7FFFFFFF)
                _walk_resource_dir(reader, base_offset, child_offset, depth + 1, child_keys, out, active)
            else:
                data_offset = base_offset + raw_offset
                data_rva = reader.u32(data_offset, "资源数据 RVA")
                size = reader.u32(data_offset + 4, "资源数据 Size")
                padded = (*child_keys, *((None, None),) * (MAX_RESOURCE_DEPTH - len(child_keys)))
                out.append(_make_resource(padded, data_rva, size, data_offset))
    finally:
        active.discard(directory_offset)


def _make_resource(
    keys: tuple[tuple[int | None, str | None], ...],
    rva: int,
    size: int,
    data_offset: int,
) -> ResourceEntry:
    """把三级键组合成资源记录。"""
    type_id, type_name = keys[0]
    name_id, name = keys[1]
    language, language_name = keys[2]
    return ResourceEntry(
        type_id=type_id,
        type_name=type_name,
        name_id=name_id,
        name=name,
        language=language,
        language_name=language_name,
        rva=rva,
        size=size,
        file_offset=data_offset,
    )


def _parse_relocations(
    reader: Reader,
    optional: OptionalHeader,
    sections: tuple[Section, ...],
) -> tuple[RelocationBlock, ...]:
    """解析基址重定位块。"""
    directory = _directory(optional, 5)
    if directory is None or directory.size == 0:
        return ()
    offset = _rva_offset(optional, sections, directory.virtual_address, directory.size, "重定位目录")
    end = offset + directory.size
    blocks: list[RelocationBlock] = []
    position = offset
    total_entries = 0
    while position + 8 <= end:
        page_rva = reader.u32(position, "重定位块 PageRVA")
        block_size = reader.u32(position + 4, "重定位块 BlockSize")
        if block_size < 8 or position + block_size > end:
            raise PEError(f"重定位块大小非法：{block_size}（偏移 0x{position:X}）")
        count = (block_size - 8) // 2
        total_entries += count
        if total_entries > MAX_RELOCATION_ENTRIES:
            raise PEError(f"重定位项数量超过上限 {MAX_RELOCATION_ENTRIES}")
        entries = []
        for index in range(count):
            value = reader.u16(position + 8 + index * 2, "重定位项")
            relocation_type = value >> 12
            if relocation_type == 0:
                continue
            entries.append(Relocation(relocation_type=relocation_type, offset=value & 0xFFF))
        blocks.append(RelocationBlock(page_rva=page_rva, entries=tuple(entries), offset=position))
        if len(blocks) > MAX_RELOCATION_BLOCKS:
            raise PEError(f"重定位块数量超过上限 {MAX_RELOCATION_BLOCKS}")
        position += block_size
    return tuple(blocks)


def _parse_debug(
    reader: Reader,
    optional: OptionalHeader,
    sections: tuple[Section, ...],
) -> tuple[DebugEntry, ...]:
    """解析调试目录项。"""
    directory = _directory(optional, 6)
    if directory is None or directory.size == 0:
        return ()
    count = min(directory.size // 28, MAX_DEBUG_ENTRIES)
    if count == 0:
        return ()
    offset = _rva_offset(optional, sections, directory.virtual_address, count * 28, "调试目录")
    return tuple(
        DebugEntry(
            characteristics=reader.u32(offset + index * 28, "调试项 Characteristics"),
            timestamp=reader.u32(offset + index * 28 + 4, "调试项 TimeDateStamp"),
            major_version=reader.u16(offset + index * 28 + 8, "调试项 MajorVersion"),
            minor_version=reader.u16(offset + index * 28 + 10, "调试项 MinorVersion"),
            debug_type=reader.u32(offset + index * 28 + 12, "调试项 Type"),
            size_of_data=reader.u32(offset + index * 28 + 16, "调试项 SizeOfData"),
            address_of_raw_data=reader.u32(offset + index * 28 + 20, "调试项 AddressOfRawData"),
            pointer_to_raw_data=reader.u32(offset + index * 28 + 24, "调试项 PointerToRawData"),
            offset=offset + index * 28,
        )
        for index in range(count)
    )


def _parse_signature(optional: OptionalHeader, file_size: int) -> SignatureInfo:
    """读取 Security 目录（其 RVA 字段实际是文件偏移）。"""
    directory = _directory(optional, 4)
    if directory is None or directory.size == 0:
        return SignatureInfo(present=False, offset=0, size=0)
    present = 0 < directory.virtual_address < file_size
    return SignatureInfo(present=present, offset=directory.virtual_address, size=directory.size)


def _parse_overlay(
    optional: OptionalHeader,
    sections: tuple[Section, ...],
    file_size: int,
) -> tuple[int, int]:
    """计算 overlay（节数据之后追加的字节）偏移与大小。"""
    raw_end = min(optional.size_of_headers, file_size)
    for section in sections:
        if section.pointer_to_raw_data <= file_size:
            raw_end = max(raw_end, section.pointer_to_raw_data + section.size_of_raw_data)
    raw_end = min(raw_end, file_size)
    if raw_end < file_size:
        return raw_end, file_size - raw_end
    return 0, 0


def _build_fields(
    optional: OptionalHeader,
    file_header: FileHeader,
    sections: tuple[Section, ...],
) -> dict[str, Field]:
    """构造可编辑字段白名单的定位信息（值来自本次解析）。"""
    offset = optional.offset
    pointer_size = 8 if optional.is_64bit else 4
    fields = {
        "entry_point": Field(
            "entry_point", "入口点 (AddressOfEntryPoint)", offset + 16, 4, optional.address_of_entry_point
        ),
        "image_base": Field(
            "image_base",
            "镜像基址 (ImageBase)",
            offset + (24 if optional.is_64bit else 28),
            pointer_size,
            optional.image_base,
        ),
        "subsystem": Field("subsystem", "子系统 (Subsystem)", offset + 68, 2, optional.subsystem),
        "dll_characteristics": Field(
            "dll_characteristics", "DLL 特性 (DllCharacteristics)", offset + 70, 2, optional.dll_characteristics
        ),
        "timestamp": Field("timestamp", "时间戳 (TimeDateStamp)", file_header.offset + 4, 4, file_header.timestamp),
        "stack_reserve": Field(
            "stack_reserve", "栈保留 (SizeOfStackReserve)", offset + 72, pointer_size, optional.size_of_stack_reserve
        ),
        "stack_commit": Field(
            "stack_commit",
            "栈提交 (SizeOfStackCommit)",
            offset + 72 + pointer_size,
            pointer_size,
            optional.size_of_stack_commit,
        ),
        "heap_reserve": Field(
            "heap_reserve",
            "堆保留 (SizeOfHeapReserve)",
            offset + 72 + pointer_size * 2,
            pointer_size,
            optional.size_of_heap_reserve,
        ),
        "heap_commit": Field(
            "heap_commit",
            "堆提交 (SizeOfHeapCommit)",
            offset + 72 + pointer_size * 3,
            pointer_size,
            optional.size_of_heap_commit,
        ),
        "checksum": Field("checksum", "校验和 (CheckSum)", offset + 64, 4, optional.checksum),
    }
    for section in sections:
        name = f"section.{section.index}.characteristics"
        fields[name] = Field(
            name,
            f"节 {section.name or '#' + str(section.index)} 属性 (Characteristics)",
            section.characteristics_offset,
            4,
            section.characteristics,
        )
    return fields
