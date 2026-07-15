# P5 迭代记录：fcmd info 内建命令

## 需求清单

- [x] 新增 `fcmd info [tool] [subcommand]` 内建命令
- [x] `fcmd info` 无参数列出全部内建命令与已注册工具概览（含子命令数）
- [x] `fcmd info <tool>` 列出工具的全部子命令（含 hidden）及 help/needs/strategy/类型
- [x] `fcmd info <tool> <subcommand>` 展示完整 ToolSpec 字段
- [x] 工具别名路由（如 `fcmd info pm`）
- [x] 未知工具 / 未知子命令返回错误码 1
- [x] 提取 `_load_tool_subs` 公共方法消除 `_builtin_info` 重复路径

## 迭代目标

在不引入新依赖的前提下，提供工具元信息内省能力，让用户在执行前即可查看
工具的子命令列表、依赖关系、TaskSpec 字段详情。与 `fcmd graph` 互补：
- `graph` 关注 DAG 拓扑结构（可视化）
- `info` 关注 TaskSpec 字段值（元数据）

## 改动文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/fcmd/cli/main.py` | 修改 | `_BUILTIN_COMMANDS` 加 `"info"`；新增 `_builtin_info` / `_load_tool_subs` / `_info_overview` / `_info_tool` / `_info_subcommand` / `_spec_kind`；`_run_builtin` 加 `info` 分支；`_list_tools` 示例加 `fcmd info pymake` |
| `tests/test_cli.py` | 修改 | 新增 `TestBuiltinInfo`（12 测试） |

## 关键决策与依据

### 1. 三层信息粒度

`fcmd info` 设计为三层渐进式信息展示：
- **概览**（无参数）：列出内建命令 + 已注册工具表（工具名/别名/子命令数/说明）
- **工具级**（`info <tool>`）：列出该工具全部子命令（visible 在前，hidden 在后），
  每行展示 子命令/类型/needs/strategy/help
- **子命令级**（`info <tool> <subcommand>`）：展示完整 ToolSpec 字段表
  （help/description/kind/cmd/needs/strategy/cwd/hidden/allow_upstream_skip/
  timeout/env/retry）

依据：用户偏好"集成函数、提升便利性"（user_profile），三层粒度让用户按需
获取信息，避免一次输出过多。

### 2. _spec_kind 分类辅助方法

新增 `_spec_kind(spec)` 静态方法判断 ToolSpec 类型：
- `cmd`：有 `cmd` 字段（执行外部命令，函数体不执行）
- `aggregate`：有 `needs` 无 `cmd` 无函数逻辑（纯依赖聚合点）
- `fn`：有函数逻辑（执行 Python 函数）

依据：用户在 `info` 输出中一眼可看出子命令的执行模式，比看 `cmd`/`needs`
字段自行推断更直观。

### 3. _load_tool_subs 提取

初版 `_builtin_info` 有 7 个 return（PLR0911 违规）。提取 `_load_tool_subs`
公共方法处理"模块导入 + 注册表查找"两步，将错误路径收敛到返回 `None`。
`_builtin_info` 降至 5 个 return，符合 ruff 阈值。

### 4. contextlib.suppress 替换 try-except-pass

`_info_overview` 中遍历工具触发模块导入以统计子命令数，导入失败时静默
跳过。原 `try-except-pass` 触发 SIM105，改用 `contextlib.suppress(ImportError)`。

### 5. 不引入 YAML 加载

YAML 配置加载需要 PyYAML 新依赖，属高风险暂停条件（rule-01），推迟到 P6
确认。P5 优先交付零依赖的 `info` 命令。

## 代码实现情况

### _builtin_info 路由

