# P30 - textdiff 文本比较工具

## 需求清单

- [x] 基于 stdlib difflib 实现两文件 unified diff 输出
- [x] 支持目录递归比较（仅左/仅右/内容不同/无法比较）
- [x] 支持 `--context`/`--color`/`--pattern`/`--no-recursive` 参数
- [x] 二进制文件检测与跳过
- [x] 编码兼容（utf-8 优先，回退 errors='replace'）

## 迭代目标

新增 fcmd textdiff 工具，基于 stdlib `difflib`/`filecmp` 提供文件与目录差异比较能力，遵循 fcmd 框架的多子命令模式。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/fcmd/cli/textdiff.py` | 新增 | textdiff 工具主体（约 210 行）：2 子命令 + 4 公共函数 |
| `tests/test_cli_textdiff.py` | 新增 | 35 测试，覆盖 7 个测试类 |
| `.trae/docs/iter-31-p30-textdiff-tool.md` | 新增 | 本迭代记录 |
| `.trae/docs/iter-26-p25-main-coverage-gaps.md` | 删除 | 清理最旧迭代记录（保留最新 5 条） |

## 关键决策与依据

### 1. 子命令设计：file + dir

**决策**：拆为两个子命令而非单命令多模式。

**依据**：
- 文件比较返回 unified diff 文本，目录比较返回差异文件列表，输出格式完全不同
- 参数语义不同：file 需要 `--context`/`--color`，dir 需要 `--pattern`/`--recursive`
- 参照 [filerename.py](file:///f:/Dev/fcmd/src/fcmd/cli/filerename.py) 的多子命令模式（replace/insert/case）

### 2. 二进制文件检测

**问题**：difflib 对二进制文件无意义，且 `readlines()` 可能产生乱码或异常。

**决策**：读取前 1024 字节，若含 `\x00` 字节判定为二进制文件，抛 `ValueError`。CLI 层捕获并提示。

```python
with filepath.open("rb") as f:
    if b"\x00" in f.read(1024):
        raise ValueError(f"二进制文件不支持比较: {filepath}")
```

**依据**：git 同样用此启发式（`bindiff.c` 中 `contains_zero`）。

### 3. 编码回退策略

**问题**：Python 3.8 没有 `encoding="locale"`（3.10+ 才有）。

**决策**：utf-8 优先，UnicodeDecodeError 时回退为 `utf-8 + errors='replace'`（损失但可用）。比 `locale.getpreferredencoding()` 更可预测。

### 4. 目录比较用 `Path.rglob` 而非 `os.walk`

**决策**：`Path.rglob(pattern)` 返回 Path 对象，`relative_to(dir).as_posix()` 转为相对路径（跨平台用 `/`），便于两侧目录比对。

```python
files1 = {p.relative_to(dir1).as_posix() for p in dir1.rglob(pattern) if p.is_file()}
```

### 5. `recursive: bool = True` 用 `--no-recursive` 关闭

**依据**：P22 已为 `bool=True` 参数添加 `--no-name store_false` 支持，`dest=pname` 保留原参数名。`--no-recursive` 自动可用。

### 6. ANSI 着色独立为 `colorize_diff` 函数

**决策**：着色逻辑独立为纯函数，输入输出都是字符串，便于单测。CLI 层根据 `--color` 标志决定是否调用。

## 代码实现情况

### 公共函数（4 个）

- `_read_lines(filepath) -> list[str]`：文本读取，二进制检测抛 ValueError，utf-8 失败回退 errors='replace'
- `colorize_diff(diff_text) -> str`：unified diff 文本添加 ANSI 颜色（-红/+绿/@@青，文件头不着色）
- `compare_files(file1, file2, context=3) -> str`：unified diff 字符串，相同返回空串
- `compare_directories(dir1, dir2, pattern="*", recursive=True) -> str`：差异报告，含仅左/仅右/不同/无法比较四类

### CLI 子命令（2 个）

- `fcmd textdiff file <f1> <f2> [--context N] [--color]`：文件 unified diff
- `fcmd textdiff dir <d1> <d2> [--pattern GLOB] [--no-recursive]`：目录差异列表

## 整合优化情况

- 初版 textdiff.py 覆盖率 92%，缺 4 处分支：OSError 处理（160-161）、errors 输出（174-175）、file2 不存在（198-199）、dir2 不存在（230-231）
- 补充 4 个测试：
  - `test_oserror_handling`：monkeypatch `filecmp.cmp` 抛 OSError，覆盖 160-161 和 174-175
  - `test_run_file_second_nonexistent`：file1 存在 file2 不存在，覆盖 198-199
  - `test_run_dir_second_nonexistent`：dir1 存在 dir2 不存在，覆盖 230-231
- textdiff.py 覆盖率从 92% 提升至 100%

## 测试验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | 0 errors |
| ruff format --check | 76 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1186 passed, 1 skipped, 2 deselected |
| coverage | 99.31%（≥99.29% 基线），textdiff.py 100% |

测试套件结构（35 测试，7 类）：
- `TestRegistration`（2）：工具注册与子命令结构
- `TestReadLines`（3）：utf-8/编码回退/二进制检测
- `TestColorizeDiff`（5）：新增绿/删除红/@@青/文件头不着色/上下文不着色
- `TestCompareFiles`（4）：相同/不同/上下文/二进制
- `TestCompareDirectories`（8）：相同/仅左/仅右/不同/递归/非递归/模式过滤/OSError
- `TestCLISubcommands`（13）：file/dir 子命令端到端 + 边界场景

## 遗留事项

无。所有功能、边界场景、错误分支均已覆盖。

## 下一轮计划

P30 textdiff 工具已完成。后续可继续：
- 扫描 `.trae/req/` 处理未完成需求
- 移植 archive 解压工具
- 移植 tomltool TOML 读写工具
- 增强现有工具
