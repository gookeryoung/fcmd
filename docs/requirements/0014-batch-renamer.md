# 需求卡片：批量重命名工具（规则流水线）

- 编号：0014（与 ADR 同号）
- 来源 / 提出人：用户
- 日期：2026-11-11
- 关联 ADR：[0014](../adr/0014-batch-renamer.md)

> 本卡片是**需求原文与约束编号的唯一副本**：ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> 在 `/root/fcmd/src/fcmd/apps` 下开发一个文件批量重命名工具，需要结合最佳实践和已有工具，实现复杂的规则，包括正则式匹配并调整其出现的位置

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 在 `src/fcmd/apps` 下开发 | 新增 `fcmd.apps.renamer` 包，CLI `renamer` + Tk 窗口 `renamerw` 双入口，设置写入 `~/.fcmd/ui/renamerw.json` | `src/fcmd/apps/renamer/`、`pyproject.toml` | `renamer` / `renamerw` 注册测试 |
| C2 | 文件批量重命名工具 | 支持文件与目录、多路径、目录递归展开、批内排序后统一求值 | `_core.collect_targets` / `build_preview` | 单测 + CLI 实测 |
| C3 | 实现复杂的规则 | 规则为有序流水线，至少覆盖正则替换、捕获组重排、匹配文本搬移、插入、大小写、计数器 | `_core.RenameRule` / `apply_rule` | 各规则单测与组合预览 |
| C4 | 正则式匹配并调整其出现的位置 | ① `reorder` 用 `{0}/{1}/{name}` 重排捕获片段；② `move` 把匹配文本搬到开头/结尾/锚点前/后/指定下标 | `_core._apply_reorder` / `_apply_move` | 单测覆盖全部锚点与捕获组模板 |
| C5 | 结合最佳实践 | 作用域区分名称/扩展名；预览标 ok/未变化/非法/冲突；目标被占用与批内重名拦截；执行两阶段改名支持互换与仅大小写改名；失败回滚；支持撤销 | `_core.build_preview` / `execute_plan` / `undo_report` | 冲突、互换、回滚、撤销单测 |
| C6 | 结合已有工具 | 复用 `fcmd.gui.window` 窗口骨架、`fcmd.gui.config` 设置持久化与主题切换；规则 JSON 可导入导出；登记静态工具清单 | `renamerw.py`、`fcmd/gui/*`、`_manifest_data.py` | GUI 纯逻辑测试、`make check-fast` |

## 现状影响面（候选，待 ADR 核实）

| # | 疑似受影响能力 | 候选位置 | 备注 |
|---|---|---|---|
| 1 | 现有单条改名工具 | `src/fcmd/tools/system/filerename.py` | 只做替换/插入/大小写，无预览、无位置重排 |
| 2 | cmder 多重命名 | `src/fcmd/apps/cmder/_rename.py`、`_ops.py` | 已有掩码/查找替换/预览，但耦合 cmder 面板，不适合独立批处理 |
| 3 | 工具发现与清单 | `src/fcmd/cli/_discovery.py`、`_manifest_data.py` | 新增 app 后需重新生成静态清单 |
| 4 | GUI 基座 | `src/fcmd/gui/window.py`、`config.py`、`theme.py` | 直接复用，不新增 GUI 框架代码 |

## 未决问题

- [x] “调整其出现的位置”指捕获组重排还是整体搬移？→ 两者都要（用户确认）
- [x] 交付形态？→ CLI + Tk GUI（用户确认）

## 卡点

- [x] 每条需求句都有 C 编号约束
- [x] 未决问题全部有结论
