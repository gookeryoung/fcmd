"""``fcmd graph`` 内建命令：可视化 DAG 执行计划。"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from fcmd.apis.errors import FcmdError
from fcmd.apis.toolkit import build_tool_graph
from fcmd.cli._common import print_unknown_tool
from fcmd.cli._discovery import resolve_tool
from fcmd.console import get_console

if TYPE_CHECKING:
    from fcmd.apis.dag import Graph

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd graph <tool> [subcommand] [--format=tree|mermaid|layers|describe]``。

    可视化工具子命令的 DAG 执行计划，不执行任务。

    格式：

    - ``tree``（默认）：树形依赖视图，从根任务（无下游）向下展开，
      菱形依赖折叠为 ``(已展开)``，软依赖以 dim 标注不展开
    - ``mermaid``：Mermaid graph 定义（节点按任务类型造型），可粘贴到
      mermaid.live
    - ``layers``：拓扑分层纵向流程图（每层可并行）
    - ``describe``：人类可读多行摘要（Graph.describe）
    """
    parser = argparse.ArgumentParser(
        prog="fcmd graph",
        description="可视化工具子命令的 DAG 执行计划",
    )
    parser.add_argument("tool", help="工具名（如 pymake）")
    parser.add_argument("subcommand", nargs="?", default=None, help="目标子命令（如 tc/all）")
    parser.add_argument(
        "--format",
        choices=("tree", "mermaid", "layers", "describe"),
        default="tree",
        help="输出格式（默认 tree）",
    )
    if not argv:
        parser.print_help()
        return 1
    parsed = parser.parse_args(argv)

    resolved = resolve_tool(parsed.tool)
    if resolved is None:
        print_unknown_tool(parsed.tool)
        return 1

    try:
        graph = build_tool_graph(resolved, parsed.subcommand)
    except FcmdError as e:
        get_console().print(f"[red]错误:[/red] {e}")
        return 1

    if parsed.format == "tree":
        _print_tree(graph)
    elif parsed.format == "mermaid":
        # markup=False：mermaid 的 [/"..."/] 造型语法含 [...]，不做 markup 解析
        get_console().print(_mermaid_shaped(graph), end="", markup=False)
    elif parsed.format == "layers":
        _print_layers(graph)
    else:  # describe
        get_console().print(graph.describe())
    return 0


def _tree_chars() -> tuple[str, str, str, str]:
    """按终端能力返回树形字符集 ``(tee, last, pipe, space)``。"""
    from fcmd import console as console_mod

    if console_mod.supports_unicode():
        return "├── ", "└── ", "│   ", "    "
    return "|-- ", "`-- ", "|   ", "    "


def _print_tree(graph: Graph) -> None:
    """树形视图：从根任务（无下游依赖）沿硬依赖向下展开。

    菱形依赖（重复节点）折叠为 ``(已展开)``；软依赖仅标注不展开
    （软依赖只用于上下文注入，不代表执行顺序）。
    """
    console = get_console()
    tee, last, pipe, space = _tree_chars()
    # 根 = 无任何下游依赖的任务；sorted 保证多根顺序确定
    dependents: dict[str, int] = dict.fromkeys(graph.specs, 0)
    for deps in graph.deps.values():
        for dep in deps:
            dependents[dep] += 1
    roots = sorted(name for name, count in dependents.items() if count == 0)

    def walk(name: str, prefix: str, rendered: set[str]) -> None:
        entries = [(dep, False) for dep in graph.deps.get(name, ())]
        entries += [(dep, True) for dep in graph.specs[name].soft_depends_on]
        for idx, (dep, is_soft) in enumerate(entries):
            branch = last if idx == len(entries) - 1 else tee
            ext = prefix + (space if idx == len(entries) - 1 else pipe)
            if is_soft:
                console.print(f"{prefix}{branch}[dim]{dep} (软依赖)[/dim]")
            elif dep in rendered:
                console.print(f"{prefix}{branch}{dep} [dim](已展开)[/dim]")
            else:
                console.print(f"{prefix}{branch}{dep}")
                rendered.add(dep)
                walk(dep, ext, rendered)

    rendered: set[str] = set()
    for i, root in enumerate(roots):
        if i:
            console.print()
        console.print(f"[bold cyan]{root}[/bold cyan]")
        rendered.add(root)
        walk(root, "", rendered)


def _print_layers(graph: Graph) -> None:
    """分层视图：拓扑层纵向流程图，同层任务可并行。"""
    console = get_console()
    from fcmd import console as console_mod

    if console_mod.supports_unicode():
        v, down = "│", "↓"
    else:
        v, down = "|", "v"
    for idx, layer in enumerate(graph.layers(), 1):
        if idx > 1:
            console.print(f"  {v}")
            console.print(f"  {down}")
            console.print(f"  {v}")
        console.print(f"[bold cyan]Layer {idx}[/bold cyan]: {', '.join(layer)}")


def _mermaid_shaped(graph: Graph) -> str:
    """渲染 Mermaid 定义，节点按任务类型造型（cmd 平行四边形 / 其余圆角）。

    TaskSpec 层面聚合任务与 fn 任务均持有 ``fn``（聚合为 noop），不可
    区分，故统一渲染为圆角节点。
    """
    lines: list[str] = ["graph TD"]
    for name, spec in graph.specs.items():
        if spec.cmd is not None:
            lines.append(f'    {name}[/"{name}"/]')
        else:
            lines.append(f'    {name}("{name}")')
    for name, deps in graph.deps.items():
        for dep in deps:
            lines.append(f"    {dep} --> {name}")
    for name, spec in graph.specs.items():
        for dep in spec.soft_depends_on:
            lines.append(f"    {dep} -.-> {name}")
    return "\n".join(lines) + "\n"
