# fcmd P1 实施计划：@fx.tool 装饰器框架 + CLI 路由

## 摘要

P1 阶段在 P0 DAG 调度核心基础上落地"组合 CLI"层：
- **`@fx.tool` 装饰器**：函数签名→argparse 自动生成 CLI，`needs`/`strategy`/`cmd` 表达 DAG 依赖与执行方式，运行时映射为 `TaskSpec` 并复用 `fcmd.run` 调度。
- **`FcmdApp` 路由**：统一入口 `fcmd <tool> [command] [options]`，importlib 懒加载工具模块，rich 表格列出工具。
- **示例工具 `pymake`**：验证装饰器+路由全链路可用。

不在范围：YAML 配置加载（P2）、内置工具集（P2）、`pf` 风格的 graph/info/completion 等内建子命令（P2 按需）。

## 当前状态分析（基于 P0 交付）

| 项 | 状态 |
|---|---|
| `TaskSpec` | 已含全部字段（cmd/fn/cwd/env/depends_on/strategy/retry/timeout/allow_upstream_skip/tags/continue_on_error），`cmd` 支持 list/str/callable |
| `Graph.from_specs` | 接受 `Iterable[TaskSpec | str]`，自动推断纯 fn 任务的 depends_on |
| `run(graph, strategy, *, dry_run, verbose, only, tags)` | 完整 DAG 执行入口 |
| `errors.py` | `FcmdError` + 7 个子类（Cycle/Duplicate/MissingDependency/Injection/TaskFailed/TaskTimeout） |
| `console.py` | rich 懒加载（`get_console`/`print_verbose`） |
| `cli/main.py` | P0 最小版（仅 `--version`/`--help`），待扩展为 FcmdApp |
| `apis/__init__.py` | 空占位 |
| `pyproject.toml` | 已预声明 `pymake = "fcmd.cli.pymake:main"`；coverage omit 含 `src/fcmd/cli/*`、`src/fcmd/tools/*` |
| Python 3.8 兼容 | `from __future__ import annotations` + `typing.Union/List`，`cast()` 实参从 `typing` 导入 |

**关键约束**：
- `apis/toolkit.py` 在 coverage 统计内 → 必须 ≥95% 覆盖率
- `cli/pymake.py`、`cli/main.py` 在 coverage omit 内 → 测试不强制覆盖率，但仍需保证可用
- 冷启动 <100ms：`toolkit.py` 通过 `__init__.py` 的 `__getattr__` 懒加载，不直接 import rich（用 `console.get_console()`）

## 提议改动

### 1. 新建 `src/fcmd/apis/toolkit.py`（核心）

**职责**：`@fx.tool` 装饰器、`ToolSpec`、`run_tool`、注册表。

**关键设计**（参考 `pyflowx/apis/toolkit.py`，按 fcmd 风格改写）：

```python
from __future__ import annotations

__all__ = [
    "ToolExitCode", "ToolSpec", "clear_tool_registry",
    "get_tool", "list_subcommands", "list_tools",
    "run_tool", "tool",
]

import argparse
import ast
import enum
import inspect
import sys
import textwrap
import typing
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Literal, Union, cast

from fcmd.console import get_console
from fcmd.dag import Graph, GraphDefaults
from fcmd.errors import FcmdError
from fcmd.executors import run
from fcmd.task import RetryPolicy, TaskSpec
```

**ToolSpec**（frozen dataclass，字段对齐 pyflowx，类型用 3.8 兼容写法）：
- `name: str`
- `subcommand: str | None`
- `func: Callable[..., Any]`
- `help: str = ""`
- `description: str = ""`
- `cmd: tuple[str, ...] | str | None = None`
- `needs: tuple[str, ...] = ()`
- `strategy: Literal["sequential","thread","async","dependency"] | None = None`
- `cwd: str | Path | None = None`
- `allow_upstream_skip: bool = False`
- `hidden: bool = False`
- `env: Mapping[str, str] | None = None`
- `retry: RetryPolicy | None = None`
- `timeout: float | None = None`

**注册表**：`_TOOL_REGISTRY: dict[str, dict[str | None, ToolSpec]] = {}`

**`tool()` 装饰器**：接受 `name`/`subcommand`/`help`/`description`/`cmd`/`needs`/`strategy`/`cwd`/`allow_upstream_skip`/`hidden`/`env`/`retry`/`timeout`，构造 `ToolSpec` 注册后返回原函数。

**`_register_tool` / `get_tool` / `list_tools` / `list_subcommands` / `clear_tool_registry`**：注册表 CRUD。

**`_collect_with_deps(name, target)`**：BFS 收集 target 及传递依赖，反转得拓扑序。

**`_has_function_logic(func)`**：ast 分析判断函数体是否仅 pass/docstring。

**`_is_aggregate(spec)`**：`spec.cmd is None and spec.needs and not _has_function_logic(spec.func)`。

