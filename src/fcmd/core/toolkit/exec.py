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
import contextlib
import inspect
import io
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import replace as dc_replace
from pathlib import Path
from subprocess import CalledProcessError
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

    返回顺序：依赖在前，target 在后（符合 DAG 拓扑）。仅看 ``needs``；
    带 ``steps`` 的编排请用 :func:`_plan_steps` + :func:`_collect_order`。

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


def _merge_step_kwargs(store: dict[str | None, dict[str, Any]], task: str | None, kwargs: Mapping[str, Any]) -> None:
    """合并步骤级静态参数；同一任务被不同 steps 配置成不同值时报告冲突。"""
    if not kwargs:
        return
    existing = store.setdefault(task, {})
    for name, value in kwargs.items():
        if name in existing and existing[name] != value:
            raise InjectionError(task=task or "<unknown>", reason=f"参数 {name!r} 在不同 steps 中配置冲突")
        existing[name] = value


def _plan_steps(
    subs: dict[str | None, ToolSpec], target: str | None
) -> tuple[dict[str | None, tuple[str | None, ...]], dict[str | None, dict[str, Any]]]:
    """计算执行边：``needs`` 依赖 + ``steps`` 顺序链。

    规则：

    - 普通任务：边即 ``spec.needs``；
    - ``steps`` 任务：第一步依赖目标自身的 ``needs``，之后每步依赖前一步，
      目标任务依赖最后一步（``needs`` 也从目标保留，便于并行前置任务）；
    - 同一任务出现在多条链时合并依赖边，参数冲突报 :class:`InjectionError`。

    Returns
    -------
    (edges, step_kwargs)
        ``edges`` 为任务名 → 依赖元组；``step_kwargs`` 为任务名 → 只注入该
        任务的静态参数（来自 ``step(..., **kwargs)``）。
    """
    edges: dict[str | None, tuple[str | None, ...]] = {}
    step_kwargs: dict[str | None, dict[str, Any]] = {}
    visiting: set[str | None] = set()
    seen: set[tuple[str | None, str | None]] = set()

    def visit(sc: str | None, prev: str | None) -> None:
        if sc in visiting:
            raise InjectionError(task=sc or "<unknown>", reason="steps 存在循环引用")
        if (sc, prev) in seen:
            return
        spec = subs.get(sc)
        if spec is None:
            return
        seen.add((sc, prev))
        visiting.add(sc)
        deps: list[str | None] = list(spec.needs)
        if prev is not None:
            deps.append(prev)
        if spec.steps:
            chain_prev = prev
            for index, item in enumerate(spec.steps):
                visit(item.task, chain_prev)
                _merge_step_kwargs(step_kwargs, item.task, item.kwargs)
                if index == 0 and spec.needs:
                    # 流水线第一步额外依赖目标自身的 needs（外层已就绪再开始）
                    first_deps = tuple(dict.fromkeys((*edges.get(item.task, ()), *spec.needs)))
                    edges[item.task] = first_deps
                    for dep in spec.needs:
                        visit(dep, None)
                chain_prev = item.task
            if chain_prev is not None:
                deps.append(chain_prev)
        visiting.discard(sc)
        merged = tuple(dict.fromkeys(dep for dep in (*edges.get(sc, ()), *deps) if dep != sc))
        edges[sc] = merged
        for dep in merged:
            visit(dep, None)

    visit(target, None)
    return edges, step_kwargs


def _collect_order(edges: Mapping[str | None, Sequence[str | None]], target: str | None) -> list[str | None]:
    """按 :func:`_plan_steps` 算出的边做深度优先遍历，返回依赖在前的拓扑序。"""
    ordered: list[str | None] = []
    done: set[str | None] = set()
    visiting: set[str | None] = set()

    def visit(sc: str | None) -> None:
        if sc in done:
            return
        if sc in visiting:
            raise InjectionError(task=sc or "<unknown>", reason="steps/needs 存在循环引用")
        visiting.add(sc)
        for dep in edges.get(sc, ()):
            visit(dep)
        visiting.discard(sc)
        done.add(sc)
        ordered.append(sc)

    visit(target)
    return ordered


def _run_preconditions(spec: ToolSpec, variables: Mapping[str, Any]) -> None:
    """按签名注入 CLI 变量后执行目标子命令的前置断言；异常由调用方呈现。"""
    for check in spec.preconditions:
        kwargs = {name: variables[name] for name in inspect.signature(check).parameters if name in variables}
        check(**kwargs)


