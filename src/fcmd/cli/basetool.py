"""basetool - 编码解码工具。

基于标准库提供 Base64、URL、HTML、十六进制四类常见编解码。

示例
----
    fcmd basetool base64 "hello"                   # Base64 编码
    fcmd basetool base64 "aGVsbG8=" --decode       # Base64 解码
    fcmd basetool url "hello world"                # URL 编码
    fcmd basetool url "hello%20world" --decode     # URL 解码
    fcmd basetool html "<a>"                       # HTML 转义
    fcmd basetool html "&lt;a&gt;" --decode        # HTML 反转义
    fcmd basetool hex "hello"                      # 十六进制编码
    fcmd basetool hex "68656c6c6f" --decode        # 十六进制解码
"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import urllib.parse
from collections.abc import Callable

import fcmd

__all__ = [
    "base64_decode",
    "base64_encode",
    "hex_decode",
    "hex_encode",
    "html_decode",
    "html_encode",
    "url_decode",
    "url_encode",
]


# ============================================================================
# 公共函数
# ============================================================================


def base64_encode(text: str) -> str:
    """Base64 编码字符串。

    Parameters
    ----------
    text:
        待编码的字符串

    Returns
    -------
    str
        Base64 编码结果
    """
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def base64_decode(text: str) -> str:
    """Base64 解码字符串。

    Parameters
    ----------
    text:
        Base64 编码的字符串

    Returns
    -------
    str
        解码后的原字符串

    Raises
    ------
    ValueError
        输入非有效 Base64 时
    """
    try:
        return base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(f"Base64 解码失败: {exc}") from exc


def url_encode(text: str) -> str:
    """URL 编码字符串（百分号编码）。

    对所有非字母数字字符（除 ``-_.~``）进行百分号编码。

    Parameters
    ----------
    text:
        待编码的字符串

    Returns
    -------
    str
        URL 编码结果
    """
    return urllib.parse.quote(text, safe="")


def url_decode(text: str) -> str:
    """URL 解码字符串（百分号解码）。

    Parameters
    ----------
    text:
        URL 编码的字符串

    Returns
    -------
    str
        解码后的原字符串

    Raises
    ------
    ValueError
        输入含无效百分号编码时
    """
    try:
        return urllib.parse.unquote(text, errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"URL 解码失败: {exc}") from exc


def html_encode(text: str) -> str:
    """HTML 转义字符串。

    将 ``&``/``<``/``>``/``"``/``'`` 转换为 HTML 实体。

    Parameters
    ----------
    text:
        待转义的字符串

    Returns
    -------
    str
        HTML 转义结果
    """
    return html_lib.escape(text, quote=True)


def html_decode(text: str) -> str:
    """HTML 反转义字符串。

    将 HTML 实体（命名实体与数字实体）还原为原字符。

    Parameters
    ----------
    text:
        HTML 转义的字符串

    Returns
    -------
    str
        反转义后的原字符串
    """
    return html_lib.unescape(text)


def hex_encode(text: str) -> str:
    """十六进制编码字符串。

    Parameters
    ----------
    text:
        待编码的字符串

    Returns
    -------
    str
        小写十六进制编码结果
    """
    return text.encode("utf-8").hex()


def hex_decode(text: str) -> str:
    """十六进制解码字符串。

    Parameters
    ----------
    text:
        十六进制编码的字符串

    Returns
    -------
    str
        解码后的原字符串

    Raises
    ------
    ValueError
        输入非有效十六进制或长度为奇数时
    """
    try:
        return bytes.fromhex(text).decode("utf-8")
    except ValueError as exc:
        raise ValueError(f"十六进制解码失败: {exc}") from exc


# ============================================================================
# CLI 子命令
# ============================================================================


def _codec_cmd(
    text: str,
    decode: bool,
    encode_fn: Callable[[str], str],
    decode_fn: Callable[[str], str],
) -> None:
    """编解码子命令通用模板：尝试解码或编码，捕获 ValueError 并打印结果。

    Parameters
    ----------
    text:
        待处理的字符串
    decode:
        解码模式为 ``True`` 时调用 *decode_fn*，否则调用 *encode_fn*
    encode_fn:
        编码函数
    decode_fn:
        解码函数（须将底层异常包装为 ``ValueError``）
    """
    try:
        result = decode_fn(text) if decode else encode_fn(text)
    except ValueError as exc:
        print(str(exc))
        return
    print(result)


@fcmd.tool("basetool", subcommand="base64", help="Base64 编解码")
def base64_cmd(text: str, decode: bool = False) -> None:
    """Base64 编码或解码字符串。"""
    _codec_cmd(text, decode, base64_encode, base64_decode)


@fcmd.tool("basetool", subcommand="url", help="URL 编解码")
def url_cmd(text: str, decode: bool = False) -> None:
    """URL 编码或解码字符串。"""
    _codec_cmd(text, decode, url_encode, url_decode)


@fcmd.tool("basetool", subcommand="html", help="HTML 转义与反转义")
def html_cmd(text: str, decode: bool = False) -> None:
    """HTML 转义或反转义字符串。"""
    print(html_decode(text) if decode else html_encode(text))


@fcmd.tool("basetool", subcommand="hex", help="十六进制编解码")
def hex_cmd(text: str, decode: bool = False) -> None:
    """十六进制编码或解码字符串。"""
    _codec_cmd(text, decode, hex_encode, hex_decode)


@fcmd.main("basetool")
def main() -> None:
    pass
