"""urltool - URL 解析工具。

基于标准库 ``urllib.parse`` 提供 URL 解析、查询参数操作与基础 URL 提取。

示例
----
    fcmd urltool parse "https://example.com/path?key=value#frag"
    fcmd urltool query "https://example.com?p=1&q=2" q
    fcmd urltool addquery "https://example.com" foo bar
    fcmd urltool baseurl "https://example.com/path?key=value"
"""

from __future__ import annotations

import urllib.parse

import fcmd

__all__ = [
    "add_query_param",
    "get_base_url",
    "get_query_param",
    "parse_url",
]


# ============================================================================
# 公共函数
# ============================================================================


def parse_url(url: str) -> dict[str, str]:
    """解析 URL 为各组成部分。

    Parameters
    ----------
    url:
        待解析的 URL 字符串

    Returns
    -------
    dict[str, str]
        包含以下键的字典：
        - ``scheme``: 协议（如 ``https``）
        - ``netloc``: 网络位置（如 ``example.com:8080``）
        - ``path``: 路径（如 ``/api/v1``）
        - ``query``: 查询字符串（如 ``a=1&b=2``）
        - ``fragment``: 片段标识符（如 ``section``）

    Raises
    ------
    ValueError
        URL 为空时
    """
    if not url:
        raise ValueError("URL 不能为空")
    parsed = urllib.parse.urlsplit(url)
    return {
        "scheme": parsed.scheme,
        "netloc": parsed.netloc,
        "path": parsed.path,
        "query": parsed.query,
        "fragment": parsed.fragment,
    }


def get_query_param(url: str, key: str) -> str | None:
    """提取 URL 查询参数值。

    Parameters
    ----------
    url:
        URL 字符串
    key:
        查询参数名

    Returns
    -------
    str | None
        参数值（不存在返回 ``None``；多值取第一个）

    Raises
    ------
    ValueError
        URL 为空时
    """
    if not url:
        raise ValueError("URL 不能为空")
    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qs(parsed.query)
    values = params.get(key)
    if not values:
        return None
    return values[0]


def add_query_param(url: str, key: str, value: str) -> str:
    """向 URL 添加查询参数。

    保留原 URL 的其他组成部分；若参数已存在则追加新值（不覆盖）。

    Parameters
    ----------
    url:
        原 URL 字符串
    key:
        参数名
    value:
        参数值

    Returns
    -------
    str
        添加参数后的 URL

    Raises
    ------
    ValueError
        URL 为空时
    """
    if not url:
        raise ValueError("URL 不能为空")
    parsed = urllib.parse.urlsplit(url)
    # 解析现有查询参数为列表（保留多值）
    params = urllib.parse.parse_qsl(parsed.query)
    params.append((key, value))
    new_query = urllib.parse.urlencode(params)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def get_base_url(url: str) -> str:
    """提取基础 URL（仅保留 scheme 与 netloc）。

    Parameters
    ----------
    url:
        完整 URL 字符串

    Returns
    -------
    str
        ``scheme://netloc`` 格式的基础 URL

    Raises
    ------
    ValueError
        URL 为空或缺少 scheme/netloc 时
    """
    if not url:
        raise ValueError("URL 不能为空")
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme:
        raise ValueError(f"URL 缺少 scheme: {url!r}")
    if not parsed.netloc:
        raise ValueError(f"URL 缺少 netloc: {url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("urltool", subcommand="parse", help="解析 URL 各组成部分")
def parse_cmd(url: str) -> None:
    """解析 URL 并逐行打印各组成部分。

    Parameters
    ----------
    url:
        待解析的 URL 字符串
    """
    try:
        parts = parse_url(url)
    except ValueError as exc:
        print(str(exc))
        return
    for key, value in parts.items():
        print(f"{key}: {value}")


@fcmd.tool("urltool", subcommand="query", help="提取查询参数值")
def query_cmd(url: str, key: str) -> None:
    """提取 URL 查询参数值并打印。

    Parameters
    ----------
    url:
        URL 字符串
    key:
        查询参数名
    """
    try:
        value = get_query_param(url, key)
    except ValueError as exc:
        print(str(exc))
        return
    if value is None:
        print(f"参数不存在: {key}")
        return
    print(value)


@fcmd.tool("urltool", subcommand="addquery", help="添加查询参数")
def addquery_cmd(url: str, key: str, value: str) -> None:
    """向 URL 添加查询参数并打印结果。

    Parameters
    ----------
    url:
        原 URL 字符串
    key:
        参数名
    value:
        参数值
    """
    try:
        result = add_query_param(url, key, value)
    except ValueError as exc:
        print(str(exc))
        return
    print(result)


@fcmd.tool("urltool", subcommand="baseurl", help="提取基础 URL")
def baseurl_cmd(url: str) -> None:
    """提取 URL 的基础部分（``scheme://netloc``）并打印。

    Parameters
    ----------
    url:
        完整 URL 字符串
    """
    try:
        result = get_base_url(url)
    except ValueError as exc:
        print(str(exc))
        return
    print(result)


@fcmd.main("urltool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
