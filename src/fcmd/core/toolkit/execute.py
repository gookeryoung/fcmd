"""任务执行：TaskSpec 构建、依赖图执行、前置断言与退出码。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

from ..dag import Graph, GraphDefaults
from ..errors import (
    CycleError,
    DuplicateTaskError,
    FcmdError,
    InjectionError,
    MissingDependencyError,
    TaskFailedError,
)
from ..execution.executors import run
from ..exit_codes import ExitCode
from ..result import has_pending_error, reset_error_state, take_pending_exit_code
from ..task import RetryPolicy, TaskSpec, TaskStatus
from .plan import _collect_order, _is_aggregate, _plan_steps, _run_preconditions
from .present import (
    _describe_exception,
    _display_label,
    _make_progress_printer,
    _print_task_summary,
    _report_tool_failure,
)
from .spec import ToolSpec, _noop, _task_name


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


def _build_task_spec(
    spec: ToolSpec,
    variables: Mapping[str, Any],
    *,
    depends: Sequence[str] | None = None,
    extra_kwargs: Mapping[str, Any] | None = None,
) -> TaskSpec[Any]:
    """将 ToolSpec + 解析后的变量转为 TaskSpec。

    - cmd 任务：执行命令，cwd 从 ``variables["cwd"]`` 或装饰器 cwd 取；
      cmd 中 ``{参数名}`` 占位符替换为 CLI 解析值
    - 聚合任务（needs/steps 编排、显式 aggregate）：fn=noop
    - fn 任务：执行函数，kwargs 按签名从 variables 取

    ``depends`` 覆盖依赖边（由 ``steps`` 顺序链计算），``extra_kwargs`` 是
    只注入该任务的静态参数（来自 ``step(...)``），不经过 CLI / schema。
    """
    depends_on: tuple[str, ...] = tuple(spec.needs) if depends is None else tuple(depends)
    extra = dict(extra_kwargs or {})
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
            depends_on=depends_on,
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
            depends_on=depends_on,
            allow_upstream_skip=spec.allow_upstream_skip,
            strategy=spec.strategy,
        )

    # fn 任务
    sig = inspect.signature(spec.func)
    kwargs: dict[str, Any] = {}
    for pname in sig.parameters:
        if pname in variables:
            kwargs[pname] = variables[pname]
    # 步骤级静态参数优先：它们是内部布线（如 create=True），不是用户入参
    kwargs.update(extra)
    # cwd 取值与 cmd 分支对齐：CLI 变量优先，回退装饰器声明
    cwd_value = variables.get("cwd", spec.cwd)
    cwd = Path(cwd_value) if cwd_value is not None else None
    return TaskSpec(
        name=task_name,
        fn=spec.func,
        kwargs=kwargs,
        depends_on=depends_on,
        cwd=cwd,
        env=spec.env,
        retry=spec.retry if spec.retry is not None else RetryPolicy(),
        timeout=spec.timeout,
        allow_upstream_skip=spec.allow_upstream_skip,
        strategy=spec.strategy,
    )


def _execute_tool_tasks(  # noqa: PLR0911 - 前置断言 / 未知子命令 / 任务失败各自有独立错误信封与退出码
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
    dry_run = False if "dry_run" in inspect.signature(target_spec.func).parameters else variables.get("dry_run", False)
    # 前置断言：只针对目标子命令，不进 DAG / 不计步数；dry-run 不执行
    if not dry_run:
        try:
            _run_preconditions(target_spec, variables)
        except Exception as exc:
            _report_tool_failure(
                name,
                target,
                code="PRECONDITION_FAILED",
                message=_describe_exception(exc),
                json_output=json_output,
                hint="按提示完成前置条件后重试（如先初始化目录 / 安装依赖）",
            )
            pending = take_pending_exit_code()
            return int(pending) if pending is not None else int(ExitCode.FAILURE)

    edges, step_kwargs = _plan_steps(subs, target)
    chain = _collect_order(edges, target)
    task_specs: list[TaskSpec[Any]] = []
    labels: dict[str, str] = {}
    noop_tasks: set[str] = set()
    for sc in chain:
        if sc not in subs:
            _report_tool_failure(
                name,
                target,
                code="RUNTIME_ERROR",
                message=f"工具 {name!r} 的子命令 {sc!r} 未注册",
                json_output=json_output,
            )
            return int(ExitCode.FAILURE)
        sc_spec = subs[sc]
        labels[_task_name(sc_spec)] = _display_label(sc_spec)
        if _is_aggregate(sc_spec):
            noop_tasks.add(_task_name(sc_spec))
        task_specs.append(
            _build_task_spec(
                sc_spec,
                variables,
                depends=[dep for dep in edges.get(sc, ()) if dep is not None],
                extra_kwargs=step_kwargs.get(sc),
            )
        )

    # 构建图并执行
    graph = Graph.from_specs(task_specs, defaults=GraphDefaults())
    strategy = variables.get("strategy") or target_spec.strategy or "dependency"
    # --json 要求 stdout 可整体解析：同时关闭框架的进度/汇总行，避免与 JSON 混排
    show_progress = not variables.get("quiet", False) and not json_output
    if show_progress:
        # cmd 任务行会被回显为 ``$ git add .`` 并把子进程输出实时透传到终端，
        # 便于用户在长命令运行期间看到进展（框架自己的生命周期行由回调接管）。
        task_specs = [dc_replace(s, verbose=True) if s.cmd is not None else s for s in task_specs]
        graph = Graph.from_specs(task_specs, defaults=GraphDefaults())

    # 工具自己声明 dry_run 时由工具生成结构化计划，框架不跳过任务
    try:
        report = run(
            graph,  # type: ignore[bad-argument-type]
            strategy=strategy,  # type: ignore[arg-type]
            dry_run=dry_run,
            verbose=False,
            on_event=_make_progress_printer(labels, frozenset(noop_tasks)) if show_progress else None,
        )
    except TaskFailedError as e:
        # continue_on_error=False 时 run() 抛 TaskFailedError，携带 report
        _report_tool_failure(
            name,
            target,
            code="RUNTIME_ERROR",
            message=_describe_exception(e.cause),
            hint="检查入参后重试；若是工具内部错误，请附上上面的错误信息反馈",
            json_output=json_output,
        )
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

    # 已呈现错误时不再打印「完成」汇总：避免与错误行、非零退出码矛盾
    if show_progress and not dry_run and not has_pending_error():
        _print_task_summary(report, labels=labels, noop_tasks=frozenset(noop_tasks))

    if not report.success:
        # continue_on_error=True 时 run() 不抛异常，但必须有错误文本：
        # 「退出 1 却什么都没说」是最坏形态（脚本侧无从判断，人也无从修正）。
        failed = [n for n, r in report.results.items() if r.status == TaskStatus.FAILED]
        detail = "、".join(labels.get(n, n) for n in failed) if failed else "未知步骤"
        _report_tool_failure(
            name,
            target,
            code="RUNTIME_ERROR",
            message=f"{detail} 未完成",
            hint="检查入参后重试；若是工具内部错误，请附上上面的错误信息反馈",
            json_output=json_output,
        )
        return int(ExitCode.FAILURE)
    # 工具在函数体内已呈现错误（print_error / emit_error）时，以其错误码为准
    pending = take_pending_exit_code()
    return int(pending) if pending is not None else int(ExitCode.SUCCESS)
