# 迭代 02：P1 @fx.tool 装饰器框架 + CLI 路由

## 迭代目标

在 P0 DAG 调度核心基础上落地"组合 CLI"层：
1. `@fx.tool` 装饰器：函数签名→argparse 自动生成 CLI，`needs`/`strategy`/`cmd` 表达 DAG 依赖与执行方式
2. `FcmdApp` 路由：统一入口 `fcmd <tool> [command] [options]`，importlib 懒加载工具模块
3. 示例工具 `pymake`：验证装饰器+路由全链路可用

## 改动文件清单

### 新建
- `src/fcmd/apis/toolkit.py`：@fx.tool 装饰器核心（ToolSpec/注册表/run_tool/argparse 构建）
- `src/fcmd/cli/pymake.py`：示例工具（cmd/聚合/hidden 三种任务类型）
- `tests/test_toolkit.py`：96 个测试，toolkit.py 覆盖率 99%
- `scripts/verify_p1.py`：P1 API 验证脚本
- `.trae/documents/fcmd-p1-implementation-plan.md`：P1 实施计划

### 修改
- `src/fcmd/apis/__init__.py`：聚合导出 toolkit 符号
- `src/fcmd/__init__.py`：_LAZY_ATTRS 追加 8 个 toolkit 符号 + __all__ 更新
- `src/fcmd/cli/main.py`：扩展为 FcmdApp 路由类（保留 _build_parser 兼容）
- `tests/test_cli.py`：新增 13 个 FcmdApp 测试
- `scripts/verify_api.py`：加 `# type: ignore[not-callable]` 处理 pyrefly 懒加载误报

## 关键决策与依据

1. **ToolSpec 字段对齐 pyflowx**：name/subcommand/func/help/description/cmd/needs/strategy/cwd/allow_upstream_skip/hidden/env/retry/timeout，复用 P0 的 TaskSpec 作为运行时表示。

2. **三种任务类型转换**：
   - cmd 任务（有 cmd）：执行命令，cwd 从 CLI 变量或装饰器取
   - 聚合任务（有 needs 无 cmd 无函数体）：fn=_noop，仅作依赖聚合点
   - fn 任务（有函数体）：按签名从 CLI 变量取 kwargs

3. **`_has_function_logic` 用 ast 分析**：判断函数体是否仅 pass/docstring/...，避免 exec 函数体。聚合任务判定依赖此函数。

4. **`run_tool` 捕获 TaskFailedError/FcmdError**：P0 的 `run()` 在 `continue_on_error=False`（默认）时抛 TaskFailedError，run_tool 捕获后返回 FAILURE 并打印诊断，不向上传播。

5. **`run_tool` 返回 int 退出码**：不抛 SystemExit，由调用方（pymake.main/FcmdApp）决定是否 sys.exit。ToolExitCode 是 IntEnum（SUCCESS=0/FAILURE=1/INTERRUPTED=130）。

6. **list[X] 注解兼容 3.8**：`from __future__ import annotations` 让注解变字符串，`_is_list_annotation` 通过 `str().startswith("list["/"List[")` 双重判断；`_list_inner_type` 从 `__args__` 或字符串提取内部类型。

7. **FcmdApp 路由**：硬编码 `_TOOL_ALIASES`/`_TOOL_MODULES`（P1 仅 pymake），importlib 懒加载触发 @tool 注册，rich 表格列工具，difflib 模糊匹配提示。

8. **`main()` 统一走 FcmdApp**：`sys.exit(FcmdApp().run())`，无参数时 `_list_tools()` 返回 0。删除 P0 的 `_build_parser().parse_args()` 路径（保留 `_build_parser` 函数供旧测试）。

9. **verify_api.py 的 pyrefly 误报**：`fx.task` 被 pyrefly 解析为 `fcmd.task` 模块（懒加载 `__getattr__` 的类型推断限制），加 `# type: ignore[not-callable]` 处理。

10. **聚合任务依赖限制**：聚合任务的依赖如果是 fn 任务，fn 任务的参数需要从 CLI 提供。但聚合任务的 CLI 只接受全局选项，所以聚合任务通常依赖 cmd 任务（如 pymake tc 依赖 c + pyrefly_check + lint）。这是设计约束，非 bug。

## 验证结果

### 全套门禁
- `ruff check src tests`：All checks passed
- `ruff format --check src tests`：27 files already formatted
- `pyrefly check`：0 errors (7 suppressed)
- `pytest -m "not slow" --cov=fcmd --cov-fail-under=95`：306 passed, coverage 96.68%

### 覆盖率
- 总覆盖率：96.68%（P0: 95.53% ↑）
- `apis/toolkit.py`：99%（266 stmts, 0 miss, 119 branch, 3 BrPart）
- P0 模块覆盖率保持或提升

### API 验证（scripts/verify_p1.py）
- cmd 任务：`fx.run_tool("demo", ["hello"])` → 输出 "hi from tool"，exit 0
- fn 任务：`fx.run_tool("demo", ["greet", "world", "--times", "2"])` → exit 0
- 聚合任务：`fx.run_tool("demo", ["all"])` → thread 策略并行执行 hello + prep，exit 0
- dry-run：`fx.run_tool("demo", ["hello", "--dry-run"])` → 打印执行计划，不执行，exit 0

### CLI 验证
- `fcmd` → rich 表格列出 pymake 工具 + 别名 pm + 示例
- `fcmd pymake` → rich 表格列出 6 个子命令（all/b/c/lint/t/tc）
- `fcmd pymake b` → 执行 `python --version`，verbose 输出生命周期
- `fcmd pm b --quiet` → 别名路由 + quiet 抑制 verbose，exit 0
- `fcmd unknown_tool` → 错误提示 + exit 1
- `fcmd pymak` → 模糊匹配建议 "pymake, pm" + exit 1
- `fcmd --version` → `fcmd 0.1.0`

### 冷启动
- `import fcmd`：4.4ms < 100ms

## 遗留事项（P2）

- 内置工具集（gittool/hashfile/...）放 `fcmd/tools/`
- YAML 配置加载（`yamlrun` 命令）
- 内建命令 `graph`/`info`/`completion`
- `pf` 风格的工具自动发现机制（替代硬编码 `_TOOL_MODULES`）
- `verify_api.py` 的 pyrefly 懒加载误报需更优雅的解决方案（如 py.typed stub 或 pyrefly plugin）
