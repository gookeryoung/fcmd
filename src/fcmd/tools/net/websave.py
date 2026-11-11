"""websave - 保存网页内容（主页及同源子页面，含图片/CSS/JS）。

示例
----
    fcmd websave https://example.com
    fcmd websave https://example.com --output-dir site --depth 3
"""

from __future__ import annotations

from urllib.error import HTTPError, URLError

import fcmd as fx
from fcmd.infras import net as netinfra

__all__ = ["websave"]


@fx.tool(
    "websave",
    subcommand=None,
    help="保存网页内容（主页及相关子页面，含图片/CSS/JS）",
    param_help={
        "url": "主页 URL",
        "output_dir": "输出目录（默认 saved_website）",
        "depth": "爬取深度（默认 2）",
        "timeout": "请求超时秒数（默认 30）",
    },
)
def websave(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 2,
    timeout: int = 30,
) -> None:
    """保存网页内容（主页及相关子页面，含图片/CSS/JS）。"""
    try:
        result = netinfra.save_website(url, output_dir, depth, timeout)
    except (HTTPError, URLError) as exc:
        print(f"错误: {exc}")
        return
    for failed_url, message in result.warnings:
        print(f"错误: [websave] 警告: 无法获取 {failed_url}: {message}")
    print(f"[websave] 成功: 网站内容已保存到 {result.output_path.resolve()}")


@fx.main("websave")
def main() -> None:
    pass  # pragma: no cover - @fx.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
