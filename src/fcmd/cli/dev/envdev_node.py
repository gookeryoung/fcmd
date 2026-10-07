"""envdev_node - 原生 Node.js 开发环境配置（npm/yarn/pnpm 镜像 + nvm）。

配置 npm / yarn / pnpm 的 registry 指向 npmmirror，
并可选安装 nvm（Node Version Manager）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import fcmd
from fcmd.cli._env_persist import persist_env
from fcmd.cli.dev.envdev_core import MirrorSpec, apply_mirror_config
from fcmd.models import run_command

__all__ = ["setup_node_env"]

_NPM_REGISTRY: str = "https://registry.npmmirror.com"

_PNPM_REGISTRY: str = "https://registry.npmmirror.com"

_YARN_REGISTRY: str = "https://registry.npmmirror.com"

_NVM_INSTALL_URL: str = "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh"

_NVMRC_CONTENT: str = """export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \\. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \\. "$NVM_DIR/bash_completion"
"""


# ============================================================================
# 镜像源配置
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-npm", help="配置 npm/yarn/pnpm 镜像源", hidden=True)
def _setup_npm_mirror() -> None:
    """配置 npm/yarn/pnpm 镜像源（持久化环境变量 + npm config set）。

    通过 :func:`persist_env` 持久化 ``NPM_CONFIG_REGISTRY`` /
    ``YARN_REGISTRY`` / ``PNPM_REGISTRY`` 环境变量，并调用各工具的
    ``config set registry`` 命令确保工具链级生效。
    """
    spec = MirrorSpec(
        env_vars={
            "NPM_CONFIG_REGISTRY": _NPM_REGISTRY,
            "YARN_REGISTRY": _YARN_REGISTRY,
            "PNPM_REGISTRY": _PNPM_REGISTRY,
        },
    )
    apply_mirror_config(spec, persist_fn=persist_env, label="npm")

    # 工具链级 config set
    if shutil.which("npm") is not None:
        run_command(["npm", "config", "set", "registry", _NPM_REGISTRY])
        run_command(["npm", "config", "set", "disturl", "https://npmmirror.com/mirrors/node"])

    if shutil.which("yarn") is not None:
        run_command(["yarn", "config", "set", "registry", _YARN_REGISTRY])

    if shutil.which("pnpm") is not None:
        run_command(["pnpm", "config", "set", "registry", _PNPM_REGISTRY])


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


def setup_node_env(install_nvm: bool = False) -> None:
    """一键配置原生 Node.js 开发环境。

    依次执行：配置 npm/yarn/pnpm 镜像源、（可选）安装 nvm。

    Parameters
    ----------
    install_nvm:
        是否同时安装 nvm（默认 False）
    """
    _setup_npm_mirror()
    if install_nvm:
        _install_nvm()
