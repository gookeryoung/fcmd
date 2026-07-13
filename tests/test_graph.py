"""Graph DAG 构建与校验测试。"""

from __future__ import annotations

import pytest

from fcmd.dag import Graph, GraphDefaults
from fcmd.errors import CycleError, DuplicateTaskError, MissingDependencyError
from fcmd.task import RetryPolicy, TaskSpec, cmd, task


# ---------------------------------------------------------------------- #
# from_specs 基础
# ---------------------------------------------------------------------- #
def test_graph_from_specs_empty() -> None:
    """空 spec 列表构造空图。"""
    graph = Graph.from_specs([])
    assert len(graph) == 0
    assert graph.names == []


def test_graph_from_specs_single() -> None:
    """单任务构造。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    graph = Graph.from_specs([spec])
    assert len(graph) == 1
    assert "x" in graph
    assert graph.names == ["x"]


def test_graph_from_specs_auto_infer_deps() -> None:
    """自动依赖推断：参数名匹配任务名。"""

    @task
    def extract() -> int:
        return 1

    @task
    def double(extract: int) -> int:
        return extract * 2

    graph = Graph.from_specs([extract, double])
    assert graph.dependencies("double") == ("extract",)


def test_graph_from_specs_explicit_deps() -> None:
    """显式 depends_on。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    graph = Graph.from_specs([a, b])
    assert graph.dependencies("b") == ("a",)


def test_graph_from_specs_cmd_task_no_auto_infer() -> None:
    """cmd 任务不自动推断依赖。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = cmd(["echo", "hi"], name="b")
    graph = Graph.from_specs([a, b])
    assert graph.dependencies("b") == ()


def test_graph_from_specs_auto_infer_skips_default_params() -> None:
    """有默认值参数不推断依赖。"""

    @task
    def a() -> int:
        return 1

    @task
    def b(a: int = 10) -> int:
        return a

    graph = Graph.from_specs([a, b])
    # b 的参数 a 有默认值，不推断为依赖
    assert graph.dependencies("b") == ()


def test_graph_from_specs_auto_infer_skips_context_params() -> None:
    """Context 标注参数不推断依赖。"""

    from fcmd.task import Context

    @task
    def a() -> int:
        return 1

    @task
    def b(ctx: Context) -> int:
        return 1

    graph = Graph.from_specs([a, b])
    # b 的参数 ctx 标注为 Context，不推断为依赖
    assert graph.dependencies("b") == ()


def test_graph_from_specs_string_ref_unsupported() -> None:
    """字符串引用暂未支持，抛 ValueError。"""
    with pytest.raises(ValueError, match="字符串引用"):
        Graph.from_specs(["some_ref"])


def test_graph_from_specs_invalid_type() -> None:
    """非 TaskSpec/str 类型抛 TypeError。"""
    with pytest.raises(TypeError, match="from_specs"):
        Graph.from_specs([123])  # type: ignore[list-item]


# ---------------------------------------------------------------------- #
# add 链式
# ---------------------------------------------------------------------- #
def test_graph_add_single() -> None:
    """add 单个 spec。"""
    graph = Graph()
    spec = TaskSpec(name="x", fn=lambda: 1)
    result = graph.add(spec)
    assert result is graph  # 返回 self
    assert "x" in graph


def test_graph_add_chain() -> None:
    """add 链式调用。"""
    graph = Graph()
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2)
    graph.add(a).add(b)
    assert len(graph) == 2


def test_graph_add_auto_infer() -> None:
    """add 时自动推断依赖（基于图中已有任务名）。"""
    graph = Graph()

    @task
    def extract() -> int:
        return 1

    @task
    def double(extract: int) -> int:
        return extract * 2

    graph.add(extract)
    graph.add(double)
    assert graph.dependencies("double") == ("extract",)


# ---------------------------------------------------------------------- #
# 校验
# ---------------------------------------------------------------------- #
def test_graph_duplicate_task_from_specs() -> None:
    """重名任务抛 DuplicateTaskError。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="a", fn=lambda: 2)
    with pytest.raises(DuplicateTaskError, match="a"):
        Graph.from_specs([a, b])


def test_graph_duplicate_task_add() -> None:
    """add 重名任务抛 DuplicateTaskError。"""
    graph = Graph()
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="a", fn=lambda: 2)
    graph.add(a)
    with pytest.raises(DuplicateTaskError, match="a"):
        graph.add(b)


