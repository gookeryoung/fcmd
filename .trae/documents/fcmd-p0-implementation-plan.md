# fcmd P0 核心框架实施计划

## Summary

本计划承接已批准的 `fcmd-architecture-plan.md`，落地 P0 阶段：从零构建 DAG 调度核心（task/graph/executors/command/context/report/console）+ 懒加载聚合层（_lazy/__init__），使编程式 API `fx.graph(...); fx.run(...)` 可用，并通过 ruff/pyrefly/pytest 全套门禁。P1（CLI 框架）与 P2（内置工具）在后续计划推进。

## Current State Analysis

### 已存在文件（实际状态，与会话总结略有出入）

| 文件 | 状态 | 说明 |
|------|------|------|
| `pyproject.toml` | 完整 | ruff/pyrefly/pytest/coverage/entry points 配置就绪 |
| `src/fcmd/errors.py` | 完整 | FcmdError + 6 子类（Cycle/Duplicate/Missing/Injection/TaskFailed/TaskTimeout） |
| `src/fcmd/__init__.py` | 占位 | 仅 `__version__`，待完善懒加载 |
| `src/fcmd/cli.py` | **冗余** | 根级简化版 argparse（仅 --version），与 entry point `fcmd.cli.main:main` 不符，需删除 |
| `src/fcmd/apis/__init__.py` | 占位 | 空文件 |
| `src/fcmd/cli/__init__.py` | 占位 | 空文件 |
| `src/fcmd/tools/__init__.py` | 占位 | 空文件 |
| `tests/test_fcmd.py` | 冒烟 | 仅 `test_version_is_string` / `test_package_importable` |
| `tests/__init__.py` | 占位 | 空文件 |
| `src/fcmd/py.typed` | 空 | 类型标记 |

### 关键问题

1. **entry point 失配**：`pyproject.toml` 声明 `fcmd = "fcmd.cli.main:main"`，但无 `cli/main.py`，当前 `fcmd --version` 不可用。P0 需创建最小 `cli/main.py`（仅 --version + --list 占位），P1 扩展为完整 FcmdApp。
2. **根级 `cli.py` 冗余**：与 `cli/main.py` 职责重叠，需删除。
3. **核心模块全部缺失**：task/graph/executors/command/context/report/console/_lazy 均未创建。
4. **测试覆盖不足**：当前仅冒烟测试，coverage 远低于 95% 门槛。

### pyflowx 参考要点（已读完 task/graph/executors/command/context/report/__init__）

- `TaskSpec` 是 frozen dataclass + Generic[T]，`effective_fn` property 包装 cmd 为可执行函数
- `Graph` 用 Kahn 算法分层，`_auto_infer_deps_single` 从函数参数名自动推断依赖
- `executors.py` 四策略 + 模块级辅助函数消除同步/异步重复（_prepare_for_execution / _handle_failure / _build_context / _store_result 等）
- `context.py` 的 `build_call_args` 支持快速路径（cmd 无参 / fn 无依赖）+ 慢路径（参数名匹配依赖）
- `report.py` 的 RunReport 提供 `__getitem__` 返回值、`result_of` 返回完整 TaskResult
- pyflowx 体量大（含 cancellation/notification/progress/storage/diagnostics/profiling/yaml_loader 等），fcmd 精简版需砍掉这些扩展

## Proposed Changes

### 决策：相对 pyflowx 的精简

| 砍掉 | 理由 |
|------|------|
| `LoopSpec` / `dynamic` / `cache_key` / `outputs` / `hooks` / `executor` / `priority` / `concurrency_key` / `skip_if_missing` / `storage_key` | P0 核心不需要，P2 按需扩展 |
| `cancellation` / `notification` / `progress` / `storage` / `diagnostics` / `profiling` / `yaml_loader` / `run_iter` | 非核心，超出 P0 范围 |
| RunReport 的 `to_csv` / `to_html` / `to_dict` / `to_json` / `from_json` / `output_of` / `slowest_tasks` / `duration_stats` / `diagnose` / `profile` | P0 保留核心查询即可 |
| `TaskHooks` / `TaskEvent` / `task_template` / `LoopSpec` | 精简 API 表面 |
| `add_subgraph` / `map` / `group` / `pipeline` / `chain` / `subgraph` / `subgraph_by_names` / `from_yaml` | P0 仅保留 `from_specs` / `add` / `subgraph_with_deps` / `to_mermaid` |

