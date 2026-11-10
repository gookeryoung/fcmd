# 0002 DSL 与 tools 的边界

- 状态：已落地（P2-1 图片域试点；archive / text / net / calc / hash / data 迁移中）
- 日期：2026-11-10

## 背景

DSL 最初只为「声明式编排」而生，但历史上把大量业务实现直接写进了 `dsl/actions/*.py`，同一能力在 `tools/` 与 `dsl/` 各有一份（如图片处理）。这造成：

- 修复要改两处，容易出现行为漂移；
- `dsl` 包承担了与其定位无关的 IO / 计算逻辑，测试与依赖边界模糊；
- 工具入口（Python 模块、GUI 模块、DSL 命令）复用困难。

## 决策

采用 D1 = A（彻底根治）：

1. `dsl` 只保留两类原语与编排能力：`exec`（子进程 / 命令执行）与 `compute`（输出采集后计算并注入参数），以及 `@action` 声明、参数 schema、DAG 编排。
2. 所有业务实现迁入 `tools/<域>`，一个功能域只保留一份实现。
3. 推荐形态：
   - `tools/<域>/<域>base.py`（图片域为 `tools/media/mediabase.py`）承载纯实现，不含 `@action`；
   - `dsl/actions/<域>.py` 只保留 `@action` 声明与参数转接，调用 `base` 中的函数；
   - `tools/<域>` 下的 CLI / GUI 入口直接复用 `base`，不复制逻辑。
4. 迁移顺序按域独立提交、可回滚，命令行为零破坏。图片域已于 P2-1 完成试点：`mediabase.py` 承载全部实现（约 925 行），`dsl/actions/media.py` 缩减为声明与转接（约 265 行）。
5. 入口命名（D2）：工具规范名（`imagetool` / `jsontool` / `piptool` / `gittool`）为官方入口并生成同名全局脚本；历史短名（`imaget` / `jsont` / `pipt` / `gitt`）保留为显式别名，同样保留脚本。与系统命令同名的 `taskkill` / `which` 例外：不生成同名全局脚本（避免遮蔽系统命令），只保留短名 `taskk` / `wch` 脚本，规范名仍可通过 `fcmd taskkill` / `fcmd which` 访问。`resolve_tool` 只做别名精确匹配，取消 `tool` / `cmd` 后缀隐式推断。

## 迁移清单

| 域 | 目标实现位置 | 状态 |
|---|---|---|
| media | `tools/media/mediabase.py` | 完成（P2-1） |
| archive | `tools/archive/archivebase.py` | 完成 |
| text | `tools/text/textbase.py` | 完成 |
| net | `tools/net/netbase.py`（通用探测在 `core/shared/net.py`） | 完成 |
| calc | `tools/calc/calcbase.py`、`calcdatebase.py` | 完成 |
| hash | `tools/hash/hashbase.py` | 完成 |
| data | `tools/data/database.py` | 完成 |

## 落地状态

- P2-1：图片域完成，`dsl/actions/media.py` 仅剩声明与转接；`tools/media/img2icow.py` 复用 `mediabase` 的尺寸解析与转换函数。
- P2-2：入口命名统一完成，短名全部为显式别名并有回归测试；后缀推断已删除；`taskkill` / `which` 不再生成同名全局脚本。
- P2-3：archive / text / net / calc / hash / data 六域全部迁移完成，`dsl/actions/*.py` 只保留 `@action` 声明与参数转接，无第二份实现。
- text 域迁移时发现 `tools` 反向依赖 `dsl/actions/fileops.py` 的文本读取 helper，已下沉到 `core/shared/textio.py`（`is_binary_file` / `read_text_lines`），`fileops` 与 `textbase` 共用。

## 交接

- 迁移新域时先机械搬迁实现（保持函数名与行为），再让 `dsl/actions` 改为转接，最后更新测试 patch 目标到新实现模块。
- 判断是否属于「原语」：若动作只是把参数拼成命令（`cmd`）或把采集输出计算后注入（`compute`），可留在 DSL；否则实现必须进 `tools/<域>`。
- 新增工具时直接按规范名注册入口与脚本，短名只在确有必要时以显式别名形式补充。
