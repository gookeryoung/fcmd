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
本模块按职责拆分为三层：

* :mod:`fcmd.apis._tool_args` —— **参数解析**（ToolSpec / ToolExitCode /
  类型注解解析 / argparse parser 构建）。
* :mod:`fcmd.apis._tool_exec` —— **执行层**（依赖收集 / TaskSpec 构建 /
  argv 路由 / DAG 执行 / 汇总输出）。本模块单向依赖它，无循环。
* 本模块 —— **注册表 + 装饰器 + 公共入口**：
  ``@fx.tool`` / ``@fx.main`` 装饰器、工具注册表、:func:`run_tool` /
  :func:`build_tool_graph` 公共入口（从注册表取出 ``subs`` 后委托执行层）。
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

import inspect
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from fcmd.console import get_console

from ._tool_args import (
    ToolExitCode,
    ToolSpec,
    _add_global_options,  # noqa: F401  重新导出供测试 import
    _add_optional_arg,  # noqa: F401  重新导出供测试 import
    _add_positional_arg,  # noqa: F401  重新导出供测试 import
    _annotation_str_to_type,  # noqa: F401  重新导出供测试 import
    _build_parser_for_tool,  # noqa: F401  重新导出供测试 import
    _is_list_annotation,  # noqa: F401  重新导出供测试 import
    _is_literal_annotation,  # noqa: F401  重新导出供测试 import
    _list_inner_type,  # noqa: F401  重新导出供测试 import
    _literal_choices,  # noqa: F401  重新导出供测试 import
    _noop,  # noqa: F401  重新导出供测试 import
    _resolve_hints,  # noqa: F401  重新导出供测试 import
    _unwrap_optional,  # noqa: F401  重新导出供测试 import
)
from ._tool_exec import (
    _build_task_spec,
    _collect_with_deps,
    _execute_tool_tasks,
    _has_function_logic,  # noqa: F401  重新导出供测试 import
    _is_aggregate,  # noqa: F401  重新导出供测试 import
    _parse_tool_args,
    _print_subcommands,  # noqa: F401  重新导出供测试 import
    _print_task_summary,  # noqa: F401  重新导出供测试 import
    _resolve_tool_target,
)
from .dag import Graph, GraphDefaults
from .errors import FcmdError
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
# 公共入口：run_tool / build_tool_graph
# ---------------------------------------------------------------------- #
def run_tool(name: str, argv: Sequence[str]) -> int:
    """运行工具：解析 argv、构建 DAG、执行并返回退出码。

    本函数是薄入口：从 ``_TOOL_REGISTRY`` 取出 ``subs`` 后，依次委托执行层的
    :func:`_resolve_tool_target` → :func:`_parse_tool_args` → :func:`_execute_tool_tasks`。

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
    return _execute_tool_tasks(name, target, variables, target_spec, subs)


def build_tool_graph(name: str, target: str | None) -> Graph:
    """构建工具的 DAG（不执行），用于可视化与内省。

    复用执行层的 :func:`_collect_with_deps`（BFS 依赖收集）与
    :func:`_build_task_spec`（TaskSpec 构建），但不调用 :func:`run`，仅返回 :class:`Graph`。

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
        chain = _collect_with_deps(subs, target)
        selected = [subs[sc] for sc in chain if sc in subs]
    task_specs: list[TaskSpec[Any]] = [_build_task_spec(spec, {}) for spec in selected]
    return Graph.from_specs(task_specs, defaults=GraphDefaults())