def test_graph_missing_dependency_hard() -> None:
    """硬依赖缺失抛 MissingDependencyError。"""
    spec = TaskSpec(name="x", fn=lambda: 1, depends_on=("missing",))
    with pytest.raises(MissingDependencyError, match="missing"):
        Graph.from_specs([spec])


def test_graph_missing_dependency_soft() -> None:
    """软依赖缺失不抛异常（由 defaults 回退）。"""
    spec = TaskSpec(name="x", fn=lambda: 1, soft_depends_on=("missing",))
    graph = Graph.from_specs([spec])
    assert "x" in graph


def test_graph_cycle_detection() -> None:
    """循环依赖抛 CycleError。"""
    a = TaskSpec(name="a", fn=lambda: 1, depends_on=("c",))
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    c = TaskSpec(name="c", fn=lambda: 3, depends_on=("b",))
    with pytest.raises(CycleError, match="循环依赖"):
        Graph.from_specs([a, b, c])


def test_graph_validate_after_build() -> None:
    """构造后 validate 不抛异常（无环）。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    graph = Graph.from_specs([a, b])
    graph.validate()  # 不抛异常


# ---------------------------------------------------------------------- #
# layers
# ---------------------------------------------------------------------- #
def test_graph_layers_linear() -> None:
    """线性依赖分层。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    c = TaskSpec(name="c", fn=lambda: 3, depends_on=("b",))
    graph = Graph.from_specs([a, b, c])
    layers = graph.layers()
    assert layers == [["a"], ["b"], ["c"]]


def test_graph_layers_parallel() -> None:
    """无依赖任务同层。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2)
    c = TaskSpec(name="c", fn=lambda: 3)
    graph = Graph.from_specs([a, b, c])
    layers = graph.layers()
    assert len(layers) == 1
    assert sorted(layers[0]) == ["a", "b", "c"]


def test_graph_layers_diamond() -> None:
    """菱形依赖分层。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    c = TaskSpec(name="c", fn=lambda: 3, depends_on=("a",))
    d = TaskSpec(name="d", fn=lambda: 4, depends_on=("b", "c"))
    graph = Graph.from_specs([a, b, c, d])
    layers = graph.layers()
    assert layers == [["a"], ["b", "c"], ["d"]]


def test_graph_layers_cached() -> None:
    """layers 结果缓存。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    graph = Graph.from_specs([a])
    layers1 = graph.layers()
    layers2 = graph.layers()
    assert layers1 is layers2  # 同一对象引用


# ---------------------------------------------------------------------- #
# resolved_spec
# ---------------------------------------------------------------------- #
def test_graph_resolved_spec_no_defaults() -> None:
    """无图级默认值时返回原 spec。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    graph = Graph.from_specs([spec])
    resolved = graph.resolved_spec("x")
    assert resolved is spec


def test_graph_resolved_spec_with_retry_default() -> None:
    """图级 retry 默认值应用。"""
    spec = TaskSpec(name="x", fn=lambda: 1)  # retry=RetryPolicy() 默认
    policy = RetryPolicy(max_attempts=3)
    defaults = GraphDefaults(retry=policy)
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.retry == policy


def test_graph_resolved_spec_with_timeout_default() -> None:
    """图级 timeout 默认值应用。"""
    spec = TaskSpec(name="x", fn=lambda: 1)  # timeout=None
    defaults = GraphDefaults(timeout=30.0)
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.timeout == 30.0


def test_graph_resolved_spec_with_strategy_default() -> None:
    """图级 strategy 默认值应用。"""
    spec = TaskSpec(name="x", fn=lambda: 1)  # strategy=None
    defaults = GraphDefaults(strategy="thread")
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.strategy == "thread"


def test_graph_resolved_spec_with_verbose_default() -> None:
    """图级 verbose 默认值应用。"""
    spec = TaskSpec(name="x", fn=lambda: 1)  # verbose=False
    defaults = GraphDefaults(verbose=True)
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.verbose is True


