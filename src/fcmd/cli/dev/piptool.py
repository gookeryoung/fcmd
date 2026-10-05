"""piptool - pip 包管理工具。

提供卸载/重装子命令（含通配符展开、受保护包过滤等输出后处理逻辑，
保留在模块内）；纯 exec 型子命令 i/up/d/f 已迁入内置 DSL
``src/fcmd/commands/piptool.toml``，两处逐子命令合并注册。

示例
----
    fcmd piptool u requests                  # 卸载包
    fcmd piptool r --offline requests        # 离线重装
    fcmd piptool d requests                  # 下载包到 packages/
    fcmd piptool f                           # 导出依赖到 requirements.txt
"""

from __future__ import annotations

import fnmatch

import fcmd
from fcmd.models import run_command

__all__ = [
    "pip_reinstall",
    "pip_uninstall",
]

# ============================================================================
# 配置
# ============================================================================

# 受保护包名（卸载/重装时跳过，避免破坏运行环境）
_PROTECTED_PACKAGES: frozenset[str] = frozenset({"fcmd"})


# ============================================================================
# 私有辅助函数
# ============================================================================


def _get_installed_packages() -> list[str]:
    """获取当前环境中所有已安装的包名。

    Returns
    -------
    list[str]
        已安装包名列表
    """
    result = run_command(["pip", "list", "--format=freeze"], capture=True)
    packages: list[str] = []
    for line in result.stdout.strip().split("\n"):
        if line and "==" in line:
            pkg_name = line.split("==")[0].strip()
            packages.append(pkg_name)
    return packages


def _expand_wildcard_packages(pattern: str) -> list[str]:
    """展开通配符模式为实际的包名列表。

    Parameters
    ----------
    pattern:
        包名通配符模式（如 ``"requests*"``）

    Returns
    -------
    list[str]
        匹配的包名列表；无通配符时返回 ``[pattern]``
    """
    if not any(char in pattern for char in ("*", "?", "[", "]")):
        return [pattern]
    installed = _get_installed_packages()
    return [pkg for pkg in installed if fnmatch.fnmatchcase(pkg.lower(), pattern.lower())]


def _filter_protected_packages(packages: list[str]) -> list[str]:
    """过滤掉受保护的包名。

    Parameters
    ----------
    packages:
        待过滤的包名列表

    Returns
    -------
    list[str]
        安全的包名列表（不含受保护包）
    """
    protected_lower = {p.lower() for p in _PROTECTED_PACKAGES}
    safe = [p for p in packages if p.lower() not in protected_lower]
    filtered = [p for p in packages if p.lower() in protected_lower]
    if filtered:
        print(f"跳过受保护的包: {', '.join(filtered)}")
    return safe


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("piptool", subcommand="u", help="卸载包")
def pip_uninstall(packages: list[str]) -> None:
    """卸载包（支持通配符，跳过受保护包）。

    Parameters
    ----------
    packages:
        包名列表（支持 ``"requests*"`` 等通配符）
    """
    packages_to_uninstall: list[str] = []
    for pattern in packages:
        packages_to_uninstall.extend(_expand_wildcard_packages(pattern))

    packages_to_uninstall = _filter_protected_packages(packages_to_uninstall)
    if not packages_to_uninstall:
        return

    run_command(["pip", "uninstall", "-y", *packages_to_uninstall])


@fcmd.tool("piptool", subcommand="r", help="重装包")
def pip_reinstall(packages: list[str], offline: bool = False) -> None:
    """重新安装包（跳过受保护包）。

    Parameters
    ----------
    packages:
        包名列表
    offline:
        离线模式（从本地 ``./`` 查找包）
    """
    safe_ps = _filter_protected_packages(packages)
    if not safe_ps:
        print("所有指定的包均为受保护包，跳过重装")
        return

    run_command(["pip", "uninstall", "-y", *safe_ps])
    options = ["--no-index", "--find-links", "."] if offline else []
    run_command(["pip", "install", *options, *safe_ps])


@fcmd.main("piptool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
