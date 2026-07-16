# P12: models 包建立与共性下沉重构

## 需求清单

- [x] 建立 `models` 包，抽离 CLI 工具共性数据结构与行为
- [x] 功能尽可能下沉到 models 层，参考 python-class-design SKILL
- [x] 重构现有工具使用 models 包，消除重复代码
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage）

## 迭代目标

1. 建立 `src/fcmd/models/` 包，提供 `CommandResult`/`run_command`、`IgnoreSpec`/`should_ignore`/`to_shutil_ignore`、`Version`/`BumpPart`/`parse_version` 三组领域模型
2. 重构 piptool/bumpversion/autofmt/packtool/gittool 使用 models，移除各工具中重复的 `_run` 函数与版本号操作
3. 更新现有测试适配重构（mock 目标从 `_run` 改为 `run_command`，返回 `CommandResult`）

## 改动文件清单

### 新增
- `src/fcmd/models/__init__.py` — 包入口，导出全部公共 API
- `src/fcmd/models/command.py` — `CommandResult` frozen dataclass + `run_command` 统一封装
- `src/fcmd/models/filefilter.py` — `IgnoreSpec` frozen dataclass + `should_ignore`/`to_shutil_ignore`
- `src/fcmd/models/version.py` — `Version` frozen dataclass + `BumpPart` Enum + `parse_version`
- `tests/test_models.py` — 46 测试覆盖 models 全部公共 API

### 重构
- `src/fcmd/cli/bumpversion.py` — 移除 `_run`/`_calculate_new_version`/`_read_version_tuple`/`_VALID_PARTS`/`_IGNORE_DIRS`/`import subprocess`；改用 `run_command`/`Version.bump(BumpPart)`/`parse_version`/`IgnoreSpec`/`should_ignore`；`_read_version_tuple` → `_read_version`（返回 `Version`）
- `src/fcmd/cli/autofmt.py` — 移除 `_run`/`import subprocess`；改用 `run_command`
- `src/fcmd/cli/packtool.py` — 移除 `_run`/`import subprocess`/`_IGNORE_PATTERNS`；改用 `run_command`/`IgnoreSpec`/`should_ignore`/`to_shutil_ignore`
- `src/fcmd/cli/piptool.py` — 移除 `_run`/`import subprocess`；改用 `run_command`
- `src/fcmd/cli/gittool.py` — 移除 `_run`/`import subprocess`；改用 `run_command`

### 测试适配
- `tests/test_cli_tools_p9.py` — mock 目标 `_run` → `run_command`；辅助函数返回 `CommandResult`；`CompletedProcess` 构造改为 `CommandResult`
- `tests/test_cli_tools_p10.py` — mock 目标 `_run` → `run_command`；移除 `_calculate_new_version` 测试（已由 test_models.py 覆盖 `Version.bump`）；`TestReadVersionTuple` → `TestReadVersion`（断言 `Version` 对象）；移除 2 个 `test_run_calls_subprocess`（bumpversion/autofmt 已无 `_run`/`subprocess`）
- `tests/test_cli_tools_p11.py` — mock 目标 `_run` → `run_command`；辅助函数返回 `CommandResult`；移除 1 个 `test_run_calls_subprocess`（packtool 已无 `_run`/`subprocess`）

## 关键决策与依据

1. **`CommandResult` frozen dataclass**：不可变值对象，替代 `subprocess.CompletedProcess[str]`，提供 `succeeded`/`failed` property。`run_command(cmd, *, capture=False, check=False)` 统一签名，向后兼容 5 个 `_run` 变体（有的 capture，有的不 capture）

2. **`IgnoreSpec` auto-classification**：`from_iterable` 用 `_GLOB_CHARS = frozenset("*?[")` 自动区分目录名与 glob 模式，实现统一的 `should_ignore`。`to_shutil_ignore` 转换为 `shutil.ignore_patterns` 参数。Python 3.8 无 `shutil.IgnoreType`，用 `Callable[[Any, list[str]], set[str]]` 替代

3. **`Version` frozen dataclass + `BumpPart` Enum**：PEP 440 兼容版本号模型，`bump(BumpPart)` 返回新实例，`__str__` 序列化。替代 bumpversion 中分散的 `_calculate_new_version`/`_read_version_tuple`/`_VALID_PARTS`

4. **`max(versions, key=lambda v: (v.major, v.minor, v.patch))`**：`Version` dataclass 默认比较所有字段（含 prerelease/buildmetadata），与原 `max(tuple[int,int,int])` 语义不一致。显式指定 key 保持原行为

5. **packtool 行为改进**：原 `item.name in _IGNORE_PATTERNS` 只做精确匹配（glob 模式如 `*.pyc` 永远不匹配文件名）。重构后 `should_ignore(Path(item.name), IGNORE_SPEC)` 正确使用 fnmatch 匹配 patterns — 这是重构过程中的行为修正

6. **bumpversion `BumpPart(part)` 校验**：原 `_VALID_PARTS` frozenset 校验改为 `BumpPart(part)` try/except ValueError，更符合 Enum 用法

7. **移除 `test_run_calls_subprocess` × 3**：bumpversion/autofmt/packtool 重构后已无 `_run`/`subprocess`，这些测试无法适配。`run_command` 的 subprocess 调用已由 test_models.py 100% 覆盖

8. **移除 `_calculate_new_version` 测试 × 4**：`Version.bump` 已由 test_models.py 覆盖（patch/minor/major/zero），避免重复

## 代码实现情况

- models 包 3 个模块 + `__init__.py`，共 137 行源码，100% 覆盖
- 5 个工具重构完成，移除约 70 行重复代码（5 个 `_run` + `_calculate_new_version` + `_read_version_tuple` + `_VALID_PARTS` + `_IGNORE_DIRS`/`_IGNORE_PATTERNS`）
- 测试适配完成，745 测试全通过（+39 from P11's 706）

## 整合优化情况

- 消除 5 个重复的 `_run` 函数，统一为 `models.run_command`
- 消除 bumpversion 中分散的版本号操作，下沉到 `models.Version`/`BumpPart`/`parse_version`
- 消除 packtool/bumpversion 中分散的忽略规则，下沉到 `models.IgnoreSpec`/`should_ignore`
- 参考 python-class-design SKILL：frozen=True 值对象、Enum 状态值、classmethod 工厂（`IgnoreSpec.from_iterable`）、property 计算属性（`succeeded`/`failed`）

## 测试验证结果

- ruff check: All checks passed
- ruff format --check: 57 files already formatted
- pyrefly check: 0 errors (13 suppressed, 6 warnings)
- pytest: 745 passed in 21.73s
- coverage: 97.80%（TOTAL 显示 98%），models/*.py 全部 100%，bumpversion/autofmt/packtool/piptool/gittool 全部 100%

## 遗留事项

- 覆盖率 97.80% 略低于 P11 的 98%（四舍五入后均显示 98%），主要因框架代码（executors.py/dag.py）的未覆盖分支占比变化，P12 改动文件均为 100%
- `_common.py` 的 `IGNORE_DIRS`/`IGNORE_EXT` 未统一到 `IgnoreSpec`（仅 folderback/filedate 等使用，三处以下不提取 per rule-01）
- 后续可考虑补充 `print_verbose`（console.py）测试进一步提升覆盖率

## 下一轮计划

- 继续移植 CLI 工具（envdev/imagetool/pdftool/screenshot/dockercmd 等）
- 实现 conditions 模块（启用 gittool isub 和 YAML matrix/if）
- 增强 fcmd 参数解析对 Literal/int/union 类型的支持
