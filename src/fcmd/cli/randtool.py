"""randtool - 随机生成工具。

基于 ``secrets`` 模块提供密码学安全的随机生成：强密码、随机整数、
自定义字符集字符串、随机字节。

示例
----
    fcmd randtool password                       # 生成 16 位强密码
    fcmd randtool password --length 24           # 生成 24 位强密码
    fcmd randtool password --no-symbols          # 不含符号
    fcmd randtool number 1 100                   # 生成 [1, 100] 随机整数
    fcmd randtool string --length 12 --chars ABCDEFG  # 自定义字符集
    fcmd randtool bytes 16                       # 生成 16 字节随机 hex
"""

from __future__ import annotations

import base64
import secrets
import string

import fcmd

__all__ = [
    "generate_bytes",
    "generate_number",
    "generate_password",
    "generate_string",
]

# 默认密码字符集
_LOWERCASE = string.ascii_lowercase
_UPPERCASE = string.ascii_uppercase
_DIGITS = string.digits
# 安全符号集：排除易混淆字符（引号/反斜杠/反引号）
_SYMBOLS = "!@#$%^&*()-_=+[]{}<>?"

# 默认密码长度
_DEFAULT_PASSWORD_LENGTH = 16
# 默认随机字符串长度
_DEFAULT_STRING_LENGTH = 16


# ============================================================================
# 公共函数
# ============================================================================


def generate_password(
    length: int = _DEFAULT_PASSWORD_LENGTH,
    symbols: bool = True,
) -> str:
    """生成强密码。

    使用 ``secrets.choice`` 保证密码学安全随机性。密码至少包含：
    一个小写字母、一个大写字母、一个数字；若 ``symbols=True`` 还含一个符号。

    Parameters
    ----------
    length:
        密码长度（默认 ``16``，必须 >= 4）
    symbols:
        是否包含符号字符（默认 ``True``）

    Returns
    -------
    str
        随机密码

    Raises
    ------
    ValueError
        ``length`` 不足 4 时
    """
    if length < 4:
        raise ValueError(f"length 必须大于等于 4，当前: {length}")

    pools: list[str] = [_LOWERCASE, _UPPERCASE, _DIGITS]
    if symbols:
        pools.append(_SYMBOLS)

    full_pool = "".join(pools)

    # 先确保每类至少 1 个字符
    result: list[str] = [secrets.choice(pool) for pool in pools]
    # 剩余位置从全集中随机选取
    result.extend(secrets.choice(full_pool) for _ in range(length - len(result)))

    # 打乱顺序避免前 N 位固定为各类首字符
    secrets.SystemRandom().shuffle(result)
    return "".join(result)


def generate_number(min_val: int, max_val: int) -> int:
    """生成 ``[min_val, max_val]`` 闭区间内的随机整数。

    使用 ``secrets.randbelow`` 保证密码学安全随机性。

    Parameters
    ----------
    min_val:
        最小值（含）
    max_val:
        最大值（含）

    Returns
    -------
    int
        随机整数

    Raises
    ------
    ValueError
        ``min_val > max_val`` 时
    """
    if min_val > max_val:
        raise ValueError(f"min_val({min_val}) 不能大于 max_val({max_val})")
    # randbelow(n) 返回 [0, n)，故 +1 使 max_val 也包含
    return min_val + secrets.randbelow(max_val - min_val + 1)


def generate_string(
    length: int = _DEFAULT_STRING_LENGTH,
    chars: str = "",
) -> str:
    """从自定义字符集生成随机字符串。

    使用 ``secrets.choice`` 保证密码学安全随机性。

    Parameters
    ----------
    length:
        字符串长度（默认 ``16``，必须大于 0）
    chars:
        字符集（默认空串，使用字母+数字）；空串等价于
        ``string.ascii_letters + string.digits``

    Returns
    -------
    str
        随机字符串

    Raises
    ------
    ValueError
        ``length`` 小于等于 0 时
    """
    if length <= 0:
        raise ValueError(f"length 必须大于 0，当前: {length}")
    pool = chars if chars else (string.ascii_letters + string.digits)
    return "".join(secrets.choice(pool) for _ in range(length))


def generate_bytes(length: int, encoding: str = "hex") -> str:
    """生成随机字节并编码为字符串。

    使用 ``secrets.token_bytes`` 保证密码学安全随机性。

    Parameters
    ----------
    length:
        字节长度（必须大于 0）
    encoding:
        输出编码（``hex`` 或 ``base64``，默认 ``hex``）

    Returns
    -------
    str
        编码后的字符串（``hex`` 为小写十六进制，``base64`` 为标准 Base64）

    Raises
    ------
    ValueError
        ``length`` 小于等于 0 或 ``encoding`` 不支持时
    """
    if length <= 0:
        raise ValueError(f"length 必须大于 0，当前: {length}")
    raw = secrets.token_bytes(length)
    if encoding == "hex":
        return raw.hex()
    if encoding == "base64":
        return base64.b64encode(raw).decode("ascii")
    raise ValueError(f"不支持的编码: {encoding}，支持: hex, base64")


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("randtool", subcommand="password", help="生成强密码")
def password_cmd(
    length: int = _DEFAULT_PASSWORD_LENGTH,
    symbols: bool = True,
) -> None:
    """生成强密码。

    Parameters
    ----------
    length:
        密码长度（默认 ``16``，必须 >= 4）
    symbols:
        是否包含符号字符（默认 ``True``）
    """
    try:
        print(generate_password(length, symbols))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("randtool", subcommand="number", help="生成随机整数")
def number_cmd(min_val: int, max_val: int) -> None:
    """生成 ``[min_val, max_val]`` 闭区间内的随机整数。

    用法：``fcmd randtool number <min> <max>``

    Parameters
    ----------
    min_val:
        最小值（含）
    max_val:
        最大值（含）
    """
    try:
        print(generate_number(min_val, max_val))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("randtool", subcommand="string", help="生成自定义字符集随机字符串")
def string_cmd(
    length: int = _DEFAULT_STRING_LENGTH,
    chars: str = "",
) -> None:
    """从自定义字符集生成随机字符串。

    Parameters
    ----------
    length:
        字符串长度（默认 ``16``，必须大于 0）
    chars:
        字符集（默认空串，使用字母+数字）
    """
    try:
        print(generate_string(length, chars))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("randtool", subcommand="bytes", help="生成随机字节")
def bytes_cmd(length: int, encoding: str = "hex") -> None:
    """生成随机字节并编码输出。

    Parameters
    ----------
    length:
        字节长度（必须大于 0）
    encoding:
        输出编码（``hex`` 或 ``base64``，默认 ``hex``）
    """
    try:
        print(generate_bytes(length, encoding))
    except ValueError as exc:
        print(str(exc))


def main() -> None:
    """``randtool`` 入口：等价于 ``fcmd randtool <args>``。"""
    from fcmd.cli._common import run_tool_main

    run_tool_main("randtool")


if __name__ == "__main__":
    main()
