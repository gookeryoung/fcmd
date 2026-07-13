# fcmd P0 收尾实施计划（剩余工作）

## Summary

承接已批准的 `fcmd-p0-implementation-plan.md`：核心模块（9 个）+ CLI 入口 + 4 个测试文件已就位，本计划聚焦剩余收尾工作——补齐 3 个测试文件、跑通全套门禁（ruff/format/pyrefly/pytest 95%）、API 与 CLI 验证、迭代文档与 git 提交。完成后 P0 阶段交付完毕，自动进入 P1（CLI 框架）。

参考 pyflowx 的 `tests/test_executors.py` 与 `tests/test_report.py` 测试组织方式，但仅覆盖 fcmd 保留的核心 API（砍掉序列化/诊断/性能剖面/取消/通知等扩展测试）。

## Current State Analysis

### 已就位文件清单（实际状态）

| 文件 | 状态 | 关键 API |
|------|------|---------|
| `src/fcmd/task.py` | 完整 | TaskSpec(17 字段) + RetryPolicy + TaskStatus + TaskResult + TaskEvent + task/cmd |
| `src/fcmd/context.py` | 完整 | build_call_args / describe_injection / is_context_annotation |
| `src/fcmd/graph.py` | 完整 | Graph + GraphDefaults + from_specs/add/layers/resolved_spec/subgraph_with_deps/to_mermaid/describe |
| `src/fcmd/command.py` | 完整 | run_command（list/str/callable 统一） |
| `src/fcmd/console.py` | 完整 | get_console 懒加载 |
| `src/fcmd/report.py` | 完整 | RunReport + __getitem__/result_of/summary/failed_tasks/succeeded_tasks/skipped_tasks/tasks_by_status/describe |
| `src/fcmd/executors.py` | 完整 | run + 4 策略 + SyncTaskRunner/AsyncTaskRunner + DependencyRunner + 模块级辅助 |
| `src/fcmd/_lazy.py` | 完整 | lazy_import 代理 |
| `src/fcmd/__init__.py` | 完整 | __getattr__ 懒加载 + graph() 快捷函数 |
| `src/fcmd/cli/main.py` | P0 最小 | --version + --help |
| `src/fcmd/errors.py` | 完整 | FcmdError + 6 子类 |
| `tests/test_task.py` | 完整 | 44 个测试 |
| `tests/test_context.py` | 完整 | 24 个测试 |
| `tests/test_graph.py` | 完整 | 43 个测试 |
| `tests/test_command.py` | 完整 | 12 个测试（含 Windows/Linux 兼容） |

### 待完成清单

1. **创建 3 个测试文件**：test_report.py / test_executors.py / test_init.py
2. **运行门禁**：ruff check + ruff format --check + pyrefly check + pytest --cov 95%
3. **修复失败**：lint/类型/测试/覆盖率问题
4. **API 验证**：编程式示例（cmd 任务 + fn 任务自动依赖推断）
5. **CLI 验证**：`fcmd --version` 输出版本号
6. **迭代文档**：`.trae/docs/iter-01-p0-core-framework.md`
7. **Git 提交**：按文件名 add + 中文 commit + push（分支已跟踪远程时）

## Proposed Changes

### 步骤 1：创建 `tests/test_report.py`

**职责**：覆盖 `src/fcmd/report.py` 的全部公共 API。

**测试用例清单**（10 个）：
- `test_run_report_getitem_returns_value`：`report["name"]` 返回 value（非 TaskResult）
- `test_run_report_getitem_missing_raises_keyerror`：缺失任务抛 KeyError
- `test_run_report_result_of_returns_full_result`：`result_of` 返回完整 TaskResult
- `test_run_report_contains`：`in` 检查存在/不存在
- `test_run_report_iter_and_len`：迭代顺序与长度
- `test_run_report_summary`：含 run_id/success/total_tasks/by_status/total_duration_seconds
- `test_run_report_failed_tasks`：仅返回 FAILED 任务名
- `test_run_report_succeeded_tasks`：仅返回 SUCCESS 任务名
- `test_run_report_skipped_tasks`：仅返回 SKIPPED 任务名
- `test_run_report_tasks_by_status`：按指定状态过滤
- `test_run_report_describe`：调试描述含 run_id/success 与每任务行

