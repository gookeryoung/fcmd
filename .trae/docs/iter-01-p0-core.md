# iter-01：P0 DAG 调度核心落地

## 迭代目标

从零构建 fcmd 的 DAG 任务调度核心（P0 阶段），使编程式 API `fx.graph(...); fx.run(...)` 可用，并通过 ruff/pyrefly/pytest/coverage 全套门禁。

## 改动文件清单

### 新增源文件
- `src/fcmd/task.py`：TaskSpec 不可变数据结构 + RetryPolicy + TaskResult + task/cmd 装饰器
- `src/fcmd/dag.py`：Graph DAG 构建/校验/分层（Kahn 算法）+ GraphDefaults 图级默认值
- `src/fcmd/context.py`：参数注入（build_call_args / describe_injection / is_context_annotation）
- `src/fcmd/command.py`：cmd 任务执行器（list/str/callable 三态）
- `src/fcmd/console.py`：rich console 懒加载
- `src/fcmd/errors.py`：异常体系（FcmdError 基类 + 6 个子类）
- `src/fcmd/executors.py`：run() 公共 API + 4 种执行策略（sequential/thread/async/dependency）
- `src/fcmd/report.py`：RunReport 运行报告
- `src/fcmd/__init__.py`：懒加载聚合层（__getattr__ + graph() 快捷函数）
- `src/fcmd/__main__.py`：python -m fcmd 入口
- `src/fcmd/cli/main.py`：CLI 最小入口（--version/--help）
- `src/fcmd/cli/__init__.py`、`src/fcmd/apis/__init__.py`、`src/fcmd/tools/__init__.py`：P1/P2 占位

### 新增测试文件
- `tests/test_task.py`、`tests/test_graph.py`、`tests/test_context.py`、`tests/test_command.py`
- `tests/test_executors.py`、`tests/test_report.py`、`tests/test_init.py`、`tests/test_cli.py`、`tests/test_fcmd.py`

### 删除
- `src/fcmd/_lazy.py`：死代码（__init__.py 直接用 importlib，未导入 _lazy）
- `src/fcmd/graph.py`：重命名为 `dag.py`（解决模块名/函数名冲突）

## 关键决策与依据

### 1. graph.py → dag.py 重命名
**问题**：`fcmd.graph` 既是模块（`fcmd/graph.py`）又是函数（`__init__.py` 中的 `def graph()`）。当 `fcmd.executors` 顶层 `from .graph import Graph` 触发模块导入时，Python 自动设置 `fcmd.__dict__["graph"] = <module>`，覆盖 `def graph()` 函数，导致 `fx.graph(...)` 调用报 `'module' object is not callable`。

**解决**：重命名模块为 `fcmd.dag`，消除命名冲突。`fx.graph()` 函数保留，`fx.Graph` 通过 `__getattr__` 从 `fcmd.dag` 懒加载。

### 2. 软依赖语义：允许缺失
**问题**：`_validate_references` 原本校验软依赖必须存在于图中，但软依赖的设计意图是"可选输入"——缺失时由 `defaults` 提供默认值。

**解决**：`_validate_references` 仅校验硬依赖；`_build_dependency_index` 只对图中存在的软依赖计数就绪度；`_build_context` 对缺失软依赖用 `defaults` 回退，无默认值时注入 `None`。

### 3. Python 3.8 兼容性
- `from typing import Callable, Coroutine, Mapping, ...` 替代 `collections.abc`（3.8 不可下标化）
- `Union[X, Y]` / `List[str]` 替代 `X | Y` / `list[str]`（3.8 运行时不支持）
- `cast()` 实参在运行时求值，`from __future__ import annotations` 不延迟，需用 `typing` 导入
- `zip(strict=True)` 是 3.10+，移除 `strict` 参数

### 4. continue_on_error 与 report.success
**问题**：`continue_on_error=True` 时不抛异常，但 `report.success` 仍为 `True`，与"有任务失败"矛盾。

**解决**：`_finalize_failure` 无论是否 continue_on_error，都设置 `report.success = False`。

### 5. tags 过滤语义
`subgraph_with_deps` 向上遍历传递依赖（上游），不向下遍历下游。`tags=["x"]` 选中带标签的任务后，补齐其上游依赖，但不包含下游。

## 验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | All checks passed |
| ruff format --check | 24 files already formatted |
| pyrefly check | 0 errors (3 suppressed) |
| pytest -m "not slow" | 197 passed |
| coverage | 95.53%（目标 95%） |

### 功能验证
- API：`fx.graph(extract, double)` + `fx.run(g)` 输出 `[2, 4, 6]`
- CLI：`fcmd --version` / `python -m fcmd --version` 输出 `fcmd 0.1.0`
- 冷启动：4.6ms（目标 < 100ms）

## 遗留事项

- P1：@fx.tool 装饰器框架（函数签名→argparse CLI 自动生成）
- P1：CLI 路由表 + importlib 懒加载工具模块（`fcmd pymake b` 参数关系处理）
- P2：内置工具集（pymake 等）
- `executors.py` 覆盖率 93%：DependencyRunner 异常路径（死锁/取消）与部分 verbose 分支未覆盖
- `command.py` 覆盖率 94%：verbose+cwd 分支与 OSError 分支在 Windows 下难触发
