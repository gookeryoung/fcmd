"""__init__.py 懒加载与 graph() 快捷函数测试。"""

from __future__ import annotations

import importlib

import pytest

import fcmd
from fcmd.task import task


def test_lazy_import_task_spec() -> None:
    """fcmd.TaskSpec 首次访问触发导入并返回正确符号。"""
    # 重新导入 fcmd 以测试首次访问
    importlib.reload(fcmd)
    spec_cls = fcmd.TaskSpec
    # 验证返回的是 task.py 中的 TaskSpec 类
    assert spec_cls.__name__ == "TaskSpec"
    assert spec_cls.__module__ == "fcmd.task"


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
