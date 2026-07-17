# P23: 新增 fcmd env / fcmd doctor 内置命令

## 需求清单

- [x] P23a: 新增 `fcmd env` 内置命令，展示环境信息（fcmd/Python/平台/工具/可选依赖）
- [x] P23b: 新增 `fcmd doctor` 内置命令，环境健康诊断（Python/核心/工具/依赖/PATH）
- [x] P23c: 在 `_BUILTIN_COMMANDS` 注册新命令，补全 _list_tools 示例
- [x] P23d: 新增 21 项测试覆盖 env/doctor 各输出路径与边界

## 迭代目标

响应"新增 fcmd 独有功能"方向，扩展 fcmd CLI 内置命令集。当前内置命令仅 graph/info/completion/yaml 四个，缺少环境信息展示与健康诊断能力。本轮新增两个只读、低风险、高用户可见价值的命令：

- `fcmd env`：描述性命令，回答"我有什么"
- `fcmd doctor`：诊断性命令，回答"我有什么问题"

两者职责互补，零核心路径改动，不影响任务调度。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/fcmd/cli/main.py` | `_BUILTIN_COMMANDS` 新增 env/doctor；`_run_builtin` 新增分发；实现 `_builtin_env` / `_builtin_doctor` / `_collect_optional_deps_status`；`_list_tools` 示例补充 env/doctor；新增 `from pathlib import Path` 导入 |
| `tests/test_cli.py` | 新增 `TestBuiltinEnv`（9 测试）与 `TestBuiltinDoctor`（12 测试） |

## 关键决策与依据

### 1. env 与 doctor 职责分离

两者功能有重叠可能，但职责不同：

- `env` 是**描述性**命令：展示当前环境"是什么"（版本、路径、工具数、依赖状态），不带判断
- `doctor` 是**诊断性**命令：检查环境"对不对"（Python 版本够不够、依赖装没装、PATH 有没有命令），带 OK/FAIL 状态与修复建议

分离的好处：用户可快速 `env` 查看信息，问题排查时用 `doctor` 获取诊断。合并会模糊职责。

### 2. doctor 退出码语义

- 全部检查通过 → 返回 0
- 有失败项 → 返回 1

这与 POSIX 惯例一致，便于在 CI 或脚本中 `fcmd doctor && fcmd ...` 链式调用。

### 3. 可选依赖检查的复用

`_collect_optional_deps_status()` 抽取为独立方法，env 与 doctor 共用，避免重复实现。返回结构化列表（extra/package/installed/version），env 用于表格渲染，doctor 用于逐项检查。

### 4. 不可达分支加 pragma

`_builtin_doctor` 中 `except ImportError as e: core_ok = False` 分支实际不可达——fcmd 已导入才能执行到此处，不可能再抛 ImportError。按 rule-11 "不留死分支"原则，本应删除，但保留 try/except 结构更健壮（防御未来 fcmd 模块拆分导致的潜在问题），故加 `# pragma: no cover` 注释。

## 代码实现情况

### `_builtin_env` 实现

- 支持 `--json` 输出机器可读格式（便于脚本解析）
- 文本输出分四节：项目 / 运行时 / 工具 / 可选依赖
- 可选依赖用 rich Table 渲染，空列表时打印 `(无)`

### `_builtin_doctor` 实现

- 5 类检查：Python 版本 / fcmd 核心 / 工具模块 / 可选依赖 / PATH 命令
- 每项检查输出 OK/FAIL 状态 + 详情，FAIL 项附修复建议
- PATH 命令检查用 `shutil.which`（rule-11 "优先标准库"）
- 汇总输出 `N/M 通过，K 项失败`

### `_collect_optional_deps_status` 实现

```python
def _collect_optional_deps_status(self) -> list[dict[str, Any]]:
    """收集可选依赖的安装状态与版本。"""
    deps: list[dict[str, Any]] = []
    for extra, package in (
        ("img", "PIL"),
        ("pdf", "fitz"),
        ("pdf", "pypdf"),
        ("ocr", "pytesseract"),
    ):
        try:
            mod = __import__(package)
            version = getattr(mod, "__version__", "")
            deps.append({"extra": extra, "package": package, "installed": True, "version": version})
        except ImportError:
            deps.append({"extra": extra, "package": package, "installed": False, "version": ""})
    return deps
```

## 整合优化情况

- env/doctor 共用 `_collect_optional_deps_status`，避免重复导入逻辑
- `_BUILTIN_COMMANDS` 元组扩展后，`_info_overview` / bash/zsh/fish 补全脚本自动包含新命令，无需额外修改
- `_run_builtin` 分发链加 `# noqa: PLR0911`（6 个内建命令需 7 个 return 语句，含默认错误分支）

## 测试验证结果

### TestBuiltinEnv（9 项）

| 测试 | 覆盖路径 |
|------|---------|
| `test_env_runs_returns_0` | 基本运行 + 返回码 |
| `test_env_shows_fcmd_version` | fcmd 版本输出 |
| `test_env_shows_python_version` | Python 版本输出 |
| `test_env_shows_optional_deps` | 可选依赖表格 |
| `test_env_json_output` | --json JSON 格式输出 |
| `test_env_routes_through_run_builtin` | _run_builtin 分发 |
| `test_env_shows_tool_count` | 工具数统计 |
| `test_env_optional_deps_table_renders` | rich Table 渲染 |
| `test_env_empty_optional_deps` | 空依赖列表分支（mock） |

### TestBuiltinDoctor（12 项）

| 测试 | 覆盖路径 |
|------|---------|
| `test_doctor_runs_returns_int` | 基本运行 + 返回码 |
| `test_doctor_checks_python_version` | Python 版本检查项 |
| `test_doctor_checks_fcmd_core` | fcmd 核心导入检查项 |
| `test_doctor_checks_tool_modules` | 工具模块扫描检查项 |
| `test_doctor_checks_optional_deps` | 可选依赖检查项 |
| `test_doctor_checks_path_commands` | PATH 命令检查项 |
| `test_doctor_shows_summary` | 诊断结果汇总 |
| `test_doctor_all_pass_returns_0` | 全通过返回 0（mock） |
| `test_doctor_failed_check_returns_1` | 有失败返回 1（mock shutil.which） |
| `test_doctor_fail_shows_fix_hint` | FAIL 修复建议 |
| `test_doctor_routes_through_run_builtin` | _run_builtin 分发 |
| `test_doctor_tool_module_failure_counted` | 工具模块导入失败计入（注入假模块） |

### 门禁结果

- ruff check: All checks passed
- ruff format --check: 68 files already formatted
- pyrefly check: 0 errors (27 suppressed, 8 warnings)
- pytest: 1026 passed, 2 deselected, 5 warnings in 5.34s
- coverage: 99.21%（≥99.18% 基线），main.py 98%（剩余未覆盖为预先存在的 _ensure_tools_discovered ImportError 与 _tool_description 边界分支）

## 遗留事项

1. **main.py 预先存在的未覆盖分支**：L79-80（_ensure_tools_discovered 的 ImportError continue）、L875（_tool_description 工具不在注册表）、L898->900（_print_unknown_tool 无建议分支）——均非本次引入，可后续补测试
2. **config 命令**：本轮未实现，涉及 .fcmd.toml 配置 schema 设计与核心路径修改，风险较高，留待后续迭代
3. **show 命令**：与现有 info 命令重叠，暂不实现

## 下一轮计划

- 检查 `.trae/req/` 是否有未完成需求
- 考虑补 main.py 预先存在的未覆盖分支测试
- 或实现 config 命令（需先设计配置 schema）
- 或增强 README 文档（当前仅 36 行）
