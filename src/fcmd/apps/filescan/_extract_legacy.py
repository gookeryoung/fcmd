"""filescan 旧版 Office（doc / xls / ppt）文本抽取：OLE 复合文档 + 格式记录解析。

三种格式都是 OLE 复合文档（CFB）容器，本模块自带最小 CFB 读取器（FAT / mini-FAT /
目录），再用各格式的记录结构取文本，不引入第三方依赖：

- **doc**（Word 97-2003）：``WordDocument`` 流 + 片段表（piece table，CLX/PlcPcd），
  按 CP 区间取 UTF-16LE 或 cp1252 文本，按 ``\\r`` 切段落；
- **xls**（BIFF8）：``Workbook`` / ``Book`` 流的 SST 共享字符串表，支持 CONTINUE
  续记录与「跨记录字符串」的续段标志字节；
- **ppt**（PowerPoint 97-2003）：``PowerPoint Document`` 流的记录树，取
  ``TextCharsAtom`` / ``TextBytesAtom`` 文本。

失败沿用 :class:`ExtractError` 的 kind 口径（加密 / 损坏 / 无文本 / 超限）。
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Protocol

from fcmd.apps.filescan._extract import (
    DECOMPRESSED_MAX_BYTES,
    KIND_CORRUPT,
    KIND_ENCRYPTED,
    KIND_NO_TEXT,
    KIND_TOO_LARGE,
    ExtractError,
    TextUnit,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["LEGACY_EXTENSIONS", "extract_legacy_text"]

LEGACY_EXTENSIONS: tuple[str, ...] = (".doc", ".xls", ".ppt")
"""按 OLE 复合文档解析的旧版 Office 后缀。"""

_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
"""OLE 复合文档（CFB）魔数。"""

_END_OF_CHAIN = 0xFFFFFFFE
"""扇区链结束标记；≥ 0xFFFFFFFA 的取值都表示链结束。"""


def _sector_end(value: int) -> bool:
    """扇区链是否已结束（含保留值）。"""
    return value >= 0xFFFFFFFA


class _StreamSource(Protocol):
    """按名称取流的容器接口，便于各格式函数与测试替身共用。"""

    def stream(self, name: str) -> bytes | None: ...


class _Cfb:
    """最小 OLE 复合文档读取器：只做目录枚举与流读取，不写、不修复。"""

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or data[:8] != _CFB_MAGIC:
            raise ValueError("不是 OLE 复合文档")
        self._data = data
        self._sector_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
        self._mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
        fat_count = struct.unpack_from("<I", data, 0x2C)[0]
        first_dir = struct.unpack_from("<I", data, 0x30)[0]
        self._cutoff = struct.unpack_from("<I", data, 0x38)[0]
        first_minifat = struct.unpack_from("<I", data, 0x3C)[0]
        minifat_count = struct.unpack_from("<I", data, 0x40)[0]
        first_difat = struct.unpack_from("<I", data, 0x44)[0]
        difat_count = struct.unpack_from("<I", data, 0x48)[0]
        self._fat = self._load_fat(fat_count, first_difat, difat_count)
        self._entries = self._load_directory(first_dir)
        root = self._entries.get("Root Entry")
        self._mini_root = self._chain_bytes(root[1]) if root is not None else b""
        self._minifat = self._load_minifat(first_minifat, minifat_count)

    # -- 底层扇区 ------------------------------------------------------- #
    def _sector(self, index: int) -> bytes:
        """按扇区号取原始字节（扇区 0 从 ``sector_size`` 偏移开始）。"""
        start = (index + 1) * self._sector_size
        block = self._data[start : start + self._sector_size]
        if len(block) < self._sector_size:
            raise ValueError("扇区越界")
        return block

    def _load_fat(self, fat_count: int, first_difat: int, difat_count: int) -> list[int]:
        """读取 FAT 数组（含 DIFAT 扩展扇区）。"""
        difat = list(struct.unpack_from("<109I", self._data, 0x4C))
        sector = first_difat
        for _ in range(difat_count):
            if _sector_end(sector):
                break
            entries = struct.unpack(f"<{self._sector_size // 4}I", self._sector(sector))
            difat.extend(entries[:-1])
            sector = entries[-1]
        fat: list[int] = []
        for item in difat:
            if _sector_end(item):
                continue
            if len(fat) >= fat_count * (self._sector_size // 4):
                break
            fat.extend(struct.unpack(f"<{self._sector_size // 4}I", self._sector(item)))
        return fat

    def _load_minifat(self, first: int, count: int) -> list[int]:
        """读取 mini-FAT 扇区链；无 mini 流时返回空表。"""
        if count == 0 or _sector_end(first):
            return []
        data = self._chain_bytes(first)
        return list(struct.unpack(f"<{len(data) // 4}I", data))

    def _load_directory(self, first: int) -> dict[str, tuple[int, int, int]]:
        """枚举目录项：``名称 → (类型, 起始扇区, 字节数)``。"""
        data = self._chain_bytes(first)
        entries: dict[str, tuple[int, int, int]] = {}
        for offset in range(0, len(data) - 127, 128):
            name_len = struct.unpack_from("<H", data, offset + 0x40)[0]
            if name_len < 2 or name_len > 64:
                continue
            name = data[offset : offset + name_len - 2].decode("utf-16-le", "replace")
            kind = data[offset + 0x42]
            start = struct.unpack_from("<I", data, offset + 0x74)[0]
            size = struct.unpack_from("<Q", data, offset + 0x78)[0]
            entries.setdefault(name, (kind, start, size))
        return entries

    def _chain_bytes(self, start: int) -> bytes:
        """沿 FAT 链拼接扇区内容（总长受解压上限约束，防构造文件撑爆内存）。"""
        parts: list[bytes] = []
        seen: set[int] = set()
        total = 0
        sector = start
        while not _sector_end(sector):
            if sector in seen:
                raise ValueError("扇区链成环")
            seen.add(sector)
            block = self._sector(sector)
            total += len(block)
            if total > DECOMPRESSED_MAX_BYTES:
                raise ExtractError(KIND_TOO_LARGE, "OLE 复合文档解压总量超过上限")
            parts.append(block)
            sector = self._fat[sector] if sector < len(self._fat) else _END_OF_CHAIN
        return b"".join(parts)

    def _mini_chain_bytes(self, start: int, size: int) -> bytes:
        """沿 mini-FAT 链从根流的小扇区里取字节。"""
        parts: list[bytes] = []
        seen: set[int] = set()
        sector = start
        while not _sector_end(sector):
            if sector in seen:
                raise ValueError("mini 扇区链成环")
            seen.add(sector)
            offset = sector * self._mini_size
            block = self._mini_root[offset : offset + self._mini_size]
            if not block:
                break
            parts.append(block)
            if sum(len(part) for part in parts) >= size:
                break
            sector = self._minifat[sector] if sector < len(self._minifat) else _END_OF_CHAIN
        return b"".join(parts)

    # -- 对外 ----------------------------------------------------------- #
    def stream(self, name: str) -> bytes | None:
        """按名称取流内容；不存在或不是流时返回 ``None``。"""
        entry = self._entries.get(name)
        if entry is None or entry[0] != 2:
            return None
        _kind, start, size = entry
        if size > DECOMPRESSED_MAX_BYTES:
            raise ExtractError(KIND_TOO_LARGE, f"流 {name} 超过解压上限")
        data = self._mini_chain_bytes(start, size) if size < self._cutoff else self._chain_bytes(start)
        return data[:size]


# ---------------------------------------------------------------------- #
# doc（Word 97-2003 片段表）
# ---------------------------------------------------------------------- #
def _doc_pieces(word: bytes, clx: bytes) -> list[str]:
    """解析 CLX 里的 PlcPcd，按片段取出文本。"""
    pos = 0
    plc = b""
    while pos < len(clx):
        marker = clx[pos]
        if marker == 0x01:  # Prc：跳过格式信息
            length = struct.unpack_from("<H", clx, pos + 1)[0]
            pos += 3 + length
        elif marker == 0x02:  # Pcdt
            length = struct.unpack_from("<I", clx, pos + 1)[0]
            plc = clx[pos + 5 : pos + 5 + length]
            break
        else:
            break
    if len(plc) < 16:
        return []
    count = (len(plc) - 4) // 12
    if count <= 0:
        return []
    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
    parts: list[str] = []
    for index in range(count):
        start_cp, end_cp = cps[index], cps[index + 1]
        if end_cp <= start_cp:
            continue
        fc = struct.unpack_from("<I", plc, (count + 1) * 4 + index * 8 + 2)[0]
        compressed = bool(fc & 0x40000000)
        offset = fc & 0x3FFFFFFF
        length = end_cp - start_cp
        if compressed:
            raw = word[offset // 2 : offset // 2 + length]
            parts.append(raw.decode("cp1252", "replace"))
        else:
            raw = word[offset : offset + length * 2]
            parts.append(raw.decode("utf-16-le", "replace"))
    return parts


def _doc_units(container: _StreamSource) -> list[TextUnit]:
    """Word 97-2003：按片段表取正文，``\\r`` 分段、``\\x07`` 记为制表符。"""
    word = container.stream("WordDocument")
    if word is None or len(word) < 0x01AA:
        raise ExtractError(KIND_CORRUPT, "旧版 Word 文档缺少 WordDocument 流")
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    if flags & 0x0100:
        raise ExtractError(KIND_ENCRYPTED, "旧版 Word 文档已加密")
    table = container.stream("1Table" if flags & 0x0200 else "0Table") or b""
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        raise ExtractError(KIND_NO_TEXT, "旧版 Word 文档缺少片段表")
    text = "".join(_doc_pieces(word, table[fc_clx : fc_clx + lcb_clx]))
    text = text.replace("\x07", "\t").replace("\x0b", "\n")
    units: list[TextUnit] = []
    for index, paragraph in enumerate(text.split("\r"), start=1):
        if paragraph.strip():
            units.append(TextUnit(paragraph, f"段落 {index}"))
    return units


# ---------------------------------------------------------------------- #
# xls（BIFF8 SST）
# ---------------------------------------------------------------------- #
_BIFF_SST = 0x00FC
_BIFF_CONTINUE = 0x003C


class _SstCursor:
    """SST 续记录游标：跨 CONTINUE 边界时按规范重读字符宽度标志字节。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self._index = 0
        self._pos = 0

    def _advance(self) -> bool:
        """跳过已耗尽的记录，返回是否发生了跨记录。"""
        moved = False
        while self._index < len(self._chunks) and self._pos >= len(self._chunks[self._index]):
            self._index += 1
            self._pos = 0
            moved = True
        if self._index >= len(self._chunks):
            raise ValueError("SST 记录被截断")
        return moved

    def take(self, size: int) -> bytes:
        """读取 ``size`` 字节，允许跨记录。"""
        out = bytearray()
        while size > 0:
            self._advance()
            chunk = self._chunks[self._index]
            step = min(size, len(chunk) - self._pos)
            out += chunk[self._pos : self._pos + step]
            self._pos += step
            size -= step
        return bytes(out)

    def skip(self, size: int) -> None:
        """跳过 ``size`` 字节，允许跨记录。"""
        while size > 0:
            self._advance()
            step = min(size, len(self._chunks[self._index]) - self._pos)
            self._pos += step
            size -= step

    def read_char(self, high: bool) -> tuple[str, bool]:
        """读一个字符；跨记录时返回 ``(字符, 新宽度标志)``。"""
        moved = self._advance()
        if moved:
            high = bool(self.take(1)[0] & 0x01)
        raw = self.take(2 if high else 1)
        return raw.decode("utf-16-le" if high else "cp1252", "replace"), high


