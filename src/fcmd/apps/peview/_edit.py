"""peview 的编辑逻辑：字段白名单、值校验、内存打补丁与安全写入。

安全边界（与 ADR 0018 D4 一致）：

- 只允许修改 :class:`~fcmd.apps.peview._pe.PEImage` ``header_fields`` 里的标量字段；
  ``e_lfanew`` / ``Magic`` / 节大小与地址 / 数据目录等结构性字段一律拒绝；
- 写入前校验取值范围（外加 ``ImageBase`` 64K 对齐、入口点小于 ``SizeOfImage``、
  节对齐码合法），任何一项不合法则整批不写；
- 先解析补丁后的字节并做结构指纹比对，再写同目录临时文件、重新解析临时文件，
  通过后创建 ``<文件名>.backup[N]`` 备份并 ``os.replace`` 原子替换；
- 任一步失败都删除临时文件，原文件保持原样；写入带 Authenticode 签名的文件
  必须显式确认（CLI ``--allow-signature-break`` / GUI 勾选）。

本模块不依赖 Tk，CLI 与 GUI 共用同一套计划与写入流程。
"""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.apps.peview._pe import PEImage, load_pe, parse_pe
from fcmd.apps.peview._reader import PEError, compute_checksum
from fcmd.apps.peview._report import check_problems

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from fcmd.apps.peview._pe import Field

__all__ = [
    "STRUCTURAL_FIELDS",
    "EditResult",
    "FieldChange",
    "apply_edits",
    "parse_assignments",
    "parse_value",
    "plan_edits",
]

STRUCTURAL_FIELDS: dict[str, str] = {
    "e_lfanew": "DOS 头 e_lfanew",
    "magic": "可选头 Magic（决定 PE32 / PE32+ 布局）",
    "number_of_sections": "节数量",
    "size_of_optional_header": "可选头大小",
    "size_of_headers": "头部大小",
    "section_alignment": "节对齐 SectionAlignment",
    "file_alignment": "文件对齐 FileAlignment",
    "size_of_image": "镜像大小 SizeOfImage",
    "number_of_rva_and_sizes": "数据目录数量",
}

_FIELD_FORMATS = {2: "<H", 4: "<I", 8: "<Q"}

_CHECKSUM_OFFSET_IN_OPTIONAL = 64
"""可选头内 ``CheckSum`` 字段的相对偏移（PE32 / PE32+ 相同）。"""

_SECTION_ALIGNMENT_SHIFT = 20
_SECTION_ALIGNMENT_MASK = 0xF
_MAX_SECTION_ALIGNMENT_CODE = 14


@dataclass(frozen=True)
class FieldChange:
    """一次字段修改：定位信息 + 旧值 / 新值。"""

    name: str
    label: str
    offset: int
    size: int
    old: int
    new: int


@dataclass(frozen=True)
class EditResult:
    """编辑结果；``dry_run`` 为真时未触碰磁盘。"""

    path: Path
    dry_run: bool
    changes: tuple[FieldChange, ...]
    backup: Path | None
    checksum: int
    signature_break: bool
    problems: tuple[str, ...]


def parse_value(text: str) -> int:
    """解析字段值：支持十进制与 ``0x`` 十六进制（允许下划线分隔）。"""
    candidate = text.strip().replace("_", "")
    if not candidate:
        raise PEError("字段值不能为空")
    try:
        return int(candidate, 0)
    except ValueError as exc:
        raise PEError(f"无法解析数值 {text!r}：支持十进制或 0x 前缀十六进制") from exc


def parse_assignments(items: Iterable[str]) -> dict[str, int]:
    """把 ``NAME=VALUE`` 列表解析为字段名到目标值的映射。"""
    result: dict[str, int] = {}
    for item in items:
        name, separator, value = item.partition("=")
        name = name.strip()
        if not separator or not name:
            raise PEError(f"字段赋值应写成 NAME=VALUE：{item!r}")
        result[name] = parse_value(value)
    return result


