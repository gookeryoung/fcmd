"""codetool 工具：Base64 / URL / Hex / ROT13 / HTML 编解码。

编解码逻辑在 :mod:`fcmd.infras.codec`，本模块只做参数适配与错误呈现。
"""

from __future__ import annotations

import binascii

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import codec


def _emit(subcommand: str, value: str, json_output: bool) -> None:
    """按输出模式呈现编解码结果。"""
    if json_output:
        emit_result("codetool", subcommand, {"result": value})
        return
    print(value)


@fx.tool(
    "codetool",
    subcommand="base64",
    help="Base64 编解码",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def codetool_base64(text: str, decode: bool = False, json_output: bool = False) -> None:
    """对文本执行 Base64 编码或解码。"""
    if not decode:
        _emit("base64", codec.encode_base64(text), json_output)
        return
    try:
        value = codec.decode_base64(text)
    except (binascii.Error, UnicodeDecodeError) as exc:
        report_error(
            "codetool",
            "base64",
            code="INVALID_ARGUMENT",
            message=f"Base64 解码失败: {exc}",
            hint="确认输入是合法的 Base64 文本（长度与字符集正确）",
            json_output=json_output,
        )
        return
    _emit("base64", value, json_output)


@fx.tool(
    "codetool",
    subcommand="url",
    help="URL 编解码",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def codetool_url(text: str, decode: bool = False, json_output: bool = False) -> None:
    """对文本执行 URL 编码或解码。"""
    _emit("url", codec.decode_url(text) if decode else codec.encode_url(text), json_output)


@fx.tool(
    "codetool",
    subcommand="hex",
    help="十六进制编解码",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def codetool_hex(text: str, decode: bool = False, json_output: bool = False) -> None:
    """对文本执行十六进制编码或解码。"""
    if not decode:
        _emit("hex", codec.encode_hex(text), json_output)
        return
    try:
        value = codec.decode_hex(text)
    except (ValueError, UnicodeDecodeError) as exc:
        report_error(
            "codetool",
            "hex",
            code="INVALID_ARGUMENT",
            message=f"Hex 解码失败: {exc}",
            hint="确认输入是合法的十六进制文本（偶数个 0-9a-f 字符）",
            json_output=json_output,
        )
        return
    _emit("hex", value, json_output)


@fx.tool(
    "codetool",
    subcommand="rot13",
    help="ROT13 转换",
    param_help={"text": "待转换的文本"},
)
def codetool_rot13(text: str, json_output: bool = False) -> None:
    """对文本执行 ROT13 转换。"""
    _emit("rot13", codec.rot13(text), json_output)


@fx.tool(
    "codetool",
    subcommand="html",
    help="HTML 转义与反转义",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时反转义，否则转义（默认 False）",
    },
)
def codetool_html(text: str, decode: bool = False, json_output: bool = False) -> None:
    """对文本执行 HTML 转义或反转义。"""
    _emit("html", codec.unescape_html(text) if decode else codec.escape_html(text), json_output)
