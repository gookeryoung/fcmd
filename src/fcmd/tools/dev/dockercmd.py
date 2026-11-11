"""dockercmd - Docker 镜像仓库登录工具。

username 为空时按 ``USERNAME`` / ``LOGNAME`` / ``USER`` / ``LNAME`` 环境变量
回退（等价 ``getpass.getuser()`` 的探测顺序）。登录过程不捕获
stdout/stderr，docker 的密码提示保持终端透传。
"""

from __future__ import annotations

import os
import subprocess

import fcmd as fx

# 用户名环境变量回退链（Windows 命中 USERNAME，POSIX 依次 LOGNAME/USER/LNAME）
_USERNAME_ENV_CHAIN: tuple[str, ...] = ("USERNAME", "LOGNAME", "USER", "LNAME")


def _env_username() -> str:
    """按回退链返回第一个非空用户名环境变量值，全空返回空串。"""
    for name in _USERNAME_ENV_CHAIN:
        value = os.environ.get(name)
        if value:
            return value
    return ""


@fx.tool(
    "dockercmd",
    subcommand="login",
    help="登录 Docker 镜像仓库",
    description="Docker 镜像仓库登录工具",
)
def login(username: str = "", registry: str = "ccr.ccs.tencentyun.com") -> None:
    """登录 Docker 镜像仓库（默认腾讯云）。

    Parameters
    ----------
    username:
        Docker 用户名（缺省回退系统用户名环境变量链）
    registry:
        镜像仓库地址（默认: ccr.ccs.tencentyun.com）
    """
    resolved_username = username or _env_username()
    cmd = ["docker", "login", "--username", resolved_username, registry]
    # 不捕获输出：密码提示需要可见可输入（tty 透传）
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"docker login 失败（退出码 {result.returncode}）")
    print(f"已登录镜像仓库: {registry} (用户: {resolved_username})")


@fx.main("dockercmd")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
