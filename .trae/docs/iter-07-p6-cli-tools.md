# P6 迭代记录：参考 pyflowx 开发 CLI 工具

## 需求清单

- [x] 参考 `ref/pyflowx` 实现 hashfile 工具（文件/目录哈希计算）
- [x] 参考 `ref/pyflowx` 实现 filedate 工具（文件日期前缀处理）
- [x] 参考 `ref/pyflowx` 实现 writefile 工具（文本写入文件）
- [x] 参考 `ref/pyflowx` 实现 folderzip 工具（子文件夹批量压缩）
- [x] 创建 `_common.py` 共享忽略列表常量模块
- [x] 4 个工具的测试覆盖

## 迭代目标

参考 `ref/pyflowx` 的 CLI 工具实现，在 fcmd 框架下移植 4 个实用工具：
hashfile、filedate、writefile、folderzip，扩展 fcmd 工具生态。

## 改动文件清单

- `src/fcmd/cli/_common.py`（新增）：共享常量模块（IGNORE_DIRS / IGNORE_EXT），下划线前缀避免自动发现
- `src/fcmd/cli/hashfile.py`（新增）：文件哈希计算工具，f/d 子命令
- `src/fcmd/cli/filedate.py`（新增）：文件日期前缀处理工具，add/clear 子命令
- `src/fcmd/cli/writefile.py`（新增）：文本写入文件工具，w 子命令
- `src/fcmd/cli/folderzip.py`（新增）：子文件夹批量压缩工具，z 子命令
- `tests/test_cli_tools.py`（新增）：4 个工具的 35 个测试

## 关键决策与依据

### 1. 单命令工具改用子命令（writefile / folderzip）

**问题**：fcmd 的 `run_tool` 路由逻辑将第一个非 dash 参数视为子命令名。单命令工具
（subcommand=None）带位置参数时路由失败，报"工具没有子命令"。

**决策**：writefile 改为 `subcommand="w"`，folderzip 改为 `subcommand="z"`。

**依据**：框架的 run_tool 路由设计假设多命令工具的子命令为第一个位置参数；
单命令工具带位置参数与该假设冲突。改为子命令保持一致性。

### 2. folderzip 参数名 `cwd` → `directory`

**问题**：fcmd 的 `_build_task_spec` 对 `cwd` 参数特殊处理（设为 TaskSpec.cwd 工作目录），
folderzip 用 `cwd` 作为扫描目录参数与框架冲突，chdir 到不存在目录导致 FileNotFoundError。

**决策**：重命名为 `directory`，避免与框架保留参数名冲突。

**依据**：`cwd` 是 fcmd 框架级保留参数名，工具不应复用。

### 3. `_common.py` 下划线前缀

**决策**：共享常量模块以 `_` 开头，被 `_ensure_tools_discovered` 自动跳过。

**依据**：rule-03 与 project_memory 一致——下划线前缀模块不参与工具自动发现。

### 4. 移除 `platform_command` / `IS_WINDOWS`

**问题**：初版 `_common.py` 包含 `platform_command` 函数和 `IS_WINDOWS` 常量，
但无任何工具使用。

**决策**：删除未使用代码。

**依据**：rule-01 "不写未被要求的功能、不为未来预留扩展点"；
rule-11 "不留死分支"。

### 5. 简化 `add_date_prefix` 死分支

**问题**：`add_date_prefix` 有 `if new_path != filepath` 守卫，
但 `new_stem = f"{timestamp}{SEP}{stem}"` 总是前置 9+ 字符，
`new_path == filepath` 在数学上不可达。

**决策**：移除守卫，直接 rename + return。

**依据**：rule-11 "不留死分支（# pragma: no cover 应激活或删除）"。

### 6. 移除 `IGNORE_FILES`

pyflowx 的 folderzip 有 `IGNORE_FILES = [".gitignore"]`，
但 fcmd 版 folderzip 仅扫描子目录（不扫文件），该常量无用，不移植。

## 代码实现情况

### hashfile（f/d 子命令）

- `compute_hash(file_path, algorithm)`：分块读取（64KB）计算哈希
- `hash_file(path, algorithm)`：单文件打印 `algorithm  digest  path`
- `hash_directory(directory, algorithm)`：rglob 遍历，跳过 IGNORE_DIRS / IGNORE_EXT
- 子命令 f（单文件）、d（目录）

### filedate（add/clear 子命令）

- `get_file_timestamp(filepath)`：取 mtime/ctime 较大值，格式化为 YYYYMMDD
- `remove_date_prefix(filepath)`：正则移除日期前缀
- `add_date_prefix(filepath)`：添加 `YYYYMMDD_` 前缀
- `process_file_date(filepath, clear)`：clear=True 移除，False 先移除再添加
- `process_files_date(targets, clear)`：批量处理，跳过不存在/点开头文件
- 子命令 add（添加/更新）、clear（清除）

### writefile（w 子命令）

- `write_file_run(path, content, encoding)`：Path.write_text 写入
- 子命令 w

### folderzip（z 子命令）

- `archive_folder(folder)`：shutil.make_archive 压缩单目录
- `zip_folders(directory)`：扫描子目录，跳过 IGNORE_DIRS / IGNORE_EXT
- 子命令 z

## 整合优化情况

- 移除 `_common.py` 中未使用的 `platform_command` / `IS_WINDOWS`（rule-01 不预留扩展点）
- 移除 `add_date_prefix` 不可达守卫分支（rule-11 不留死分支）
- 移除 pyflowx 的 `IGNORE_FILES`（fcmd folderzip 仅扫子目录，不适用）

## 测试验证结果

- 446 tests passed（P5: 411 → P6: 446，新增 35）
- coverage 97.14%（P5: 96.97% → P6: 97.14%，提升 0.17%）
- 4 个新工具模块全部 100% 覆盖
- ruff check / format / pyrefly 全通过

## 遗留事项

- P7 候选：YAML 配置加载（需用户确认 PyYAML 依赖）
- P7 候选：`fcmd completion` shell 自动补全
- P7 候选：单命令工具支持（需改造 run_tool 路由逻辑）

## 下一轮计划

根据用户需求决定 P7 方向。若继续参考 pyflowx，可移植更多工具；
否则可推进 YAML 配置或 completion 等基础设施。