### 保留并精简的核心

| 模块 | 保留功能 |
|------|---------|
| `task.py` | TaskSpec（17 字段）+ RetryPolicy + TaskStatus + TaskResult + task/cmd 装饰器 + Context 类型别名 |
| `context.py` | build_call_args（含快速路径）+ describe_injection + is_context_annotation |
| `graph.py` | Graph + GraphDefaults + Kahn 分层 + _auto_infer_deps_single + from_specs + add + resolved_spec + subgraph_with_deps + validate + layers + to_mermaid + describe |
| `command.py` | run_command（list/str/callable 统一） |
| `console.py` | get_console() 懒加载 rich |
| `report.py` | RunReport（results/success/run_id + __getitem__/result_of/summary/failed_tasks/succeeded_tasks/skipped_tasks/describe） |
| `executors.py` | run() + 4 策略（SequentialLayerRunner/ThreadedLayerRunner/AsyncLayerRunner/DependencyRunner）+ SyncTaskRunner/AsyncTaskRunner + 模块级辅助 |
| `_lazy.py` | lazy_import 工具 |
| `__init__.py` | __getattr__ 懒加载聚合 fx.task/fx.cmd/fx.graph/fx.run/fx.sh 等 |

---

### 步骤 1：删除冗余文件

**文件**：`src/fcmd/cli.py`

**操作**：删除（由 `cli/main.py` 取代）

**理由**：根级 `cli.py` 与 entry point `fcmd.cli.main:main` 不符，且 P0 会创建最小 `cli/main.py`。

---

### 步骤 2：创建 `src/fcmd/console.py`

**职责**：rich 懒加载入口，核心模块不直接 import rich，保证冷启动 < 100ms。

**内容设计**：
```python
"""rich 显示层懒加载。

核心模块（task/graph/executors）不直接 import rich，通过本模块统一访问，
确保冷启动时 rich 仅在首次输出时才加载。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

__all__ = ["get_console"]

_console: "Console | None" = None


def get_console() -> "Console":
    """获取全局 rich Console 实例（懒加载）。"""
    global _console
    if _console is None:
        from rich.console import Console
        _console = Console()
    return _console


def _print_verbose(*args: Any, **kwargs: Any) -> None:
    """verbose 模式输出辅助（通过 rich console）。"""
    get_console().print(*args, **kwargs)
```

---

### 步骤 3：创建 `src/fcmd/task.py`

**职责**：TaskSpec 不可变数据结构 + task/cmd 装饰器 + RetryPolicy + TaskStatus + TaskResult。

**字段清单（17 个，相对 pyflowx 砍掉 13 个）**：
```python
@dataclass(frozen=True)
class TaskSpec(Generic[T]):
    name: str
    fn: TaskFn[T] | None = None
    cmd: TaskCmd | None = None
    depends_on: tuple[str, ...] = ()
    soft_depends_on: tuple[str, ...] = ()
    defaults: Mapping[str, Any] = field(default_factory=dict)
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout: float | None = None
    tags: tuple[str, ...] = ()
    conditions: tuple[Condition, ...] = ()
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    verbose: bool = False
    allow_upstream_skip: bool = False
    strategy: str | None = None
    continue_on_error: bool = False
```

**关键方法**：
- `__post_init__`：校验 name 非空、retry.max_attempts>=1、timeout>0、不自依赖、硬软依赖不重叠、fn/cmd 至少一个
- `effective_fn` property：cmd 包装为可执行函数（调用 command.run_command），fn 直接返回
- `should_execute(context)`：检查 conditions，返回 (should_run, skip_reason)
- `env_context()`：临时应用 env/cwd 的上下文管理器（threading.RLock 序列化进程全局状态）

**RetryPolicy**：max_attempts/delay/backoff/jitter/retry_on，含 should_retry / wait_seconds

**TaskStatus 枚举**：PENDING/RUNNING/SUCCESS/FAILED/SKIPPED

**TaskResult**：可变 dataclass，含 spec/status/value/error/attempts/started_at/finished_at/reason + duration property

**task 装饰器**：支持 `@task` / `@task(...)` / `task(cmd=..., name=...)` 三种形式

