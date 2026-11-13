"""``fcmd yaml`` 内建命令：从 YAML 文件加载并执行任务图。"""

from __future__ import annotations

import argparse

from fcmd.console import get_console, print_error
from fcmd.core.errors import FcmdError, TaskFailedError
from fcmd.core.execution.executors import run as run_graph
from fcmd.core.exit_codes import ExitCode
from fcmd.core.orchestration.yaml_loader import load_yaml

__all__ = ["run"]


def run(argv: list[str]) -> int:  # noqa: PLR0911 - 每类错误各自对应一个退出码
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
        get_console().print("[yellow]提示:[/yellow] 用法 fcmd yaml <file> [job] [--dry-run] [--strategy S]")
        return int(ExitCode.USAGE)
    parsed = parser.parse_args(argv)

    try:
        graph = load_yaml(parsed.file)
    except FileNotFoundError:
        print_error(
            f"YAML 文件不存在: {parsed.file}",
            code="FILE_NOT_FOUND",
            hint="确认路径存在，或传入绝对路径",
        )
        return int(ExitCode.NOT_FOUND)
    except ValueError as e:
        print_error(
            f"加载 YAML 失败: {e}",
            code="INVALID_ARGUMENT",
            hint="按 GitHub Actions 语法修正 YAML（需含非空 jobs 映射）后重试",
        )
        return int(ExitCode.INVALID_INPUT)
    except OSError as e:
        print_error(
            f"读取 YAML 失败: {e}",
            code="RUNTIME_ERROR",
            hint="检查文件权限后重试",
        )
        return int(ExitCode.FAILURE)

    only = [parsed.job] if parsed.job else None
    try:
        report = run_graph(
            graph,
            strategy=parsed.strategy,
            dry_run=parsed.dry_run,
            verbose=parsed.verbose,
            only=only,
        )
    except TaskFailedError as e:
        print_error(
            f"执行失败: {e.cause!r}",
            code="RUNTIME_ERROR",
            hint="检查失败任务的命令与依赖后重试",
        )
        return int(ExitCode.FAILURE)
    except FcmdError as e:
        print_error(
            str(e),
            code="INVALID_ARGUMENT",
            hint="检查 job 名与 needs 依赖是否存在于该 YAML 中",
        )
        return int(ExitCode.INVALID_INPUT)

    if report.success:
        get_console().print("[green]YAML 任务图执行成功[/green]")
        return 0
    print_error(
        "YAML 任务图执行失败",
        code="RUNTIME_ERROR",
        hint="检查失败任务的命令输出后重试",
    )
    return int(ExitCode.FAILURE)
