# P33 - jsontool JSON 处理工具

## 需求清单

- [x] 支持 JSON 格式化打印（自定义缩进）
- [x] 支持 JSON 压缩为单行
- [x] 支持点路径查询（对象键 + 列表索引混合）
- [x] 支持递归按键名排序
- [x] 支持输出到文件或标准输出
- [x] 中文不转义（ensure_ascii=False）

## 迭代目标

新增 fcmd jsontool 工具，基于标准库 `json` 提供 JSON 格式化、压缩、点路径查询、按键排序能力，遵循 fcmd 框架的多子命令模式。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/fcmd/cli/jsontool.py` | 新增 | jsontool 工具主体（约 220 行）：4 子命令 + 6 公共函数 |
| `tests/test_cli_jsontool.py` | 新增 | 40 测试，覆盖 8 个测试类 |
| `.trae/docs/iter-34-p33-jsontool-tool.md` | 新增 | 本迭代记录 |
| `.trae/docs/iter-29-p28-zipencrypt-tool.md` | 删除 | 清理最旧迭代记录（保留最新 5 条） |

## 关键决策与依据

### 1. 子命令设计：pretty + minify + query + sort

**决策**：拆为四个子命令。

**依据**：
- pretty/minify 是格式转换（多行 ↔ 单行），query 是路径查询，sort 是键名排序，语义完全不同
- 参照 [csvtool.py](file:///f:/Dev/fcmd/src/fcmd/cli/csvtool.py) 的多子命令模式（show/to-json/from-json/select）
- 参数语义不同：pretty 需要 `--indent`，query 需要 `path` 位置参数，sort 需要 `--output`

### 2. 纯标准库实现

**决策**：仅用 `json` 标准库，不引入 jq/orjson/ujson。

**依据**：
- rule-11 '优先标准库 + 谨慎新增依赖'
- jq 是 C 库绑定，跨平台打包复杂
- 标准库 `json` 已支持 pretty/minify/sort_keys 等基础场景

### 3. 点路径查询不引入 jq 语法

**决策**：实现简化的点分路径（`a.b.0.c`），不支持 jq 通配符/管道/切片。

**依据**：
- jq 语法复杂（`.[].key`、`select(...)`、`|`），实现成本高且偏离 fcmd 简化原则
- 80% 场景只需固定路径访问
- 路径段以 `.` 分隔：数字段视为列表索引，其他段视为对象键

```python
for seg in segments:
    if isinstance(current, list):
        idx = int(seg)  # 数字段 → 列表索引
        ...
    elif isinstance(current, dict):
        ...
```

### 4. 路径查询错误类型分类

**决策**：根据错误场景抛不同异常：
- `KeyError`：对象键不存在
- `IndexError`：列表索引越界（含负索引）
- `TypeError`：对列表用非数字键，对非容器取子项
- `ValueError`：路径格式错误（含空段 `a..b`）

**依据**：
- 不同错误类型便于上层捕获与用户提示
- 负索引禁用：简化语义（避免 `list[-1]` 与路径计算歧义）
- 空段检测：`a..b` 是常见笔误，提前抛错避免误判

### 5. `sort_keys` 递归但不影响列表顺序

**决策**：
- dict 类型按键名排序（`sorted(dict)`）
- list 类型保持顺序，但元素递归排序
- 标量原样返回

**依据**：
- 列表顺序通常是数据语义（如时间序列），不能改
- 嵌套对象键名排序对人类阅读更友好
- 函数返回新对象，原对象不变（纯函数）

### 6. CLI 查询结果按类型输出

**决策**：`query` 子命令根据结果类型选择输出方式：
- 容器（dict/list）→ `pretty_json` 格式化
- 标量（str/int/float/bool/None）→ 直接 `print`

**依据**：
- 标量加引号（`"hello"`）反而误导
- 容器需要多行格式化便于阅读

### 7. 中文不转义

**决策**：所有 `json.dumps` 调用使用 `ensure_ascii=False`。

**依据**：rule-11 中文优先；ASCII 转义（`\u4e2d`）对用户不友好。

## 代码实现情况

### 公共函数（6 个）

- `read_json(filepath) -> Any`：读取 JSON 文件，不存在抛 FileNotFoundError
- `write_json(filepath, data, indent=2) -> None`：写入 JSON，自动创建父目录
- `pretty_json(data, indent=2) -> str`：格式化为多行 JSON
- `minify_json(data) -> str`：压缩为单行（`separators=(",", ":")`）
- `query_json(data, path) -> Any`：点路径查询，含 5 种错误分支
- `sort_keys(data) -> Any`：递归按键名排序（返回新对象）

### CLI 子命令（4 个）

- `fcmd jsontool pretty <file> [--indent N]`：格式化打印
- `fcmd jsontool minify <file>`：压缩为单行
- `fcmd jsontool query <file> <path>`：点路径查询
- `fcmd jsontool sort <file> [--output OUT]`：按键排序

## 整合优化情况

### 覆盖率一次达标

jsontool.py 首次测试即 100% 覆盖率。设计时已预先覆盖所有错误分支：
- `query_json` 的 5 个错误分支（KeyError/IndexError/TypeError×2/ValueError）均有对应测试
- `sort_keys` 的 3 种返回路径（dict/list/scalar）均有测试
- CLI 4 个子命令的不存在文件分支均覆盖

### 测试设计要点

- 框架前缀/后缀问题（P32 已记录）：`test_pretty`/`test_query_container`/`test_sort_print` 均用 `out.index("{")`/`out.index("[")` 提取 JSON 部分再解析
- `test_query_scalar`：标量直接打印，验证 `42` 在输出中
- `test_original_unchanged`：验证 `sort_keys` 不修改原对象（纯函数语义）

## 测试验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | 0 errors |
| ruff format --check | 80 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1322 passed, 1 skipped, 2 deselected |
| coverage | 99.36%（≥99.35% 基线），jsontool.py 100% |

测试套件结构（40 测试，8 类）：
- `TestRegistration`（2）：工具注册与子命令结构
- `TestReadJson`（2）：基本读取/不存在
- `TestWriteJson`（3）：基本写入/创建父目录/自定义缩进
- `TestPrettyJson`（3）：基本/自定义缩进/中文不转义
- `TestMinifyJson`（3）：基本/无空白/中文不转义
- `TestQueryJson`（10）：空路径/对象键/列表索引/混合/键缺失/越界/负索引/非数字键/非容器取子项/空段
- `TestSortKeys`（5）：基本/嵌套/列表顺序保持/原对象不变/标量
- `TestCLISubcommands`（12）：pretty/minify/query/sort 端到端 + 错误分支

## 遗留事项

无。所有功能、边界场景、错误分支均已覆盖。

## 下一轮计划

P33 jsontool 工具已完成。后续可继续：
- 扫描 `.trae/req/` 处理未完成需求
- 新增 archivex create 子命令（目录打包归档）
- 新增 csvtool merge 子命令（多 CSV 合并）
- 新增 filesearch 文件搜索 + 内容 grep 工具
