"""工具执行层：依赖收集 + TaskSpec 构建 + argv 路由 + DAG 执行 + 输出。

承载 ``@fx.tool`` 框架中**执行**的关注点，与 :mod:`fcmd.core.toolkit.args`
（参数解析）分离：

* 依赖收集：:func:`_collect_with_deps`（BFS 收集 target 及传递依赖）。
* 聚合判断：:func:`_has_function_logic` / :func:`_is_aggregate`。
* TaskSpec 构建：:func:`_build_task_spec`（ToolSpec + 变量 → TaskSpec）。
* argv 路由与解析：:func:`_resolve_tool_target` / :func:`_parse_tool_args`。
* DAG 执行：:func:`_execute_tool_tasks`（收集依赖、构建图、调用 :func:`fcmd.core.execution.executors.run`）。
* 输出：:func:`_print_task_summary` / :func:`_print_subcommands`。

循环导入规避
------------
本模块**不依赖** :mod:`fcmd.core.toolkit` 的全局注册表 ``_TOOL_REGISTRY``。
需要访问注册表的函数（:func:`_collect_with_deps` / :func:`_execute_tool_tasks` /
:func:`_print_subcommands`）改为接收 ``subs`` 参数，由 :mod:`fcmd.core.toolkit`
的公共入口（:func:`run_tool` / :func:`build_tool_graph`）从注册表取出 ``subs``
后传入。这样 :mod:`fcmd.core.toolkit` 单向依赖本模块，无循环。
"""

from __future__ import annotations

import argparse
import ast
import inspect
import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fcmd.core.execution.executors import run

from ..dag import Graph, GraphDefaults
from ..errors import (
    CycleError,
    DuplicateTaskError,
    FcmdError,
    InjectionError,
    MissingDependencyError,
    TaskFailedError,
)
from ..exit_codes import ExitCode
from ..result import emit_error, has_pending_error, reset_error_state, take_pending_exit_code
from ..task import RetryPolicy, TaskSpec, TaskStatus
from .args import (
    ToolSpec,
    _add_global_options,
    _build_parser_for_tool,
    _noop,
)


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


