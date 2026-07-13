"""fcmd CLI 主入口：FcmdApp 路由。

通过 ``fcmd <tool> [command] [options]`` 调用所有工具，
工具定义在 ``fcmd.cli`` 包中，每个模块用 ``@fx.tool`` 装饰器注册。

用法
----
    fcmd                  # 列出所有可用工具
    fcmd pymake           # 查看 pymake 工具的子命令
    fcmd pymake b         # 调用 pymake 的 b 子命令
    fcmd --version        # 输出版本号
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import sys
from collections.abc import Sequence

from fcmd import __version__
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.console import get_console

__all__ = ["FcmdApp", "main"]


# 工具别名 → 规范名
_TOOL_ALIASES: dict[str, str] = {
    "pymake": "pymake",
    "pm": "pymake",
}

# 规范工具名 → 模块路径
_TOOL_MODULES: dict[str, str] = {
    "pymake": "fcmd.cli.pymake",
}


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

    路由 ``fcmd <tool> [command]`` 到 ``@fx.tool`` 注册的工具。
    """

    def __init__(self, argv: Sequence[str] | None = None) -> None:
        self._argv: list[str] = list(argv) if argv is not None else sys.argv[1:]

    def run(self) -> int:
        """主入口，返回退出码。"""
        if not self._argv or self._argv[0] in ("--help", "-h"):
            self._list_tools()
            return 0

        first = self._argv[0]
        if first in ("--version", "-V"):
            get_console().print(f"fcmd [bold cyan]{__version__}[/bold cyan]")
            return 0

        rest = self._argv[1:]
        resolved = self._resolve_tool(first)
        if resolved is None:
            self._print_unknown_tool(first)
            return 1

        return self._run_tool(resolved, rest)

    # ------------------------------------------------------------------ #
    # 工具列表 (rich)
    # ------------------------------------------------------------------ #
    def _list_tools(self) -> None:
        """rich 表格列出所有可用工具。"""
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        console = get_console()
        console.print(
            Panel(
                Text(f"fcmd v{__version__}", style="bold cyan", justify="center"),
                subtitle="[dim]fcmd <tool> [command] [options][/dim]",
            )
        )

        table = Table(title="可用工具", show_header=True, header_style="bold", show_lines=False)
        table.add_column("命令", style="cyan", no_wrap=True)
        table.add_column("别名", style="dim", no_wrap=True)
        table.add_column("说明")

        for tool_name in sorted(set(_TOOL_ALIASES.values())):
            aliases = self._aliases_for(tool_name)
            table.add_row(f"fcmd {tool_name}", ", ".join(aliases), self._tool_description(tool_name))

        console.print(table)

        console.print("\n[bold]示例:[/bold]")
        console.print("  [cyan]fcmd pymake[/cyan]              # 查看 pymake 子命令")
        console.print("  [cyan]fcmd pymake b[/cyan]            # 构建项目")
        console.print("  [cyan]fcmd pymake tc[/cyan]           # 类型检查（聚合）")
        console.print("  [cyan]fcmd --version[/cyan]           # 查看版本")

    def _aliases_for(self, canonical: str) -> list[str]:
        """获取工具的别名（不含规范名本身）。"""
        return sorted(a for a, t in _TOOL_ALIASES.items() if t == canonical and a != canonical)

    def _tool_description(self, tool_name: str) -> str:
        """获取工具描述（从 _TOOL_REGISTRY 中已注册 ToolSpec 的 description/help）。"""
        # 触发模块导入以注册工具
        if tool_name in _TOOL_MODULES:
            try:
                importlib.import_module(_TOOL_MODULES[tool_name])
            except ImportError:
                return ""

        if tool_name not in _TOOL_REGISTRY:
            return ""

        subs = _TOOL_REGISTRY[tool_name]
        for spec in subs.values():
            if spec.description:
                return spec.description
        for spec in subs.values():
            if not spec.hidden and spec.help:
                return spec.help
        return ""

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #
    def _resolve_tool(self, name: str) -> str | None:
        """解析工具名，返回规范名或 None。"""
        return _TOOL_ALIASES.get(name)

    def _print_unknown_tool(self, name: str) -> None:
        """打印未知工具错误 + 模糊匹配建议。"""
        console = get_console()
        console.print(f"[red]错误:[/red] 未知工具 [yellow]{name!r}[/yellow]")
        suggestions = difflib.get_close_matches(name, list(_TOOL_ALIASES), n=3, cutoff=0.5)
        if suggestions:
            console.print(f"[dim]是否想用: {', '.join(suggestions)}[/dim]")
        console.print("[dim]运行 'fcmd' 查看可用工具列表[/dim]")

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
