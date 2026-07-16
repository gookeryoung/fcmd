# P18：错误信息完善与执行汇总表

## 需求清单

- [x] 错误信息中显示执行出问题时的命令（FileNotFoundError 等异常包含命令名）
- [x] 多任务场景显示执行过程及各任务用时汇总表
- [x] 修复 clr 工具在 Windows 上 cls 命令无法执行的 bug（需 shell=True）
- [x] 全套门禁通过（ruff/pyrefly/pytest --cov）
- [x] 总覆盖率不低于 P17（98.86%）

## 迭代目标

完善 CLI 工具的错误提示与执行过程展示：失败时错误信息包含具体执行的命令名，便于定位问题；多任务执行结束后打印汇总表，展示各任务状态/耗时/重试次数及合计，便于优化。

## 改动文件清单

- `src/fcmd/cli/clr.py`：修改，修复 Windows 上 cls 需要 shell=True 的 bug；clear_screen 捕获 FileNotFoundError 并包装为含命令名的 RuntimeError；新增 `_clear_cmd()` 辅助函数（运行时求值，便于跨平台测试）
- `src/fcmd/apis/toolkit.py`：修改，新增 `_print_task_summary()` 函数打印任务汇总表；run_tool 成功/失败后调用汇总表（多任务时打印，失败时 force=True 强制打印）
- `tests/test_toolkit.py`：修改，新增 7 个测试覆盖 _print_task_summary（单任务/多任务/跳过/成功/dry-run/单任务不打印/失败场景）
- `tests/test_cli_tools_p11.py`：修改，调整 TestClr 4 个测试适配 shell 参数与字符串命令；新增 test_clear_screen_not_found 验证命令未找到时抛 RuntimeError

## 关键决策与依据

### clr 工具修复方案选择

**方案 A（未采用）**：将 clr 改为 cmd 任务（`cmd="cls"`），由框架 `run_command` 统一执行。
- 优点：错误信息自动包含命令名
- 缺点：`cmd` 参数在模块加载时求值，`_CLEAR_CMD` 常量无法随 `monkeypatch.setattr(sys, "platform", ...)` 变化，跨平台测试困难

**方案 B（采用）**：保持 fn 任务，在 `clear_screen` 内捕获 `FileNotFoundError` 并包装为 `RuntimeError(f"清屏命令未找到: {cmd}")`。
- 优点：`_clear_cmd()` 运行时求值，跨平台测试友好；错误信息包含命令名
- 缺点：fn 任务的错误包装需手动实现

选择方案 B，因为可测试性更重要。

### 汇总表触发条件

- 成功场景：`len(report.results) > 1` 才打印（单任务避免冗余）
- 失败场景：`force=True`，只要有结果就打印（失败诊断需要完整信息）
- dry-run 场景：不打印（未实际执行，无耗时数据）

### 汇总表设计

| 列 | 说明 |
|----|------|
| 任务 | 任务名 |
| 状态 | 成功（绿）/失败（红）/跳过（黄） |
| 耗时 | 秒，3 位小数；未执行显示 `-` |
| 重试 | 重试次数；>1 才显示，否则 `-` |
| 合计 | 所有任务耗时总和 |

## 代码实现情况

### clr.py

- `_clear_cmd()`：运行时根据 `sys.platform` 返回 `"cls"` 或 `"clear"`
- `clear_screen()`：`shell=sys.platform == "win32"`（Windows 内置命令需 shell=True）；捕获 `FileNotFoundError` 包装为 `RuntimeError`
- `clear_screen_run`：保持 fn 任务，调用 `clear_screen()`

### toolkit.py `_print_task_summary`

- 使用 rich.Table 渲染
- `force` 参数控制单任务是否打印
- 空结果直接返回

## 整合优化情况

- 失败场景原来逐行打印 `fname: status error=...`，改为统一调用 `_print_task_summary`，输出更规整
- 汇总表复用 rich.Table，与 `_print_subcommands` 风格一致

## 测试验证结果

- `tests/test_toolkit.py`：124 passed（含 7 个新增）
- `tests/test_cli_tools_p11.py`：26 passed（含 1 个新增）
- 全套：917 passed, 2 deselected
- 覆盖率：98.86%
- ruff check/format：通过
- pyrefly：0 errors

## 遗留事项

- `command.py` 第 43、84-87 行（callable cwd 分支、TimeoutExpired/OSError 分支）覆盖率未达 100%，属历史遗留，本次未处理
- test_toolkit.py 与 test_pymake_tool.py 合并运行时存在全局状态泄漏（_TOOL_REGISTRY），属历史遗留

## 下一轮计划

无明确下一轮计划，待用户进一步指示。