def _value_to_cmd_str(value: Any) -> str:
    """将 CLI 解析值转换为 cmd 模板插值字符串。

    list → 空格连接（shell 友好）；bool → 小写 true/false；
    Path → 展开 ``~``（shell 重定向/命令中的引号内 ``~`` 不会被展开，
    Windows cmd.exe 根本不识别 ``~``）；其他 → ``str()``。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    if isinstance(value, Path):
        return str(value.expanduser())
    return str(value)


def _expand_value(value: str, spec: ToolSpec, variables: Mapping[str, Any]) -> str:
    """将字符串值中 ``{参数名}`` 占位符替换为 CLI 解析值。

    仅替换 ``spec`` 签名内声明且在 ``variables`` 中有值的参数名（cmd /
    cwd / env 值共用）。字面花括号与全局变量（dry_run/quiet/strategy，
    不在签名内）不受影响。
    """
    names = inspect.signature(spec.func).parameters
    for pname in names:
        if pname in variables:
            value = value.replace("{" + pname + "}", _value_to_cmd_str(variables[pname]))
    return value


def _expand_cmd_placeholders(cmd: str | list[str], spec: ToolSpec, variables: Mapping[str, Any]) -> str | list[str]:
    """将 cmd（str 或 list）中 ``{参数名}`` 占位符替换为 CLI 解析值。

    无占位符时零成本直返。list 参数在 list cmd 中的**独占占位符项**（整项
    恰为 ``{name}``）按元素逐项展开（无 shell 执行下每元素一个 argv token）。
    """
    if isinstance(cmd, str):
        return _expand_value(cmd, spec, variables) if "{" in cmd else cmd
    sig_params = inspect.signature(spec.func).parameters
    result: list[str] = []
    for item in cmd:
        if item.startswith("{") and item.endswith("}") and item[1:-1] in sig_params:
            value = variables.get(item[1:-1])
            if isinstance(value, (list, tuple)):
                # list 值独占占位符：按元素展开
                result.extend(str(element) for element in value)
                continue
        result.append(_expand_value(item, spec, variables) if "{" in item else item)
    return result


def _build_task_spec(spec: ToolSpec, variables: Mapping[str, Any]) -> TaskSpec[Any]:
    """将 ToolSpec + 解析后的变量转为 TaskSpec。

    - cmd 任务：执行命令，cwd 从 ``variables["cwd"]`` 或装饰器 cwd 取；
      cmd 中 ``{参数名}`` 占位符替换为 CLI 解析值
    - 聚合任务（有 needs 无 cmd 无函数逻辑）：fn=noop
    - fn 任务：执行函数，kwargs 按签名从 variables 取
    """
    task_name = spec.subcommand if spec.subcommand is not None else spec.name

    # cmd 任务
    if spec.cmd is not None:
        cwd_value = variables.get("cwd", spec.cwd)
        if isinstance(cwd_value, str) and "{" in cwd_value:
            cwd_value = _expand_value(cwd_value, spec, variables)
        cwd = Path(cwd_value) if cwd_value is not None else None
        cmd_value: Any = list(spec.cmd) if isinstance(spec.cmd, tuple) else spec.cmd
        # 模板插值仅对命令模板（str/list）有意义；callable 型 cmd（如
        # pymake push 的函数任务）直通引擎
        if isinstance(cmd_value, (str, list)):
            cmd_value = _expand_cmd_placeholders(cmd_value, spec, variables)
        env = spec.env
        if env and any("{" in v for v in env.values()):
            env = {k: _expand_value(v, spec, variables) for k, v in env.items()}
        return TaskSpec(
            name=task_name,
            cmd=cmd_value,
            depends_on=spec.needs,
            cwd=cwd,
            env=env,
            retry=spec.retry if spec.retry is not None else RetryPolicy(),
            timeout=spec.timeout,
            allow_upstream_skip=spec.allow_upstream_skip,
            passthrough=spec.passthrough,
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
    # cwd 取值与 cmd 分支对齐：CLI 变量优先，回退装饰器声明
    cwd_value = variables.get("cwd", spec.cwd)
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
    # 显式请求工具级帮助（fcmd <tool> --help）：打印子命令表并正常退出（0）
    if argv and argv[0] in ("-h", "--help"):
        _print_subcommands(name, subs)
        return int(ExitCode.SUCCESS)
    # 列出工具的所有子命令：缺少子命令属于用法错误（退出码 2）
    from fcmd.console import print_error

    print_error(
        f"工具 {name!r} 需要指定子命令",
        code="USAGE",
        hint=f"运行 fcmd {name} --help，或从下面的子命令表中选择",
    )
    _print_subcommands(name, subs)
    return int(ExitCode.USAGE)


def _parse_tool_args(
    name: str, target: str | None, argv_rest: list[str], subs: dict[str | None, ToolSpec]
) -> tuple[dict[str, Any], ToolSpec] | int:
    """校验 target、构建 parser 解析 ``argv_rest``，返回变量字典与 ``target_spec``。"""
    if target is not None and target not in subs:
        from fcmd.console import print_error

        print_error(
            f"工具 {name!r} 没有子命令 {target!r}",
            code="UNKNOWN_SUBCOMMAND",
            hint=f"运行 fcmd {name} --help 查看可用子命令",
        )
        _print_subcommands(name, subs)
        return int(ExitCode.USAGE)

    target_spec = subs[target]

    # 聚合任务：无参签名保持裸 parser（仅全局选项，既有行为）；带参签名
    # 复用完整 parser，参数经共享 variables 流入子任务插值
    if _is_aggregate(target_spec) and not inspect.signature(target_spec.func).parameters:
        parser = argparse.ArgumentParser(prog=f"{name} {target}", description=target_spec.help)
        _add_global_options(parser)
    else:
        parser = _build_parser_for_tool(target_spec)

    try:
        parsed = parser.parse_args(argv_rest)
    except SystemExit as e:
        # argparse 解析失败（unrecognized args / 类型错误）按用法错误处理；
        # --help 正常退出仍为 0。argparse 自己已打印具体原因（英文），这里补
        # 一句中文修正提示，避免非零退出只剩一段英文用法文本。
        if e.code == 0:
            return int(ExitCode.SUCCESS)
        from fcmd.console import get_console

        usage = f"fcmd {name} {target} --help" if target is not None else f"fcmd {name} --help"
        get_console().print(f"[yellow]提示:[/yellow] 运行 {usage} 查看参数用法")
        return int(ExitCode.USAGE)
    variables: dict[str, Any] = {k: v for k, v in vars(parsed).items() if v is not None}
    return variables, target_spec


def _describe_exception(exc: BaseException) -> str:
    """把任务异常转成一句人能看懂的话。"""
    if isinstance(exc, SystemExit):
        return f"工具进程以退出码 {exc.code} 结束"
    return f"{type(exc).__name__}: {exc}"


def _report_tool_failure(  # noqa: PLR0913 - 与错误信封字段一一对应
    name: str,
    target: str | None,
    *,
    code: str,
    message: str,
    json_output: bool,
    hint: str | None = None,
) -> None:
    """呈现工具级失败：已呈现过则跳过，避免同一次调用输出两条错误。

    ``--json`` 走 stderr 错误信封，默认路径走 ``错误: …`` 行；两条路径都由
    这里决定，保证不会出现「退出 1 却没有任何错误文本」。
    """
    if has_pending_error():
        return
    if json_output:
        emit_error(name, target, code=code, message=message, hint=hint)
        return
    from fcmd.console import print_error

    print_error(message, code=code, hint=hint)


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
    reset_error_state()
    json_output = bool(variables.get("json_output", False))
    chain = _collect_with_deps(subs, target)
    task_specs: list[TaskSpec[Any]] = []
    for sc in chain:
        if sc not in subs:
            from fcmd.console import print_error

            print_error(f"工具 {name!r} 的子命令 {sc!r} 未注册", code="RUNTIME_ERROR")
            return int(ExitCode.FAILURE)
        sc_spec = subs[sc]
        task_specs.append(_build_task_spec(sc_spec, variables))

    # 构建图并执行
    graph = Graph.from_specs(task_specs, defaults=GraphDefaults())
    strategy = variables.get("strategy") or target_spec.strategy or "dependency"
    # --json 要求 stdout 可整体解析：同时关闭框架的进度/汇总行，避免与 JSON 混排
    verbose = not variables.get("quiet", False) and not json_output

    # 工具自己声明 dry_run 时由工具生成结构化计划，框架不跳过任务
    owns_dry_run = "dry_run" in inspect.signature(target_spec.func).parameters
    try:
        report = run(
            graph,  # type: ignore[bad-argument-type]
            strategy=strategy,  # type: ignore[arg-type]
            dry_run=False if owns_dry_run else variables.get("dry_run", False),
            verbose=verbose,
        )
    except TaskFailedError as e:
        # continue_on_error=False 时 run() 抛 TaskFailedError，携带 report
        _report_tool_failure(
            name,
            target,
            code="RUNTIME_ERROR",
            message=f"执行失败: {_describe_exception(e.cause)}",
            hint="检查入参后重试；若为工具内部错误，请附上上面的一句话与类型反馈",
            json_output=json_output,
        )
        if verbose and e.report is not None:
            _print_task_summary(e.report, force=True)
        pending = take_pending_exit_code()
        return int(pending) if pending is not None else int(ExitCode.FAILURE)
    except FcmdError as e:
        is_input_error = isinstance(e, (CycleError, DuplicateTaskError, MissingDependencyError, InjectionError))
        _report_tool_failure(
            name,
            target,
            code="INVALID_ARGUMENT" if is_input_error else "RUNTIME_ERROR",
            message=str(e),
            hint="检查任务定义/参数后重试" if is_input_error else "重试；若持续失败请附上错误信息反馈",
            json_output=json_output,
        )
        return int(ExitCode.INVALID_INPUT if is_input_error else ExitCode.FAILURE)
    except KeyboardInterrupt:
        _report_tool_failure(
            name,
            target,
            code="INTERRUPTED",
            message="已中断",
            hint="重新运行以继续",
            json_output=json_output,
        )
        return int(ExitCode.INTERRUPTED)

    # 已呈现错误时不再打印「成功」汇总：避免与错误行、非零退出码矛盾
    if verbose and not variables.get("dry_run", False) and not has_pending_error():
        _print_task_summary(report)

    if not report.success:
        # continue_on_error=True 时 run() 不抛异常，但必须有错误文本：
        # 「退出 1 却什么都没说」是最坏形态（脚本侧无从判断，人也无从修正）。
        failed = [n for n, r in report.results.items() if r.status == TaskStatus.FAILED]
        detail = "、".join(failed) if failed else "未知任务"
        _report_tool_failure(
            name,
            target,
            code="RUNTIME_ERROR",
            message=f"执行失败: 任务 {detail} 未成功",
            hint="检查入参后重试；若为工具内部错误，请附上上面的一句话与类型反馈",
            json_output=json_output,
        )
        return int(ExitCode.FAILURE)
    # 工具在函数体内已呈现错误（print_error / emit_error）时，以其错误码为准
    pending = take_pending_exit_code()
    return int(pending) if pending is not None else int(ExitCode.SUCCESS)


# ---------------------------------------------------------------------- #
# 输出
# ---------------------------------------------------------------------- #
def _print_task_summary(report: Any, force: bool = False) -> None:
    """打印任务执行汇总表（多任务场景）。

    单任务时不打印，避免冗余；多任务时按完成顺序列出各任务的状态与耗时，
    便于定位瓶颈与优化。``force=True`` 时即使单任务也打印（用于失败诊断）。
    """
    from fcmd.console import Table, get_console

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
    from fcmd.console import Table, get_console

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
