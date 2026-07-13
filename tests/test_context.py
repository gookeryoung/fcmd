"""上下文注入测试。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from fcmd.context import (
    Context,
    build_call_args,
    describe_injection,
    is_context_annotation,
)
from fcmd.errors import InjectionError
from fcmd.task import TaskSpec


# ---------------------------------------------------------------------- #
# is_context_annotation
# ---------------------------------------------------------------------- #
def test_is_context_annotation_context_type() -> None:
    """Context 类型标注识别。"""
    assert is_context_annotation(Context) is True


def test_is_context_annotation_string() -> None:
    """字符串标注 "Context" 识别。"""
    assert is_context_annotation("Context") is True
    assert is_context_annotation("fcmd.task.Context") is True


def test_is_context_annotation_mapping_name() -> None:
    """Mapping __name__ 识别。"""
    assert is_context_annotation(Mapping) is True


def test_is_context_annotation_other() -> None:
    """其他类型不识别。"""
    assert is_context_annotation(int) is False
    assert is_context_annotation(str) is False
    assert is_context_annotation(None) is False


# ---------------------------------------------------------------------- #
# build_call_args 快速路径
# ---------------------------------------------------------------------- #
def test_build_call_args_fast_path_cmd() -> None:
    """cmd 无参快速路径返回 ((), {})。"""
    spec = TaskSpec(name="x", cmd=["echo", "hi"])
    args, kwargs = build_call_args(spec, {})
    assert args == ()
    assert kwargs == {}


def test_build_call_args_fast_path_fn_no_deps() -> None:
    """fn 无依赖无静态参数快速路径。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    args, kwargs = build_call_args(spec, {})
    assert args == ()
    assert kwargs == {}


def test_build_call_args_fast_path_fn_with_context_param() -> None:
    """fn 无依赖但有 Context 标注参数。"""

    def fn(ctx: Context) -> int:
        return 1

    spec = TaskSpec(name="x", fn=fn)
    args, kwargs = build_call_args(spec, {})
    assert args == ()
    assert kwargs == {"ctx": {}}


def test_build_call_args_fast_path_fn_unresolved_param() -> None:
    """fn 无依赖但有必填参数抛 InjectionError。"""

    def fn(missing: int) -> int:
        return missing

    spec = TaskSpec(name="x", fn=fn)
    with pytest.raises(InjectionError, match="missing"):
        build_call_args(spec, {})


# ---------------------------------------------------------------------- #
# build_call_args 慢路径
# ---------------------------------------------------------------------- #
def test_build_call_args_dep_injection() -> None:
    """参数名匹配依赖注入值。"""

    def double(extract: int) -> int:
        return extract * 2

    spec = TaskSpec(name="double", fn=double, depends_on=("extract",))
    args, kwargs = build_call_args(spec, {"extract": 21})
    assert args == ()
    assert kwargs == {"extract": 21}


def test_build_call_args_context_annotation_injection() -> None:
    """Context 标注参数接收完整 dep_context。"""

    def fn(extract: int, ctx: Context) -> int:
        return extract

    spec = TaskSpec(name="fn", fn=fn, depends_on=("extract",))
    args, kwargs = build_call_args(spec, {"extract": 21})
    assert args == ()
    assert kwargs == {"extract": 21, "ctx": {"extract": 21}}


