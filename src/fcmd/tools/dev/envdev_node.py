"""envdev_node - 原生 Node.js 开发环境配置（npm/yarn/pnpm 镜像 + nvm）。

配置 npm / yarn / pnpm 的 registry 指向所选国内 npm 镜像（默认 npmmirror），
并可选安装 nvm（Node Version Manager）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import fcmd
from fcmd.cli._env_persist import persist_env
from fcmd.core.models import run_command
from fcmd.tools.dev.envdev_core import MirrorSpec, apply_mirror_config, mirror_supported, resolve_mirror

__all__ = ["setup_node_env"]

# 国内 npm registry 候选（镜像名 -> registry 根地址）。
_NPM_REGISTRY_MIRRORS: dict[str, str] = {
    "npmmirror": "https://registry.npmmirror.com",
    "tencent": "https://mirrors.cloud.tencent.com/npm/",
    "huaweicloud": "https://mirrors.huaweicloud.com/repository/npm/",
}

# 探测地址：拉取固定热门包的 metadata（lodash，约 2 MB）。
# registry 根路径常返回 404，/-/ping 体积过小无法反映下载吞吐与限速情况。
_NPM_PROBE_URLS: dict[str, str] = {name: f"{url.rstrip('/')}/lodash" for name, url in _NPM_REGISTRY_MIRRORS.items()}

_NPM_DEFAULT: str = "npmmirror"

_NVM_INSTALL_URL: str = "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh"

_NVMRC_CONTENT: str = """export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \\. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \\. "$NVM_DIR/bash_completion"
"""


# ============================================================================
# 镜像源配置
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-npm", help="配置 npm/yarn/pnpm 镜像源", hidden=True)
def _setup_npm_mirror(mirror: str = "auto") -> None:
    """配置 npm/yarn/pnpm 镜像源（持久化环境变量 + npm config set）。

    通过 :func:`persist_env` 持久化 ``NPM_CONFIG_REGISTRY`` /
    ``YARN_REGISTRY`` / ``PNPM_REGISTRY`` 环境变量，并调用各工具的
    ``config set registry`` 命令确保工具链级生效。

    Parameters
    ----------
    mirror:
        镜像源名称：npmmirror/tencent/huaweicloud；``auto`` 时探测各 registry
        的 ``/-/ping`` 并选用最快的可达镜像，全部不可达回退 npmmirror
        （默认 ``auto``）
    """
    mirror = resolve_mirror("npm", mirror, _NPM_PROBE_URLS, _NPM_DEFAULT)
    if not mirror_supported(mirror, _NPM_REGISTRY_MIRRORS):
        print(f"未知 npm 镜像源: {mirror}")
        return

    registry = _NPM_REGISTRY_MIRRORS[mirror]
    spec = MirrorSpec(
        env_vars={
            "NPM_CONFIG_REGISTRY": registry,
            "YARN_REGISTRY": registry,
            "PNPM_REGISTRY": registry,
        },
    )
    apply_mirror_config(spec, persist_fn=persist_env, label="npm")

    # 工具链级 config set
    if shutil.which("npm") is not None:
        run_command(["npm", "config", "set", "registry", registry])
        if mirror == "npmmirror":
            run_command(["npm", "config", "set", "disturl", "https://npmmirror.com/mirrors/node"])

    if shutil.which("yarn") is not None:
        run_command(["yarn", "config", "set", "registry", registry])

    if shutil.which("pnpm") is not None:
        run_command(["pnpm", "config", "set", "registry", registry])


# ============================================================================
# 工具链安装
# ============================================================================


@fcmd.tool("envdev", subcommand="install-nvm", help="安装 nvm (Node Version Manager)", hidden=True)
def _install_nvm() -> None:
    """安装 nvm（已安装时跳过）。

    Linux/macOS 通过官方脚本安装；Windows 提示手动安装。
    """
    if (Path.home() / ".nvm").is_dir() or shutil.which("nvm") is not None:
        print("nvm 已安装，跳过")
        return

    if sys.platform == "win32":
        print("Windows 请参考 https://github.com/coreybutler/nvm-windows 手动安装")
        return

    print("安装 nvm...")
    run_command(["bash", "-c", f"curl -fsSL {_NVM_INSTALL_URL} | bash"])
    print("nvm 安装完成（重开终端后生效）")


# ============================================================================
# 一键命令（由 envdev lang node 路由调用）
# ============================================================================


def setup_node_env(mirror: str = "auto", install_nvm: bool = False) -> None:
    """一键配置原生 Node.js 开发环境。

    依次执行：配置 npm/yarn/pnpm 镜像源、（可选）安装 nvm。

    Parameters
    ----------
    mirror:
        镜像源名称：npmmirror/tencent/huaweicloud；``auto`` 按 npm 服务自动选优
        （默认 ``auto``）
    install_nvm:
        是否同时安装 nvm（默认 False）
    """
    _setup_npm_mirror(mirror)
    if install_nvm:
        _install_nvm()