def test_graph_resolved_spec_cached() -> None:
    """resolved_spec 结果缓存。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    graph = Graph.from_specs([spec])
    r1 = graph.resolved_spec("x")
    r2 = graph.resolved_spec("x")
    assert r1 is r2


def test_graph_resolved_spec_spec_override_default() -> None:
    """spec 显式设置时图级默认值不覆盖。"""
    policy = RetryPolicy(max_attempts=5)
    spec = TaskSpec(name="x", fn=lambda: 1, retry=policy)
    defaults = GraphDefaults(retry=RetryPolicy(max_attempts=10))
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.retry == policy  # spec 的值优先


# ---------------------------------------------------------------------- #
# 内省
# ---------------------------------------------------------------------- #
def test_graph_spec_lookup() -> None:
    """spec(name) 查找。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    graph = Graph.from_specs([spec])
    assert graph.spec("x") is spec


def test_graph_spec_lookup_missing() -> None:
    """spec(name) 不存在抛 KeyError。"""
    graph = Graph.from_specs([])
    with pytest.raises(KeyError):
        graph.spec("missing")


def test_graph_all_deps() -> None:
    """all_deps 返回硬+软依赖。"""
    spec = TaskSpec(
        name="x",
        fn=lambda: 1,
        depends_on=("a",),
        soft_depends_on=("b",),
    )
    graph = Graph.from_specs(
        [
            TaskSpec(name="a", fn=lambda: 1),
            TaskSpec(name="b", fn=lambda: 2),
            spec,
        ]
    )
    assert graph.all_deps("x") == ("a", "b")


def test_graph_all_specs() -> None:
    """all_specs 返回只读视图。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    graph = Graph.from_specs([a])
    all_specs = graph.all_specs()
    assert "a" in all_specs
    assert all_specs["a"] is a


def test_graph_repr() -> None:
    """__repr__ 格式。"""
    graph = Graph.from_specs([TaskSpec(name="x", fn=lambda: 1)])
    assert repr(graph) == "Graph(tasks=1)"


def test_graph_len() -> None:
    """__len__ 返回任务数。"""
    graph = Graph.from_specs(
        [
            TaskSpec(name="a", fn=lambda: 1),
            TaskSpec(name="b", fn=lambda: 2),
        ]
    )
    assert len(graph) == 2


def test_graph_contains() -> None:
    """__contains__ 检查任务名。"""
    graph = Graph.from_specs([TaskSpec(name="x", fn=lambda: 1)])
    assert "x" in graph
    assert "missing" not in graph


# ---------------------------------------------------------------------- #
# subgraph_with_deps
# ---------------------------------------------------------------------- #
def test_subgraph_with_deps_simple() -> None:
    """subgraph_with_deps 收集传递依赖。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    c = TaskSpec(name="c", fn=lambda: 3, depends_on=("b",))
    graph = Graph.from_specs([a, b, c])
    sub = graph.subgraph_with_deps(["c"])
    assert set(sub.names) == {"a", "b", "c"}


def test_subgraph_with_deps_partial() -> None:
    """subgraph_with_deps 部分选择。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    c = TaskSpec(name="c", fn=lambda: 3)  # 独立任务
    graph = Graph.from_specs([a, b, c])
    sub = graph.subgraph_with_deps(["b"])
    assert set(sub.names) == {"a", "b"}
    assert "c" not in sub


def test_subgraph_with_deps_missing_name() -> None:
    """subgraph_with_deps 不存在名称抛 KeyError。"""
    graph = Graph.from_specs([TaskSpec(name="x", fn=lambda: 1)])
    with pytest.raises(KeyError, match="missing"):
        graph.subgraph_with_deps(["missing"])


def test_subgraph_with_deps_soft_deps() -> None:
    """subgraph_with_deps 沿软依赖向上遍历。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, soft_depends_on=("a",))
    graph = Graph.from_specs([a, b])
    sub = graph.subgraph_with_deps(["b"])
    assert set(sub.names) == {"a", "b"}


# ---------------------------------------------------------------------- #
# to_mermaid
# ---------------------------------------------------------------------- #
def test_to_mermaid_simple() -> None:
    """to_mermaid 基础输出。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    graph = Graph.from_specs([a, b])
    mermaid = graph.to_mermaid()
    assert "graph TD" in mermaid
    assert 'a["a"]' in mermaid
    assert 'b["b"]' in mermaid
    assert "a --> b" in mermaid


def test_to_mermaid_soft_dep() -> None:
    """to_mermaid 软依赖用虚线。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, soft_depends_on=("a",))
    graph = Graph.from_specs([a, b])
    mermaid = graph.to_mermaid()
    assert "a -.-> b" in mermaid


