# P35 - filesearch 文件搜索工具

## 需求清单

- [x] 新增 `filesearch` 工具：按文件名 glob 搜索 + 按内容正则搜索
- [x] 纯 stdlib 实现（pathlib + fnmatch + re）
- [x] 复用 `_common.IGNORE_DIRS` 跳过常见忽略目录
- [x] 二进制文件检测（前 1024 字节 `\x00` 启发式）
- [x] 编码回退（utf-8 → utf-8 + errors="replace"，Python 3.8 兼容）
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage）

## 迭代目标

为 fcmd 增加文件搜索能力，填补 ripgrep 简化版的空白。两种搜索模式分列为子命令：
1. `name` - 按文件名 glob 模式（fnmatch 语法）递归搜索
2. `content` - 按文件内容正则（re 语法）递归搜索，输出 `文件:行号:行内容` 格式

## 改动文件清单

| 文件 | 类型 | 行数 | 说明 |
|------|------|------|------|
| `src/fcmd/cli/filesearch.py` | 新增 | 245 | filesearch 工具实现，2 子命令 name/content |
| `tests/test_cli_filesearch.py` | 新增 | 326 | 46 测试，7 类（覆盖公共 API + 错误分支 + CLI 端到端） |
| `.trae/docs/iter-36-p35-filesearch-tool.md` | 新增 | - | 本迭代记录 |
| `.trae/docs/iter-31-p30-textdiff-tool.md` | 删除 | - | 清理最旧记录（保留最新 5 条） |

## 关键决策与依据

### 1. 两子命令拆分（name/content）
- **依据**：不同搜索语义（文件名匹配 vs 内容匹配）+ 不同参数（`include_dirs` vs `extension`）+ 不同输出格式（路径列表 vs `文件:行号:行内容`）。
- 参考 P30 textdiff 的 file/dir 拆分模式。

### 2. 纯 stdlib 实现
- **依据**：rule-11「优先标准库」+ memory「stdlib-first」。`fnmatch` 处理 glob，`re` 处理正则，`pathlib.rglob` 递归遍历，无需引入第三方依赖（如 wcmatch/regex）。

### 3. 复用 `_common.IGNORE_DIRS` 通过惰性导入
- 在 `search_by_name`/`search_by_content` 内部 `from fcmd.cli._common import IGNORE_DIRS`，避免模块级循环依赖风险，且支持测试自定义 `ignore_dirs` 参数。

### 4. `_should_skip_part` 支持通配模式
- `IGNORE_DIRS` 中含 `*.egg-info` 通配模式（_common.py 已定义），`_should_skip_part` 同时处理精确匹配与 fnmatch 通配：
  ```python
  for part in parts:
      if part in ignore_dirs:
          return True
      if any(fnmatch.fnmatch(part, pat) for pat in ignore_dirs if "*" in pat):
          return True
  ```
- 仅对含 `*` 的模式调用 fnmatch，避免对每个精确目录名做无谓 fnmatch 调用（性能优化）。

### 5. 二进制文件检测（git 启发式）
- 复用 P30 textdiff 的 `\x00` 字节检测：前 1024 字节含 `\x00` 判定为二进制。
- `is_binary_file` 的 `except OSError` 保守返回 True（无法读取的文件跳过内容搜索）。
- `read_text_lines` 抛 `ValueError("二进制文件: ...")`，`search_by_content` 捕获后 `continue` 跳过该文件。

### 6. 编码回退 Python 3.8 兼容
- Python 3.8 没有 `encoding="locale"`（3.10+ 才有），采用 `utf-8` 优先 + UnicodeDecodeError 回退 `utf-8 + errors="replace"`，与 P30 textdiff 一致。
- 测试 `test_utf8_decode_fallback` 写入非法 utf-8 字节序列（不含 `\x00`，避免被识别为二进制），验证不抛异常。

### 7. 结果排序保证确定性
- `search_by_name` 用 `results.sort(key=str)` 显式按字符串排序（ruff PLW0108 提示 `lambda p: str(p)` 应简化为 `str`）。
- `search_by_content` 用 `sorted(directory.rglob("*"))` 先排序再遍历，保证多文件匹配结果顺序确定。

### 8. 大文件保护
- `_MAX_LINES_PER_FILE = 100_000` 限制单文件读取行数上限，避免大日志文件内存爆炸。`lines[:_MAX_LINES_PER_FILE]` 切片后 enumerate。

## 代码实现情况

### filesearch.py 结构（245 行）

