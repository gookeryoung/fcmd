"""fcmd CLI 主入口：FcmdApp 路由。

通过 ``fcmd <tool> [command] [options]`` 调用所有工具，
工具定义在 ``fcmd.cli`` 包中，每个模块用 ``@fx.tool`` 装饰器注册。

职责划分
--------
- 工具发现与注册表查询：:mod:`fcmd.cli._discovery`
- 内建命令（graph/info/completion/yaml/env/doctor/profiler）实现：
  :mod:`fcmd.cli._builtins` 子包
- 本模块：命令行路由（``FcmdApp.run``）、工具列表展示与入口 ``main``

工具发现
--------
``fcmd.cli`` 包下每个非 ``main`` / 非 ``_`` 前缀的模块即一个工具，
模块名即工具名。模块内可选定义 ``__tool_aliases__: list[str]`` 声明别名。
首次调用 ``FcmdApp.run()`` 时扫描并导入所有工具模块，
``import fcmd`` 冷启动不受影响。

用法
----
    fcmd                  # 列出所有可用工具
    fcmd pymake           # 查看 pymake 工具的子命令
    fcmd pymake b         # 调用 pymake 的 b 子命令
    fcmd --version        # 输出版本号
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence

from fcmd import __version__
from fcmd.apis.toolkit import run_tool
from fcmd.cli._builtins import run_builtin
from fcmd.cli._common import _BUILTIN_COMMANDS, print_unknown_tool
from fcmd.cli._discovery import (
    _TOOL_ALIASES,
    _TOOL_MODULES,
    aliases_for,
    ensure_tools_discovered,
    resolve_tool,
    tool_description,
)
from fcmd.console import get_console

__all__ = ["FcmdApp", "main"]


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器（保留 P0 兼容）。"""
    parser = argparse.ArgumentParser(
        prog="fcmd",
        description="极速 Python 工具集应用：DAG 任务调度 + 组合 CLI。",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


class FcmdApp:
    """fcmd 统一入口应用。

    路由 ``fcmd <tool> [command]`` 到 ``@fx.tool`` 注册的工具，
    内建命令分发到 :mod:`fcmd.cli._builtins`。
    """

    def __init__(self, argv: Sequence[str] | None = None) -> None:
        self._argv: list[str] = list(argv) if argv is not None else sys.argv[1:]

    def run(self) -> int:
        """主入口，返回退出码。"""
        ensure_tools_discovered()
        if not self._argv or self._argv[0] in ("--help", "-h"):
            self._list_tools()
            return 0

        first = self._argv[0]
        if first in ("--version", "-V"):
            get_console().print(f"fcmd [bold cyan]{__version__}[/bold cyan]")
            return 0

        # 内建命令（graph/info/...）优先于工具路由
        if first in _BUILTIN_COMMANDS:
            return run_builtin(first, self._argv[1:])

        rest = self._argv[1:]
        resolved = resolve_tool(first)
        if resolved is None:
            print_unknown_tool(first)
            return 1

        return self._run_tool(resolved, rest)

    def _list_tools(self) -> None:
        """列出所有可用工具。"""
        from fcmd.console import Table

        console = get_console()
        console.print(f"[bold cyan]fcmd v{__version__}[/bold cyan]")
        console.print("[dim]fcmd <tool> [command] [options][/dim]\n")

        table = Table(title="可用工具", show_header=True, header_style="bold", show_lines=False)
        table.add_column("命令", style="cyan", no_wrap=True)
        table.add_column("别名", style="dim", no_wrap=True)
        table.add_column("说明")

        for tool_name in sorted(set(_TOOL_ALIASES.values())):
            aliases = aliases_for(tool_name)
            table.add_row(f"fcmd {tool_name}", ", ".join(aliases), tool_description(tool_name))

        console.print(table)

        console.print("\n[bold]示例:[/bold]")
        console.print("  [cyan]fcmd pymake[/cyan]              # 查看 pymake 子命令")
        console.print("  [cyan]fcmd pymake b[/cyan]            # 构建项目")
        console.print("  [cyan]fcmd pymake tc[/cyan]           # 类型检查（聚合）")
        console.print("  [cyan]fcmd info pymake[/cyan]         # 查看 pymake 元信息")
        console.print("  [cyan]fcmd graph pymake tc[/cyan]     # 可视化 DAG（Mermaid）")
        console.print("  [cyan]fcmd env[/cyan]                 # 查看环境信息")
        console.print("  [cyan]fcmd doctor[/cyan]              # 环境健康诊断")
        console.print("  [cyan]fcmd --version[/cyan]           # 查看版本")

    def _run_tool(self, tool_name: str, argv: list[str]) -> int:
        """运行工具：importlib 懒加载模块触发 @tool 注册，再调 run_tool。"""
        module_path = _TOOL_MODULES.get(tool_name)
        if module_path is None:
            get_console().print(f"[red]错误:[/red] 工具 {tool_name!r} 无模块映射")
            return 1

        try:
            importlib.import_module(module_path)
        except ImportError as e:
            get_console().print(f"[red]错误:[/red] 加载工具 {tool_name!r} 失败: {e}")
            return 1

        return run_tool(tool_name, argv)


def main() -> None:
    """主入口：解析参数并执行。"""
    sys.exit(FcmdApp().run())


if __name__ == "__main__":
    main()
