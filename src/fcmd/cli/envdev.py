"""envdev - 开发环境镜像源配置工具。

配置 Python / Conda / Rust 镜像源（Linux 还会安装 Qt 库、中文字体、Docker）。
所有镜像源参数互不影响，可单独使用。Linux 专用操作（系统镜像/Qt/字体/Docker）
在非 Linux 平台上由函数内部跳过。

示例
----
    fcmd envdev setup-python                      # 配置 Python 镜像源（默认清华）
    fcmd envdev setup-python --mirror aliyun      # 配置阿里云镜像源
    fcmd envdev setup-conda --mirror ustc         # 配置 Conda 镜像源
    fcmd envdev setup-rust --mirror tsinghua      # 配置 Rust 镜像源
    fcmd envdev download-rustup                   # 下载 Rustup 安装脚本
    fcmd envdev install-rust --version stable     # 安装 Rust 工具链
"""

from __future__ import annotations

import getpass
import os
import shutil
import sys
from pathlib import Path

import fcmd
from fcmd.models import run_command

__all__ = [
    "download_rustup_script",
    "install_linux_docker",
    "install_linux_fonts",
    "install_linux_qt_libs",
    "install_rust_toolchain",
    "setup_conda_mirror",
    "setup_linux_system_mirror",
    "setup_python_mirror",
    "setup_rust_mirror",
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
    "bsfu": [
        "https://mirrors.bsfu.edu.cn/anaconda/pkgs/main/",
        "https://mirrors.bsfu.edu.cn/anaconda/pkgs/free/",
        "https://mirrors.bsfu.edu.cn/anaconda/pkgs/r/",
        "https://mirrors.bsfu.edu.cn/anaconda/pkgs/msys2/",
        "https://mirrors.bsfu.edu.cn/anaconda/pkgs/pro/",
        "https://mirrors.bsfu.edu.cn/anaconda/pkgs/dev/",
        "https://mirrors.bsfu.edu.cn/anaconda/cloud/conda-forge/",
        "https://mirrors.bsfu.edu.cn/anaconda/cloud/bioconda/",
        "https://mirrors.bsfu.edu.cn/anaconda/cloud/menpo/",
        "https://mirrors.bsfu.edu.cn/anaconda/cloud/pytorch/",
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


# ============================================================================
# 私有辅助
# ============================================================================


def _pip_config_path() -> Path:
    """返回当前平台的 pip 配置文件路径。"""
    if sys.platform.startswith("linux"):
        return Path.home() / ".pip" / "pip.conf"
    return Path.home() / "pip" / "pip.ini"


# ============================================================================
# 镜像源配置子命令
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-python", help="配置 Python 镜像源")
def setup_python_mirror(mirror: str = "tsinghua") -> None:
    """配置 Python 镜像源（设置环境变量 + 写入 pip 配置文件）。

    设置 ``PIP_INDEX_URL`` / ``PIP_TRUSTED_HOSTS`` / ``UV_INDEX_URL`` /
    ``UV_PYTHON_INSTALL_MIRROR`` 等环境变量，并写入 pip 配置文件。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/aliyun/huaweicloud/ustc/zju（默认 tsinghua）
    """
    if mirror not in _PIP_INDEX_URLS:
        print(f"未知 Python 镜像源: {mirror}")
        return

    index_url = _PIP_INDEX_URLS[mirror]
    trusted_host = _PIP_TRUSTED_HOSTS[mirror]

    os.environ["PIP_INDEX_URL"] = index_url
    os.environ["PIP_TRUSTED_HOSTS"] = trusted_host
    os.environ["UV_INDEX_URL"] = index_url
    os.environ["UV_PYTHON_INSTALL_MIRROR"] = _UV_PYTHON_INSTALL_MIRROR
    os.environ["UV_HTTP_TIMEOUT"] = "600"
    os.environ["UV_LINK_MODE"] = "copy"

    config_path = _pip_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = f"[global]\nindex-url = {index_url}\ntrusted-host = {trusted_host}\n"
    config_path.write_text(content, encoding="utf-8")
    print(f"Python 镜像源已配置: {mirror} -> {config_path}")


@fcmd.tool("envdev", subcommand="setup-conda", help="配置 Conda 镜像源")
def setup_conda_mirror(mirror: str = "tsinghua") -> None:
    """配置 Conda 镜像源（写入 ~/.condarc）。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/ustc/bsfu/aliyun（默认 tsinghua）
    """
    if mirror not in _CONDA_MIRROR_URLS:
        print(f"未知 Conda 镜像源: {mirror}")
        return

    urls = _CONDA_MIRROR_URLS[mirror]
    config_path = Path.home() / ".condarc"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = "show_channel_urls: true\nchannels:\n  - " + "\n  - ".join(urls) + "\n  - defaults\n"
    config_path.write_text(content, encoding="utf-8")
    print(f"Conda 镜像源已配置: {mirror} -> {config_path}")


@fcmd.tool("envdev", subcommand="setup-rust", help="配置 Rust 镜像源")
def setup_rust_mirror(mirror: str = "tsinghua") -> None:
    """配置 Rust 镜像源（设置环境变量 + 写入 cargo config + 创建 sccache 目录）。

    设置 ``RUSTUP_DIST_SERVER`` / ``RUSTUP_UPDATE_ROOT`` / ``RUST_SCCACHE_DIR``
    等环境变量，写入 ``~/.cargo/config.toml``，并创建 sccache 缓存目录。

    Parameters
    ----------
    mirror:
        镜像源名称：tsinghua/ustc/aliyun（默认 tsinghua）
    """
    if mirror not in _RUSTUP_MIRRORS:
        print(f"未知 Rust 镜像源: {mirror}")
        return

    mirrors = _RUSTUP_MIRRORS[mirror]
    os.environ["RUSTUP_DIST_SERVER"] = mirrors["RUSTUP_DIST_SERVER"]
    os.environ["RUSTUP_UPDATE_ROOT"] = mirrors["RUSTUP_UPDATE_ROOT"]
    os.environ["RUST_SCCACHE_DIR"] = str(_RUST_SCCACHE_DIR)
    os.environ["RUST_SCCACHE_CACHE_SIZE"] = _RUST_SCCACHE_CACHE_SIZE

    _RUST_SCCACHE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = Path.home() / ".cargo" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    registry = mirrors["TOML_REGISTRY"]
    content = (
        f"\n[source.crates-io]\nreplace-with = '{mirror}'\n\n"
        f'[source.{mirror}]\nregistry = "sparse+{registry}"\n\n'
        f'[registries.{mirror}]\nindex = "sparse+{registry}"\n'
    )
    config_path.write_text(content, encoding="utf-8")
    print(f"Rust 镜像源已配置: {mirror} -> {config_path}")


# ============================================================================
# Rust 工具链安装
# ============================================================================


@fcmd.tool("envdev", subcommand="download-rustup", help="下载 Rustup 安装脚本")
def download_rustup_script() -> None:
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


@fcmd.tool("envdev", subcommand="install-rust", help="安装 Rust 工具链")
def install_rust_toolchain(version: str = "stable") -> None:
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


# ============================================================================
# Linux 专用子命令
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-linux-mirror", help="配置 Linux 系统镜像源")
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


@fcmd.tool("envdev", subcommand="install-qt-libs", help="安装 Qt 依赖库")
def install_linux_qt_libs() -> None:
    """安装 Qt 依赖库（仅 Linux）。"""
    if not sys.platform.startswith("linux"):
        print("install_linux_qt_libs: 仅在 Linux 上支持")
        return

    run_command(["sudo", "apt", "install", "-y", *_QT_LIBS])
    print("Qt 依赖库安装完成")


@fcmd.tool("envdev", subcommand="install-fonts", help="安装中文字体")
def install_linux_fonts() -> None:
    """安装中文字体（仅 Linux）。"""
    if not sys.platform.startswith("linux"):
        print("install_linux_fonts: 仅在 Linux 上支持")
        return

    run_command(["sudo", "apt", "install", "-y", *_CHINESE_FONTS])
    print("中文字体安装完成")


@fcmd.tool("envdev", subcommand="install-docker", help="安装 Docker")
def install_linux_docker() -> None:
    """安装 Docker（仅 Linux）。"""
    if not sys.platform.startswith("linux"):
        print("install_linux_docker: 仅在 Linux 上支持")
        return

    run_command(["sudo", "apt", "install", "-y", "docker-compose-v2"])
    run_command(["sudo", "usermod", "-aG", "docker", getpass.getuser()])
    print("Docker 安装完成（需重新登录以生效 docker 用户组）")