**cmd 工厂**：`cmd(command, *, name=None, **kwargs)`，name 默认 `"_".join(command[:2])`

**类型别名**：
- `TaskFn = Callable[..., T] | Callable[..., Coroutine[Any, Any, T]]`
- `Context = Mapping[str, Any]`
- `TaskCmd = list[str] | str | Callable[..., Any]`
- `Condition = Callable[[Context], bool]`

---

### 步骤 4：创建 `src/fcmd/context.py`

**职责**：把上游结果转换为函数参数。

**核心函数**：
- `is_context_annotation(annotation)`：判断标注是否为 Context
- `build_call_args(spec, context) -> (args, kwargs)`：含快速路径（cmd 无参 / fn 无依赖）+ 慢路径（参数名匹配依赖）
- `describe_injection(spec) -> str`：dry_run 用的人类可读描述

**实现要点**：
- `lru_cache` 缓存 `inspect.signature` 与 `_fn_no_dep_injection` 预计算
- 快速路径 1：cmd 无 args/kwargs → 返回 ((), {})
- 快速路径 2：fn 无依赖无静态参数 → 跳过 dep_context 构建
- 慢路径：Context 标注参数接收完整映射，名称匹配依赖接收值，**kwargs 接收剩余

---

### 步骤 5：创建 `src/fcmd/graph.py`

**职责**：DAG 构建、校验、分层。

**核心组件**：
- `_topological_layers(deps) -> (layers, cycle_nodes)`：Kahn 算法，返回分层 + 环节点
- `GraphDefaults`：图级默认值（retry/timeout/strategy/env/cwd/tags/continue_on_error/verbose）
- `Graph` dataclass：
  - 字段：specs/deps/defaults/namespace + 缓存字段（_resolved_cache/_layers_cache）
  - 构建：`from_specs(specs, defaults, namespace)` / `add(spec)` / `_register` / `_register_single`
  - 自动依赖推断：`_auto_infer_deps_single(spec, all_names)` 从必需参数名匹配任务名
  - 校验：`_validate_references` / `validate`（环检测）
  - 内省：`names` / `spec(name)` / `resolved_spec(name)` / `dependencies(name)` / `all_deps(name)` / `all_specs()` / `layers()`
  - 子图：`subgraph_with_deps(names)`（BFS 收集传递依赖）
  - 可视化：`to_mermaid(orientation)` / `describe()`
  - 协议：`__repr__` / `__len__` / `__contains__`

**砍掉**：`chain`/`pipeline`/`group`/`map`/`add_subgraph`/`subgraph`/`subgraph_by_names`/`from_yaml`/`_rewrite_deps`/`_loop_groups`/`_groups`/`_pending_refs`

---

### 步骤 6：创建 `src/fcmd/command.py`

**职责**：执行 TaskSpec.cmd（list/str/callable）。

**核心函数**：`run_command(spec) -> Any`
- callable：直接调用，异常包装 RuntimeError
- list/str：subprocess.run，shell=not is_list，cwd/env 透传，非零返回码抛 RuntimeError
- verbose=True 时通过 console.get_console() 打印执行信息与返回码
- FileNotFoundError / TimeoutExpired / OSError 包装为 RuntimeError

**关键**：不直接 `from rich.console import Console`，通过 `console.get_console()` 懒加载。

---

### 步骤 7：创建 `src/fcmd/report.py`

**职责**：运行报告（极简版）。

**RunReport dataclass**：
- 字段：`results: dict[str, TaskResult]` / `success: bool = True` / `run_id: str = uuid4().hex[:8]`
- 访问：`__getitem__(name) -> Any`（返回 value）/ `result_of(name) -> TaskResult` / `__contains__` / `__iter__` / `__len__`
- 汇总：`summary() -> dict`（run_id/success/total_tasks/by_status/total_duration_seconds）/ `failed_tasks()` / `succeeded_tasks()` / `skipped_tasks()` / `tasks_by_status(status)`
- 调试：`describe() -> str`

**砍掉**：所有序列化（to_dict/to_json/to_csv/to_html/from_json）/ output_of / slowest_tasks / duration_stats / diagnose / profile

---

### 步骤 8：创建 `src/fcmd/executors.py`

**职责**：4 策略执行 + 公共 run 入口。

