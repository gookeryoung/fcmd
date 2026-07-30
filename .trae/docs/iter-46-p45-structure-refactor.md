# iter-46 P45 项目结构重构

## 需求清单

- [x] 合理拆分高复杂度函数（run_tool 17 / run_command 16 / _add_optional_arg 11）
- [x] 修复 RET501/RET505 风格问题（_noop/_task_noop/archivex）
- [x] 评估 ERA001 注释死代码（全为误报，跳过）

## 迭代目标

R1：推进项目结构重构——拆分 framework 核心模块的高复杂度函数，清理无效代码与风格问题。

## 改动文件清单

- src/fcmd/apis/toolkit.py：run_tool 拆为路由/解析/执行三子函数；_add_optional_arg 提取 _resolve_list_inner_type；_noop 删多余 return None
- src/fcmd/command.py：run_command 拆为 _run_callable_command/_run_subprocess_command/_handle_subprocess_result 三子函数；修正 cwd 类型 str→Path
- src/fcmd/cli/archivex.py：_detect_format 的 elif 链改 if（RET505）
- src/fcmd/task.py：_task_noop 删多余 return None（RET501）

## 关键决策与依据

1. **ERA001 全为误报**：13 处注释代码检测均为分节注释（`# ===`）、算法说明（伪代码 `# earliest_finish[...]`）、解释性注释（`# not failure() → True`），非死代码。删除会损害可读性，跳过。
2. **run_tool 拆分（复杂度 17→3）**：99 行函数拆为三阶段编排——`_resolve_tool_target`（路由）、`_parse_tool_args`（解析）、`_execute_tool_tasks`（执行）。子函数返回 `tuple | int`，调用方用 `isinstance(result, int)` 判定早退。去掉了原 `# noqa: PLR0911, PLR0912`。
3. **run_command 拆分（复杂度 16→2）**：82 行函数拆为 `_run_callable_command`（callable 分支）、`_run_subprocess_command`（list/str 分支）、`_handle_subprocess_result`（结果处理）。去掉了原 `# noqa: PLR0912`。修正 `cwd` 类型 `str|None`→`Path|None` 匹配 TaskSpec.cwd（pyrefly 类型错误）。
4. **_add_optional_arg 拆分（复杂度 11→7）**：提取 `_resolve_list_inner_type(inner)` 子函数，将 list 内部类型映射（Path/int/float/str 四分支）从内联 if/elif 改为独立函数。
5. **executors.execute(14)/context.build_call_args(12) 保留**：C901 未在项目 ruff 默认配置启用（手动 `--select C901` 才报）。execute 是 async 调度主循环含嵌套闭包引用外层局部变量，强行拆分破坏闭包上下文。两者逻辑清晰内聚，保留。
6. **RET501 误报处理**：_run_subprocess_result 的 `return None` 在 returncode==0 分支是必须的（否则 fall through 到 raise），ruff 误报。提取 `_handle_subprocess_result` 子函数后 early return 自然消除误报。

## 代码实现情况

- run_tool：主体 ~20 行三步编排，3 个子函数各复杂度 <10
- run_command：主体 3 行 dispatch，3 个子函数职责单一
- _add_optional_arg：list 分支从 4 个 if/elif 简化为调用 _resolve_list_inner_type
- archivex._detect_format：elif 链全改 if（每个分支都 return）
- _noop/_task_noop：删 return None（RET501，唯一返回值隐式 None）

## 测试验证结果

- 针对性运行（command/cli/toolkit/executors/context）：385 passed
- `make check` 全套通过：lint（ruff check + format All passed）+ typecheck（pyrefly 0 errors, 42 suppressed）+ 2281 passed, 3 skipped, 2 deselected
- 总覆盖率 99.06% → **99.07%**
- command.py / toolkit.py 均达 100% 覆盖（重构后子函数全部被现有测试覆盖）
- Stmts 6500 → 6519（提取子函数增加少量语句，主体简化）

## 遗留事项

- C901 复杂度检查未在项目 ruff 默认配置启用，context.build_call_args(12)/filesearch.search_by_content(11)/yaml_loader._parse_defaults(11)/_parse_optional_fields(11) 略超阈值但逻辑清晰内聚，保留不拆
- main.py FcmdApp 类 1035 行（974 行文件），属较大重构，本轮未涉及（需评估是否值得拆类）

## 下一轮计划

无明确下一轮方向。函数级重构已完成高价值目标，模块级结构（main.py 拆类）需更大范围评估。
