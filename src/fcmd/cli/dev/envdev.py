"""envdev - 开发环境镜像源配置工具。

子命令整合为五组操作入口：

- ``lang <语言>``：语言类一键配置（python/js/rust/go/java/node），镜像默认 ``auto``
  按服务（pip/conda/rustup/cargo/go/maven）独立探测选优
- ``app <应用>``：应用/系统类一键配置（linux-mirror/qt-libs/fonts/docker/docker-mirror/openssh/remote）
- ``mirror``：探测教育网镜像站点（可访问性 + 访问速度排名）
- ``check``：检测开发环境配置状态（工具链 + 镜像源，只读）
- ``all``：一键配置所有环境

细粒度步骤命令（setup-* / install-*）为隐藏子命令，可单独调用也可由
分组命令与一键命令编排。

本模块是门面层：分组路由、语言级一键命令、Linux 专用命令、远程桌面命令
在此定义；公共辅助提取自 :mod:`fcmd.cli.dev.envdev_core`。

示例
----
    fcmd envdev lang python                      # 自动探测并选用最快的 Python 镜像源
    fcmd envdev lang python --mirror tsinghua    # 指定镜像源配置 Python 环境（pip/uv + Conda）
    fcmd envdev lang js                          # 一键配置 JavaScript 环境（Bun）
    fcmd envdev lang rust --mirror tsinghua nightly  # 一键配置 Rust 环境（镜像源 + 工具链）
    fcmd envdev mirror                           # 探测教育网镜像站点可访问性与速度
    fcmd envdev app remote                       # 一键配置 Linux 远程桌面（xrdp + Xfce）
    fcmd envdev check                            # 检测开发环境配置状态
    fcmd envdev all                              # 一键配置所有环境
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Literal

import fcmd
from fcmd.cli._env_persist import persist_env
from fcmd.cli.dev.envdev_core import (
    MirrorSpec,
    apply_mirror_config,
    mirror_supported,
    pip_config_path,
    resolve_mirror,
)
from fcmd.cli.dev.envdev_go import setup_go_env
from fcmd.cli.dev.envdev_java import setup_java_env
from fcmd.cli.dev.envdev_node import setup_node_env
from fcmd.dsl.actions.net import check_urls
from fcmd.models import run_command

logger = logging.getLogger(__name__)

__all__ = [
    "check_cernet_mirrors",
    "check_env",
    "fetch_mirrorz_sites",
    "install_linux_docker",
    "install_linux_fonts",
    "install_linux_openssh",
    "install_linux_qt_libs",
    "setup_all_env",
    "setup_app_env",
    "setup_conda_mirror",
    "setup_js_env",
    "setup_lang_env",
    "setup_linux_remote",
    "setup_linux_system_mirror",
    "setup_python_env",
    "setup_python_mirror",
    "setup_rust_env",
]

# ============================================================================
# 配置常量
# ============================================================================

_PIP_INDEX_URLS: dict[str, str] = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "huaweicloud": "https://mirrors.huaweicloud.com/repository/pypi/simple/",
    "ustc": "https://pypi.mirrors.ustc.edu.cn/simple/",
    "zju": "https://mirrors.zju.edu.cn/pypi/simple/",
}

_PIP_TRUSTED_HOSTS: dict[str, str] = {
    "tsinghua": "pypi.tuna.tsinghua.edu.cn",
    "aliyun": "mirrors.aliyun.com",
    "huaweicloud": "mirrors.huaweicloud.com",
    "ustc": "pypi.mirrors.ustc.edu.cn",
    "zju": "mirrors.zju.edu.cn",
}

_UV_PYTHON_INSTALL_MIRROR: str = "https://registry.npmmirror.com/-/binary/python-build-standalone"

_CONDA_MIRROR_URLS: dict[str, list[str]] = {
    "tsinghua": [
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/msys2/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/pro/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/bioconda/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/menpo/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/pytorch/",
    ],
    "ustc": [
        "https://mirrors.ustc.edu.cn/anaconda/pkgs/main/",
        "https://mirrors.ustc.edu.cn/anaconda/pkgs/free/",
        "https://mirrors.ustc.edu.cn/anaconda/pkgs/r/",
        "https://mirrors.ustc.edu.cn/anaconda/pkgs/msys2/",
        "https://mirrors.ustc.edu.cn/anaconda/pkgs/pro/",
        "https://mirrors.ustc.edu.cn/anaconda/pkgs/dev/",
        "https://mirrors.ustc.edu.cn/anaconda/cloud/conda-forge/",
        "https://mirrors.ustc.edu.cn/anaconda/cloud/bioconda/",
        "https://mirrors.ustc.edu.cn/anaconda/cloud/menpo/",
        "https://mirrors.ustc.edu.cn/anaconda/cloud/pytorch/",
    ],
    "bfsu": [
        "https://mirrors.bfsu.edu.cn/anaconda/pkgs/main/",
        "https://mirrors.bfsu.edu.cn/anaconda/pkgs/free/",
        "https://mirrors.bfsu.edu.cn/anaconda/pkgs/r/",
        "https://mirrors.bfsu.edu.cn/anaconda/pkgs/msys2/",
        "https://mirrors.bfsu.edu.cn/anaconda/pkgs/pro/",
        "https://mirrors.bfsu.edu.cn/anaconda/pkgs/dev/",
        "https://mirrors.bfsu.edu.cn/anaconda/cloud/conda-forge/",
        "https://mirrors.bfsu.edu.cn/anaconda/cloud/bioconda/",
        "https://mirrors.bfsu.edu.cn/anaconda/cloud/menpo/",
        "https://mirrors.bfsu.edu.cn/anaconda/cloud/pytorch/",
    ],
    "aliyun": [
        "https://mirrors.aliyun.com/anaconda/pkgs/main/",
        "https://mirrors.aliyun.com/anaconda/pkgs/free/",
        "https://mirrors.aliyun.com/anaconda/pkgs/r/",
        "https://mirrors.aliyun.com/anaconda/pkgs/msys2/",
        "https://mirrors.aliyun.com/anaconda/pkgs/pro/",
        "https://mirrors.aliyun.com/anaconda/pkgs/dev/",
        "https://mirrors.aliyun.com/anaconda/cloud/conda-forge/",
        "https://mirrors.aliyun.com/anaconda/cloud/bioconda/",
        "https://mirrors.aliyun.com/anaconda/cloud/menpo/",
        "https://mirrors.aliyun.com/anaconda/cloud/pytorch/",
    ],
}

_RUSTUP_MIRRORS: dict[str, dict[str, str]] = {
    "tsinghua": {
        "RUSTUP_DIST_SERVER": "https://mirrors.tuna.tsinghua.edu.cn/rustup",
        "RUSTUP_UPDATE_ROOT": "https://mirrors.tuna.tsinghua.edu.cn/rustup/rustup",
        "TOML_REGISTRY": "https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/",
    },
    "aliyun": {
        "RUSTUP_DIST_SERVER": "https://mirrors.aliyun.com/rustup",
        "RUSTUP_UPDATE_ROOT": "https://mirrors.aliyun.com/rustup/rustup",
        "TOML_REGISTRY": "https://mirrors.aliyun.com/crates.io-index/",
    },
    "ustc": {
        "RUSTUP_DIST_SERVER": "https://mirrors.ustc.edu.cn/rust-static",
        "RUSTUP_UPDATE_ROOT": "https://mirrors.ustc.edu.cn/rust-static/rustup",
        "TOML_REGISTRY": "https://mirrors.ustc.edu.cn/crates.io-index/",
    },
}

_RUST_SCCACHE_DIR: Path = Path.home() / ".cargo" / "sccache"
_RUST_SCCACHE_CACHE_SIZE: str = "20G"

_QT_LIBS: list[str] = [
    "build-essential",
    "libgl1",
    "libegl1",
    "libglib2.0-0",
    "libfontconfig1",
    "libfreetype6",
    "libxkbcommon0",
    "libdbus-1-3",
    "libxcb-xinerama0",
    "libxcb-icccm4",
    "libxcb-image0",
    "libxcb-keysyms1",
    "libxcb-randr0",
    "libxcb-render-util0",
    "libxcb-shape0",
    "libxcb-xfixes0",
    "libxcb-cursor0",
]

_CHINESE_FONTS: list[str] = [
    "fonts-noto-cjk",
    "fonts-wqy-microhei",
    "fonts-wqy-zenhei",
    "fonts-noto-color-emoji",
]

_DOWNLOAD_MIRROR_SCRIPT: str = "curl -sSL https://linuxmirrors.cn/main.sh -o /tmp/linuxmirrors.sh"
_INSTALL_MIRROR_SCRIPT: str = "sudo bash /tmp/linuxmirrors.sh"

_RUSTUP_DOWNLOAD_URL_LINUX: str = "https://mirrors.aliyun.com/repo/rust/rustup-init.sh"
_RUSTUP_DOWNLOAD_URL_WINDOWS: str = "https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe"

_BUN_NPM_REGISTRY: str = "https://registry.npmmirror.com"
_BUN_INSTALL_SCRIPT_URL: str = "https://bun.sh/install"
_PLAYWRIGHT_DOWNLOAD_HOST: str = "https://npmmirror.com/mirrors/playwright"

# 教育网镜像站点列表（站名 -> 站点首页）。
# 来源：https://help.mirrors.cernet.edu.cn/（MirrorZ，CERNET 教育网镜像帮助站）
# 收录的参与镜像站点，供 ``envdev mirror`` 探测与镜像自动选优参考。
_CERNET_MIRROR_SITES: dict[str, str] = {
    "tsinghua": "https://mirrors.tuna.tsinghua.edu.cn",
    "ustc": "https://mirrors.ustc.edu.cn",
    "zju": "https://mirrors.zju.edu.cn",
    "bfsu": "https://mirrors.bfsu.edu.cn",
    "sjtu": "https://mirror.sjtu.edu.cn",
    "nju": "https://mirror.nju.edu.cn",
    "pku": "https://mirrors.pku.edu.cn",
    "hit": "https://mirrors.hit.edu.cn",
    "cqu": "https://mirrors.cqu.edu.cn",
    "lzu": "https://mirror.lzu.edu.cn",
}

# Docker 镜像加速候选（探测可达性后按速度写入 daemon.json）
_DOCKER_REGISTRY_MIRRORS: list[str] = [
    "https://registry.cn-hangzhou.aliyuncs.com",
    "https://docker.m.daocloud.io",
    "https://hub-mirror.c.163.com",
]
_DOCKER_DAEMON_PATH: Path = Path("/etc/docker/daemon.json")

# 各服务镜像自动选优的探测地址（服务名 -> {镜像名: 该服务实际 URL}）。
# 注意 pip/conda/rustup/cargo 等服务在同名镜像站下的地址互不相同，且各站
# 对不同服务的可用性也不同，选优须按服务独立探测，不可混用地址。
_RUSTUP_PROBE_URLS: dict[str, str] = {name: cfg["RUSTUP_DIST_SERVER"] for name, cfg in _RUSTUP_MIRRORS.items()}
_CARGO_PROBE_URLS: dict[str, str] = {name: cfg["TOML_REGISTRY"] for name, cfg in _RUSTUP_MIRRORS.items()}
_CONDA_PROBE_URLS: dict[str, str] = {name: urls[0] for name, urls in _CONDA_MIRROR_URLS.items()}

# 各服务自动选优全部不可达时的兜底默认值（与各一键命令的原默认一致）
_SERVICE_DEFAULT_MIRRORS: dict[str, str] = {
    "pip": "aliyun",
    "conda": "aliyun",
    "rustup": "aliyun",
    "cargo": "aliyun",
    "go": "goproxy",
    "maven": "aliyun",
}


# ============================================================================
# Python 镜像源
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-python", help="配置 Python 镜像源", hidden=True)
def setup_python_mirror(mirror: str = "auto") -> None:
    """配置 Python（pip/uv）镜像源（持久化环境变量 + 写入 pip 配置文件）。

    通过 :func:`persist_env` 持久化 ``PIP_INDEX_URL`` / ``PIP_TRUSTED_HOSTS`` /
    ``UV_INDEX_URL`` / ``UV_PYTHON_INSTALL_MIRROR`` 等环境变量（Windows 写注册表，
    Linux/macOS 写 ``~/.profile``，同时更新当前进程），并写入 pip 配置文件。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/aliyun/huaweicloud/ustc/zju；``auto`` 时探测
        pip 服务各候选镜像（pypi 地址）并选用最快的可达镜像，全部不可达
        回退 aliyun（默认 ``auto``）
    """
    mirror = resolve_mirror("pip", mirror, _PIP_INDEX_URLS, _SERVICE_DEFAULT_MIRRORS["pip"])
    if not mirror_supported(mirror, _PIP_INDEX_URLS):
        print(f"未知 Python 镜像源: {mirror}")
        return

    index_url = _PIP_INDEX_URLS[mirror]
    trusted_host = _PIP_TRUSTED_HOSTS[mirror]

    print(f"配置 Python 镜像源: {mirror}")

    spec = MirrorSpec(
        env_vars={
            "PIP_INDEX_URL": index_url,
            "PIP_TRUSTED_HOSTS": trusted_host,
            "UV_INDEX_URL": index_url,
            "UV_PYTHON_INSTALL_MIRROR": _UV_PYTHON_INSTALL_MIRROR,
            "UV_HTTP_TIMEOUT": "600",
            "UV_LINK_MODE": "copy",
        },
        config_path=pip_config_path(),
        config_content=f"[global]\nindex-url = {index_url}\ntrusted-host = {trusted_host}\n",
    )
    apply_mirror_config(spec, persist_fn=persist_env, label="Python")


@fcmd.tool("envdev", subcommand="setup-conda", help="配置 Conda 镜像源", hidden=True)
def setup_conda_mirror(mirror: str = "auto") -> None:
    """配置 Conda 镜像源（写入 ~/.condarc）。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/ustc/bfsu/aliyun；``auto`` 时探测 Conda 服务
        各候选镜像（anaconda 频道地址，与 pypi 地址不同）并选用最快的可达
        镜像，全部不可达回退 aliyun（默认 ``auto``）
    """
    mirror = resolve_mirror("conda", mirror, _CONDA_PROBE_URLS, _SERVICE_DEFAULT_MIRRORS["conda"])
    if not mirror_supported(mirror, _CONDA_MIRROR_URLS):
        print(f"未知 Conda 镜像源: {mirror}")
        return

    urls = _CONDA_MIRROR_URLS[mirror]
    config_path = Path.home() / ".condarc"
    content = "show_channel_urls: true\nchannels:\n  - " + "\n  - ".join(urls) + "\n  - defaults\n"

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")
    print(f"Conda 镜像源已配置: {mirror} -> {config_path}")


# ============================================================================
# 语言级一键命令（由 lang 分组路由调用）
# ============================================================================


def setup_python_env(mirror: str = "auto") -> None:
    """一键配置 Python 开发环境（pip/uv 镜像源 + Conda 镜像源）。

    依次执行：配置 pip/uv 镜像源（环境变量 + pip 配置文件）、
    配置 Conda 镜像源（``~/.condarc``）。pip 与 Conda 支持的镜像源列表与
    服务地址均不同，不支持的步骤会打印提示并跳过（如 ``huaweicloud`` 仅
    pip 支持）；``mirror=auto`` 时二者分别独立探测选优，选优结果可能不同。

    Parameters
    ----------
    mirror:
        镜像源名称：pip 支持 tsinghua/aliyun/huaweicloud/ustc/zju，
        Conda 支持 tsinghua/ustc/bfsu/aliyun；``auto`` 按服务独立自动选优
        （默认 ``auto``）
    """
    setup_python_mirror(mirror)
    setup_conda_mirror(mirror)


# ============================================================================
# Rust 工具链
# ============================================================================


def _setup_rustup_mirror(mirror: str) -> None:
    """配置 rustup 镜像源（持久化 RUSTUP_* 环境变量 + 创建 sccache 目录）。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/ustc/aliyun（已由调用方解析，不含 ``auto``）
    """
    if not mirror_supported(mirror, _RUSTUP_MIRRORS):
        print(f"未知 Rust 镜像源: {mirror}")
        return

    mirrors = _RUSTUP_MIRRORS[mirror]
    spec = MirrorSpec(
        env_vars={
            "RUSTUP_DIST_SERVER": mirrors["RUSTUP_DIST_SERVER"],
            "RUSTUP_UPDATE_ROOT": mirrors["RUSTUP_UPDATE_ROOT"],
            "RUST_SCCACHE_DIR": str(_RUST_SCCACHE_DIR),
            "RUST_SCCACHE_CACHE_SIZE": _RUST_SCCACHE_CACHE_SIZE,
        },
        ensure_dirs=[_RUST_SCCACHE_DIR],
    )
    apply_mirror_config(spec, persist_fn=persist_env, label="Rust rustup")

    print(f"Rust rustup 镜像源已配置: {mirror}")


def _setup_cargo_mirror(mirror: str) -> None:
    """配置 cargo crates 镜像源（写入 ~/.cargo/config.toml）。

    crates.io 索引地址与 rustup 分发地址不同，单独探测选优。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/ustc/aliyun（已由调用方解析，不含 ``auto``）
    """
    if not mirror_supported(mirror, _RUSTUP_MIRRORS):
        print(f"未知 Rust 镜像源: {mirror}")
        return

    registry = _RUSTUP_MIRRORS[mirror]["TOML_REGISTRY"]
    config_content = (
        f"\n[source.crates-io]\nreplace-with = '{mirror}'\n\n"
        f'[source.{mirror}]\nregistry = "sparse+{registry}"\n\n'
        f'[registries.{mirror}]\nindex = "sparse+{registry}"\n'
    )

    spec = MirrorSpec(
        config_path=Path.home() / ".cargo" / "config.toml",
        config_content=config_content,
    )
    apply_mirror_config(spec, persist_fn=persist_env, label="Rust cargo")


@fcmd.tool("envdev", subcommand="setup-rust", help="配置 Rust 镜像源", hidden=True)
def _setup_rust_mirror(mirror: str = "auto") -> None:
    """配置 Rust 镜像源（rustup 环境变量 + cargo 配置 + sccache 目录）。

    通过 :func:`persist_env` 持久化 ``RUSTUP_DIST_SERVER`` / ``RUSTUP_UPDATE_ROOT`` /
    ``RUST_SCCACHE_DIR`` 等环境变量，写入 ``~/.cargo/config.toml``，并创建
    sccache 缓存目录。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/ustc/aliyun；``auto`` 时 rustup 与 cargo 分别
        独立探测选优（rustup 分发地址与 crates.io 索引地址不同，各站可达性
        可能不一致，选优结果可能不同），全部不可达各自回退 aliyun
        （默认 ``auto``）
    """
    _setup_rustup_mirror(resolve_mirror("rustup", mirror, _RUSTUP_PROBE_URLS, _SERVICE_DEFAULT_MIRRORS["rustup"]))
    _setup_cargo_mirror(resolve_mirror("cargo", mirror, _CARGO_PROBE_URLS, _SERVICE_DEFAULT_MIRRORS["cargo"]))


@fcmd.tool("envdev", subcommand="download-rustup", help="下载 Rustup 安装脚本", hidden=True)
def _download_rustup() -> None:
    """下载 Rustup 安装脚本（跨平台，已安装 rustup 时跳过）。

    Linux 下载 ``rustup-init.sh``，Windows 下载 ``rustup-init.exe``。
    """
    if shutil.which("rustup") is not None:
        print("rustup 已安装，跳过下载")
        return

    if sys.platform == "win32":
        print("下载 rustup-init.exe...")
        run_command(
            [
                "powershell",
                "-Command",
                "Invoke-WebRequest",
                "-Uri",
                _RUSTUP_DOWNLOAD_URL_WINDOWS,
                "-OutFile",
                "rustup-init.exe",
            ],
        )
    else:
        print("下载 rustup-init.sh...")
        run_command(["curl", "-fsSL", _RUSTUP_DOWNLOAD_URL_LINUX, "-o", "rustup-init.sh"])


@fcmd.tool("envdev", subcommand="install-rust", help="安装 Rust 工具链", hidden=True)
def _install_rust_toolchain(version: str = "stable") -> None:
    """安装 Rust 工具链（rustup 未安装时跳过）。

    Parameters
    ----------
    version:
        Rust 版本：``stable`` / ``nightly`` / ``beta``（默认 ``stable``）
    """
    if shutil.which("rustup") is None:
        print("rustup 未安装，跳过工具链安装")
        return

    run_command(["rustup", "toolchain", "install", version])
    print(f"Rust 工具链 {version} 安装完成")


def setup_rust_env(mirror: str = "auto", rust_version: str = "stable") -> None:
    """一键配置 Rust 开发环境（镜像源 + 下载 rustup + 安装工具链）。

    依次执行：配置 Rust 镜像源（rustup 环境变量 + ``~/.cargo/config.toml`` +
    sccache 目录，``auto`` 时 rustup 与 cargo 按服务独立选优）、下载 Rustup
    安装脚本（已安装 rustup 时跳过）、安装指定版本工具链（rustup 未安装时
    跳过）。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/ustc/aliyun；``auto`` 按服务独立自动选优
        （默认 ``auto``）
    rust_version:
        Rust 版本：``stable`` / ``nightly`` / ``beta``（默认 ``stable``）
    """
    _setup_rust_mirror(mirror)
    _download_rustup()
    _install_rust_toolchain(rust_version)


# ============================================================================
# JavaScript (Bun)
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-bun", help="配置 Bun 镜像源", hidden=True)
def _setup_bun_mirror() -> None:
    """配置 Bun npm 镜像源（持久化环境变量 + 写入 ``~/.bunfig.toml``）。

    通过 :func:`persist_env` 持久化 ``BUN_CONFIG_REGISTRY`` 环境变量，
    并写入 bunfig.toml 指向 npmmirror。
    """
    spec = MirrorSpec(
        env_vars={"BUN_CONFIG_REGISTRY": _BUN_NPM_REGISTRY},
        config_path=Path.home() / ".bunfig.toml",
        config_content=f'[install]\nregistry = "{_BUN_NPM_REGISTRY}"\n',
    )
    apply_mirror_config(spec, persist_fn=persist_env, label="Bun")


@fcmd.tool("envdev", subcommand="install-bun", help="安装 Bun", hidden=True)
def _install_bun() -> None:
    """安装 Bun.js（已安装时跳过）。

    Linux/macOS 通过官方脚本安装（Linux 先安装 curl/zip/unzip 依赖）；
    Windows 提示使用 PowerShell 安装命令。
    """
    if shutil.which("bun") is not None:
        print("bun 已安装，跳过安装")
        return

    if sys.platform == "win32":
        print("Windows 请使用 PowerShell 执行: irm bun.sh/install.ps1 | iex")
        return

    if sys.platform.startswith("linux"):
        print("更新系统包...")
        run_command(["sudo", "apt", "update"])
        run_command(["sudo", "apt", "install", "-y", "curl", "zip", "unzip"])

    print("下载并安装 Bun.js...")
    run_command(["bash", "-c", f"curl -fsSL {_BUN_INSTALL_SCRIPT_URL} | bash"])
    print("Bun.js 安装完成")


def setup_js_env() -> None:
    """一键配置 JavaScript 开发环境（Bun npm 镜像源 + 安装 Bun.js）。

    依次执行：配置 Bun npm 镜像源（环境变量 + ``~/.bunfig.toml``）、
    安装 Bun.js（已安装时跳过）。
    """
    _setup_bun_mirror()
    _install_bun()
    persist_env("PLAYWRIGHT_DOWNLOAD_HOST", _PLAYWRIGHT_DOWNLOAD_HOST)


# ============================================================================
# Linux 专用子命令
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-linux-mirror", help="配置 Linux 系统镜像源", hidden=True)
def setup_linux_system_mirror() -> None:
    """下载并安装 Linux 系统镜像源（仅 Linux，已配置国内镜像时跳过）。

    检查 ``/etc/apt/sources.list`` 与 ``/etc/apt/sources.list.d/ubuntu.sources``
    是否已配置国内镜像，已配置则跳过；未配置则下载并执行 linuxmirrors 脚本。
    """
    if not sys.platform.startswith("linux"):
        print("setup_linux_system_mirror: 仅在 Linux 上支持")
        return

    apt_files = ["/etc/apt/sources.list", "/etc/apt/sources.list.d/ubuntu.sources"]
    mirror_keys = list(_PIP_INDEX_URLS.keys())
    already_configured = False
    for apt_file in apt_files:
        try:
            content = Path(apt_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        else:
            if any(mirror in content for mirror in mirror_keys):
                already_configured = True

    if already_configured:
        print("已配置国内镜像源，跳过系统镜像配置")
        return

    print("下载 linuxmirrors 脚本...")
    run_command(["bash", "-c", _DOWNLOAD_MIRROR_SCRIPT])
    print("安装 linuxmirrors...")
    run_command(["bash", "-c", _INSTALL_MIRROR_SCRIPT])


@fcmd.tool("envdev", subcommand="install-qt-libs", help="安装 Qt 依赖库", hidden=True)
def install_linux_qt_libs() -> None:
    """安装 Qt 依赖库（仅 Linux）。"""
    if not sys.platform.startswith("linux"):
        print("install_linux_qt_libs: 仅在 Linux 上支持")
        return

    run_command(["sudo", "apt", "install", "-y", *_QT_LIBS])
    print("Qt 依赖库安装完成")


@fcmd.tool("envdev", subcommand="install-fonts", help="安装中文字体", hidden=True)
def install_linux_fonts() -> None:
    """安装中文字体（仅 Linux）。"""
    if not sys.platform.startswith("linux"):
        print("install_linux_fonts: 仅在 Linux 上支持")
        return

    run_command(["sudo", "apt", "install", "-y", *_CHINESE_FONTS])
    print("中文字体安装完成")


@fcmd.tool("envdev", subcommand="install-docker", help="安装 Docker", hidden=True)
def install_linux_docker() -> None:
    """安装 Docker（仅 Linux）。"""
    if not sys.platform.startswith("linux"):
        print("install_linux_docker: 仅在 Linux 上支持")
        return

    run_command(["sudo", "apt", "install", "-y", "docker-compose-v2"])
    run_command(["sudo", "usermod", "-aG", "docker", getpass.getuser()])
    print("Docker 安装完成（需重新登录以生效 docker 用户组）")


@fcmd.tool("envdev", subcommand="setup-docker-mirror", help="配置 Docker 镜像加速源", hidden=True)
def setup_docker_mirror() -> None:
    """配置 Docker 镜像加速源（仅 Linux）。

    先并发探测候选加速源的可访问性与访问速度，仅将可达项按速度排序写入
    ``/etc/docker/daemon.json`` 的 ``registry-mirrors``；全部不可达时保留
    完整候选列表并提示。Windows/macOS 提示手动在 Docker Desktop 设置中配置。
    """
    if not sys.platform.startswith("linux"):
        print("Linux 专用：请在 Docker Desktop 设置 > Docker Engine 中添加 registry-mirrors")
        return

    # 探测候选加速源，仅保留可达项并按速度排序写入
    results = check_urls(_DOCKER_REGISTRY_MIRRORS, timeout=2.0)
    mirrors = [url for url, ok, _latency in results if ok]
    if mirrors:
        print("Docker 加速源探测结果（按访问速度排序）:")
        for url, ok, latency in results:
            speed = f"{latency:.0f} ms" if ok else "-"
            print(f"  {url}  {'可访问' if ok else '不可访问'}  {speed}")
    else:
        print("加速源探测全部不可达，保留完整候选列表")
        mirrors = list(_DOCKER_REGISTRY_MIRRORS)

    new_config = {"registry-mirrors": mirrors}

    _DOCKER_DAEMON_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DOCKER_DAEMON_PATH.write_text(json.dumps(new_config, indent=2), encoding="utf-8")
    run_command(["sudo", "systemctl", "restart", "docker"])
    print(f"Docker 镜像加速已配置 -> {_DOCKER_DAEMON_PATH}")


# ============================================================================
# OpenSSH
# ============================================================================


@fcmd.tool("envdev", subcommand="install-openssh", help="安装并启动 OpenSSH Server", hidden=True)
def install_linux_openssh() -> None:
    """安装 OpenSSH Server（仅 Linux），enable 并立即启动 sshd。

    已安装时跳过 apt 安装，仅确保 systemd 服务处于 enable + active 状态。
    """
    if not sys.platform.startswith("linux"):
        print("install_linux_openssh: 仅在 Linux 上支持")
        return

    if shutil.which("sshd") is None:
        run_command(["sudo", "apt", "update"])
        run_command(["sudo", "apt", "install", "-y", "openssh-server"])
    else:
        print("openssh-server 已安装，跳过安装")

    run_command(["sudo", "systemctl", "enable", "--now", "ssh"])
    print("OpenSSH Server 已启用（systemd 服务 ssh）")


# ============================================================================
# 远程桌面（xrdp + Xfce）
# ============================================================================

_GNOME_REMOTE_DESKTOP_PKGS: list[str] = [
    "gnome-remote-desktop",
]

_XFCE_DESKTOP_PKGS: list[str] = [
    "xfce4",
    "xfce4-goodies",
    "xorgxrdp",
]

_XSESSION_XFCE4: str = "xfce4-session\n"


@fcmd.tool("envdev", subcommand="uninstall-gnome-remote", help="卸载 GNOME 远程桌面", hidden=True)
def _uninstall_gnome_remote_desktop() -> None:
    """禁用并卸载 GNOME 远程桌面（仅 Linux），避免与 xrdp 冲突。"""
    if not sys.platform.startswith("linux"):
        return

    if shutil.which("gnome-remote-desktop") is None:
        print("gnome-remote-desktop 未安装，跳过卸载")
        return

    print("禁用 gnome-remote-desktop 服务...")
    run_command(["sudo", "systemctl", "disable", "--now", "gnome-remote-desktop"])
    print("卸载 gnome-remote-desktop...")
    run_command(["sudo", "apt", "purge", "-y", *_GNOME_REMOTE_DESKTOP_PKGS])
    run_command(["sudo", "apt", "autoremove", "-y"])


@fcmd.tool("envdev", subcommand="install-xfce", help="安装 Xfce 桌面 + xorgxrdp", hidden=True)
def _install_xfce_desktop() -> None:
    """安装轻量 Xfce 桌面环境及 xorgxrdp（仅 Linux），写入 ~/.xsession。"""
    if not sys.platform.startswith("linux"):
        return

    print("更新包索引...")
    run_command(["sudo", "apt", "update"])
    print("安装 Xfce 桌面 + xorgxrdp...")
    run_command(["sudo", "apt", "install", "-y", *_XFCE_DESKTOP_PKGS])

    xsession = Path.home() / ".xsession"
    xsession.write_text(_XSESSION_XFCE4, encoding="utf-8")
    xsession.chmod(0o755)
    print(f"已写入 {xsession}")


@fcmd.tool("envdev", subcommand="install-xrdp", help="安装并启动 xrdp", hidden=True)
def _install_xrdp() -> None:
    """安装 xrdp（仅 Linux），加入 ssl-cert 用户组并 enable + now 启动。"""
    if not sys.platform.startswith("linux"):
        return

    if shutil.which("xrdp") is None:
        print("安装 xrdp...")
        run_command(["sudo", "apt", "install", "-y", "xrdp"])
    else:
        print("xrdp 已安装，跳过安装")

    run_command(["sudo", "adduser", "xrdp", "ssl-cert"])
    run_command(["sudo", "systemctl", "enable", "--now", "xrdp"])
    print("xrdp 已启用（systemd 服务 xrdp）")


@fcmd.tool("envdev", subcommand="configure-lightdm", help="配置 lightdm 显示管理器", hidden=True)
def _configure_lightdm() -> None:
    """将 lightdm 设为默认显示管理器（仅 Linux）。

    通过 ``debconf-set-selections`` 注入 lightdm 选项后，以 noninteractive
    模式调用 ``dpkg-reconfigure lightdm``，避免交互式弹窗。最后重启 lightdm。
    """
    if not sys.platform.startswith("linux"):
        return

    print("将 lightdm 设为默认显示管理器...")
    run_command(
        ["sudo", "sh", "-c", "echo lightdm | debconf-set-selections && dpkg-reconfigure -f noninteractive lightdm"]
    )
    run_command(["sudo", "systemctl", "restart", "lightdm"])
    print("lightdm 已配置并重启")


@fcmd.tool("envdev", subcommand="remote", help="一键配置远程桌面（xrdp + Xfce）", hidden=True)
def setup_linux_remote() -> None:
    """一键配置 Linux 远程桌面（仅 Linux）。

    依次执行：卸载 GNOME 远程桌面（切断冲突）、安装 Xfce + xorgxrdp
    （轻量桌面）、安装并启动 xrdp、配置 lightdm 为默认显示管理器。
    已安装的组件会跳过对应步骤。
    """
    if not sys.platform.startswith("linux"):
        print("setup_linux_remote: 仅在 Linux 上支持")
        return

    _uninstall_gnome_remote_desktop()
    _install_xfce_desktop()
    _install_xrdp()
    _configure_lightdm()
    print("远程桌面配置完成（RDP 端口 3389，SSH 端口 22）")


# ============================================================================
# 镜像站点探测（mirror）
# ============================================================================

# MirrorZ CERNET 门户入口与 JS 包内嵌站点元数据的提取模式。
# 门户前端将参与镜像站的元数据以 ``JSON.parse('{"url":...,"abbr":...}')``
# 形式内嵌于 JS 包，从中可动态提取完整站点列表。
_MIRRORZ_HOME: str = "https://mirrors.cernet.edu.cn/"
_MIRRORZ_JS_PATH_RE: str = r'src="?([^"\s>]+\.js)'
_MIRRORZ_SITE_BLOB_RE: str = r"JSON\.parse\('(\{\"url\":.*?\})'\)"
# 聚合门户自身也会作为站点条目出现，需剔除
_MIRRORZ_PORTAL_URL: str = "https://mirrors.cernet.edu.cn"


def fetch_mirrorz_sites(timeout: float = 5.0) -> dict[str, str] | None:
    """动态拉取 MirrorZ（CERNET 教育网联合镜像站门户）收录的镜像站点列表。

    流程：抓取门户入口页 -> 定位 JS 包 -> 提取内嵌站点元数据
    （``JSON.parse('{"url":...}')`` 片段，含站名缩写与首页地址）->
    按首页地址去重（同址多站如 TUNA.NANO/NEO 仅保留首个）。

    Parameters
    ----------
    timeout:
        页面与 JS 包的抓取超时秒数（默认 ``5``）

    Returns
    -------
    dict[str, str] | None
        ``{站名缩写(小写): 首页地址}``；任一环节失败（网络错误、页面或
        包结构变化、解析结果为空）返回 ``None``，调用方应回退内置列表。
    """
    # 惰性导入 ssl 链（urllib.request → http.client → ssl）：本模块被
    # 工具发现导入，顶层导入会让每个 CLI 命令启动都付 ~37ms
    from urllib.error import URLError
    from urllib.parse import urljoin
    from urllib.request import Request, urlopen

    try:
        req_headers = {"User-Agent": "Mozilla/5.0 (compatible; fcmd-envdev)"}
        html_resp = urlopen(Request(_MIRRORZ_HOME, headers=req_headers), timeout=timeout)
        with html_resp:
            html = html_resp.read().decode("utf-8")
        js_match = re.search(_MIRRORZ_JS_PATH_RE, html)
        if js_match is None:
            logger.warning("MirrorZ 门户页面未找到 JS 包引用")
            return None
        js_resp = urlopen(Request(urljoin(_MIRRORZ_HOME, js_match.group(1)), headers=req_headers), timeout=timeout)
        with js_resp:
            js = js_resp.read().decode("utf-8", "ignore")
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning("拉取 MirrorZ 站点列表失败: %s", exc)
        return None

    sites: dict[str, str] = {}
    seen_urls: set[str] = set()
    for blob in re.findall(_MIRRORZ_SITE_BLOB_RE, js):
        # JS 单引号字符串中的 \' 在 JSON 中非法，先还原为 '
        raw = blob.replace("\\'", "'")
        try:
            data = json.loads(raw)
            url, abbr = str(data["url"]), str(data["abbr"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.debug("跳过无法解析的 MirrorZ 站点片段: %s", exc)
            continue
        if url.rstrip("/") == _MIRRORZ_PORTAL_URL:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        sites[abbr.lower()] = url

    if not sites:
        logger.warning("MirrorZ JS 包中未解析出任何站点")
        return None
    return sites


@fcmd.tool("envdev", subcommand="mirror", help="探测教育网镜像站点（可访问性 + 访问速度排名）")
def check_cernet_mirrors(timeout: float = 3.0, source: Literal["auto", "builtin"] = "auto") -> int:
    """并发探测教育网镜像站点列表（只读，不做任何配置变更）。

    站点列表默认（``auto``）从 MirrorZ（``mirrors.cernet.edu.cn``，CERNET
    教育网联合镜像站门户）动态拉取；拉取失败或显式指定 ``builtin`` 时使用
    内置列表（取自 ``help.mirrors.cernet.edu.cn`` 收录站点）。输出按访问
    速度排序的探测结果，供选择镜像源参考。

    Parameters
    ----------
    timeout:
        单个站点的探测超时秒数（默认 ``3``）
    source:
        站点列表来源：``auto`` 动态拉取（失败回退内置） / ``builtin`` 内置列表

    Returns
    -------
    int
        存在可达站点返回 ``0``，全部不可达返回 ``1``。
    """
    dynamic = False
    if source == "auto":
        fetched = fetch_mirrorz_sites(timeout)
        if fetched is not None:
            dynamic = True
            sites = fetched
            print(f"[站点列表] 动态拉取 MirrorZ：{len(sites)} 个站点")
        else:
            print("[站点列表] 动态拉取失败，回退内置列表")
            sites = dict(_CERNET_MIRROR_SITES)
    else:
        sites = dict(_CERNET_MIRROR_SITES)

    name_by_url = {url: name for name, url in sites.items()}
    results = check_urls(list(sites.values()), timeout=timeout)

    origin = "mirrors.cernet.edu.cn 动态列表" if dynamic else "help.mirrors.cernet.edu.cn 内置列表"
    print(f"[教育网镜像站点探测]（来源: {origin}，按访问速度排序）")
    reachable = 0
    for index, (url, ok, latency) in enumerate(results, start=1):
        if ok:
            reachable += 1
        speed = f"{latency:.0f} ms" if ok else "-"
        print(f"  {index}. {name_by_url[url]:<10} {url}  {'可访问' if ok else '不可访问'}  {speed}")

    if reachable == 0:
        print("\n全部镜像站点不可达，请检查网络")
        return 1
    print(f"\n可达 {reachable}/{len(results)} 个站点")
    return 0


# ============================================================================
# 分组路由（lang / app）
# ============================================================================


@fcmd.tool("envdev", subcommand="lang", help="语言类一键配置（python/js/rust/go/java/node）")
def setup_lang_env(  # noqa: PLR0913  CLI 参数需全量透传给各语言一键命令
    language: Literal["python", "js", "rust", "go", "java", "node"],
    mirror: str = "auto",
    rust_version: str = "stable",
    install_nvm: bool = False,
    install_gvm: bool = False,
    install_sdkman: bool = False,
) -> None:
    """按语言一键配置开发环境。

    各语言对应的一键配置：

    - ``python``：pip/uv 镜像源（环境变量 + pip 配置文件）+ Conda 镜像源
    - ``js``：Bun npm 镜像源 + 安装 Bun.js
    - ``rust``：Rust 镜像源 + 下载 rustup + 安装工具链
    - ``go``：GOPROXY 镜像源（可选安装 gvm）
    - ``java``：Maven 镜像源（可选安装 SDKMAN）
    - ``node``：npm/yarn/pnpm 镜像源（可选安装 nvm）

    镜像源默认 ``auto``：各服务（pip/conda/rustup/cargo/go/maven）在配置前
    独立探测自身支持镜像的可访问性与速度，分别选用最快的可达镜像；全部
    不可达时回退该服务默认镜像。注意各服务的镜像地址与支持列表互不相同，
    选优结果可能不同（如 pip 选中 tsinghua 而 conda 选中 ustc）。

    Parameters
    ----------
    language:
        语言名：python / js / rust / go / java / node
    mirror:
        镜像源名称（默认 ``auto`` 按服务独立自动选优）；``auto`` 之外为
        显式指定，各语言支持列表不同，不支持时打印提示跳过。注意：``go``
        原独立命令默认 goproxy，经 ``lang`` 显式指定时亦受支持，
        可用 ``--mirror goproxy`` 还原
    rust_version:
        Rust 版本：stable / nightly / beta（默认 stable，仅 ``rust`` 使用）
    install_nvm:
        是否同时安装 nvm（仅 ``node`` 使用）
    install_gvm:
        是否同时安装 gvm（仅 ``go`` 使用）
    install_sdkman:
        是否同时安装 SDKMAN（仅 ``java`` 使用）
    """
    if language == "python":
        setup_python_env(mirror)
    elif language == "js":
        setup_js_env()
    elif language == "rust":
        setup_rust_env(mirror, rust_version)
    elif language == "go":
        setup_go_env(mirror, install_gvm)
    elif language == "java":
        setup_java_env(mirror, install_sdkman)
    else:
        setup_node_env(install_nvm)


@fcmd.tool(
    "envdev",
    subcommand="app",
    help="应用/系统类一键配置（linux-mirror/qt-libs/fonts/docker/docker-mirror/openssh/remote）",
)
def setup_app_env(
    target: Literal["linux-mirror", "qt-libs", "fonts", "docker", "docker-mirror", "openssh", "remote"],
) -> None:
    """按应用/系统项一键配置（多数仅 Linux 支持，非 Linux 打印提示跳过）。

    各应用对应的配置：

    - ``linux-mirror``：下载并安装系统镜像源（linuxmirrors）
    - ``qt-libs``：安装 Qt 依赖库
    - ``fonts``：安装中文字体
    - ``docker``：安装 Docker 并加入 docker 用户组
    - ``docker-mirror``：探测候选加速源可达性后写入 Docker 镜像加速配置
    - ``openssh``：安装并启动 OpenSSH Server
    - ``remote``：一键配置远程桌面（xrdp + Xfce）

    Parameters
    ----------
    target:
        应用/系统项名称（见上）
    """
    if target == "linux-mirror":
        setup_linux_system_mirror()
    elif target == "qt-libs":
        install_linux_qt_libs()
    elif target == "fonts":
        install_linux_fonts()
    elif target == "docker":
        install_linux_docker()
    elif target == "docker-mirror":
        setup_docker_mirror()
    elif target == "openssh":
        install_linux_openssh()
    else:
        setup_linux_remote()


@fcmd.tool("envdev", subcommand="all", help="一键配置所有环境")
def setup_all_env(mirror: str = "auto", rust_version: str = "stable") -> None:
    """一键配置所有开发环境（Python + JavaScript + Rust + Go + Java + Node + Linux 系统依赖）。

    依次执行：一键配置各语言环境的镜像源（Python / JavaScript / Rust / Go /
    Java / Node）；Linux 平台额外配置系统镜像源、安装 Qt 依赖库、中文字体、
    Docker 及其镜像加速（非 Linux 平台自动跳过并打印提示）。

    Parameters
    ----------
    mirror:
        镜像源名称：默认 ``auto`` 时各服务（pip/conda/rustup/cargo/go/maven）
        独立探测选优；显式指定时各语言支持列表不同，不支持的步骤打印提示跳过
    rust_version:
        Rust 版本：``stable`` / ``nightly`` / ``beta``（默认 ``stable``）
    """
    setup_python_env(mirror)
    setup_js_env()
    setup_rust_env(mirror, rust_version)
    setup_go_env()
    setup_java_env(mirror)
    setup_node_env()
    setup_linux_system_mirror()
    install_linux_qt_libs()
    install_linux_fonts()
    install_linux_docker()


# ============================================================================
# 环境检测
# ============================================================================

_TOOLCHAIN_CHECKS: list[tuple[str, str]] = [
    ("python", "Python"),
    ("pip", "pip"),
    ("go", "Go"),
    ("node", "Node.js"),
    ("java", "Java"),
    ("rustup", "Rust"),
    ("bun", "Bun"),
    ("docker", "Docker"),
]

_ENV_VAR_CHECKS: list[tuple[str, str, str]] = [
    ("PIP_INDEX_URL", "Python pip", "pip config get global.index-url"),
    ("GOPROXY", "Go", "go env GOPROXY"),
    ("NPM_CONFIG_REGISTRY", "npm", "npm config get registry"),
    ("RUSTUP_DIST_SERVER", "Rust", "rustup show"),
]


@fcmd.tool("envdev", subcommand="check", help="检测开发环境配置状态")
def check_env() -> int:
    """检测开发环境配置状态（只读）。

    检查各语言工具链是否存在 + 关键镜像源环境变量是否已设置。
    返回 0 表示全部 OK，返回 1 表示存在未配置项。
    """
    all_ok = True
    missing_tools: list[str] = []
    missing_envs: list[str] = []

    print("[工具链检测]")
    for tool, display in _TOOLCHAIN_CHECKS:
        found = shutil.which(tool) is not None
        status = "OK" if found else "缺失"
        print(f"  {display:<10} {status}")
        if not found:
            missing_tools.append(display)
            all_ok = False

    print("\n[镜像源检测]")
    for env_var, display, _hint in _ENV_VAR_CHECKS:
        val = os.environ.get(env_var, "")
        ok = bool(val)
        status = f"已设置 ({val})" if ok else "未设置"
        print(f"  {display:<10} {status}")
        if not ok:
            missing_envs.append(display)
            all_ok = False

    if all_ok:
        print("\n全部环境检测通过 ✓")
        return 0

    print(f"\n未通过 {len(missing_tools) + len(missing_envs)} 项：")
    if missing_tools:
        print(f"  缺失工具: {', '.join(missing_tools)}")
    if missing_envs:
        print(f"  未配置镜像: {', '.join(missing_envs)}")
    print("\n运行 'fcmd envdev all' 一键配置")
    return 1


@fcmd.main("envdev")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