**模块级辅助函数**（消除同步/异步重复）：
- `_is_async_fn(spec)` / `_emit(on_event, result)` / `_emit_running(on_event, spec)`
- `_build_context(spec, global_context, report) -> dict`（硬依赖 + 软依赖 + 默认值）
- `_upstream_skip_reason(spec, report)` / `_prepare_for_execution(spec, context, report, on_event) -> TaskResult | None`
- `_should_retry(spec, attempts, exc)` / `_mark_success(spec, result, value)` / `_handle_failure(spec, result, exc, layer_idx, ctx) -> bool` / `_finalize_failure(result, layer_idx, ctx, continue_on_error)`
- `_sort_by_priority(layer, specs)` / `_filter_and_sort(layer, graph, ctx)` / `_store_result(result, spec, task_ctx, ctx)`
- `_build_dependency_index(remaining, all_specs, completed) -> (in_degree, dependents, ready)`（DependencyRunner 增量就绪集）

**任务执行器**：
- `SyncTaskRunner.run(spec, task_ctx, layer_idx, ctx) -> TaskResult`
- `AsyncTaskRunner.run(spec, task_ctx, layer_idx, ctx, semaphore=None) -> TaskResult`
- `_execute_async_task(spec, args, kwargs, loop)` / `_submit_sync_task(spec, args, kwargs, loop)`

**层执行器**：
- `SequentialLayerRunner.execute(layer, graph, ctx, layer_idx)`
- `ThreadedLayerRunner.execute(layer, graph, ctx, layer_idx, pool)`
- `AsyncLayerRunner.execute(layer, graph, ctx, layer_idx)`

**DependencyRunner**：依赖驱动调度，增量就绪集（in_degree + dependents 反向邻接表），asyncio 并发

**_ExecContext dataclass**：捆绑 context/report/on_event/cancel_event，减少参数传递

**公共 API**：
- `run(graph, strategy="dependency", *, max_workers=None, dry_run=False, verbose=False, on_event=None, only=None, tags=None) -> RunReport`
- `_dispatch_strategy(strategy, graph, ctx, max_workers)`
- `_print_dry_run(graph, layers)` / `_drive_sequential` / `_drive_threaded` / `_async_drive` / `_make_verbose_callback`

**砍掉**：cancellation（CancelToken/_is_cancelled/_mark_remaining_skipped）/ notification / progress / storage（StateBackend/resolve_backend/_apply_cached）/ process_pool / dynamic / resume_from / run_iter / concurrency_limits / concurrency_key / notifiers / progress / cancel_event

**EventCallback 类型**：`Callable[[TaskEvent], None]`（但 TaskEvent 简化为仅含 task/status/attempts/error/duration/reason 的 dataclass，放在 task.py 中）

---

### 步骤 9：创建 `src/fcmd/_lazy.py`

**职责**：lazy_import 工具，支持 `__getattr__` 模式懒加载。

**内容**：
```python
"""懒加载工具：支持模块级 __getattr__ 延迟导入。"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

__all__ = ["lazy_import"]


def lazy_import(fullname: str) -> Any:
    """返回延迟导入的属性代理。

    首次访问属性时才触发真实导入。适用于 __init__.py 的 __getattr__。
    """
    module_name, _, attr_name = fullname.rpartition(".")
    if not module_name:
        raise ImportError(f"lazy_import 需要完整模块路径，收到: {fullname!r}")

    class _LazyModule:
        def __init__(self) -> None:
            self._resolved: Any = None
            self._loaded = False

        def _load(self) -> Any:
            if not self._loaded:
                module: ModuleType = importlib.import_module(module_name)
                self._resolved = getattr(module, attr_name) if attr_name else module
                self._loaded = True
            return self._resolved

        def __getattr__(self, name: str) -> Any:
            target = self._load()
            return getattr(target, name)

        def __repr__(self) -> str:
            return f"<lazy {fullname}>"

    return _LazyModule()
```

**注意**：实际实现可能更简单——直接在 `__init__.py` 的 `__getattr__` 中 `importlib.import_module` + `getattr`。`_lazy.py` 提供工具函数封装此模式。简化版可直接用函数：

```python
def lazy_import(module_name: str, attr_name: str | None = None) -> Any:
    """延迟导入并缓存到模块全局。"""
    # 实际实现见文件
```

