"""filescan 旧版 Office 抽取测试：OLE 复合文档读取、doc 片段表、xls SST、ppt 记录树。"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fcmd.apps.filescan._extract import KIND_CORRUPT, KIND_ENCRYPTED, ExtractError, extract_document
from fcmd.apps.filescan._extract_legacy import (
    _doc_units,
    _parse_sst,
    _ppt_units,
    _xls_units,
    extract_legacy_text,
)


class _FakeCfb:
    """按流名取内容的假容器，避免为每个格式都构造完整 OLE 文件。"""

    def __init__(self, streams: dict[str, bytes]) -> None:
        self._streams = streams

    def stream(self, name: str) -> bytes | None:
        return self._streams.get(name)


# ---------------------------------------------------------------------- #
# 最小 OLE 复合文档（单流、FAT 链）
# ---------------------------------------------------------------------- #
def _cfb(stream_name: str, payload: bytes) -> bytes:
    """构造含一个流的最小 CFB（扇区 512、payload 需 > 4096 才走 FAT 链）。"""
    sector = 512
    sectors = (len(payload) + sector - 1) // sector
    fat = [0xFFFFFFFF] * (sector // 4)
    fat[0] = 0xFFFFFFFD  # FAT 自身
    fat[1] = 0xFFFFFFFE  # 目录链结束
    for index in range(sectors):
        fat[2 + index] = 0xFFFFFFFE if index == sectors - 1 else 3 + index

    header = bytearray(sector)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", header, 0x1E, 9)
    struct.pack_into("<H", header, 0x20, 6)
    struct.pack_into("<I", header, 0x2C, 1)
    struct.pack_into("<I", header, 0x30, 1)
    struct.pack_into("<I", header, 0x38, 4096)
    struct.pack_into("<I", header, 0x3C, 0xFFFFFFFE)
    struct.pack_into("<I", header, 0x44, 0xFFFFFFFE)
    struct.pack_into("<109I", header, 0x4C, 0, *([0xFFFFFFFF] * 108))

    directory = bytearray(sector)
    for offset, name, kind, start, size in (
        (0, "Root Entry", 5, 0xFFFFFFFE, 0),
        (128, stream_name, 2, 2, len(payload)),
    ):
        encoded = name.encode("utf-16-le") + b"\x00\x00"
        directory[offset : offset + len(encoded)] = encoded
        struct.pack_into("<H", directory, offset + 0x40, len(encoded))
        directory[offset + 0x42] = kind
        struct.pack_into("<I", directory, offset + 0x74, start)
        struct.pack_into("<Q", directory, offset + 0x78, size)

    body = payload + b"\x00" * (sectors * sector - len(payload))
    return bytes(header) + struct.pack(f"<{len(fat)}I", *fat) + bytes(directory) + body


def _sst(unique: int, strings: list[bytes]) -> bytes:
    """拼接 SST 记录负载（``strings`` 已含各自的头与字符）。"""
    return struct.pack("<II", unique, unique) + b"".join(strings)


def _biff_record(kind: int, payload: bytes) -> bytes:
    """BIFF 记录头：2 字节类型 + 2 字节长度。"""
    return struct.pack("<HH", kind, len(payload)) + payload


def _ppt_record(kind: int, payload: bytes, *, version: int = 0) -> bytes:
    """PPT 记录头：2 字节版本 + 2 字节类型 + 4 字节长度。"""
    return struct.pack("<HHI", version, kind, len(payload)) + payload


# ---------------------------------------------------------------------- #
# doc
# ---------------------------------------------------------------------- #
def _doc_container(text: str = "Hello\rWorld", *, flags: int = 0x0200) -> _FakeCfb:
    encoded = text.encode("cp1252")
    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, 0x40000000 | (1024 * 2), 0)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    word = bytearray(2048)
    struct.pack_into("<H", word, 0x0A, flags)
    struct.pack_into("<II", word, 0x01A2, 0, len(clx))
    word[1024 : 1024 + len(encoded)] = encoded
    table = bytearray(64)
    table[: len(clx)] = clx
    return _FakeCfb({"WordDocument": bytes(word), "1Table": bytes(table), "0Table": bytes(table)})


def test_doc_piece_table_splits_paragraphs() -> None:
    units = _doc_units(_doc_container())
    assert [(unit.text, unit.location) for unit in units] == [("Hello", "段落 1"), ("World", "段落 2")]


def test_doc_encrypted_flag_raises() -> None:
    with pytest.raises(ExtractError) as excinfo:
        _doc_units(_doc_container(flags=0x0300))
    assert excinfo.value.kind == KIND_ENCRYPTED


# ---------------------------------------------------------------------- #
# xls
# ---------------------------------------------------------------------- #
def test_xls_sst_reads_strings() -> None:
    payload = _sst(
        2,
        [
            struct.pack("<H", 2) + b"\x01" + "张三".encode("utf-16-le"),
            struct.pack("<H", 5) + b"\x00" + b"abcd1",
        ],
    )
    units = _xls_units(_FakeCfb({"Workbook": _biff_record(0x00FC, payload)}))
    assert [unit.text for unit in units] == ["张三", "abcd1"]


def test_xls_sst_handles_continue_boundary() -> None:
    """字符串跨 CONTINUE 边界时，续记录首字节是新的字符宽度标志。"""
    chunks = [
        _sst(2, [struct.pack("<H", 4) + b"\x00" + b"AB"]),
        b"\x01" + "CD".encode("utf-16-le"),  # 续段：宽度标志 0x01 + UTF-16LE 剩余字符
    ]
    chunks[1] += struct.pack("<H", 3) + b"\x00" + b"xyz"
    assert _parse_sst(chunks) == ["ABCD", "xyz"]


# ---------------------------------------------------------------------- #
# ppt
# ---------------------------------------------------------------------- #
def test_ppt_walks_nested_text_atoms() -> None:
    chars = _ppt_record(4000, "大纲\r合同".encode("utf-16-le"))
    bytes_atom = _ppt_record(4008, b"note")
    stream = _ppt_record(1000, chars + bytes_atom, version=0x000F)
    units = _ppt_units(_FakeCfb({"PowerPoint Document": stream}))
    assert [unit.text for unit in units] == ["大纲", "合同", "note"]


def test_ppt_missing_stream_raises_corrupt() -> None:
    with pytest.raises(ExtractError) as excinfo:
        _ppt_units(_FakeCfb({}))
    assert excinfo.value.kind == KIND_CORRUPT


# ---------------------------------------------------------------------- #
# OLE 读取与入口
# ---------------------------------------------------------------------- #
def test_legacy_extract_reads_ole_stream(tmp_path: Path) -> None:
    sst = _sst(1, [struct.pack("<H", 6) + b"\x00" + b"secret"])
    payload = _biff_record(0x00FC, sst)
    payload += b"\x00" * (4200 - len(payload))
    target = tmp_path / "book.xls"
    target.write_bytes(_cfb("Workbook", payload))

    assert [unit.text for unit in extract_legacy_text(target)] == ["secret"]
    assert [unit.text for unit in extract_document(target)] == ["secret"]


def test_legacy_extract_rejects_non_ole(tmp_path: Path) -> None:
    target = tmp_path / "old.doc"
    target.write_bytes(b"not an ole file")
    with pytest.raises(ExtractError) as excinfo:
        extract_legacy_text(target)
    assert excinfo.value.kind == KIND_CORRUPT
