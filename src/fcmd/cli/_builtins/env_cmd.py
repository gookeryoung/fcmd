"""``fcmd env`` 内建命令：展示当前运行环境信息。"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

from fcmd.cli._discovery import import_all_tool_modules, tool_names
from fcmd.cli._env_helpers import collect_optional_deps_status
from fcmd.console import get_console

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd env``。

    展示当前运行环境信息（只读，用于调试与问题排查）：

    - fcmd 版本与安装路径
    - Python 版本、平台、解释器路径
    - 已注册工具数与子命令总数
    - 可选依赖（img/pdf/ocr）的安装状态与版本
    """
    parser = argparse.ArgumentParser(
        prog="fcmd env",
        description="展示当前运行环境信息",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    # argv 非空但首参数为 --help 时 argparse 自动处理；空 argv 时打印信息（env 无必需参数）
    parsed = parser.parse_args(argv) if argv else parser.parse_args([])

    from fcmd import __version__ as fcmd_version

    # 触发全部工具模块导入以统计准确数字
    import_all_tool_modules()
    from fcmd.apis.toolkit import _TOOL_REGISTRY

    tool_count = len(tool_names())
    subcommand_total = sum(len(subs) for subs in _TOOL_REGISTRY.values())

    optional_deps = collect_optional_deps_status()

    env_pkg_path = Path(__file__).resolve().parent.parent.parent  # fcmd 包根
    cli_pkg_path = env_pkg_path / "cli"

    if parsed.json:
        import json as json_mod

        data = {
            "fcmd_version": fcmd_version,
            "fcmd_path": str(env_pkg_path),
            "cli_path": str(cli_pkg_path),
            "python_version": sys.version,
            "python_executable": sys.executable,
            "platform": sys.platform,
            "platform_info": platform.platform(),
            "tool_count": tool_count,
            "subcommand_total": subcommand_total,
            "optional_deps": optional_deps,
        }
        sys.stdout.write(json_mod.dumps(data, ensure_ascii=False, indent=2))
        sys.stdout.flush()
        return 0

    console = get_console()
    console.print(f"[bold cyan]fcmd v{fcmd_version}[/bold cyan] 环境信息\n")

    console.print("[bold]项目[/bold]")
    console.print(f"  fcmd 版本     [cyan]{fcmd_version}[/cyan]")
    console.print(f"  fcmd 路径     [dim]{env_pkg_path}[/dim]")
    console.print(f"  工具发现路径  [dim]{cli_pkg_path}[/dim]")

    console.print("\n[bold]运行时[/bold]")
    console.print(f"  Python 版本   [cyan]{sys.version.split()[0]}[/cyan]")
    console.print(f"  平台         [cyan]{sys.platform}[/cyan]")
    console.print(f"  平台信息     [dim]{platform.platform()}[/dim]")
    console.print(f"  解释器       [dim]{sys.executable}[/dim]")

    console.print("\n[bold]工具[/bold]")
    console.print(f"  已注册工具数  [cyan]{tool_count}[/cyan]")
    console.print(f"  已注册子命令  [cyan]{subcommand_total}[/cyan]")

    console.print("\n[bold]可选依赖[/bold]")
    if not optional_deps:
        console.print("  [dim](无)[/dim]")
    else:
        from fcmd.console import Table

        table = Table(show_header=True, header_style="bold", show_lines=False)
        table.add_column("extra", style="cyan", no_wrap=True)
        table.add_column("包名", no_wrap=True)
        table.add_column("状态", justify="center", no_wrap=True)
        table.add_column("版本")
        for dep in optional_deps:
            status_str = "[green]已安装[/green]" if dep["installed"] else "[red]未安装[/red]"
            table.add_row(dep["extra"], dep["package"], status_str, dep.get("version", ""))
        console.print(table)

    console.print("\n[dim]提示: 运行 'fcmd doctor' 进行环境健康检查[/dim]")
    return 0
