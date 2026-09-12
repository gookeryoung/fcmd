"""fcmd.engine —— DAG 执行引擎层。

承载 ``run()`` 调用链中全部**执行侧**关注点（"动词"：调度与执行）：

- ``executors``：公共 :func:`run` 入口与策略派发（``sequential`` /
  ``thread`` / ``async`` / ``dependency`` 四种策略）
- ``task_runner``：任务级执行内核（执行上下文、线程池复用、跳过/
  重试/失败处理、同步/异步任务执行器）
- ``layer_runner``：层屏障模型调度（同层并发、整层完成后进入下一层）
- ``dependency_runner``：依赖驱动调度（无层屏障，任务就绪即启动）
- ``task_command``：TaskSpec ``cmd`` 字段执行器（list / shell 字符串 /
  可调用对象）

与 :mod:`fcmd.apis`（API 定义层，"名词"：数据结构与类型）相对。
本包通过 ``__getattr__`` 懒加载，避免父包加载时 eager import 整个
``executors`` 模块（它依赖 ``fcmd.apis.toolkit → asyncio`` 整条链）。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Strategy",
    "run",
    "run_command",
]

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "Strategy": ("fcmd.engine.executors", "Strategy"),
    "run": ("fcmd.engine.executors", "run"),
    "run_command": ("fcmd.engine.task_command", "run_command"),
}


def __getattr__(name: str) -> Any:
    """懒加载执行引擎公共 API。"""
    mapping = _LAZY_ATTRS.get(name)
    if mapping is None:
        raise AttributeError(f"module 'fcmd.engine' has no attribute {name!r}")
    module_path, attr_name = mapping
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value  # 缓存到全局，后续直接命中
    return value


def __dir__() -> list[str]:
    """补全建议。"""
    return sorted(set(globals()) | set(__all__))
