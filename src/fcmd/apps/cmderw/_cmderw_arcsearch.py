"""cmderw 归档内搜索纯逻辑：zip / tar 条目名与内容，内容按流读取。

名称匹配沿用文件名搜索的 glob 语义；内容匹配复用
:func:`fcmd.dsl.actions.fileops.compile_content_pattern` 的大小写与整词规则。
内容搜索逐成员流式解码（先探测编码，遇到 NUL 视为二进制跳过），不会把整个
成员读入内存；加密成员与超过大小上限的成员计数后跳过。
"""

from __future__ import annotations

import codecs
import contextlib
import fnmatch
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.apps.cmderw._cmderw_preview import detect_encoding
from fcmd.dsl.actions.fileops import compile_content_pattern

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any, BinaryIO

__all__ = [
    "MAX_CONTENT_MEMBER_SIZE",
    "MAX_HITS",
    "MAX_LINES_PER_MEMBER",
    "ArchiveHit",
    "ArchiveSearchReport",
    "format_archive_hit",
    "search_archive",
]

MAX_HITS = 500
MAX_LINES_PER_MEMBER = 100_000
MAX_CONTENT_MEMBER_SIZE = 32 * 1024 * 1024
_CHUNK_SIZE = 1 << 16
_DETECT_SIZE = 1 << 16


@dataclass(frozen=True)
class ArchiveHit:
    """一条命中：``line`` 为 0 表示条目名命中，否则为内容行号。"""

    member: str
    line: int = 0
    text: str = ""


@dataclass(frozen=True)
class ArchiveSearchReport:
    """搜索汇总：命中、扫描成员数、跳过数与是否因上限截断。"""

    hits: tuple[ArchiveHit, ...]
    scanned: int
    skipped: int
    truncated: bool


def format_archive_hit(hit: ArchiveHit) -> str:
    """把命中格式化为结果面板的一行文本。"""
    if hit.line <= 0:
        return hit.member
    return f"{hit.member}:{hit.line}: {hit.text}"


def _is_zip(archive: Path) -> bool:
    return archive.name.lower().endswith(".zip")


def _is_tar(archive: Path) -> bool:
    return archive.name.lower().endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tbz2", ".tar.xz", ".txz"))


@contextlib.contextmanager
def _member_entries(archive: Path) -> Iterator[Iterator[tuple[str, int, bool, Callable[[], Any]]]]:
    """逐个产出 ``(成员名, 大小, 是否加密, 打开函数)``，句柄在本上下文内有效。"""
    if _is_zip(archive):
        with zipfile.ZipFile(archive) as handle:
            yield (
                (info.filename, info.file_size, bool(info.flag_bits & 0x1), lambda info=info: handle.open(info))
                for info in handle.infolist()
                if not info.is_dir()
            )
    elif _is_tar(archive):
        with tarfile.open(archive) as handle:
            yield (
                (info.name, info.size, False, lambda info=info: handle.extractfile(info))
                for info in handle.getmembers()
                if info.isfile()
            )
    else:
        raise ValueError(f"该格式不支持包内搜索: {archive.name}")


def _iter_member_lines(stream: BinaryIO, max_lines: int) -> Iterator[tuple[int, str]]:
    """流式解码成员并逐行产出；探测到 NUL 时按二进制跳过。"""
    prefix = stream.read(_DETECT_SIZE)
    if not prefix or b"\x00" in prefix:
        return
    encoding = detect_encoding(prefix)
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    buffer = decoder.decode(prefix)
    line_number = 0
    while True:
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line_number += 1
            yield line_number, line.rstrip("\r")
            if line_number >= max_lines:
                return
        chunk = stream.read(_CHUNK_SIZE)
        if not chunk:
            break
        buffer += decoder.decode(chunk)
    buffer += decoder.decode(b"", final=True)
    if buffer and line_number < max_lines:
        yield line_number + 1, buffer.rstrip("\r")


def search_archive(  # noqa: PLR0912, PLR0913 - 搜索开关与上限由界面透传
    archive: Path,
    pattern: str,
    *,
    content: bool = False,
    case_sensitive: bool = True,
    whole_word: bool = False,
    max_hits: int = MAX_HITS,
    max_member_size: int = MAX_CONTENT_MEMBER_SIZE,
    max_lines: int = MAX_LINES_PER_MEMBER,
) -> ArchiveSearchReport:
    """在归档内搜索名称或内容；名称用 glob，内容用正则语义。"""
    if not archive.is_file():
        raise FileNotFoundError(f"归档不存在: {archive}")
    if not pattern:
        raise ValueError("搜索内容不能为空")
    if max_hits <= 0:
        raise ValueError("命中上限必须大于 0")
    matcher: Callable[[str], bool]
    if case_sensitive:

        def matcher(name: str) -> bool:
            return fnmatch.fnmatchcase(name, pattern)
    else:
        folded = pattern.casefold()

        def matcher(name: str) -> bool:
            return fnmatch.fnmatchcase(name.casefold(), folded)

    regex = compile_content_pattern(pattern, case_sensitive=case_sensitive, whole_word=whole_word) if content else None

    hits: list[ArchiveHit] = []
    scanned = 0
    skipped = 0
    truncated = False
    with _member_entries(archive) as entries:
        for name, size, encrypted, opener in entries:
            scanned += 1
            if not content:
                if matcher(name):
                    hits.append(ArchiveHit(name))
            else:
                if encrypted or size > max_member_size:
                    skipped += 1
                    continue
                stream = opener()
                if stream is None:
                    skipped += 1
                    continue
                assert regex is not None
                with stream:
                    for line_number, text in _iter_member_lines(stream, max_lines):
                        if not regex.search(text):
                            continue
                        hits.append(ArchiveHit(name, line_number, text))
                        if len(hits) >= max_hits:
                            truncated = True
                            break
            if len(hits) >= max_hits:
                truncated = True
                break
    return ArchiveSearchReport(tuple(hits), scanned, skipped, truncated)
