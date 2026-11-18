"""重复文件查找基础件：大小预筛 + 哈希确认（只读）。

遍历复用 :func:`fcmd.infras.hashing.iter_directory_files`，哈希复用
:func:`fcmd.infras.hashing.compute_file_hash`；只有大小相同的候选才计算哈希，
避免对全部文件做 I/O。只负责「找出重复组」，不涉及删除或移动。

``apps/cmder/_dupes.py`` 复用本模块，并保留 GUI 侧的移动动作。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.infras import hashing

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DEFAULT_ALGORITHM",
    "DuplicateGroup",
    "DuplicateReport",
    "duplicate_paths",
    "find_duplicates",
    "report_to_mapping",
]

DEFAULT_ALGORITHM = "sha256"

# 并行哈希的最大线程数：``hashlib`` 计算时释放 GIL，SSD 上多线程接近线性提速；
# 机械盘/小目录由 min(total) 自动收敛，避免过度并发。
_MAX_HASH_WORKERS = 8


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


def _hash_candidates(
    files: Sequence[Path],
    algorithm: str,
    progress: Callable[[int, int, str], None] | None,
) -> dict[Path, str]:
    """并发计算候选文件哈希，返回 ``路径 → 摘要``（读取失败的文件省略）。

    ``hashlib`` 在计算时释放 GIL，线程池即可获得接近线性的多核提速；进度
    回调仍在调用线程（主线程）触发，``done`` 单调递增、``total`` 与串行一致。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(files)
    workers = max(1, min(_MAX_HASH_WORKERS, os.cpu_count() or 1, total))
    digests: dict[Path, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(hashing.compute_file_hash, path, algorithm): path for path in files}
        for future in as_completed(futures):
            file_path = futures[future]
            try:
                digest = future.result()
            except OSError:
                done += 1
                continue
            digests[file_path] = digest
            done += 1
            if progress is not None:
                progress(done, total, file_path.name)
    return digests


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
    ordered_sizes = sorted(candidates, reverse=True)
    digests = _hash_candidates(
        [file_path for size in ordered_sizes for file_path in candidates[size]], algorithm, progress
    )
    groups: list[DuplicateGroup] = []
    for size in ordered_sizes:
        by_digest: dict[str, list[Path]] = {}
        for file_path in candidates[size]:
            digest = digests.get(file_path)
            if digest is None:
                continue
            by_digest.setdefault(digest, []).append(file_path)
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


def report_to_mapping(report: DuplicateReport, *, limit: int = 0) -> dict[str, object]:
    """把扫描结果转成机器可读映射（``--json`` 与人类输出的同一数据源）。

    ``limit > 0`` 时只保留前 ``limit`` 组，并用 ``truncated`` 标记是否被截断；
    总量字段 ``total_groups`` / ``reclaimable_bytes`` 始终是全量结果。
    """
    groups = report.groups if limit <= 0 else report.groups[:limit]
    return {
        "scanned": report.scanned,
        "total_groups": len(report.groups),
        "reclaimable_bytes": report.wasted,
        "returned": len(groups),
        "truncated": len(groups) < len(report.groups),
        "groups": [
            {
                "size": group.size,
                "digest": group.digest,
                "wasted": group.wasted,
                "paths": [str(path) for path in group.paths],
            }
            for group in groups
        ],
    }
