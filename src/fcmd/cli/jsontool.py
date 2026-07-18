"""jsontool - JSON 处理工具。

基于标准库 ``json`` 提供 JSON 格式化、压缩、点路径查询、按键排序能力。

示例
----
    fcmd jsontool pretty data.json               # 格式化打印（默认 2 空格缩进）
    fcmd jsontool pretty data.json --indent 4    # 4 空格缩进
    fcmd jsontool minify data.json               # 压缩为单行
    fcmd jsontool query data.json a.b.0          # 点路径查询 a.b[0]
    fcmd jsontool sort data.json --output out.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fcmd

__all__ = [
    "minify_json",
    "pretty_json",
    "query_json",
    "read_json",
    "sort_keys",
    "write_json",
]


def read_json(filepath: Path) -> Any:
    """读取 JSON 文件并解析为 Python 对象。

    Parameters
    ----------
    filepath:
        JSON 文件路径

    Returns
    -------
    Any
        解析后的对象（dict/list/str/number/bool/None）

    Raises
    ------
    FileNotFoundError
        文件不存在
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    with filepath.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(filepath: Path, data: Any, indent: int = 2) -> None:
    """写入 JSON 文件。

    Parameters
    ----------
    filepath:
        目标 JSON 文件路径
    data:
        待序列化的对象
    indent:
        缩进空格数（默认 2）
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def pretty_json(data: Any, indent: int = 2) -> str:
    """格式化为多行 JSON 字符串。

    Parameters
    ----------
    data:
        待格式化的对象
    indent:
        缩进空格数（默认 2）

    Returns
    -------
    str
        多行 JSON 文本
    """
    return json.dumps(data, ensure_ascii=False, indent=indent)


def minify_json(data: Any) -> str:
    """压缩为单行 JSON 字符串（无空白）。

    Parameters
    ----------
    data:
        待压缩的对象

    Returns
    -------
    str
        单行 JSON 文本
    """
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def query_json(data: Any, path: str) -> Any:
    """按点路径查询 JSON 对象。

    路径段以 ``.`` 分隔，数字段视为列表索引，其他段视为对象键。
    空路径返回原对象。

    示例
    ----
        >>> data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
        >>> query_json(data, "a.b.0.c")
        1
        >>> query_json(data, "a.b.1.c")
        2

    Parameters
    ----------
    data:
        待查询的对象
    path:
        点分路径（如 ``a.b.0.c``）

    Returns
    -------
    Any
        路径指向的值

    Raises
    ------
    KeyError
        对象键不存在
    IndexError
        列表索引越界
    TypeError
        路径段类型不匹配（如对列表用字符串键）
    ValueError
        路径格式错误（如空段 ``a..b``）
    """
    if not path:
        return data
    segments = path.split(".")
    if any(seg == "" for seg in segments):
        raise ValueError(f"路径格式错误（含空段）: {path}")
    current: Any = data
    for seg in segments:
        if isinstance(current, list):
            try:
                idx = int(seg)
            except ValueError as e:
                raise TypeError(f"列表索引必须是整数，得到: {seg}") from e
            if idx < 0 or idx >= len(current):
                raise IndexError(f"列表索引越界: {idx}（长度 {len(current)}）")
            current = current[idx]
        elif isinstance(current, dict):
            if seg not in current:
                raise KeyError(f"键不存在: {seg}")
            current = current[seg]
        else:
            raise TypeError(f"无法对非容器类型 {type(current).__name__} 取子项: {seg}")
    return current


def sort_keys(data: Any) -> Any:
    """递归按键名排序（仅影响对象，不影响数组顺序）。

    Parameters
    ----------
    data:
        待排序的对象

    Returns
    -------
    Any
        排序后的新对象（原对象不变）
    """
    if isinstance(data, dict):
        return {key: sort_keys(data[key]) for key in sorted(data)}
    if isinstance(data, list):
        return [sort_keys(item) for item in data]
    return data


@fcmd.tool("jsontool", subcommand="pretty", help="格式化打印 JSON")
def jsontool_pretty(file: Path, indent: int = 2) -> None:
    """格式化打印 JSON 文件内容。

    Parameters
    ----------
    file:
        JSON 文件路径
    indent:
        缩进空格数（默认 2）
    """
    try:
        data = read_json(file)
    except FileNotFoundError as e:
        print(str(e))
        return
    print(pretty_json(data, indent=indent))


@fcmd.tool("jsontool", subcommand="minify", help="压缩 JSON 为单行")
def jsontool_minify(file: Path) -> None:
    """压缩 JSON 文件为单行输出。

    Parameters
    ----------
    file:
        JSON 文件路径
    """
    try:
        data = read_json(file)
    except FileNotFoundError as e:
        print(str(e))
        return
    print(minify_json(data))


@fcmd.tool("jsontool", subcommand="query", help="按点路径查询 JSON")
def jsontool_query(file: Path, path: str) -> None:
    """按点路径查询 JSON 文件并打印结果。

    Parameters
    ----------
    file:
        JSON 文件路径
    path:
        点分路径（如 ``a.b.0.c``）
    """
    try:
        data = read_json(file)
    except FileNotFoundError as e:
        print(str(e))
        return
    try:
        result = query_json(data, path)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        print(str(e))
        return
    # 标量直接打印，容器格式化为 JSON
    if isinstance(result, (dict, list)):
        print(pretty_json(result))
    else:
        print(result)


@fcmd.tool("jsontool", subcommand="sort", help="按键排序 JSON")
def jsontool_sort(file: Path, output: str = "") -> None:
    """按键名递归排序 JSON 并输出到文件或打印。

    Parameters
    ----------
    file:
        JSON 文件路径
    output:
        输出 JSON 路径（默认: 打印到标准输出）
    """
    try:
        data = read_json(file)
    except FileNotFoundError as e:
        print(str(e))
        return
    sorted_data = sort_keys(data)
    if output:
        write_json(Path(output), sorted_data)
        print(f"排序完成: {file} -> {output}")
    else:
        print(pretty_json(sorted_data))
