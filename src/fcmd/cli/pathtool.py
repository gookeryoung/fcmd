"""pathtool - 路径处理工具。

基于标准库 ``pathlib`` 提供路径规范化、相对路径计算、各部分提取、路径差异比较能力。

示例
----
    fcmd pathtool show ./src/fcmd/cli/main.py     # 显示路径各部分
    fcmd pathtool rel ./src/fcmd/cli .            # 计算相对路径
    fcmd pathtool norm ~/projects/../src          # 规范化路径
    fcmd pathtool diff ./src/fcmd ./src/tests     # 比较两路径差异
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fcmd

__all__ = [
    "normalize_path",
    "path_diff",
    "path_parts",
    "relative_to",
]


def normalize_path(path: Path) -> Path:
    """规范化路径：展开用户目录、解析为绝对路径、消除 ``..``/``.``。

    不解析符号链接（保留路径信息），仅做词法规范化。

    Parameters
    ----------
    path:
        待规范化的路径

    Returns
    -------
    Path
        规范化后的绝对路径
    """
    # Python 3.8 Windows 上 Path.resolve(strict=False) 对不存在路径可能返回相对路径，
    # 先用 absolute() 确保绝对化（基于 cwd），再 resolve(strict=False) 消除 .. / .
    return path.expanduser().absolute().resolve(strict=False)


def relative_to(path: Path, base: Path) -> Path:
    """计算 ``path`` 相对 ``base`` 的相对路径。

    Parameters
    ----------
    path:
        目标路径
    base:
        基准路径

    Returns
    -------
    Path
        相对路径

    Raises
    ------
    ValueError
        ``path`` 不在 ``base`` 之下（含不同驱动器卷标）
    """
    p1 = normalize_path(path)
    p2 = normalize_path(base)
    return p1.relative_to(p2)


def path_parts(path: Path) -> dict[str, Any]:
    """提取路径各部分信息。

    返回字典包含：``anchor``（卷标/root）、``parent``（父目录）、``name``（文件名）、
    ``stem``（主干名）、``suffix``（扩展名）、``suffixes``（所有扩展名列表）、
    ``parts``（组件元组）、``absolute``（绝对路径字符串）。

    Parameters
    ----------
    path:
        待解析的路径

    Returns
    -------
    dict[str, Any]
        各部分信息字典
    """
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
    """比较两路径的组件差异。

    返回 ``(common, only_p1, only_p2)``：公共前缀组件、仅在 p1 的组件、仅在 p2 的组件。

    示例
    ----
        >>> path_diff(Path("a/b/c/d"), Path("a/b/x/y"))
        (["a", "b"], ["c", "d"], ["x", "y"])

    Parameters
    ----------
    p1, p2:
        待比较的两路径

    Returns
    -------
    tuple
        ``(common, only_p1, only_p2)``
    """
    parts1 = list(normalize_path(p1).parts)
    parts2 = list(normalize_path(p2).parts)
    # 计算公共前缀
    common: list[str] = []
    for a, b in zip(parts1, parts2):
        if a != b:
            break
        common.append(a)
    only_p1 = parts1[len(common) :]
    only_p2 = parts2[len(common) :]
    return (common, only_p1, only_p2)


@fcmd.tool("pathtool", subcommand="show", help="显示路径各部分信息")
def pathtool_show(path: Path) -> None:
    """显示路径各部分信息（anchor/parent/name/stem/suffix 等）。

    Parameters
    ----------
    path:
        待解析的路径
    """
    info = path_parts(path)
    print(f"输入路径:   {info['input']}")
    print(f"绝对路径:   {info['absolute']}")
    print(f"卷标/root: {info['anchor'] or '(无)'}")
    print(f"父目录:     {info['parent']}")
    print(f"文件名:     {info['name'] or '(无)'}")
    print(f"主干名:     {info['stem'] or '(无)'}")
    print(f"扩展名:     {info['suffix'] or '(无)'}")
    if info["suffixes"]:
        print(f"所有扩展名: {' '.join(info['suffixes'])}")
    print(f"组件:       {' / '.join(info['parts'])}")


@fcmd.tool("pathtool", subcommand="rel", help="计算相对路径")
def pathtool_rel(path: Path, base: Path) -> None:
    """计算 ``path`` 相对 ``base`` 的相对路径。

    Parameters
    ----------
    path:
        目标路径
    base:
        基准路径
    """
    try:
        rel = relative_to(path, base)
    except ValueError as e:
        print(f"无法计算相对路径: {e}")
        return
    print(str(rel))


@fcmd.tool("pathtool", subcommand="norm", help="规范化路径")
def pathtool_norm(path: Path) -> None:
    """规范化路径（展开 ~、绝对化、消除 ``..``/``.``）。

    Parameters
    ----------
    path:
        待规范化的路径
    """
    print(str(normalize_path(path)))


@fcmd.tool("pathtool", subcommand="diff", help="比较两路径差异")
def pathtool_diff(p1: Path, p2: Path) -> None:
    """比较两路径组件差异，输出公共前缀与各自独有部分。

    Parameters
    ----------
    p1, p2:
        待比较的两路径
    """
    common, only1, only2 = path_diff(p1, p2)
    print(f"路径 1: {normalize_path(p1)}")
    print(f"路径 2: {normalize_path(p2)}")
    if common:
        print(f"公共前缀: {' / '.join(common)}")
    else:
        print("公共前缀: (无)")
    if only1:
        print(f"仅路径 1: {' / '.join(only1)}")
    else:
        print("仅路径 1: (无)")
    if only2:
        print(f"仅路径 2: {' / '.join(only2)}")
    else:
        print("仅路径 2: (无)")
