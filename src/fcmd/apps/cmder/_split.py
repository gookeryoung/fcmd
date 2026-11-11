"""cmderw 文件分割 / 合并纯逻辑。

分割按「大小」或「份数」二选一，产物命名为 ``<原名>.001``、``.002``……
并写一个 ``<原名>.sha256`` 校验文件；合并按编号顺序拼接，有校验文件时核对
SHA-256 与总大小。所有写盘都先落临时文件再原子替换，失败清理半成品。
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "CHECKSUM_SUFFIX",
    "MergeResult",
    "SplitResult",
    "default_merged_name",
    "discover_parts",
    "merge_parts",
    "part_names",
    "split_file",
    "split_plan",
]

CHECKSUM_SUFFIX = ".sha256"
_CHUNK_SIZE = 1 << 20
_PART_PATTERN = re.compile(r"^(?P<base>.+)\.(?P<index>\d{3,})$")


@dataclass(frozen=True)
class SplitResult:
    """分割结果：分卷路径、校验文件、整体摘要与源大小。"""

    parts: tuple[Path, ...]
    checksum: Path
    digest: str
    size: int


@dataclass(frozen=True)
class MergeResult:
    """合并结果：输出路径、大小、摘要以及是否用校验文件核对过。"""

    output: Path
    size: int
    digest: str
    verified: bool


def split_plan(size: int, *, part_size: int | None = None, parts: int | None = None) -> list[int]:
    """计算每卷字节数：按大小或按份数二选一（份数超过字节数时按字节拆）。"""
    if size <= 0:
        raise ValueError("文件为空，无需分割")
    if (part_size is None) == (parts is None):
        raise ValueError("请按大小或按份数指定一种分割方式")
    if part_size is not None:
        if part_size <= 0:
            raise ValueError("分卷大小必须大于 0")
        count = (size + part_size - 1) // part_size
        return [part_size] * (count - 1) + [size - (count - 1) * part_size]
    assert parts is not None
    if parts <= 0:
        raise ValueError("分卷份数必须大于 0")
    effective = min(parts, size)
    base, extra = divmod(size, effective)
    return [base + (1 if index < extra else 0) for index in range(effective)]


def part_names(name: str, count: int) -> list[str]:
    """生成从 ``.001`` 起的分卷名，份数超过 999 时自动加宽。"""
    if count <= 0:
        raise ValueError("分卷份数必须大于 0")
    width = max(3, len(str(count)))
    return [f"{name}.{index:0{width}d}" for index in range(1, count + 1)]


def _split_index(name: str) -> tuple[str, int] | None:
    match = _PART_PATTERN.match(name)
    if match is None:
        return None
    return match.group("base"), int(match.group("index"))


def default_merged_name(part: Path) -> str:
    """由分卷名推断合并后的文件名（去掉 ``.NNN``）。"""
    parsed = _split_index(part.name)
    if parsed is None:
        raise ValueError(f"不是分卷文件：{part.name}")
    return parsed[0]


def discover_parts(path: Path) -> list[Path]:
    """从任一分卷发现同组全部分卷，要求编号从 1 连续。"""
    parsed = _split_index(path.name)
    if parsed is None:
        raise ValueError(f"不是分卷文件：{path.name}")
    base, _ = parsed
    candidates: dict[int, Path] = {}
    for sibling in path.parent.glob(f"{base}.*"):
        sibling_parsed = _split_index(sibling.name)
        if sibling_parsed is None or sibling_parsed[0] != base or not sibling.is_file():
            continue
        candidates[sibling_parsed[1]] = sibling
    if not candidates:
        raise FileNotFoundError(f"未找到分卷：{path.name}")
    expected = list(range(1, max(candidates) + 1))
    if sorted(candidates) != expected:
        raise ValueError("分卷编号不连续，无法合并")
    return [candidates[index] for index in expected]


def _write_checksum(path: Path, digest: str, size: int) -> None:
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(handle)
    temp = Path(temp_name)
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{digest}  {size}\n")
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def read_checksum(path: Path) -> tuple[str, int] | None:
    """读取 ``<名>.sha256``；缺失或格式不合法时返回 ``None``。"""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    parts = text.split()
    if len(parts) != 2 or len(parts[0]) != 64:
        return None
    try:
        size = int(parts[1])
    except ValueError:
        return None
    return parts[0].lower(), size


def split_file(
    path: Path,
    *,
    part_size: int | None = None,
    parts: int | None = None,
    overwrite: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> SplitResult:
    """分割文件并写校验文件；任一步失败删除已生成的分卷。"""
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("文件为空，无需分割")
    plan = split_plan(size, part_size=part_size, parts=parts)
    names = part_names(path.name, len(plan))
    targets = [path.parent / name for name in names]
    existing = [target.name for target in targets if target.exists()]
    if existing and not overwrite:
        raise FileExistsError("分卷已存在: " + "、".join(existing[:5]))
    checksum_path = path.parent / f"{path.name}{CHECKSUM_SUFFIX}"

    digest = hashlib.sha256()
    created: list[Path] = []
    written_total = 0
    try:
        with path.open("rb") as source:
            for target, chunk_size in zip(targets, plan, strict=True):
                handle, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
                os.close(handle)
                temp = Path(temp_name)
                try:
                    with temp.open("wb") as sink:
                        remaining = chunk_size
                        while remaining > 0:
                            block = source.read(min(_CHUNK_SIZE, remaining))
                            if not block:
                                raise OSError(f"读取到文件末尾: {path.name}")
                            sink.write(block)
                            digest.update(block)
                            remaining -= len(block)
                            written_total += len(block)
                            if progress is not None:
                                progress(written_total, size, target.name)
                    temp.replace(target)
                except BaseException:
                    temp.unlink(missing_ok=True)
                    raise
                created.append(target)
        _write_checksum(checksum_path, digest.hexdigest(), size)
    except BaseException:
        for target in created:
            target.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        raise
    return SplitResult(tuple(targets), checksum_path, digest.hexdigest(), size)


def merge_parts(
    parts: Sequence[Path],
    output: Path,
    *,
    verify: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> MergeResult:
    """按编号顺序合并分卷；有校验文件时核对摘要与总大小。"""
    sources = [Path(part) for part in parts]
    if not sources:
        raise ValueError("没有可合并的分卷")
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"分卷不存在: {source}")
    parsed = [_split_index(source.name) for source in sources]
    if any(item is None for item in parsed):
        raise ValueError("存在不是分卷的文件")
    bases = {item[0] for item in parsed if item is not None}
    if len(bases) != 1:
        raise ValueError("分卷不属于同一组")
    indices = sorted(item[1] for item in parsed if item is not None)
    if indices != list(range(1, len(indices) + 1)):
        raise ValueError("分卷编号不连续，无法合并")

    ordered = [
        source
        for _index, source in sorted(
            (item[1], source) for item, source in zip(parsed, sources, strict=True) if item is not None
        )
    ]
    total = sum(source.stat().st_size for source in ordered)
    base = bases.pop()
    checksum_path = ordered[0].parent / f"{base}{CHECKSUM_SUFFIX}"
    expected = read_checksum(checksum_path) if verify else None

    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(handle)
    temp = Path(temp_name)
    digest = hashlib.sha256()
    done = 0
    try:
        with temp.open("wb") as sink:
            for source in ordered:
                with source.open("rb") as reader:
                    while True:
                        block = reader.read(_CHUNK_SIZE)
                        if not block:
                            break
                        sink.write(block)
                        digest.update(block)
                        done += len(block)
                        if progress is not None:
                            progress(done, total, source.name)
        actual = digest.hexdigest()
        if expected is not None and (expected[0] != actual or expected[1] != total):
            raise ValueError("分卷校验失败：SHA-256 或总大小与校验文件不一致")
        temp.replace(output)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return MergeResult(output, total, actual, expected is not None)
