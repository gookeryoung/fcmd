"""工具执行层：依赖收集 + TaskSpec 构建 + argv 路由 + DAG 执行 + 输出。

承载 ``@fx.tool`` 框架中**执行**的关注点，与 :mod:`fcmd.apis._tool_args`
（参数解析）分离：

* 依赖收集：:func:`_collect_with_deps`（BFS 收集 target 及传递依赖）。
* 聚合判断：:func:`_has_function_logic` / :func:`_is_aggregate`。
* TaskSpec 构建：:func:`_build_task_spec`（ToolSpec + 变量 → TaskSpec）。
* argv 路由与解析：:func:`_resolve_tool_target` / :func:`_parse_tool_args`。
* DAG 执行：:func:`_execute_tool_tasks`（收集依赖、构建图、调用 :func:`fcmd.engine.executors.run`）。
* 输出：:func:`_print_task_summary` / :func:`_print_subcommands`。

循环导入规避
------------
本模块**不依赖** :mod:`fcmd.apis.toolkit` 的全局注册表 ``_TOOL_REGISTRY``。
需要访问注册表的函数（:func:`_collect_with_deps` / :func:`_execute_tool_tasks` /
:func:`_print_subcommands`）改为接收 ``subs`` 参数，由 :mod:`fcmd.apis.toolkit`
的公共入口（:func:`run_tool` / :func:`build_tool_graph`）从注册表取出 ``subs``
后传入。这样 :mod:`fcmd.apis.toolkit` 单向依赖本模块，无循环。
"""

from __future__ import annotations

import argparse
import ast
import inspect
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fcmd.console import get_console
from fcmd.engine.executors import run

from ._tool_args import (
    ToolExitCode,
    ToolSpec,
    _add_global_options,
    _build_parser_for_tool,
    _noop,
)
from .dag import Graph, GraphDefaults
from .errors import FcmdError, TaskFailedError
from .task import RetryPolicy, TaskSpec


# ---------------------------------------------------------------------- #
# 依赖收集 + TaskSpec 构建
# ---------------------------------------------------------------------- #
def _collect_with_deps(subs: dict[str | None, ToolSpec], target: str | None) -> list[str | None]:
    """BFS 收集 target 及其传递依赖（subcommand 名）。

    返回顺序：依赖在前，target 在后（符合 DAG 拓扑）。

    Parameters
    ----------
    subs:
        工具的子命令字典（``{subcommand: ToolSpec}``），由调用方从
        ``_TOOL_REGISTRY`` 取出后传入，避免本模块反向依赖注册表
    target:
        目标子命令名；``None`` 表示单命令工具
    """
    result: list[str | None] = []
    seen: set[str | None] = set()
    queue: list[str | None] = [target]
    while queue:
        sc = queue.pop(0)
        if sc in seen:
            continue
        seen.add(sc)
        result.append(sc)
        if sc in subs:
            queue.extend(subs[sc].needs)
    # 反转：依赖在前，target 在后
    result.reverse()
    return result


def _has_function_logic(func: Any) -> bool:
    """判断函数体是否有实际逻辑（非 pass/.../docstring）。

    用 ast 分析，避免 exec 函数体。
    """
    try:
        src = inspect.getsource(func)
        src = textwrap.dedent(src)
        tree = ast.parse(src)
    except (OSError, TypeError, SyntaxError):  # pragma: no cover
        return True
    func_def = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if func_def is None:  # pragma: no cover
        return True
    for stmt in func_def.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring
        if isinstance(stmt, ast.Pass):
            continue
        return True
    return False


def _is_aggregate(spec: ToolSpec) -> bool:
    """判断是否为聚合任务（有 needs 无 cmd 无函数逻辑）。"""
    if spec.cmd is not None or not spec.needs:
        return False
    return not _has_function_logic(spec.func)


