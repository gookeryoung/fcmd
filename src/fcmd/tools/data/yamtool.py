"""yamtool - YAML 格式化、点路径取值、顶层键列举与语法校验。

示例
----
    fcmd yamtool pretty config.yaml --sort-keys
    fcmd yamtool get config.yaml database.host
    fcmd yamtool keys config.yaml
    fcmd yamtool validate config.yaml
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fcmd as fx
from fcmd.infras import data


def _read_or_report(file: Path) -> Any | None:
    """读取 YAML，失败时打印错误并返回 None（与既有输出一致）。"""
    try:
        return data.read_yaml(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return None
    except ImportError as exc:
        print(f"错误: {exc}")
        return None
    except Exception as exc:
        print(f"YAML 解析失败: {exc}")
        return None


@fx.tool(
    "yamtool",
    subcommand="pretty",
    help="格式化打印 YAML",
    param_help={
        "file": "YAML 文件路径",
        "sort_keys": "是否按键名排序（默认 False）",
        "indent": "缩进空格数（默认 2）",
    },
)
def pretty(file: Path, sort_keys: bool = False, indent: int = 2) -> None:
    """格式化打印 YAML。"""
    data_obj = _read_or_report(file)
    if data_obj is None:
        return
    try:
        print(data.pretty_yaml(data_obj, sort_keys=sort_keys, indent=indent), end="")
    except ImportError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "yamtool",
    subcommand="get",
    help="按点路径取值",
    param_help={
        "file": "YAML 文件路径",
        "path": "点分路径（如 a.b.0.c）",
    },
)
def get(file: Path, path: str) -> None:
    """按点路径取值。"""
    data_obj = _read_or_report(file)
    if data_obj is None:
        return
    try:
        result = data.get_yaml(data_obj, path)
        if isinstance(result, (dict, list)):
            print(data.pretty_yaml(result), end="")
        else:
            print(result)
    except (KeyError, IndexError, TypeError, ValueError, ImportError) as exc:
        print(f"错误: {exc}")


@fx.tool(
    "yamtool",
    subcommand="keys",
    help="列出顶层键",
    param_help={"file": "YAML 文件路径"},
)
def keys(file: Path) -> None:
    """列出顶层键。"""
    data_obj = _read_or_report(file)
    if data_obj is None:
        return
    try:
        for key in data.keys_yaml(data_obj):
            print(key)
    except TypeError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "yamtool",
    subcommand="validate",
    help="校验 YAML 语法",
    param_help={"file": "YAML 文件路径"},
)
def validate(file: Path) -> None:
    """校验 YAML 语法。"""
    try:
        data.validate_yaml(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    except ImportError as exc:
        print(f"错误: {exc}")
        return
    except Exception as exc:
        print(f"语法校验失败: {exc}")
        return
    print(f"语法校验通过: {file}")
