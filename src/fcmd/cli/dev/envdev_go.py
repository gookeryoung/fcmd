"""envdev_go - Go 开发环境配置（镜像源 + 工具链）。

配置 GOPROXY 镜像并可选安装 gvm（Go Version Manager）。
国内主流镜像：goproxy.cn、aliyun、ustc、goproxy.io。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import fcmd
from fcmd.cli._env_persist import persist_env
from fcmd.cli.dev.envdev_core import MirrorSpec, apply_mirror_config, mirror_supported, resolve_mirror
from fcmd.models import run_command

__all__ = ["setup_go_env"]

_GO_PROXY_MIRRORS: dict[str, str] = {
    "goproxy": "https://goproxy.cn,direct",
    "aliyun": "https://mirrors.aliyun.com/goproxy/,direct",
    "ustc": "https://mirrors.ustc.edu.cn/goproxy/,direct",
    "goproxy_io": "https://goproxy.io,direct",
}

# GOPROXY 探测地址（去除 ",direct" 回退后缀，仅探测镜像服务本身）
_GO_PROXY_PROBE_URLS: dict[str, str] = {name: url.split(",", 1)[0] for name, url in _GO_PROXY_MIRRORS.items()}

_GOPATH_DEFAULT: str = str(Path.home() / "go")

_GVM_INSTALL_URL: str = "https://raw.githubusercontent.com/moovweb/gvm/master/binscripts/gvm-installer"

_GO_GLOBAL_TOOLS: list[str] = [
    "golang.org/x/tools/gopls@latest",
    "github.com/go-delve/delve/cmd/dlv@latest",
]


# ============================================================================
# 镜像源配置
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-go", help="配置 Go 镜像源", hidden=True)
def _setup_go_mirror(mirror: str = "auto") -> None:
    """配置 Go 镜像源（持久化 GOPROXY / GOMODCACHE / GOPATH）。

    通过 :func:`persist_env` 持久化 ``GOPROXY`` / ``GOMODCACHE`` / ``GOPATH``
    三个环境变量。

    Parameters
    ----------
    mirror:
        镜像源名称：goproxy / aliyun / ustc / goproxy_io；``auto`` 时探测
        GOPROXY 服务各候选镜像并选用最快的可达镜像，全部不可达回退 goproxy
        （默认 ``auto``）
    """
    mirror = resolve_mirror("go", mirror, _GO_PROXY_PROBE_URLS, "goproxy")
    if not mirror_supported(mirror, _GO_PROXY_MIRRORS):
        print(f"未知 Go 镜像源: {mirror}")
        return

    proxy = _GO_PROXY_MIRRORS[mirror]

    spec = MirrorSpec(
        env_vars={
            "GOPROXY": proxy,
            "GOPATH": _GOPATH_DEFAULT,
        },
    )
    apply_mirror_config(spec, persist_fn=persist_env, label="Go")


# ============================================================================
# 工具链安装
# ============================================================================


@fcmd.tool("envdev", subcommand="install-gvm", help="安装 gvm (Go Version Manager)", hidden=True)
def _install_gvm() -> None:
    """安装 gvm（已安装时跳过）。

    Linux/macOS 通过官方脚本安装；Windows 提示手动安装。
    """
    if shutil.which("gvm") is not None or (Path.home() / ".gvm").is_dir():
        print("gvm 已安装，跳过")
        return

    if sys.platform == "win32":
        print("Windows 请参考 https://github.com/moovweb/gvm 手动安装")
        return

    print("安装 gvm...")
    run_command(["bash", "-c", f"curl -fsSL {_GVM_INSTALL_URL} | bash"])
    print("gvm 安装完成（重开终端后生效）")


@fcmd.tool("envdev", subcommand="install-go-tools", help="安装 Go 常用工具（gopls/dlv）", hidden=True)
def _install_go_global_tools() -> None:
    """安装 Go 常用开发工具（已安装时跳过）。"""
    if shutil.which("go") is None:
        print("go 未安装，跳过工具安装")
        return

    for tool in _GO_GLOBAL_TOOLS:
        print(f"安装 {tool}...")
        run_command(["go", "install", tool])


# ============================================================================
# 一键命令（由 envdev lang go 路由调用）
# ============================================================================


def setup_go_env(mirror: str = "auto", install_gvm: bool = False) -> None:
    """一键配置 Go 开发环境。

    依次执行：配置 GOPROXY 镜像源、（可选）安装 gvm。

    Parameters
    ----------
    mirror:
        镜像源名称：goproxy / aliyun / ustc / goproxy_io；``auto`` 按服务
        自动选优（默认 ``auto``）
    install_gvm:
        是否同时安装 gvm（默认 False）
    """
    _setup_go_mirror(mirror)
    if install_gvm:
        _install_gvm()
