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
from typing import Any, Literal

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import data

from ._convert import write_or_print


def _jsonable(value: Any) -> Any:
    """把 TOML 读取结果转为可 JSON 序列化的形态（日期时间转字符串）。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _load(subcommand: str, file: Path, json_output: bool) -> Any | None:
    """读取 TOML；失败时按统一错误码呈现并返回 ``None``。"""
    try:
        return data.read_toml(file)
    except FileNotFoundError as exc:
        report_error(
            "tomltool",
            subcommand,
            code="FILE_NOT_FOUND",
            message=str(exc),
            field="file",
            expected="存在的 TOML 文件",
            json_output=json_output,
        )
    except ValueError as exc:
        report_error(
            "tomltool",
            subcommand,
            code="INVALID_ARGUMENT",
            message=f"TOML 解析失败: {exc}",
            field="file",
            expected="语法正确的 TOML 文件",
            json_output=json_output,
        )
    return None


@fx.tool(
    "tomltool",
    subcommand="get",
    help="按点路径取值",
    param_help={
        "file": "TOML 文件路径",
        "key": "点分路径（如 project.name）",
    },
)
def get(file: Path, key: str, json_output: bool = False) -> None:
    """按点路径取值。"""
    if _load("get", file, json_output) is None:
        return
    try:
        result = data.get_toml_value(file, key)
    except KeyError:
        report_error(
            "tomltool",
            "get",
            code="NOT_FOUND",
            message=f"键不存在: {key}",
            field="key",
            expected="已存在的点分路径（如 project.name）",
            json_output=json_output,
        )
        return
    if json_output:
        emit_result("tomltool", "get", {"key": key, "value": _jsonable(result)})
        return
    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(result)


@fx.tool(
    "tomltool",
    subcommand="keys",
    help="列出顶层键",
    param_help={"file": "TOML 文件路径"},
)
def keys(file: Path, json_output: bool = False) -> None:
    """列出顶层键。"""
    if _load("keys", file, json_output) is None:
        return
    keys_list = data.keys_toml(file)
    if json_output:
        emit_result("tomltool", "keys", {"keys": keys_list})
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
def format(file: Path, json_output: bool = False) -> None:
    """转为 JSON 格式化输出。"""
    if _load("format", file, json_output) is None:
        return
    if json_output:
        emit_result("tomltool", "format", {"toml": _jsonable(data.read_toml(file))})
        return
    print(data.format_toml(file))


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
    data_obj = _load("convert", file, json_output=False)
    if data_obj is None:
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
def validate(file: Path, json_output: bool = False) -> None:
    """语法校验。"""
    if _load("validate", file, json_output) is None:
        return
    if json_output:
        emit_result("tomltool", "validate", {"file": str(file), "valid": True})
        return
    print(f"语法校验通过: {file}")
