"""文件过滤模型。

本模块是文件遍历忽略规则的唯一出处：``DEFAULT_IGNORE_*`` 提供跨工具共享的
默认规则，``IgnoreSpec`` 描述自定义规则，``should_skip_parts`` 负责路径匹配。
filesearch/hashfile/folderzip/archivex 等工具统一调用本模块，不再各自维护副本。

示例
----
    from fcmd.core.filefilter import DEFAULT_IGNORE_SPEC, should_skip_parts

    if should_skip_parts(path.parts, DEFAULT_IGNORE_SPEC):
        continue

    from fcmd.core.models import IgnoreSpec, to_shutil_ignore
    import shutil

    spec = IgnoreSpec.from_iterable([".git", "__pycache__", "*.pyc"])
    shutil.copytree(src, dst, ignore=to_shutil_ignore(spec))
"""

from __future__ import annotations

import fnmatch
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_IGNORE_DIRS",
    "DEFAULT_IGNORE_EXTS",
    "DEFAULT_IGNORE_SPEC",
    "IgnoreSpec",
    "should_ignore",
    "should_skip_parts",
    "to_shutil_ignore",
]

# 通配符字符，用于区分目录名与 glob 模式
_GLOB_CHARS: frozenset[str] = frozenset("*?[")


@dataclass(frozen=True)
class IgnoreSpec:
    """文件忽略规则（不可变值对象）。

    组合目录名与 glob 模式，统一描述文件遍历时的跳过规则。
    字段含 ``dirs``（跳过的目录名集合，如 ``.git``/``__pycache__``）与
    ``patterns``（跳过的 glob 模式元组，如 ``*.pyc``/``*.egg-info``）。
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


# 默认忽略的目录名（含 ``*.egg-info`` 通配），跨工具共享
DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        ".idea",
        ".vscode",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pyrefly_cache",
        "*.egg-info",
    }
)

# 默认忽略的扩展名（压缩包与字节码）
DEFAULT_IGNORE_EXTS: frozenset[str] = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".pyc", ".pyo"})

# 默认目录规则的 ``IgnoreSpec`` 形态（供 should_skip_parts 直接使用）
DEFAULT_IGNORE_SPEC: IgnoreSpec = IgnoreSpec.from_iterable(DEFAULT_IGNORE_DIRS)


def should_skip_parts(parts: tuple[str, ...], spec: IgnoreSpec = DEFAULT_IGNORE_SPEC) -> bool:
    """判断路径的各级组件是否命中忽略规则。

    ``spec.dirs`` 中的目录名按组件精确匹配；``spec.patterns`` 中的 glob
    模式（如 ``*.egg-info``）对每一级组件做 ``fnmatch`` 匹配。

    Parameters
    ----------
    parts:
        路径组件元组（``Path.parts``）
    spec:
        忽略规则，默认 ``DEFAULT_IGNORE_SPEC``

    Returns
    -------
    bool
        命中返回 True
    """
    for part in parts:
        if part in spec.dirs:
            return True
    return any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in spec.patterns)


def should_ignore(path: Path, spec: IgnoreSpec) -> bool:
    """判断路径是否应被忽略。

    依次检查路径的每一级组件：目录名是否在 ``spec.dirs`` 中、
    组件是否命中 ``spec.patterns`` 中的 glob 模式（含文件名）。

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
    return should_skip_parts(path.parts, spec)


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
