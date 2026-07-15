# 迭代 03：P2 pymake 子命令集扩展

## 迭代目标

在 P1 pymake 6 子命令示例基础上，参考 `ref/pyflowx` 扩展为完整的构建/测试/清理/检查/格式化/发布工具集：
1. 单任务别名：b/sync/c/t/tf/lint/fmt/fmtc/bumpmi/bumpma/doc/tox（12 个）
2. 内部 hidden job：pyrefly_check/test_coverage/bumpversion/git_add_all/git_push/git_push_tags/twine_publish（7 个）
3. 聚合 job：tc/cov/bump/p/pb/all（6 个），通过 `needs` + `strategy` 表达 DAG 依赖
4. 适配 fcmd 工具链：uv build / uv sync / pytest / ruff / pyrefly / bump-my-version / twine / sphinx / tox

## 改动文件清单

### 修改
- `src/fcmd/cli/pymake.py`：从 6 子命令重写为 18 可见 + 7 hidden + 6 聚合
  - `c` 改为 fn 任务（无 cmd，函数体用 `shutil.rmtree` 清理构建产物与缓存目录）
  - 新增 sync/tf/lint/fmt/fmtc/bumpmi/bumpma/doc/tox 单任务
  - 新增 7 个 hidden job（pyrefly_check/test_coverage/bumpversion/git_add_all/git_push/git_push_tags/twine_publish）
  - 新增 6 个聚合 job（tc/cov/bump/p/pb/all），tc 与 p 用 thread 策略，all 用 dependency 策略
  - hidden 链：`bumpversion` → `git_add_all` → `tc` → (c, pyrefly_check, lint)
  - `shutil` 导入移至模块顶部（规则：惰性导入仅用于打破循环依赖）
- `tests/test_cli.py`：新增 6 个 `_tool_description` / `_run_tool` 防御路径测试
  - `pymake b` 测试改用 `--dry-run` 避免真实 uv build
  - 覆盖 `_tool_description` 的 ImportError / unknown / description / no-help 分支
  - 覆盖 `_run_tool` 的 module_path None / ImportError 分支
- `src/fcmd/__init__.py`：`__all__` 追加 `"__version__"`（修复预存在 test_package_importable 失败）
- `tests/test_executors.py`：`import fcmd as fcmd` → `import fcmd`（修复预存在 PLC0414）
- `tests/test_toolkit.py`：`import fcmd as fcmd` → `import fcmd`（同上）

### 新建
- `tests/test_pymake_tool.py`：58 个测试，7 个测试类
  - TestPymakeRegistration（4）：注册与可见性
  - TestPymakeCmdTasks（14）：cmd 内容片段验证
  - TestPymakeHiddenJobs（9）：hidden job cmd 与 needs
  - TestPymakeAggregateJobs（15）：聚合 needs 与 strategy
  - TestPymakeCliDispatch（10）：dry-run 执行计划
  - TestPymakeCleanFn（4）：c 函数实际执行（tmp_path + monkeypatch.chdir）
  - TestPymakeMain（2）：main() 入口

## 关键决策与依据

1. **`c` 改为 fn 任务**：原 pyflowx 的 clean 用 cmd 调外部脚本，fcmd 改为直接用 `shutil.rmtree` 在函数体内清理，避免依赖外部 clean 脚本。`cwd` 参数保留在签名中以驱动 CLI `--cwd` 选项，框架通过 `spec.env_context()` 处理 chdir，函数体不需引用 cwd（`# noqa: ARG001`）。

2. **hidden 链表达发布流程**：`bump` → `bumpversion` → `git_add_all` → `tc` → (c, pyrefly_check, lint)。先通过类型检查（tc）再 add 再 bump，确保提交前代码质量。聚合任务 `bump` 仅引用 `bumpversion`，依赖链自动展开。

3. **`tc` 与 `p` 用 thread 策略**：tc 的三个依赖（c/pyrefly_check/lint）相互独立，可并行；p 的 push 与 push tags 相互独立，可并行。`all` 用 dependency 策略最大化并行度（c/b/t/tc 中 c 与 b 可并行，t 依赖 c，tc 依赖 c）。

