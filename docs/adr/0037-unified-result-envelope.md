# 0037 统一结果信封推广

- 状态：P0 / P1 已落地（P2 / P3 待办）
- 日期：2026-11-18
- 关联：需求卡片 [0037](../requirements/0037-unified-result-envelope.md)

## 背景

`core/result.py` 与 `core/exit_codes.py` 已提供 `fcmd-result/1` / `fcmd-error/1` 与退出码契约，
`--json` 也已作为 `json_output` 参数别名（`core/toolkit/args.py`）。但实际接入面很小，
而 MCP 适配器按 `agent_whitelist` 的 `force_json` **无条件追加 `--json`**（`mcp_adapter.py`）：

- 白名单 146 条，其中 45 条目标子命令接受 `--json`，**101 条不接受**（34 个工具）；
- 这些工具经 MCP 暴露后直接 `argparse: unrecognized arguments: --json`，退出 2；
- 复现：`fcmd casetool snake hello --json`、`fcmd which python --json`。

同时，框架层的用法错误（未知工具 / 缺子命令 / 未知子命令 / 解析失败）只输出文本，
JSON 调用方拿不到可判定的结果；部分工具在 JSON 模式下仍会向 stdout 写 rich 表格或
「提示:」行，破坏可整体 `json.loads` 的约定。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | `--json` 只增加机读路径，默认文本渲染与命令行为不变 | `core/result.py`、各工具 |
| D2 | 单值结果 `data={"result": ...}`；多值用语义字段（`lines` / `matches` / `algorithm`+`digest` 等）；人类文案不进 `data` | 各工具 |
| D3 | 接入的子命令把手写 `print("错误: …")` 收敛为 `report_error`，文本前缀统一为 `错误: `，退出码按契约（缺文件 4 / 非法参数 3 / 运行期 1），新错误码登记 `ERROR_CODE_KINDS`；这是本次唯一的行为变更 | 各工具、`core/exit_codes.py` |
| D4 | 白名单先对齐真实能力：暂不支持的 101 条置 `force_json=False`，接入后翻回 `True`；守卫测试断言 `force_json=True ⇒ param_schema 含 json_output` | `cli/agent_whitelist.py`、`tests/cli/test_mcp_adapter.py` |
| D5 | argv 含 `--json` 时，用法错误输出 `fcmd-error/1`（stderr，退出 2）；JSON 模式抑制子命令表与「提示:」行，argparse 英文文本用 `redirect_stderr` 捕获并只取 `error:` 行 | `core/toolkit/exec.py`、`core/toolkit/__init__.py`、`cli/main.py` |
| D6 | 自身已输出机读格式的工具不套信封：jsontool / xmltool / yamtool / nettool / peview edit / graph tree / csvtool `to-json`·`from-json`；filescan `scan` 暂用 `--format json` | 上述工具、白名单 |
| D7 | 分期收敛：P0 框架+对齐 → P1 白名单纯只读工具 → P2 文件/配置/网络/LLM/领域应用 → P3 收口（守卫、文档、写操作评估） | 本 ADR |

## 分期

### P0 框架 + 白名单对齐（已落地）

- 需求卡片与 ADR 立项，索引登记。
- `core/toolkit/exec.py`：`_json_requested(argv)`（逐 token 精确匹配 `--json`，遇 `--` 停止）；
  `_resolve_tool_target` / `_parse_tool_args` 的错误走 JSON 信封；JSON 模式不打印子命令表与提示行；
  argparse 失败仅保留 `error:` 行作为 `message`。
- `core/toolkit/__init__.py`（未注册工具）与 `cli/main.py`（未知工具 / 导入失败）同样支持信封。
- 白名单 101 条置 `force_json=False`，并加守卫测试。

### P1 白名单纯只读工具（已落地）

- P1a 计算与编码：mathtool、convtool、hashtool、codetool、urltool、calcdate。
- P1b 统计与文本：stattool、timetool、colortool、asciitool、casetool、txttool、regextool、regexvalidate。
- 每工具加 `json_output` → `emit_result` / `report_error`；白名单对应条目翻回 `force_json=True`；
  重生成 `_manifest_data.py`（源码指纹变化，HELP 文本不含子命令参数，不受影响）。

### P2 文件 / 配置 / 网络 / LLM / 领域应用（待办）

which、filecheck、pathtool、tomltool、inifile、archivex list、urlcheck、portcheck、iptool、
filescan scan（`--json` 与 `--format` 关系）、llmserve / llmbench / llmdoctor / llmenv / llmmodel、
dynacalc(report/compare/gates)、paramcad(digest/inspect)、paramesh(check/report)、lscalc status、
csvtool 非 JSON 子命令；`folderzip` 属写操作，单独评估。

### P2 落地进度

已接入（2026-11-24）：`which`、`pathtool`（show/rel/norm/diff）、`tomltool`
（get/keys/format/validate）、`inifile`（get/list/keys）；MCP 白名单
`_JSON_READY` 同步补齐这四个工具的条目。

待办：`filecheck`、`archivex list`、`csvtool select/merge`、`urlcheck`、
`portcheck`、`iptool`、`filescan scan`、LLM 与领域应用工具。

### P3 收口（待办）

README 与 ADR 状态回填、写操作工具接入评估、错误码补表审查。

## 验收

- `fcmd casetool snake hello --json` → `fcmd-result/1`，`data.result` 为转换结果，退出 0。
- `fcmd txttool count 不存在 --json` → stderr 单个 `fcmd-error/1`，退出 4。
- `fcmd nosuchtool --json` / `fcmd casetool --json` / `fcmd casetool snake --json`（缺参）→
  stdout 为空、stderr 单个 `fcmd-error/1`、退出 2。
- 不带 `--json` 时文本输出与既有行为一致（错误退出码按 D3 收敛除外）。
- 守卫测试：白名单 `force_json=True` 的条目均可解析出 `json_output`。
- `make check` 全绿，覆盖率 ≥95%。

## 风险与退出条件

- **退出码变更**：D3 会让原先「报错但退出 0」的子命令返回 4/3，依赖旧行为的脚本需调整；
  只针对本次接入的子命令生效，其余工具在 P2/P3 接入时同步收敛。
- **白名单维护成本**：`force_json` 必须与实现同步；已由守卫测试兜底，接入后忘记翻回会失败。
- **重复包裹**：自身输出机读格式的工具严禁再套信封（D6）；新增此类工具时先判定应归入例外。
- 若 MCP 适配器后续改为按 `param_schema` 自动决定是否注入 `--json`，白名单的 `force_json`
  可退化为纯声明，届时删除守卫中的翻回流程。
