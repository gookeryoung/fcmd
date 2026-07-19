"""dockercmd - Docker 镜像仓库登录工具.

登录 Docker 镜像仓库（默认腾讯云 ``ccr.ccs.tencentyun.com``）。
``username`` 为空时使用当前系统用户名，密码由 docker 交互式提示输入.

示例
----
    fcmd dockercmd login                                # 默认登录腾讯云镜像仓库
    fcmd dockercmd login --username admin               # 指定用户名
    fcmd dockercmd login --registry registry.example.com  # 自定义镜像仓库
"""

from __future__ import annotations

import getpass

import fcmd
from fcmd.models import run_command

__all__ = ["docker_login"]

# 默认腾讯云 Docker 镜像仓库
_DEFAULT_REGISTRY: str = "ccr.ccs.tencentyun.com"


@fcmd.tool("dockercmd", subcommand="login", help="登录 Docker 镜像仓库")
def docker_login(username: str = "", registry: str = _DEFAULT_REGISTRY) -> None:
    """登录 Docker 镜像仓库（默认腾讯云）。

    ``username`` 为空时使用当前系统用户名，密码由 docker 交互式提示输入.

    Parameters
    ----------
    username:
        Docker 用户名（为空时使用当前系统用户名）
    registry:
        镜像仓库地址（默认: ``ccr.ccs.tencentyun.com``）
    """
    user = username or getpass.getuser()
    result = run_command(["docker", "login", "--username", user, registry])
    if result.failed:
        print(f"登录失败: {registry} (用户: {user})")
        return
    print(f"已登录镜像仓库: {registry} (用户: {user})")