def _build_task_spec(spec: ToolSpec, variables: Mapping[str, Any]) -> TaskSpec[Any]:
    """将 ToolSpec + 解析后的变量转为 TaskSpec。

    - cmd 任务：执行命令，cwd 从 ``variables["cwd"]`` 或装饰器 cwd 取
    - 聚合任务（有 needs 无 cmd 无函数逻辑）：fn=noop
    - fn 任务：执行函数，kwargs 按签名从 variables 取
    """
    task_name = spec.subcommand if spec.subcommand is not None else spec.name

    # cmd 任务
    if spec.cmd is not None:
        cwd_value = variables.get("cwd", spec.cwd)
        cwd = Path(cwd_value) if cwd_value is not None else None
        cmd_value: Any = list(spec.cmd) if isinstance(spec.cmd, tuple) else spec.cmd
        return TaskSpec(
            name=task_name,
            cmd=cmd_value,
            depends_on=spec.needs,
            cwd=cwd,
            env=spec.env,
            retry=spec.retry if spec.retry is not None else RetryPolicy(),
            timeout=spec.timeout,
            allow_upstream_skip=spec.allow_upstream_skip,
            strategy=spec.strategy,
        )

    # 聚合任务
    if _is_aggregate(spec):
        return TaskSpec(
            name=task_name,
            fn=_noop,
            depends_on=spec.needs,
            allow_upstream_skip=spec.allow_upstream_skip,
            strategy=spec.strategy,
        )

    # fn 任务
    sig = inspect.signature(spec.func)
    kwargs: dict[str, Any] = {}
    for pname in sig.parameters:
        if pname in variables:
            kwargs[pname] = variables[pname]
    cwd_value = variables.get("cwd")
    cwd = Path(cwd_value) if cwd_value is not None else None
    return TaskSpec(
        name=task_name,
        fn=spec.func,
        kwargs=kwargs,
        depends_on=spec.needs,
        cwd=cwd,
        env=spec.env,
        retry=spec.retry if spec.retry is not None else RetryPolicy(),
        timeout=spec.timeout,
        allow_upstream_skip=spec.allow_upstream_skip,
        strategy=spec.strategy,
    )


# ---------------------------------------------------------------------- #
# argv 路由与参数解析
# ---------------------------------------------------------------------- #
def _resolve_tool_target(
    name: str, subs: dict[str | None, ToolSpec], argv: Sequence[str]
) -> tuple[str | None, list[str]] | int:
    """确定工具子命令 ``target`` 与剩余参数 ``argv_rest``。

    纯单命令工具（仅有 None 子命令）透传全部 argv；否则取 argv[0] 为 target
    （非 ``-`` 开头时），或回退到 None 子命令；无匹配时列出子命令。
    """
    # 纯单命令工具（仅有 None 子命令）：target=None，全部 argv 透传给 parser
    if None in subs and len(subs) == 1:
        return None, list(argv)
    if argv and not argv[0].startswith("-"):
        return argv[0], list(argv[1:])
    if None in subs:
        return None, list(argv)
    # 列出工具的所有子命令
    _print_subcommands(name, subs)
    return ToolExitCode.SUCCESS.value


def _parse_tool_args(
    name: str, target: str | None, argv_rest: list[str], subs: dict[str | None, ToolSpec]
) -> tuple[dict[str, Any], ToolSpec] | int:
    """校验 target、构建 parser 解析 ``argv_rest``，返回变量字典与 ``target_spec``。"""
    if target is not None and target not in subs:
        get_console().print(f"[red]错误:[/red] 工具 {name!r} 没有子命令 {target!r}")
        _print_subcommands(name, subs)
        return ToolExitCode.FAILURE.value

    target_spec = subs[target]

    # 聚合任务无 CLI 参数（函数体为空），仅保留全局选项
    if _is_aggregate(target_spec):
        parser = argparse.ArgumentParser(prog=f"{name} {target}", description=target_spec.help)
        _add_global_options(parser)
    else:
        parser = _build_parser_for_tool(target_spec)

    try:
        parsed = parser.parse_args(argv_rest)
    except SystemExit as e:
        # argparse 解析失败（unrecognized args / --help）时 raise SystemExit
        return ToolExitCode.SUCCESS.value if e.code == 0 else ToolExitCode.FAILURE.value
    variables: dict[str, Any] = {k: v for k, v in vars(parsed).items() if v is not None}
    return variables, target_spec


