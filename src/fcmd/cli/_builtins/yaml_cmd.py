"""``fcmd yaml`` 内建命令：从 YAML 文件加载并执行任务图。"""

from __future__ import annotations

import argparse

from fcmd.apis.errors import FcmdError
from fcmd.console import get_console
from fcmd.engine.executors import run as run_graph
from fcmd.orchestration.yaml_loader import load_yaml

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd yaml <file> [job] [--dry-run] [--strategy S] [--verbose]``。

    从 YAML 文件加载 GitHub Actions 风格任务图并执行。

    - ``fcmd yaml deploy.yaml``：执行全部 jobs
    - ``fcmd yaml deploy.yaml build``：仅执行 build 及其依赖
    - ``fcmd yaml deploy.yaml --dry-run``：打印执行计划不执行
    - ``fcmd yaml deploy.yaml --strategy thread``：覆盖执行策略
    """
    parser = argparse.ArgumentParser(
        prog="fcmd yaml",
        description="从 YAML 文件加载并执行任务图",
    )
    parser.add_argument("file", help="YAML 文件路径")
    parser.add_argument("job", nargs="?", default=None, help="仅执行该 job 及其依赖（默认全部）")
    parser.add_argument("--dry-run", action="store_true", help="打印执行计划不执行")
    parser.add_argument(
        "--strategy",
        choices=("sequential", "thread", "async", "dependency"),
        default="dependency",
        help="执行策略（默认 dependency）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印详细执行过程")
    if not argv:
        parser.print_help()
        return 1
    parsed = parser.parse_args(argv)

    try:
        graph = load_yaml(parsed.file)
    except (OSError, ValueError) as e:
        get_console().print(f"[red]错误:[/red] 加载 YAML 失败: {e}")
        return 1

    only = [parsed.job] if parsed.job else None
    try:
        report = run_graph(
            graph,
            strategy=parsed.strategy,
            dry_run=parsed.dry_run,
            verbose=parsed.verbose,
            only=only,
        )
    except FcmdError as e:
        get_console().print(f"[red]错误:[/red] {e}")
        return 1

    if report.success:
        get_console().print("[green]YAML 任务图执行成功[/green]")
        return 0
    get_console().print("[red]YAML 任务图执行失败[/red]")
    return 1
