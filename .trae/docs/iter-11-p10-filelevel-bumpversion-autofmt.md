# P10 迭代记录：filelevel / bumpversion / autofmt

## 需求清单

- [x] 移植 filelevel 工具（文件等级重命名，单子命令 set）
- [x] 移植 bumpversion 工具（版本号管理，单命令，两阶段策略）
- [x] 移植 autofmt 工具精简版（fmt/lint 子命令，移除 doc/sync）
- [x] 新增 3 个工具的测试
- [x] 全套门禁 + 迭代记录 + 提交

## 迭代目标

继续扩展 fcmd CLI 工具生态，从 `ref/pyflowx` 移植 3 个轻量纯 Python 工具，
覆盖文件重命名、版本号管理、代码格式化三个场景。

## 改动文件清单

### 新增

- `src/fcmd/cli/filelevel.py` - 文件等级重命名工具（单子命令 set）
- `src/fcmd/cli/bumpversion.py` - 版本号自动管理工具（单命令，两阶段策略）
- `src/fcmd/cli/autofmt.py` - 代码格式化与检查工具（fmt/lint 子命令）
- `tests/test_cli_tools_p10.py` - 71 个测试用例
- `.trae/docs/iter-11-p10-filelevel-bumpversion-autofmt.md` - 本迭代记录

### 删除

- `.trae/docs/iter-06-p5-info-builtin.md` - 清理最旧迭代记录（保留最新 5 条）

## 关键决策与依据

### 1. autofmt 精简版（移除 doc/sync 子命令）

**决策**：仅保留 fmt/lint 子命令，移除 pyflowx 原版的 doc/sync 子命令。

**依据**：
- `doc` 子命令自动添加英文 docstring（如 `"""Command-line interface for xxx."""`），
  违反 rule-11 "公共 API 必须有中文 docstring" 约束。
- `sync` 子命令用于多项目 pyproject.toml 同步，场景少见且与 fcmd 单项目定位不符。
- rule-01 "不写未被要求的功能"，精简版本聚焦核心格式化能力。

### 2. bumpversion 保留 Literal 类型注解

**决策**：`part: BumpVersionType = "patch"` 使用 `Literal["patch", "minor", "major"]`，
但 fcmd 的 argparse 参数解析对字符串注解的 `int`/`Literal` 类型不支持 choice 验证，
在函数内部额外验证 `part` 值。

**依据**：
- Literal 类型注解更精确表达取值范围，便于静态检查与文档。
- fcmd 的 `_add_optional_arg` 不处理 Literal（未匹配 `int/float/str/Path/list`），
  实际按 str 解析，用户传入的任意字符串需在函数内验证。
- 不修改 fcmd 框架的参数解析逻辑（超出当前任务范围）。

### 3. filelevel 内部 int 转换

**决策**：`process_file_level` 开头添加 `level = int(level)`。

**依据**：fcmd 对 `from __future__ import annotations` 的 `int` 类型注解
解析不稳定（`get_type_hints` 可能失败，回退到字符串注解 "int" 不被识别），
argparse 默认按 str 解析。内部转换保证函数正确性。
其他工具（如 filedate 的 `clear: bool`）因 fcmd 对 bool 有 `isinstance(default, bool)`
特殊处理而不受影响。

### 4. bumpversion 用 subprocess.run 替代 px.sh

**决策**：`_run` 使用 `subprocess.run(cmd, check=False, capture_output=True, text=True)`。

**依据**：fcmd 无 pyflowx 的 `px.sh()` 等价物，直接用 subprocess.run。
`capture_output=True` 避免 git 命令输出污染终端。
`check=False` 容忍 git 命令失败（如无 tag 权限时）。

### 5. bumpversion 保留 git commit/tag 行为

**决策**：bumpversion 工具调用 git add + commit + tag，不违反 rule-01 暂停条件。

**依据**：rule-01 的"任务完成后自动提交"是框架行为；bumpversion 是用户主动调用的
工具，用户明确请求 bump 版本并提交，这是工具核心功能。bumpversion 修改
pyproject.toml 的 version 字段属于业务数据，不属于工具链配置文件修改。

## 代码实现情况

### filelevel.py

- `LEVELS` 字典：0=清除, 1=PUB,NOR, 2=INT, 3=CON, 4=CLA
- `BRACKETS`：左右括号字符集（支持 `()[]_【】` 等多种括号）
- `remove_marks(stem, marks)`：仅移除被括号包裹的标记
- `process_file_level(filepath, level)`：清除所有标记 + 数字，再添加新等级
- `process_files_level(files, level)`：CLI 入口（`@fcmd.tool("filelevel", subcommand="set")`）

### bumpversion.py

- `BumpVersionType = Literal["patch", "minor", "major"]`
- 两个正则模式：`_PYPROJECT_VERSION_PATTERN` / `_INIT_VERSION_PATTERN`（PEP 440 兼容）
- `_IGNORE_DIRS`：排除 .venv/.git/__pycache__ 等
- 两阶段策略：先读取所有文件版本号取最大值，再统一写入新版本号
- `bump_project_version(part, no_tag)`：CLI 入口（单命令）
- 内部验证 `part` 值（fcmd 不支持 Literal 转 choice）

### autofmt.py

- `fmt(target=".")`：调用 `ruff format <target>`
- `lint(target=".", fix=False)`：调用 `ruff check <target>`，`fix=True` 添加 `--fix --unsafe-fixes`
- `_run(cmd)`：`subprocess.run(cmd, check=False, text=True)`（输出透传到终端）

## 整合优化情况

- 3 个工具均遵循 fcmd 既有风格（模块 docstring、`__all__`、中文注释）
- `_run` 辅助函数在各工具内独立定义（签名不同，不强行抽取）
- 测试用命名 helper 函数（`_recording_run`/`_success_run`）避免 lambda ARG005

## 测试验证结果

- 71 个测试用例全部通过
- 全套门禁通过：
  - `ruff check`：All checks passed!
  - `ruff format --check`：49 files already formatted
  - `pyrefly check`：0 errors
  - `pytest`：680 passed（P9: 609 → P10: 680，+71）
  - 覆盖率：97.64%（P9: 97.50% → P10: 97.64%，+0.14%）
- 各工具覆盖率：
  - filelevel.py: 99%
  - bumpversion.py: 98%（补充异常分支测试后从 93% 提升）
  - autofmt.py: 100%（补充 _run 调用测试后从 95% 提升）

## 遗留事项

- fcmd 框架的 `_add_optional_arg` 对 `from __future__ import annotations` 的
  `int`/`Literal` 类型注解支持不完善，未来可考虑增强（当前在各工具内自行转换/验证）。
- bumpversion 未支持 `.bumpversion.toml` 配置文件（pyflowx 原版也未支持）。
- autofmt 未支持 ruff 之外的格式化工具（如 black/isort）。

## 下一轮计划

P10 完成 3 个工具移植。后续可选方向：
- 继续移植更多 CLI 工具（envdev/imagetool/packtool/pdftool/screenshot/sshcopyid 等）
- 实现 conditions 模块（启用 gittool isub 和 YAML matrix/if 支持）
- 增强 fcmd 参数解析对 Literal/int 字符串注解的支持
- YAML schema 扩展（matrix/if 条件）
- 动态 completion（运行时查询 vs 静态嵌入）
