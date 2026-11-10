"""envdev - 开发环境镜像源配置工具。

子命令整合为五组操作入口：

- ``lang <语言>``：语言类一键配置（python/js/rust/go/java/node），镜像默认 ``auto``
  按服务（pip/conda/rustup/cargo/go/maven/npm）独立探测选优
- ``app <应用>``：应用/系统类一键配置（linux-mirror/qt-libs/fonts/docker/docker-mirror/openssh/remote）
- ``mirror``：探测教育网镜像站点；指定 ``--service`` 时按服务测速并可选自动应用最快镜像
- ``check``：检测开发环境配置状态（工具链 + 镜像源，只读）
- ``all``：一键配置所有环境

细粒度步骤命令（setup-* / install-*）为隐藏子命令，可单独调用也可由
分组命令与一键命令编排。

本模块是门面层：分组路由（lang / app / all / check）在此定义；语言级步骤在
:mod:`fcmd.tools.dev._envdev_langs`，Linux 专用命令在
:mod:`fcmd.tools.dev._envdev_linux`，镜像站点探测在
:mod:`fcmd.tools.dev._envdev_mirrors`，公共辅助提取自
:mod:`fcmd.tools.dev.envdev_core`。各子模块在导入时完成 ``fcmd.tool`` 注册，
本模块 re-export 其公开函数与常量，既有导入路径不变。

示例
----
    fcmd envdev lang python                      # 自动探测并选用最快的 Python 镜像源
    fcmd envdev lang python --mirror tsinghua    # 指定镜像源配置 Python 环境（pip/uv + Conda）
    fcmd envdev lang js                          # 一键配置 JavaScript 环境（Bun）
    fcmd envdev lang rust --mirror tsinghua nightly  # 一键配置 Rust 环境（镜像源 + 工具链）
    fcmd envdev mirror                           # 探测教育网镜像站点可访问性与速度
    fcmd envdev mirror --service pip             # pip 候选镜像测速并推荐最快项
    fcmd envdev mirror --service npm --apply     # npm 测速并自动应用最快镜像
    fcmd envdev app remote                       # 一键配置 Linux 远程桌面（xrdp + Xfce）
    fcmd envdev check                            # 检测开发环境配置状态
    fcmd envdev all                              # 一键配置所有环境
"""

from __future__ import annotations

import os
import shutil
from typing import Literal

import fcmd

# 门面层 re-export：保留拆分前的 ``envdev.<name>`` 访问路径（含私有常量 / 步骤函数），
# 子模块在导入时完成 ``fcmd.tool`` 注册。
from fcmd.core.env_persist import persist_env  # noqa: F401
from fcmd.core.models import run_command  # noqa: F401
from fcmd.core.shared.net import check_urls, probe_mirrors  # noqa: F401
from fcmd.tools.dev._envdev_langs import (  # noqa: F401
    _BUN_INSTALL_SCRIPT_URL,
    _CARGO_PROBE_URLS,
    _CARGO_REGISTRY_MIRRORS,
    _CONDA_MIRROR_URLS,
    _CONDA_PROBE_URLS,
    _PIP_INDEX_URLS,
    _PIP_PROBE_URLS,
    _PIP_TRUSTED_HOSTS,
    _PLAYWRIGHT_DOWNLOAD_HOST,
    _RUST_SCCACHE_CACHE_SIZE,
    _RUST_SCCACHE_DIR,
    _RUSTUP_DOWNLOAD_URL_LINUX,
    _RUSTUP_DOWNLOAD_URL_WINDOWS,
    _RUSTUP_MIRRORS,
    _RUSTUP_PROBE_URLS,
    _SERVICE_DEFAULT_MIRRORS,
    _UV_PYTHON_INSTALL_MIRROR,
    _download_rustup,
    _install_bun,
    _install_rust_toolchain,
    _setup_bun_mirror,
    _setup_cargo_mirror,
    _setup_rust_mirror,
    _setup_rustup_mirror,
    setup_conda_mirror,
    setup_js_env,
    setup_python_env,
    setup_python_mirror,
    setup_rust_env,
)
from fcmd.tools.dev._envdev_linux import (  # noqa: F401
    _CHINESE_FONTS,
    _DOCKER_DAEMON_PATH,
    _DOCKER_REGISTRY_MIRRORS,
    _DOWNLOAD_MIRROR_SCRIPT,
    _GNOME_REMOTE_DESKTOP_PKGS,
    _INSTALL_MIRROR_SCRIPT,
    _QT_LIBS,
    _XFCE_DESKTOP_PKGS,
    _XSESSION_XFCE4,
    _configure_lightdm,
    _install_xfce_desktop,
    _install_xrdp,
    _uninstall_gnome_remote_desktop,
    install_linux_docker,
    install_linux_fonts,
    install_linux_openssh,
    install_linux_qt_libs,
    setup_docker_mirror,
    setup_linux_remote,
    setup_linux_system_mirror,
)
from fcmd.tools.dev._envdev_mirrors import (  # noqa: F401
    _CERNET_MIRROR_SITES,
    _MIRROR_SERVICE_APPLY,
    _MIRROR_SERVICE_CANDIDATES,
    _MIRRORZ_HOME,
    _MIRRORZ_JS_PATH_RE,
    _MIRRORZ_PORTAL_URL,
    _MIRRORZ_SITE_BLOB_RE,
    _probe_service_mirrors,
    check_cernet_mirrors,
    fetch_mirrorz_sites,
)
from fcmd.tools.dev.envdev_core import (  # noqa: F401
    _MIRROR_DEFAULT_UA,
    _MIRROR_USER_AGENTS,
    MirrorSpec,
    apply_mirror_config,
    mirror_supported,
    pip_config_path,
    resolve_mirror,
)
from fcmd.tools.dev.envdev_go import _GO_PROXY_PROBE_URLS, _setup_go_mirror, setup_go_env  # noqa: F401
from fcmd.tools.dev.envdev_java import _MAVEN_PROBE_URLS, _setup_maven_mirror, setup_java_env  # noqa: F401
from fcmd.tools.dev.envdev_node import (  # noqa: F401
    _NPM_DEFAULT,
    _NPM_PROBE_URLS,
    _NPM_REGISTRY_MIRRORS,
    _setup_npm_mirror,
    setup_node_env,
)

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
# 分组路由（lang / app）
# ============================================================================


@fcmd.tool("envdev", subcommand="lang", help="语言类一键配置（python/js/rust/go/java/node）")
def setup_lang_env(  # noqa: PLR0913, PLR0917  CLI 参数需全量透传给各语言一键命令
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

    镜像源默认 ``auto``：各服务（pip/conda/rustup/cargo/go/maven/npm）在配置前
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
        setup_js_env(mirror)
    elif language == "rust":
        setup_rust_env(mirror, rust_version)
    elif language == "go":
        setup_go_env(mirror, install_gvm)
    elif language == "java":
        setup_java_env(mirror, install_sdkman)
    else:
        setup_node_env(mirror, install_nvm)


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
    setup_js_env(mirror)
    setup_rust_env(mirror, rust_version)
    setup_go_env(mirror)
    setup_java_env(mirror)
    setup_node_env(mirror)
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
