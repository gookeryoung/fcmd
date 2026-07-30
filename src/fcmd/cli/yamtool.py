"""yamtool - YAML 处理工具。

基于 ``PyYAML``（``yaml.safe_load``/``yaml.safe_dump``）提供 YAML 格式化、
点路径查询、键列举与语法校验。

示例
----
    fcmd yamtool pretty config.yaml                # 格式化打印
    fcmd yamtool pretty config.yaml --sort-keys    # 按键名排序
    fcmd yamtool get config.yaml database.host      # 点路径取值
    fcmd yamtool keys config.yaml                   # 列出顶层键
    fcmd yamtool validate config.yaml               # 语法校验
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-not-found]

import fcmd

__all__ = [
    "get_yaml",
    "keys_yaml",
    "pretty_yaml",
    "read_yaml",
    "validate_yaml",
    "write_yaml",
]


# ============================================================================
# 公共函数
# ============================================================================


def read_yaml(filepath: Path) -> Any:
    """读取 YAML 文件并解析为 Python 对象。

    使用 ``yaml.safe_load`` 避免 arbitrary code execution。

    Parameters
    ----------
    filepath:
        YAML 文件路径

    Returns
    -------
    Any
        解析后的对象（dict/list/str/number/bool/None）

    Raises
    ------
    FileNotFoundError
        文件不存在
    yaml.YAMLError
        YAML 语法错误
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    with filepath.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_yaml(filepath: Path, data: Any, sort_keys: bool = False) -> None:
    """写入 YAML 文件。

    Parameters
    ----------
    filepath:
        目标 YAML 文件路径
    data:
        待序列化的对象
    sort_keys:
        是否按键名排序（默认 ``False``，保持插入顺序）
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            sort_keys=sort_keys,
            indent=2,
            allow_unicode=True,
            default_flow_style=False,
        )


def pretty_yaml(data: Any, sort_keys: bool = False, indent: int = 2) -> str:
    """格式化为 YAML 字符串。

    Parameters
    ----------
    data:
        待格式化的对象
    sort_keys:
        是否按键名排序（默认 ``False``）
    indent:
        缩进空格数（默认 2）

    Returns
    -------
    str
        YAML 文本
    """
    return yaml.safe_dump(
        data,
        sort_keys=sort_keys,
        indent=indent,
        allow_unicode=True,
        default_flow_style=False,
    )


def get_yaml(data: Any, path: str) -> Any:
    """按点路径查询 YAML 对象。

    路径段以 ``.`` 分隔，数字段视为列表索引，其他段视为对象键。
    空路径返回原对象。

    示例
    ----
        >>> data = {"a": {"b": [{"c": 1}]}}
        >>> get_yaml(data, "a.b.0.c")
        1

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
        路径段类型不匹配
    ValueError
        路径格式错误（如空段）
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
            except ValueError as exc:
                raise TypeError(f"列表索引必须是整数，得到: {seg}") from exc
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


def keys_yaml(data: Any) -> list[str]:
    """列出顶层键。

    Parameters
    ----------
    data:
        待查询的对象

    Returns
    -------
    list[str]
        顶层键列表（dict 返回键，list 返回索引字符串，其他返回空列表）

    Raises
    ------
    TypeError
        ``data`` 非 dict/list 时
    """
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        return [str(i) for i in range(len(data))]
    raise TypeError(f"无法对非容器类型 {type(data).__name__} 列键")


def validate_yaml(filepath: Path) -> None:
    """校验 YAML 文件是否语法正确。

    Parameters
    ----------
    filepath:
        YAML 文件路径

    Raises
    ------
    FileNotFoundError
        文件不存在
    yaml.YAMLError
        YAML 语法错误
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    with filepath.open("r", encoding="utf-8") as f:
        yaml.safe_load(f)


# ============================================================================
# CLI 子命令
# ============================================================================


def _read_or_print(filepath: Path) -> Any | None:
    """读取 YAML 文件，失败时打印错误并返回 ``None``。"""
    try:
        return read_yaml(filepath)
    except FileNotFoundError as exc:
        print(str(exc))
        return None
    except yaml.YAMLError as exc:
        print(f"YAML 解析失败: {exc}")
        return None


@fcmd.tool("yamtool", subcommand="pretty", help="格式化打印 YAML")
def yaml_pretty_cmd(file: Path, sort_keys: bool = False, indent: int = 2) -> None:
    """格式化打印 YAML 文件内容。

    Parameters
    ----------
    file:
        YAML 文件路径
    sort_keys:
        是否按键名排序（默认 ``False``）
    indent:
        缩进空格数（默认 2）
    """
    data = _read_or_print(file)
    if data is None:
        return
    print(pretty_yaml(data, sort_keys=sort_keys, indent=indent), end="")


@fcmd.tool("yamtool", subcommand="get", help="按点路径取值")
def yaml_get_cmd(file: Path, path: str) -> None:
    """按点路径查询 YAML 文件并打印结果。

    Parameters
    ----------
    file:
        YAML 文件路径
    path:
        点分路径（如 ``a.b.0.c``）
    """
    data = _read_or_print(file)
    if data is None:
        return
    try:
        result = get_yaml(data, path)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        print(str(exc))
        return
    if isinstance(result, (dict, list)):
        print(pretty_yaml(result), end="")
    else:
        print(result)


@fcmd.tool("yamtool", subcommand="keys", help="列出顶层键")
def yaml_keys_cmd(file: Path) -> None:
    """列出 YAML 文件的顶层键。

    Parameters
    ----------
    file:
        YAML 文件路径
    """
    data = _read_or_print(file)
    if data is None:
        return
    try:
        keys = keys_yaml(data)
    except TypeError as exc:
        print(str(exc))
        return
    for key in keys:
        print(key)


@fcmd.tool("yamtool", subcommand="validate", help="校验 YAML 语法")
def yaml_validate_cmd(file: Path) -> None:
    """校验 YAML 文件是否语法正确。

    Parameters
    ----------
    file:
        YAML 文件路径
    """
    try:
        validate_yaml(file)
    except FileNotFoundError as exc:
        print(str(exc))
        return
    except yaml.YAMLError as exc:
        print(f"语法校验失败: {exc}")
        return
    print(f"语法校验通过: {file}")


@fcmd.main("yamtool")
def main() -> None:
    pass
