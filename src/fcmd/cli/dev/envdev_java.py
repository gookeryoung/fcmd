"""envdev_java - Java 开发环境配置（Maven 镜像 + JDK）。

配置 Maven settings.xml 镜像仓库，并可选安装 SDKMAN（用于管理 JDK 版本）。
国内主流 Maven 镜像：阿里云、华为云。
"""

from __future__ import annotations

import sys
from pathlib import Path

import fcmd
from fcmd.cli.dev.envdev_core import MirrorSpec, apply_mirror_config, mirror_supported
from fcmd.models import run_command

__all__ = ["setup_java_env"]

_MAVEN_MIRRORS: dict[str, str] = {
    "aliyun": "https://maven.aliyun.com/repository/public",
    "huaweicloud": "https://repo.huaweicloud.com/repository/maven",
    "ustc": "https://mirrors.ustc.edu.cn/apache/maven/repository/public",
}

_MAVEN_SETTINGS_PATH: Path = Path.home() / ".m2" / "settings.xml"

_SDKMAN_INSTALL_URL: str = "https://get.sdkman.io"
_SDKMAN_CANDIDATE_URL: str = "https://get.sdkman.io/candidates"

_MAVEN_SETTINGS_TEMPLATE: str = """<?xml version="1.0" encoding="UTF-8"?>
<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.0.0
                              http://maven.apache.org/xsd/settings-1.0.0.xsd">
  <mirrors>
    <mirror>
      <id>custom-mirror</id>
      <mirrorOf>*</mirrorOf>
      <url>{mirror_url}</url>
    </mirror>
  </mirrors>
</settings>
"""


# ============================================================================
# 镜像源配置
# ============================================================================


@fcmd.tool("envdev", subcommand="setup-maven", help="配置 Maven 镜像源", hidden=True)
def _setup_maven_mirror(mirror: str = "aliyun") -> None:
    """配置 Maven 镜像源（写入 ~/.m2/settings.xml）。

    Parameters
    ----------
    mirror:
        镜像源名称：aliyun / huaweicloud / ustc（默认 aliyun）
    """
    if not mirror_supported(mirror, _MAVEN_MIRRORS):
        print(f"未知 Maven 镜像源: {mirror}")
        return

    mirror_url = _MAVEN_MIRRORS[mirror]
    content = _MAVEN_SETTINGS_TEMPLATE.format(mirror_url=mirror_url)

    spec = MirrorSpec(
        config_path=_MAVEN_SETTINGS_PATH,
        config_content=content,
    )
    apply_mirror_config(spec, label=f"Maven ({mirror})")


# ============================================================================
# 工具链安装
# ============================================================================


@fcmd.tool("envdev", subcommand="install-sdkman", help="安装 SDKMAN (Java 版本管理)", hidden=True)
def _install_sdkman() -> None:
    """安装 SDKMAN（已安装时跳过）。

    Linux/macOS 通过官方脚本安装；Windows 提示手动安装。
    """
    if (Path.home() / ".sdkman").is_dir():
        print("SDKMAN 已安装，跳过")
        return

    if sys.platform == "win32":
        print("Windows 请参考 https://sdkman.io 手动安装")
        return

    print("安装 SDKMAN...")
    run_command(["bash", "-c", f"curl -fsSL {_SDKMAN_INSTALL_URL} | bash"])
    print("SDKMAN 安装完成（重开终端后生效）")


# ============================================================================
# 一键命令（由 envdev lang java 路由调用）
# ============================================================================


def setup_java_env(mirror: str = "aliyun", install_sdkman: bool = False) -> None:
    """一键配置 Java 开发环境。

    依次执行：配置 Maven 镜像源、（可选）安装 SDKMAN。

    Parameters
    ----------
    mirror:
        镜像源名称：aliyun / huaweicloud / ustc（默认 aliyun）
    install_sdkman:
        是否同时安装 SDKMAN（默认 False）
    """
    _setup_maven_mirror(mirror)
    if install_sdkman:
        _install_sdkman()