# ---------------------------------------------------------------------- #
# 任务分类
# ---------------------------------------------------------------------- #


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
    """判断是否为编排壳任务（有 needs/steps 无 cmd）。

    优先使用装饰器显式声明的 ``aggregate``；未声明时保留历史推断：
    无 cmd、有依赖且函数体无实际逻辑。声明 ``steps`` 时天然是聚合任务，
    不受函数体是否写了代码影响。
    """
    if spec.cmd is not None:
        return False
    if spec.aggregate is not None:
        return spec.aggregate
    if spec.steps:
        return True
    if not spec.needs:
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


# ---------------------------------------------------------------------- #
# argv 路由与参数解析
# ---------------------------------------------------------------------- #
def _json_requested(argv: Sequence[str]) -> bool:
    """判断 argv 是否显式请求机读输出。

    逐 token 精确匹配 ``--json``（不匹配 ``--json-output`` 的前缀），遇到
    ``--`` 即停止——其后的内容属于位置参数，不作为选项解释。
    """
    for token in argv:
        if token == "--":
            return False
        if token == "--json":
            return True
    return False


def _argparse_error_detail(captured: str) -> str:
    """从 argparse 写入 stderr 的文本中取出最后一条 ``error:`` 说明。"""
    for line in reversed(captured.splitlines()):
        _, separator, detail = line.partition("error: ")
        if separator:
            return detail.strip()
    return "参数解析失败"


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

    if _json_requested(argv):
        emit_error(
            name,
            None,
            code="USAGE",
            message=f"工具 {name!r} 需要指定子命令",
            hint=f"运行 fcmd {name} --help 查看可用子命令",
        )
        return int(ExitCode.USAGE)
    print_error(
        f"工具 {name!r} 需要指定子命令",
        code="USAGE",
        hint=f"运行 fcmd {name} --help，或从下面的子命令表中选择",
    )
    _print_subcommands(name, subs)
    return int(ExitCode.USAGE)


def _parse_arguments(
    parser: argparse.ArgumentParser,
    name: str,
    target: str | None,
    argv_rest: Sequence[str],
    usage: str,
) -> dict[str, Any] | int:
    """解析参数；用法错误按模式输出文本或信封，并返回退出码。"""
    if _json_requested(argv_rest):
        # argparse 失败会向 stderr 打英文 usage + error；机读模式改由信封承载，
        # 避免 stderr 混入非 JSON 文本。
        captured = io.StringIO()
        try:
            with contextlib.redirect_stderr(captured):
                parsed = parser.parse_args(argv_rest)
        except SystemExit as e:
            if e.code == 0:
                return int(ExitCode.SUCCESS)
            emit_error(
                name,
                target,
                code="USAGE",
                message=f"参数解析失败: {_argparse_error_detail(captured.getvalue())}",
                hint=f"运行 {usage} 查看参数用法",
            )
            return int(ExitCode.USAGE)
    else:
        try:
            parsed = parser.parse_args(argv_rest)
        except SystemExit as e:
            # argparse 解析失败（unrecognized args / 类型错误）按用法错误处理；
            # --help 正常退出仍为 0。argparse 自己已打印具体原因（英文），这里补
            # 一句中文修正提示，避免非零退出只剩一段英文用法文本。
            if e.code == 0:
                return int(ExitCode.SUCCESS)
            from fcmd.console import get_console

            get_console().print(f"[yellow]提示:[/yellow] 运行 {usage} 查看参数用法")
            return int(ExitCode.USAGE)
    return {k: v for k, v in vars(parsed).items() if v is not None}


def _parse_tool_args(
    name: str, target: str | None, argv_rest: list[str], subs: dict[str | None, ToolSpec]
) -> tuple[dict[str, Any], ToolSpec] | int:
    """校验 target、构建 parser 解析 ``argv_rest``，返回变量字典与 ``target_spec``。"""
    json_output = _json_requested(argv_rest)
    if target is not None and target not in subs:
        from fcmd.console import print_error

        hint = f"运行 fcmd {name} --help 查看可用子命令"
        if json_output:
            emit_error(
                name, target, code="UNKNOWN_SUBCOMMAND", message=f"工具 {name!r} 没有子命令 {target!r}", hint=hint
            )
            return int(ExitCode.USAGE)
        print_error(f"工具 {name!r} 没有子命令 {target!r}", code="UNKNOWN_SUBCOMMAND", hint=hint)
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

    usage = f"fcmd {name} {target} --help" if target is not None else f"fcmd {name} --help"
    variables = _parse_arguments(parser, name, target, argv_rest, usage)
    if isinstance(variables, int):
        return variables
    return variables, target_spec


