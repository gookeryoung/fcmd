"""``mirrortool`` —— 镜像源查询与配置的独立一级入口（薄壳）。

镜像探测、按服务测速与自动应用的唯一实现在
:mod:`fcmd.tools.dev._envdev_mirrors`；本模块只注册一级工具，``fcmd
mirrortool`` 与 ``fcmd envdev mirror`` 共用同一份实现，不复制逻辑。

示例
----
    fcmd mirrortool                           # 探测教育网镜像站点可访问性与速度
    fcmd mirrortool --service pip             # pip 候选镜像测速并推荐最快项
    fcmd mirrortool --service npm --apply     # npm 测速并自动应用最快镜像
"""

from __future__ import annotations

from typing import Literal

import fcmd
from fcmd.tools.dev._envdev_mirrors import check_cernet_mirrors

__all__ = ["mirror"]


@fcmd.tool("mirrortool", help="镜像站点探测 / 指定服务镜像测速与自动选取")
def mirror(
    timeout: float = 3.0,
    source: Literal["auto", "builtin"] = "auto",
    service: Literal["", "pip", "conda", "rustup", "cargo", "go", "maven", "npm"] = "",
    apply: bool = False,
) -> int:
    """探测教育网镜像站点，或对指定服务做镜像测速并可选自动应用。

    参数语义与 ``fcmd envdev mirror`` 完全一致：``service`` 为空时探测教育
    网镜像站点（只读）；指定服务时按实测下载吞吐排序并推荐最快镜像，
    ``apply=True`` 时立即写入该服务配置。

    Parameters
    ----------
    timeout:
        单个站点/镜像的探测超时秒数（默认 ``3``）
    source:
        站点列表来源：``auto`` 动态拉取（失败回退内置） / ``builtin`` 内置列表
    service:
        指定服务名做镜像测速：``pip`` / ``conda`` / ``rustup`` / ``cargo`` /
        ``go`` / ``maven`` / ``npm``；空串表示探测教育网镜像站点
    apply:
        是否把测速推荐的最快镜像立即应用到对应服务（默认 ``False`` 只读）

    Returns
    -------
    int
        存在可达站点/镜像返回 ``0``，全部不可达返回 ``1``。
    """
    return check_cernet_mirrors(timeout=timeout, source=source, service=service, apply=apply)


@fcmd.main("mirrortool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