def _read_sst_string(cursor: _SstCursor) -> str:
    """读取一条 XLUnicodeRichExtendedString。"""
    length = struct.unpack("<H", cursor.take(2))[0]
    flags = cursor.take(1)[0]
    high = bool(flags & 0x01)
    chars: list[str] = []
    for _ in range(length):
        char, high = cursor.read_char(high)
        chars.append(char)
    if flags & 0x08:  # 富文本：cRun 个 4 字节格式串
        cursor.skip(struct.unpack("<H", cursor.take(2))[0] * 4)
    if flags & 0x04:  # 注音：cbExtRst 字节
        cursor.skip(struct.unpack("<I", cursor.take(4))[0])
    return "".join(chars)


def _parse_sst(chunks: list[bytes]) -> list[str]:
    """解析 SST + CONTINUE 记录，返回去重后的共享字符串。"""
    cursor = _SstCursor(chunks)
    _total, unique = struct.unpack("<II", cursor.take(8))
    return [_read_sst_string(cursor) for _ in range(unique)]


def _biff_records(data: bytes) -> list[tuple[int, bytes]]:
    """按 BIFF 记录头切分 ``(类型, 负载)``；截断处停止。"""
    records: list[tuple[int, bytes]] = []
    pos = 0
    while pos + 4 <= len(data):
        kind, length = struct.unpack_from("<HH", data, pos)
        body = data[pos + 4 : pos + 4 + length]
        if len(body) < length:
            break
        records.append((kind, body))
        pos += 4 + length
    return records


