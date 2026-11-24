# 0047 破坏性写操作的确认闸门与 `filetime` 工具

- 状态：已落地（确认闸门 + `filetime`）；后续项见「交接」
- 日期：2026-11-24
- 关联：[0037](0037-unified-result-envelope.md)（结果信封 / `--json`）、[0045](0045-fx-tool-execution-primitives.md)（`fx.tool` 原语）

## 背景

对照 rscmd 的契约层（`--dry-run` / `--confirm` 全局参数 + `CONFIRM_REQUIRED` +
工具元数据）后，fcmd 有两处结构性缺口：

1. **确认闸门只存在于个别工具**：`CONFIRM_REQUIRED` 错误码早已登记
   （`core/exit_codes.py`），但 CLI 侧没有统一入口；写操作各自用
   `--preview` / `--force` / `--dry-run` 表达，模型无法从 schema 判断
   某条命令是否具有破坏性。
2. **CLI 缺少文件 mtime/atime 的读/写能力**：`filedate` 是文件名日期前缀
   （同名不同义），时间戳写入只在 `cmderw` GUI 的「批量修改时间戳」里，
   CLI 无法脚本化。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | `ToolSpec.requires_confirmation`（默认 `False`）声明子命令必须显式确认；仅在为 `True` 时给 parser 增加 `--confirm`，未确认且非 `--dry-run` 时按 `CONFIRM_REQUIRED`（退出 2）拒绝 | 所有工具无条件暴露 `--confirm`（多数工具用不上，帮助文本噪音）；按工具名维护一份「危险工具表」（与实现脱节，新增工具易漏） | `core/toolkit/spec.py`、`__init__.py`、`parser.py`、`routing.py` |
| D2 | 新增 `filetime get/set`：`get` 读 mtime/atime/ctime（本地时间 + UTC + Unix 秒），`set` 写 mtime（可选 atime），`set` 声明 `requires_confirmation=True` | 扩展 `filedate` 增加时间戳子命令（两套语义混在一个工具里）；只在 GUI 保留（无法脚本化） | `tools/system/filetime.py`、`infras/fs/timestamps.py` |
| D3 | `fcmd info --json` 的每个子命令增加 `json`（是否已接入 `--json`）与 `requires_confirmation` 字段 | 让模型从 `params` 里自行推断（`--json` 是否存在可以推，破坏性无法推） | `cli/_schema.py` |

时间戳文本只接受两种形态，避免歧义：

- `@<unix>`：Unix 秒（`2025` 这类纯数字不会被误判为 1970 年）；
- ISO 8601：`YYYY-MM-DD`、`YYYY-MM-DD HH:MM[:SS]`，可带 `Z` 或时区偏移；
  无时区按本机时区解释，与终端 `dir` / `ls` 一致。

## 验证

- `tests/tools/system/test_cli_filetime.py`：注册、人类可读 / `--json` 输出、
  多文件、缺失文件（4）、未确认拒绝（2 与 `CONFIRM_REQUIRED` 信封）、
  确认后写入、独立 `--atime`、ISO/`Z`/本地时区、`--dry-run` 不落盘、
  非法/空 `mtime`、非法 `atime`、`utime` 失败（1）；
- `tests/infras/test_fs_timestamps.py`：`@unix`、空串、非法 `@`、日期、
  `Z`、偏移、非法 ISO、`format_epoch` 字段、`file_stamps` 三时间戳；
- `tests/core/test_toolkit_confirm.py`：闸门拒绝/放行/`--dry-run` 放行、
  JSON 错误信封、未声明的工具不暴露 `--confirm`、schema 两个新字段；
- `tests/cli/test_tools_manifest.py`：静态清单随新工具重生成
  （`TOOL_COUNT` 115 → 116，`SUBCOMMAND_TOTAL` 398 → 400）。

## 后果

- 新增写工具只需 `requires_confirmation=True` 即获得统一的 `--confirm`
  闸门与 schema 标记，不再各写一套达成方式；
- 现有工具行为零变化（字段默认 `False`，不改变任何 parser）；
- `filetime` 与 `filedate` 语义分离：前者改元数据，后者改文件名。

## 交接

- 存量破坏性 / 写操作工具（`filerename`、`zipencrypt`、`folderzip`、
  `archivex`、`reshack edit`、`csvtool from-json`、`fonttool install`、
  `filesync apply`、`setenv`、`taskkill`、`writefile`）尚未逐个声明
  `requires_confirmation`；逐个开启属于行为变更，需单独评估并告知用户；
- 对照分析中的其余缺口按「价值 / 成本」排期：只读环境变量查询、
  `pathtool join/abs`、`completion powershell`、`txttool count` 字节数、
  `stattool percentile`（TOML/YAML 跨格式转换见 [0048](0048-config-format-convert.md)）；
  `--json` 的空缺工具按 [ADR 0037](0037-unified-result-envelope.md) P2 继续接入；
- 目录锁、通用断点续传、缓存框架、PyO3 绑定明确不补（fcmd 单进程 CLI +
  原子写，下载/缓存策略按 app 语义下沉）。
