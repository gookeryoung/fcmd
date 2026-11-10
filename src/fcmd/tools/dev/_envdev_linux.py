"""envdev 的 Linux 专用命令（系统镜像 / Qt 依赖 / 字体 / Docker / OpenSSH / 远程桌面）。

从 :mod:`fcmd.tools.dev.envdev` 拆出；``envdev`` 仍 re-export 全部函数与常量，
既有导入路径与 CLI 注册不变。
"""

from __future__ import annotations

import getpass
import json
import shutil
import sys
from pathlib import Path

import fcmd
from fcmd.core.models import run_command
from fcmd.core.shared.net import check_urls
from fcmd.tools.dev._envdev_langs import _PIP_INDEX_URLS

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
# Docker 镜像加速候选（探测可达性后按速度写入 daemon.json）
_DOCKER_REGISTRY_MIRRORS: list[str] = [
    "https://registry.cn-hangzhou.aliyuncs.com",
    "https://docker.m.daocloud.io",
    "https://hub-mirror.c.163.com",
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://mirror.ccs.tencentyun.com",
]
_DOCKER_DAEMON_PATH: Path = Path("/etc/docker/daemon.json")
_GNOME_REMOTE_DESKTOP_PKGS: list[str] = [
    "gnome-remote-desktop",
]

_XFCE_DESKTOP_PKGS: list[str] = [
    "xfce4",
    "xfce4-goodies",
    "xorgxrdp",
]

_XSESSION_XFCE4: str = "xfce4-session\n"
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