```
导入区（fnmatch/re/Path/fcmd）
__all__（5 个公共符号）
常量：_BINARY_SNIFF_SIZE=1024, _MAX_LINES_PER_FILE=100_000

辅助函数：
  _should_skip_part(parts, ignore_dirs) -> bool
  
公共函数（5 个，含 4 个公开 API + 1 个内部辅助）：
  is_binary_file(path) -> bool
  read_text_lines(path) -> list[str]    # 二进制抛 ValueError
  search_by_name(directory, pattern, include_dirs=False, ignore_dirs=None) -> list[Path]
  search_by_content(directory, pattern, extension="", ignore_dirs=None)
                  -> list[tuple[Path, int, str]]

CLI 子命令（2 个）：
  @fcmd.tool("filesearch", subcommand="name")
  filesearch_name(directory, pattern, include_dirs=False)
  
  @fcmd.tool("filesearch", subcommand="content")
  filesearch_content(directory, pattern, extension="")
```

### 测试覆盖（46 测试，7 类）

| 类 | 测试数 | 覆盖点 |
|----|--------|--------|
| TestRegistration | 2 | 工具注册 + 子命令结构 |
| TestShouldSkipPart | 5 | 精确匹配/无匹配/通配/通配无匹配/空 |
| TestIsBinaryFile | 4 | 文本/二进制/空文件/OSError 回退 |
| TestReadTextLines | 4 | 正常/无尾换行/二进制抛错/utf-8 回退 |
| TestSearchByName | 11 | 基本/递归/include_dirs/默认排除/自定义排除/通配/无匹配/不存在/非目录/排序 |
| TestSearchByContent | 10 | 基本/多匹配/扩展名/跳过二进制/默认排除/自定义排除/无匹配/不存在/非目录/正则错误 |
| TestCLISubcommands | 11 | name 基本/无匹配/include_dirs/不存在/非目录 + content 基本/扩展名/无匹配/正则错误/不存在 |

## 整合优化情况

- 复用 `_common.IGNORE_DIRS` 跨工具共享常量，避免重复定义。
- 二进制检测、编码回退策略与 P30 textdiff 一致，形成项目约定。
- `read_text_lines` 设计为可独立测试的公共函数，便于 P30 textdiff 后续重构复用（暂未实施，避免本次扩大范围）。

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| ruff check | All checks passed |
| ruff format --check | 86 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1395 passed, 1 skipped, 2 deselected |
| 总覆盖率 | 99.37%（≥99.37% P34 基线，持平） |
| filesearch.py 覆盖率 | 99%（103 stmts, 53 branches, 仅 140->142 一处 coverage.py 已知误报） |

### 修复过程

#### 1. ruff PLW0108：lambda 不必要
- 错误：`results.sort(key=lambda p: str(p))` 
- 修复：改为 `results.sort(key=str)`
- 依据：ruff 提示 "Inline function call"

#### 2. 测试期望错误：test_multiple_matches
- 错误：期望 `results[1][1] == 3`，实际 `4`
- 原因：`"def foo():\n    pass\n\ndef bar():\n    pass\n"` 中空行也算一行
  - 行 1: `def foo():`
  - 行 2: `    pass`
  - 行 3: ``（空行）
  - 行 4: `def bar():`
- 修复：期望改为 `4`，添加注释说明

#### 3. 覆盖率分支 189->193 未覆盖
- 原因：`if ignore_dirs is None:` 的 False 分支（即传入自定义 ignore_dirs）未被 search_by_content 的测试触发
- 修复：添加 `test_ignore_dirs_custom` 测试，传入 `ignore_dirs={"skipme"}`

#### 4. 覆盖率分支 140->142 未覆盖（已知误报，忽略）
- 原因：coverage.py 对小循环 + continue 的控制流统计有已知 bug
- 依据：memory 已记录「coverage.py may not track `continue` statements correctly in some for-loops」
- 实际代码路径已被 `test_exclude_dirs_by_default` 覆盖（include_dirs=False + is_dir()=True 触发 line 140 短路 + line 142 continue）
- 不加 `# pragma: no cover`（会排除周边语句）

## 遗留事项

- 无

## 下一轮计划

候选方向（按优先级）：
1. archivex 新增 `create` 子命令（zip/tar.gz/tar.bz2/tar.xz 目录打包）
2. csvtool 新增 `merge` 子命令（多 CSV 按列合并，union/intersection 两种模式）
3. jsontool 新增 `query` 子命令（jq 简化版，支持点路径 + 过滤表达式）
4. 增强现有工具的边界场景测试
