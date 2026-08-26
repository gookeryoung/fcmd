"""``fcmd graph`` 内建命令：可视化 DAG 执行计划。"""

from __future__ import annotations

import argparse

from fcmd.apis.errors import FcmdError
from fcmd.apis.toolkit import build_tool_graph
from fcmd.cli._common import print_unknown_tool
from fcmd.cli._discovery import resolve_tool
from fcmd.console import get_console

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd graph <tool> <subcommand> [--format=mermaid|layers|describe]``。

    可视化工具子命令的 DAG 执行计划，不执行任务。

    格式：
    - ``mermaid``（默认）：Mermaid graph 定义，可粘贴到 mermaid.live
    - ``layers``：拓扑分层列表（每层可并行）
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
        choices=("mermaid", "layers", "describe"),
        default="mermaid",
        help="输出格式（默认 mermaid）",
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

    if parsed.format == "mermaid":
        get_console().print(graph.to_mermaid(), end="")
    elif parsed.format == "layers":
        layers = graph.layers()
        for idx, layer in enumerate(layers, 1):
            get_console().print(f"Layer {idx}: {layer}")
    else:  # describe
        get_console().print(graph.describe())
    return 0