```python
def _builtin_info(self, argv: list[str]) -> int:
    if not argv:
        self._info_overview()
        return 0
    parsed = parser.parse_args(argv)
    resolved = self._resolve_tool(parsed.tool)
    if resolved is None:
        self._print_unknown_tool(parsed.tool)
        return 1
    subs = self._load_tool_subs(resolved)
    if subs is None:
        return 1
    if parsed.subcommand is None:
        self._info_tool(resolved, subs)
        return 0
    spec = subs.get(parsed.subcommand)
    if spec is None:
        # 未知子命令错误
        return 1
    self._info_subcommand(resolved, spec)
    return 0
```

### _info_tool 子命令表

排序策略：visible 在前（按名排序），hidden 在后（按名排序）。hidden 行
用 `[dim]` 样式标记，help 文本也置灰，视觉上区分内部 job。

### _info_subcommand 字段表

用 `rich.Table(show_header=False, box=None)` 渲染为键值对列表，便于
逐字段查看。`cmd` 字段对 tuple 类型用空格 join 展示完整命令。

## 整合优化情况

- `_load_tool_subs` 提取后，`_builtin_info` 与未来其他内建命令（如
  `completion`）可复用工具加载逻辑
- `_spec_kind` 静态方法可被其他可视化/内省场景复用
- `_info_overview` 复用 `_aliases_for` / `_tool_description`，与 `_list_tools`
  共享工具信息查询逻辑

## 测试验证结果

### 门禁

- `uv run ruff check src tests`：All checks passed
- `uv run ruff format --check src tests`：28 files already formatted
- `uv run pyrefly check`：0 errors (3 suppressed, 4 warnings not shown)
- `uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95`：411 passed, 96.97%

### 覆盖率对比

| 阶段 | 覆盖率 | 测试数 |
|------|--------|--------|
| P1 | 96.68% | - |
| P2 | 96.80% | 376 |
| P3 | 96.75% | - |
| P4 | 96.85% | 399 |
| P5 | 96.97% | 411 |

P5 覆盖率 96.97% > P4 96.85%，满足"不得低于上一次的值"约束。

### 新增测试

`TestBuiltinInfo`（12 测试）：
- `test_info_no_args_shows_overview`：概览列出内建命令与工具
- `test_info_tool_shows_subcommands`：工具级列出全部子命令（含 hidden 标记）
- `test_info_subcommand_shows_full_spec`：子命令级展示完整字段（tc 是 aggregate）
- `test_info_cmd_subcommand_shows_cmd`：cmd 任务展示命令（b → uv build）
- `test_info_unknown_tool`：未知工具返回 1
- `test_info_unknown_subcommand`：未知子命令返回 1
- `test_info_pm_alias_works`：别名路由
- `test_info_overview_shows_subcommand_count`：概览显示子命令数（含 hidden 标注）
- `test_info_subcommand_marked_hidden`：hidden 子命令带标记
- `test_spec_kind_classification`：_spec_kind 分类 cmd/aggregate/fn
- `test_info_tool_import_failure`：模块导入失败返回 1（monkeypatch 伪模块路径）
- `test_info_tool_not_in_registry`：工具未注册返回 1（monkeypatch 伪别名）

## 遗留事项

- P6：YAML 配置加载（需引入 PyYAML 依赖，高风险暂停条件，需用户确认）
- `main.py` 78-79 行（`_ensure_tools_discovered` 的 ImportError 静默路径）：
  需构造 pkgutil 扫描时导入失败的模块，属既有遗留
- `main.py` 311->exit 分支（`_info_subcommand` 的 `spec.subcommand is None`
  单命令工具路径）：pymake 无单命令工具，需专用测试工具覆盖

## 下一轮计划

P6 候选方向（按优先级）：
1. YAML 配置加载：参考 `ref/pyflowx/yaml_loader.py`，支持 jobs/needs/
   strategy.matrix，需先与用户确认引入 PyYAML 依赖
2. `fcmd completion --shell=bash|zsh|powershell`：生成 shell 补全脚本
3. 单命令工具支持：为 `info` / `graph` 补充 subcommand=None 的单命令工具路径
   测试（需先有单命令工具示例）
