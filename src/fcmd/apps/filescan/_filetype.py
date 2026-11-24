"""文件类型（魔数）与归档成员：识别改名文件、检查压缩包成员路径（纯逻辑）。"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

FILE_TYPES: tuple[str, ...] = (
    "text",
    "pdf",
    "zip",
    "gzip",
    "7z",
    "rar",
    "exe",
    "ole",
    "image",
    "other",
)
"""``detect_file_type`` 的返回值；text 只表示没有命中已知二进制魔数。"""

MAX_ARCHIVE_MEMBERS = 5000
"""单归档最多检查的成员数，避免超大归档拖慢扫描。"""


def detect_file_type(path: Path) -> str:  # noqa: PLR0911 - 逐一按魔数早返回
    """按文件头魔数判断类型；读失败时返回 ``other``。"""
    try:
        with path.open("rb") as handle:
            head = handle.read(512)
    except OSError:
        return "other"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head.startswith(b"MZ"):
        return "exe"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM")):
        return "image"
    if b"\x00" in head[:8192]:
        return "other"
    return "text"


def archive_members(path: Path, file_type: str | None = None) -> list[str]:
    """返回 zip / tar 归档的成员路径；非归档、加密或读取失败返回空列表。"""
    kind = file_type or detect_file_type(path)
    names: list[str] = []
    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist()[:MAX_ARCHIVE_MEMBERS]:
                    names.append(info.filename)
        except (OSError, zipfile.BadZipFile):
            return []
        return names
    if kind in ("gzip", "tar") or path.suffix.lower() in (".tar", ".tgz", ".tar.gz"):
        try:
            with tarfile.open(path) as archive:
                for member in archive.getmembers()[:MAX_ARCHIVE_MEMBERS]:
                    names.append(member.name)
        except (OSError, tarfile.TarError):
            return []
    return names
