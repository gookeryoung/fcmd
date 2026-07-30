"""codetool - 编解码工具。

对文本执行常见编解码：Base64、URL、十六进制、ROT13、HTML 转义。

示例
----
    fcmd codetool base64 "hello"                # 编码为 Base64
    fcmd codetool base64 "aGVsbG8=" --decode     # 解码 Base64
    fcmd codetool url "hello world"              # URL 编码
    fcmd codetool hex "hello"                    # 十六进制编码
    fcmd codetool rot13 "hello"                  # ROT13 转换
    fcmd codetool html "<a>"                     # HTML 转义
"""

from __future__ import annotations

import base64
import binascii
import codecs
import html
import urllib.parse

import fcmd

__all__ = [
    "decode_base64",
    "decode_hex",
    "decode_url",
    "encode_base64",
    "encode_hex",
    "encode_url",
    "escape_html",
    "rot13",
    "unescape_html",
]


# ============================================================================
# 公共函数 - Base64
# ============================================================================


def encode_base64(text: str) -> str:
    """将文本编码为 Base64 字符串。

    Parameters
    ----------
    text:
        待编码的文本

    Returns
    -------
    str
        Base64 编码结果
    """
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_base64(text: str) -> str:
    """将 Base64 字符串解码为文本。

    Parameters
    ----------
    text:
        待解码的 Base64 字符串

    Returns
    -------
    str
        解码后的文本

    Raises
    ------
    binascii.Error
        ``text`` 不是合法的 Base64 字符串时
    UnicodeDecodeError
        解码后的字节不是合法 UTF-8 时
    """
    return base64.b64decode(text, validate=True).decode("utf-8")


# ============================================================================
# 公共函数 - URL
# ============================================================================


def encode_url(text: str) -> str:
    """对文本进行 URL 编码（百分号编码）。

    Parameters
    ----------
    text:
        待编码的文本

    Returns
    -------
    str
        URL 编码结果
    """
    return urllib.parse.quote(text, safe="")


def decode_url(text: str) -> str:
    """对 URL 编码的文本进行解码。

    Parameters
    ----------
    text:
        待解码的 URL 编码文本

    Returns
    -------
    str
        解码后的文本
    """
    return urllib.parse.unquote(text)


# ============================================================================
# 公共函数 - 十六进制
# ============================================================================


def encode_hex(text: str) -> str:
    """将文本编码为十六进制字符串。

    Parameters
    ----------
    text:
        待编码的文本

    Returns
    -------
    str
        十六进制编码结果
    """
    return text.encode("utf-8").hex()


def decode_hex(text: str) -> str:
    """将十六进制字符串解码为文本。

    Parameters
    ----------
    text:
        待解码的十六进制字符串

    Returns
    -------
    str
        解码后的文本

    Raises
    ------
    ValueError
        ``text`` 不是合法的十六进制字符串时
    UnicodeDecodeError
        解码后的字节不是合法 UTF-8 时
    """
    return bytes.fromhex(text).decode("utf-8")


# ============================================================================
# 公共函数 - ROT13
# ============================================================================


def rot13(text: str) -> str:
    """对文本执行 ROT13 转换（自逆，编解码同一函数）。

    Parameters
    ----------
    text:
        待转换的文本

    Returns
    -------
    str
        ROT13 转换结果
    """
    return codecs.encode(text, "rot_13")


# ============================================================================
# 公共函数 - HTML
# ============================================================================


def escape_html(text: str) -> str:
    """对文本进行 HTML 转义（``<``/``>``/``&``/``"``/``'``）。

    Parameters
    ----------
    text:
        待转义的文本

    Returns
    -------
    str
        HTML 转义结果
    """
    return html.escape(text)


def unescape_html(text: str) -> str:
    """对 HTML 转义的文本进行反转义。

    Parameters
    ----------
    text:
        待反转义的 HTML 文本

    Returns
    -------
    str
        反转义后的文本
    """
    return html.unescape(text)


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("codetool", subcommand="base64", help="Base64 编解码")
def base64_cmd(text: str, decode: bool = False) -> None:
    """对文本执行 Base64 编码或解码。

    Parameters
    ----------
    text:
        待处理的文本
    decode:
        为 ``True`` 时解码，否则编码（默认 ``False``）
    """
    if decode:
        try:
            print(decode_base64(text))
        except (binascii.Error, UnicodeDecodeError) as exc:
            print(f"Base64 解码失败: {exc}")
    else:
        print(encode_base64(text))


@fcmd.tool("codetool", subcommand="url", help="URL 编解码")
def url_cmd(text: str, decode: bool = False) -> None:
    """对文本执行 URL 编码或解码。

    Parameters
    ----------
    text:
        待处理的文本
    decode:
        为 ``True`` 时解码，否则编码（默认 ``False``）
    """
    print(decode_url(text) if decode else encode_url(text))


@fcmd.tool("codetool", subcommand="hex", help="十六进制编解码")
def hex_cmd(text: str, decode: bool = False) -> None:
    """对文本执行十六进制编码或解码。

    Parameters
    ----------
    text:
        待处理的文本
    decode:
        为 ``True`` 时解码，否则编码（默认 ``False``）
    """
    if decode:
        try:
            print(decode_hex(text))
        except (ValueError, UnicodeDecodeError) as exc:
            print(f"Hex 解码失败: {exc}")
    else:
        print(encode_hex(text))


@fcmd.tool("codetool", subcommand="rot13", help="ROT13 转换")
def rot13_cmd(text: str) -> None:
    """对文本执行 ROT13 转换。

    Parameters
    ----------
    text:
        待转换的文本
    """
    print(rot13(text))


@fcmd.tool("codetool", subcommand="html", help="HTML 转义与反转义")
def html_cmd(text: str, decode: bool = False) -> None:
    """对文本执行 HTML 转义或反转义。

    Parameters
    ----------
    text:
        待处理的文本
    decode:
        为 ``True`` 时反转义，否则转义（默认 ``False``）
    """
    print(unescape_html(text) if decode else escape_html(text))


@fcmd.main("codetool")
def main() -> None:
    """``codetool`` 入口：等价于 ``fcmd codetool <args>``。"""


if __name__ == "__main__":
    main()
