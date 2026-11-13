"""codetool 工具：Base64 / URL / Hex / ROT13 / HTML 编解码。

编解码逻辑在 :mod:`fcmd.infras.codec`，本模块只做参数适配与错误呈现。
"""

from __future__ import annotations

import binascii

import fcmd as fx
from fcmd.console import print_error
from fcmd.infras import codec


@fx.tool(
    "codetool",
    subcommand="base64",
    help="Base64 编解码",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def codetool_base64(text: str, decode: bool = False) -> None:
    """对文本执行 Base64 编码或解码。"""
    if decode:
        try:
            print(codec.decode_base64(text))
        except (binascii.Error, UnicodeDecodeError) as exc:
            print_error(
                f"Base64 解码失败: {exc}",
                code="INVALID_ARGUMENT",
                hint="确认输入是合法的 Base64 文本（长度与字符集正确）",
            )
    else:
        print(codec.encode_base64(text))


@fx.tool(
    "codetool",
    subcommand="url",
    help="URL 编解码",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def codetool_url(text: str, decode: bool = False) -> None:
    """对文本执行 URL 编码或解码。"""
    print(codec.decode_url(text) if decode else codec.encode_url(text))


@fx.tool(
    "codetool",
    subcommand="hex",
    help="十六进制编解码",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def codetool_hex(text: str, decode: bool = False) -> None:
    """对文本执行十六进制编码或解码。"""
    if decode:
        try:
            print(codec.decode_hex(text))
        except (ValueError, UnicodeDecodeError) as exc:
            print_error(
                f"Hex 解码失败: {exc}",
                code="INVALID_ARGUMENT",
                hint="确认输入是合法的十六进制文本（偶数个 0-9a-f 字符）",
            )
    else:
        print(codec.encode_hex(text))


@fx.tool(
    "codetool",
    subcommand="rot13",
    help="ROT13 转换",
    param_help={"text": "待转换的文本"},
)
def codetool_rot13(text: str) -> None:
    """对文本执行 ROT13 转换。"""
    print(codec.rot13(text))


@fx.tool(
    "codetool",
    subcommand="html",
    help="HTML 转义与反转义",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时反转义，否则转义（默认 False）",
    },
)
def codetool_html(text: str, decode: bool = False) -> None:
    """对文本执行 HTML 转义或反转义。"""
    print(codec.unescape_html(text) if decode else codec.escape_html(text))
