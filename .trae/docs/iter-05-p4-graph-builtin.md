# P4 迭代记录：fcmd graph 内建命令

## 需求清单

- [x] 新增 `fcmd graph <tool> [subcommand] [--format=mermaid|layers|describe]` 内建命令
- [x] 复用既有 `Graph.to_mermaid()` / `layers()` / `describe()` 实现可视化
- [x] 支持 `--format` 切换输出格式（Mermaid / 分层 / 摘要）
- [x] `fcmd graph` 无参数打印帮助
- [x] 未知工具 / 未知子命令返回错误码 1
- [x] 工具别名路由（如 `fcmd graph pm tc`）

## 迭代目标

在不引入新依赖的前提下，基于既有 `Graph` 可视化能力提供 `fcmd graph` 内建命令，
让用户在执行前即可查看 DAG 执行计划，提升工具集的可内省性。同时将 DAG 构建逻辑
从 `run_tool()` 中提取为公共 API `build_tool_graph()`，便于其他场景复用。

## 改动文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/fcmd/apis/toolkit.py` | 新增 | `build_tool_graph()` 公共 API；从 `run_tool` 抽取 DAG 构建逻辑 |
| `src/fcmd/apis/__init__.py` | 修改 | 导出 `build_tool_graph` |
| `src/fcmd/__init__.py` | 修改 | `__all__` 与 `_LAZY_ATTRS` 注册 `build_tool_graph` |
| `src/fcmd/cli/main.py` | 修改 | 新增 `_BUILTIN_COMMANDS` / `_run_builtin()` / `_builtin_graph()`；更新 `_list_tools` 示例 |
| `tests/test_cli.py` | 修改 | 新增 `TestBuiltinGraph`（10 测试）+ `TestBuildToolGraph`（4 测试） |

## 关键决策与依据

### 1. 内建命令 vs @fx.tool 注册

`fcmd graph` 不通过 `@fx.tool` 注册，而是作为 `_BUILTIN_COMMANDS` 由 `FcmdApp`
直接处理。原因：
- `graph` 是元命令（消费工具元数据，不执行任务），不符合 `@fx.tool` 的"函数签名
  驱动 CLI + 函数体即逻辑"模型
- 内建命令优先于工具路由，避免与可能存在的同名工具冲突
- 后续 `info` / `completion` 等内建命令可沿用此模式扩展

### 2. build_tool_graph 设计

从 `run_tool()` 中提取 DAG 构建逻辑为独立函数：
- 接收 `(name, target)`，返回 `Graph`（不执行）
- `target=None` 时包含工具全部子命令（含 hidden），便于完整可视化
- `target` 非 None 时仅含 target 及其传递依赖（`_collect_with_deps` BFS）
- 用空 `variables={}` 构建 TaskSpec，因为可视化只关心拓扑结构

### 3. target=None 语义修复

初版 `build_tool_graph` 对 `target=None` 返回空图（bug）：`_collect_with_deps`
对多命令工具返回 `[None]`，循环中 `sc is None` 触发 `continue` 导致 `task_specs`
为空。修复为 `target=None` 时直接取 `subs.values()` 全量构建。

### 4. 不引入 YAML 加载

参考 `ref/pyflowx/yaml_loader.py` 的 YAML 加载能力推迟到 P5：
- YAML 加载需要 PyYAML 新依赖（违反"新增依赖须审慎"原则）
- P4 优先交付零依赖、基于既有能力的可视化命令
- YAML 加载与 `fcmd graph` 是正交功能，可独立迭代

## 代码实现情况

### `build_tool_graph` (toolkit.py)

