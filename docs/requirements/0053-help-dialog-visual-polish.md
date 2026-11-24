# 需求卡片：帮助窗口视觉美化

- 编号：0053（卡片存 `docs/requirements/0053-help-dialog-visual-polish.md`；与 ADR 同号）
- 来源 / 提出人：用户（会话内提出）
- 日期：2026-11-15
- 关联 ADR：[0053](../adr/0053-help-dialog-visual-polish.md)

> 本卡片是**需求原文与约束编号的唯一副本**：ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> 帮助文档组件的GUI不够美化，请结合最佳时间和项目已有基础组件进行美化，给出方案

> 可以

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 「结合项目已有基础组件」 | 头部 / 目录 / 正文 / 示例 / 底部使用 `Card`、`HintLabel`、`StatusTag`、`button`、`make_table` 等既有组件，不新建重复控件 | `gui/help.py` | `tests/gui/test_help.py` 结构断言 |
| C2 | 「不够美化」 | 新增 `#HelpTopNav` / `#HelpBreadcrumb` / `#HelpTitle` / `#HelpSearch` / `#HelpSidebar` / `#HelpSidebarTitle` / `#HelpContent` / `#HelpToc` / `#HelpBody` / `#HelpExamples` QSS，全部使用主题 token | `gui/style/qss.py` | 深浅主题下无硬编码色；QSS 快照 / 主题测试 |
| C3 | 「美化」 | Markdown 正文用 `QTextDocument` 默认样式表主题化：标题层级、链接、行内代码、代码块、表格、引用、分隔线 | `gui/help.py` | 正文样式函数单测；离屏渲染不报错 |
| C4 | 「最佳实践」 | 视觉层级按 Web 文档站：顶部导航（面包屑 + 搜索）→ 左侧目录树（可展开收起 + 节数）→ 右侧正文 + 示例；目录与正文不套面板，只用 1px 分隔线区分；不保留不可用的操作按钮与底部提示行 | `gui/help.py` | 目视 + 对象名断言 |
| C5 | 「美化」 | 交互增强：`Ctrl+F` 聚焦搜索、正文命中高亮、滚动时目录联动、示例卡主操作、按钮图标 | `gui/help.py` | 离屏测试覆盖搜索 / 目录 / 示例 |
| C6 | 「方案」范围 | 本次落地 P0 视觉基线 + P1 交互；窗口几何记忆 / 代码块级复制留 P2 | 全链 | `make flow`、`make cov` |

## 现状影响面（候选，待 ADR 核实）

| # | 疑似受影响能力 | 候选位置 | 备注 |
|---|---|---|---|
| 1 | 帮助窗口布局 | `gui/help.py:96` | 直接 `QVBoxLayout`，无 Card / 分区 |
| 2 | Markdown 正文样式 | `gui/help.py:140` | `setMarkdown` 使用 Qt 默认文档样式 |
| 3 | 主题 QSS | `gui/style/qss.py` | 无 `#Help*` 选择器 |
| 4 | 搜索 / 目录联动 | `gui/help.py:174` | 搜索只过滤目录，无正文高亮 / 滚动联动 |
| 5 | 示例表 | `gui/help.py:158` | 裸表格，无卡片 / 主操作 |
| 6 | 测试口径 | `tests/gui/test_help.py` | 需保留 `toc` / `browser` / `search` / `example_table` 属性 |

## 未决问题

- [x] 范围：P0 + P1（用户确认「可以」），P2 留待反馈。

## 卡点

- [x] 每条需求句都有 C 编号约束（无遗漏、无「待定」）
- [x] 未决问题全部有结论，需求方确认
