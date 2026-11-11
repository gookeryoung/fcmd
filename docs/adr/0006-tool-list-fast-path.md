# 0006 工具列表快路径（静态元数据 + 内容指纹回退）

- 状态：已落地
- 日期：2026-11-11

## 背景

`fcmd` 无参（及 `--help`）列出全部工具时走全量发现：扫描并导入 `tools/` 下全部
Python 工具模块。实测（WSL2 Ubuntu-24.04，Python 3.13，独立进程 15 次取中位数，
热页缓存）列表耗时 174.4ms，其中：

| 阶段 | 耗时 | 占比 |
|---|---|---|
| 导入工具模块及其第三方依赖（rich / yaml / core） | ~76ms | 65% |
| core / console 导入链 | ~26ms | 22% |
| pkgutil 扫描 / find_spec / 别名表 | ~4ms | 3% |
| ToolSpec 构建 | 1.0ms | 1% |

即热点是「为列表导入全部工具模块」，而列表实际只需要工具名、别名、描述三样
静态元数据。

## 决策

### D1 列表元数据在生成期固化进静态清单

`_manifest_data.py` 增加 `DESCRIPTIONS`（每工具一行描述）与 `DIGEST`（工具源码
内容指纹）。描述在生成期直接调用运行期的 `_discovery.tool_description`，保证与
实际全量发现同口径；`fcmd` 列表据此直接渲染，不导入任何工具模块。

导入失败的工具仍会出现在列表里（描述为空），因此清单按
`set(_TOOL_ALIASES.values())` 全量收录，缺失的以 `kind="unknown"` 补位，单工具
快路径遇到 `unknown` 继续回退全量发现。

### D2 两级校验：布局指纹（单工具）+ 内容指纹（列表）

- 单工具快路径只要模块路径，继续只校验 `LAYOUT`（目录形状），零额外 I/O 成本。
- 列表需要文案，布局不变而内容变化（改描述 / 别名）会让清单过期，故
  `_manifest.load_strict()` 追加 `DIGEST` 校验：逐文件 `blake2b` 读取工具源码，
  热缓存约 2ms，远低于全量发现的 ~110ms。

`DIGEST` 用内容而非 mtime，保证 wheel / 另一台机器安装后仍然有效（生成期与
运行期内容一致即可命中）。

### D3 任何不可信信号都回退全量发现

以下情形 `list_rows()` 返回 `None`，调用方回退 `ensure_tools_discovered()`：

- 清单模块缺失 / 导入异常、`SCHEMA` 与运行期不一致
- 目录布局与 `LAYOUT` 不一致（新增 / 删除工具模块）
- 内容指纹与 `DIGEST` 不一致、源码读取失败（`OSError`）

回退路径就是改动前的原实现，语义与文案不变；清单只影响速度。

## 落地状态

- `_manifest.list_rows()` / `load_strict()` / `tool_digest()` 与
  `_discovery.list_tool_rows()` 已落地，`main.FcmdApp._list_tools` 优先走快路径。
- 实测列表 174.4ms → 64.0ms（-63%）；`--version`、`fcmd <tool>` 与
  `fcmd pymake` 耗时在噪声范围内不变。
- 测试覆盖快路径与三条回退路径，并断言快慢路径输出逐字一致、描述与
  `tool_description` 逐条一致。

## 交接

- 改工具名 / 别名 / 描述后必须重生成清单：
  `uv run python -m fcmd.cli._manifest_data`，否则列表会自动回退（变慢但不报错）。
- 若生成清单时工作区存在**其他未提交**的工具源码改动，`DIGEST` 会指向这些
  未提交内容，提交后 CI 的「指纹与源码一致」断言会红——请在干净工作区重生成。