def test_to_mermaid_orientation() -> None:
    """to_mermaid 自定义方向。"""
    graph = Graph.from_specs([TaskSpec(name="x", fn=lambda: 1)])
    mermaid = graph.to_mermaid(orientation="LR")
    assert "graph LR" in mermaid


def test_to_mermaid_invalid_orientation() -> None:
    """to_mermaid 无效方向抛 ValueError。"""
    graph = Graph.from_specs([TaskSpec(name="x", fn=lambda: 1)])
    with pytest.raises(ValueError, match="orientation"):
        graph.to_mermaid(orientation="XX")


# ---------------------------------------------------------------------- #
# describe
# ---------------------------------------------------------------------- #
def test_graph_describe() -> None:
    """describe 输出含层信息。"""
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda: 2, depends_on=("a",))
    graph = Graph.from_specs([a, b])
    desc = graph.describe()
    assert "Graph(tasks=2)" in desc
    assert "Layer 1" in desc
    assert "Layer 2" in desc


# ---------------------------------------------------------------------- #
# resolved_spec 其余默认值分支
# ---------------------------------------------------------------------- #
def test_graph_resolved_spec_with_env_default() -> None:
    """图级 env 默认值应用。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    defaults = GraphDefaults(env={"FOO": "bar"})
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.env == {"FOO": "bar"}


def test_graph_resolved_spec_with_cwd_default() -> None:
    """图级 cwd 默认值应用。"""
    from pathlib import Path

    spec = TaskSpec(name="x", fn=lambda: 1)
    defaults = GraphDefaults(cwd=Path("/tmp"))
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.cwd == Path("/tmp")


def test_graph_resolved_spec_with_continue_on_error_default() -> None:
    """图级 continue_on_error 默认值应用。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    defaults = GraphDefaults(continue_on_error=True)
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.continue_on_error is True


def test_graph_resolved_spec_with_tags_default() -> None:
    """图级 tags 默认值应用。"""
    spec = TaskSpec(name="x", fn=lambda: 1)
    defaults = GraphDefaults(tags=("deploy",))
    graph = Graph.from_specs([spec], defaults=defaults)
    resolved = graph.resolved_spec("x")
    assert resolved.tags == ("deploy",)


# ---------------------------------------------------------------------- #
# add() 与 layers() 缓存路径
# ---------------------------------------------------------------------- #
def test_graph_add_chained() -> None:
    """add 链式构造并自动推断依赖。"""
    graph = Graph()
    a = TaskSpec(name="a", fn=lambda: 1)
    b = TaskSpec(name="b", fn=lambda a: a + 1)
    graph.add(a).add(b)
    assert set(graph.names) == {"a", "b"}
    assert graph.dependencies("b") == ("a",)


def test_graph_add_duplicate() -> None:
    """add 重名抛 DuplicateTaskError。"""
    graph = Graph()
    graph.add(TaskSpec(name="a", fn=lambda: 1))
    with pytest.raises(DuplicateTaskError, match="a"):
        graph.add(TaskSpec(name="a", fn=lambda: 2))


def test_graph_add_missing_dep() -> None:
    """add 引用不存在的硬依赖抛 MissingDependencyError。"""
    graph = Graph()
    with pytest.raises(MissingDependencyError, match="missing"):
        graph.add(TaskSpec(name="x", fn=lambda: 1, depends_on=("missing",)))


def test_graph_layers_cache_miss() -> None:
    """直接构造的 Graph（未经 from_specs）layers() 走缓存未命中路径。"""
    graph = Graph()
    graph.add(TaskSpec(name="a", fn=lambda: 1))
    graph.validate()
    layers = graph.layers()
    assert layers == [["a"]]


def test_graph_auto_infer_uninspectable_fn() -> None:
    """fn 无签名（如内置）时自动推断安全跳过。"""
    # int 无 inspect.signature，触发 except 分支
    spec = TaskSpec(name="x", fn=int)  # type: ignore[arg-type]
    graph = Graph.from_specs([spec])
    assert graph.dependencies("x") == ()