def plan_edits(pe: PEImage, edits: Mapping[str, int]) -> tuple[FieldChange, ...]:
    """校验字段白名单与取值，返回待执行的修改列表（不写文件）。

    Raises
    ------
    PEError
        字段不在白名单、取值越界或违反结构约束时抛出，此时整批修改都不会执行。
    """
    changes: list[FieldChange] = []
    for name, value in edits.items():
        field = pe.header_fields.get(name)
        if field is None:
            raise PEError(_unknown_field_message(name))
        _validate(pe, field, value)
        changes.append(
            FieldChange(name=name, label=field.label, offset=field.offset, size=field.size, old=field.value, new=value)
        )
    if not changes:
        raise PEError("没有指定要修改的字段（用法：--assign entry_point=0x2000）")
    return tuple(changes)


def _unknown_field_message(name: str) -> str:
    """为未在白名单中的字段生成带原因的中文错误。"""
    if name in STRUCTURAL_FIELDS:
        return f"{STRUCTURAL_FIELDS[name]} 改变文件布局，peview 不支持编辑"
    if name.startswith("section.") and not name.endswith(".characteristics"):
        return f"节字段 {name} 改变文件布局，peview 不支持编辑"
    if name.startswith("directory."):
        return f"数据目录字段 {name} 改变文件布局，peview 不支持编辑"
    return f"未知字段 {name!r}：可用 `fcmd peview fields <文件>` 查看可编辑白名单"


def _validate(pe: PEImage, field: Field, value: int) -> None:
    """校验取值：宽度、对齐、入口点范围与节对齐码。"""
    limit = 1 << (field.size * 8)
    if value < 0 or value >= limit:
        raise PEError(f"{field.label} 的值 {value} 超出 {field.size} 字节范围（0 ~ {limit - 1}）")
    if field.name == "image_base" and value % 0x10000:
        raise PEError(f"ImageBase 必须按 64K 对齐：0x{value:X} 不是 0x10000 的整数倍")
    if field.name == "entry_point" and pe.optional.size_of_image and value >= pe.optional.size_of_image:
        raise PEError(f"入口点 0x{value:X} 必须小于 SizeOfImage 0x{pe.optional.size_of_image:X}")
    if field.name.startswith("section.") and field.name.endswith(".characteristics"):
        alignment = (value >> _SECTION_ALIGNMENT_SHIFT) & _SECTION_ALIGNMENT_MASK
        if alignment > _MAX_SECTION_ALIGNMENT_CODE:
            raise PEError(f"节对齐码 {alignment} 无效：可取 0 ~ {_MAX_SECTION_ALIGNMENT_CODE}")


def apply_edits(
    path: Path,
    edits: Mapping[str, int],
    *,
    apply: bool = False,
    allow_signature_break: bool = False,
    recompute_checksum: bool = True,
) -> EditResult:
    """按白名单修改字段；``apply`` 为假时只预演并返回将要写入的内容。

    Parameters
    ----------
    path:
        PE 文件路径。
    edits:
        字段名 → 目标值。
    apply:
        是否真正写入；默认只预演。
    allow_signature_break:
        文件带 Authenticode 签名时是否仍允许写入（写入必然使签名失效）。
    recompute_checksum:
        写入（含预演）时是否按新内容重算校验和；显式修改 ``checksum`` 时不覆盖。

    Raises
    ------
    PEError
        字段非法、签名未确认、补丁后无法解析或结构指纹变化时抛出；原文件不变。
    """
    pe = load_pe(path)
    changes = plan_edits(pe, edits)
    signature_break = pe.signature.present
    if signature_break and not allow_signature_break:
        raise PEError(
            "文件带 Authenticode 签名：写入任何字段都会使签名失效；GUI 需勾选确认，CLI 需加 --allow-signature-break"
        )

    patched = _patch(pe, changes, recompute_checksum)
    updated = parse_pe(patched, path)
    _verify(pe, updated, changes)
    problems = tuple(check_problems(updated))

    if not apply:
        return EditResult(
            path=path,
            dry_run=True,
            changes=changes,
            backup=None,
            checksum=updated.optional.checksum,
            signature_break=signature_break,
            problems=problems,
        )

    backup = _write_atomic(path, patched, pe, changes)
    return EditResult(
        path=path,
        dry_run=False,
        changes=changes,
        backup=backup,
        checksum=updated.optional.checksum,
        signature_break=signature_break,
        problems=problems,
    )


