API 参考
========

本节按模块组织 fcmd 的公共 API。顶层包 ``fcmd`` 通过 ``__getattr__`` 懒加载聚合
各子模块符号，确保 ``import fcmd`` 冷启动 < 100ms；首次访问 ``fx.task`` /
``fx.run`` 等才触发对应模块导入。

顶层 API
--------

.. automodule:: fcmd
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: __version__

任务与图
--------

任务定义、依赖推断与 DAG 构建。

.. automodule:: fcmd.apis.dag
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: fcmd.apis.task
   :members:
   :undoc-members:
   :show-inheritance:

执行器
------

四种执行策略（``sequential`` / ``thread`` / ``async`` / ``dependency``）
与对应的 Runner 实现（位于 ``fcmd.engine`` 执行引擎包）。

.. automodule:: fcmd.engine.executors
   :members:
   :undoc-members:
   :show-inheritance:

工具箱
------

``@tool`` 装饰器、``run_tool`` / ``build_tool_graph`` 等 CLI 工具注册与执行 API。

.. automodule:: fcmd.apis.toolkit
   :members:
   :undoc-members:
   :show-inheritance:

报告与上下文
------------

.. automodule:: fcmd.apis.report
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: fcmd.apis.context
   :members:
   :undoc-members:
   :show-inheritance:

性能分析
--------

``fcmd profiler`` 内建命令背后的剖面数据结构。

.. automodule:: fcmd.apis.profiling
   :members:
   :undoc-members:
   :show-inheritance:

异常
----

fcmd 异常层次，均继承自 :class:`fcmd.FcmdError`。

.. automodule:: fcmd.apis.errors
   :members:
   :undoc-members:
   :show-inheritance:

命令执行
--------

.. automodule:: fcmd.engine.task_command
   :members:
   :undoc-members:
   :show-inheritance:

YAML 加载
---------

GitHub Actions 风格的 ``jobs`` / ``needs`` / ``matrix`` / ``if`` 任务图加载
（位于 ``fcmd.orchestration`` 编排包）。

.. automodule:: fcmd.orchestration.yaml_loader
   :members:
   :undoc-members:
   :show-inheritance:

模型
----

.. automodule:: fcmd.models.command
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: fcmd.models.filefilter
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: fcmd.models.version
   :members:
   :undoc-members:
   :show-inheritance:
