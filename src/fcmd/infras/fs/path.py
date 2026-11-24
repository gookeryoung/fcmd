"""``fcmd.infras.fs.path``（由 ``infras/fs.py`` 拆分）。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "join_path",
    "normalize_path",
    "path_diff",
    "path_parts",
    "relative_to",
]


def normalize_path(path: Path) -> Path:
    """规范化路径：展开用户目录、解析为绝对路径、消除 ``..``/``.``。"""
    return path.expanduser().resolve(strict=False)


def relative_to(path: Path, base: Path) -> Path:
    """计算 ``path`` 相对 ``base`` 的相对路径。"""
    p1 = normalize_path(path)
    p2 = normalize_path(base)
    return p1.relative_to(p2)


def join_path(parts: Sequence[str | Path]) -> Path:
    """按顺序拼接路径片段并规范化（展开 ``~``、绝对化、消除 ``..``/``.``）。"""
    if not parts:
        raise ValueError("至少需要一个路径片段")
    joined = Path(parts[0])
    for part in parts[1:]:
        joined = joined / part
    return normalize_path(joined)


def path_parts(path: Path) -> dict[str, Any]:
    """提取路径各部分信息。"""
    p = normalize_path(path)
    return {
        "input": str(path),
        "absolute": str(p),
        "anchor": p.anchor,
        "parent": str(p.parent),
        "name": p.name,
        "stem": p.stem,
        "suffix": p.suffix,
        "suffixes": p.suffixes,
        "parts": list(p.parts),
    }


def path_diff(p1: Path, p2: Path) -> tuple[list[str], list[str], list[str]]:
    """比较两路径的组件差异，返回 ``(common, only_p1, only_p2)``。"""
    parts1 = list(normalize_path(p1).parts)
    parts2 = list(normalize_path(p2).parts)
    common: list[str] = []
    for a, b in zip(parts1, parts2, strict=False):
        if a != b:
            break
        common.append(a)
    only_p1 = parts1[len(common) :]
    only_p2 = parts2[len(common) :]
    return (common, only_p1, only_p2)
