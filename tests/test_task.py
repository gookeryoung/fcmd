"""TaskSpec 核心数据结构测试。"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from fcmd.task import (
    RetryPolicy,
    TaskResult,
    TaskSpec,
    TaskStatus,
    cmd,
    task,
)


# ---------------------------------------------------------------------- #
# TaskSpec 构造与校验
# ---------------------------------------------------------------------- #
def test_task_spec_minimal() -> None:
    """最小字段构造（仅 name + fn）。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    assert spec.name == "x"
    assert spec.fn is not None
    assert spec.cmd is None
    assert spec.depends_on == ()
    assert spec.retry == RetryPolicy()


def test_task_spec_validation_empty_name() -> None:
    """name 为空抛 ValueError。"""
    with pytest.raises(ValueError, match="name"):
        TaskSpec(name="", fn=lambda: 1)


def test_task_spec_validation_no_fn_no_cmd() -> None:
    """无 fn 无 cmd 抛 ValueError。"""
    with pytest.raises(ValueError, match="必须提供 fn 或 cmd"):
        TaskSpec(name="x")


def test_task_spec_validation_self_dependency() -> None:
    """自依赖抛 ValueError。"""
    with pytest.raises(ValueError, match="依赖自身"):
        TaskSpec(name="x", fn=lambda: 1, depends_on=("x",))


def test_task_spec_validation_self_soft_dependency() -> None:
    """自软依赖抛 ValueError。"""
    with pytest.raises(ValueError, match="依赖自身"):
        TaskSpec(name="x", fn=lambda: 1, soft_depends_on=("x",))


def test_task_spec_validation_dep_overlap() -> None:
    """硬软依赖重叠抛 ValueError。"""
    with pytest.raises(ValueError, match="重叠"):
        TaskSpec(name="x", fn=lambda: 1, depends_on=("a",), soft_depends_on=("a",))


def test_task_spec_validation_timeout_zero() -> None:
    """timeout <= 0 抛 ValueError。"""
    with pytest.raises(ValueError, match="timeout"):
        TaskSpec(name="x", fn=lambda: 1, timeout=0)


def test_task_spec_validation_timeout_negative() -> None:
    """timeout 负数抛 ValueError。"""
    with pytest.raises(ValueError, match="timeout"):
        TaskSpec(name="x", fn=lambda: 1, timeout=-1)


# ---------------------------------------------------------------------- #
# RetryPolicy
# ---------------------------------------------------------------------- #
def test_retry_policy_default() -> None:
    """默认 RetryPolicy：max_attempts=1，不重试。"""
    policy = RetryPolicy()
    assert policy.max_attempts == 1
    assert policy.retries == 0
    assert policy.should_retry(RuntimeError("x"))  # 默认 retry_on=(Exception,)


def test_retry_policy_validation_max_attempts() -> None:
    """max_attempts < 1 抛 ValueError。"""
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)


def test_retry_policy_validation_delay_negative() -> None:
    """delay < 0 抛 ValueError。"""
    with pytest.raises(ValueError, match="delay"):
        RetryPolicy(delay=-1)


def test_retry_policy_validation_backoff_negative() -> None:
    """backoff < 0 抛 ValueError。"""
    with pytest.raises(ValueError, match="backoff"):
        RetryPolicy(backoff=-1)


def test_retry_policy_validation_jitter_negative() -> None:
    """jitter < 0 抛 ValueError。"""
    with pytest.raises(ValueError, match="jitter"):
        RetryPolicy(jitter=-1)


def test_retry_policy_should_retry_filtered() -> None:
    """retry_on 过滤：仅对指定异常重试。"""
    policy = RetryPolicy(max_attempts=3, retry_on=(ValueError,))
    assert policy.should_retry(ValueError("x"))
    assert not policy.should_retry(TypeError("x"))