def _describe_exception(exc: BaseException) -> str:
    """把任务异常转成一句人能看懂的话。

    - 工具自己抛的 :class:`RuntimeError` 已是中文句子（如「命令执行失败: …（返回码 1）」），
      直接透传；
    - ``subprocess`` 的 :class:`~subprocess.CalledProcessError` 转成命令行 + 返回码，
      不再把 Python 的 ``repr(list)`` 丢给用户；
    - 其他异常保留类型名，方便反馈时定位。
    """
    if isinstance(exc, SystemExit):
        return f"工具进程以退出码 {exc.code} 结束"
    text = str(exc).strip()
    if isinstance(exc, CalledProcessError):
        cmd = exc.cmd if isinstance(exc.cmd, str) else " ".join(str(part) for part in exc.cmd or ())
        return f"命令执行失败: {cmd}（返回码 {exc.returncode}）"
    if isinstance(exc, RuntimeError) and text:
        return text
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


def _task_name(spec: ToolSpec) -> str:
    """子命令在 DAG 内的任务名（与 :func:`_build_task_spec` 保持一致）。"""
    return spec.subcommand if spec.subcommand is not None else spec.name


def _display_label(spec: ToolSpec) -> str:
    """子命令的用户可见动作名。

    优先用子命令的 ``help``（已是中文动词短语），去掉靠后的「（内部）」标记；
    两者都缺失时回退到子命令名 / 工具名。展示层不再出现 ``'_commit'`` 这类
    内部标识，也不使用 ``repr`` 的引号。
    """
    text = (spec.help or "").strip().rstrip("。")
    for suffix in ("（内部）", "(内部)"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text or str(spec.subcommand or spec.name)


def _make_progress_printer(labels: Mapping[str, str], noop_tasks: frozenset[str]) -> Any:
    """构造进度回调：把任务事件翻译成用户可读的步骤行。

    - ``noop_tasks``（纯聚合壳任务，如 ``gitt a``）不单独占一行：用户只关心
      真正干活的那几步；
    - 不打印开始行，成功路径只留一行结果，减少噪音；
    - 任务已在函数体内通过 ``print_error`` 报错时不再补「完成」，避免矛盾。
    """
    from fcmd.console import print_step_done, print_step_failed, print_step_skipped
    from fcmd.core.result import has_pending_error

    def _printer(event: Any) -> None:
        if event.status == TaskStatus.SUCCESS:
            if event.task in noop_tasks or has_pending_error():
                return
            print_step_done(labels.get(event.task, event.task), event.duration)
        elif event.status == TaskStatus.FAILED:
            print_step_failed(labels.get(event.task, event.task), event.duration, attempts=event.attempts)
        elif event.status == TaskStatus.SKIPPED:
            print_step_skipped(labels.get(event.task, event.task), event.reason or "")

    return _printer


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


# ---------------------------------------------------------------------- #
# 输出
# ---------------------------------------------------------------------- #
def _print_task_summary(
    report: Any,
    labels: Mapping[str, str] | None = None,
    noop_tasks: frozenset[str] = frozenset(),
) -> None:
    """多步执行成功后的收尾行：``共 3 步，用时 1.61s``。

    单步不打印（上面那行「完成 …」已说明结果）；纯聚合壳任务（如 ``gitt a``）
    不计入步数——它们是编排占位，不是用户眼里的“一步”。

    Parameters
    ----------
    report:
        本次运行报告。
    labels:
        任务名 → 用户可读动作名；缺失时回退到任务名（保持向后兼容）。
    noop_tasks:
        不计入步数的纯聚合任务名。
    """
    from fcmd.console import print_run_summary

    del labels  # 步骤行已带动作名，收尾行只给步数与耗时
    steps = [r for name, r in report.results.items() if name not in noop_tasks]
    if len(steps) <= 1:
        return
    total = sum(r.duration for r in steps if r.duration is not None)
    print_run_summary(len(steps), total)


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
