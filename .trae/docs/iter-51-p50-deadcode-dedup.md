# iter-51 P50 死代码与重复工具清理

## 需求清单

- [x] 扫描 src/fcmd/ 全部模块识别未使用的导出/函数/类
- [x] 识别重复功能与可合并的工具
- [x] 清理不必要功能（测试除外不留冗余）
- [x] 避免臃肿
- [x] 测试覆盖率 ≥ 95%，0 lint/type 错误

## 迭代目标

按用户要求扫描 src/fcmd/ 全部模块（apis/、cli/、models/、核心模块），
识别死代码、重复功能、可合并工具，清理冗余以避免臃肿。

## 改动文件清单

- `src/fcmd/apis/dag.py`：修正 `subgraph_with_deps` docstring 中残留的
  `subgraph_by_names` 引用（该方法不存在，是历史命名遗留）
- `src/fcmd/apis/task.py`：从 `__all__` 移除 `TaskFn`（仅在 task.py 内部
  使用，外部从未 `from fcmd.apis.task import TaskFn`，保留类型本身供内部使用）
- `src/fcmd/cli/basetool.py`：**删除**（4 个子命令 base64/url/hex/html 是
  codetool 5 个子命令的真子集，函数命名风格不同但功能 100% 重复）
- `tests/test_cli_basetool.py`：**删除**
- `tests/test_cli_codetool.py`：迁移 basetool 测试中独有的边界用例
  （hex 奇数长度抛 ValueError、HTML 命名/数字实体反转义）
- `tests/test_cli.py`：从 `_MAIN_ENTRY_TOOLS` 移除 `("basetool", ...)`
- `pyproject.toml`：移除 `[project.scripts]` 中的 `basetool` 入口
- `README.md`：删除 basetool 行；修正 codetool 描述错误（原误写为
  "代码统计"，实际是"编解码"），迁移到"文本与编码"小节

## 关键决策与依据

### 决策 1：basetool 与 codetool 合并方向

basetool 提供 4 个子命令（base64/url/hex/html），codetool 提供 5 个
（base64/url/hex/rot13/html，多 rot13）。同名子命令功能 100% 相同，
仅函数命名风格不同（`base64_encode/decode` vs `encode_base64/decode_base64`）。
basetool 是 codetool 的真子集，明显冗余。

合并方向：删除 basetool 保留 codetool（功能更全）。已征求用户确认。

### 决策 2：hashtool vs hashfile 不合并

hashtool（md5/sha1/sha256/sha512 文本哈希）与 hashfile（f/d 文件流式哈希）
互补而非重复，处理对象不同（字符串 vs 文件，后者支持大文件分块读取）。
保留两者。

### 决策 3：filerename.case / casetool / txttool.case 不合并

- filerename.case：文件名大小写转换（lower/upper/title），操作文件名主干
- casetool：字符串命名风格转换（snake/camel/pascal/kebab）
- txttool.case：文本文件内容大小写转换（upper/lower/title/capitalize/swapcase）

三者领域不同（文件名/字符串/文本文件内容），不构成重复。

### 决策 4：envdev 的 hidden 子命令非冗余

envdev 的 setup-rust/download-rustup/install-rust/setup-bun/install-bun
均为 DAG 中间步骤，被 `rust`/`js-bun` 聚合子命令通过 `needs` 引用。
属于 DAG 编排模式的标准做法，非冗余。

### 决策 5：两个 run_command 同名不同签名保留

- `fcmd.command.run_command(spec: TaskSpec)`：TaskSpec 内部执行器，
  仅被 `apis/task.py` 调用
- `fcmd.models.command.run_command(cmd: list[str])`：CLI 工具通用
  subprocess 包装，被 13 个 cli 模块调用

命名重叠但用途清晰，签名不同，保留两者。

## 代码实现情况

### 死代码清理

1. `dag.py` L365：删除 docstring 中 "与 :meth:`subgraph_by_names` 不同"
   残留句，直接描述 `subgraph_with_deps` 自身行为
2. `task.py` L67：从 `__all__` 列表移除 `"TaskFn"`，类型本身保留（内部使用）

### 重复工具合并

1. 删除 `src/fcmd/cli/basetool.py`（264 行）
2. 删除 `tests/test_cli_basetool.py`（255 行，35 个测试）
3. `tests/test_cli_codetool.py`：补充 3 个边界用例
   - `TestHex.test_decode_odd_length_raises`：奇数长度 hex 抛 ValueError
   - `TestHtml.test_unescape_named_entities`：命名实体反转义
   - `TestHtml.test_unescape_numeric_entities`：数字实体反转义
4. `tests/test_cli.py` `_MAIN_ENTRY_TOOLS`：移除 basetool 条目
5. `pyproject.toml` `[project.scripts]`：移除 `basetool = "fcmd.cli.basetool:main"`
6. `README.md`：
   - "文本与编码"小节：删除 basetool 行，新增 codetool 行（编解码描述）
   - "开发环境"小节：删除错误的 codetool 行（原误写"代码统计"）

## 整合优化情况

- README codetool 描述错误（"代码统计"，与实际功能"编解码"完全不符）一并修正
- 测试用例迁移保证边界覆盖不丢失

## 测试验证结果

- `uv run ruff check`：All checks passed
- `uv run ruff format --check`：150 files already formatted
- `uv run pyrefly check`：0 errors (43 suppressed)
- `uv run pytest -m "not slow" --cov=fcmd --cov-fail-under=95`：
  2431 passed, 2 deselected, coverage 98.18%

测试数从 2465 降至 2431（净减 34，其中删除 35 个 basetool 测试，
新增 3 个 codetool 边界用例）。覆盖率 98.04% → 98.18%。

## 遗留事项

无。

## 下一轮计划

无（用户初始要求已闭环交付）。