def test_retry_policy_should_retry_empty() -> None:
    """retry_on 空元组等价于不重试。"""
    policy = RetryPolicy(max_attempts=3, retry_on=())
    assert not policy.should_retry(ValueError("x"))


def test_retry_policy_wait_seconds() -> None:
    """wait_seconds 退避计算。"""
    policy = RetryPolicy(delay=1.0, backoff=2.0)
    assert policy.wait_seconds(0) == 0.0
    assert policy.wait_seconds(1) == 1.0
    assert policy.wait_seconds(2) == 2.0
    assert policy.wait_seconds(3) == 4.0


def test_retry_policy_wait_seconds_jitter() -> None:
    """wait_seconds 含 jitter 时返回值在 [base, base+jitter) 区间。"""
    policy = RetryPolicy(delay=1.0, jitter=0.5)
    wait = policy.wait_seconds(1)
    assert 1.0 <= wait < 1.5


def test_retry_policy_retries_property() -> None:
    """retries 属性 = max_attempts - 1。"""
    assert RetryPolicy(max_attempts=1).retries == 0
    assert RetryPolicy(max_attempts=3).retries == 2


# ---------------------------------------------------------------------- #
# TaskSpec.effective_fn
# ---------------------------------------------------------------------- #
def test_task_spec_effective_fn_fn() -> None:
    """fn 任务：effective_fn 直接返回 fn。"""

    def my_fn() -> int:
        return 42

    spec = TaskSpec(name="x", fn=my_fn)
    assert spec.effective_fn is my_fn
    assert spec.effective_fn() == 42


def test_task_spec_effective_fn_cmd() -> None:
    """cmd 任务：effective_fn 包装为可执行函数。"""
    if sys.platform == "win32":
        spec = TaskSpec(name="x", cmd=["cmd", "/c", "echo", "hello"])
    else:
        spec = TaskSpec(name="x", cmd=["echo", "hello"])
    fn = spec.effective_fn
    assert fn.__name__ == "x"
    # 执行 cmd（echo hello 返回 None）
    result = fn()
    assert result is None


def test_task_spec_effective_fn_callable_cmd() -> None:
    """cmd 为 callable 时与 fn 等效。"""
    spec = TaskSpec(name="x", cmd=lambda: 100)
    assert spec.effective_fn() == 100


