# 0049 小型只读能力补齐：`env --vars`、`pathtool join/kind`、`completion powershell`、`txttool count` 字节数、`stattool percentile`

- 状态：已落地
- 日期：2026-11-24
- 关联：[0037](0037-unified-result-envelope.md)（机读输出）、[0048](0048-config-format-convert.md)（对照补齐）

## 背景

对照 rscmd 的若干只读小能力：fcmd 的 `fcmd env` 只输出运行时概览
（版本 / 平台 / 工具数 / 可选依赖），没有查询进程环境变量的入口；`setenv`
只能写，脚本里想读一个变量只能绕到 shell。`pathtool` 能解析 / 规范化 /
算相对路径 / 比差异，但没有「拼接片段」与「是否绝对路径」这两个最常用的
判定入口。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | `fcmd env --vars [--prefix P]` 只读列出进程环境变量，按变量名排序，`KEY=VALUE` 每行一条；`--prefix` 大小写不敏感且隐含 `--vars` | 新增独立 `envvar` 工具（内建命令已有 `env`，拆成两个入口反而让模型难判断用哪个） | `cli/_builtins/env_cmd.py` |
| D2 | `--vars --json` 输出 `{prefix, count, vars}`；无匹配时人类可读输出 `(无匹配的环境变量)`，退出码仍为 0 | 空结果按错误码 4（「查不到」不是错误，与 `grep` 无匹配一致） | 同上 |
| D3 | 默认 `fcmd env`（不加 `--vars`）输出完全不变 | 把环境变量列表并进概览（一次性打印几百行，破坏概览的可读性） | 同上 |
| D4 | `pathtool join <片段...>` 按 `Path` 语义拼接后规范化输出；`pathtool kind <path>` 输出「绝对路径 / 相对路径」 | `join` 只做字符串拼接（Windows 混合分隔符时结果不可用）；`kind` 用退出码 0/1 表达判定（与既有只读查询工具「打印结论、退出 0」不一致） | `infras/fs/path.py`、`tools/system/pathtool.py` |
| D5 | `fcmd completion --shell powershell` 生成 `Register-ArgumentCompleter -Native` 脚本：第一层为内建命令 + 工具名/别名 + `--version`，第二层按「首参数命中工具」查子命令表 | 只为 PowerShell 设动态补全（每次 Tab 调 `fcmd info`，冷启动慢）；只补第一层（子命令是 fcmd 最常用的 Tab 场景） | `cli/_completion_scripts.py`、`cli/_builtins/completion_cmd.py` |
| D6 | `txttool count` 增加 `bytes`（文件磁盘字节数，`stat().st_size`），人类可读输出多一行「字节数」 | 用 `len(text.encode())`（丢失 BOM / 换行差异，与 `dir` / `ls` 看到的字节数不一致） | `tools/text/txttool.py` |
| D7 | `stattool percentile <file> [--p 0-100]`（默认 50），线性插值口径与 numpy `linear` 一致；`p` 越界按 `INVALID_ARGUMENT`（3） | 新增 `quantile` 子命令（同一能力两个名字）；用最近秩口径（与主流统计库默认不一致） | `infras/calc.py`、`tools/calc/stattool.py` |

`--shell` 从三值扩展到四值：原先取值非法的 `powershell` 现在是合法目标
（这是有意行为变更，见「后果」）；非法 shell 的用法错误仍由 argparse 以
退出码 2 拒绝。

## 验证

- `tests/cli/test_cli.py::TestBuiltinEnv`：前缀过滤（大小写不敏感）、
  `--prefix` 隐含 `--vars`、`--json` 结构、无匹配提示；既有概览用例不变。
- `tests/tools/system/test_cli_pathtool.py::TestJoinAndKind`：`join_path`
  基本拼接 / 消 `..` / 绝对片段覆盖 / 空片段报错，`join` 与 `kind`
  两个子命令的人类可读输出。
- `tests/cli/test_completion_scripts.py`：PowerShell 脚本结构（补全注册、
  第一层、子命令表、无子命令工具不入门映射）、单引号转义、全无子命令；
  `tests/cli/test_cli.py::TestBuiltinCompletion`：`--shell powershell`
  端到端输出，非法 shell 改用 `tcsh` 断言退出码 2。
- `tests/tools/text/test_cli_txttool.py`：`count` 人类可读新增「字节数」
  （ASCII 与中文两例，按磁盘字节计）；`tests/cli/test_json_envelope.py`
  同步断言 `bytes` 字段。
- `tests/tools/calc/test_cli_stattool.py`：`stat_percentile` 的 P50 与中位数
  一致、0/100 边界、线性插值、单值、空列表、越界；CLI 默认 `--p 50`、
  显式 `--p`、越界退出 3、`--json` 信封。

## 后果

- 脚本可 `fcmd env --vars --prefix PATH --json` 取机读结果，不依赖平台差异
  （`set` / `env` / `printenv`）；
- 既有 `fcmd env` / `fcmd env --json` 与 `pathtool` 前四个子命令输出、
  退出码零变化；
- **`fcmd completion --shell powershell` 从「非法取值（退出 2）」变为
  「正常生成脚本（退出 0）」**，这是本次唯一的用户可见行为变更；依赖
  旧行为的调用方需改用其他非法值来测试参数校验；
- `txttool count --json` 的 `data` 新增 `bytes` 字段（既有 `lines` /
  `words` / `chars` 不变），严格比对旧字段集的机读方需放宽断言；
- `stattool` 新增 `percentile` 子命令，其余子命令不变。

## 交接

- 对照清单中的小项已全部补齐；下一步按 [0037](0037-unified-result-envelope.md)
  P2 把剩余工具的 `--json` 接入（按域分批，先做 MCP 白名单里的重点工具）；
- 存量破坏性工具声明 `requires_confirmation` 属行为变更，仍未开启（见 0047）。
