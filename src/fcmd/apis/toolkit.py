"""@fx.tool 装饰器：argparse 驱动 CLI + DAG 编排。

本模块是 ``@fx.tool`` 框架的实现，属于 :mod:`fcmd.apis` 包的共性接口层。
替代手写 argparse 模板，用 .py 装饰器统一描述工具：函数签名 → argparse
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
    "run_tool",
    "tool",
]

import argparse
import ast
import enum
import inspect
import textwrap
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from fcmd.console import get_console
from fcmd.dag import Graph, GraphDefaults
from fcmd.errors import FcmdError, TaskFailedError
from fcmd.executors import run
from fcmd.task import RetryPolicy, TaskSpec


class ToolExitCode(enum.IntEnum):
    """工具执行退出码。"""

    SUCCESS = 0
    FAILURE = 1
    INTERRUPTED = 130  # 与 POSIX 信号中断一致


def _noop() -> None:
    """聚合任务的占位函数。"""
    return None


# ---------------------------------------------------------------------- #
# ToolSpec: 工具描述符
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolSpec:
    """工具描述符：由 ``@fx.tool`` 装饰器注册。

    封装函数 + CLI 元数据 + DAG 编排参数，运行时映射为 :class:`TaskSpec`。

    参数
    ----
    name:
        工具名（如 ``"pymake"``）；多个 ``@fx.tool`` 共用同名即多 subcommand 工具
    subcommand:
        子命令名；``None`` 表示单命令工具（整个工具仅一个函数）
    func:
        被装饰的函数（签名驱动 CLI，函数体即逻辑）
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

    name: str
    subcommand: str | None
    func: Callable[..., Any]
    help: str = ""
    description: str = ""
    cmd: tuple[str, ...] | str | None = None
    needs: tuple[str, ...] = ()
    strategy: Literal["sequential", "thread", "async", "dependency"] | None = None
    cwd: str | Path | None = None
    allow_upstream_skip: bool = False
    hidden: bool = False
    env: Mapping[str, str] | None = None
    retry: RetryPolicy | None = None
    timeout: float | None = None


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
# argparse parser 构建（函数签名 → argparse 参数）
# ---------------------------------------------------------------------- #
def _resolve_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """解析函数的类型注解（处理 from __future__ import annotations 的字符串注解）。

    Python 3.8 下 ``typing.get_type_hints`` 对 PEP 604 (``X | Y``) 和
    PEP 585 (``list[X]``) 泛型语法会抛 :class:`TypeError`，导致整个函数的
    注解解析失败。本函数在 ``get_type_hints`` 失败时逐参数回退：用 ``eval``
    求值字符串注解（类型注解是开发者代码，非用户输入，安全），单个参数失败
    不影响其他参数，失败参数保留字符串形式供下游处理。
    """
    try:
        return typing.get_type_hints(func)
    except Exception:
        # get_type_hints 整体失败（如返回类型用了 X|Y），逐参数 eval 回退
        sig = inspect.signature(func)
        globalns = getattr(func, "__globals__", {})
        hints: dict[str, Any] = {}
        for pname, param in sig.parameters.items():
            if param.annotation is inspect.Parameter.empty:
                continue
            if not isinstance(param.annotation, str):
                hints[pname] = param.annotation
                continue
            try:
                # 类型注解求值是 typing.get_type_hints 的标准做法，非用户输入
                hints[pname] = eval(param.annotation, globalns)
            except Exception:
                # eval 失败（如 list[X] 在 3.8），保留字符串供下游处理
                hints[pname] = param.annotation
        return hints


def _is_list_annotation(annotation: Any) -> bool:
    """判断注解是否为 list[X] / List[X] 类型。"""
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        return True
    ann_str = str(annotation)
    return ann_str.startswith("list[") or ann_str.startswith("List[")


def _list_inner_type(annotation: Any) -> Any:
    """提取 list[X] 的内部类型 X，无法提取时返回 None。"""
    args = getattr(annotation, "__args__", None)
    if args:
        return args[0]
    ann_str = str(annotation)
    for prefix in ("list[", "List["):
        if ann_str.startswith(prefix) and ann_str.endswith("]"):
            return ann_str[len(prefix) : -1].strip()
    return None


def _is_literal_annotation(annotation: Any) -> bool:
    """判断注解是否为 ``Literal[X, Y, ...]`` 类型。"""
    origin = getattr(annotation, "__origin__", None)
    return origin is typing.Literal


def _literal_choices(annotation: Any) -> tuple[Any, ...]:
    """提取 ``Literal[X, Y, ...]`` 的选项值元组。"""
    return getattr(annotation, "__args__", ())


