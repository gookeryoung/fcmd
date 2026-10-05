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
import os
import subprocess
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
from .task import Condition, Context, RetryPolicy, TaskSpec


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

    用 ast 分析，避免 exec 函数体。DSL 合成函数标记 ``__dsl_empty_body__``
    （声明式 exec/聚合编排，占位函数体永不承载逻辑），直接判定无逻辑。
    """
    if getattr(func, "__dsl_empty_body__", False):
        return False
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
    Path / 其他 → ``str()``。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def _expand_value(value: str, spec: ToolSpec, variables: Mapping[str, Any]) -> str:
    """将字符串值中 ``{参数名}`` 占位符替换为 CLI 解析值。

    仅替换 ``spec`` 签名内声明且在 ``variables`` 中有值的参数名（cmd /
    cwd / env 值共用）。字面花括号与全局变量（dry_run/quiet/strategy，
    不在签名内）不受影响。
    """
    for pname in inspect.signature(spec.func).parameters:
        if pname in variables:
            value = value.replace("{" + pname + "}", _value_to_cmd_str(variables[pname]))
    return value


def _collect_on_tokens(spec: ToolSpec, variables: Mapping[str, Any]) -> list[str]:
    """收集 DSL bool 参数 ``on`` 固定 token（值为真时的参数）。

    ``__dsl_param_on__`` 由 :func:`fcmd.dsl.synth._synthesize_func` 注入，
    非 DSL 合成函数（无该属性）返回空列表。
    """
    on_map: Mapping[str, tuple[str, ...]] = getattr(spec.func, "__dsl_param_on__", None) or {}
    if not on_map:
        return []
    return [token for pname, tokens in on_map.items() if variables.get(pname) for token in tokens]


def _expand_cmd_placeholders(cmd: str | list[str], spec: ToolSpec, variables: Mapping[str, Any]) -> str | list[str]:
    """将 cmd（str 或 list）中 ``{参数名}`` 占位符替换为 CLI 解析值。

    无占位符时零成本直返。DSL 扩展语义：

    - list 参数在 list cmd 中的**独占占位符项**（整项恰为 ``{name}``）按
      元素逐项展开（无 shell 执行下每元素一个 argv token）；
    - bool 参数声明 ``on`` 时，值为真向 cmd 尾部追加固定 token。
    """
    if isinstance(cmd, str):
        expanded = _expand_value(cmd, spec, variables) if "{" in cmd else cmd
        on_tokens = _collect_on_tokens(spec, variables)
        return f"{expanded} {' '.join(on_tokens)}" if on_tokens else expanded
    sig_params = inspect.signature(spec.func).parameters
    result: list[str] = []
    for item in cmd:
        if item.startswith("{") and item.endswith("}") and item[1:-1] in sig_params:
            value = variables.get(item[1:-1])
            if isinstance(value, (list, tuple)):
                # list 参数独占占位符：按元素展开
                result.extend(str(element) for element in value)
                continue
        result.append(_expand_value(item, spec, variables) if "{" in item else item)
    result.extend(_collect_on_tokens(spec, variables))
    return result


def _build_conditions(spec: ToolSpec) -> tuple[Condition, ...]:
    """将 DSL ``when`` 探针声明构造为引擎条件闭包。

    ``__dsl_when__`` 由 :func:`fcmd.dsl.synth._synthesize_func` 注入（仅声明
    when 的 DSL 命令），非 DSL 合成函数返回空元组。探针在任务执行前同步
    求值一次；探针自身失败（命令不存在/路径异常）由引擎
    :meth:`TaskSpec.should_execute` 捕获并视为条件不满足（SKIPPED）。

    - cmd 探针：shell 执行（与 DSL str cmd 语义一致），按 stdout 是否非空判定；
    - path 探针：``~`` 展开后按存在性判定。

    闭包携带 ``_reason`` 属性，供引擎把跳过原因格式化为
    ``"条件不满足: <描述>"``。
    """
    when = getattr(spec.func, "__dsl_when__", None)
    if when is None:
        return ()

    if when.cmd is not None:
        probe_cmd, expects_nonempty = when.cmd, when.expect == "nonempty"
        reason = f'命令探针 "{when.cmd}" 期望输出{"非空" if expects_nonempty else "为空"}'

        def _probe(_context: Context) -> bool:
            result = subprocess.run(probe_cmd, shell=True, capture_output=True, text=True, check=False)
            has_output = bool(result.stdout.strip())
            return has_output if expects_nonempty else not has_output

    else:
        assert when.path is not None  # 声明期校验保证 cmd/path 恰有其一
        probe_path, expects_exists = when.path, when.expect == "exists"
        reason = f'路径探针 "{when.path}" 期望{"存在" if expects_exists else "不存在"}'

        def _probe(_context: Context) -> bool:
            return Path(probe_path).expanduser().exists() == expects_exists

    _probe._reason = reason  # type: ignore[attr-defined]
    return (_probe,)


def _apply_env_defaults(variables: dict[str, Any], spec: ToolSpec) -> None:
    """解析 DSL str 参数 ``default_env`` 环境变量回退链（就地写 ``variables``）。

    ``__dsl_param_env__`` 由 :func:`fcmd.dsl.synth._synthesize_func` 注入，
    非 DSL 合成函数（无该属性）为空操作。CLI 解析值等于声明 default 时，
    按链取第一个**非空**环境变量值写入 ``variables``——后续 cmd/cwd/env
    模板插值与 post-run message 均使用解析后的值；链全空（或对应环境变量
    未设置/为空）则保持声明 default 不变。
    """
    env_map: Mapping[str, tuple[tuple[str, ...], Any]] = getattr(spec.func, "__dsl_param_env__", None) or {}
    for pname, (chain, default) in env_map.items():
        if variables.get(pname) != default:
            continue
        for env_name in chain:
            value = os.environ.get(env_name)
            if value:
                variables[pname] = value
                break


def _build_task_spec(spec: ToolSpec, variables: Mapping[str, Any]) -> TaskSpec[Any]:
    """将 ToolSpec + 解析后的变量转为 TaskSpec。

    - cmd 任务：执行命令，cwd 从 ``variables["cwd"]`` 或装饰器 cwd 取；
      cmd 中 ``{参数名}`` 占位符替换为 CLI 解析值（DSL 声明式命令）
    - 聚合任务（有 needs 无 cmd 无函数逻辑）：fn=noop
    - fn 任务：执行函数，kwargs 按签名从 variables 取

    DSL 声明 when 守卫时，三个分支统一挂引擎条件闭包（不满足 → SKIPPED）。
    """
    task_name = spec.subcommand if spec.subcommand is not None else spec.name
    conditions = _build_conditions(spec)

    # cmd 任务
    if spec.cmd is not None:
        cwd_value = variables.get("cwd", spec.cwd)
        if isinstance(cwd_value, str) and "{" in cwd_value:
            # DSL 声明的 cwd 支持 {参数名} 插值
            cwd_value = _expand_value(cwd_value, spec, variables)
        cwd = Path(cwd_value) if cwd_value is not None else None
        cmd_value: Any = list(spec.cmd) if isinstance(spec.cmd, tuple) else spec.cmd
        # 模板插值仅对命令模板（str/list）有意义；callable 型 cmd（如
        # pymake push 的函数任务）直通引擎
        if isinstance(cmd_value, (str, list)):
            cmd_value = _expand_cmd_placeholders(cmd_value, spec, variables)
        env = spec.env
        if env and any("{" in v for v in env.values()):
            # DSL 声明的 env 值支持 {参数名} 插值
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
            conditions=conditions,
        )

    # 聚合任务
    if _is_aggregate(spec):
        return TaskSpec(
            name=task_name,
            fn=_noop,
            depends_on=spec.needs,
            allow_upstream_skip=spec.allow_upstream_skip,
            strategy=spec.strategy,
            conditions=conditions,
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
        conditions=conditions,
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

    # 聚合任务：无参签名保持裸 parser（仅全局选项，既有行为）；带参签名
    # （DSL 聚合 args）复用完整 parser，参数经共享 variables 流入子任务插值
    if _is_aggregate(target_spec) and not inspect.signature(target_spec.func).parameters:
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
        sc_spec = subs[sc]
        # DSL 参数环境变量回退链解析（依赖在前：链上子任务可见解析结果；
        # 就地写 variables，post-run message 同样取解析后的值）
        _apply_env_defaults(variables, sc_spec)
        task_specs.append(_build_task_spec(sc_spec, variables))

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

    # DSL post-run 完成消息（__dsl_message__ 由 synth 注入）：执行成功后打印，
    # 支持 {参数名} 插值；dry-run 未实际执行不打印
    if not variables.get("dry_run", False) and report.success:
        message = getattr(target_spec.func, "__dsl_message__", None)
        if message:
            print(_expand_value(message, target_spec, variables))

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
