"""校验和基础件：算法归一、摘要计算与校验清单（.sha256/.sha1/.md5/.sfv）的生成、解析与比对。

哈希算法复用 :mod:`fcmd.infras.hashing`；SFV 的 CRC32 由本模块用 ``zlib.crc32``
计算（``hashing`` 只暴露 hashlib 算法，不扩展其口径）。工具与 GUI 层只做参数
适配与结果文案，不再自带摘要循环或清单解析。
"""

from __future__ import annotations

import os
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.infras import hashing

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "ALGORITHM_SUFFIXES",
    "SUFFIX_ALGORITHMS",
    "ChecksumEntry",
    "ChecksumResult",
    "ParsedManifest",
    "VerifyReport",
    "algorithm_for_suffix",
    "build_manifest",
    "compute_crc32",
    "compute_digest",
    "format_manifest",
    "manifest_name",
    "normalize_algorithm",
    "parse_manifest",
    "report_text",
    "verify_manifest",
]

SUFFIX_ALGORITHMS: dict[str, str] = {
    ".sha256": "sha256",
    ".sha512": "sha512",
    ".sha1": "sha1",
    ".md5": "md5",
    ".sfv": "crc32",
}
"""校验文件后缀到算法的映射（后缀统一小写）。"""

ALGORITHM_SUFFIXES: dict[str, str] = {algorithm: suffix for suffix, algorithm in SUFFIX_ALGORITHMS.items()}

_DIGEST_LENGTHS = {"md5": 32, "sha1": 40, "sha256": 64, "sha512": 128, "crc32": 8}
_HASH_LINE = re.compile(r"^([0-9A-Fa-f]+)[ \t]+\*?(.+)$")
_CRC_LINE = re.compile(r"^(.+?)[ \t]+([0-9A-Fa-f]{8})$")


def normalize_algorithm(raw: str) -> str:
    """把用户输入归一为受支持的算法名；``sfv`` / ``crc`` 视为 ``crc32``。"""
    algorithm = raw.strip().lower()
    if algorithm in ("sfv", "crc"):
        algorithm = "crc32"
    if algorithm not in _DIGEST_LENGTHS:
        raise ValueError(f"不支持的校验算法：{raw}，可选：{', '.join(sorted(_DIGEST_LENGTHS))}")
    return algorithm


def algorithm_for_suffix(suffix: str) -> str | None:
    """按校验文件后缀返回算法；未知后缀返回 ``None``。"""
    return SUFFIX_ALGORITHMS.get(suffix.lower())


def compute_crc32(path: Path) -> str:
    """分块计算 CRC32，返回 8 位小写十六进制。"""
    checksum = 0
    with path.open("rb") as file:
        while True:
            chunk = file.read(hashing.CHUNK_SIZE)
            if not chunk:
                break
            checksum = zlib.crc32(chunk, checksum)
    return f"{checksum & 0xFFFFFFFF:08x}"


def compute_digest(path: Path, algorithm: str) -> str:
    """按算法计算单个文件摘要（小写十六进制）。"""
    if algorithm == "crc32":
        return compute_crc32(path)
    return hashing.compute_file_hash(path, algorithm)


def relative_name(path: Path, base: Path) -> str:
    """生成清单内的相对路径（POSIX 分隔）；不在基准目录下时退化为文件名。"""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def manifest_name(base: Path, algorithm: str) -> str:
    """按基准目录名生成默认校验文件名，如 ``backup.sha256``。"""
    stem = base.name or "checksums"
    return f"{stem}{ALGORITHM_SUFFIXES[algorithm]}"


