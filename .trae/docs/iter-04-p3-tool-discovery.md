# 迭代 04：P3 工具自动发现机制

## 迭代目标

替代 P1 硬编码的 `_TOOL_ALIASES`/`_TOOL_MODULES` 字典，实现 `fcmd.cli` 包下工具模块的自动发现：
1. 用 `pkgutil.iter_modules` 扫描 `fcmd/cli/` 目录，模块名即工具名
2. 模块内可选定义 `__tool_aliases__: list[str]` 声明别名
3. 排除 `main` 入口模块、`_` 前缀私有模块
4. 懒加载策略：`FcmdApp.run()` 首次调用时触发扫描，`import fcmd` 冷启动不受影响

## 改动文件清单

### 修改
- `src/fcmd/cli/main.py`：
  - 新增 `_ensure_tools_discovered()` 函数，用 `pkgutil.iter_modules` 扫描 `fcmd.cli.__path__`
  - `_TOOL_ALIASES`/`_TOOL_MODULES` 从硬编码字面量改为初始空 dict，由 discovery 懒填充
  - 新增 `_TOOLS_DISCOVERED` 全局标志，保证 discovery 幂等
  - `FcmdApp.run()` 开头调用 `_ensure_tools_discovered()`
  - 用 `setdefault` 填充，不覆盖测试通过 `monkeypatch.setitem` 注入的键
- `src/fcmd/cli/pymake.py`：新增 `__tool_aliases__: list[str] = ["pm"]` 声明别名
- `tests/test_cli.py`：新增 `TestToolDiscovery` 测试类（8 个测试）

## 关键决策与依据

1. **`pkgutil.iter_modules` 而非 `importlib.metadata.entry_points`**：entry_points 需在 `pyproject.toml` 注册，违反"模块名即工具名"零配置原则。pkgutil 直接扫描包路径，无需额外配置。

2. **模块名即工具名约定**：`pymake.py` → tool_name="pymake"。`@fx.tool(name="pymake")` 的 name 参数必须与模块名一致。这是约定优于配置的设计，简化发现逻辑。

3. **`__tool_aliases__` 模块级常量**：别名无法从模块名推断，需显式声明。放在模块顶部（仅次于导入），扫描时导入模块读取。`getattr(mod, "__tool_aliases__", ())` 提供默认空元组，未声明的模块不影响。

4. **懒加载策略保证冷启动**：`_ensure_tools_discovered()` 只在 `FcmdApp.run()` 调用时触发，`import fcmd` 不触发。`_TOOLS_DISCOVERED` 标志保证幂等。测试验证 `test_cold_start_import_does_not_trigger_discovery`。

5. **`setdefault` 不覆盖测试注入**：discovery 用 `setdefault` 填充字典，测试通过 `monkeypatch.setitem` 注入的键不会被覆盖。测试验证 `test_discovery_does_not_override_mock_entries`。

6. **排除规则**：`main` 模块（CLI 入口本身）、`_` 前缀模块（私有/内部）不作为工具。包自身（`__init__`）由 `pkgutil.iter_modules` 自动跳过。

7. **向后兼容**：P2 的 86 个测试全过，动态发现机制完全向后兼容。`_TOOL_ALIASES`/`_TOOL_MODULES` 接口不变，仅填充时机从模块加载改为首次 `run()`。

## 验证结果

### 全套门禁
- `ruff check src tests`：All checks passed
- `ruff format --check src tests`：28 files already formatted
- `pyrefly check`：0 errors (3 suppressed, 3 warnings not shown)
- `pytest -m "not slow" --cov=fcmd --cov-fail-under=95`：384 passed, coverage 96.75%

### 覆盖率
- 总覆盖率：96.75%（P2: 96.80% ↓ 0.05%，仍超 P1 基线 96.68%）
- `cli/main.py`：97%（114 stmts, 3 miss, 34 branch, 1 BrPart）
  - 未覆盖：72-73（discovery 的 ImportError 分支）、167（_run_tool 防御分支）
- `cli/pymake.py`：100%

### Discovery 测试（8 个）
- `test_discovery_finds_pymake_module`：扫描后发现 pymake
- `test_discovery_loads_aliases_from_module`：读取 `__tool_aliases__` 注册 pm 别名
- `test_discovery_is_idempotent`：多次调用不重复扫描
- `test_discovery_skips_main_module`：排除 main 入口
- `test_discovery_skips_private_modules`：排除 _ 前缀模块
- `test_discovery_does_not_override_mock_entries`：setdefault 不覆盖测试注入
- `test_run_triggers_discovery`：FcmdApp.run() 触发 discovery
- `test_cold_start_import_does_not_trigger_discovery`：import fcmd 不触发 discovery

### 扩展性验证
- 新增工具只需在 `fcmd/cli/` 下创建模块（如 `gittool.py`），无需修改 `main.py`
- 工具自动出现在 `fcmd` 列表与路由中
- 别名通过 `__tool_aliases__` 声明，零配置

## 遗留事项（P4）

- 内建命令 `fcmd graph`（可视化 DAG）、`fcmd info`（显示任务详情）
- YAML 配置加载（`fcmd yamlrun` 命令，从 YAML 定义任务 DAG）
- 更多内置工具（gittool/hashfile/...）放 `fcmd/tools/`
- discovery 的 ImportError 分支（72-73 行）未覆盖（需构造导入失败的工具模块）
- Windows 控制台 UnicodeEncodeError（`▸` 字符在 GBK 编码下报错，预存在问题）

## 下一轮计划

P4 候选方向（待用户确认优先级）：
1. 内建命令 `fcmd graph` 可视化 DAG 执行计划
2. YAML 配置加载（`fcmd yamlrun`）
3. 更多内置工具（gittool/hashfile/...）
