"""taskkill - 进程终止工具。

跨平台按名称终止进程：Windows 用 ``taskkill``，Linux/macOS 用 ``pkill``。

示例
----
    fcmd taskkill chrome.exe python
    fcmd taskkill node
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import PureWindowsPath

import fcmd

__all__ = [
    "kill_process",
    "taskkill_run",
]


def _system_taskkill_path() -> str:
    """返回系统 ``taskkill.exe`` 绝对路径。

    必须使用绝对路径调用系统 taskkill.exe，避免 fcmd 自身注册的 ``taskkill``
    entry script（``pip install`` 生成于 Python Scripts 目录）在 PATH 中
    优先于 ``C:\\Windows\\System32\\taskkill.exe``，导致 ``subprocess.run``
    递归调用 fcmd taskkill 自身，指数级进程爆炸直至系统资源耗尽。

    使用 ``PureWindowsPath`` 而非 ``Path``：本模块仅在 ``sys.platform == 'win32'``
    分支调用，但 CI 在 Linux 上运行时会 monkeypatch ``sys.platform``，此时
    ``Path`` 会退化为 ``PosixPath``，把 ``C:\\Windows`` 当作单一组件用 ``/``
    拼接，产生混合分隔符路径。``PureWindowsPath`` 跨平台一致使用反斜杠。
    """
    # Windows 环境变量大小写不敏感，SystemRoot 是系统约定写法
    system_root = os.environ.get("SystemRoot", r"C:\Windows")  # noqa: SIM112
    return str(PureWindowsPath(system_root) / "System32" / "taskkill.exe")


def kill_process(process_name: str) -> int:
    """终止匹配名称的进程（跨平台）。

    Windows 使用 ``taskkill /f /fi "imagename eq <name>*"``（``/FI`` 过滤器
    支持通配符，兼容 Win7；``/IM <name>*`` 部分通配符仅在 Win10+ 支持），
    Linux/macOS 使用 ``pkill -f <name>*``。

    Parameters
    ----------
    process_name:
        进程名称（自动追加 ``*`` 通配符）

    Returns
    -------
    int
        命令返回码：``0`` 表示已发送终止信号，``1`` 表示未找到匹配进程，
        其他值表示终止失败。
    """
    if sys.platform == "win32":
        # 用 /FI 过滤器替代 /IM 通配符：Win7 的 /IM 不支持部分通配符
        cmd = [_system_taskkill_path(), "/f", "/fi", f"imagename eq {process_name}*"]
    else:
        cmd = ["pkill", "-f", f"{process_name}*"]

    # pkill 返回 1 表示无匹配进程（非错误），故 check=False + 手动检查 returncode
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return result.returncode


@fcmd.tool("taskkill", help="按名称终止进程（跨平台）")
def taskkill_run(process_names: list[str]) -> None:
    """按名称终止进程（跨平台）。

    Windows 使用 ``taskkill /f /fi "imagename eq <name>*"``（``/FI`` 过滤器
    支持通配符，兼容 Win7），Linux/macOS 使用 ``pkill -f <name>*``。

    Parameters
    ----------
    process_names:
        进程名称列表（如 ``["chrome.exe", "python"]``）
    """
    for name in process_names:
        print(f"终止进程: {name}")
        returncode = kill_process(name)
        if returncode == 0:
            print(f"  已发送终止信号: {name}")
        else:
            print(f"  未找到匹配进程或终止失败 (returncode={returncode}): {name}")


@fcmd.main("taskkill")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
