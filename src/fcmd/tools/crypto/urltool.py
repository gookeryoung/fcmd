"""urltool 工具：URL 解析 / 查询参数 / 基础 URL 提取。

URL 逻辑在 :mod:`fcmd.infras.codec`，本模块只做参数适配与错误呈现。
"""

from __future__ import annotations

import fcmd as fx
from fcmd.console import print_error
from fcmd.infras import codec


@fx.tool(
    "urltool",
    subcommand="parse",
    help="解析 URL 各组成部分",
    param_help={"url": "待解析的 URL 字符串"},
)
def urltool_parse(url: str) -> None:
    """解析 URL 并逐行打印各组成部分。"""
    try:
        parts = codec.parse_url(url)
    except ValueError as error:
        print_error(str(error), code="INVALID_ARGUMENT", hint="确认 URL 含 scheme://host 且格式合法")
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
def urltool_query(url: str, key: str) -> None:
    """提取 URL 查询参数值并打印。"""
    try:
        value = codec.get_query_param(url, key)
    except ValueError as error:
        print_error(str(error), code="INVALID_ARGUMENT", hint="确认 URL 含 scheme://host 且格式合法")
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
def urltool_addquery(url: str, key: str, value: str) -> None:
    """向 URL 添加查询参数并打印结果。"""
    try:
        result = codec.add_query_param(url, key, value)
    except ValueError as error:
        print_error(str(error), code="INVALID_ARGUMENT", hint="确认 URL 含 scheme://host 且格式合法")
        return
    print(result)


@fx.tool(
    "urltool",
    subcommand="baseurl",
    help="提取基础 URL",
    param_help={"url": "完整 URL 字符串"},
)
def urltool_baseurl(url: str) -> None:
    """提取 URL 的基础部分（scheme://netloc）并打印。"""
    try:
        result = codec.get_base_url(url)
    except ValueError as error:
        print_error(str(error), code="INVALID_ARGUMENT", hint="确认 URL 含 scheme://host 且格式合法")
        return
    print(result)
