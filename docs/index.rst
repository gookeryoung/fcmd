fcmd
====

极速 Python 工具集应用：DAG 任务调度 + 组合 CLI。

.. toctree::
   :maxdepth: 2
   :caption: 目录

   api
   changelog

特性
====

- **极速冷启动** (小于 100ms)：三层懒加载，核心模块零外部依赖
- **DAG 任务调度**：四种执行策略（sequential/thread/async/dependency）
- **组合 API 简洁**：``@fx.task`` 装饰器 + 自动依赖推断 + ``@fx.tool`` CLI 工具
- **纯 CLI（自实现 Console）**：ASCII 表格、彩色输出、Win7/8 兼容
- **YAML 编排**：GitHub Actions 风格的 ``jobs``/``needs``/``matrix``/``if`` 任务图
- **零配置工具发现**：``fcmd/cli/`` 下的模块自动注册为 CLI 子命令

安装
====

.. code-block:: bash

   pip install fcmd          # 或 uv add fcmd

可选依赖按需安装：``fcmd[img]`` (Pillow)、``fcmd[pdf]`` (PyMuPDF + pypdf)、
``fcmd[ocr]`` (pytesseract)、``fcmd[office]`` (img + pdf + ocr)。
要求 Python ≥ 3.8。

快速上手
========

Python API
----------

.. code-block:: python

   import fcmd as fx

   @fx.task
   def extract() -> list[int]: return [1, 2, 3]

   @fx.task
   def double(extract: list[int]) -> list[int]: return [x * 2 for x in extract]

   graph = fx.graph(extract, double)  # double 自动依赖 extract
   report = fx.run(graph)
   print(report["double"])  # [2, 4, 6]

CLI
---

.. code-block:: bash

   fcmd                   # 列出所有工具
   fcmd pymake b          # 构建
   fcmd pymake tc         # 类型检查
   fcmd info pymake       # 查看 pymake 子命令
   fcmd graph pymake tc   # 输出 DAG（Mermaid）
   fcmd env               # 查看运行环境
   fcmd doctor            # 诊断环境问题
   fcmd profiler workflow.py   # 性能剖面分析

更多用法（任务定义 API、YAML 编排、执行策略）详见 ``README.md``。

项目结构
========

采用 ``src`` layout，核心分为三层：

.. code-block:: text

   src/fcmd/
   ├── __init__.py          # 顶层导出：task/tool/graph/run 等
   ├── _compat.py           # 跨版本兼容补丁
   ├── command.py           # 命令执行辅助
   ├── conditions.py        # YAML ``if`` 条件函数（success()/failure() 等）
   ├── console.py           # 自实现 Console + Table（替代 rich）
   ├── executors.py         # 四种执行策略的 Runner
   ├── yaml_loader.py       # YAML 任务图加载
   ├── apis/                # 核心 API：DAG/任务/工具箱/报告/上下文/性能
   │   ├── dag.py           # 图构建与拓扑排序
   │   ├── task.py          # TaskSpec/TaskResult 数据结构
   │   ├── toolkit.py       # @tool 装饰器与 run_tool/build_tool_graph
   │   ├── report.py        # RunReport 执行报告
   │   ├── context.py       # 执行上下文
   │   ├── profiling.py     # 性能剖面
   │   └── errors.py        # 异常层次
   ├── models/              # 命令/文件过滤/版本等模型
   └── cli/                 # CLI 入口与 54 个工具模块
       ├── main.py          # FcmdApp 主入口与 7 个内建命令
       ├── _common.py       # 共享常量（_BUILTIN_COMMANDS 等）
       ├── _completion_scripts.py  # shell 补全生成
       ├── _profiler_helpers.py    # profiler 内建命令辅助
       ├── _env_persist.py         # 环境变量持久化
       └── <tool>.py        # 工具模块（@fcmd.tool 自动注册）

开发
====

.. code-block:: bash

   # 安装开发依赖（lint + test + office + prek + tox）
   uv sync --extra dev

   fcmd pymake tc           # 类型检查（pyrefly + ruff）
   fcmd pymake t            # 运行测试
   fcmd pymake cov          # 测试 + 覆盖率
   make check               # 全套门禁（lint + typecheck + cov ≥ 95%）

工具链独立配置文件：``ruff.toml`` / ``pyrefly.toml`` / ``pytest.ini`` / ``.coveragerc``
/ ``.bumpversion.toml`` / ``uv.toml`` / ``.pre-commit-config.yaml``。
``pyproject.toml`` 仅含项目元数据。

新增工具：在 ``src/fcmd/cli/`` 下新建模块（非 ``_`` 前缀、非 ``main``），
用 ``@fcmd.tool`` 装饰器注册即可，无需修改 ``main.py``。
