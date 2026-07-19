# P38 - dockercmd / lscalc 工具迁移

## 需求清单

- [x] 迁移 pyflowx 的 `dockercmd` 工具：Docker 镜像仓库登录
- [x] 迁移 pyflowx 的 `lscalc` 工具：LS-DYNA 计算（单机/MPI/状态检查）
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage ≥ 95%）

## 迭代目标

完成 pyflowx → fcmd 迁移收尾：把 project_memory 中原标记为「主动 skip」的 dockercmd（腾讯云专用）、lscalc（过于专用）两个工具迁移到 fcmd CLI，使 req-01 中所有未完成项清零。迁移过程对原工具做小幅泛化（registry 抽成可选参数、失败路径明确打印），不引入新依赖、不为未来预留扩展点。

## 改动文件清单

| 文件 | 类型 | 行数变化 | 说明 |
|------|------|---------|------|
| `src/fcmd/cli/dockercmd.py` | 新增 | +44 | `dockercmd login` 子命令；`_DEFAULT_REGISTRY` 常量；`docker_login(username, registry)` 公共函数 |
| `src/fcmd/cli/lscalc.py` | 新增 | +93 | `lscalc run/mpi/status` 三子命令；`get_ls_dyna_command` 公共函数；`_DEFAULT_NCPU` 常量 |
| `tests/test_cli_tools_p22.py` | 新增 | +492 | 26 个测试：注册 3 + dockercmd 6 + lscalc 命令 11 + lscalc status 6（含 Windows/Linux 平台分支） |
| `.trae/req/done/req-01-功能需求.md` | 修改 | 1 行 | 第 3 项 `[]` → `[x]` |
| `c:/Users/zhou/.trae-cn/memory/projects/-f-Dev-fcmd/project_memory.md` | 修改 | 1 行 | 移除「skip lscalc/dockercmd」表述，新增迁移完成说明 |

## 关键决策与依据

### 1. dockercmd：把 registry 抽成可选参数
- **依据**：原 pyflowx 实现 hardcode `ccr.ccs.tencentyun.com`，project_memory 标记为「平台特定」即因此而来。用户明确要求迁移，若仍 hardcode 等于没解决「平台特定」问题。
- **实现**：`docker_login(username="", registry=_DEFAULT_REGISTRY)`，`_DEFAULT_REGISTRY` 保留腾讯云默认值，向后兼容原行为；用户传 `--registry xxx` 即可登录任意仓库。
- **范围**：仅泛化 registry，未引入子命令（pull/push/logout 等），避免「为未来预留扩展点」（rule-01）。

### 2. lscalc：忠实迁移三个子命令
- **依据**：原 pyflowx 三子命令（run/mpi/status）职责清晰，无冗余，直接 1:1 迁移。
- **实现**：
  - `run_ls_dyna_single(input_file, ncpu=4)` → 单机 LS-DYNA
  - `run_ls_dyna_mpi(input_file, ncpu=4)` → MPI 并行（`mpirun -np N`）
  - `check_ls_dyna_status()` → 跨平台进程检查
- **平台分支**：`sys.platform == "win32"` → `tasklist /fi`，其他平台 → `pgrep -f ls-dyna`。pgrep 返回 1 视为「无匹配进程」（非错误），区分 stdout 空与 returncode。

### 3. lscalc：用 `fcmd.models.run_command` 替代 `px.sh()`
- **依据**：project_memory 已记录的迁移映射 `px.sh() → run_command()`。`run_command` 默认 `capture=False`（透传到终端），与原 `px.sh(capture=False)` 行为一致；`capture=True` 用于 status 命令需要解析 stdout 的场景。
- **失败处理**：原 `px.sh()` 返回 None 表示失败；`run_command` 返回 `CommandResult`，用 `result.failed`/`result.succeeded` 属性判断，更类型安全。

