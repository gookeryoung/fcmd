"""Python 3.10 兼容垫片：3.11/3.12 才有的标准库 API。"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TypeVar

__all__ = ["batched", "is_junction", "override"]

if sys.version_info >= (3, 12):  # pragma: no cover - 3.12+
    from typing import override
else:  # pragma: no cover - 3.10 / 3.11
    from typing_extensions import override


_T = TypeVar("_T")


def batched(iterable: Iterable[_T], n: int, *, strict: bool = False) -> Iterator[tuple[_T, ...]]:
    """``itertools.batched`` 的 3.10 兼容实现（3.13+ 直接转标准库）。"""
    if sys.version_info >= (3, 13):  # pragma: no cover - 3.13+
        from itertools import batched as _batched

        yield from _batched(iterable, n, strict=strict)
        return
    if n < 1:
        raise ValueError("n must be at least one")
    batch: list[_T] = []
    for item in iterable:
        batch.append(item)
        if len(batch) == n:
            yield tuple(batch)
            batch = []
    if batch:
        if strict:
            raise ValueError("batched(): incomplete batch")
        yield tuple(batch)


def is_junction(path: object) -> bool:
    """Python 3.12 的 ``Path.is_junction`` 兼容实现（3.10 / 3.11）。

    支持 ``Path`` 与 ``os.DirEntry`` 等带 ``is_junction`` 的对象；3.10/3.11
    回落到 Windows reparse point 属性判断。
    """
    checker = getattr(path, "is_junction", None)
    if callable(checker):
        return bool(checker())
    target = Path(path)  # type: ignore[arg-type]
    if os.name != "nt":
        return False
    try:
        info = os.lstat(target)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(info, "st_file_attributes", 0) & reparse) and target.is_dir() and not target.is_symlink()
