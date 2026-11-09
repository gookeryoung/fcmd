"""reshack - Windows PE 资源查看与修改（CLI）。

交互参考 ``bitool_gui.reshack``：打开 exe/dll/sys 等 PE 文件，按
类型 / 名称 / 语言列出资源目录，导出单个资源、替换已有资源或一次性导出
全部资源。解析与写回由 ``pefile`` 完成（安装 ``pip install fcmd[pe]``）。

替换限制
--------
``pefile`` 只能原位覆盖资源数据，无法安全扩容。新数据大于原资源时直接
报错；小于等于原资源时按原大小零填充，保证后续资源偏移不变。写入前会
自动生成 ``<文件名>.backup``（已存在则递增编号）。

示例
----
    fcmd reshack list app.exe
    fcmd reshack extract app.exe 3 1 --output icon.ico
    fcmd reshack replace app.exe 3 1 icon.ico
    fcmd reshack export-all app.exe --output-dir app_resources
"""

from __future__ import annotations

import contextlib
import importlib
import shutil
import struct
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.console import print_error

__all__ = [
    "PE_EXTENSIONS",
    "RESOURCE_EXTENSIONS",
    "RESOURCE_TYPES",
    "RT_BITMAP",
    "RT_ICON",
    "PEError",
    "PEFile",
    "PEFileInfo",
    "ResourceEntry",
    "backup_path",
    "bitmap_to_bmp",
    "export_payload",
    "format_file_info",
    "format_file_size",
    "format_resource_report",
    "format_resource_table",
    "icon_to_ico",
    "is_pe_file",
    "resource_extension",
    "resource_type_name",
]

__version__ = "0.1.0"

_DESCRIPTION = "PE 资源查看与修改（列出/导出/替换/全部导出）；替换写入前自动备份，依赖 fcmd[pe]"

PE_EXTENSIONS = frozenset({".exe", ".dll", ".sys", ".ocx", ".scr", ".cpl", ".ax", ".acm", ".drv"})

