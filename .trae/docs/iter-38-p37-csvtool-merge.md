# P37 - csvtool merge 子命令

## 需求清单

- [x] 为 csvtool 新增 `merge` 子命令，支持多 CSV 文件按列合并
- [x] `union` 模式：取所有 CSV 列的并集（保持首次出现顺序），缺失列填空字符串
- [x] `intersection` 模式：取列交集（以第一个 CSV 顺序为准，仅保留共有列）
- [x] 两种模式均保留所有行（纵向拼接）
- [x] 支持 `--output` 输出到文件，默认打印到标准输出
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage）

## 迭代目标

为 csvtool 补齐多文件合并能力，与现有 show/to-json/from-json/select 形成完整五子命令工具。合并语义参考 pandas DataFrame merge 的外/内连接思路，但简化为纵向拼接（UNION ALL 语义）+ 列并集/列交集，避免引入 join key 概念保持 CLI 简洁。

## 改动文件清单

| 文件 | 类型 | 行数变化 | 说明 |
|------|------|---------|------|
| `src/fcmd/cli/csvtool.py` | 修改 | +100 | 新增 `merge_csvs` 公共函数 + `_merge_headers_union`/`_merge_headers_intersection` 辅助 + `merge` CLI 子命令；`__all__` 新增 `merge_csvs` |
| `tests/test_cli_csvtool.py` | 修改 | +180 | 新增 `TestMergeCsvs` 类（13 测试）+ `TestCLISubcommands` 追加 6 个 merge CLI 测试；修改 `test_subcommands` 期望为五子命令 |

## 关键决策与依据

### 1. 提取 `_merge_headers_union`/`_merge_headers_intersection` 辅助函数
- **依据**：初版将 union/intersection 表头计算逻辑内联在 `merge_csvs` 中，触发 ruff PLR0912（分支数 13 > 12）与 PLR5501（`else: if` 应改 `elif`）。
- **实现**：将两种模式的表头计算分别提取为独立函数，`merge_csvs` 仅根据 `mode` 分派调用，主函数分支数降到阈值以下。
- **额外收益**：辅助函数单一职责，便于后续单独测试。

### 2. union 模式列顺序：保持首次出现顺序
- **依据**：与 P32 `json_to_csv` 表头收集逻辑一致（`seen` 去重 + 顺序列表）。用户预期 CSV 列顺序稳定可预测。
- **实现**：`_merge_headers_union` 用 `seen: set` + `merged: list` 双结构，遍历所有 CSV 表头，新列追加到 merged 并登记 seen。

### 3. intersection 模式列顺序：以第一个 CSV 顺序为准
- **依据**：交集无自然顺序，需选定锚点。第一个 CSV 是用户传入的主文件，顺序符合用户预期。pandas `intersection` 也采用左表顺序。
- **实现**：`_merge_headers_intersection` 先用 set 求交集，再按第一个 CSV 表头顺序过滤。
- **边界**：第一个 CSV 为空文件时 `headers[0]` 为 `[]`，直接返回 `[]`（不进入集合运算）。

### 4. 行长不足补空字符串
- **依据**：与 P32 `select_columns`/`csv_to_json` 的行处理一致（rule-11 一致性）。CSV 行可能列数不足（脏数据），不应抛错。
- **实现**：`row[i] if i is not None and i < len(row) else ""`——`i is None` 表示该列在此 CSV 中不存在（填空），`i >= len(row)` 表示该行数据不全（填空）。

### 5. 两种模式均保留所有行（纵向拼接）
- **依据**：合并语义为 UNION ALL（保留重复行），不是 UNION（去重）。去重需计算所有列哈希，成本高且语义模糊（用户可能想保留重复）。
- **实现**：`for header, rows in zip(headers, all_rows):` 遍历每个 CSV，将重排后的行追加到 `merged_rows`。

### 6. CLI 错误处理：捕获 ValueError/FileNotFoundError 打印提示
- **依据**：与 csvtool 其他子命令（select/from-json）一致，CLI 层不抛错，打印提示后 return（exit 0）。
- **实现**：`except (ValueError, FileNotFoundError) as e: print(str(e)); return`。

