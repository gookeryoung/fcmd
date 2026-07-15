# P7 迭代记录：单命令工具路由 / completion / YAML 配置

## 需求清单

- [x] P7a：修复 `run_tool` 单命令工具路由（writefile/folderzip 改回单命令）
- [x] P7a：更新测试匹配单命令工具签名
- [x] P7b：实现 `fcmd completion` 内建命令（bash/zsh/fish 脚本生成）
- [x] P7b：completion 测试覆盖
- [x] P7c：询问用户确认 PyYAML 依赖后实现 YAML 配置加载
- [x] P7c：yaml_loader 与 Graph.from_yaml API
- [x] P7c：yaml_loader 测试覆盖

## 迭代目标

完成 P6 遗留的全部 P7 候选项：单命令工具路由修复、shell 自动补全、
YAML 任务编排（GitHub Actions 风格，简化版）。

## 改动文件清单

- `src/fcmd/cli/main.py`（修改）：
  - `_BUILTIN_COMMANDS` 增加 `"completion"` 与 `"yaml"`
  - 新增 `_builtin_completion` / `_collect_completion_data` /
    `_gen_bash_script` / `_gen_zsh_script` / `_gen_fish_script`
  - 新增 `_builtin_yaml`
- `src/fcmd/yaml_loader.py`（新增）：YAML 解析为 Graph，简化版 schema
- `src/fcmd/dag.py`（修改）：新增 `Graph.from_yaml` 类方法
- `src/fcmd/__init__.py`（修改）：`__all__` 与 `_LAZY_ATTRS` 注册 yaml_loader API
- `pyproject.toml`（修改）：新增 `pyyaml>=6.0.1` 依赖
- `tests/test_cli.py`（修改）：新增 `TestBuiltinCompletion`（18）与 `TestBuiltinYaml`（12）
- `tests/test_yaml_loader.py`（新增）：49 个测试覆盖解析、字段兼容、错误处理、文件加载、执行

## 关键决策与依据

### 1. P7a：单命令工具通过 subcommand=None 路由

**问题**：P6 将 writefile/folderzip 改为 `subcommand="w"`/`"z"` 是因为
`run_tool` 把第一个非 dash 参数视为子命令名。

**决策**：改造 `run_tool` 路由逻辑——工具的 `_TOOL_REGISTRY[tool]` 仅含
`{None: spec}` 时（即单命令工具），跳过子命令查找，直接执行。
writefile/folderzip 恢复 `subcommand=None`。

**依据**：用户偏好 GUI 简化与后端富化；框架应为单命令工具提供自然路径，
而非让工具被迫绕开。

### 2. P7b：fish `__fish_seen_subcommand_from` OR 语义

**问题**：初版 fish 脚本对别名生成多个 `__fish_seen_subcommand_from` 调用，
但 fish 中多个独立调用是 AND（同时见过 pymake 与 pm，别名场景下不可能成立）。

**决策**：合并为单次调用——`__fish_seen_subcommand_from pymake pm`，
单次调用多参数是 OR 语义。

**依据**：fish 官方文档；smoke 测试别名场景。

### 3. P7c：PyYAML 依赖需用户确认

**决策**：rule-01 暂停条件第 2 条要求新依赖需用户确认；
通过 AskUserQuestion 提供 ruamel.yaml / PyYAML / 手写解析三选项，
用户选择 PyYAML。

**依据**：rule-01 高风险/不可逆操作边界。

### 4. P7c：简化 schema（不支持 matrix / if 条件）

**问题**：pyflowx 的 yaml_loader 支持 `strategy.matrix` 扇出与 `if` 条件，
需额外 conditions 模块。

**决策**：fcmd 版仅保留 `jobs`/`needs`/`cmd`/`run`/`env`/`cwd`/`timeout`/
`retry`/`strategy`/`defaults`/`tags`/`verbose`/`continue-on-error`/
`allow-upstream-skip`。

**依据**：rule-01 "不写未被要求的功能、不为未来预留扩展点"。
当前需求为配置加载，matrix/if 属于高级编排，按需再加。

### 5. P7c：hyphen/underscore 字段兼容

**决策**：`_get_field(data, name)` 同时检查下划线与连字符两种命名
（`continue_on_error` 与 `continue-on-error` 均可）。

**依据**：GitHub Actions 原生使用连字符，Python 风格使用下划线，
兼容降低用户记忆负担。

### 6. P7c：pyrefly src-layout 类型不匹配

**问题**：pyrefly 将 `fcmd.dag.Graph` 与 `src.fcmd.dag.Graph` 视为不同类型，
`Graph.from_yaml` 直接 `return load_yaml(path)` 报 bad-return。

**决策**：`return cast("Graph", load_yaml(path))`。

**依据**：pyrefly 在 src-layout 下的已知 quirk；cast 是类型系统无法表达时的合法回退。

### 7. P7c：移除 `_replace` 死分支

**问题**：初版 `_replace(defaults, **overrides)` 含
`if not overrides: return defaults` 守卫，但所有调用方都传非空 overrides。

**决策**：删除 `_replace` 函数，`_build_graph` 内直接 `replace(defaults, ...)`。

**依据**：rule-11 "不留死分支"。

## 代码实现情况

### P7a：单命令工具路由

`run_tool` 在解析到工具后，检查该工具的注册表：
若仅含 `{None: spec}`（单命令工具），直接调用 spec 而不做子命令查找。
writefile/folderzip 恢复 `subcommand=None`。

### P7b：completion 内建命令

`fcmd completion --shell bash|zsh|fish` 输出静态补全脚本到 stdout，
嵌入当前工具名、别名、子命令名。脚本静态生成，新增工具后需重新 eval。

- bash：`_fcmd_complete` 函数 + `complete -F`
- zsh：`_fcmd` compadd 风格
- fish：`__fish_seen_subcommand_from` 单次多参数 OR 语义

### P7c：YAML 加载

- `parse_yaml_string(text) -> Graph`：从字符串解析
- `load_yaml(path) -> Graph`：从文件加载
- `Graph.from_yaml(path) -> Graph`：便捷类方法
- 内部 `_build_graph` / `_parse_defaults` / `_parse_retry` / `_build_spec` 等
- `_get_field` 统一处理 hyphen/underscore 兼容

`fcmd yaml <file> [job] [--dry-run] [--strategy S] [--verbose]` 内建命令
加载 YAML 并执行任务图。

## 整合优化情况

- 删除 `_replace` 函数及其死分支（rule-11）
- `__all__` 与 `_LAZY_ATTRS` 同步注册 yaml_loader 公共 API
- Graph.from_yaml 通过 cast 绕过 pyrefly src-layout 类型 quirk
- 测试增加 `_echo_cmd` 跨平台 helper（Windows `echo` 是 shell builtin）

## 测试验证结果

- 526 tests passed（P6: 446 → P7: 526，新增 80）
- coverage 97.33%（P6: 97.14% → P7: 97.33%，提升 0.19%）
- yaml_loader.py 100% 覆盖
- ruff check / format / pyrefly 全通过

## 遗留事项

- 无明确遗留项。可选方向：
  - `fcmd completion` 支持动态补全（运行时查询而非静态嵌入）
  - YAML schema 扩展（matrix / if 条件，需 conditions 模块）
  - 更多 CLI 工具移植

## 下一轮计划

P7 完成 fcmd 全部规划候选。后续方向由用户决定：
继续移植 pyflowx 工具、扩展 YAML schema、或推进其他基础设施。
