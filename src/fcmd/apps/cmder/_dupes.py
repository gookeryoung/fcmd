"""cmderw 重复文件查找纯逻辑：大小预筛 + 哈希确认，结果可删除或移动。

遍历复用 :func:`fcmd.infras.hashing.iter_directory_files`，哈希复用
:func:`fcmd.infras.hashing.compute_file_hash`；只有大小相同的候选才计算哈希，
避免对全部文件做 I/O。删除动作由 Tk 层复用现有回收站 / 永久删除入口，本模块
只负责「找出重复组」与「把多余副本移动到目标目录」。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.infras import hashing

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "DEFAULT_ALGORITHM",
    "DuplicateActionReport",
    "DuplicateGroup",
    "DuplicateReport",
    "duplicate_paths",
    "find_duplicates",
    "move_duplicates",
]

DEFAULT_ALGORITHM = "sha256"


@dataclass(frozen=True)
class DuplicateGroup:
    """一组内容相同的文件；``paths`` 按路径排序，第一项为默认保留项。"""

    size: int
    digest: str
    paths: tuple[Path, ...]

    @property
    def wasted(self) -> int:
        """删除多余副本可回收的字节数。"""
        return self.size * (len(self.paths) - 1)


@dataclass(frozen=True)
class DuplicateReport:
    """扫描结果：重复组、扫描文件数与可回收字节数。"""

    groups: tuple[DuplicateGroup, ...]
    scanned: int
    wasted: int


@dataclass(frozen=True)
class DuplicateActionReport:
    """移动结果：成功的源路径与逐项错误。"""

    completed: tuple[Path, ...] = ()
    errors: tuple[tuple[Path, str], ...] = ()


def _identity(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path.absolute()))


def _collect_files(roots: Sequence[Path]) -> list[Path]:
    seen: set[str] = set()
    files: list[Path] = []
    for raw_root in roots:
        root = Path(raw_root)
        if root.is_dir():
            candidates: Iterable[Path] = hashing.iter_directory_files(root)
        elif root.is_file():
            candidates = (root,)
        else:
            raise FileNotFoundError(f"路径不存在: {root}")
        for candidate in candidates:
            key = _identity(candidate)
            if key in seen:
                continue
            seen.add(key)
            files.append(candidate)
    return files


def find_duplicates(
    roots: Sequence[Path],
    *,
    algorithm: str = DEFAULT_ALGORITHM,
    min_size: int = 1,
    progress: Callable[[int, int, str], None] | None = None,
) -> DuplicateReport:
    """查找重复文件：先按大小分桶，仅对同大小候选计算哈希确认。"""
    hashing.ensure_algorithm(algorithm)
    if min_size < 0:
        raise ValueError("最小大小不能为负数")
    files = _collect_files(roots)
    buckets: dict[int, list[Path]] = {}
    for file_path in files:
        try:
            size = file_path.stat().st_size
        except OSError:
            continue
        if size < min_size:
            continue
        buckets.setdefault(size, []).append(file_path)
    candidates = {size: items for size, items in buckets.items() if len(items) > 1}
    total = sum(len(items) for items in candidates.values())
    groups: list[DuplicateGroup] = []
    done = 0
    for size in sorted(candidates, reverse=True):
        by_digest: dict[str, list[Path]] = {}
        for file_path in candidates[size]:
            try:
                digest = hashing.compute_file_hash(file_path, algorithm)
            except OSError:
                done += 1
                continue
            by_digest.setdefault(digest, []).append(file_path)
            done += 1
            if progress is not None:
                progress(done, total, file_path.name)
        for digest, paths in by_digest.items():
            if len(paths) > 1:
                ordered = tuple(sorted(paths, key=str))
                groups.append(DuplicateGroup(size, digest, ordered))
    groups.sort(key=lambda group: (-group.wasted, group.digest))
    wasted = sum(group.wasted for group in groups)
    return DuplicateReport(tuple(groups), len(files), wasted)


def duplicate_paths(report: DuplicateReport) -> list[Path]:
    """返回每组除保留项（第一项）之外的多余副本。"""
    return [path for group in report.groups for path in group.paths[1:]]


def _unique_target(directory: Path, name: str) -> Path:
    target = directory / name
    if not (target.exists() or target.is_symlink()):
        return target
    suffix = target.suffix
    stem = name[: -len(suffix)] if suffix else name
    for index in range(2, 10000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not (candidate.exists() or candidate.is_symlink()):
            return candidate
    raise FileExistsError(f"无法生成唯一名称: {target}")


def move_duplicates(
    paths: Sequence[Path],
    destination: Path,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> DuplicateActionReport:
    """把多余副本移动到 ``destination``，同名时自动加序号，逐项记录错误。"""
    if not destination.is_dir():
        raise NotADirectoryError(f"目标不是目录: {destination}")
    completed: list[Path] = []
    errors: list[tuple[Path, str]] = []
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        try:
            target = _unique_target(destination, path.name)
            shutil.move(str(path), str(target))
            completed.append(path)
        except (OSError, ValueError) as exc:
            errors.append((path, str(exc)))
        if progress is not None:
            progress(index, total, path.name)
    return DuplicateActionReport(tuple(completed), tuple(errors))
