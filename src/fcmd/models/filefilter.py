"""文件过滤模型。

提供统一的文件忽略规则，替代各工具中分散的 ``IGNORE_DIRS``/``IGNORE_PATTERNS``。

示例
----
    from fcmd.models import IgnoreSpec, to_shutil_ignore
    import shutil

    spec = IgnoreSpec.from_iterable([".git", "__pycache__", "*.pyc"])
    shutil.copytree(src, dst, ignore=to_shutil_ignore(spec))
"""

from __future__ import annotations

import fnmatch
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["IgnoreSpec", "should_ignore", "to_shutil_ignore"]

# 通配符字符，用于区分目录名与 glob 模式
_GLOB_CHARS: frozenset[str] = frozenset("*?[")


@dataclass(frozen=True)
class IgnoreSpec:
    """文件忽略规则（不可变值对象）。

    组合目录名与 glob 模式，统一描述文件遍历时的跳过规则。

    Attributes
    ----------
    dirs:
        跳过的目录名集合（如 ``.git``/``__pycache__``）
    patterns:
        跳过的 glob 模式元组（如 ``*.pyc``/``*.egg-info``）
    """

    dirs: frozenset[str] = frozenset()
    patterns: tuple[str, ...] = ()

    @classmethod
    def from_iterable(
        cls,
        items: list[str] | tuple[str, ...] | set[str] | frozenset[str],
    ) -> IgnoreSpec:
        """从可迭代项构建，自动识别目录名与 glob 模式。

        含通配符（``*``/``?``/``[``）的项归入 ``patterns``，其余归入 ``dirs``。

        Parameters
        ----------
        items:
            待分类的忽略项集合

        Returns
        -------
        IgnoreSpec
            构建好的忽略规则
        """
        dirs: set[str] = set()
        patterns: list[str] = []
        for item in items:
            if any(c in item for c in _GLOB_CHARS):
                patterns.append(item)
            else:
                dirs.add(item)
        return cls(dirs=frozenset(dirs), patterns=tuple(patterns))


def should_ignore(path: Path, spec: IgnoreSpec) -> bool:
    """判断路径是否应被忽略。

    检查路径的每一级目录名是否在 ``spec.dirs`` 中，
    以及文件名是否匹配 ``spec.patterns`` 中的任一模式。

    Parameters
    ----------
    path:
        待检查的路径
    spec:
        忽略规则

    Returns
    -------
    bool
        应忽略返回 True
    """
    for part in path.parts:
        if part in spec.dirs:
            return True
    name = path.name
    return any(fnmatch.fnmatch(name, pattern) for pattern in spec.patterns)


def to_shutil_ignore(spec: IgnoreSpec) -> Callable[[Any, list[str]], set[str]]:
    """转换为 ``shutil.copytree`` 的 ignore 参数。

    Parameters
    ----------
    spec:
        忽略规则

    Returns
    -------
    Callable[[Any, list[str]], set[str]]
        可直接传给 ``shutil.copytree(ignore=...)`` 的可调用对象
    """
    return shutil.ignore_patterns(*spec.patterns, *spec.dirs)