# ---------------------------------------------------------------------- #
# 执行
# ---------------------------------------------------------------------- #
def _execute_tool_tasks(
    name: str,
    target: str | None,
    variables: dict[str, Any],
    target_spec: ToolSpec,
    subs: dict[str | None, ToolSpec],
) -> int:
    """收集依赖、构建 DAG 并执行，返回退出码。

    Parameters
    ----------
    subs:
        工具的子命令字典，由 :func:`run_tool` 从 ``_TOOL_REGISTRY`` 取出后传入
    """
    chain = _collect_with_deps(subs, target)
    task_specs: list[TaskSpec[Any]] = []
    for sc in chain:
        if sc not in subs:
            get_console().print(f"[red]错误:[/red] 工具 {name!r} 的子命令 {sc!r} 未注册")
            return ToolExitCode.FAILURE.value
        task_specs.append(_build_task_spec(subs[sc], variables))

    # 构建图并执行
    graph = Graph.from_specs(task_specs, defaults=GraphDefaults())
    strategy = variables.get("strategy") or target_spec.strategy or "dependency"
    verbose = not variables.get("quiet", False)

    try:
        report = run(
            graph,  # type: ignore[bad-argument-type]
            strategy=strategy,  # type: ignore[arg-type]
            dry_run=variables.get("dry_run", False),
            verbose=verbose,
        )
    except TaskFailedError as e:
        # continue_on_error=False 时 run() 抛 TaskFailedError，携带 report
        if verbose:
            err_console = get_console()
            err_console.print("[red]执行失败[/red]")
            if e.report is not None:
                _print_task_summary(e.report, force=True)
        return ToolExitCode.FAILURE.value
    except FcmdError as e:
        if verbose:
            get_console().print(f"[red]错误:[/red] {e}")
        return ToolExitCode.FAILURE.value
    except KeyboardInterrupt:
        return ToolExitCode.INTERRUPTED.value

    if verbose and not variables.get("dry_run", False):
        _print_task_summary(report)

    return ToolExitCode.SUCCESS.value if report.success else ToolExitCode.FAILURE.value


# ---------------------------------------------------------------------- #
# 输出
# ---------------------------------------------------------------------- #
def _print_task_summary(report: Any, force: bool = False) -> None:
    """打印任务执行汇总表（多任务场景）。

    单任务时不打印，避免冗余；多任务时按完成顺序列出各任务的状态与耗时，
    便于定位瓶颈与优化。``force=True`` 时即使单任务也打印（用于失败诊断）。
    """
    from fcmd.console import Table

    if not force and len(report.results) <= 1:
        return
    if not report.results:
        return
    console = get_console()
    table = Table(title="执行汇总", show_header=True, header_style="bold", show_lines=False)
    table.add_column("任务", style="cyan", no_wrap=True)
    table.add_column("状态", no_wrap=True, justify="center")
    table.add_column("耗时", no_wrap=True, justify="right")
    table.add_column("重试", no_wrap=True, justify="right")
    total = 0.0
    for name, r in report.results.items():
        dur = r.duration
        if dur is not None:
            total += dur
            dur_str = f"{dur:.3f}s"
        else:
            dur_str = "-"
        status_map = {
            "success": "[green]成功[/green]",
            "failed": "[red]失败[/red]",
            "skipped": "[yellow]跳过[/yellow]",
            "running": "[cyan]运行中[/cyan]",
            "pending": "[dim]待执行[/dim]",
        }
        status_str = status_map.get(r.status.value, r.status.value)
        attempts_str = str(r.attempts) if r.attempts > 1 else "-"
        table.add_row(name, status_str, dur_str, attempts_str)
    # 合计行
    if total > 0:
        table.add_row("[bold]合计[/bold]", "", f"[bold]{total:.3f}s[/bold]", "")
    console.print(table)


def _print_subcommands(name: str, subs: dict[str | None, ToolSpec]) -> None:
    """打印工具的所有非 hidden 子命令。

    Parameters
    ----------
    subs:
        工具的子命令字典，由调用方从 ``_TOOL_REGISTRY`` 取出后传入
    """
    from fcmd.console import Table

    console = get_console()
    visible = [(sc, spec) for sc, spec in subs.items() if sc is not None and not spec.hidden]
    if not visible:
        console.print(f"[dim]工具 {name!r} 无可见子命令[/dim]")
        return
    table = Table(title=f"{name} 子命令", show_header=True, header_style="bold")
    table.add_column("子命令", style="cyan", no_wrap=True)
    table.add_column("说明")
    for sc, spec in sorted(visible, key=lambda x: str(x[0])):
        table.add_row(str(sc), spec.help or "")
    console.print(table)