**`_build_task_spec(spec, variables)`**：三种任务转换：
- cmd 任务：`TaskSpec(name=..., cmd=list(spec.cmd), depends_on=spec.needs, cwd=..., env=..., retry=..., timeout=..., allow_upstream_skip=..., strategy=...)`
- 聚合任务：`TaskSpec(name=..., fn=_noop, depends_on=spec.needs, allow_upstream_skip=..., strategy=...)`
- fn 任务：按签名从 variables 取 kwargs，`TaskSpec(name=..., fn=spec.func, kwargs=..., depends_on=spec.needs, cwd=..., ...)`

**`_resolve_hints` / `_is_list_annotation` / `_list_inner_type`**：类型注解处理（3.8 兼容：`str(annotation).startswith("list[")` 或 `"List["`）。

**`_add_optional_arg` / `_add_positional_arg` / `_build_parser_for_tool`**：argparse 构建（bool→store_true；list→nargs="+"；int/float/str/Path→type）。

**`_add_global_options`**：`--dry-run`/`-q --quiet`/`--strategy`。

**`run_tool(name, argv)`**：主入口：
1. 收集依赖 → 构建 `(subcommand, TaskSpec)` 列表
2. 为 target 子命令构建 parser（聚合任务用最小 parser）
3. 解析 argv → variables
4. 对每个收集到的 subcommand 调用 `_build_task_spec`
5. `Graph.from_specs(task_specs)` → `run(graph, strategy=..., dry_run=..., verbose=...)`
6. 失败时打印诊断，返回 `ToolExitCode.FAILURE.value`
7. 成功返回 `ToolExitCode.SUCCESS.value`

**`ToolExitCode`**：`IntEnum`，`SUCCESS=0`/`FAILURE=1`/`INTERRUPTED=130`。

### 2. 更新 `src/fcmd/apis/__init__.py`

```python
"""fcmd.apis —— @fx.tool 装饰器框架。"""
from __future__ import annotations

from fcmd.apis.toolkit import (
    ToolExitCode,
    ToolSpec,
    clear_tool_registry,
    get_tool,
    list_subcommands,
    list_tools,
    run_tool,
    tool,
)

__all__ = [
    "ToolExitCode", "ToolSpec", "clear_tool_registry",
    "get_tool", "list_subcommands", "list_tools",
    "run_tool", "tool",
]
```

### 3. 更新 `src/fcmd/__init__.py`

在 `_LAZY_ATTRS` 中追加（保持字母序）：
```python
"ToolExitCode": ("fcmd.apis.toolkit", "ToolExitCode"),
"ToolSpec": ("fcmd.apis.toolkit", "ToolSpec"),
"clear_tool_registry": ("fcmd.apis.toolkit", "clear_tool_registry"),
"get_tool": ("fcmd.apis.toolkit", "get_tool"),
"list_subcommands": ("fcmd.apis.toolkit", "list_subcommands"),
"list_tools": ("fcmd.apis.toolkit", "list_tools"),
"run_tool": ("fcmd.apis.toolkit", "run_tool"),
"tool": ("fcmd.apis.toolkit", "tool"),
```

`__all__` 追加同名符号。

### 4. 新建 `tests/test_toolkit.py`

**目标覆盖率 ≥95%**。覆盖：
- `tool()` 装饰器：单命令/多 subcommand/重复注册抛错
- `get_tool`/`list_tools`/`list_subcommands`（含 hidden）
- `clear_tool_registry`
- `_collect_with_deps`：BFS + 拓扑序
- `_has_function_logic`：pass/docstring/有逻辑
- `_is_aggregate`：cmd/聚合/fn 三态
- `_build_task_spec`：cmd/聚合/fn 三种转换（cwd 变量覆盖、env、retry 默认）
- argparse 构建：positional/optional/bool/list/Path/int
- `run_tool`：cmd 任务成功/失败、聚合任务、fn 任务、dry_run、unknown subcommand、unknown tool、`--help` SystemExit
- 全局选项 `--dry-run`/`--quiet`/`--strategy`
- `ToolExitCode` 值
- fixture：`autouse` 清空注册表，避免测试间污染

Windows 兼容：cmd 任务用 `["cmd", "/c", "echo", "hello"]`（同 P0）。

### 5. 新建 `src/fcmd/cli/pymake.py`

参考 `pyflowx/cli/pymake.py`，精简为 5-8 个子命令验证全链路：
- `b`/`bc`/`sync`/`c`/`t`/`lint`（cmd 任务）
- `pyrefly_check`/`git_add_all`（hidden 内部 job）
- `ba`/`tc`（聚合任务，needs + strategy="thread"）
- `main()`：`sys.exit(run_tool("pymake", sys.argv[1:]))`

命令用跨平台可用的（`python -c "..."` / `git --version` 等），避免依赖 uv/maturin 等可能未安装的工具。

### 6. 更新 `src/fcmd/cli/main.py`：FcmdApp

**职责**：路由 `fcmd <tool> [command] [options]`。

