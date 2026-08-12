"""@fx.tool 装饰器：argparse 驱动 CLI + DAG 编排。

本模块是 ``@fx.tool`` 框架的实现，属于 :mod:`fcmd.apis` 包的共性接口层。
替代手写 argparse 模板，用 ``@fx.tool`` 装饰器统一描述工具：函数签名 → argparse
自动生成 CLI，函数体即任务逻辑，``needs``/``strategy``/``cmd`` 表达 DAG。

示例
----
::

    @fx.tool("demo", subcommand="hello", cmd=["python", "-c", "print('hi')"])
    def hello() -> None:
        pass  # cmd 任务：签名仅驱动 CLI，函数体不执行

    @fx.tool("demo", subcommand="greet", help="问候")
    def greet(name: str, times: int = 1) -> str:
        return f"hello {name} " * times

    # CLI: fcmd demo greet world --times 2

聚合任务（有 needs 无 cmd 无函数逻辑）::

    @fx.tool("demo", subcommand="all", needs=["hello", "greet"], strategy="thread")
    def all() -> None:
        pass  # 仅作依赖聚合点

架构
----
本模块按职责拆分：

* :mod:`fcmd.apis._tool_args` —— **参数解析**（ToolSpec / ToolExitCode /
  类型注解解析 / argparse parser 构建）。
* 本模块 —— **注册表 + 依赖收集 + 执行 + 输出**：
  ``@fx.tool`` / ``@fx.main`` 装饰器、工具注册表、BFS 依赖收集、
  TaskSpec 构建、argv 路由与解析、DAG 执行、汇总输出。
"""

from __future__ import annotations

__all__ = [
    "ToolExitCode",
    "ToolSpec",
    "build_tool_graph",
    "clear_tool_registry",
    "get_tool",
    "list_subcommands",
    "list_tools",
    "main",
    "run_tool",
    "tool",
]

import argparse
import ast
import inspect
import textwrap
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from fcmd.console import get_console
from fcmd.executors import run

from ._tool_args import (
    ToolExitCode,
    ToolSpec,
    _add_global_options,
    _add_optional_arg,  # noqa: F401  重新导出供测试 import
    _add_positional_arg,  # noqa: F401  重新导出供测试 import
    _annotation_str_to_type,  # noqa: F401  重新导出供测试 import
    _build_parser_for_tool,
    _is_list_annotation,  # noqa: F401  重新导出供测试 import
    _is_literal_annotation,  # noqa: F401  重新导出供测试 import
    _list_inner_type,  # noqa: F401  重新导出供测试 import
    _literal_choices,  # noqa: F401  重新导出供测试 import
    _noop,
    _resolve_hints,  # noqa: F401  重新导出供测试 import
    _unwrap_optional,  # noqa: F401  重新导出供测试 import
)
from .dag import Graph, GraphDefaults
from .errors import FcmdError, TaskFailedError
from .task import RetryPolicy, TaskSpec

# 全局工具注册表：{tool_name: {subcommand: ToolSpec}}
_TOOL_REGISTRY: dict[str, dict[str | None, ToolSpec]] = {}


