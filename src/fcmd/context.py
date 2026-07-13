"""上下文注入：把上游结果转换为函数参数。

本机制让用户可以编写普通函数，其参数名*就是*依赖声明，从而消除其他
DAG 库中泛滥的样板包装器。

注入规则（按顺序求值）
----------------------
1. **标注为** :class:`Context` 的参数接收完整结果映射（含硬依赖与软依赖）。
2. **名称匹配某个依赖**（硬或软）的参数接收该依赖的结果。
3. ``**kwargs`` 参数以 dict 形式接收*所有*依赖结果。
4. ``TaskSpec.args`` / ``TaskSpec.kwargs`` 为*非依赖*参数提供静态值。

若某参数无法解析且无默认值，则抛出 :class:`~fcmd.errors.InjectionError`。
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from .errors import InjectionError
from .task import Context, TaskSpec

__all__ = ["Context", "build_call_args", "describe_injection", "is_context_annotation"]


@lru_cache(maxsize=1024)
def _cached_signature(fn: Any) -> inspect.Signature:
    """缓存 ``inspect.signature`` 结果（按 fn 对象键控）。

    ``fn`` 对象在 :meth:`TaskSpec.effective_fn` 缓存后稳定，签名重复内省
    属纯开销。对不可哈希的可调用对象，调用方回退到直接内省。
    """
    return inspect.signature(fn)


def _signature(fn: Any) -> inspect.Signature:
    """获取签名，优先走缓存；``fn`` 不可哈希时回退到直接内省。"""
    try:
        return _cached_signature(fn)
    except TypeError:
        return inspect.signature(fn)


@lru_cache(maxsize=1024)
def _fn_no_dep_injection(fn: Any) -> tuple[tuple[str, ...] | None, str | None]:
    """预计算 fn 在无依赖/无静态参数时的注入计划。

    返回 ``(context_params, error_param)``：
    - ``context_params``: Context 标注参数名元组；``None`` 表示存在必填参数需走慢路径。
    - ``error_param``: 第一个无默认值且非 Context 标注的参数名（用于错误信息）；``None`` 表示无此类参数。
    """
    sig = inspect.signature(fn)
    context_params: list[str] = []
    for pname, param in sig.parameters.items():
        if is_context_annotation(param.annotation):
            context_params.append(pname)
            continue
        if param.default is inspect.Parameter.empty and param.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            return None, pname
    return tuple(context_params), None


def is_context_annotation(annotation: Any) -> bool:
    """判断参数标注是否为（或指向）``Context``。"""
    if annotation is Context:
        return True
    if isinstance(annotation, str):
        return annotation == "Context" or annotation.endswith(".Context")
    name = getattr(annotation, "__name__", None) or getattr(annotation, "_name", None)
    return name in ("Context", "Mapping")


def _try_fast_path(spec: TaskSpec[Any]) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
    """尝试快速路径：cmd 无参任务或 fn 无依赖任务。

    返回 ``None`` 表示快速路径不适用，需走慢路径。
    """
    # 快速路径 1：cmd 任务（无 fn）的 effective_fn 是无参闭包。
    if spec.fn is None and spec.cmd is not None and not spec.args and not spec.kwargs:
        return (), {}
    # 快速路径 2：fn 任务无依赖、无静态 args/kwargs 时，跳过 dep_context/collisions/leftover 构建。
    if not spec.depends_on and not spec.soft_depends_on and not spec.args and not spec.kwargs:
        fn = spec.effective_fn
        context_params, error_param = _fn_no_dep_injection(fn)
        if error_param is not None:
            raise InjectionError(
                spec.name,
                f"parameter {error_param!r} has no dependency, static value, or default.",
            )
        assert context_params is not None  # error_param is None ⟺ context_params 非 None
        return (), {p: {} for p in context_params}
    return None


def build_call_args(
    spec: TaskSpec[Any],
    context: Mapping[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """解析用于调用 ``spec.fn`` 的 ``(args, kwargs)``。

    ``context`` 必须已包含所有硬依赖与软依赖的结果（软依赖被跳过时由
    执行器填入 :attr:`TaskSpec.defaults` 中的默认值）。
    """
    fast = _try_fast_path(spec)
    if fast is not None:
        return fast

    fn = spec.effective_fn
    sig = _signature(fn)
    params = sig.parameters

    var_keyword = next(
        (p for p in params.values() if p.kind == inspect.Parameter.VAR_KEYWORD),
        None,
    )

    # 本任务相关的上下文子集：硬依赖 + 软依赖。
    all_deps = set(spec.depends_on) | set(spec.soft_depends_on)
    dep_context: dict[str, Any] = {name: context[name] for name in all_deps if name in context}

    collisions = set(spec.kwargs) & set(dep_context)
    if collisions:
        raise InjectionError(
            spec.name,
            f"static kwargs {sorted(collisions)} collide with dependency names; "
            + "rename the static kwarg or the dependency.",
        )

    injected_kwargs: dict[str, Any] = {}
    leftover_dep_results: dict[str, Any] = dict(dep_context)

    positional_params: list[str] = []
    positional_kinds = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    for pname, param in params.items():
        if param.kind in positional_kinds:
            positional_params.append(pname)
    args_filled = set(positional_params[: len(spec.args)])

    for pname, param in params.items():
        if pname in args_filled:
            continue

        if is_context_annotation(param.annotation):
            injected_kwargs[pname] = dep_context
            continue

        if pname in dep_context:
            injected_kwargs[pname] = dep_context[pname]
            leftover_dep_results.pop(pname, None)
            continue

        if pname in spec.kwargs:
            injected_kwargs[pname] = spec.kwargs[pname]
            continue

        if param.default is inspect.Parameter.empty and param.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise InjectionError(
                spec.name,
                f"parameter {pname!r} has no dependency, static value, or default.",
            )

    if var_keyword is not None and leftover_dep_results:
        merged = dict(spec.kwargs)
        merged.update(injected_kwargs)
        merged.update(leftover_dep_results)
        injected_kwargs = merged

    return tuple(spec.args), injected_kwargs


def describe_injection(spec: TaskSpec[Any]) -> str:
    """生成任务参数注入方式的人类可读描述。供 ``dry_run`` 使用。"""
    fn = spec.effective_fn
    sig = _signature(fn)
    positional_params = [
        p
        for p, param in sig.parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    args_filled = set(positional_params[: len(spec.args)])
    all_deps = set(spec.depends_on) | set(spec.soft_depends_on)
    parts: list[str] = []
    for pname, param in sig.parameters.items():
        if pname in args_filled:
            idx = positional_params.index(pname)
            parts.append(f"{pname}={spec.args[idx]!r}")
        elif is_context_annotation(param.annotation):
            parts.append(f"{pname}=<Context>")
        elif pname in all_deps:
            tag = "soft" if pname in spec.soft_depends_on else "dep"
            parts.append(f"{pname}=<{tag}:{pname}>")
        elif pname in spec.kwargs:
            parts.append(f"{pname}={spec.kwargs[pname]!r}")
        elif param.default is not inspect.Parameter.empty:
            parts.append(f"{pname}=<default>")
        elif param.kind == inspect.Parameter.VAR_KEYWORD:
            parts.append("**kwargs=<all-deps>")
        elif param.kind == inspect.Parameter.VAR_POSITIONAL:
            parts.append("*args")
        else:
            parts.append(f"{pname}=<UNRESOLVED>")
    return f"{spec.name}({', '.join(parts)})"