---

### 步骤 10：完善 `src/fcmd/__init__.py`

**职责**：公共 API 懒加载聚合，冷启动 < 100ms。

**设计**：
```python
"""fcmd —— 极速 Python 工具集应用。

公共 API 通过 __getattr__ 懒加载聚合，确保冷启动 < 100ms。
首次访问 fx.task / fx.graph / fx.run 等才触发对应模块导入。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

__all__ = [
    "Context", "CycleError", "DuplicateTaskError", "FcmdError",
    "Graph", "GraphDefaults", "InjectionError", "MissingDependencyError",
    "RetryPolicy", "RunReport", "Strategy", "TaskCmd", "TaskFailedError",
    "TaskResult", "TaskSpec", "TaskStatus", "TaskTimeoutError",
    "cmd", "describe_injection", "graph", "run", "run_command", "task",
]

# 懒加载映射：属性名 -> (模块路径, 符号名)
_LAZY_ATTRS = {
    "Context": ("fcmd.context", "Context"),
    "CycleError": ("fcmd.errors", "CycleError"),
    "DuplicateTaskError": ("fcmd.errors", "DuplicateTaskError"),
    "FcmdError": ("fcmd.errors", "FcmdError"),
    "Graph": ("fcmd.graph", "Graph"),
    "GraphDefaults": ("fcmd.graph", "GraphDefaults"),
    "InjectionError": ("fcmd.errors", "InjectionError"),
    "MissingDependencyError": ("fcmd.errors", "MissingDependencyError"),
    "RetryPolicy": ("fcmd.task", "RetryPolicy"),
    "RunReport": ("fcmd.report", "RunReport"),
    "TaskCmd": ("fcmd.task", "TaskCmd"),
    "TaskFailedError": ("fcmd.errors", "TaskFailedError"),
    "TaskResult": ("fcmd.task", "TaskResult"),
    "TaskSpec": ("fcmd.task", "TaskSpec"),
    "TaskStatus": ("fcmd.task", "TaskStatus"),
    "TaskTimeoutError": ("fcmd.errors", "TaskTimeoutError"),
    "cmd": ("fcmd.task", "cmd"),
    "describe_injection": ("fcmd.context", "describe_injection"),
    "run": ("fcmd.executors", "run"),
    "run_command": ("fcmd.command", "run_command"),
    "task": ("fcmd.task", "task"),
}


def __getattr__(name: str) -> Any:
    """懒加载公共 API 符号。"""
    mapping = _LAZY_ATTRS.get(name)
    if mapping is None:
        raise AttributeError(f"module 'fcmd' has no attribute {name!r}")
    module_path, attr_name = mapping
    import importlib
    module = importlib.import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value  # 缓存到全局，后续直接命中
    return value


def __dir__() -> list[str]:
    """补全建议。"""
    return sorted(set(globals()) | set(__all__))
```

**graph() 快捷函数**：在 `__init__.py` 中定义（非懒加载，因为它仅是 Graph.from_specs 的薄包装）：
```python
def graph(*specs: Any, defaults: Any = None, namespace: str | None = None) -> Any:
    """快捷构造图：等价于 Graph.from_specs。"""
    from fcmd.graph import Graph, GraphDefaults
    return Graph.from_specs(specs, defaults=defaults or GraphDefaults(), namespace=namespace)
```

**砍掉**（相对 pyflowx __init__）：fileops/apis/tool/compose/conditions/cancellation/notification/progress/storage/runner/shell/pipelines/profiling/diagnostics/history/imaging/monitoring/yaml_loader 等导入。

---

### 步骤 11：创建 `src/fcmd/cli/main.py`（最小版）

**职责**：P0 阶段让 `fcmd --version` 可用，P1 扩展为完整 FcmdApp。

**内容**：
```python
"""fcmd CLI 主入口（P0 最小版）。

P1 阶段将扩展为完整 FcmdApp（路由表 + importlib 懒加载工具模块）。
当前仅支持 --version 与 --help。
"""
from __future__ import annotations

import argparse

from fcmd import __version__

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="fcmd",
        description="极速 Python 工具集应用。",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> None:
    """主入口。"""
    _build_parser().parse_args()


if __name__ == "__main__":
    main()
```

---