def _add_optional_arg(
    parser: argparse.ArgumentParser,
    pname: str,
    annotation: Any,
    default: Any,
) -> None:
    """添加 --name 选项（有默认值的参数）。

    支持的注解类型：
    - ``bool``（默认 ``False``）→ ``store_true``
    - ``int`` / ``float`` / ``str`` / ``Path`` → 对应 ``type``
    - ``Literal[X, Y, ...]`` → ``choices``（argparse 自动校验取值）
    - ``list[X]`` / ``List[X]`` → ``nargs="*"`` + 对应 ``type``
    """
    cli_name = f"--{pname.replace('_', '-')}"
    if annotation is bool or (isinstance(default, bool) and default is False):
        parser.add_argument(cli_name, action="store_true", default=False, help=pname)
        return
    kwargs: dict[str, Any] = {"default": default, "help": pname}
    if annotation in (int, float, str):
        kwargs["type"] = annotation
    elif annotation is Path:
        kwargs["type"] = Path
    elif _is_literal_annotation(annotation):
        # _is_literal_annotation 为 True 时 __args__ 一定存在且非空
        kwargs["choices"] = list(_literal_choices(annotation))
    elif _is_list_annotation(annotation):
        inner = _list_inner_type(annotation)
        kwargs["nargs"] = "*"
        if inner in (Path, "Path", "pathlib.Path"):
            kwargs["type"] = Path
        elif inner in (int, "int"):
            kwargs["type"] = int
        elif inner in (float, "float"):
            kwargs["type"] = float
        elif inner in (str, "str"):
            kwargs["type"] = str
    parser.add_argument(cli_name, **kwargs)


def _add_positional_arg(
    parser: argparse.ArgumentParser,
    pname: str,
    annotation: Any,
) -> None:
    """添加 positional 参数（无默认值的参数）。

    支持的注解类型：
    - ``int`` / ``float`` / ``str`` / ``Path`` → 对应 ``type``
    - ``Literal[X, Y, ...]`` → ``choices``
    - ``list[X]`` / ``List[X]`` → ``nargs="+"`` + 对应 ``type``
    """
    if _is_list_annotation(annotation):
        inner = _list_inner_type(annotation)
        kwargs: dict[str, Any] = {"nargs": "+", "help": pname}
        if inner in (Path, "Path", "pathlib.Path"):
            kwargs["type"] = Path
        elif inner in (int, "int"):
            kwargs["type"] = int
        elif inner in (float, "float"):
            kwargs["type"] = float
        elif inner in (str, "str"):
            kwargs["type"] = str
        parser.add_argument(pname, **kwargs)
    elif annotation in (int, float, str):
        parser.add_argument(pname, type=annotation, help=pname)
    elif annotation is Path:
        parser.add_argument(pname, type=Path, help=pname)
    elif _is_literal_annotation(annotation):
        # _is_literal_annotation 为 True 时 __args__ 一定存在且非空
        kwargs = {"help": pname, "choices": list(_literal_choices(annotation))}
        parser.add_argument(pname, **kwargs)
    else:
        parser.add_argument(pname, help=pname)


def _build_parser_for_tool(spec: ToolSpec) -> argparse.ArgumentParser:
    """为单个 ToolSpec 构建 argparse parser。

    函数签名映射规则：
    - 有默认值 → ``--name`` 选项
    - 无默认值 → positional 参数
    - ``bool`` 且默认 ``False`` → ``store_true``
    - ``list[X]`` / ``List[X]`` → ``nargs="+"`` (positional) 或 ``nargs="*"`` (optional)
    - ``int`` / ``float`` / ``str`` / ``Path`` → 对应 ``type``
    - ``Literal[X, Y, ...]`` → ``choices``（argparse 自动校验取值）
    """
    hints = _resolve_hints(spec.func)
    sig = inspect.signature(spec.func)
    prog = spec.name if spec.subcommand is None else f"{spec.name} {spec.subcommand}"
    description = spec.help or inspect.getdoc(spec.func) or ""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(pname, param.annotation)
        if param.default is inspect.Parameter.empty:
            _add_positional_arg(parser, pname, annotation)
        else:
            _add_optional_arg(parser, pname, annotation, param.default)
    _add_global_options(parser)
    return parser


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    """为 parser 添加全局选项（--dry-run / --quiet / --strategy）。"""
    parser.add_argument("--dry-run", action="store_true", default=False, help="仅打印执行计划，不执行")
    parser.add_argument("-q", "--quiet", action="store_true", default=False, help="减少输出")
    parser.add_argument(
        "--strategy",
        default=None,
        help="执行策略 (sequential/thread/async/dependency)",
    )


# ---------------------------------------------------------------------- #
# run_tool 主入口
# ---------------------------------------------------------------------- #
def run_tool(name: str, argv: Sequence[str]) -> int:  # noqa: PLR0911, PLR0912
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

    # 纯单命令工具（仅有 None 子命令）：target=None，全部 argv 透传给 parser
    if None in subs and len(subs) == 1:
        target: str | None = None
        argv_rest: list[str] = list(argv)
    elif argv and not argv[0].startswith("-"):
        target = argv[0]
        argv_rest = list(argv[1:])
    elif None in subs:
        target = None
        argv_rest = list(argv)
    else:
        # 列出工具的所有子命令
        _print_subcommands(name)
        return ToolExitCode.SUCCESS.value

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

    # 收集 target 及其传递依赖，构建 TaskSpec 列表
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
            graph,
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
                for fname in e.report.failed_tasks():
                    r = e.report.result_of(fname)
                    err_console.print(f"  [yellow]{fname}[/yellow]: {r.status.value} error={r.error!r}")
        return ToolExitCode.FAILURE.value
    except FcmdError as e:
        if verbose:
            get_console().print(f"[red]错误:[/red] {e}")
        return ToolExitCode.FAILURE.value
    except KeyboardInterrupt:
        return ToolExitCode.INTERRUPTED.value

    return ToolExitCode.SUCCESS.value if report.success else ToolExitCode.FAILURE.value


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


def _print_subcommands(name: str) -> None:
    """打印工具的所有非 hidden 子命令。"""
    from rich.table import Table

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
