# 0049 只读环境变量查询：`fcmd env --vars`

- 状态：已落地
- 日期：2026-11-24
- 关联：[0037](0037-unified-result-envelope.md)（机读输出）、[0048](0048-config-format-convert.md)（对照补齐）

## 背景

对照 rscmd 的「读取单个环境变量」能力：fcmd 的 `fcmd env` 只输出运行时概览
（版本 / 平台 / 工具数 / 可选依赖），没有查询进程环境变量的入口；`setenv`
只能写，脚本里想读一个变量只能绕到 shell。用户不需要写入能力，只需要
只读列举与按前缀过滤。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | `fcmd env --vars [--prefix P]` 只读列出进程环境变量，按变量名排序，`KEY=VALUE` 每行一条；`--prefix` 大小写不敏感且隐含 `--vars` | 新增独立 `envvar` 工具（内建命令已有 `env`，拆成两个入口反而让模型难判断用哪个） | `cli/_builtins/env_cmd.py` |
| D2 | `--vars --json` 输出 `{prefix, count, vars}`；无匹配时人类可读输出 `(无匹配的环境变量)`，退出码仍为 0 | 空结果按错误码 4（「查不到」不是错误，与 `grep` 无匹配一致） | 同上 |
| D3 | 默认 `fcmd env`（不加 `--vars`）输出完全不变 | 把环境变量列表并进概览（一次性打印几百行，破坏概览的可读性） | 同上 |

## 验证

- `tests/cli/test_cli.py::TestBuiltinEnv`：前缀过滤（大小写不敏感）、
  `--prefix` 隐含 `--vars`、`--json` 结构、无匹配提示；既有概览用例不变。

## 后果

- 脚本可 `fcmd env --vars --prefix PATH --json` 取机读结果，不依赖平台差异
  （`set` / `env` / `printenv`）；
- 既有 `fcmd env` / `fcmd env --json` 输出与退出码零变化。

## 交接

- 其余小型缺口继续按批补齐：`pathtool join` 与绝对路径判定、
  `completion powershell`、`txttool count` 字节数、`stattool percentile`；
- 存量破坏性工具声明 `requires_confirmation` 属行为变更，仍未开启（见 0047）。
