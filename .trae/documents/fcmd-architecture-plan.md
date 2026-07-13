# fcmd 整体架构方案

## Context

用户需要一个"极速 Python 工具集应用"，从底层调用模块构建，支持各种形式的组合调用。核心诉求：
- 组合 API 简洁明了（参考 pyflowx 的 `@px.tool` + `task/cmd/graph/run` 模式）
- 极速冷启动（< 100ms，懒导入 + 命令缓存）
- 纯 CLI（rich 增强输出，无 GUI）
- 便捷脚本运行模式，处理好 `fcmd pymake b` 这种 fcmd 与功能模块的参数关系

参考项目：
- `f:\Dev\fcmd\.trae\ref\pyflowx`（主参考）：TaskSpec/Graph/executors DAG 调度 + `@px.tool` 装饰器 + CliRunner
- `f:\Dev\fcmd\.trae\ref\bitool`（次参考）：懒加载机制、命令缓存

fcmd 当前为空项目，从零构建。

## 架构设计

### 模块层次

```
src/fcmd/
├── __init__.py              # 公共 API 懒加载聚合（fx.task/fx.cmd/fx.graph/fx.run/fx.tool/fx.sh）
├── _lazy.py                 # 懒加载工具（__getattr__ 模式，< 100ms 冷启动关键）
├── task.py                  # TaskSpec 不可变数据结构（Generic[T]）+ task/cmd 装饰器
├── graph.py                 # Graph DAG（Kahn 拓扑分层 + 自动依赖推断）
├── executors.py             # 执行策略（sequential/thread/async/dependency）
├── command.py               # 命令执行器（list/shell/callable 统一）
├── compose.py               # 图组合（字符串引用展开，GraphComposer）
├── runner.py                # CliRunner（aliases + tasks 模式）
├── shell.py                 # sh() 辅助（subprocess.run 轻量封装）
├── conditions.py            # 条件系统（IS_WINDOWS/BuiltinConditions）
├── context.py               # 上下文注入（build_call_args 参数名→依赖）
├── report.py                # RunReport 结果（极简版）
├── errors.py                # 异常体系（FcmdError 基类 + 子类）
├── console.py               # rich 显示层（get_console() 懒加载）
├── apis/
│   ├── __init__.py
│   └── toolkit.py           # @fx.tool 装饰器（函数签名→argparse，needs/strategy DAG）
├── cli/
│   ├── __init__.py
│   ├── main.py              # fcmd 主入口（FcmdApp，路由表 + importlib 懒加载）
│   ├── _common.py           # 共享辅助（platform_command 等）
│   └── pymake.py            # 构建工具示例（@fx.tool）
└── tools/                   # 内置工具集
    ├── __init__.py
    ├── which.py
    ├── fileops.py
    └── sysinfo.py
```

### 核心 API 设计

```python
import fcmd as fx

# 1. 装饰器模式（函数参数名自动推断依赖）
@fx.task
def extract() -> list[int]: return [1, 2, 3]

@fx.task
def double(extract: list[int]) -> list[int]: return [x * 2 for x in extract]

graph = fx.graph(extract, double)  # double 自动依赖 extract
report = fx.run(graph)
print(report["double"])  # [2, 4, 6]

# 2. 命令任务
graph = fx.graph(
    fx.cmd(["ls", "-la"]),
    fx.cmd("git status", name="check_git"),
)
fx.run(graph)

# 3. @fx.tool 装饰器（CLI 工具，函数签名驱动 argparse）
@fx.tool("pymake", subcommand="b", help="构建", cmd=["uv", "build"])
def b(cwd: Path = Path()) -> None: pass

@fx.tool("pymake", subcommand="tc", help="类型检查",
         needs=["c", "pyrefly_check", "lint"], strategy="thread")
def tc(cwd: Path = Path()) -> None: pass

# 4. sh() 辅助
fx.sh(["git", "add", "."])
result = fx.sh("echo hello", capture=True)
```

### `fcmd pymake b` 参数分离方案