# ---------------------------------------------------------------------- #
# @tool 装饰器 + 注册表
# ---------------------------------------------------------------------- #
def tool(  # noqa: PLR0913
    name: str,
    *,
    subcommand: str | None = None,
    help: str = "",
    description: str = "",
    cmd: Sequence[str] | str | None = None,
    needs: Sequence[str] | None = None,
    strategy: Literal["sequential", "thread", "async", "dependency"] | None = None,
    cwd: str | Path | None = None,
    allow_upstream_skip: bool = False,
    hidden: bool = False,
    env: Mapping[str, str] | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """装饰器：将函数注册为 ``@fx.tool`` 工具。

    Parameters
    ----------
    name:
        工具名（如 ``"pymake"``）；多个 ``@fx.tool`` 共用同名即多 subcommand 工具
    subcommand:
        子命令名；``None`` 表示单命令工具（整个工具仅一个函数）
    help:
        子命令帮助文本；默认用函数 docstring
    description:
        工具描述，用于 fcmd 工具列表
    cmd:
        命令列表或 shell 字符串；有 ``cmd`` 执行命令，函数体不执行（签名仅驱动 CLI）
    needs:
        依赖任务名（引用同 tool 的其他 subcommand）
    strategy:
        执行策略：``"sequential"`` / ``"thread"`` / ``"async"`` / ``"dependency"``
    cwd:
        工作目录（cmd 任务装饰器级默认）；若函数签名有 ``cwd`` 参数则被 CLI 值覆盖
    allow_upstream_skip:
        上游 SKIPPED 时本任务仍执行
    hidden:
        不暴露为 subcommand（内部 job，仅被 needs 引用）
    env / retry / timeout:
        透传 :class:`TaskSpec` 对应字段
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = ToolSpec(
            name=name,
            subcommand=subcommand,
            func=func,
            help=help or inspect.getdoc(func) or "",
            description=description,
            cmd=cast("tuple[str, ...] | str | None", tuple(cmd) if isinstance(cmd, (list, tuple)) else cmd),
            needs=tuple(needs) if needs else (),
            strategy=strategy,
            cwd=cwd,
            allow_upstream_skip=allow_upstream_skip,
            hidden=hidden,
            env=dict(env) if env else None,
            retry=retry,
            timeout=timeout,
        )
        _register_tool(spec)
        return func

    return decorator


def _register_tool(spec: ToolSpec) -> None:
    """注册工具到全局注册表，校验重复。"""
    if spec.name not in _TOOL_REGISTRY:
        _TOOL_REGISTRY[spec.name] = {}
    if spec.subcommand in _TOOL_REGISTRY[spec.name]:
        raise ValueError(f"工具 {spec.name!r} 的子命令 {spec.subcommand!r} 已注册")
    _TOOL_REGISTRY[spec.name][spec.subcommand] = spec


def get_tool(name: str, subcommand: str | None = None) -> ToolSpec:
    """获取已注册工具。

    Raises
    ------
    KeyError
        工具或子命令未注册
    """
    if name not in _TOOL_REGISTRY:
        raise KeyError(f"工具 {name!r} 未注册")
    subs = _TOOL_REGISTRY[name]
    if subcommand not in subs:
        raise KeyError(f"工具 {name!r} 没有子命令 {subcommand!r}")
    return subs[subcommand]


def list_tools() -> list[str]:
    """列出所有已注册工具名。"""
    return sorted(_TOOL_REGISTRY.keys())


def list_subcommands(name: str, include_hidden: bool = False) -> list[str]:
    """列出工具的子命令（hidden 默认排除）。

    单命令工具（subcommand=None）返回空列表。
    """
    if name not in _TOOL_REGISTRY:
        return []
    return sorted(
        sc for sc, spec in _TOOL_REGISTRY[name].items() if sc is not None and (include_hidden or not spec.hidden)
    )


def clear_tool_registry() -> None:
    """清空注册表（测试用）。"""
    _TOOL_REGISTRY.clear()


# ---------------------------------------------------------------------- #
# @main 装饰器：工具模块独立入口
# ---------------------------------------------------------------------- #
def main(tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """装饰器：将函数注册为工具模块的独立入口。

    等价于 ``fcmd <tool_name> <args>`` 的快捷入口，
    封装 ``sys.exit(run_tool(...))`` 样板代码。
    自动将函数体替换为 ``pass``，并生成统一的中文文档字符串。

    Parameters
    ----------
    tool_name:
        工具名（如 ``"lscalc"``），与 ``@fx.tool`` 注册的名称对应。

    用法::

        @fx.main("lscalc")
        def main() -> None:
            pass
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        import functools
        import sys

        func.__doc__ = f"``{tool_name}`` 入口：等价于 ``fcmd {tool_name} <args>``。"

        @functools.wraps(func)
        def wrapper(*_args: Any, **_kwargs: Any) -> Any:
            sys.exit(run_tool(tool_name, sys.argv[1:]))

        return wrapper

    return decorator


# ---------------------------------------------------------------------- #
# 依赖收集 + TaskSpec 构建
# ---------------------------------------------------------------------- #
def _collect_with_deps(name: str, target: str | None) -> list[str | None]:
    """BFS 收集 target 及其传递依赖（subcommand 名）。

    返回顺序：依赖在前，target 在后（符合 DAG 拓扑）。
    """
    if name not in _TOOL_REGISTRY:
        return [target]
    subs = _TOOL_REGISTRY[name]
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


def _has_function_logic(func: Callable[..., Any]) -> bool:
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
    _print_subcommands(name)
    return ToolExitCode.SUCCESS.value


def _parse_tool_args(
    name: str, target: str | None, argv_rest: list[str], subs: dict[str | None, ToolSpec]
) -> tuple[dict[str, Any], ToolSpec] | int:
    """校验 target、构建 parser 解析 ``argv_rest``，返回变量字典与 ``target_spec``。"""
    if target is not None and target not in subs:
        get_console().print(f"[red]错误:[/red] 工具 {name!r} 没有子命令 {target!r}")
        _print_subcommands(name)
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
def _execute_tool_tasks(name: str, target: str | None, variables: dict[str, Any], target_spec: ToolSpec) -> int:
    """收集依赖、构建 DAG 并执行，返回退出码。"""
    subs = _TOOL_REGISTRY[name]
    chain = _collect_with_deps(name, target)
    task_specs: list[TaskSpec[Any]] = []
    for sc in chain:
        if sc not in subs:
            get_console().print(f"[red]错误:[/red] 子命令 {sc!r} 未注册")
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


def run_tool(name: str, argv: Sequence[str]) -> int:
    """运行工具：解析 argv、构建 DAG、执行并返回退出码。

    Parameters
    ----------
    name:
        工具名（必须在注册表中）
    argv:
        命令行参数（不含工具名本身），如 ``["b", "--dry-run"]``

    Returns
    -------
    int
        :class:`ToolExitCode` 值（0=成功 / 1=失败 / 130=中断）
    """
    if name not in _TOOL_REGISTRY:
        get_console().print(f"[red]错误:[/red] 工具 {name!r} 未注册")
        return ToolExitCode.FAILURE.value

    subs = _TOOL_REGISTRY[name]

    # 1. 路由解析：确定 target（子命令）与 argv_rest
    resolved = _resolve_tool_target(name, subs, argv)
    if isinstance(resolved, int):
        return resolved
    target, argv_rest = resolved

    # 2. 参数解析：构建 parser，解析 argv_rest
    parsed = _parse_tool_args(name, target, argv_rest, subs)
    if isinstance(parsed, int):
        return parsed
    variables, target_spec = parsed

    # 3. 构建图并执行
    return _execute_tool_tasks(name, target, variables, target_spec)


def build_tool_graph(name: str, target: str | None) -> Graph:
    """构建工具的 DAG（不执行），用于可视化与内省。

    复用 :func:`_collect_with_deps` 的 BFS 依赖收集与 :func:`_build_task_spec`
    的 TaskSpec 构建，但不调用 :func:`run`，仅返回 :class:`Graph`。

    Parameters
    ----------
    name:
        工具名（必须在注册表中）
    target:
        目标子命令名；``None`` 表示包含工具的全部子命令（含 hidden，
        便于完整可视化 DAG）

    Returns
    -------
    Graph
        构建好的任务图。target 非 None 时含 target 及其传递依赖；
        target 为 None 时含工具全部子命令

    Raises
    ------
    FcmdError
        工具或子命令未注册时
    """
    if name not in _TOOL_REGISTRY:
        raise FcmdError(f"工具 {name!r} 未注册")
    subs = _TOOL_REGISTRY[name]
    if target is not None and target not in subs:
        raise FcmdError(f"工具 {name!r} 没有子命令 {target!r}")
    if target is None:
        # 包含全部子命令（含 hidden），便于完整可视化
        selected: list[ToolSpec] = list(subs.values())
    else:
        chain = _collect_with_deps(name, target)
        selected = [subs[sc] for sc in chain if sc in subs]
    task_specs: list[TaskSpec[Any]] = [_build_task_spec(spec, {}) for spec in selected]
    return Graph.from_specs(task_specs, defaults=GraphDefaults())


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


def _print_subcommands(name: str) -> None:
    """打印工具的所有非 hidden 子命令。"""
    from fcmd.console import Table

    subs = _TOOL_REGISTRY.get(name, {})
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