### 4. lscalc status：Windows 路径行为微调
- **原版**：tasklist 命令成功时无条件 print stdout（即使 stdout 是「INFO: No tasks are running...」提示）。
- **本次**：保持原版行为（成功就 print），避免引入「检测 ls-dyna_mpp.exe 是否在 stdout」的额外逻辑——这是原版未提供的行为，避免「未被要求的功能」（rule-01）。
- **Linux 路径**：原版 `pgrep` 失败（returncode=1）时 print「没有运行中的 LS-DYNA 进程」；本次保持一致，额外处理 stdout 为空也视为无进程的边界（returncode=0 + 空 stdout）。

### 5. 测试：stub 函数避免 ARG005
- **问题**：ruff `ARG005` 报「未使用的 lambda 参数」——`tests/**` 仅豁免 `ARG001`/`ARG002`，不豁免 `ARG005`。lambda 中 `capture`/`check` 未使用即触发。
- **解决**：在测试文件顶部定义 5 个 stub 工厂函数（`_stub_success`/`_stub_failure`/`_stub_failure_with_stderr`/`_stub_success_with_stdout`/`_stub_returncode_with_stdout`），函数签名用 `**_kwargs: object` 接受任意关键字参数避免 ARG005，同时与 `run_command(cmd, capture=True, check=False)` 的调用约定兼容。
- **依据**：参考 test_cli_tools_p17.py:295 用 `def fake_run` 函数定义避免 ARG005 的既有模式。

## 代码实现情况

### dockercmd.py
- 44 行（含 docstring 与空行）
- `docker_login(username, registry)` 公共函数 + `@fcmd.tool` 装饰器
- `getpass.getuser()` 提供默认用户名（与原版一致）
- `run_command` 失败时打印「登录失败」并 return，成功时打印「已登录镜像仓库」

### lscalc.py
- 93 行（含 docstring 与空行）
- `get_ls_dyna_command(input_file, ncpu)` 公共函数 + 3 个 `@fcmd.tool` 子命令
- `_DEFAULT_NCPU = 4` 常量
- `check_ls_dyna_status` 跨平台分支：Windows tasklist / POSIX pgrep

### 测试覆盖
- dockercmd.py：100%（14 stmts，2 branches）
- lscalc.py：100%（44 stmts，14 branches）
- 全项目覆盖率：99.39%（≥95% 要求）

## 整合优化情况

- dockercmd 与 lscalc 都使用 `fcmd.models.run_command`（与 screenshot/reseticoncache/envdev/sshcopyid 一致），未引入新的命令执行抽象。
- stub 函数 `_stub_*` 共 5 个，集中定义在测试文件顶部，复用于 8 处 monkeypatch 调用，避免重复 lambda。
- 无重复代码，无新风险引入。

## 测试验证结果

```
uv run ruff check src tests           # All checks passed!
uv run ruff format --check src tests  # 89 files already formatted
uv run pyrefly check                  # 0 errors (35 suppressed, 10 warnings not shown)
uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95
# 1468 passed, 1 skipped, 2 deselected
# Total coverage: 99.39%
# dockercmd.py: 100% / lscalc.py: 100%
```

26 个新测试覆盖：
- 工具注册与子命令数（3）
- dockercmd：默认 registry / 自定义 username / 自定义 registry / 失败路径 / run_tool 调用（6）
- lscalc 命令构造与子命令：命令构造 / 输入文件不存在 / 成功路径 / 自定义 ncpu / 失败路径 / run_tool 调用（11）
- lscalc status：Windows 有进程 / Windows tasklist 失败 / POSIX 有进程 / POSIX 无进程 / POSIX 空 stdout / run_tool 调用（6）

## 遗留事项

- 无。req-01 全部三项已 `[x]`，pyflowx → fcmd CLI 工具层迁移收尾完成。

## 下一轮计划

- pyflowx 仍有未迁移的核心 API 层模块（cancellation/compose/diagnostics/fileops/history/imaging/monitoring/notification/pipelines/progress/runner/shell/storage/task 高级特性/pypack 子包）与少量 CLI 工具（msdownload/sglang/legacy/emlmanager）。
- 是否继续迁移核心 API 层与 pypack 子包需用户确认范围（涉及显著扩大范围，触发 rule-01 暂停条件第 4 条）。
