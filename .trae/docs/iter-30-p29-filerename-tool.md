# P29 - filerename 文件批量重命名工具

## 需求清单

- [x] 移植 bitool filename.py 为 fcmd filerename 工具，支持正则替换、位置插入、大小写转换三种模式
- [x] 三子命令结构（replace/insert/case），操作文件名主干保留扩展名
- [x] 支持 --preview 预览模式
- [x] 目标名冲突时跳过并提示

## 迭代目标

将 bitool 的 `filename.py`（基于 attrs/BaseCommand 模式）移植为 fcmd 的 `@fcmd.tool` 多子命令工具，遵循 fcmd 框架约定（函数签名驱动 CLI、`list[Path]` 位置参数、子命令分发）。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/fcmd/cli/filerename.py` | 新增 | filerename 工具主体（76 行）：3 子命令 + 4 公共函数 |
| `tests/test_cli_filerename.py` | 新增 | 36 测试，覆盖 7 个测试类 |
| `.trae/docs/iter-30-p29-filerename-tool.md` | 新增 | 本迭代记录 |
| `.trae/docs/iter-25-p24-readme-enhancement.md` | 删除 | 清理最旧迭代记录（保留最新 5 条） |

## 关键决策与依据

### 1. 参数名 `dry_run` → `preview`（避免与框架 `--dry-run` 冲突）

**问题**：`_add_global_options()` 在 [toolkit.py:593](file:///f:/Dev/fcmd/src/fcmd/apis/toolkit.py#L593) 为每个 parser 自动添加 `--dry-run` 全局选项；函数签名中 `dry_run: bool = False` 又会生成同名 `--dry-run`，触发 `argparse.ArgumentError: argument --dry-run/-n: conflicting option string`。

**决策**：将函数参数统一重命名为 `preview`，CLI 选项变为 `--preview`，与框架全局 `--dry-run`（控制 DAG 执行）解耦——`--preview` 表达"工具级预览不实际执行"，语义更精准。

### 2. Windows 大小写不敏感文件系统处理

**问题**：Windows/macOS 文件系统大小写不敏感，`MyFile.txt` 与 `myfile.txt` 是同一文件。仅大小写不同的重命名（如 `change_case` 转 lower）时，`target.exists()` 返回 True，原 `if target.exists():` 判定会错误跳过。

**决策**：用 `target.resolve() != filepath.resolve()` 区分"同一文件（仅大小写不同）"与"真实冲突"。仅大小写不同的重命名允许执行。

```python
if target.exists() and target.resolve() != filepath.resolve():
    print(f"跳过（目标已存在）: {filepath.name} -> {target.name}")
    return False
```

### 3. 位置参数 vs 选项参数

**问题**：`pattern: str` 和 `text: str` 无默认值，被 argparse 当作位置参数，但早期 CLI 测试错误地用 `--pattern`/`--text` 传递导致失败。

**决策**：保留位置参数语义（pattern 和 text 是必填），修正 CLI 测试调用方式：
```python
# 错误：--pattern 是选项
run_tool("filerename", ["replace", str(src), "--pattern", r"\s+", ...])
# 正确：pattern 是位置参数
run_tool("filerename", ["replace", str(src), r"\s+", ...])
```

### 4. 无默认值参数设计参考

参考 [filedate.py](file:///f:/Dev/fcmd/src/fcmd/cli/filedate.py) 与 [filelevel.py](file:///f:/Dev/fcmd/src/fcmd/cli/filelevel.py) 的多子命令模式：`files: list[Path]` 作为第一个位置参数（必填），其余必填参数也是位置参数，可选参数用 `--name` 选项。

## 代码实现情况

### 公共函数（4 个）

- `_safe_rename(filepath, new_stem, preview) -> bool`：安全重命名，保留扩展名；同名跳过；目标已存在且非同一文件跳过；preview 模式仅打印
- `replace_pattern(filepath, pattern, replacement, preview) -> bool`：正则替换文件名主干
- `insert_text(filepath, text, position, preview) -> bool`：位置插入文本（position 越界截断到末尾）
- `change_case(filepath, mode, preview) -> bool`：大小写转换（lower/upper/title）

### CLI 子命令（3 个）

- `fcmd filerename replace <files...> <pattern> [--replacement R] [--preview]`：正则替换
- `fcmd filerename insert <files...> <text> [--position N] [--preview]`：位置插入
- `fcmd filerename case <files...> [--mode lower|upper|title] [--preview]`：大小写转换

## 整合优化情况

- 移除测试中冗余的 `filerename_replace`/`filerename_insert`/`filerename_case` 导入：`@fcmd.tool` 装饰器在模块导入时已注册工具，仅需导入辅助函数即可（遵循 P28 总结的"from-import 注册"经验）
- 补充 `test_run_insert_nonexistent_file` 与 `test_run_case_nonexistent_file` 测试，覆盖 insert/case 子命令的文件不存在分支，将 filerename.py 覆盖率从 93% 提升到 100%

## 测试验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | 0 errors |
| ruff format --check | 74 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1151 passed, 1 skipped, 2 deselected |
| coverage | 99.29%（≥99.27% 基线），filerename.py 100% |

测试套件结构（36 测试，7 类）：
- `TestRegistration`（2）：工具注册与子命令结构
- `TestSafeRename`（4）：成功/同名跳过/目标已存在/预览
- `TestReplacePattern`（5）：匹配/不匹配/反向引用/删除匹配/保留扩展名
- `TestInsertText`（6）：开头/中间/末尾/空文本/保留扩展名/预览
- `TestChangeCase`（6）：lower/upper/title/无效模式/无变化/预览
- `TestCLISubcommands`（13）：三子命令端到端 + 文件不存在 + 无效参数 + 多文件批量

## 遗留事项

无。所有功能、边界场景、错误分支均已覆盖。

## 下一轮计划

P29 filerename 工具已完成。后续可继续：
- 扫描 `.trae/req/` 处理未完成需求
- 移植 bitool 其他通用工具
- 持续优化现有工具
