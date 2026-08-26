"""``fcmd info`` 内建命令：展示工具或子命令的元信息。"""

from __future__ import annotations

import argparse
from typing import Any

from fcmd.cli._common import _BUILTIN_COMMANDS, print_unknown_tool
from fcmd.cli._discovery import aliases_for, import_all_tool_modules, load_tool_subs, resolve_tool, tool_description
from fcmd.cli._discovery import tool_names as _tool_names
from fcmd.console import get_console

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd info <tool> [subcommand]``。

    展示工具或子命令的元信息（不执行）：

    - ``fcmd info``：列出全部内建命令与已注册工具
    - ``fcmd info <tool>``：列出工具的所有子命令（含 hidden）及
      help / needs / strategy / cmd 摘要
    - ``fcmd info <tool> <subcommand>``：展示该子命令的完整 ToolSpec
      字段（cmd / needs / strategy / cwd / hidden / env / retry / timeout）
    """
    parser = argparse.ArgumentParser(
        prog="fcmd info",
        description="展示工具或子命令的元信息",
    )
    parser.add_argument("tool", nargs="?", default=None, help="工具名（如 pymake）")
    parser.add_argument("subcommand", nargs="?", default=None, help="子命令名（如 tc）")
    if not argv:
        _info_overview()
        return 0
    parsed = parser.parse_args(argv)

    resolved = resolve_tool(parsed.tool)
    if resolved is None:
        print_unknown_tool(parsed.tool)
        return 1

    subs = load_tool_subs(resolved)
    if subs is None:
        return 1

    if parsed.subcommand is None:
        _info_tool(resolved, subs)
        return 0

    # 子命令详情：subcommand 可能是 None（单命令工具），用 .get 兼容
    spec = subs.get(parsed.subcommand)
    if spec is None:
        get_console().print(f"[red]错误:[/red] 工具 {resolved!r} 没有子命令 {parsed.subcommand!r}")
        return 1
    _info_subcommand(resolved, spec)
    return 0


def _info_overview() -> None:
    """``fcmd info`` 无参数：列出内建命令与工具。"""
    from fcmd.console import Table

    console = get_console()
    console.print("[bold]内建命令:[/bold]")
    for cmd_name in _BUILTIN_COMMANDS:
        console.print(f"  [cyan]fcmd {cmd_name}[/cyan]")

    console.print("\n[bold]已注册工具:[/bold]")
    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("工具", style="cyan", no_wrap=True)
    table.add_column("别名", style="dim", no_wrap=True)
    table.add_column("子命令数", justify="right", no_wrap=True)
    table.add_column("说明")
    import_all_tool_modules()
    from fcmd.apis.toolkit import _TOOL_REGISTRY

    for tool_name in _tool_names():
        aliases = aliases_for(tool_name)
        subs = _TOOL_REGISTRY.get(tool_name, {})
        visible = sum(1 for sc in subs if sc is not None and not subs[sc].hidden)
        hidden = sum(1 for sc in subs if sc is not None and subs[sc].hidden)
        count_str = f"{visible} (+{hidden} hidden)" if hidden else str(visible)
        table.add_row(tool_name, ", ".join(aliases), count_str, tool_description(tool_name))
    console.print(table)
    console.print("\n[dim]用法: fcmd info <tool> [subcommand][/dim]")


def _info_tool(tool_name: str, subs: dict[str | None, Any]) -> None:
    """``fcmd info <tool>``：列出工具的全部子命令。"""
    from fcmd.console import Table

    console = get_console()
    console.print(f"[bold cyan]{tool_name}[/bold cyan] 子命令:")
    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("子命令", style="cyan", no_wrap=True)
    table.add_column("类型", style="dim", no_wrap=True)
    table.add_column("needs", style="yellow", no_wrap=True)
    table.add_column("strategy", style="magenta", no_wrap=True)
    table.add_column("说明")
    # 排序：visible 在前（按名排序），hidden 在后（按名排序）
    visible = sorted((sc, spec) for sc, spec in subs.items() if sc is not None and not spec.hidden)
    hidden = sorted((sc, spec) for sc, spec in subs.items() if sc is not None and spec.hidden)
    for sc, spec in visible + hidden:
        kind = _spec_kind(spec)
        needs_str = ", ".join(spec.needs) if spec.needs else "-"
        strategy_str = spec.strategy or "-"
        help_str = spec.help or ""
        if spec.hidden:
            sc_str = f"[dim]{sc} (hidden)[/dim]"
            help_str = f"[dim]{help_str}[/dim]"
        else:
            sc_str = str(sc)
        table.add_row(sc_str, kind, needs_str, strategy_str, help_str)
    console.print(table)


def _info_subcommand(tool_name: str, spec: Any) -> None:
    """``fcmd info <tool> <subcommand>``：展示 ToolSpec 完整字段。"""
    console = get_console()
    sc_name = spec.subcommand if spec.subcommand is not None else "(single)"
    console.print(f"[bold cyan]{tool_name}[/bold cyan] / [cyan]{sc_name}[/cyan]")
    fields: list[tuple[str, str]] = [
        ("help", spec.help or "-"),
        ("description", spec.description or "-"),
        ("kind", _spec_kind(spec)),
        ("cmd", " ".join(spec.cmd) if isinstance(spec.cmd, tuple) else (spec.cmd or "-")),
        ("needs", ", ".join(spec.needs) if spec.needs else "-"),
        ("strategy", spec.strategy or "-"),
        ("cwd", str(spec.cwd) if spec.cwd is not None else "-"),
        ("hidden", "yes" if spec.hidden else "no"),
        ("allow_upstream_skip", "yes" if spec.allow_upstream_skip else "no"),
        ("timeout", str(spec.timeout) if spec.timeout is not None else "-"),
        ("env", ", ".join(f"{k}=..." for k in spec.env) if spec.env else "-"),
        ("retry", str(spec.retry) if spec.retry is not None else "-"),
    ]
    from fcmd.console import Table

    table = Table(show_header=False, box=None, show_lines=False)
    table.add_column("字段", style="bold", no_wrap=True)
    table.add_column("值")
    for name, value in fields:
        table.add_row(name, value)
    console.print(table)


def _spec_kind(spec: Any) -> str:
    """判断 ToolSpec 类型：cmd / aggregate / fn。"""
    if spec.cmd is not None:
        return "cmd"
    if spec.needs:
        return "aggregate"
    return "fn"