# ---------------------------------------------------------------------- #
# TaskSpec.should_execute
# ---------------------------------------------------------------------- #
def test_task_spec_should_execute_no_conditions() -> None:
    """无条件返回 (True, None)。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    should, reason = spec.should_execute({})
    assert should is True
    assert reason is None


def test_task_spec_should_execute_conditions_pass() -> None:
    """条件全 True 返回 (True, None)。"""

    def cond(_ctx: Mapping[str, Any]) -> bool:
        return True

    spec = TaskSpec(name="x", fn=lambda: 1, conditions=(cond,))
    should, reason = spec.should_execute({})
    assert should is True
    assert reason is None


def test_task_spec_should_execute_conditions_fail() -> None:
    """条件 False 返回 (False, reason)。"""

    def my_cond(_ctx: Mapping[str, Any]) -> bool:
        return False

    my_cond.__name__ = "my_cond"
    spec = TaskSpec(name="x", fn=lambda: 1, conditions=(my_cond,))
    should, reason = spec.should_execute({})
    assert should is False
    assert reason is not None
    assert "my_cond" in reason


def test_task_spec_should_execute_conditions_exception() -> None:
    """条件抛异常视为不满足。"""

    def bad_cond(_ctx: Mapping[str, Any]) -> bool:
        raise RuntimeError("boom")

    spec = TaskSpec(name="x", fn=lambda: 1, conditions=(bad_cond,))
    should, reason = spec.should_execute({})
    assert should is False
    assert reason is not None
    assert "匿名条件(执行错误)" in reason


def test_task_spec_should_execute_conditions_with_reason_attr() -> None:
    """条件带 _reason 属性时使用自定义原因。"""

    def cond(_ctx: Mapping[str, Any]) -> bool:
        return False

    cond._reason = "自定义原因"  # type: ignore[attr-defined]
    spec = TaskSpec(name="x", fn=lambda: 1, conditions=(cond,))
    should, reason = spec.should_execute({})
    assert should is False
    assert reason is not None
    assert "自定义原因" in reason


def test_task_spec_should_execute_conditions_list_reason() -> None:
    """条件 _reason 为列表时拼接展示。"""

    def cond(_ctx: Mapping[str, Any]) -> bool:
        return False

    cond._reason = ["原因1", "原因2"]  # type: ignore[attr-defined]
    spec = TaskSpec(name="x", fn=lambda: 1, conditions=(cond,))
    should, reason = spec.should_execute({})
    assert should is False
    assert reason is not None
    assert "原因1" in reason and "原因2" in reason


def test_task_spec_should_execute_many_conditions() -> None:
    """失败条件 > 2 个时仅展示前 2 个并附总数。"""
    conditions: list[Any] = []
    for i in range(3):

        def cond(_ctx: Mapping[str, Any], _i: int = i) -> bool:
            return False

        cond.__name__ = f"cond{i}"
        conditions.append(cond)
    spec = TaskSpec(name="x", fn=lambda: 1, conditions=tuple(conditions))
    should, reason = spec.should_execute({})
    assert should is False
    assert reason is not None
    assert "等3个条件" in reason


# ---------------------------------------------------------------------- #
# TaskSpec.env_context
# ---------------------------------------------------------------------- #
def test_task_spec_env_context_no_env_no_cwd() -> None:
    """无 env 无 cwd 时直接 yield。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    with spec.env_context():
        pass  # 不应抛异常


def test_task_spec_env_context_with_cwd() -> None:
    """cwd 临时切换后恢复。"""

    original_cwd = str(Path.cwd())
    spec = TaskSpec(name="x", fn=lambda: 1, cwd=Path())
    with spec.env_context():
        # cwd 被设置为 Path(".")，与原 cwd 相同
        assert str(Path.cwd()) == str(Path().resolve()) or str(Path.cwd()) == original_cwd
    assert str(Path.cwd()) == original_cwd


def test_task_spec_env_context_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 临时设置后恢复。"""
    import os

    monkeypatch.delenv("FCMD_TEST_VAR", raising=False)
    spec = TaskSpec(name="x", fn=lambda: 1, env={"FCMD_TEST_VAR": "hello"})
    with spec.env_context():
        assert os.environ["FCMD_TEST_VAR"] == "hello"
    assert "FCMD_TEST_VAR" not in os.environ


def test_task_spec_env_context_restores_existing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 覆盖已存在变量后恢复原值。"""
    import os

    monkeypatch.setenv("FCMD_TEST_VAR", "original")
    spec = TaskSpec(name="x", fn=lambda: 1, env={"FCMD_TEST_VAR": "override"})
    with spec.env_context():
        assert os.environ["FCMD_TEST_VAR"] == "override"
    assert os.environ["FCMD_TEST_VAR"] == "original"


# ---------------------------------------------------------------------- #
# TaskResult
# ---------------------------------------------------------------------- #
def test_task_result_default() -> None:
    """TaskResult 默认状态为 PENDING。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    result = TaskResult(spec=spec)
    assert result.status == TaskStatus.PENDING
    assert result.value is None
    assert result.error is None
    assert result.attempts == 0
    assert result.duration is None


def test_task_result_duration() -> None:
    """duration 从 started_at/finished_at 计算。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    result = TaskResult(spec=spec)
    result.started_at = datetime(2026, 1, 1, 12, 0, 0)
    result.finished_at = datetime(2026, 1, 1, 12, 0, 1, 500000)
    assert result.duration == 1.5


