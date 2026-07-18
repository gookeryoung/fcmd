# P34 - pathtool 路径处理工具

## 需求清单

- [x] 支持路径规范化（展开 ~、绝对化、消除 `..`/`.`）
- [x] 支持相对路径计算
- [x] 支持路径各部分提取（anchor/parent/name/stem/suffix 等）
- [x] 支持两路径差异比较（公共前缀 + 各自独有部分）

## 迭代目标

新增 fcmd pathtool 工具，基于标准库 `pathlib` 提供路径规范化、相对路径计算、各部分提取、路径差异比较能力，遵循 fcmd 框架的多子命令模式。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/fcmd/cli/pathtool.py` | 新增 | pathtool 工具主体（约 170 行）：4 子命令 + 4 公共函数 |
| `tests/test_cli_pathtool.py` | 新增 | 27 测试，覆盖 6 个测试类 |
| `.trae/docs/iter-35-p34-pathtool-tool.md` | 新增 | 本迭代记录 |
| `.trae/docs/iter-30-p29-filerename-tool.md` | 删除 | 清理最旧迭代记录（保留最新 5 条） |

## 关键决策与依据

### 1. 子命令设计：show + rel + norm + diff

**决策**：拆为四个子命令。

**依据**：
- show 是只读信息提取，rel 是相对路径计算，norm 是规范化，diff 是比较，语义完全不同
- 参照 [csvtool.py](file:///f:/Dev/fcmd/src/fcmd/cli/csvtool.py) 的多子命令模式
- 参数语义不同：rel 需要两个位置参数（path/base），diff 需要两个位置参数（p1/p2）

### 2. 纯标准库实现

**决策**：仅用 `pathlib.Path`，不引入 os.path 或第三方库。

**依据**：
- rule-11 '优先标准库 + 优先 pathlib'
- pathlib 是面向对象 API，比 os.path 更现代
- 跨平台自动处理分隔符（Windows `\` / POSIX `/`）

### 3. Python 3.8 Windows 兼容性：`absolute()` 先于 `resolve()`

**问题**：Python 3.8 Windows 上 `Path.resolve(strict=False)` 对不存在路径可能返回相对路径（已知行为差异，3.10+ 修复）。

**决策**：先 `absolute()` 确保绝对化，再 `resolve(strict=False)` 消除 `..`/`.`。

```python
return path.expanduser().absolute().resolve(strict=False)
```

**依据**：
- `Path.absolute()` 基于 cwd 绝对化，不解析符号链接，不消除 `..`
- `Path.resolve(strict=False)` 解析符号链接（如可解析）+ 消除 `..`/`.`
- 两者组合保证跨版本一致行为

### 4. `path_diff` 返回组件列表而非路径

**决策**：返回 `(common, only_p1, only_p2)` 三元组，每个是组件列表（如 `["a", "b"]`）而非路径字符串。

**依据**：
- 路径字符串含分隔符，跨平台处理麻烦
- 组件列表便于上层格式化输出（用 ` / ` 连接）
- 与 `Path.parts` 一致的表示

### 5. `path_parts` 返回字典

**决策**：返回包含 8 个字段的字典：`input`/`absolute`/`anchor`/`parent`/`name`/`stem`/`suffix`/`suffixes`/`parts`。

**依据**：
- 字段名与 `pathlib.Path` 属性一致，降低学习成本
- 字典便于 CLI 层格式化输出（按字段名取值）
- 包含 `input` 字段保留原始输入，便于对比

### 6. `relative_to` 自动规范化输入

**决策**：调用 `normalize_path` 规范化两个输入路径，再计算相对路径。

**依据**：
- 用户可能传入 `./project/../project/file.txt` 这种含 `..` 的路径
- `Path.relative_to` 要求严格匹配前缀，不规范化会失败
- 自动规范化提升鲁棒性

## 代码实现情况

### 公共函数（4 个）

- `normalize_path(path) -> Path`：规范化路径（expanduser + absolute + resolve(strict=False)）
- `relative_to(path, base) -> Path`：相对路径计算（先规范化两路径）
- `path_parts(path) -> dict[str, Any]`：提取 8 个字段（input/absolute/anchor/parent/name/stem/suffix/suffixes/parts）
- `path_diff(p1, p2) -> tuple[list[str], list[str], list[str]]`：返回公共前缀 + 各自独有组件

### CLI 子命令（4 个）

- `fcmd pathtool show <path>`：显示路径各部分信息
- `fcmd pathtool rel <path> <base>`：计算相对路径
- `fcmd pathtool norm <path>`：规范化路径
- `fcmd pathtool diff <p1> <p2>`：比较两路径差异

## 整合优化情况

### 初版 4 个测试失败

1. **3 个 `TestNormalizePath` 失败**：Python 3.8 Windows 上 `Path.resolve(strict=False)` 对不存在路径未绝对化
   - 修复：`normalize_path` 改为 `path.expanduser().absolute().resolve(strict=False)`

2. **1 个 `test_norm` 失败**：框架输出 `> 'norm' 开始执行...` 含 `...`（含 `..`），导致 `assert ".." not in out` 误判
   - 修复：提取实际路径行（`lines[1]`）单独验证

### ruff 错误修复

- **RUF059 × 3**：`path_diff` 返回的 `common` 在 3 个测试中未使用，改为 `_common`

### 覆盖率一次达标

修复上述问题后 pathtool.py 100% 覆盖率，所有 12 个分支均覆盖。

## 测试验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | 0 errors |
| ruff format --check | 84 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1349 passed, 1 skipped, 2 deselected |
| coverage | 99.37%（≥99.36% 基线），pathtool.py 100% |

测试套件结构（27 测试，6 类）：
- `TestRegistration`（2）：工具注册与子命令结构
- `TestNormalizePath`（4）：相对转绝对/.. 消除/. 消除/~ 展开
- `TestRelativeTo`（4）：基本/嵌套/不在 base 下/规范化输入
- `TestPathParts`（4）：带扩展名/多扩展名/目录/输入保留
- `TestPathDiff`（4）：公共前缀/无公共/相同路径/前缀关系
- `TestCLISubcommands`（9）：show/rel/norm/diff 端到端 + 错误分支

## 遗留事项

无。所有功能、边界场景、错误分支均已覆盖。

## 下一轮计划

P34 pathtool 工具已完成。后续可继续：
- 扫描 `.trae/req/` 处理未完成需求
- 新增 archivex create 子命令（目录打包归档）
- 新增 csvtool merge 子命令（多 CSV 合并）
- 新增 filesearch 文件搜索 + 内容 grep 工具
