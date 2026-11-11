"""envdev 的语言级镜像配置（Python / Rust / JavaScript）。

从 :mod:`fcmd.tools.dev.envdev` 拆出；``envdev`` 仍 re-export 公开函数与镜像表
常量，既有导入路径、CLI 注册顺序与输出契约不变。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import fcmd
from fcmd.infras.command import run_command
from fcmd.infras.system import persist_env
from fcmd.tools.dev.envdev_core import (
    MirrorSpec,
    apply_mirror_config,
    mirror_supported,
    pip_config_path,
    resolve_mirror,
)
from fcmd.tools.dev.envdev_node import (
    _NPM_DEFAULT,
    _NPM_PROBE_URLS,
    _NPM_REGISTRY_MIRRORS,
)

_PIP_INDEX_URLS: dict[str, str] = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "huaweicloud": "https://mirrors.huaweicloud.com/repository/pypi/simple/",
    "tencent": "https://mirrors.cloud.tencent.com/pypi/simple",
    "ustc": "https://pypi.mirrors.ustc.edu.cn/simple/",
    "bfsu": "https://mirrors.bfsu.edu.cn/pypi/web/simple",
    "zju": "https://mirrors.zju.edu.cn/pypi/web/simple",
    "nju": "https://mirror.nju.edu.cn/pypi/web/simple",
    "sjtu": "https://mirror.sjtu.edu.cn/pypi/web/simple",
    "pku": "https://mirrors.pku.edu.cn/pypi/web/simple",
    "cernet": "https://mirrors.cernet.edu.cn/pypi/web/simple",
}

_PIP_TRUSTED_HOSTS: dict[str, str] = {
    "tsinghua": "pypi.tuna.tsinghua.edu.cn",
    "aliyun": "mirrors.aliyun.com",
    "huaweicloud": "mirrors.huaweicloud.com",
    "tencent": "mirrors.cloud.tencent.com",
    "ustc": "pypi.mirrors.ustc.edu.cn",
    "bfsu": "mirrors.bfsu.edu.cn",
    "zju": "mirrors.zju.edu.cn",
    "nju": "mirror.nju.edu.cn",
    "sjtu": "mirror.sjtu.edu.cn",
    "pku": "mirrors.pku.edu.cn",
    "cernet": "mirrors.cernet.edu.cn",
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
    "zju": [
        "https://mirrors.zju.edu.cn/anaconda/pkgs/main/",
        "https://mirrors.zju.edu.cn/anaconda/pkgs/free/",
        "https://mirrors.zju.edu.cn/anaconda/pkgs/r/",
        "https://mirrors.zju.edu.cn/anaconda/pkgs/msys2/",
        "https://mirrors.zju.edu.cn/anaconda/pkgs/pro/",
        "https://mirrors.zju.edu.cn/anaconda/cloud/conda-forge/",
        "https://mirrors.zju.edu.cn/anaconda/cloud/bioconda/",
        "https://mirrors.zju.edu.cn/anaconda/cloud/pytorch/",
    ],
    "nju": [
        "https://mirror.nju.edu.cn/anaconda/pkgs/main/",
        "https://mirror.nju.edu.cn/anaconda/pkgs/free/",
        "https://mirror.nju.edu.cn/anaconda/pkgs/r/",
        "https://mirror.nju.edu.cn/anaconda/pkgs/msys2/",
        "https://mirror.nju.edu.cn/anaconda/pkgs/pro/",
        "https://mirror.nju.edu.cn/anaconda/cloud/conda-forge/",
        "https://mirror.nju.edu.cn/anaconda/cloud/bioconda/",
        "https://mirror.nju.edu.cn/anaconda/cloud/pytorch/",
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
    "huaweicloud": {
        "RUSTUP_DIST_SERVER": "https://mirrors.huaweicloud.com/rustup",
        "RUSTUP_UPDATE_ROOT": "https://mirrors.huaweicloud.com/rustup/rustup",
        "TOML_REGISTRY": "https://mirrors.huaweicloud.com/crates.io-index/",
    },
    "sjtu": {
        "RUSTUP_DIST_SERVER": "https://mirror.sjtu.edu.cn/rust-static",
        "RUSTUP_UPDATE_ROOT": "https://mirror.sjtu.edu.cn/rust-static/rustup",
        "TOML_REGISTRY": "https://mirror.sjtu.edu.cn/crates.io-index/",
    },
}

# crates.io 稀疏索引镜像：与 rustup 分发地址相互独立（站点可能只镜像其一）。
# bfsu 已停止提供 rust-static 分发但 crates.io 索引仍可用；华为云 crates 索引
# 已下线（返回门户 HTML），故不纳入。两份候选表不可互相推导。
_CARGO_REGISTRY_MIRRORS: dict[str, str] = {
    "tsinghua": "https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/",
    "aliyun": "https://mirrors.aliyun.com/crates.io-index/",
    "ustc": "https://mirrors.ustc.edu.cn/crates.io-index/",
    "sjtu": "https://mirror.sjtu.edu.cn/crates.io-index/",
    "bfsu": "https://mirrors.bfsu.edu.cn/crates.io-index/",
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

_BUN_INSTALL_SCRIPT_URL: str = "https://bun.sh/install"
_PLAYWRIGHT_DOWNLOAD_HOST: str = "https://npmmirror.com/mirrors/playwright"
_PIP_PROBE_URLS: dict[str, str] = {name: f"{url.rstrip('/')}/requests/" for name, url in _PIP_INDEX_URLS.items()}
_CONDA_PROBE_URLS: dict[str, str] = {
    name: f"{urls[0]}noarch/repodata.json" for name, urls in _CONDA_MIRROR_URLS.items()
}
_RUSTUP_PROBE_URLS: dict[str, str] = {
    name: f"{cfg['RUSTUP_DIST_SERVER'].rstrip('/')}/dist/channel-rust-stable.toml"
    for name, cfg in _RUSTUP_MIRRORS.items()
}
_CARGO_PROBE_URLS: dict[str, str] = {
    name: f"{url.rstrip('/')}/se/rd/serde" for name, url in _CARGO_REGISTRY_MIRRORS.items()
}

# 各服务自动选优全部不可达时的兜底默认值（优先选用无需限速、覆盖稳定的站点）
_SERVICE_DEFAULT_MIRRORS: dict[str, str] = {
    "pip": "pku",
    "conda": "nju",
    "rustup": "tsinghua",
    "cargo": "bfsu",
    "go": "goproxy",
    "maven": "tencent",
    "npm": _NPM_DEFAULT,
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
        镜像源名称：tsinghua/aliyun/huaweicloud/tencent/ustc/bfsu/zju/nju/sjtu/pku/cernet；
        ``auto`` 时按实测下载吞吐探测 pip 服务各候选镜像（pypi 地址）并选用最快且
        未限速的可达镜像，全部不可达回退 pku（默认 ``auto``）
    """
    mirror = resolve_mirror("pip", mirror, _PIP_PROBE_URLS, _SERVICE_DEFAULT_MIRRORS["pip"])
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
        镜像源名称：tsinghua/ustc/bfsu/zju/nju；``auto`` 时探测 Conda 服务各候选镜像
        （anaconda 频道地址，与 pypi 地址不同）并选用最快的可达镜像，全部不可达
        回退 nju（默认 ``auto``）
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
        镜像源名称：pip 支持 tsinghua/aliyun/huaweicloud/tencent/ustc/bfsu/zju/nju/sjtu/pku/cernet，
        Conda 支持 tsinghua/ustc/bfsu/zju/nju；``auto`` 按服务独立自动选优
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
        镜像源名称：tsinghua/ustc/aliyun/huaweicloud/sjtu（已由调用方解析，不含 ``auto``）
    """
    if not mirror_supported(mirror, _RUSTUP_MIRRORS):
        print(f"未知 Rust rustup 镜像源: {mirror}")
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
        镜像源名称：tsinghua/ustc/aliyun/sjtu/bfsu（已由调用方解析，不含 ``auto``）
    """
    if not mirror_supported(mirror, _CARGO_REGISTRY_MIRRORS):
        print(f"未知 Rust cargo 镜像源: {mirror}（跳过 cargo 配置，cargo 将使用官方源）")
        return

    registry = _CARGO_REGISTRY_MIRRORS[mirror]
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
        镜像源名称：rustup 支持 tsinghua/ustc/aliyun/huaweicloud/sjtu，cargo 支持
        tsinghua/ustc/aliyun/sjtu/bfsu；``auto`` 时 rustup 与 cargo 分别独立探测选优
        （rustup 分发地址与 crates.io 索引地址不同，各站可达性可能不一致，
        选优结果可能不同），全部不可达各自回退默认镜像（默认 ``auto``）
    """
    _setup_rustup_mirror(resolve_mirror("rustup", mirror, _RUSTUP_PROBE_URLS, _SERVICE_DEFAULT_MIRRORS["rustup"]))
    _setup_cargo_mirror(resolve_mirror("cargo", mirror, _CARGO_PROBE_URLS, _SERVICE_DEFAULT_MIRRORS["cargo"]))


@fcmd.tool("envdev", subcommand="download-rustup", help="下载 Rustup 安装脚本", hidden=True)
def _download_rustup() -> None:
    """下载 Rustup 安装脚本（跨平台，已安装 rustup 时跳过）。

    Linux 下载 ``rustup-init.sh``，Windows 下载 ``rustup-init.exe``。
    平台下载命令缺失时（Windows 无 powershell、Linux 无 curl）提示
    手动下载并跳过。
    """
    if shutil.which("rustup") is not None:
        print("rustup 已安装，跳过下载")
        return

    if sys.platform == "win32":
        if shutil.which("powershell") is None:
            print("未找到 powershell，请手动下载 rustup-init.exe：")
            print(f"  {_RUSTUP_DOWNLOAD_URL_WINDOWS}")
            return
        print("下载 rustup-init.exe...")
        try:
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
        except RuntimeError as e:
            print(f"下载失败: {e}，请手动下载 rustup-init.exe：")
            print(f"  {_RUSTUP_DOWNLOAD_URL_WINDOWS}")
    else:
        if shutil.which("curl") is None:
            print("未找到 curl，请手动下载 rustup-init.sh：")
            print(f"  {_RUSTUP_DOWNLOAD_URL_LINUX}")
            return
        print("下载 rustup-init.sh...")
        try:
            run_command(["curl", "-fsSL", _RUSTUP_DOWNLOAD_URL_LINUX, "-o", "rustup-init.sh"])
        except RuntimeError as e:
            print(f"下载失败: {e}，请手动下载 rustup-init.sh：")
            print(f"  {_RUSTUP_DOWNLOAD_URL_LINUX}")


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
        镜像源名称：tsinghua/ustc/aliyun/huaweicloud/bfsu；``auto`` 按服务独立自动选优
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
def _setup_bun_mirror(mirror: str = "auto") -> None:
    """配置 Bun npm 镜像源（持久化环境变量 + 写入 ``~/.bunfig.toml``）。

    通过 :func:`persist_env` 持久化 ``BUN_CONFIG_REGISTRY`` 环境变量，
    并写入 bunfig.toml 指向所选 npm 镜像源。

    Parameters
    ----------
    mirror:
        镜像源名称：npmmirror/tencent/huaweicloud；``auto`` 时探测 npm 服务各候选
        镜像（``/-/ping``）并选用最快的可达镜像，全部不可达回退 npmmirror
        （默认 ``auto``）
    """
    mirror = resolve_mirror("npm", mirror, _NPM_PROBE_URLS, _NPM_DEFAULT)
    if not mirror_supported(mirror, _NPM_REGISTRY_MIRRORS):
        print(f"未知 npm 镜像源: {mirror}")
        return

    registry = _NPM_REGISTRY_MIRRORS[mirror]
    spec = MirrorSpec(
        env_vars={"BUN_CONFIG_REGISTRY": registry},
        config_path=Path.home() / ".bunfig.toml",
        config_content=f'[install]\nregistry = "{registry}"\n',
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


def setup_js_env(mirror: str = "auto") -> None:
    """一键配置 JavaScript 开发环境（Bun npm 镜像源 + 安装 Bun.js）。

    依次执行：配置 Bun npm 镜像源（环境变量 + ``~/.bunfig.toml``）、
    安装 Bun.js（已安装时跳过）。

    Parameters
    ----------
    mirror:
        镜像源名称：npmmirror/tencent/huaweicloud；``auto`` 按 npm 服务自动选优
        （默认 ``auto``）
    """
    _setup_bun_mirror(mirror)
    _install_bun()
    persist_env("PLAYWRIGHT_DOWNLOAD_HOST", _PLAYWRIGHT_DOWNLOAD_HOST)