### 步骤 12：创建测试文件

**测试策略**：公共 API 优先通过公共接口测试；覆盖率 ≥ 95%（branch）。

#### `tests/test_task.py`
- `test_task_spec_minimal`：最小字段构造
- `test_task_spec_validation`：name 空 / retry<1 / timeout<=0 / 自依赖 / 硬软依赖重叠 / 无 fn 无 cmd 抛 ValueError
- `test_task_decorator_bare`：`@task` 直接装饰
- `test_task_decorator_with_args`：`@task(depends_on=...)` 带参装饰
- `test_task_decorator_cmd_only`：`task(cmd=..., name=...)` 无函数
- `test_cmd_factory_default_name`：`cmd(["uv","build"])` → name="uv_build"
- `test_cmd_factory_custom_name`：`cmd([...], name="lint")`
- `test_retry_policy_validation`：max_attempts<1 / delay<0 等抛 ValueError
- `test_retry_policy_should_retry`：retry_on 过滤
- `test_retry_policy_wait_seconds`：backoff 计算
- `test_task_spec_effective_fn_cmd`：cmd 包装后执行
- `test_task_spec_effective_fn_fn`：fn 直接返回
- `test_task_spec_should_execute_no_conditions`：无条件返回 (True, None)
- `test_task_spec_should_execute_conditions_pass`：条件全 True
- `test_task_spec_should_execute_conditions_fail`：条件 False 返回 (False, reason)
- `test_task_spec_env_context`：env/cwd 临时应用后恢复
- `test_task_result_duration`：started/finished 设置后 duration 计算

#### `tests/test_context.py`
- `test_is_context_annotation`：Context / 字符串 / 其他类型
- `test_build_call_args_fast_path_cmd`：cmd 无参快速路径
- `test_build_call_args_fast_path_fn_no_deps`：fn 无依赖快速路径
- `test_build_call_args_dep_injection`：参数名匹配依赖
- `test_build_call_args_context_annotation`：Context 标注接收完整映射
- `test_build_call_args_var_keyword`：**kwargs 接收剩余
- `test_build_call_args_static_kwargs`：spec.kwargs 提供
- `test_build_call_args_collision`：static kwargs 与依赖名冲突抛 InjectionError
- `test_build_call_args_unresolved`：无依赖无默认值抛 InjectionError
- `test_describe_injection`：人类可读描述

#### `tests/test_graph.py`
- `test_graph_from_specs_simple`：两任务自动依赖推断
- `test_graph_from_specs_explicit_deps`：显式 depends_on
- `test_graph_add_chain`：add 链式调用
- `test_graph_duplicate_task`：重名抛 DuplicateTaskError
- `test_graph_missing_dependency`：缺失依赖抛 MissingDependencyError
- `test_graph_cycle_detection`：环抛 CycleError
- `test_graph_layers`：Kahn 分层正确
- `test_graph_layers_diamond`：菱形依赖分层
- `test_graph_resolved_spec_defaults`：图级默认值应用
- `test_graph_subgraph_with_deps`：BFS 传递依赖收集
- `test_graph_to_mermaid`：Mermaid 输出格式
- `test_graph_describe`：调试描述
- `test_graph_auto_infer_deps`：参数名匹配任务名自动建立依赖
- `test_graph_auto_infer_deps_with_default`：有默认值参数不推断
- `test_graph_validate_references`：软依赖也校验

#### `tests/test_command.py`
- `test_run_command_list_success`：`["echo", "hi"]` 返回 None
- `test_run_command_list_failure`：非零返回码抛 RuntimeError
- `test_run_command_str_success`：shell 字符串
- `test_run_command_callable`：可调用对象
- `test_run_command_callable_exception`：callable 抛异常包装 RuntimeError
- `test_run_command_not_found`：FileNotFoundError 包装
- `test_run_command_timeout`：超时包装
- `test_run_command_verbose`：verbose 模式输出
- `test_run_command_cwd_env`：cwd/env 透传

#### `tests/test_report.py`
- `test_run_report_getitem`：`report["name"]` 返回 value
- `test_run_report_result_of`：返回 TaskResult
- `test_run_report_contains`：`in` 检查
- `test_run_report_iter_len`：迭代与长度
- `test_run_report_summary`：汇总统计
- `test_run_report_failed_tasks`：失败任务列表
- `test_run_report_succeeded_tasks`：成功任务列表
- `test_run_report_skipped_tasks`：跳过任务列表
- `test_run_report_tasks_by_status`：按状态过滤
- `test_run_report_describe`：调试描述