4. **`test_coverage` 依赖 `c`**：覆盖率测试前先清理，避免旧 .pytest_cache/htmlcov 干扰结果。

5. **`assert spec.cmd is not None` 类型收窄**：pyrefly strict 模式下 `spec.cmd` 为 `tuple[str, ...] | str | None`，`in spec.cmd` 操作需先断言非 None。8 个测试在 `in spec.cmd` 前加 `assert spec.cmd is not None`。

6. **覆盖率回归修复**：新增 pymake.py 后总覆盖率从 P1 的 96.68% 降至 95.33%（pymake.py 仅 77%，c 函数体与 main() 未覆盖）。通过新增 TestPymakeCleanFn（4 测试）+ TestPymakeMain（2 测试）将 pymake.py 提升至 100%；再补充 cli/main.py 防御路径测试（6 个）将总覆盖率提升至 96.80%，超过 P1 基线。

7. **预存在问题修复**：
   - `__version__` 未在 `fcmd.__all__` 中（test_package_importable 失败）
   - `import fcmd as fcmd` 触发 PLC0414（test_executors.py / test_toolkit.py）

## 验证结果

### 全套门禁
- `ruff check src tests`：All checks passed
- `ruff format --check src tests`：28 files already formatted
- `pyrefly check`：0 errors (3 suppressed, 1 warning not shown)
- `pytest -m "not slow" --cov=fcmd --cov-fail-under=95`：376 passed, coverage 96.80%

### 覆盖率
- 总覆盖率：96.80%（P1: 96.68% ↑）
- `cli/pymake.py`：100%（66 stmts, 0 miss, 8 branch, 0 BrPart）
- `cli/main.py`：98%（92 stmts, 1 miss, 26 branch, 1 BrPart）—— 仅 127 行（tool_name 不在 _TOOL_REGISTRY 的防御分支）未覆盖

### CLI 验证（dry-run）
- `fcmd pymake t --dry-run` → 打印单任务执行计划
- `fcmd pymake tc --dry-run` → 打印 2 层 DAG（Layer 1: c/lint/pyrefly_check 并行, Layer 2: tc）
- `fcmd pymake all --dry-run` → 打印 3 层 DAG（c/b → t → tc）
- `fcmd pymake bump --dry-run` → 打印 bumpversion → git_add_all → tc → (c, pyrefly_check, lint) 链
- `fcmd pm t --dry-run` → pm 别名路由正常

### 子命令清单（18 可见 + 7 hidden + 6 聚合 = 31 个 @fx.tool）
- 可见：b/sync/c/t/tf/lint/fmt/fmtc/bumpmi/bumpma/doc/tox/tc/cov/bump/p/pb/all
- hidden：pyrefly_check/test_coverage/bumpversion/git_add_all/git_push/git_push_tags/twine_publish
- 聚合（无 cmd 有 needs）：tc/cov/bump/p/pb/all

## 遗留事项（P3）

- YAML 配置加载（`yamlrun` 命令）
- 内建命令 `graph`/`info`/`completion`
- `pf` 风格的工具自动发现机制（替代硬编码 `_TOOL_MODULES`）
- 内置工具集（gittool/hashfile/...）放 `fcmd/tools/`
- `verify_api.py` 的 pyrefly 懒加载误报需更优雅的解决方案
- Windows 控制台 UnicodeEncodeError（`▸` 字符在 GBK 编码下报错，预存在问题，不影响测试）

## 下一轮计划

P3 候选方向（待用户确认优先级）：
1. 工具自动发现机制（扫描 `fcmd/cli/` 下所有模块自动注册，替代硬编码 _TOOL_MODULES）
2. YAML 配置加载（`fcmd yamlrun` 命令，从 YAML 定义任务 DAG）
3. 内建命令（`fcmd graph` 可视化 DAG，`fcmd info` 显示任务详情）
4. 更多内置工具（gittool/hashfile/...）
