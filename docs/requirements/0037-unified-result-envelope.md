# 需求卡片：统一结果信封（--json 机读输出）

- 编号：0037（与 ADR 同号）
- 来源 / 提出人：用户
- 日期：2026-11-18
- 关联 ADR：[0037](../adr/0037-unified-result-envelope.md)

> 本卡片是**需求原文与约束编号的唯一副本**：ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> 按推荐做法

> 立项开展

> 注：上句中的「推荐做法」指前序对话中的推荐——「统一结果信封先只在新增 `--json` 字段上落地」。用户先选择完成缓存 TTL/容量优化，随后指示「立项开展」，即以正式立项方式推进统一结果信封。

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 立项开展 | 以需求卡片 + ADR 形式确定口径、分期（P0–P3）与例外；ADR 登记索引 | `docs/adr/0037-*.md`、`docs/adr/README.md` | `make adr-check`、评审通过 |
| C2 | 按推荐做法（先在 `--json` 上落地） | `--json` 为增量开关：默认文本输出、命令行为保持不变 | `src/fcmd/core/result.py` | 存量文本用例全绿 |
| C3 | 统一结果信封 | 成功输出 `fcmd-result/1`（`schema/ok/tool/subcommand/data`），错误输出 `fcmd-error/1`（含 `kind/code/message/exit_code`）到 stderr | `core/result.py`、各工具模块 | JSON 信封字段断言 |
| C4 | 统一结果信封 | `data` 约定：单值 `{"result": ...}`，多值用语义字段；人类文案不进 `data` | 各工具模块 | 逐工具 JSON 用例 |
| C5 | 工具可被 agent 调用 | MCP 白名单 `force_json=True` 仅当目标子命令接受 `--json`；不满足者先置 `False`，接入后翻回；守卫测试防漂移 | `cli/agent_whitelist.py`、`tests/cli/test_mcp_adapter.py` | 守卫用例通过 |
| C6 | 统一结果信封 | 用法错误（未知工具/缺子命令/未知子命令/解析失败）在 `--json` 下同样输出错误信封，且 stdout 不含子命令表或提示行 | `core/toolkit/exec.py`、`core/toolkit/__init__.py`、`cli/main.py` | 4 条 JSON 用法错误用例 |
| C7 | 立项开展 | 本轮落地 P0（框架+对齐）与 P1（14 个只读工具）；P2/P3 在 ADR 记为待办 | ADR、`tools/{text,calc,crypto}` | 提交记录与 ADR 状态 |

## 现状影响面（候选，待 ADR 核实）

| # | 疑似受影响能力 | 候选位置 | 备注 |
|---|---|---|---|
| 1 | 已有信封 API | `src/fcmd/core/result.py`、`core/exit_codes.py` | 直接复用，不改 schema 版本 |
| 2 | 工具调度与用法错误 | `core/toolkit/exec.py`、`core/toolkit/__init__.py`、`cli/main.py` | 错误路径当前只出文本 |
| 3 | MCP 白名单 | `cli/agent_whitelist.py` | 146 条中 101 条目标不接受 `--json` |
| 4 | 只读工具（P1） | `tools/text/*`、`tools/calc/*`、`tools/crypto/*` | 14 个工具约 50 个子命令 |
| 5 | 静态清单 | `cli/_manifest_data.py` | 源码指纹变化需重生成，HELP 不受影响 |

## 未决问题

- [x] 默认退出码是否一并收敛？→ 接入的子命令按 `exit_codes` 契约收敛（用户已批准 ADR D3）
- [x] 自身已输出 JSON/XML/YAML 的工具怎么办？→ 不套信封，保持 `force_json=False`（ADR D6）
- [x] 如何防白名单与真实能力再次漂移？→ 守卫测试（ADR D4）

## 卡点

- [x] 每条需求句都有 C 编号约束
- [x] 未决问题全部有结论