**辅助函数**：`_make_result(name, status, value, duration, attempts, reason)` 构造 TaskResult，参考 pyflowx 测试模式但去掉 tags/depends_on/outputs 字段（fcmd RunReport 不暴露这些）。

**关键点**：
- 不依赖 `run()`，直接构造 RunReport + TaskResult 注入 `report.results`，测试纯数据结构行为
- duration 通过 `datetime` + `timedelta` 精确设置，避免浮点截断
- success 字段默认 True，由测试按场景修改

---

### 步骤 2：创建 `tests/test_executors.py`

**职责**：覆盖 `src/fcmd/executors.py` 的 `run()` 公共 API 与 4 种策略。这是最复杂的测试文件。

**测试用例清单**（约 22 个，按功能分组）：

**基础执行（4 策略）**：
- `test_run_sequential_simple`：sequential 策略两任务（自动依赖推断）
- `test_run_thread_strategy`：thread 策略并行执行
- `test_run_async_strategy`：async 策略
- `test_run_dependency_strategy_default`：默认 dependency 策略
- `test_run_diamond_dependency`：菱形依赖 4 任务

**dry_run 与 verbose**：
- `test_run_dry_run`：dry_run=True 返回空报告，不执行任务（capsys 验证打印）
- `test_run_verbose`：verbose=True 打印任务生命周期（capsys 验证）

**依赖注入**：
- `test_run_auto_dep_injection`：参数名匹配依赖，上游结果注入下游
- `test_run_cmd_task`：cmd 任务执行（用 echo 等跨平台命令）
- `test_run_soft_dependency_with_default`：软依赖未提供时注入 defaults
- `test_run_soft_dependency_with_upstream_value`：软依赖上游成功时注入其值

**失败处理**：
- `test_run_failure_propagation`：任务失败抛 TaskFailedError，含 task/cause/attempts
- `test_run_continue_on_error`：continue_on_error=True 不抛异常，下游硬依赖被 SKIPPED
- `test_run_retry_then_success`：RetryPolicy(max_attempts=3) 第 3 次成功
- `test_run_retry_exhausted`：重试耗尽抛 TaskFailedError，attempts == max_attempts
- `test_run_allow_upstream_skip`：上游 SKIPPED 后 allow_upstream_skip=True 仍执行
- `test_run_conditions_skip`：conditions 返回 False 时任务被 SKIPPED
- `test_run_timeout`：超时抛 TaskFailedError（cause 为 TaskTimeoutError）

**异步任务**：
- `test_run_async_fn_dependency`：异步 fn 任务 + 自动依赖注入

**过滤**：
- `test_run_only_filter`：only=["double"] 含传递依赖
- `test_run_tags_filter`：tags=["test"] 含传递依赖
- `test_run_only_and_tags_union`：only 与 tags 取并集

**on_event 回调**：
- `test_run_on_event_callback`：on_event 收到 RUNNING + SUCCESS 事件

**关键实现要点**：
- 跨平台命令：用 `sys.platform == "win32"` 分支选择 `["cmd", "/c", "..."]` vs `["sh", "-c", "..."]`
- 异步测试不依赖 `pytest-asyncio`：直接调用 `run()`（内部 `asyncio.run`）
- 超时测试用 0.3s timeout + 长任务（`time.sleep(1)` 或 `ping -n 10`）
- on_event 用列表收集事件，断言状态序列
- continue_on_error 测试：上游 FAILED + 下游硬依赖 → 下游 SKIPPED + 第三方独立任务 SUCCESS

---

### 步骤 3：创建 `tests/test_init.py`

**职责**：覆盖 `src/fcmd/__init__.py` 的懒加载机制与 `graph()` 快捷函数。

