"""DSL 命令的 console script 通用入口。

pyproject ``[project.scripts]`` 中 DSL 命令的入口统一指向本模块
:func:`run_named`（如 ``clr = "fcmd.dsl.entry:run_named"``）：pip 生成的
console script 可执行文件名即 pyproject 键名，故从 ``sys.argv[0]`` 的
basename 可可靠推断工具名。

已知边界：直接执行 pip 生成的 ``*-script.py`` 包装脚本会得到带后缀的
``*-script`` 名（标准安装走 exe/软链入口，不受影响）。
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["infer_tool_name", "run_named"]


def infer_tool_name(argv0: str) -> str:
    """从 argv[0] 推断工具名：取 basename 去可执行扩展。

    先统一反斜杠为正斜杠再解析，确保 POSIX 系统（CI）也能正确处理
    Windows 风格路径；正斜杠路径不受影响。

    Examples
    --------
    ``C:\\Scripts\\clr.exe`` / ``/usr/local/bin/clr`` / ``clr`` → ``"clr"``
    """
    return Path(argv0.replace("\\", "/")).stem


def run_named() -> None:
    """console script 通用入口：按 argv[0] 推断工具名并转发执行。"""
    # 懒导入避免 cli ↔ dsl 包级循环（_discovery 函数内懒导入本包）
    from fcmd.apis.toolkit import run_tool
    from fcmd.cli._discovery import ensure_tools_discovered, resolve_tool
    from fcmd.console import get_console

    name = infer_tool_name(sys.argv[0])
    ensure_tools_discovered()
    resolved = resolve_tool(name)
    if resolved is None:
        # runpy 入口（fspack 打包包装器以 run_module(alter_sys=True) 执行本模块）
        # 会把 argv[0] 改写为入口模块文件路径而非加载器 exe 名；此时退回从
        # sys.executable（即 per-tool loader exe，如 img2ico.exe）推断工具名。
        resolved = resolve_tool(infer_tool_name(sys.executable))
    if resolved is None:
        console = get_console()
        console.print(f"[red]错误:[/red] 入口 {name!r} 未对应任何已注册工具")
        console.print("[dim]运行 'fcmd' 查看可用工具列表[/dim]")
        sys.exit(1)
    sys.exit(run_tool(resolved, sys.argv[1:]))


if __name__ == "__main__":
    # fspack 打包包装器经 runpy.run_module 执行本模块（无函数调用上下文），
    # 与 fcmd.cli.main 同样需要 __main__ 守卫触发入口函数。
    run_named()
