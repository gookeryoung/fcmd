"""__init__.py 懒加载与 graph() 快捷函数测试。"""

from __future__ import annotations

import importlib

import pytest

import fcmd
from fcmd.core.task import task


def test_lazy_import_task_spec() -> None:
    """fcmd.TaskSpec 首次访问触发导入并返回正确符号。"""
    # 重新导入 fcmd 以测试首次访问
    importlib.reload(fcmd)
    spec_cls = fcmd.TaskSpec
    # 验证返回的是 task.py 中的 TaskSpec 类
    assert spec_cls.__name__ == "TaskSpec"
    assert spec_cls.__module__ == "fcmd.core.task"


def test_lazy_import_missing_attribute() -> None:
    """不存在的属性抛 AttributeError 含模块名。"""
    with pytest.raises(AttributeError, match="fcmd"):
        _ = fcmd.nonexistent_attribute_xyz


def test_lazy_import_caches_to_globals() -> None:
    """二次访问命中 globals 缓存（id 一致性）。"""
    importlib.reload(fcmd)
    first = fcmd.RunReport
    # 首次访问后应缓存到 __dict__
    assert "RunReport" in fcmd.__dict__
    second = fcmd.RunReport
    assert first is second


def test_dir_returns_complete_list() -> None:
    """dir(fcmd) 应包含 __all__ 全部符号。"""
    names = dir(fcmd)
    for symbol in fcmd.__all__:
        assert symbol in names, f"{symbol} 未出现在 dir(fcmd) 中"


def test_graph_shortcut_function() -> None:
    """fx.graph(spec1, spec2) 等价 Graph.from_specs([spec1, spec2])，且自动依赖推断生效。"""

    @task
    def extract() -> int:
        return 1

    @task
    def double(extract: int) -> int:
        return extract * 2

    graph = fcmd.graph(extract, double)
    assert len(graph) == 2
    assert graph.dependencies("double") == ("extract",)


# ---------------------------------------------------------------------- #
# _lazyload 模板（三个包共用）
# ---------------------------------------------------------------------- #
def test_lazy_getattr_loads_and_caches() -> None:
    """lazy_getattr 按映射加载符号并写入传入命名空间。"""
    from fcmd._lazyload import lazy_getattr

    namespace: dict[str, object] = {}
    value = lazy_getattr("fcmd", {"ToolSpec": ("fcmd.core.toolkit", "ToolSpec")}, "ToolSpec", namespace)
    assert value.__name__ == "ToolSpec"
    assert namespace["ToolSpec"] is value


def test_lazy_getattr_returns_submodule_when_attr_none() -> None:
    """符号名为 None 时返回子模块自身（from pkg import submodule 语义）。"""
    import types

    from fcmd._lazyload import lazy_getattr

    value = lazy_getattr("fcmd", {"console": ("fcmd.console", None)}, "console", {})
    assert isinstance(value, types.ModuleType)
    assert value.__name__ == "fcmd.console"


def test_lazy_getattr_missing_raises_attribute_error() -> None:
    """未登记属性抛 AttributeError，消息含传入的模块名。"""
    from fcmd._lazyload import lazy_getattr

    with pytest.raises(AttributeError, match=r"fcmd\.core"):
        lazy_getattr("fcmd.core", {}, "nope", {})


def test_lazy_dir_merges_namespace_and_all() -> None:
    """lazy_dir 合并命名空间与 __all__ 并排序。"""
    from fcmd._lazyload import lazy_dir

    assert lazy_dir({"b": 1}, ["c", "a"]) == ["a", "b", "c"]
