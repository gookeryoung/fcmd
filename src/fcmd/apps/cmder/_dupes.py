"""cmderw 重复文件动作层：复用 :mod:`fcmd.infras.dupes` 的查找结果，负责移动副本。

查找逻辑（大小预筛 + 哈希确认）已下沉到 ``infras/dupes.py``（ADR 0001：纯计算进
infras）；本模块保留 GUI 侧的移动动作，并重新导出查找 API 以保持既有调用方不变。
删除动作由 Tk 层复用现有回收站 / 永久删除入口。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.infras.dupes import (
    DEFAULT_ALGORITHM,
    DuplicateGroup,
    DuplicateReport,
    duplicate_paths,
    find_duplicates,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "DEFAULT_ALGORITHM",
    "DuplicateActionReport",
    "DuplicateGroup",
    "DuplicateReport",
    "duplicate_paths",
    "find_duplicates",
    "move_duplicates",
]


@dataclass(frozen=True)
class DuplicateActionReport:
    """移动结果：成功的源路径与逐项错误。"""

    completed: tuple[Path, ...] = ()
    errors: tuple[tuple[Path, str], ...] = ()


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