def _patch(pe: PEImage, changes: tuple[FieldChange, ...], recompute_checksum: bool) -> bytes:
    """在内存中打补丁，必要时重算校验和。"""
    buffer = bytearray(pe.data)
    for change in changes:
        struct.pack_into(_FIELD_FORMATS[change.size], buffer, change.offset, change.new)
    if recompute_checksum and all(change.name != "checksum" for change in changes):
        offset = pe.optional.offset + _CHECKSUM_OFFSET_IN_OPTIONAL
        struct.pack_into("<I", buffer, offset, compute_checksum(bytes(buffer), offset))
    return bytes(buffer)


def _write_atomic(path: Path, data: bytes, before: PEImage, changes: tuple[FieldChange, ...]) -> Path:
    """写临时文件 → 重新解析校验 → 备份 → 原子替换；失败时清理临时文件。"""
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    temp = Path(temp_name)
    try:
        temp.write_bytes(data)
        shutil.copystat(path, temp)
        _verify(before, load_pe(temp), changes)
        backup = _next_backup_path(path)
        try:
            shutil.copy2(path, backup)
        except OSError:
            backup.unlink(missing_ok=True)
            raise
        temp.replace(path)
        return backup
    finally:
        temp.unlink(missing_ok=True)


def _next_backup_path(path: Path) -> Path:
    """返回第一个不存在的 ``<文件名>.backup<N>`` 路径。"""
    index = 1
    while True:
        candidate = path.with_name(f"{path.name}.backup{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _verify(before: PEImage, after: PEImage, changes: tuple[FieldChange, ...]) -> None:
    """校验补丁后的镜像：被改字段回读一致、其余结构指纹不变。"""
    for change in changes:
        actual = after.header_fields[change.name].value
        if actual != change.new:
            raise PEError(f"写入后校验失败：{change.label} 回读为 0x{actual:X}，期望 0x{change.new:X}")
    excluded = {change.name for change in changes} | {"checksum"}
    if _snapshot(before, excluded) != _snapshot(after, excluded):
        raise PEError("写入后校验失败：文件结构发生变化，已放弃写入（原文件未改动）")


def _snapshot(pe: PEImage, excluded: set[str]) -> tuple[object, ...]:
    """结构指纹：除被编辑字段与校验和以外的全部解析结果。"""
    fields = tuple((name, field.value) for name, field in pe.header_fields.items() if name not in excluded)
    directories = tuple((entry.index, entry.virtual_address, entry.size) for entry in pe.optional.directories)
    sections = tuple(
        (
            section.index,
            section.name,
            section.virtual_address,
            section.virtual_size,
            section.size_of_raw_data,
            section.pointer_to_raw_data,
        )
        for section in pe.sections
    )
    imports = tuple(
        (library.name, library.delayed, tuple((symbol.name, symbol.ordinal, symbol.hint) for symbol in library.symbols))
        for library in pe.imports
    )
    exports = (
        None
        if pe.exports is None
        else (
            pe.exports.dll_name,
            pe.exports.base_ordinal,
            tuple((symbol.name, symbol.ordinal, symbol.rva, symbol.forwarder) for symbol in pe.exports.symbols),
        )
    )
    resources = tuple(
        (
            resource.type_id,
            resource.type_name,
            resource.name_id,
            resource.name,
            resource.language,
            resource.language_name,
            resource.rva,
            resource.size,
        )
        for resource in pe.resources
    )
    relocations = tuple(
        (block.page_rva, tuple((entry.relocation_type, entry.offset) for entry in block.entries))
        for block in pe.relocations
    )
    debug = tuple(
        (entry.debug_type, entry.size_of_data, entry.address_of_raw_data, entry.pointer_to_raw_data)
        for entry in pe.debug
    )
    header = pe.file_header
    optional = pe.optional
    return (
        header.machine,
        header.number_of_sections,
        header.size_of_optional_header,
        header.characteristics,
        header.pointer_to_symbol_table,
        header.number_of_symbols,
        optional.magic,
        optional.section_alignment,
        optional.file_alignment,
        optional.size_of_image,
        optional.size_of_headers,
        optional.number_of_rva_and_sizes,
        directories,
        sections,
        imports,
        exports,
        resources,
        relocations,
        debug,
        pe.signature.present,
        pe.signature.offset,
        pe.signature.size,
        pe.overlay_offset,
        pe.overlay_size,
        pe.size,
        fields,
    )
