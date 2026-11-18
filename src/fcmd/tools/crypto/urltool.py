"""urltool 工具：URL 解析 / 查询参数 / 基础 URL 提取。

URL 逻辑在 :mod:`fcmd.infras.codec`，本模块只做参数适配与错误呈现。
"""

from __future__ import annotations

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import codec

_HINT = "确认 URL 含 scheme://host 且格式合法"


@fx.tool(
    "urltool",
    subcommand="parse",
    help="解析 URL 各组成部分",
    param_help={"url": "待解析的 URL 字符串"},
)
def urltool_parse(url: str, json_output: bool = False) -> None:
    """解析 URL 并逐行打印各组成部分。"""
    try:
        parts = codec.parse_url(url)
    except ValueError as error:
        report_error(
            "urltool", "parse", code="INVALID_ARGUMENT", message=str(error), hint=_HINT, json_output=json_output
        )
        return
    if json_output:
        emit_result("urltool", "parse", parts)
        return
    for key, value in parts.items():
        print(f"{key}: {value}")


@fx.tool(
    "urltool",
    subcommand="query",
    help="提取查询参数值",
    param_help={
        "url": "URL 字符串",
        "key": "查询参数名",
    },
)
def urltool_query(url: str, key: str, json_output: bool = False) -> None:
    """提取 URL 查询参数值并打印。"""
    try:
        value = codec.get_query_param(url, key)
    except ValueError as error:
        report_error(
            "urltool", "query", code="INVALID_ARGUMENT", message=str(error), hint=_HINT, json_output=json_output
        )
        return
    if json_output:
        # 参数不存在时仍视为成功：``result`` 为 null，便于调用方区分「未找到」与「URL 非法」。
        emit_result("urltool", "query", {"result": value})
        return
    if value is None:
        print(f"参数不存在: {key}")
        return
    print(value)


@fx.tool(
    "urltool",
    subcommand="addquery",
    help="添加查询参数",
    param_help={
        "url": "原 URL 字符串",
        "key": "参数名",
        "value": "参数值",
    },
)
def urltool_addquery(url: str, key: str, value: str, json_output: bool = False) -> None:
    """向 URL 添加查询参数并打印结果。"""
    try:
        result = codec.add_query_param(url, key, value)
    except ValueError as error:
        report_error(
            "urltool", "addquery", code="INVALID_ARGUMENT", message=str(error), hint=_HINT, json_output=json_output
        )
        return
    if json_output:
        emit_result("urltool", "addquery", {"result": result})
        return
    print(result)


@fx.tool(
    "urltool",
    subcommand="baseurl",
    help="提取基础 URL",
    param_help={"url": "完整 URL 字符串"},
)
def urltool_baseurl(url: str, json_output: bool = False) -> None:
    """提取 URL 的基础部分（scheme://netloc）并打印。"""
    try:
        result = codec.get_base_url(url)
    except ValueError as error:
        report_error(
            "urltool", "baseurl", code="INVALID_ARGUMENT", message=str(error), hint=_HINT, json_output=json_output
        )
        return
    if json_output:
        emit_result("urltool", "baseurl", {"result": result})
        return
    print(result)