def _xls_units(container: _StreamSource) -> list[TextUnit]:
    """Excel 97-2003：取 SST 共享字符串，位置记为「字符串 N」。"""
    workbook = container.stream("Workbook")
    if workbook is None:
        workbook = container.stream("Book")
    if workbook is None:
        raise ExtractError(KIND_CORRUPT, "旧版 Excel 文档缺少 Workbook 流")
    chunks: list[bytes] = []
    for kind, payload in _biff_records(workbook):
        if (kind == _BIFF_SST and not chunks) or (kind == _BIFF_CONTINUE and chunks):
            chunks.append(payload)
        elif chunks:
            break
    if not chunks:
        raise ExtractError(KIND_NO_TEXT, "旧版 Excel 文档没有共享字符串表")
    try:
        strings = _parse_sst(chunks)
    except (ValueError, struct.error) as exc:
        raise ExtractError(KIND_CORRUPT, f"共享字符串表损坏: {exc}") from exc
    return [TextUnit(text, f"字符串 {index}") for index, text in enumerate(strings, start=1) if text.strip()]


# ---------------------------------------------------------------------- #
# ppt（PowerPoint 97-2003 记录树）
# ---------------------------------------------------------------------- #
_PPT_TEXT_CHARS = 4000
_PPT_TEXT_BYTES = 4008
_PPT_CONTAINER_VER = 0x0F