#### `tests/test_executors.py`
- `test_run_sequential_simple`：sequential 策略两任务
- `test_run_thread_strategy`：thread 策略并行
- `test_run_async_strategy`：async 策略
- `test_run_dependency_strategy`：dependency 策略（默认）
- `test_run_dry_run`：dry_run 打印计划不执行
- `test_run_verbose`：verbose 模式
- `test_run_auto_dep_injection`：参数名自动注入上游结果
- `test_run_cmd_task`：cmd 任务执行
- `test_run_failure_propagation`：任务失败抛 TaskFailedError
- `test_run_continue_on_error`：continue_on_error=True 不抛异常
- `test_run_retry_policy`：重试成功
- `test_run_retry_exhausted`：重试耗尽抛异常
- `test_run_soft_dependency`：软依赖注入默认值
- `test_run_allow_upstream_skip`：上游跳过后仍执行
- `test_run_conditions_skip`：条件不满足跳过
- `test_run_only_filter`：only 参数子图过滤
- `test_run_tags_filter`：tags 参数子图过滤
- `test_run_async_fn`：异步任务执行
- `test_run_timeout`：超时处理

#### `tests/test_init.py`（补充）
- `test_lazy_import`：__getattr__ 懒加载符号
- `test_lazy_import_missing`：不存在属性抛 AttributeError
- `test_lazy_import_caches`：二次访问命中缓存
- `test_dir_function`：__dir__ 返回完整列表
- `test_graph_shortcut`：fx.graph() 快捷函数

#### `tests/test_fcmd.py`（保留现有）
- `test_version_is_string`
- `test_package_importable`

---

### 步骤 13：更新 `tests/test_fcmd.py`

保留现有冒烟测试，无需修改。

---

### 步骤 14：验证与门禁

**验证命令序列**：
```bash
# 1. 类型检查
uv run ruff check src tests
uv run ruff format --check src tests

# 2. 类型检查
uv run pyrefly check

# 3. 单元测试 + 覆盖率
uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95

# 4. 编程式 API 验证
uv run python -c "import fcmd as fx; print(fx.__version__)"
uv run python -c "import fcmd as fx; g = fx.graph(fx.cmd(['echo', 'hello'], name='hi')); r = fx.run(g); print(r.success)"
uv run python -c "import fcmd as fx
@fx.task
def extract(): return [1, 2, 3]
@fx.task
def double(extract): return [x * 2 for x in extract]
g = fx.graph(extract, double)
r = fx.run(g)
print(r['double'])"

# 5. CLI 验证
uv run fcmd --version

# 6. 冷启动性能
uv run python -X importtime -c "import fcmd" 2>&1 | Select-Object -Last 10
```

**验收标准**：
- ruff check 无错误
- ruff format --check 无差异
- pyrefly check 无错误
- pytest 全部通过
- coverage ≥ 95%（branch）
- `fcmd --version` 输出版本号
- 编程式 API 示例输出正确
- 冷启动 < 100ms

---

### 步骤 15：记录迭代文档

**文件**：`.trae/docs/iter-01-p0-core-framework.md`

**内容**：迭代目标 / 改动文件清单 / 关键决策与依据 / 验证结果 / 遗留事项

---

### 步骤 16：Git 提交

```bash
git add src/fcmd/task.py src/fcmd/context.py src/fcmd/graph.py src/fcmd/command.py src/fcmd/console.py src/fcmd/report.py src/fcmd/executors.py src/fcmd/_lazy.py src/fcmd/__init__.py src/fcmd/cli/main.py
git add tests/test_task.py tests/test_context.py tests/test_graph.py tests/test_command.py tests/test_report.py tests/test_executors.py tests/test_init.py
git rm src/fcmd/cli.py
git add .trae/docs/iter-01-p0-core-framework.md
git commit -m "feat: 完成 P0 核心框架（task/graph/executors/command/context/report/console/懒加载）"
git push
```

## Assumptions & Decisions

