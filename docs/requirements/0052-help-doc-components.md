# 需求卡片：帮助文档通用组件

- 编号：0052（卡片存 `docs/requirements/0052-help-doc-components.md`；与 ADR 同号）
- 来源 / 提出人：用户（会话内提出）
- 日期：2026-11-15
- 关联 ADR：[0052](../adr/0052-help-doc-components.md)

> 本卡片是**需求原文与约束编号的唯一副本**：ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> 增加帮助文档用的通用组件，结合最佳实践给出建议方案

> 全做

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 「帮助文档用的通用组件」 | 提供跨 CLI / GUI 共用的帮助数据层：分段（用法 / 示例 / 环境变量 / 退出码 / 文件 / 相关）、Markdown 解析、CLI epilog 渲染，且只依赖标准库 | `shared/helpdoc.py` | `tests/shared/test_helpdoc.py`；`import fcmd` 不导入 Qt / rich |
| C2 | 「通用组件」 | `ToolSpec` / `@fx.tool` 增加 `help_sections`，`--help` 与 `fcmd info` 同源渲染；未声明时输出与旧版逐字一致 | `core/toolkit/`、`cli/_builtins/info_cmd.py` | `tests/core/test_toolkit.py`、`tests/cli/test_cli.py`、清单帮助对照 |
| C3 | 「帮助文档」 | 提供 GUI 通用帮助窗口：Markdown 正文、二级 / 三级标题目录、搜索、可选示例表、外链打开；非模态 F1 | `gui/help.py` | `tests/gui/test_help.py`（离屏） |
| C4 | 「通用组件」 | 帮助菜单装配统一：`HelpMenuMixin` 把「使用帮助」插到「任务日志 → 关于」之前；renamerw / regexvalidatew 迁移到公共窗口 | `gui/help.py`、`apps/renamer/_renamerw_qt.py`、`tools/text/_regexvalidatew_qt.py` | `tests/apps/renamer/test_renamerw_qt.py`、`tests/tools/text/test_regexvalidatew_qt.py` |
| C5 | 「帮助文档」 | 包内 Markdown 为完整说明唯一来源（`src/fcmd/help/<tool>.md`），renamerw 文档从 `apps/renamer/help/` 归位；新增 `docs/help/README.md` 索引 | `src/fcmd/help/`、`docs/help/` | `tests/shared/test_helpdoc.py`（索引与链接检查） |
| C6 | 「结合最佳实践」 | 分层披露（`--help` 短 / `info` 中 / Markdown 长）、单一事实源、文档随包发布、相对链接可解析、防漂移测试 | 全链 | `make flow`；文档检查 |

## 现状影响面（候选，待 ADR 核实）

| # | 疑似受影响能力 | 候选位置 | 备注 |
|---|---|---|---|
| 1 | `--help` 仅渲染示例 | `core/toolkit/parser.py:186` | epilog 硬编码「示例:」 |
| 2 | 工具描述符缺少帮助分段 | `core/toolkit/spec.py:96` | 只有 `help` / `param_help` / `examples` |
| 3 | `fcmd info` 只展示参数与示例 | `cli/_builtins/info_cmd.py` | 需要新增分段段 |
| 4 | GUI 帮助各写各的 | `apps/renamer/_renamerw_qt.py:751`、`tools/text/_regexvalidatew_qt.py:65` | 重复对话框与菜单装配 |
| 5 | 帮助文档位置分散 | `apps/renamer/help/renamerw.md` | 需要统一包内目录与索引 |
| 6 | 帮助质量无门禁 | `tests/cli/_e2e_help.py:12` | 只断言非空、不崩溃 |

## 未决问题

- [x] 范围：按建议方案 P0–P2 全做（用户确认「全做」）。

## 卡点

- [x] 每条需求句都有 C 编号约束（无遗漏、无「待定」）
- [x] 未决问题全部有结论，需求方确认
