"""piptool - pip 常用操作封装。

工具级别名 ``pipt`` 保持历史 pip 入口名不变。

- ``i`` / ``up``：纯 exec 子命令
- ``d``：下载包，``--offline`` 追加本地查找参数
- ``f``：导出依赖到 requirements.txt
- ``u``：通配符展开 + 受保护包过滤后卸载（空结果不执行）
- ``r``：受保护包过滤后先卸载再安装（隐藏链 ``_uninstall`` → ``_install``）
"""

from __future__ import annotations

import subprocess

import fcmd as fx
from fcmd.infras.dev import expand_packages, filter_protected

__tool_aliases__ = ["pipt"]


def _run(cmd: str | list[str], *, shell: bool = False) -> None:
    """执行命令，非零返回码转换为任务失败。"""
    result = subprocess.run(cmd, shell=shell, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"命令执行失败（退出码 {result.returncode}）: {cmd}")


def _safe_packages(packages: list[str], *, report: bool = True) -> list[str]:
    """过滤受保护包；``report`` 为真时打印逐条目跳过提示。"""
    kept, skipped = filter_protected(packages)
    if skipped and report:
        print(f"跳过受保护的包: {', '.join(skipped)}")
    return kept


@fx.tool("piptool", subcommand="i", help="安装包", cmd=["pip", "install", "{packages}"])
def install(packages: list[str]) -> None:
    """安装包。

    Parameters
    ----------
    packages:
        包名列表
    """


@fx.tool(
    "piptool",
    subcommand="up",
    help="升级 pip",
    cmd=["python", "-m", "pip", "install", "--upgrade", "pip"],
)
def upgrade_pip() -> None:
    """升级 pip。"""


@fx.tool("piptool", subcommand="d", help="下载包")
def download(packages: list[str], offline: bool = False) -> None:
    """下载包；``offline`` 为真时从本地 ``./`` 查找。

    Parameters
    ----------
    packages:
        包名列表
    offline:
        离线模式（从本地 ./ 查找包）
    """
    cmd = ["pip", "download", *packages, "-d", "packages"]
    if offline:
        cmd += ["--no-index", "--find-links", "."]
    _run(cmd)


@fx.tool("piptool", subcommand="f", help="导出依赖")
def freeze() -> None:
    """执行 ``pip freeze --exclude-editable > requirements.txt`` 并提示。"""
    _run("pip freeze --exclude-editable > requirements.txt", shell=True)
    print("依赖已导出到 requirements.txt")


@fx.tool("piptool", subcommand="u", help="卸载包（支持通配符）")
def uninstall(packages: list[str]) -> None:
    """卸载包（支持 ``requests*`` 等通配符）。

    Parameters
    ----------
    packages:
        包名列表（支持 "requests*" 等通配符）
    """
    safe = _safe_packages(expand_packages(packages))
    if not safe:
        return
    _run(["pip", "uninstall", "-y", *safe])


@fx.tool("piptool", subcommand="_uninstall", help="卸载待重装包（内部）", hidden=True)
def reinstall_uninstall(packages: list[str]) -> None:
    """卸载待重装包（过滤受保护包，空结果不执行）。"""
    safe = _safe_packages(packages)
    if not safe:
        return
    _run(["pip", "uninstall", "-y", *safe])


@fx.tool(
    "piptool",
    subcommand="_install",
    help="安装包（内部）",
    needs=["_uninstall"],
    hidden=True,
)
def reinstall_install(packages: list[str], offline: bool = False) -> None:
    """安装包（过滤受保护包，空结果不执行；``offline`` 追加本地查找参数）。"""
    safe = _safe_packages(packages, report=False)
    if not safe:
        return
    cmd = ["pip", "install", *safe]
    if offline:
        cmd += ["--no-index", "--find-links", "."]
    _run(cmd)


@fx.tool("piptool", subcommand="r", help="重装包", needs=["_install"])
def reinstall(packages: list[str], offline: bool = False) -> None:
    """重装包：先过滤受保护包，先卸载后安装；全受保护/空结果不执行。

    Parameters
    ----------
    packages:
        包名列表
    offline:
        离线模式（从本地 ./ 查找包）
    """


@fx.main("piptool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