```
fcmd [全局选项] <tool> [子命令] [工具选项]
  │         │        │        └─ toolkit._build_argparse_parser 解析
  │         │        └─ cli/main.py 路由，importlib 导入工具模块
  │         └─ 工具级全局选项（--dry-run/--quiet/--strategy，由 toolkit 管理）
  └─ fcmd 级：仅 --version/--help
```

- `fcmd` 主入口（cli/main.py 的 FcmdApp）路由到 `pymake` 工具
- `pymake` 通过 `@fx.tool` 注册子命令
- `b` 子命令执行 `["uv", "build"]`（cmd 任务）
- `_collect_with_deps` BFS 收集依赖 → `Graph.from_specs` 构建 DAG → `run()` 执行
- 同时支持独立脚本 `pymake b`（通过 `[project.scripts]` 注册入口点）

### 极速启动策略（< 100ms）

三层懒加载：
- **Layer 1 `__init__.py`**：`__getattr__` 懒加载所有公共符号（移植 bitool/lazy.py 模式）
- **Layer 2 `cli/main.py`**：仅加载路由表 dict，importlib 按需导入工具模块
- **Layer 3 `console.py`**：rich 首次 print 时才导入

预期冷启动：
- `fcmd --version`：仅触发 `__init__.py` + cli 路由（~30ms）
- `fcmd pymake b`：按需加载 toolkit+graph+executors（~60ms）

### 依赖清单

```toml
dependencies = ["rich>=13.0"]  # 唯一运行时依赖（用户明确要求）
[project.optional-dependencies]
dev = ["fcmd[lint,test]", "prek", "tox-uv", "tox"]
lint = ["pyrefly>=1.1.1", "ruff>=0.8.0"]
test = ["pytest>=8.0", "pytest-asyncio", "pytest-cov", "pytest-xdist"]
```

- `typing-extensions` 仅 Python < 3.13 时引入 TypeVar default
- rich 是必需依赖（用户明确要求），但通过 console.py 懒加载不影响冷启动
- 核心模块（task/graph/executors/command）零 rich 直接导入

## 分阶段实施

### P0 核心框架（零外部依赖路径）

**目标**：DAG 调度核心可用，纯 Python 标准库 + typing-extensions

**任务清单**：
1. `pyproject.toml`：项目配置（ruff/pyrefly/pytest/coverage，entry points）
2. `errors.py`：FcmdError 基类 + CycleError/DuplicateTaskError/MissingDependencyError/TaskFailedError/TaskTimeoutError
3. `task.py`：TaskSpec(frozen, Generic[T]) 精简版（15 字段）+ task/cmd 装饰器 + RetryPolicy + TaskStatus + TaskResult
4. `context.py`：build_call_args（参数名→依赖注入）+ is_context_annotation
5. `graph.py`：Graph + Kahn 分层 + `_auto_infer_deps` + `from_specs` + `resolved_spec` + `subgraph_with_deps`
6. `command.py`：run_command（list/str/callable，rich 通过 console.py 懒加载）
7. `console.py`：get_console() 懒加载 rich
8. `report.py`：RunReport 极简版（results/success/run_id + `__getitem__` + `result_of()`）
9. `executors.py`：run() + 4 策略（sequential/thread/async/dependency）+ SyncTaskRunner/AsyncTaskRunner
10. `_lazy.py`：lazy_import 工具
11. `__init__.py`：`__getattr__` 懒加载聚合公共 API

**TaskSpec 字段精简**（相对 pyflowx 砍掉 loop/dynamic/concurrency_key/outputs/cache_key/storage）：
name/fn/cmd/depends_on/soft_depends_on/defaults/args/kwargs/retry/timeout/tags/conditions/cwd/env/verbose/strategy/continue_on_error

**测试**：test_task/test_graph/test_executors/test_command/test_context

### P1 CLI 框架

**目标**：`fcmd pymake b` 可运行

