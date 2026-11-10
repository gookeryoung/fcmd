"""网络工具 DSL 动作：nettool / iptool / websave / urlcheck。

业务实现统一在 :mod:`fcmd.tools.net.netbase`；本模块只保留 ``@action``
声明与参数转接，避免 DSL 层出现第二套网络实现。TOML 声明通过
``action = "<名>"`` 直接引用这些名字。通用探测能力（``check_url`` /
``check_urls`` 等）继续来自 :mod:`fcmd.core.shared.net`。
"""

from __future__ import annotations

from urllib.error import HTTPError, URLError

from fcmd.core.shared.net import _DEFAULT_CHECK_TIMEOUT
from fcmd.dsl.actions import action
from fcmd.tools.net import netbase

# ============================================================================
# nettool DSL 动作
# ============================================================================


@action(
    "nettool_get",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 30）",
    },
    errors=(HTTPError, URLError),
    error_prefix="请求失败: ",
)
def nettool_get(url: str, timeout: int = netbase._DEFAULT_TIMEOUT) -> None:
    """发送 HTTP GET 请求并打印响应体。"""
    netbase.nettool_get(url, timeout)


@action(
    "nettool_post",
    param_help={
        "url": "目标 URL",
        "data": "请求体（默认空串）",
        "timeout": "超时秒数（默认 30）",
    },
    errors=(HTTPError, URLError),
    error_prefix="请求失败: ",
)
def nettool_post(url: str, data: str = "", timeout: int = netbase._DEFAULT_TIMEOUT) -> None:
    """发送 HTTP POST 请求并打印响应体。"""
    netbase.nettool_post(url, data, timeout)


@action(
    "nettool_head",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 30）",
    },
    errors=(HTTPError, URLError),
    error_prefix="请求失败: ",
)
def nettool_head(url: str, timeout: int = netbase._DEFAULT_TIMEOUT) -> None:
    """发送 HTTP HEAD 请求并打印响应头。"""
    netbase.nettool_head(url, timeout)


# ============================================================================
# iptool DSL 动作
# ============================================================================


@action("iptool_parse", param_help={"ip": "IP 地址字符串（IPv4 或 IPv6）"}, errors=(ValueError,))
def iptool_parse(ip: str) -> None:
    """解析 IP 地址并逐行打印属性。"""
    netbase.iptool_parse(ip)


@action(
    "iptool_subnet",
    param_help={
        "ip": "网络地址字符串",
        "prefix": "前缀长度",
    },
    errors=(ValueError,),
)
def iptool_subnet(ip: str, prefix: int) -> None:
    """计算子网信息并逐行打印。"""
    netbase.iptool_subnet(ip, prefix)


@action("iptool_validate", param_help={"ip": "待校验的 IP 地址字符串"})
def iptool_validate(ip: str) -> None:
    """校验 IP 地址格式是否有效。"""
    netbase.iptool_validate(ip)


# ============================================================================
# websave DSL 动作
# ============================================================================


@action(
    "websave",
    param_help={
        "url": "主页 URL",
        "output_dir": "输出目录（默认 saved_website）",
        "depth": "爬取深度（默认 2）",
        "timeout": "请求超时秒数（默认 30）",
    },
    errors=(HTTPError, URLError),
)
def websave(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 2,
    timeout: int = 30,
) -> None:
    """保存网页内容（主页及相关子页面，含图片/CSS/JS）。"""
    netbase.save_website(url, output_dir, depth, timeout)


# ============================================================================
# urlcheck DSL 动作
# ============================================================================


@action(
    "urlcheck_u",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 5）",
    },
)
def urlcheck_u(url: str, timeout: float = _DEFAULT_CHECK_TIMEOUT) -> None:
    """检测单个 URL 的可访问性并打印延迟。"""
    netbase.urlcheck_u(url, timeout)


@action(
    "urlcheck_r",
    param_help={
        "urls": "目标 URL 列表",
        "timeout": "单个 URL 的超时秒数（默认 5）",
    },
)
def urlcheck_r(urls: list[str], timeout: float = _DEFAULT_CHECK_TIMEOUT) -> None:
    """并发探测多个 URL，按访问速度排序打印。"""
    netbase.urlcheck_r(urls, timeout)