def test_task_result_duration_no_start() -> None:
    """无 started_at 时 duration 为 None。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    result = TaskResult(spec=spec)
    result.finished_at = datetime.now()
    assert result.duration is None


# ---------------------------------------------------------------------- #
# task 装饰器
# ---------------------------------------------------------------------- #
def test_task_decorator_bare() -> None:
    """@task 直接装饰返回 TaskSpec。"""

    @task
    def my_fn() -> int:
        return 1

    assert isinstance(my_fn, TaskSpec)
    assert my_fn.name == "my_fn"
    assert my_fn.fn is not None
    assert my_fn.fn() == 1


def test_task_decorator_with_args() -> None:
    """@task(...) 带参数装饰。"""

    @task(depends_on=("a",), tags=("t",))
    def my_fn() -> int:
        return 1

    assert isinstance(my_fn, TaskSpec)
    assert my_fn.name == "my_fn"
    assert my_fn.depends_on == ("a",)
    assert my_fn.tags == ("t",)


def test_task_decorator_with_name() -> None:
    """@task(name=...) 自定义名称。"""

    @task(name="custom")
    def my_fn() -> int:
        return 1

    assert isinstance(my_fn, TaskSpec)
    assert my_fn.name == "custom"


def test_task_decorator_cmd_only() -> None:
    """task(cmd=..., name=...) 无函数直接构造。"""
    spec = task(cmd=["echo", "hi"], name="say_hi")
    assert isinstance(spec, TaskSpec)
    assert spec.name == "say_hi"
    assert spec.cmd == ["echo", "hi"]
    assert spec.fn is not None  # 占位 fn


def test_task_decorator_cmd_only_no_name() -> None:
    """task(cmd=...) 无 name 抛 ValueError。"""
    with pytest.raises(ValueError, match="name"):
        task(cmd=["echo", "hi"])


def test_task_decorator_returns_callable_when_no_fn_no_cmd() -> None:
    """@task() 无 fn 无 cmd 返回装饰器（等待被装饰函数）。"""
    decorator = task(depends_on=("a",))
    assert callable(decorator)

    @decorator
    def my_fn() -> int:
        return 1

    assert isinstance(my_fn, TaskSpec)
    assert my_fn.depends_on == ("a",)


def test_task_decorator_with_retry() -> None:
    """@task(retry=...) 透传 RetryPolicy。"""
    policy = RetryPolicy(max_attempts=3)

    @task(retry=policy)
    def my_fn() -> int:
        return 1

    assert my_fn.retry == policy


def test_task_decorator_with_cwd_str() -> None:
    """@task(cwd=str) 字符串转 Path。"""

    @task(cwd="/tmp")
    def my_fn() -> int:
        return 1

    assert my_fn.cwd == Path("/tmp")


def test_task_decorator_with_env() -> None:
    """@task(env=...) 透传环境变量。"""

    @task(env={"KEY": "value"})
    def my_fn() -> int:
        return 1

    assert my_fn.env == {"KEY": "value"}


# ---------------------------------------------------------------------- #
# cmd 工厂
# ---------------------------------------------------------------------- #
def test_cmd_factory_default_name() -> None:
    """cmd(["uv","build"]) 默认 name="uv_build"。"""
    spec = cmd(["uv", "build"])
    assert spec.name == "uv_build"
    assert spec.cmd == ["uv", "build"]


def test_cmd_factory_custom_name() -> None:
    """cmd(..., name=...) 自定义名称。"""
    spec = cmd(["ruff", "check", "--fix"], name="lint")
    assert spec.name == "lint"


def test_cmd_factory_single_command() -> None:
    """单元素命令默认 name 为该元素。"""
    spec = cmd(["ls"])
    assert spec.name == "ls"


def test_cmd_factory_with_depends_on() -> None:
    """cmd 透传 depends_on。"""
    spec = cmd(["echo", "hi"], name="hi", depends_on=("a",))
    assert spec.depends_on == ("a",)