**任务清单**：
1. `shell.py`：sh() 辅助（参考 pyflowx/shell.py）
2. `conditions.py`：Condition 类型 + IS_WINDOWS/IS_LINUX/IS_MACOS + BuiltinConditions（HAS_INSTALLED/FILE_EXISTS）
3. `compose.py`：GraphComposer + compose()（字符串引用展开）
4. `runner.py`：CliRunner（aliases + tasks 模式，参考 pyflowx/runner.py）
5. `apis/toolkit.py`：@fx.tool 装饰器 + ToolSpec + run_tool + _build_argparse_parser（函数签名→argparse）
6. `apis/__init__.py`：聚合导出
7. `cli/main.py`：FcmdApp（路由表 + importlib 懒加载 + rich 表格 + 模糊匹配建议）
8. `cli/_common.py`：platform_command/ensure_platform/IGNORE_PATTERNS
9. `cli/pymake.py`：@fx.tool 示例（b/tc/lint/c 等子命令，聚合任务）

**测试**：test_toolkit/test_runner/test_compose/test_conditions

### P2 内置工具

**目标**：提供常用工具示例

**任务清单**：
1. `tools/which.py`：which 命令（查找可执行文件路径）
2. `tools/fileops.py`：文件操作（filedate/filelevel 等基础文件操作）
3. `tools/sysinfo.py`：系统信息（平台/Python 版本/环境变量）
4. 烟雾测试：端到端验证 `fcmd pymake b`、`fcmd which python` 等

## 关键接口签名

```python
# task.py
class TaskSpec(Generic[T]):  # frozen dataclass
    name: str
    fn: TaskFn[T] | None = None
    cmd: TaskCmd | None = None
    depends_on: tuple[str, ...] = ()
    soft_depends_on: tuple[str, ...] = ()
    # ... 精简字段

def task(fn=None, *, cmd=None, depends_on=(), ...) -> TaskSpec | Callable
def cmd(command: list[str], *, name=None, ...) -> TaskSpec

# graph.py
class Graph:
    @classmethod
    def from_specs(cls, specs, defaults=None, *, namespace=None) -> Graph
    def layers(self) -> list[list[str]]
    def validate(self) -> None

def graph(*specs: TaskSpec | str, defaults=None) -> Graph  # __init__.py 中定义

# executors.py
def run(graph: Graph, strategy="dependency", *, verbose=False, dry_run=False, ...) -> RunReport

# apis/toolkit.py
def tool(name: str, *, subcommand=None, cmd=None, needs=(), strategy=None,
         cwd=None, hidden=False, help="", ...) -> Callable
def run_tool(name: str, argv=None) -> int

# shell.py
def sh(cmd: list[str] | str, *, capture=False, check=True, label="命令",
       timeout=None) -> CompletedProcess | None

# console.py
def get_console() -> "Console"  # 懒加载 rich
```

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 懒加载导致 IDE 补全失效 | `__all__` 显式声明 + py.typed + 类型存根 |
| rich 懒加载与 API 一致性 | console.py 提供 `get_console()` 统一入口，核心模块不直接 import rich |
| TaskSpec 精简后功能不足 | 保留 soft_depends_on/conditions/continue_on_error，P2 按需扩展 |
| `fcmd pymake b` 参数歧义 | fcmd 级仅 --version/--help，工具级全局选项由 toolkit 管理 |
| 极速启动难以达标 | 三层懒加载 + 核心模块零依赖 + importlib 按需导入 |

## 验证方法

### P0 验证
```bash
# 类型检查 + lint
uv run ruff check src tests
uv run pyrefly check

# 单元测试
uv run pytest tests/test_task.py tests/test_graph.py tests/test_executors.py -v

# 编程式 API 验证
python -c "import fcmd as fx; print(fx.__version__)"
python -c "import fcmd as fx; g = fx.graph(fx.cmd(['echo', 'hello'], name='hi')); r = fx.run(g); print(r.success)"
```

### P1 验证
```bash
# CLI 验证
fcmd --version
fcmd --list
fcmd pymake b --dry-run
fcmd pymake --list

# 独立脚本验证
pymake b --dry-run

# 冷启动性能
python -X importtime -c "import fcmd" 2>&1 | tail -5
time fcmd --version
```

### P2 验证
```bash
fcmd which python
fcmd sysinfo
fcmd fileops --help
```

## 实施顺序

P0 → P1 → P2，每个阶段走完"计划→实现→测试→文档→验证"闭环。
阶段间无依赖倒置风险：P0 零外部依赖，P1 依赖 P0，P2 依赖 P1。