```python
class FcmdApp:
    _TOOL_ALIASES: dict[str, str] = {"pymake": "pymake", "pm": "pymake"}
    _TOOL_MODULES: dict[str, str] = {"pymake": "fcmd.cli.pymake"}

    def __init__(self, argv=None) -> None: ...
    def run(self) -> int: ...
    def _list_tools(self) -> None: ...      # rich Table
    def _resolve_tool(self, name) -> str | None: ...
    def _run_tool(self, name, argv) -> int: ...  # importlib 懒加载模块触发 @tool 注册，再调 run_tool
    def _print_unknown_tool(self, name) -> None: ...  # difflib 模糊匹配
```

**保留** P0 的 `--version`/`--help`/无参行为（`_build_parser` 不删，向后兼容 `test_cli.py`）。

`main()` 改为 `sys.exit(FcmdApp().run())`，但保留 `_build_parser` 函数供旧测试。

### 7. 更新 `tests/test_cli.py`

- 保留现有 3 个测试（`_build_parser`/无参/`--version`）
- 新增：`fcmd pymake`（列子命令）、`fcmd pymake <sub>`（执行）、`fcmd unknown`（模糊匹配提示）、`fcmd --version` 经 FcmdApp

## 假设与决策

1. **不引入 `pyflowx.runner.CliExitCode`**：用本地 `ToolExitCode`，避免依赖未实现的 runner 模块。
2. **`run_tool` 返回 int**（退出码），不抛 `SystemExit`，由调用方（`pymake.main`/`FcmdApp`）决定是否 `sys.exit`。
3. **聚合任务 `fn=_noop`**：复用 `fcmd.task._task_noop`？不——`_task_noop` 是私有的，toolkit 定义自己的 `_noop` 保持模块边界清晰。
4. **`_has_function_logic` 用 ast 分析**：避免 exec 函数体；处理 `pass`/`...`/docstring 三种空体。
5. **list[X] 检测兼容 3.8**：`from __future__ import annotations` 让注解变字符串，`typing.get_type_hints` 在 3.8 仍能解析 `list[X]` 为 `list[X]`（`__origin__ is list`）；但 `List[X]` 也需支持 → 用 `str(annotation)` 双重判断。
6. **`cwd` 参数处理**：函数签名有 `cwd` 参数时，CLI 解析的值覆盖装饰器 `cwd`；否则用装饰器 `cwd`。与 pyflowx 一致。
7. **不实现 yamlrun/graph/info/completion 内建命令**：P2 按需。
8. **`FcmdApp._TOOL_ALIASES`/`_TOOL_MODULES` 硬编码**：P1 仅 pymake 一个工具，硬编码足够；P2 引入注册机制。
9. **`pymake` 命令跨平台**：用 `python --version`/`git --version` 等保证测试环境可用，不依赖 uv/maturin。
10. **诊断输出**：失败时打印 `report.diagnose()` 到 stderr（若 `report.diagnose()` 返回 None 则跳过）。

## 验证步骤

1. **单元测试**：`uv run pytest tests/test_toolkit.py tests/test_cli.py -v`，toolkit.py 覆盖率 ≥95%
2. **全量门禁**：
   ```bash
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run pyrefly check
   uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95
   ```
3. **API 验证**：
   ```python
   import fcmd as fx

   @fx.tool("demo", subcommand="hello", cmd=["python", "-c", "print('hi')"])
   def hello() -> None: pass

   fx.run_tool("demo", ["hello"])  # 输出 hi，返回 0
   ```
4. **CLI 验证**：
   - `fcmd` → 列出工具
   - `fcmd pymake` → 列出子命令
   - `fcmd pymake <sub>` → 执行
   - `fcmd --version` → 输出 `fcmd 0.1.0`
   - `python -m fcmd --version` → 同上
5. **冷启动**：`python -X importtime -c "import fcmd"` <100ms
6. **覆盖率不下降**：当前 95.53%，P1 后应保持或提升（toolkit.py 新增需 ≥95%）

## 实施顺序

1. `apis/toolkit.py`（核心，最长）
2. `apis/__init__.py`（聚合导出）
3. `__init__.py`（懒加载映射）
4. `tests/test_toolkit.py`（覆盖率达 95%）
5. 跑 `ruff` + `pytest tests/test_toolkit.py` 验证核心
6. `cli/pymake.py`（示例工具）
7. `cli/main.py`（FcmdApp）
8. `tests/test_cli.py`（FcmdApp 测试）
9. 全量门禁 + API 验证 + CLI 验证
10. 迭代文档 `iter-02-p1-tool-cli.md`
11. git commit（中文，feat 类型）

## 遗留事项（P2）

- 内置工具集（gittool/hashfile/...）放 `fcmd/tools/`
- YAML 配置加载（`yamlrun` 命令）
- 内建命令 `graph`/`info`/`completion`
- `pf` 风格的工具自动发现机制（替代硬编码 `_TOOL_MODULES`）
