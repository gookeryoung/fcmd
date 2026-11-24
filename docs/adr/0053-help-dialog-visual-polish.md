# 0053 帮助窗口视觉美化

- 状态：已落地（P0 / P1）
- 日期：2026-11-15
- 需求卡片：[0053](../requirements/0053-help-dialog-visual-polish.md)

## 背景

ADR 0052 交付的帮助窗口只有功能骨架：标题、搜索、目录、正文、按钮直接铺在
`QVBoxLayout` 里；`QTextBrowser.setMarkdown()` 使用 Qt 默认文档样式，代码块 /
表格 / 链接不随主题变化；目录和正文是默认 `QListWidget` / `QTextBrowser` 边框，
与项目 `Card` / 主题 QSS 脱节。本次只改展示层，不改 `HelpDoc` 数据层与
`HelpMenuMixin` 的菜单契约。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 顶部导航 `QFrame#HelpTopNav`：左侧品牌 + 面包屑（帮助 / 工具名），右侧只保留 `#HelpSearch`；搜索收进导航，不再单独占一行 | `gui/help.py`、`gui/style/qss.py` |
| D2 | 左侧 `QWidget#HelpSidebar` 只承载「目录树 + 节数」，来源放标题 tooltip、摘要留在正文；目录用 `QTreeWidget#HelpToc` 建二级 / 三级树，支持展开收起；右侧 `QWidget#HelpContent` 直接放 `QTextBrowser#HelpBody`，两者不套卡片，只用 `QuietSplitter#HelpSplitter` 的一条细线分隔 | `gui/help.py`、`gui/style/qss_help.py` |
| D3 | Markdown 正文通过 `QTextDocument.setDefaultStyleSheet` 使用主题 token：标题 / 链接 / 行内代码 / 代码块 / 表格 / 引用 / 分隔线；不引入新的 Markdown 渲染依赖 | `gui/help.py` |
| D4 | 示例改为右侧内容列的 `Card` + `make_table`，标题行动作「载入选中示例」（有回调时才出现）；双击行仍走 `on_example(row)` | `gui/help.py` |
| D5 | 顶部导航不再放文档操作按钮（原图标按钮不可用，已删除）；窗口关闭走标题栏 / `Esc`，正文复制走文本选区 | `gui/help.py` |
| D6 | 交互：`Ctrl+F` 聚焦搜索、正文命中高亮（`ExtraSelection`）、滚动时目录联动、搜索过滤目录 | `gui/help.py` |
| D7 | 保留 `toc` / `browser` / `search` / `example_table` / `_activate_example` 等既有属性与方法，避免帮助窗口调用方与测试返工 | `gui/help.py`、`tests/gui/test_help.py` |

## 口径（C1..Cn → 落点）

| # | 约束 | 落点 |
|---|---|---|
| C1 | 复用既有基础组件 | `Card` / `HintLabel` / `StatusTag` / `button` / `make_table` / `set_icon` |
| C2 | 主题 QSS | `gui/style/qss.py` 的 `#Help*` 选择器 |
| C3 | Markdown 主题化 | `_document_stylesheet()` |
| C4 | 视觉层级 | 头部 / 正文 / 示例 / 底部分区 |
| C5 | 交互增强 | `Ctrl+F` / `_apply_highlight` / `_sync_toc_from_scroll` |
| C6 | P0 + P1 | 窗口几何记忆与代码块级复制留 P2 |

## 落地状态

- P0：顶部导航 / 左侧文档栏 / 右侧正文 / 示例结构；`#Help*` QSS；Markdown 默认样式表。
- P1：搜索 `Ctrl+F` + 正文高亮；目录改为可展开收起的树，滚动联动；顶部移除不可用的
  文档操作图标，底部快捷键提示删除。
- P1：`Ctrl+F`、正文命中高亮、目录滚动联动、示例卡「载入选中示例」与双击提示。

## 交接

- 新帮助窗口继续用 `HelpDialog` / `HelpMenuMixin`，不要另写对话框；样式只加
  `#Help*` 选择器，色值统一取主题 token。
- 顶部导航只放「面包屑 + 搜索」；侧边栏只放「目录树 + 节数」，来源用标题 tooltip，
  摘要留在正文；目录与正文不套 Card，靠 `#HelpSplitter` 的 1px 分隔线区分。
- 不要恢复不可用的文档操作按钮或底部快捷键提示行。
- 新增头部信息先判断是否属于导航 / 目录，不要重新堆回顶部。
- 修改 `HelpDialog` 公开属性（`toc` / `browser` / `search` / `example_table`）时
  同步 `tests/gui/test_help.py` 与各窗口的引用。
- 已知边界：Qt 富文本对 CSS 的支持是子集，`line-height` / 部分表格属性可能被忽略；
  P2 若要代码块级复制，需要在 `QTextBrowser` 内做块级命中检测，成本高于本轮收益。
