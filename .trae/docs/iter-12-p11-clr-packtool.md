# P11 迭代记录：clr / packtool

## 需求清单

- [x] 移植 clr 工具（跨平台清屏，单命令）
- [x] 移植 packtool 工具（6 子命令：src/deps/wheel/embed/zip/clean）
- [x] 新增 2 个工具的测试
- [x] 全套门禁 + 迭代记录 + 提交

## 迭代目标

继续扩展 fcmd CLI 工具生态，从 `ref/pyflowx` 移植 2 个工具：
- clr 补全基础工具生态（清屏）
- packtool 提供 Python 项目打包全流程（源码/依赖/wheel/嵌入式 Python/zip/清理）

## 改动文件清单

### 新增

- `src/fcmd/cli/clr.py` - 跨平台清屏工具（单命令）
- `src/fcmd/cli/packtool.py` - Python 打包工具（6 子命令）
- `tests/test_cli_tools_p11.py` - 26 个测试用例
- `.trae/docs/iter-12-p11-clr-packtool.md` - 本迭代记录

### 删除

- `.trae/docs/iter-07-p6-cli-tools.md` - 清理最旧迭代记录（保留最新 5 条）

## 关键决策与依据

### 1. packtool 简化版不解析 pyproject.toml

**决策**：`pack_source` 直接用 `project_dir.name` 作为项目名，不解析 `pyproject.toml`
获取 `project.name`。

**依据**：
- pyflowx 原版用 `tomllib`（Python 3.11+）或 `tomli`（第三方）解析 TOML。
- rule-11 最低支持 Python 3.8，`tomllib` 不可用；引入 `tomli` 需用户授权（rule-01 暂停条件）。
- rule-11 "优先标准库；新增依赖须审慎"。
- 项目名通常就是目录名，简化版不影响核心打包功能。

### 2. packtool deps 子命令改为 positional 参数

**决策**：`pack_dependencies(packages: list[str], lib_dir: Path = Path("libs"))`，
`packages` 为 positional 必填参数（pyflowx 原版为 `dependencies: list[str] | None = None`）。

**依据**：
- fcmd 的 `_add_optional_arg` 不支持 `list[str] | None` 联合类型（origin 是 UnionType，
  非 list），无法识别为多值参数。
- positional `list[str]` 映射为 `nargs="+"`，用户必须传至少一个包名，语义更清晰。
- rule-11 "可变默认参数用 None 哨兵"——这里改为必填 positional，避免可变默认参数问题。

### 3. packtool embed 保留网络下载功能

**决策**：`install_embed_python` 保留 `urllib.request.urlretrieve` 下载 + `zipfile` 解压，
测试用 mock + 预创建 fake zip 覆盖 cache 命中与未命中两条路径。

**依据**：
- `urllib`/`zipfile` 均为标准库，无新依赖。
- pyflowx 原版支持 cache 机制（避免重复下载），保留此优化。
- 测试不进行真实网络请求，用 mock + shutil.copy 模拟下载。

### 4. clr 用 sys.platform 替代 platform_command

**决策**：`clear_screen` 用 `sys.platform == "win32"` 直接判断，不引入 pyflowx 的
`platform_command` 辅助函数。

**依据**：
- rule-01 "不做未被要求的功能"，`platform_command` 已在 P6 移除。
- taskkill.py 已建立用 `sys.platform` 的先例（P9）。
- clr 逻辑极简（10 行代码），无需抽象。

### 5. packtool 局部定义 _IGNORE_PATTERNS

**决策**：`_IGNORE_PATTERNS` 在 `packtool.py` 内部定义，不添加到 `_common.py`。

**依据**：
- rule-01 "三处相似才考虑提取，不过早抽象"——仅 packtool 使用 glob 模式忽略。
- `_common.py` 的 `IGNORE_DIRS`/`IGNORE_EXT` 是 set[str]，不支持 glob 通配符。
- `shutil.ignore_patterns` 需要 glob 模式列表，类型不同。

## 代码实现情况

### clr.py

- `clear_screen()`：返回 int 退出码，`sys.platform` 选择 `cls`/`clear`
- `clear_screen_run()`：CLI 入口（`@fcmd.tool("clr")`，单命令）
- `subprocess.run(check=False)` 容忍非 TTY 环境失败

### packtool.py

- 6 个子命令：
  - `src`：`shutil.copytree` + `ignore_patterns` 复制源码（区分 src/ 子目录与散文件）
  - `deps`：`pip install --target --no-compile` 打包依赖
  - `wheel`：`pip wheel --no-deps --wheel-dir` 构建 wheel
  - `embed`：`urllib.request.urlretrieve` 下载 + `zipfile.extractall` 解压嵌入式 Python
  - `zip`：`zipfile.ZIP_DEFLATED` 压缩
  - `clean`：`shutil.rmtree` 清理构建目录
- `_normalize_arch()`：x86_64/amd64 → amd64，arm64/aarch64 → arm64，其他原值返回
- `_VERSION_MAP`：3.8-3.12 短版本到完整版本映射
- `_IGNORE_PATTERNS`：glob 模式列表（__pycache__/.git/.venv 等）

## 整合优化情况

- 2 个工具均遵循 fcmd 既有风格（模块 docstring、`__all__`、中文注释）
- `_run` 辅助函数与 piptool/autofmt 风格一致（`subprocess.run(check=False, text=True)`）
- 测试用命名 helper 函数（`_recording_run`/`_success_run`）避免 lambda ARG005

## 测试验证结果

- 26 个测试用例全部通过
- 全套门禁通过：
  - `ruff check`：All checks passed!
  - `ruff format --check`：52 files already formatted
  - `pyrefly check`：0 errors
  - `pytest`：706 passed（P10: 680 → P11: 706，+26）
  - 覆盖率：98%（P10: 97.64% → P11: 98%，+0.36%）
- 各工具覆盖率：
  - clr.py: 100%
  - packtool.py: 100%（补充 _run/arch fallback/subdir 分支测试后从 95% 提升）

## 遗留事项

- packtool 不解析 pyproject.toml，项目名取目录名（简化版）。
- packtool embed 仅支持 Windows 嵌入式 Python（python.org 发布的 embed zip）。
- packtool deps/wheel 依赖外部 pip 命令，未在测试中真实执行。

## 下一轮计划

P11 完成 2 个工具移植。后续可选方向：
- 继续移植更多 CLI 工具（envdev/imagetool/pdftool/screenshot/dockercmd 等）
- 实现 conditions 模块（启用 gittool isub 和 YAML matrix/if 支持）
- 增强 fcmd 参数解析对 Literal/int 字符串注解的支持
- YAML schema 扩展（matrix/if 条件）
- 动态 completion（运行时查询 vs 静态嵌入）