**测试用例清单**（5 个）：
- `test_lazy_import_task_spec`：`fcmd.TaskSpec` 首次访问触发导入并返回正确符号
- `test_lazy_import_missing_attribute`：`fcmd.nonexistent` 抛 AttributeError 含模块名
- `test_lazy_import_caches_to_globals`：二次访问命中 globals 缓存（通过 `id()` 一致性）
- `test_dir_returns_complete_list`：`dir(fcmd)` 包含 `__all__` 全部符号
- `test_graph_shortcut_function`：`fx.graph(spec1, spec2)` 等价 `Graph.from_specs([spec1, spec2])`

**关键点**：
- 懒加载缓存测试：首次访问后 `name in fcmd.__dict__` 应为 True
- graph() 快捷函数：构造简单两任务图，验证自动依赖推断生效

---

### 步骤 4：运行门禁（按顺序，逐项修复）

**命令序列**：
```bash
# 1. ruff lint
uv run ruff check src tests

# 2. ruff format
uv run ruff format --check src tests
# 如有差异：uv run ruff format src tests

# 3. pyrefly 类型检查
uv run pyrefly check

# 4. pytest + 覆盖率
uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95
```

**预期问题与修复策略**：

| 类别 | 可能问题 | 修复方式 |
|------|---------|---------|
| ruff ARG001/ARG002 | 测试 fixture 未使用 | tests/ 已在 per-file-ignores |
| ruff PLR0913 | task() 装饰器参数过多 | 已有 `# noqa: PLR0913` |
| ruff PLC0415 | 懒导入 | 已在 ignore 列表 |
| pyrefly 类型 | TaskResult 泛型 / cast | 加 `# type: ignore[具体规则]` 或修正注解 |
| pytest 失败 | 跨平台命令差异 | sys.platform 分支处理 |
| coverage < 95% | 某些分支未覆盖 | 补充测试，不放宽断言，不用 `# pragma: no cover` 绕过 |

**修复原则**：
- 失败时定位根因，不放宽断言
- 覆盖率不得低于 95% 门槛
- 不修改 git config / 不跳过 hooks

---

### 步骤 5：API 验证

**命令**（逐条执行，验证输出）：
```bash
# 1. 版本号
uv run python -c "import fcmd as fx; print(fx.__version__)"
# 期望：0.1.0

# 2. cmd 任务
uv run python -c "import fcmd as fx; g = fx.graph(fx.cmd(['echo', 'hello'], name='hi')); r = fx.run(g); print(r.success)"
# 期望：True

# 3. fn 任务自动依赖推断
uv run python -c "import fcmd as fx
@fx.task
def extract(): return [1, 2, 3]
@fx.task
def double(extract): return [x * 2 for x in extract]
g = fx.graph(extract, double)
r = fx.run(g)
print(r['double'])"
# 期望：[2, 4, 6]

# 4. CLI
uv run fcmd --version
# 期望：fcmd 0.1.0
```

---

### 步骤 6：冷启动性能验证

```bash
uv run python -X importtime -c "import fcmd" 2>&1 | Select-Object -Last 10
```
验证顶层导入 < 100ms（self 时间 总和）。

---

### 步骤 7：创建迭代文档

**文件**：`.trae/docs/iter-01-p0-core-framework.md`

**内容结构**：
- 迭代目标：P0 核心框架（DAG 调度 + 懒加载聚合 + CLI 入口）
- 改动文件清单：12 个源文件 + 7 个测试文件 + 1 个文档
- 关键决策与依据：
  - TaskSpec 17 字段（相对 pyflowx 砍 13 个）
  - 三层懒加载（__init__.__getattr__ / cli/main.py importlib / console.py rich）
  - DependencyRunner 增量就绪集（in_degree + dependents）
  - 砍掉 cancellation/notification/progress/storage/diagnostics 等扩展
- 验证结果：ruff/pyrefly/pytest 95% 全通过 + API/CLI 验证通过
- 遗留事项：P1 CLI 框架（shell.py/conditions.py/runner.py/apis/cli 完整版）+ P2 内置工具

---

### 步骤 8：Git 提交

