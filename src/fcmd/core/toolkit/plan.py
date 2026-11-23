"""编排规划：needs / steps 依赖边、执行顺序、前置断言与聚合判定。"""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import InjectionError
from .spec import ToolSpec


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