def test_build_call_args_var_keyword() -> None:
    """**kwargs 接收所有依赖结果。"""

    def fn(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    spec = TaskSpec(name="fn", fn=fn, depends_on=("a", "b"))
    args, kwargs = build_call_args(spec, {"a": 1, "b": 2})
    assert args == ()
    assert kwargs == {"a": 1, "b": 2}


def test_build_call_args_static_kwargs() -> None:
    """spec.kwargs 提供非依赖参数。"""

    def fn(multiplier: int = 1) -> int:
        return multiplier

    spec = TaskSpec(name="fn", fn=fn, kwargs={"multiplier": 10})
    _, kwargs = build_call_args(spec, {})
    assert kwargs == {"multiplier": 10}


def test_build_call_args_collision() -> None:
    """static kwargs 与依赖名冲突抛 InjectionError。"""

    def fn(extract: int) -> int:
        return extract

    spec = TaskSpec(name="fn", fn=fn, depends_on=("extract",), kwargs={"extract": 999})
    with pytest.raises(InjectionError, match="collide"):
        build_call_args(spec, {"extract": 1})


def test_build_call_args_unresolved() -> None:
    """参数无依赖无默认值抛 InjectionError。"""

    def fn(missing: int) -> int:
        return missing

    spec = TaskSpec(name="fn", fn=fn, depends_on=("extract",))
    with pytest.raises(InjectionError, match="missing"):
        build_call_args(spec, {"extract": 1})


def test_build_call_args_static_args() -> None:
    """spec.args 填充位置参数。"""

    def fn(a: int, b: int) -> int:
        return a + b

    spec = TaskSpec(name="fn", fn=fn, args=(1, 2))
    args, kwargs = build_call_args(spec, {})
    assert args == (1, 2)
    assert kwargs == {}


def test_build_call_args_soft_dependency_with_value() -> None:
    """软依赖上游成功注入其值。"""

    def fn(optional: int) -> int:
        return optional

    spec = TaskSpec(name="fn", fn=fn, soft_depends_on=("optional",))
    _, kwargs = build_call_args(spec, {"optional": 42})
    assert kwargs == {"optional": 42}


def test_build_call_args_soft_dependency_with_default() -> None:
    """软依赖上游无值时注入 defaults。"""

    def fn(optional: int) -> int:
        return optional

    spec = TaskSpec(
        name="fn",
        fn=fn,
        soft_depends_on=("optional",),
        defaults={"optional": 99},
    )
    # 软依赖未在 context 中时，build_call_args 仍尝试从 context 取
    # 实际注入由 executor 在 _build_context 中处理
    # optional 不在 context，未匹配到依赖，也无 default → InjectionError
    # 但 spec.defaults 是软依赖默认值，build_call_args 不直接使用
    # 这里测试的是：optional 不在 context 时，参数无默认值 → 抛 InjectionError
    with pytest.raises(InjectionError):
        build_call_args(spec, {})


def test_build_call_args_param_with_default_value() -> None:
    """参数有默认值时不抛 InjectionError。"""

    def fn(missing: int = 100) -> int:
        return missing

    spec = TaskSpec(name="fn", fn=fn)
    args, kwargs = build_call_args(spec, {})
    assert args == ()
    assert kwargs == {}


# ---------------------------------------------------------------------- #
# describe_injection
# ---------------------------------------------------------------------- #
def test_describe_injection_no_params() -> None:
    """无参数任务描述。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    desc = describe_injection(spec)
    assert "x()" in desc


def test_describe_injection_with_dep() -> None:
    """有依赖任务描述。"""

    def fn(extract: int) -> int:
        return extract

    spec = TaskSpec(name="fn", fn=fn, depends_on=("extract",))
    desc = describe_injection(spec)
    assert "extract=<dep:extract>" in desc


def test_describe_injection_with_soft_dep() -> None:
    """软依赖描述。"""

    def fn(optional: int) -> int:
        return optional

    spec = TaskSpec(name="fn", fn=fn, soft_depends_on=("optional",))
    desc = describe_injection(spec)
    assert "optional=<soft:optional>" in desc


def test_describe_injection_with_context() -> None:
    """Context 标注参数描述。"""

    def fn(ctx: Context) -> int:
        return 1

    spec = TaskSpec(name="fn", fn=fn)
    desc = describe_injection(spec)
    assert "ctx=<Context>" in desc


def test_describe_injection_with_static_args() -> None:
    """静态位置参数描述。"""

    def fn(a: int, b: int) -> int:
        return a + b

    spec = TaskSpec(name="fn", fn=fn, args=(1, 2))
    desc = describe_injection(spec)
    assert "a=1" in desc
    assert "b=2" in desc


def test_describe_injection_with_static_kwargs() -> None:
    """静态关键字参数描述。"""

    def fn(multiplier: int = 1) -> int:
        return multiplier

    spec = TaskSpec(name="fn", fn=fn, kwargs={"multiplier": 10})
    desc = describe_injection(spec)
    assert "multiplier=10" in desc


def test_describe_injection_with_default() -> None:
    """有默认值参数描述。"""

    def fn(missing: int = 100) -> int:
        return missing

    spec = TaskSpec(name="fn", fn=fn)
    desc = describe_injection(spec)
    assert "missing=<default>" in desc


def test_describe_injection_with_var_keyword() -> None:
    """**kwargs 参数描述。"""

    def fn(**kwargs: Any) -> int:
        return 1

    spec = TaskSpec(name="fn", fn=fn)
    desc = describe_injection(spec)
    assert "**kwargs=<all-deps>" in desc


def test_describe_injection_with_var_positional() -> None:
    """*args 参数描述。"""

    def fn(*args: int) -> int:
        return 1

    spec = TaskSpec(name="fn", fn=fn)
    desc = describe_injection(spec)
    assert "*args" in desc