```bash
git add src/fcmd/task.py src/fcmd/context.py src/fcmd/graph.py src/fcmd/command.py src/fcmd/console.py src/fcmd/report.py src/fcmd/executors.py src/fcmd/_lazy.py src/fcmd/__init__.py src/fcmd/cli/main.py src/fcmd/errors.py src/fcmd/py.typed
git add tests/test_task.py tests/test_context.py tests/test_graph.py tests/test_command.py tests/test_report.py tests/test_executors.py tests/test_init.py tests/test_fcmd.py tests/__init__.py
git add pyproject.toml README.md .gitignore
git add .trae/docs/iter-01-p0-core-framework.md
git commit -m "feat: 完成 P0 核心框架（task/graph/executors/command/context/report/console/懒加载 + 测试覆盖 95%）"
git push
```

**说明**：
- 按文件名 add，不用 `git add -A` / `git add .`
- commit 信息中文，含变更类型（feat）
- push 仅当分支已跟踪远程时执行；新分支跳过并在总结说明

## Assumptions & Decisions

### 假设
1. 用户已批准的 `fcmd-p0-implementation-plan.md` 中 P0 范围不变
2. rule-11 的 95% 覆盖率门槛适用于 P0（核心模块必测）
3. rule-02 自驱原则：初始确认后自主完成，普通 commit/push 自动执行
4. rich 是唯一运行时依赖，typing-extensions 仅 Python < 3.13
5. 当前 git 分支已跟踪远程（基于会话总结中"git push 自动执行"的约定）

### 决策
1. **测试文件组织**：按模块一对一（test_report ↔ report，test_executors ↔ executors），与 pyflowx 一致
2. **不依赖 pytest-asyncio**：`run()` 内部用 `asyncio.run`，测试同步调用即可
3. **跨平台命令**：所有 cmd 任务测试用 `sys.platform` 分支，避免 Windows/Linux 差异
4. **on_event 测试**：用列表收集事件而非 mock，符合 rule-11 的 "monkeypatch > 内联 stub > unittest.mock" 优先级
5. **coverage 修复**：补充测试而非放宽门槛，符合 rule-11
6. **不创建额外文档**：仅 iter-01 文档（rule-01 要求），不主动新建 README 章节或其他 .md

### 不在范围内（P1/P2 处理）
- CLI 框架：shell.py / conditions.py / compose.py / runner.py / apis/toolkit.py / cli/main.py 完整版 / cli/_common.py / cli/pymake.py
- 内置工具：tools/which.py / tools/fileops.py / tools/sysinfo.py
- 扩展功能：取消 / 通知 / 进度条 / 状态后端 / 诊断 / 性能剖面 / YAML 加载 / 动态任务 / 循环展开
- `run_iter` 流式执行（P0 保留接口抛 NotImplementedError，P1 实现）

## Verification Steps

1. **静态检查**：`uv run ruff check src tests` + `uv run ruff format --check src tests` + `uv run pyrefly check` 全部通过
2. **测试**：`uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95` 全部通过，覆盖率 ≥ 95%
3. **API 验证**：4 条编程式示例输出正确（版本号 / cmd 任务 / fn 自动依赖 / CLI）
4. **冷启动**：`uv run python -X importtime -c "import fcmd"` 顶层导入 < 100ms
5. **文档**：`.trae/docs/iter-01-p0-core-framework.md` 记录完整
6. **Git**：commit + push 成功（分支已跟踪远程时）

## 实施顺序（严格按此执行）

1. 创建 `tests/test_report.py`（10 个测试）
2. 创建 `tests/test_executors.py`（约 22 个测试）
3. 创建 `tests/test_init.py`（5 个测试）
4. 运行 `uv run ruff check src tests`，修复 lint 错误
5. 运行 `uv run ruff format --check src tests`，必要时 `uv run ruff format src tests`
6. 运行 `uv run pyrefly check`，修复类型错误
7. 运行 `uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95`，修复失败测试与覆盖率缺口
8. 运行 4 条 API 验证命令
9. 运行 `fcmd --version`
10. 运行冷启动性能检查
11. 创建 `.trae/docs/iter-01-p0-core-framework.md`
12. git add（按文件名）+ commit + push
13. 输出收尾总结