def format_manifest(entries: Sequence[tuple[str, str]], algorithm: str) -> str:
    """把 ``(名称, 摘要)`` 列表格式化为校验文件内容。

    - ``.sfv``：``名称 CRC32``（8 位大写）。
    - 其余：GNU 风格 ``摘要␠␠名称``，与 ``sha256sum -c`` 兼容。
    """
    lines: list[str] = []
    for name, digest in entries:
        if algorithm == "crc32":
            lines.append(f"{name} {digest.upper()}")
        else:
            lines.append(f"{digest.lower()}  {name}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_manifest(
    paths: Iterable[Path],
    base: Path,
    algorithm: str,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """计算文件摘要并生成校验文件内容（按文件名排序保证可复现）。

    参数:
        paths: 待计算的文件；目录会被忽略。
        base: 相对路径的基准目录。
        algorithm: :func:`normalize_algorithm` 归一后的算法。
        progress: 可选进度回调 ``(已完成, 总数)``。
    """
    files = sorted((path for path in paths if path.is_file()), key=lambda item: item.name)
    entries: list[tuple[str, str]] = []
    for index, path in enumerate(files, start=1):
        entries.append((relative_name(path, base), compute_digest(path, algorithm)))
        if progress is not None:
            progress(index, len(files))
    return format_manifest(entries, algorithm)


@dataclass(frozen=True)
class ChecksumEntry:
    """校验文件中的一行。"""

    name: str
    digest: str


@dataclass(frozen=True)
class ParsedManifest:
    """解析结果：有效条目与无法解析的行。"""

    entries: list[ChecksumEntry]
    errors: list[str]


def parse_manifest(text: str, algorithm: str) -> ParsedManifest:
    """解析校验文件文本；空行、``;`` / ``#`` 注释跳过，非法行记入 ``errors``。"""
    entries: list[ChecksumEntry] = []
    errors: list[str] = []
    expected_length = _DIGEST_LENGTHS[algorithm]
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith((";", "#")):
            continue
        if algorithm == "crc32":
            match = _CRC_LINE.match(line)
        else:
            match = _HASH_LINE.match(line)
        if match is None:
            errors.append(f"第 {number} 行无法解析：{line}")
            continue
        if algorithm == "crc32":
            name = match.group(1).strip()
            digest = match.group(2).lower()
        else:
            name = match.group(2)
            digest = match.group(1).lower()
        if len(digest) != expected_length:
            errors.append(f"第 {number} 行摘要长度不符（{algorithm} 需 {expected_length} 位）：{line}")
            continue
        entries.append(ChecksumEntry(name=name, digest=digest))
    return ParsedManifest(entries=entries, errors=errors)


@dataclass(frozen=True)
class ChecksumResult:
    """单个条目的比对结果。"""

    name: str
    expected: str
    actual: str | None
    error: str = ""

    @property
    def ok(self) -> bool:
        """摘要一致且文件可读。"""
        return self.actual is not None and self.actual == self.expected


@dataclass(frozen=True)
class VerifyReport:
    """整份校验文件的比对结果。"""

    algorithm: str
    results: list[ChecksumResult]
    errors: list[str]

    @property
    def failures(self) -> list[ChecksumResult]:
        """不匹配或读取失败的条目。"""
        return [result for result in self.results if not result.ok]


def verify_manifest(text: str, base: Path, algorithm: str) -> VerifyReport:
    """逐项比对清单与 ``base`` 下的实际文件；缺失与读取失败都记为失败项。"""
    parsed = parse_manifest(text, algorithm)
    results: list[ChecksumResult] = []
    for entry in parsed.entries:
        target = base / entry.name.replace("/", os.sep)
        if not target.is_file():
            results.append(ChecksumResult(entry.name, entry.digest, None, "文件不存在"))
            continue
        try:
            actual = compute_digest(target, algorithm)
        except OSError as exc:
            results.append(ChecksumResult(entry.name, entry.digest, None, str(exc)))
            continue
        results.append(ChecksumResult(entry.name, entry.digest, actual))
    return VerifyReport(algorithm=algorithm, results=results, errors=parsed.errors)


def report_text(report: VerifyReport) -> str:
    """把比对结果格式化为结果窗口文本（失败项单独标注原因）。"""
    failures = len(report.failures)
    lines = [
        f"算法：{report.algorithm}",
        f"条目：{len(report.results)}，失败：{failures}，无法解析：{len(report.errors)}",
        "",
    ]
    for result in report.results:
        if result.ok:
            lines.append(f"OK    {result.name}")
            continue
        detail = f"FAIL  {result.name}  期望 {result.expected}  实际 {result.actual or '—'}"
        if result.error:
            detail = f"{detail}  {result.error}"
        lines.append(detail)
    if report.errors:
        lines.extend(["", "无法解析的行：", *report.errors])
    return "\n".join(lines)
