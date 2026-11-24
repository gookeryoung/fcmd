"""tomltool - TOML 取值、顶层键列举、JSON 格式化、跨格式转换与语法校验。

示例
----
    fcmd tomltool get pyproject.toml project.name
    fcmd tomltool keys pyproject.toml
    fcmd tomltool format pyproject.toml
    fcmd tomltool convert pyproject.toml --to yaml
    fcmd tomltool validate pyproject.toml
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import fcmd as fx
from fcmd.core.result import report_error
from fcmd.infras import data

from ._convert import write_or_print


@fx.tool(
    "tomltool",
    subcommand="get",
    help="按点路径取值",
    param_help={
        "file": "TOML 文件路径",
        "key": "点分路径（如 project.name）",
    },
)
def get(file: Path, key: str) -> None:
    """按点路径取值。"""
    try:
        result = data.get_toml_value(file, key)
        if isinstance(result, (dict, list)):
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            print(result)
    except (FileNotFoundError, KeyError, ImportError) as exc:
        print(f"错误: {exc}")


@fx.tool(
    "tomltool",
    subcommand="keys",
    help="列出顶层键",
    param_help={"file": "TOML 文件路径"},
)
def keys(file: Path) -> None:
    """列出顶层键。"""
    try:
        keys_list = data.keys_toml(file)
    except (FileNotFoundError, ImportError) as exc:
        print(f"错误: {exc}")
        return
    if not keys_list:
        print("无顶层键")
        return
    for key in keys_list:
        print(key)


@fx.tool(
    "tomltool",
    subcommand="format",
    help="转为 JSON 格式化输出",
    param_help={"file": "TOML 文件路径"},
)
def format(file: Path) -> None:
    """转为 JSON 格式化输出。"""
    try:
        print(data.format_toml(file))
    except (FileNotFoundError, ImportError) as exc:
        print(f"错误: {exc}")


@fx.tool(
    "tomltool",
    subcommand="convert",
    help="转换为 JSON 或 YAML",
    param_help={
        "file": "TOML 文件路径",
        "to": "目标格式（json/yaml）",
        "output": "输出文件路径（省略时写到标准输出）",
    },
)
def convert(file: Path, to: Literal["json", "yaml"] = "json", output: Path | None = None) -> None:
    """把 TOML 转换输出为 JSON / YAML。"""
    try:
        data_obj = data.read_toml(file)
    except FileNotFoundError as exc:
        report_error(
            "tomltool",
            "convert",
            code="FILE_NOT_FOUND",
            message=str(exc),
            field="file",
            expected="存在的 TOML 文件",
            json_output=False,
        )
        return
    except ValueError as exc:
        report_error(
            "tomltool",
            "convert",
            code="INVALID_ARGUMENT",
            message=f"TOML 解析失败: {exc}",
            field="file",
            expected="语法正确的 TOML 文件",
            json_output=False,
        )
        return
    if to == "json":
        content = json.dumps(data_obj, indent=2, ensure_ascii=False, default=str) + "\n"
    else:
        try:
            content = data.pretty_yaml(data_obj)
        except ImportError as exc:
            report_error(
                "tomltool",
                "convert",
                code="RUNTIME_ERROR",
                message=str(exc),
                hint="安装 PyYAML：pip install pyyaml",
                json_output=False,
            )
            return
    write_or_print("tomltool", "convert", content, output)


@fx.tool(
    "tomltool",
    subcommand="validate",
    help="语法校验",
    param_help={"file": "TOML 文件路径"},
)
def validate(file: Path) -> None:
    """语法校验。"""
    try:
        data.validate_toml(file)
    except (FileNotFoundError, ValueError, ImportError) as exc:
        print(f"错误: {exc}")
        return
    print(f"语法校验通过: {file}")