## 代码实现情况

### csvtool.py 新增结构（100 行）

```
__all__ 新增：merge_csvs

辅助函数（2 个）：
  _merge_headers_union(headers) -> list[str]
  _merge_headers_intersection(headers) -> list[str]

公共函数：
  merge_csvs(files, mode="union") -> tuple[list[str], list[list[str]]]
    - 校验 files >= 2 + mode 合法
    - 读取所有 CSV（首行视为表头，空文件 header=[]）
    - 按 mode 分派表头计算
    - 重排每个 CSV 的行到 merged_header 顺序

CLI 子命令：
  @fcmd.tool("csvtool", subcommand="merge")
  csvtool_merge(files: list[Path], mode: str = "union", output: str = "") -> None
    - 捕获 ValueError/FileNotFoundError 打印提示
    - --output 写文件，否则 format_table 打印
```

### 测试覆盖（19 新测试）

| 类 | 测试数 | 覆盖点 |
|----|--------|--------|
| TestMergeCsvs | 13 | union 基本 + union 默认 + union 首次出现顺序 + intersection 基本 + intersection 无公共列 + intersection 第一个 CSV 为空 + intersection 顺序以第一个 CSV 为准 + 少于 2 文件 + 无效 mode + 文件不存在 + 空 CSV + 行长不足补空 + 三文件 union |
| TestCLISubcommands（追加） | 6 | merge union 打印 + merge intersection 打印 + merge --output 输出文件 + merge 仅 1 文件提示 + merge 文件不存在提示 |

## 整合优化情况

- 复用 P32 的 `read_csv`/`write_csv`/`format_table`，无重复读写逻辑。
- union 表头收集与 P32 `json_to_csv` 的键收集模式一致（`seen` + 顺序列表）。
- 行长不足补空字符串与 P32 `select_columns`/`csv_to_json` 的行处理一致。
- `_merge_headers_union`/`_merge_headers_intersection` 提取后，`merge_csvs` 主函数分支数符合 ruff PLR0912 阈值。

## 测试验证结果

| 检查项 | 结果 |
|--------|------|
| ruff check | All checks passed |
| ruff format --check | 86 files already formatted |
| pyrefly check | 0 errors (35 suppressed, 10 warnings) |
| pytest | 1442 passed, 1 skipped, 2 deselected |
| 总覆盖率 | 99.38%（与 P36 基线一致，未下降） |
| csvtool.py 覆盖率 | 100%（181 stmts, 94 branches, 0 miss） |

### 修复过程

#### 1. ruff PLR0912 + PLR5501
- 错误：`merge_csvs` 分支数 13 > 12，且 `else: if` 应改 `elif`
- 修复：提取 `_merge_headers_union`/`_merge_headers_intersection` 辅助函数，主函数仅 `if mode == "union": ... else: ...` 分派
- 结果：分支数降至阈值以下，PLR5501 自动消除

#### 2. 测试 `test_intersection_preserves_first_order` 期望错误
- 原期望：`["c", "b", "a"]`（误以为 b 在交集中）
- 实际：b 不在第二个 CSV 表头中，交集为 {c, a}，按第一个 CSV 顺序得 `["c", "a"]`
- 修复：更正期望与注释

#### 3. 测试 `test_intersection_first_csv_empty` 期望错误
- 原期望：`rows == []`
- 实际：第二个 CSV 有 1 行数据，intersection 后列被清空但行保留，得 `[[]]`
- 修复：更正期望为 `[[]]`，注释说明「行保留但列被清空」

## 遗留事项

- 无

## 下一轮计划

候选方向（按优先级）：
1. jsontool 新增 `query` 子命令（jq 简化版，支持点路径 + 过滤表达式）
2. csvtool 新增 `sort` 子命令（按列排序，支持升序/降序/数值/字符串）
3. csvtool 新增 `filter` 子命令（按列值过滤行，支持简单表达式）
4. 提取 `_should_skip_part` 到 `_common.py`（若第三处出现）
5. 增强现有工具的边界场景测试