def _ppt_walk(data: bytes, start: int, end: int, depth: int, units: list[TextUnit]) -> None:
    """递归遍历记录树，收集文本原子。"""
    pos = start
    while pos + 8 <= end:
        version, kind, length = struct.unpack_from("<HHI", data, pos)
        body = pos + 8
        if body + length > end:
            return
        if kind == _PPT_TEXT_CHARS:
            text = data[body : body + length].decode("utf-16-le", "replace")
            units.extend(TextUnit(line, f"文本 {len(units) + 1}") for line in text.split("\r") if line.strip())
        elif kind == _PPT_TEXT_BYTES:
            text = data[body : body + length].decode("cp1252", "replace")
            units.extend(TextUnit(line, f"文本 {len(units) + 1}") for line in text.split("\r") if line.strip())
        elif (version & 0x0F) == _PPT_CONTAINER_VER and depth < 8:
            _ppt_walk(data, body, body + length, depth + 1, units)
        pos = body + length


def _ppt_units(container: _StreamSource) -> list[TextUnit]:
    """PowerPoint 97-2003：遍历记录树取文本原子。"""
    if container.stream("EncryptedSummary") is not None:
        raise ExtractError(KIND_ENCRYPTED, "旧版 PowerPoint 文档已加密")
    stream = container.stream("PowerPoint Document")
    if stream is None:
        raise ExtractError(KIND_CORRUPT, "旧版 PowerPoint 文档缺少 PowerPoint Document 流")
    units: list[TextUnit] = []
    _ppt_walk(stream, 0, len(stream), 0, units)
    return units


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
def extract_legacy_text(path: Path) -> list[TextUnit]:
    """按后缀抽取旧版 Office 文本；失败时抛 :class:`ExtractError`。"""
    suffix = path.suffix.lower()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ExtractError(KIND_CORRUPT, f"无法读取文档: {exc}") from exc
    try:
        container = _Cfb(data)
    except ExtractError:
        raise
    except (ValueError, struct.error, UnicodeDecodeError) as exc:
        raise ExtractError(KIND_CORRUPT, f"OLE 复合文档损坏: {exc}") from exc
    if suffix == ".doc":
        return _doc_units(container)
    if suffix == ".xls":
        return _xls_units(container)
    if suffix == ".ppt":
        return _ppt_units(container)
    raise ExtractError(KIND_CORRUPT, f"不是旧版 Office 后缀: {suffix or '无后缀'}")