RESOURCE_TYPES: dict[int, str] = {
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

RESOURCE_EXTENSIONS: dict[int, str] = {
    1: ".cur",
    2: ".bmp",
    3: ".ico",
    4: ".rc",
    5: ".rc",
    6: ".txt",
    9: ".rc",
    10: ".bin",
    11: ".txt",
    12: ".cur",
    14: ".ico",
    16: ".txt",
    21: ".ani",
    22: ".ani",
    23: ".html",
    24: ".xml",
}

_MACHINES: dict[int, str] = {
    0x014C: "x86（32 位）",
    0x0200: "IA64",
    0x01C0: "ARM",
    0x01C4: "ARMv7",
    0xAA64: "ARM64",
    0x8664: "x64（64 位）",
}

_SUBSYSTEMS: dict[int, str] = {
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


class PEError(ValueError):
    """PE 解析 / 读写的业务错误（CLI 直接打印，不抛堆栈）。"""


RT_BITMAP = 2
RT_ICON = 3


_pefile_module: Any = None


def _require_pefile() -> Any:
    """惰性导入 ``pefile``；未安装时返回 ``None``。"""
    global _pefile_module  # noqa: PLW0603 - 模块级缓存
    if _pefile_module is not None:
        return _pefile_module
    try:
        pefile_module = importlib.import_module("pefile")
    except ImportError:
        return None
    _pefile_module = pefile_module
    return pefile_module


def resource_type_name(type_id: int) -> str:
    """返回资源类型的中文可读名称（未知类型保留 ID）。"""
    return RESOURCE_TYPES.get(type_id, f"Unknown ({type_id})")


def resource_extension(type_id: int) -> str:
    """返回资源类型对应的默认导出扩展名。"""
    return RESOURCE_EXTENSIONS.get(type_id, ".bin")


def bitmap_to_bmp(data: bytes) -> bytes | None:
    """把 RT_BITMAP 的 DIB 数据包装成带 14 字节文件头的 BMP。"""
    if len(data) < 40:
        return None
    header_size = int.from_bytes(data[0:4], "little")
    if header_size < 40:
        return None
    bit_count = int.from_bytes(data[14:16], "little")
    colors_used = int.from_bytes(data[32:36], "little")
    palette = colors_used if colors_used else (1 << bit_count if bit_count <= 8 else 0)
    offset = 14 + header_size + palette * 4
    return b"BM" + struct.pack("<IHHI", 14 + len(data), 0, 0, offset) + data


def icon_to_ico(data: bytes) -> bytes | None:
    """把 RT_ICON 的 DIB 数据包装成单图像 ICO。"""
    if len(data) < 40:
        return None
    width = int.from_bytes(data[4:8], "little", signed=True)
    height = int.from_bytes(data[8:12], "little", signed=True) // 2
    bit_count = int.from_bytes(data[14:16], "little")
    if width <= 0 or height <= 0:
        return None
    entry_width = 0 if width >= 256 else width
    entry_height = 0 if height >= 256 else height
    header = struct.pack("<HHH", 0, 1, 1)
    directory = struct.pack("<BBBBHHII", entry_width, entry_height, 0, 0, 1, bit_count, len(data), 22)
    return header + directory + data


def export_payload(resource: ResourceEntry) -> bytes:
    """返回可直接打开的文件内容：Icon 补 ICO 头、Bitmap 补 BMP 头。

    其余资源原样返回；头部数据无效时退回原始字节。
    """
    if resource.type_id == RT_ICON:
        return icon_to_ico(resource.data) or resource.data
    if resource.type_id == RT_BITMAP:
        return bitmap_to_bmp(resource.data) or resource.data
    return resource.data


def format_file_size(size: int) -> str:
    """把字节数格式化为 ``N B`` / ``N.N KB`` / ``N.N MB``。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def is_pe_file(path: Path) -> bool:
    """判断路径是否为扩展名受支持且带 ``MZ`` / ``PE\\0\\0`` 签名的文件。"""
    if not path.is_file() or path.suffix.lower() not in PE_EXTENSIONS:
        return False
    try:
        with path.open("rb") as handle:
            header = handle.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return False
            offset = int.from_bytes(header[0x3C:0x40], "little")
            if offset + 4 <= len(header):
                signature = header[offset : offset + 4]
            else:
                handle.seek(offset)
                signature = handle.read(4)
    except OSError:
        return False
    return signature == b"PE\x00\x00"


def backup_path(path: Path) -> Path:
    """返回不冲突的备份路径（``file.exe.backup``、``file.exe.backup1``…）。"""
    candidate = path.with_name(path.name + ".backup")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.backup{counter}")
        counter += 1
    return candidate


@dataclass(frozen=True)
class ResourceEntry:
    """一条 PE 资源记录。"""

    type_id: int
    type_name: str
    name_id: int
    language_id: int
    offset: int
    size: int
    data: bytes


@dataclass(frozen=True)
class PEFileInfo:
    """PE 文件头信息。"""

    path: Path
    machine: str
    subsystem: str
    is_dll: bool
    is_64bit: bool
    image_base: int
    entry_point: int
    resource_count: int


def format_file_info(info: PEFileInfo) -> str:
    """格式化 PE 文件头信息为多行文本。"""
    kind = "DLL" if info.is_dll else "可执行文件"
    bits = "64 位" if info.is_64bit else "32 位"
    return "\n".join(
        (
            f"文件: {info.path.name}",
            f"路径: {info.path}",
            f"类型: {kind}（{bits}）",
            f"架构: {info.machine}",
            f"子系统: {info.subsystem}",
            f"镜像基址: 0x{info.image_base:X}",
            f"入口点: 0x{info.entry_point:X}",
            f"资源数量: {info.resource_count}",
        )
    )


def _display_width(text: str) -> int:
    """按终端显示宽度计数（中文等宽字符按 2 列计）。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int) -> str:
    """按终端显示宽度左对齐填充。"""
    return text + " " * max(0, width - _display_width(text))


def format_resource_table(resources: list[ResourceEntry]) -> str:
    """格式化资源列表为对齐表格文本。"""
    if not resources:
        return "(该文件没有资源)"
    header = _pad("类型", 18) + _pad("名称", 8) + _pad("语言", 8) + _pad("大小", 12) + "RVA"
    lines = [header, "-" * _display_width(header)]
    for resource in sorted(resources, key=lambda item: (item.type_id, item.name_id, item.language_id)):
        lines.append(
            _pad(resource.type_name, 18)
            + _pad(str(resource.name_id), 8)
            + _pad(str(resource.language_id), 8)
            + _pad(format_file_size(resource.size), 12)
            + f"0x{resource.offset:X}"
        )
    return "\n".join(lines)


def format_resource_report(
    info: PEFileInfo,
    resources: list[ResourceEntry],
) -> str:
    """组合文件头信息与资源表格。"""
    type_names = sorted({resource.type_name for resource in resources})
    parts = [format_file_info(info)]
    if type_names:
        parts.append(f"资源类型: {', '.join(type_names)}")
    parts.append(format_resource_table(resources))
    return "\n\n".join(parts)


class PEFile:
    """``pefile`` 的轻量封装：资源枚举、导出与替换。"""

    def __init__(self) -> None:
        self._pe: Any = None
        self._cache: list[ResourceEntry] | None = None
        self.path: Path | None = None

    def __enter__(self) -> PEFile:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def open(self, path: Path) -> None:
        """加载 PE 文件；不是 PE 或缺少 ``pefile`` 时抛 :class:`PEError`。"""
        if not path.is_file():
            raise PEError(f"文件不存在: {path}")
        if not is_pe_file(path):
            raise PEError(f"不是有效的 PE 文件: {path}")
        module = _require_pefile()
        if module is None:
            raise PEError("未安装 pefile 库，请安装: pip install fcmd[pe]")
        try:
            pe = module.PE(str(path))
        except Exception as exc:  # pefile 对损坏文件抛自定义异常
            raise PEError(f"解析 PE 文件失败: {path}: {exc}") from exc
        self._pe = pe
        self._cache = None
        self.path = path

    def close(self) -> None:
        """释放 ``pefile`` 句柄。"""
        if self._pe is not None:
            close = getattr(self._pe, "close", None)
            if callable(close):
                close()
        self._pe = None
        self._cache = None
        self.path = None

    def _loaded(self) -> Any:
        if self._pe is None or self.path is None:
            raise PEError("尚未加载 PE 文件")
        return self._pe

    def file_info(self) -> PEFileInfo:
        """读取 PE 文件头信息。"""
        pe = self._loaded()
        path = self._require_path()
        machine = _MACHINES.get(pe.FILE_HEADER.Machine, f"Unknown (0x{pe.FILE_HEADER.Machine:04X})")
        subsystem = _SUBSYSTEMS.get(
            pe.OPTIONAL_HEADER.Subsystem,
            f"Unknown ({pe.OPTIONAL_HEADER.Subsystem})",
        )
        return PEFileInfo(
            path=path,
            machine=machine,
            subsystem=subsystem,
            is_dll=bool(pe.is_dll()),
            is_64bit=pe.OPTIONAL_HEADER.Magic == 0x20B,
            image_base=pe.OPTIONAL_HEADER.ImageBase,
            entry_point=pe.OPTIONAL_HEADER.AddressOfEntryPoint,
            resource_count=len(self.resources()),
        )

    def resources(self) -> list[ResourceEntry]:
        """枚举全部资源（按类型 / 名称 / 语言排序）。"""
        if self._cache is not None:
            return list(self._cache)
        pe = self._loaded()
        directory = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
        entries: list[ResourceEntry] = []
        for type_entry in getattr(directory, "entries", []) if directory is not None else []:
            type_id = getattr(type_entry, "id", None)
            if not isinstance(type_id, int):
                continue
            type_directory = getattr(type_entry, "directory", None)
            for name_entry in getattr(type_directory, "entries", []) if type_directory is not None else []:
                name_id = getattr(name_entry, "id", None)
                if not isinstance(name_id, int):
                    continue
                lang_directory = getattr(name_entry, "directory", None)
                for lang_entry in getattr(lang_directory, "entries", []) if lang_directory is not None else []:
                    data_entry = getattr(lang_entry, "data", None)
                    if data_entry is None:
                        continue
                    rva = int(data_entry.struct.OffsetToData)
                    size = int(data_entry.struct.Size)
                    entries.append(
                        ResourceEntry(
                            type_id=type_id,
                            type_name=resource_type_name(type_id),
                            name_id=name_id,
                            language_id=int(data_entry.lang),
                            offset=rva,
                            size=size,
                            data=self._read(pe, rva, size),
                        )
                    )
        entries.sort(key=lambda item: (item.type_id, item.name_id, item.language_id))
        self._cache = entries
        return list(entries)

    @staticmethod
    def _read(pe: Any, rva: int, size: int) -> bytes:
        try:
            return bytes(pe.get_data(rva, size))
        except (OSError, ValueError):
            return b""

    def resource_types(self) -> dict[int, str]:
        """返回当前文件实际存在的资源类型映射。"""
        return {resource.type_id: resource.type_name for resource in self.resources()}

    def get_resource(
        self,
        type_id: int,
        name_id: int,
        language_id: int = 0,
    ) -> ResourceEntry | None:
        """按类型 / 名称 / 语言查找资源。"""
        for resource in self.resources():
            if resource.type_id == type_id and resource.name_id == name_id and resource.language_id == language_id:
                return resource
        return None

    def export_resource(
        self,
        type_id: int,
        name_id: int,
        output: Path,
        language_id: int = 0,
    ) -> ResourceEntry | None:
        """导出单个资源；资源不存在时返回 ``None``。"""
        resource = self.get_resource(type_id, name_id, language_id)
        if resource is None:
            return None
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(export_payload(resource))
        except OSError as exc:
            raise PEError(f"写入资源文件失败: {output}: {exc}") from exc
        return resource

    def export_all(self, output_dir: Path) -> tuple[int, int, Path]:
        """导出全部资源到 ``output_dir/<类型名>/<名称>_<语言><扩展名>``。

        Returns
        -------
        tuple[int, int, Path]
            ``(成功数量, 总数量, 输出目录)``
        """
        resources = self.resources()
        exported = 0
        for resource in resources:
            target = (
                output_dir
                / resource.type_name
                / f"{resource.name_id}_{resource.language_id}{resource_extension(resource.type_id)}"
            )
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(export_payload(resource))
            except OSError:
                continue
            exported += 1
        return exported, len(resources), output_dir

    def replace_resource(
        self,
        type_id: int,
        name_id: int,
        new_data: bytes,
        language_id: int = 0,
    ) -> tuple[int, int, Path]:
        """用 ``new_data`` 原位替换资源，写入前自动备份。

        新数据大于原资源时抛 :class:`PEError`（``pefile`` 无法安全扩容）；
        小于原资源时零填充到原大小，避免后续资源偏移变化。

        Returns
        -------
        tuple[int, int, Path]
            ``(原大小, 新大小, 备份路径)``
        """
        pe = self._loaded()
        path = self._require_path()
        resource = self.get_resource(type_id, name_id, language_id)
        if resource is None:
            raise PEError(f"未找到资源: Type={type_id}, Name={name_id}, Language={language_id}")
        old_size = resource.size
        new_size = len(new_data)
        if new_size > old_size:
            raise PEError(
                f"新数据 {new_size} 字节大于原资源 {old_size} 字节，pefile 无法安全扩容；请使用不大于原资源的数据"
            )
        struct_obj = self._locate_struct(type_id, name_id, language_id)
        if struct_obj is None:
            raise PEError("在 PE 结构中定位资源失败")

        try:
            file_offset = int(pe.get_offset_from_rva(int(struct_obj.OffsetToData)))
        except Exception as exc:
            raise PEError(f"资源 RVA 转换文件偏移失败: {exc}") from exc
        padded = new_data + b"\x00" * (old_size - new_size)
        try:
            if not pe.set_bytes_at_offset(file_offset, padded):
                raise PEError("写入资源数据失败（偏移超出文件范围）")
            struct_obj.Size = new_size
        except PEError:
            raise
        except Exception as exc:
            raise PEError(f"写入资源数据失败: {exc}") from exc

        backup = backup_path(path)
        try:
            shutil.copy2(path, backup)
        except OSError as exc:
            raise PEError(f"创建备份失败: {backup}: {exc}") from exc
        try:
            pe.write(str(path))
        except Exception as exc:
            with contextlib.suppress(OSError):
                shutil.copy2(backup, path)
                backup.unlink(missing_ok=True)
            raise PEError(f"保存 PE 文件失败: {exc}") from exc
        self._cache = None
        return old_size, new_size, backup

    def _locate_struct(
        self,
        type_id: int,
        name_id: int,
        language_id: int,
    ) -> Any:
        """在 ``pefile`` 资源树中定位数据目录项结构体。"""
        pe = self._loaded()
        directory = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
        for type_entry in getattr(directory, "entries", []) if directory is not None else []:
            if getattr(type_entry, "id", None) != type_id:
                continue
            type_directory = getattr(type_entry, "directory", None)
            for name_entry in getattr(type_directory, "entries", []) if type_directory is not None else []:
                if getattr(name_entry, "id", None) != name_id:
                    continue
                lang_directory = getattr(name_entry, "directory", None)
                for lang_entry in getattr(lang_directory, "entries", []) if lang_directory is not None else []:
                    data_entry = getattr(lang_entry, "data", None)
                    if data_entry is not None and int(data_entry.lang) == language_id:
                        return data_entry.struct
        return None

    def _require_path(self) -> Path:
        if self.path is None:
            raise PEError("尚未加载 PE 文件")
        return self.path


# ---------------------------------------------------------------------- #
# CLI 子命令
# ---------------------------------------------------------------------- #
@fcmd.tool("reshack", subcommand="list", help="列出 PE 文件中的资源")
def list_resources(file: Path) -> None:
    """列出 PE 文件头与全部资源。

    Parameters
    ----------
    file:
        PE 文件路径（exe/dll/sys 等）
    """
    try:
        with PEFile() as pe:
            pe.open(file)
            print(format_resource_report(pe.file_info(), pe.resources()))
    except PEError as exc:
        print_error(str(exc))


@fcmd.tool("reshack", subcommand="extract", help="导出单个资源")
def extract_resource(
    file: Path,
    type_id: int,
    name_id: int,
    language: int = 0,
    output: Path | None = None,
) -> None:
    """导出指定类型 / 名称 / 语言的资源。

    Parameters
    ----------
    file:
        PE 文件路径
    type_id:
        资源类型 ID（如 3=Icon、16=Version、24=Manifest）
    name_id:
        资源名称 ID
    language:
        语言 ID，默认 0
    output:
        输出文件路径；缺省为当前目录下的 ``<文件名>_resource_<类型>_<名称>_<语言>.<扩展名>``
    """
    try:
        with PEFile() as pe:
            pe.open(file)
            target = output or Path(f"{file.stem}_resource_{type_id}_{name_id}_{language}{resource_extension(type_id)}")
            resource = pe.export_resource(type_id, name_id, target, language)
            if resource is None:
                print_error(f"未找到资源: Type={type_id}, Name={name_id}, Language={language}")
                return
            print(f"已导出 {resource.type_name} #{resource.name_id} -> {target}（{format_file_size(resource.size)}）")
    except PEError as exc:
        print_error(str(exc))


@fcmd.tool("reshack", subcommand="replace", help="用新数据替换已有资源")
def replace_resource(
    file: Path,
    type_id: int,
    name_id: int,
    input_path: Path,
    language: int = 0,
) -> None:
    """替换资源数据，写入前自动备份原文件。

    Parameters
    ----------
    file:
        PE 文件路径
    type_id:
        资源类型 ID
    name_id:
        资源名称 ID
    input_path:
        新资源数据文件
    language:
        语言 ID，默认 0
    """
    try:
        new_data = input_path.read_bytes()
    except OSError as exc:
        print_error(f"读取新资源数据失败: {input_path}: {exc}")
        return
    try:
        with PEFile() as pe:
            pe.open(file)
            old_size, new_size, backup = pe.replace_resource(type_id, name_id, new_data, language)
            print(
                f"已替换资源 Type={type_id}, Name={name_id}, Language={language}："
                f"{format_file_size(old_size)} -> {format_file_size(new_size)}"
            )
            print(f"原文件备份: {backup}")
    except PEError as exc:
        print_error(str(exc))


@fcmd.tool("reshack", subcommand="export-all", help="导出全部资源到目录")
def export_all_resources(
    file: Path,
    output_dir: Path | None = None,
) -> None:
    """把全部资源按类型分目录导出。

    Parameters
    ----------
    file:
        PE 文件路径
    output_dir:
        输出目录；缺省为同目录下的 ``<文件名>_resources/``
    """
    try:
        with PEFile() as pe:
            pe.open(file)
            target = output_dir or file.parent / f"{file.stem}_resources"
            exported, total, directory = pe.export_all(target)
            print(f"已导出 {exported}/{total} 个资源 -> {directory}")
    except PEError as exc:
        print_error(str(exc))


@fcmd.main("reshack")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
