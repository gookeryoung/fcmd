"""__init__.py 懒加载与 graph() 快捷函数测试。"""

from __future__ import annotations

import importlib
import time
import types

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


# ---------------------------------------------------------------------- #
# 导入耗时诊断（FCMD_IMPORT_DEBUG）
# ---------------------------------------------------------------------- #
def _slow_loader(delay: float) -> object:
    """构造一个延迟返回的假模块加载器。"""
    module = types.ModuleType("fake")

    def load(_path: str) -> types.ModuleType:
        time.sleep(delay)
        return module

    return load


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("0", None),
        ("off", None),
        ("1", 100.0),
        ("ON", 100.0),
        ("250", 250.0),
        (" 50 ", 50.0),
        ("-5", 100.0),
        ("abc", 100.0),
    ],
)
def test_slow_import_threshold_parsing(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float | None
) -> None:
    """环境变量取值到阈值的解析：关闭、布尔开关、数字与非法值回退。"""
    from fcmd._lazyload import slow_import_threshold_ms

    if raw is None:
        monkeypatch.delenv("FCMD_IMPORT_DEBUG", raising=False)
    else:
        monkeypatch.setenv("FCMD_IMPORT_DEBUG", raw)
    assert slow_import_threshold_ms() == expected


def test_timed_import_reports_slow_import(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """开启诊断且超过阈值时向 stderr 输出模块、耗时与来源。"""
    import fcmd._lazyload as lazyload

    monkeypatch.setenv("FCMD_IMPORT_DEBUG", "5")
    monkeypatch.setattr(lazyload, "_load_module", _slow_loader(0.01))
    module = lazyload.timed_import("fake.slow", "unit")
    assert module.__name__ == "fake"
    err = capsys.readouterr().err
    assert "slow import" in err
    assert "fake.slow" in err
    assert "from unit" in err


def test_timed_import_below_threshold_is_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """耗时低于阈值时不输出。"""
    import fcmd._lazyload as lazyload

    monkeypatch.setenv("FCMD_IMPORT_DEBUG", "250")
    monkeypatch.setattr(lazyload, "_load_module", _slow_loader(0))
    assert lazyload.timed_import("fake.fast", "unit").__name__ == "fake"
    assert capsys.readouterr().err == ""


def test_timed_import_disabled_is_silent(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """未开启诊断时不做计时也不输出。"""
    import fcmd._lazyload as lazyload

    monkeypatch.delenv("FCMD_IMPORT_DEBUG", raising=False)
    monkeypatch.setattr(lazyload, "_load_module", _slow_loader(0))
    assert lazyload.timed_import("fake.off", "unit").__name__ == "fake"
    assert capsys.readouterr().err == ""


def test_timed_import_propagates_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """导入异常原样抛出，不被诊断逻辑吞掉。"""
    import fcmd._lazyload as lazyload

    def boom(_path: str) -> types.ModuleType:
        raise ModuleNotFoundError("missing")

    monkeypatch.setenv("FCMD_IMPORT_DEBUG", "5")
    monkeypatch.setattr(lazyload, "_load_module", boom)
    with pytest.raises(ModuleNotFoundError, match="missing"):
        lazyload.timed_import("fake.missing", "unit")


def test_lazy_getattr_reports_origin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """默认懒加载路径同样带上 ``包名.属性名`` 来源。"""
    import fcmd._lazyload as lazyload

    monkeypatch.setenv("FCMD_IMPORT_DEBUG", "5")
    monkeypatch.setattr(lazyload, "_load_module", _slow_loader(0.01))
    value = lazyload.lazy_getattr("fcmd.core", {"Demo": ("fake.module", None)}, "Demo", {})
    assert value.__name__ == "fake"
    assert "from fcmd.core.Demo" in capsys.readouterr().err
