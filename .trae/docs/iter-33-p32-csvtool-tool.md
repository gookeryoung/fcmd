# P32 - csvtool CSV 处理工具

## 需求清单

- [x] 支持 CSV 文件预览（前 N 行，表格对齐输出）
- [x] 支持 CSV 与 JSON 双向转换
- [x] 支持按列筛选与重排
- [x] 支持 `--no-header` 无表头模式
- [x] 支持输出到文件或标准输出

## 迭代目标

新增 fcmd csvtool 工具，基于标准库 `csv`/`json` 提供 CSV 文件预览、与 JSON 互转、按列筛选能力，遵循 fcmd 框架的多子命令模式。

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/fcmd/cli/csvtool.py` | 新增 | csvtool 工具主体（约 340 行）：4 子命令 + 6 公共函数 |
| `tests/test_cli_csvtool.py` | 新增 | 43 测试，覆盖 8 个测试类 |
| `.trae/docs/iter-33-p32-csvtool-tool.md` | 新增 | 本迭代记录 |
| `.trae/docs/iter-28-p27-tox-cross-version-fix.md` | 删除 | 清理最旧迭代记录（保留最新 5 条） |

## 关键决策与依据

### 1. 子命令设计：show + to-json + from-json + select

**决策**：拆为四个子命令。

**依据**：
- show 是只读预览，to-json/from-json 是格式互转，select 是按列投影，语义完全不同
- 参照 [textdiff.py](file:///f:/Dev/fcmd/src/fcmd/cli/textdiff.py) 的多子命令模式（file/dir）
- 参数语义不同：show 需要 `--rows`/`--no-header`，from-json 需要 `--output`，select 需要 `columns` 位置参数

### 2. 纯标准库实现

**决策**：仅用 `csv` + `json` 标准库，不引入 pandas。

**依据**：
- rule-11 '优先标准库 + 谨慎新增依赖'
- pandas 体积庞大（数百 MB），对简单 CSV 操作过重
- 标准库 `csv` 模块已支持表头/引号/转义/多行字段等复杂场景

### 3. JSON 转 CSV 时表头由键并集生成

**决策**：`json_to_csv` 收集所有对象的键并集作为表头，缺失键补空字符串，键顺序按首次出现顺序。

```python
header_keys: list[str] = []
seen: set[str] = set()
for item in data:
    for key in item:
        if key not in seen:
            seen.add(key)
            header_keys.append(key)
```

**依据**：
- JSON 对象数组中各对象键可能不一致（如某些行缺字段）
- 用并集而非首对象键，避免数据丢失
- 首次出现顺序保持稳定，避免 `set` 的哈希顺序导致输出不确定

### 4. CSV 转 JSON 时行长不足补空字符串

**决策**：行长度小于表头时补 `""`，超出时截断。

**依据**：
- 实际 CSV 文件可能存在行不齐（手工编辑、数据导出 bug）
- 抛异常会中断批量处理，补齐更友好
- 截断超长部分避免索引越界

### 5. `format_table` 独立为纯函数

**决策**：表格对齐逻辑独立为纯函数，输入 `header`/`rows`，输出多行文本。

**依据**：
- 便于单测（验证列宽计算、截断、对齐）
- CLI 层根据需要调用，纯函数无副作用

### 6. 列宽截断策略

**决策**：每列最大宽度 30 字符，超出截断并加省略号 `...`。

```python
if len(cell) > max_width:
    cell = cell[: max_width - 3] + "..."
```

**依据**：
- 终端宽度有限，长字段（如 URL、描述）会破坏对齐
- 30 字符兼顾可读性与信息密度
- 截断后加 `...` 明确标识

### 7. `header: bool = True` 用 `--no-header` 关闭

**依据**：P22 已为 `bool=True` 参数添加 `--no-name store_false` 支持，`--no-header` 自动可用。

### 8. CLI 异常上抛由子命令层统一捕获

**决策**：`read_csv`/`json_to_csv`/`select_columns` 抛 `FileNotFoundError`/`ValueError`，CLI 子命令层用 `except` 捕获并打印。

**依据**：保持纯函数无副作用，便于测试与复用。

## 代码实现情况

### 公共函数（6 个）

- `read_csv(filepath, has_header=True) -> tuple[list[str] | None, list[list[str]]]`：读取 CSV，返回表头与数据行
- `write_csv(filepath, rows, header=None) -> None`：写入 CSV，自动创建父目录
- `csv_to_json(filepath, indent=2) -> str`：CSV 转 JSON 字符串（首行作表头，行长不足补空）
- `json_to_csv(filepath) -> tuple[list[str], list[list[str]]]`：JSON 转 CSV（键并集作表头）
- `select_columns(rows, header, columns) -> tuple[list[str], list[list[str]]]`：按列名筛选与重排
- `format_table(header, rows, max_width=30) -> str`：格式化为对齐表格（含截断）

### CLI 子命令（4 个）

- `fcmd csvtool show <file> [--rows N] [--no-header]`：预览前 N 行
- `fcmd csvtool to-json <file> [--indent N]`：CSV 转 JSON
- `fcmd csvtool from-json <file> [--output OUT]`：JSON 转 CSV
- `fcmd csvtool select <file> <columns...> [--output OUT]`：按列筛选与重排

## 整合优化情况

### 覆盖率提升过程

初版 csvtool.py 覆盖率 98%，缺 1 处代码路径：
- Lines 326-328：`csvtool_select` 的 `except FileNotFoundError` 分支

补充测试：
- `test_select_nonexistent`：select 不存在文件时打印"文件不存在"

csvtool.py 覆盖率从 98% 提升至 100%。

### 测试调整

- **ruff RUF059**：`test_ragged_row` 中 `new_header` 未使用，改为 `_new_header`
- **test_to_json 调整**：框架输出含前缀（"> 'to-json' 开始执行..."）与后缀（"OK 'to-json' 成功..."），不能直接 `json.loads(out)`。改为先用 `out.index("[")` 与 `out.rindex("]")` 提取 JSON 数组部分再解析

## 测试验证结果

| 门禁 | 结果 |
|------|------|
| ruff check | 0 errors |
| ruff format --check | 80 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1282 passed, 1 skipped, 2 deselected |
| coverage | 99.35%（≥99.33% 基线），csvtool.py 100% |

测试套件结构（43 测试，8 类）：
- `TestRegistration`（2）：工具注册与子命令结构
- `TestReadCsv`（4）：有表头/无表头/空文件/不存在
- `TestWriteCsv`（3）：带表头/不带表头/自动创建父目录
- `TestCsvToJson`（5）：基本/自定义缩进/空文件/短行补齐/不存在
- `TestJsonToCsv`（6）：基本/缺失键/键顺序/不存在/非数组/元素非对象
- `TestSelectColumns`（4）：基本/重排/缺失列/不齐行
- `TestFormatTable`（5）：带表头/无表头/空/截断/不齐行
- `TestCLISubcommands`（14）：show 默认/自定义行数/无表头/不存在 + to-json/不存在 + from-json 默认/指定输出/非法 + select 打印/输出文件/缺失列/空文件/不存在

## 遗留事项

无。所有功能、边界场景、错误分支均已覆盖。

## 下一轮计划

P32 csvtool 工具已完成。后续可继续：
- 扫描 `.trae/req/` 处理未完成需求
- 新增 jsontool JSON 处理工具（pretty/minify/路径查询/键排序）
- 新增 archivex create 子命令（目录打包归档）
- 增强现有工具
