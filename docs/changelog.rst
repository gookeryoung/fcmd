更新日志
=========

遵循 `Keep a Changelog <https://keepachangelog.com/>`_ 风格，版本倒序排列。

[Unreleased]
------------

- ``refactor``: 调度引擎采用标准库 ``graphlib.TopologicalSorter`` 替换两处自实现的 Kahn 拓扑排序（``dag._topological_layers`` 与 ``_dependency_runner`` 的增量就绪集簿记），消除"造轮子"
- ``refactor``: 6 个无状态单方法 Runner 类（``SyncTaskRunner``/``AsyncTaskRunner``/三个 ``LayerRunner``/``DependencyRunner``）收敛为模块级函数；``_filter_and_sort`` 重命名为 ``_build_spec_map`` 并去除冗余 ``to_run``
- ``chore``: 放弃 Python 3.8 支持（已 EOL），最低版本提升至 3.9；同步更新 CI 矩阵、tox envlist、ruff/pyrefly 目标版本与文档

v0.2.3 (2026-08-12)
-------------------

- ``feat``: 新增 3 个 CLI 工具：``inifile``（INI 读写，configparser）、``tomltool``（TOML 解析，3.11+ tomllib / 3.9-3.10 回退 tomli）、``calcdate``（日期计算 add/workdays/diff/compare）
- ``refactor``: 拆分 ``executors.py``，抽取 Runner 抽象与 LayerRunner 系列，简化 ``_dispatch_strategy``
- ``refactor``: 拆分 ``toolkit.py``，参数解析移至 ``apis/_tool_args.py``，执行逻辑移至 ``apis/_tool_exec.py``
- ``refactor``: 拆分 ``main.py`` 内建命令纯函数辅助到 ``cli/_env_helpers.py`` 与 ``cli/_doctor_helpers.py``
- ``refactor``: 审计异常处理模式，统一 CLI 错误打印格式
- ``perf``: 建立性能基线（cold start / tool discovery / DAG / tool exec），工具发现耗时从 126ms 降至 63ms
- ``chore``: 死代码扫描清理，复测性能基线对比

v0.2.2 (2026-08-12)
-------------------

- ``feat(envdev)``: 新增 Bun 环境配置功能，完善 Rust 子命令实现
- ``feat(portcheck)``: 端口占用时显示进程详情（PID/名称/状态/用户）
- ``fix``: envdev 镜像源环境变量实质持久化到用户层面（Windows 注册表 + Linux/macOS profile）
- ``refactor``: 清理死代码与重复工具，删除 basetool 合并到 codetool
- ``refactor``: 提升代码质量，覆盖率 98.04% → 98.16%，补全异常处理注释
- ``chore``: 清理项目内废弃的 .trae 文档与配置文件，envdev 默认镜像源改为 aliyun

v0.2.1 (2026-08-08)
-------------------

- ``refactor(envdev)``: 重构 envdev 子命令，发布版本 0.2.1
- ``perf``: 修复打包后管道输出无内容的问题
- ``fix``: 为所有 CLI 工具模块添加 ``if __name__ == "__main__"`` 块，修复 fspack 打包后单独运行无输出

v0.2.0 (2026-07-31)
-------------------

- ``refactor``: 重构项目结构，将核心 API 抽取到 ``apis`` 子模块（dag/task/toolkit/report/context/profiling/errors）
- ``refactor``: 重构项目 API 结构，将核心模块迁移至 apis 目录

v0.1.10 (2026-07-31)
--------------------

- ``chore``: 移除 pymake 任务中多余的 c 依赖并更新相关测试

v0.1.9 (2026-07-31)
-------------------

- ``chore``: 移除 pymake 任务中多余的 c 依赖并更新相关测试

v0.1.8 (2026-07-30)
-------------------

- ``refactor``: 拆分 ``run_tool`` / ``run_command`` 高复杂度函数并修复 RET 风格
- ``refactor``: 提取 main.py 补全生成器与 profiler 辅助函数到独立模块（``_completion_scripts`` / ``_profiler_helpers``）
- ``fix(console)``: 兼容 Win7/8 终端宽度，修复表格超出窗口问题
- ``fix(console)``: Win7/8 下强制 ascii_only 修复 box 字符乱码
- ``fix(console)``: 改用 AsciiBoxStream 包装 stdout 兜底 Win7 乱码
- ``fix(console)``: ``_display_width`` 补全 Fullwidth 字符宽度计算

v0.1.7 (2026-07-30)
-------------------

- ``perf``: pdftool 惰性导入 fitz/pypdf，工具发现耗时从 163ms 降至 126ms
- ``fix``: 缩窄 basetool.py 的异常捕获范围，符合 python-standards 硬约束
- ``refactor``: 删除 randtool 两处不可达 raise 并补测 timetool 无效 from_tz 分支

v0.1.6 (2026-07-29)
-------------------

- ``build``: 适配 Python 3.12+ 版本的依赖包
- ``fix(taskkill)``: 修复跨平台路径拼接导致的进程爆炸问题

v0.1.5 (2026-07-29)
-------------------

- ``fix(pdf)``: 降级 Win7 环境的 PyMuPDF 版本至 1.22 系列（mupdfcpp64.dll 链接了 Win7 UCRT 不支持的函数）

v0.1.4 (2026-07-29)
-------------------

- ``fix(cli)``: 修复 taskkill 递归调用自身的进程爆炸问题
- ``build``: 修复命令行别名拼写错误并适配 Win7 Python 环境

v0.1.3 (2026-07-29)
-------------------

- ``feat``: 新增 7 个 CLI 工具及其测试用例
- ``refactor``: 统一变量命名并简化代码格式

v0.1.2 (2026-07-18 ~ 2026-07-29)
--------------------------------

- ``feat``: 迁移 dockercmd 和 lscalc 两个 CLI 工具到 fcmd
- ``feat``: 为所有 CLI 工具模块添加独立入口脚本，支持打包后直接命令调用
- ``build``: 添加 fspack 打包入口声明，解决 src layout 无法识别入口的问题
- ``refactor``: 重组 CLI 测试文件结构，按工具拆分为单工具测试文件

v0.1.1 (2026-07-16 ~ 2026-07-18)
--------------------------------

- ``feat``: 移植 imagetool/pdftool 工具并修复 verbose 回调 Unicode 编码
- ``feat``: 框架支持 Optional 解包与 bool=True 取反，还原 imagetool 反转语义
- ``feat``: 新增 ``fcmd env`` 与 ``fcmd doctor`` 内置命令
- ``feat``: 移植 profiler 性能分析工具，支持离线 HTML/text 报告
- ``fix``: 修复 ``_unwrap_optional`` 跨 Python 版本兼容性问题
- ``fix``: 修复测试状态泄漏并补全 command.py 覆盖率至 100%

v0.1.0 (2026-07-13 ~ 2026-07-16)
--------------------------------

- 项目初始化，完成基础框架搭建
- ``feat``: 实现 P0 DAG 调度核心，支持编程式 API ``fx.graph`` / ``fx.run``
- ``feat``: 实现 P1 ``@fx.tool`` 装饰器框架与 FcmdApp CLI 路由
- ``feat``: 扩展 pymake 子命令集为完整构建/测试/发布工具集
- ``feat``: 实现工具自动发现机制替代硬编码注册表
- ``feat``: 新增 ``fcmd graph`` 内建命令与 ``build_tool_graph`` 公共 API
- ``feat``: 新增 ``fcmd info`` 内建命令展示工具与子命令元信息
