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
本包顶层模块仅供人读与兼容引用；内部代码应使用深路径
（如 ``fcmd.engine.executors``）以避免不必要的导入开销。
"""

from __future__ import annotations

from fcmd.engine.executors import Strategy, run
from fcmd.engine.task_command import run_command

__all__ = [
    "Strategy",
    "run",
    "run_command",
]
