"""pathtool - 路径解析、规范化与差异计算。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras.fs import normalize_path, path_diff, path_parts, relative_to

__all__ = ["diff", "norm", "rel", "show"]


@fx.tool(
    "pathtool",
    subcommand="show",
    help="显示路径各部分信息",
    param_help={"path": "待解析的路径"},
)
def show(path: Path) -> None:
    """显示路径各部分信息（anchor/parent/name/stem/suffix 等）。"""
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


@fx.tool(
    "pathtool",
    subcommand="rel",
    help="计算相对路径",
    param_help={
        "path": "目标路径",
        "base": "基准路径",
    },
)
def rel(path: Path, base: Path) -> None:
    """计算 ``path`` 相对 ``base`` 的相对路径。"""
    try:
        result = relative_to(path, base)
    except ValueError as exc:
        print(f"错误: 无法计算相对路径: {exc}")
        return
    print(str(result))


@fx.tool(
    "pathtool",
    subcommand="norm",
    help="规范化路径",
    param_help={"path": "待规范化的路径"},
)
def norm(path: Path) -> None:
    """规范化路径（展开 ~、绝对化、消除 ``..``/``.``）。"""
    print(str(normalize_path(path)))


@fx.tool(
    "pathtool",
    subcommand="diff",
    help="比较两路径差异",
    param_help={
        "p1": "待比较的路径 1",
        "p2": "待比较的路径 2",
    },
)
def diff(p1: Path, p2: Path) -> None:
    """比较两路径组件差异，输出公共前缀与各自独有部分。"""
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