### 假设
1. 用户已批准的 `fcmd-architecture-plan.md` 中 P0 范围不变
2. rule-11 的 95% 覆盖率门槛适用于 P0（核心模块必测）
3. 冷启动 < 100ms 目标在 P0 阶段通过懒加载架构保证，无需微基准测试
4. rich 是唯一运行时依赖，typing-extensions 仅 Python < 3.13

### 决策
1. **TaskSpec 17 字段**：相对 pyflowx 砍掉 loop/dynamic/cache_key/outputs/hooks/executor/priority/concurrency_key/skip_if_missing/storage_key/TaskHooks。保留 soft_depends_on/conditions/continue_on_error/allow_upstream_skip 满足核心调度需求。
2. **TaskEvent 保留**：作为 executors 的事件载体（简化版，仅含 task/status/attempts/error/duration/reason），放在 task.py 中。
3. **EventCallback 保留**：`run(on_event=...)` 支持用户回调，但砍掉内部通知器/进度条/监控器。
4. **cli/main.py 最小版**：P0 仅 --version，P1 扩展为 FcmdApp。
5. **删除根级 cli.py**：与 entry point 失配，由 cli/main.py 取代。
6. **_lazy.py 保留**：提供 lazy_import 工具，但 __init__.py 的 __getattr__ 直接用 importlib 更简单。两者并存，_lazy.py 供其他模块复用。
7. **graph() 快捷函数直接定义在 __init__.py**：非懒加载，因为它是薄包装，直接定义更清晰。
8. **executors 砍掉 cancel_event/concurrency_limits/resume_from**：P0 不支持取消、并发限制、断点续跑。这些功能在 P2 按需加回。
9. **测试覆盖率门槛 95%**：适用于核心模块（task/graph/executors/command/context/report），cli/main.py 在 coverage omit 列表（pyproject.toml 已配置 `omit = ["src/fcmd/cli/*", ...]`）。

### 不在范围内（P1/P2 处理）
- CLI 框架：shell.py / conditions.py / compose.py / runner.py / apis/toolkit.py / cli/main.py 完整版 / cli/_common.py / cli/pymake.py
- 内置工具：tools/which.py / tools/fileops.py / tools/sysinfo.py
- 扩展功能：取消 / 通知 / 进度条 / 状态后端 / 诊断 / 性能剖面 / YAML 加载 / 动态任务 / 循环展开

## Verification Steps

1. **静态检查**：`uv run ruff check src tests` + `uv run ruff format --check src tests` + `uv run pyrefly check` 全部通过
2. **测试**：`uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95` 全部通过，覆盖率 ≥ 95%
3. **API 验证**：编程式示例（cmd 任务 + fn 任务自动依赖推断）输出正确
4. **CLI 验证**：`uv run fcmd --version` 输出 `fcmd 0.1.0`
5. **冷启动**：`uv run python -X importtime -c "import fcmd"` 顶层导入 < 100ms
6. **文档**：`.trae/docs/iter-01-p0-core-framework.md` 记录完整
7. **Git**：commit + push 成功（分支已跟踪远程时）

## 实施顺序（严格按此执行）

1. 删除 `src/fcmd/cli.py`
2. 创建 `src/fcmd/console.py`
3. 创建 `src/fcmd/task.py`
4. 创建 `src/fcmd/context.py`
5. 创建 `src/fcmd/graph.py`
6. 创建 `src/fcmd/command.py`
7. 创建 `src/fcmd/report.py`
8. 创建 `src/fcmd/executors.py`
9. 创建 `src/fcmd/_lazy.py`
10. 完善 `src/fcmd/__init__.py`
11. 创建 `src/fcmd/cli/main.py`
12. 创建 `tests/test_task.py`
13. 创建 `tests/test_context.py`
14. 创建 `tests/test_graph.py`
15. 创建 `tests/test_command.py`
16. 创建 `tests/test_report.py`
17. 创建 `tests/test_executors.py`
18. 创建 `tests/test_init.py`
19. 运行 ruff check + format
20. 运行 pyrefly check
21. 运行 pytest --cov
22. 修复任何失败（lint/类型/测试/覆盖率）
23. 运行 API 验证脚本
24. 运行 `fcmd --version`
25. 创建 `.trae/docs/iter-01-p0-core-framework.md`
26. git add（按文件名）+ commit + push