```python
def build_tool_graph(name: str, target: str | None) -> Graph:
    """构建工具的 DAG（不执行），用于可视化与内省。"""
    if name not in _TOOL_REGISTRY:
        raise FcmdError(f"工具 {name!r} 未注册")
    subs = _TOOL_REGISTRY[name]
    if target is not None and target not in subs:
        raise FcmdError(f"工具 {name!r} 没有子命令 {target!r}")
    if target is None:
        selected: list[ToolSpec] = list(subs.values())
    else:
        chain = _collect_with_deps(name, target)
        selected = [subs[sc] for sc in chain if sc in subs]
    task_specs: list[TaskSpec[Any]] = [_build_task_spec(spec, {}) for spec in selected]
    return Graph.from_specs(task_specs, defaults=GraphDefaults())
```

### `_builtin_graph` (main.py)

支持三种 `--format`：
- `mermaid`（默认）：`graph.to_mermaid()`，可粘贴到 mermaid.live
- `layers`：`graph.layers()`，每层一行 `Layer N: [...]`
- `describe`：`graph.describe()`，人类可读多行摘要

路由：`FcmdApp.run()` 中 `first in _BUILTIN_COMMANDS` 优先于工具路由。

## 整合优化情况

- 将 DAG 构建逻辑从 `run_tool` 提取为 `build_tool_graph` 公共 API，消除未来
  YAML 加载、其他内建命令重复实现 DAG 构建的可能
- `_builtin_graph` 复用 `_resolve_tool` / `_print_unknown_tool`，与工具路由
  共享错误提示逻辑
- `_run_builtin` 设计为分发器，新增内建命令只需在此处加分支

## 测试验证结果

### 门禁

- `uv run ruff check src tests`：All checks passed
- `uv run ruff format --check src tests`：28 files already formatted
- `uv run pyrefly check`：0 errors (3 suppressed, 3 warnings not shown)
- `uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95`：399 passed, 96.85%

### 覆盖率对比

| 阶段 | 覆盖率 | 测试数 |
|------|--------|--------|
| P1 | 96.68% | - |
| P2 | 96.80% | 376 |
| P3 | 96.75% | - |
| P4 | 96.85% | 399 |

P4 覆盖率 96.85% > P3 96.75%，满足"不得低于上一次的值"约束。

### 新增测试

`TestBuiltinGraph`（10 测试）：
- `test_graph_pymake_tc_mermaid`：默认 Mermaid 输出
- `test_graph_pymake_all_mermaid`：聚合任务 DAG
- `test_graph_format_layers`：分层格式
- `test_graph_format_describe`：摘要格式
- `test_graph_unknown_tool`：未知工具错误
- `test_graph_unknown_subcommand`：未知子命令错误
- `test_graph_no_args_prints_help`：无参数打印帮助
- `test_graph_pm_alias_works`：别名路由
- `test_graph_single_command_tool`：单任务节点
- `test_graph_no_subcommand_shows_all`：无子命令时全量输出
- `test_run_builtin_unknown_name`：未知内建命令防御路径

`TestBuildToolGraph`（4 测试，直接 API）：
- `test_unknown_tool_raises`：未注册工具抛 FcmdError
- `test_unknown_subcommand_raises`：未注册子命令抛 FcmdError
- `test_none_target_includes_all_subcommands`：target=None 全量包含
- `test_target_with_deps_includes_upstream`：target 带 deps 含上游

## 遗留事项

- P5：YAML 配置加载（参考 `ref/pyflowx/yaml_loader.py`，需引入 PyYAML 依赖）
- P5：`fcmd info` 内建命令（显示工具/子命令详情）
- P5：`fcmd completion` shell 补全生成
- `dag.py` `layers()` 未校验路径（325-329）的覆盖：需构造未 `validate()` 的
  Graph 直接调 `layers()`，属于既有遗留，非 P4 引入

## 下一轮计划

P5 候选方向（按优先级）：
1. YAML 配置加载：参考 pyflowx 的 GitHub Actions 风格 YAML，支持 jobs/needs/
   strategy.matrix，让用户用 YAML 描述 DAG 而非 Python 装饰器
2. `fcmd info <tool>` 内建命令：显示工具的子命令列表 + 依赖关系 + TaskSpec 详情
3. `fcmd completion --shell=bash|zsh|powershell`：生成 shell 补全脚本
