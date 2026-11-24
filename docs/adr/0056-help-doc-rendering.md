# 0056 帮助文档渲染层（docview）

- 状态：已落地（P0 / P1）
- 日期：2026-11-15
- 需求卡片：[0056](../requirements/0056-help-doc-rendering.md)

## 背景

帮助窗口此前用 `QTextBrowser.setMarkdown()`，代码块无语法高亮、无法单独复制，
表格 / 删除线等 GFM 能力受限；渲染 CSS、搜索高亮、链接处理都堆在
`gui/help.py`，文件接近 800 行预算，也不利于其它 GUI 复用。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 标题提取下沉 `shared/helpdoc.py`：`help_outline` / `help_headings`，CLI 与 GUI 共用 | `shared/helpdoc.py` |
| D2 | 新增 `gui/docview/` 包：`render.py`（Markdown → HTML）、`highlight.py`（Pygments）、`theme.py`（QTextDocument CSS）、`view.py`（`HelpView`） | `gui/docview/` |
| D3 | Markdown 改用 `markdown-it-py`（`commonmark` + table / strikethrough，`html=False`），不再依赖 Qt 自带解析器 | `render.py` |
| D4 | 代码块按语言 Pygments 高亮，未知语言 / 超 2000 行回退转义；渲染为左侧强调色细条 + 无边框暗色容器，语言名与复制链接轻量置于顶部，由 `HelpView` 拦截 `fcmd-copy-code:<index>` 复制原文 | `highlight.py`、`render.py`、`view.py` |
| D5 | `HelpView` 统一维护搜索高亮、标题定位、相对链接 / 图片基准目录；`gui/help.py` 只保留窗口导航 / 目录树 / 示例 / 菜单 | `view.py`、`help.py` |
| D6 | 显式声明 `markdown-it-py>=4` 与 `pygments>=2`，不再依赖 `rich` 的传递依赖 | `pyproject.toml`、`uv.lock` |

## 口径（C1..Cn → 落点）

| # | 约束 | 落点 |
|---|---|---|
| C1 | Markdown 渲染 | `render.py` |
| C2 | 代码块高亮 / 复制 | `highlight.py`、`render.py`、`view.py` |
| C3 | 位置 | `gui/docview/`、`shared/helpdoc.py` |
| C4 | 搜索 / 定位 / 链接复用 | `view.py` |

## 落地状态

- P0：`gui/docview/` 包、`shared.helpdoc.help_outline` / `help_headings`、
  `markdown-it-py` 渲染、主题 CSS 迁移。
- P1：Pygments 代码高亮（深色 `one-dark` / 浅色 `friendly`）、艺术化
  左侧强调条 + 顶部语言 / 复制行、超长 / 未知语言回退；`HelpView` 搜索高亮 / 定位 / 链接处理。

## 交接

- 新增 Markdown 能力（图片、任务列表、脚注）优先改 `gui/docview/render.py`；
  不要把解析逻辑塞回 `gui/help.py`。
- 新增代码高亮主题只改 `gui/docview/theme.py::pygments_style`，不要在渲染器里写死颜色。
- `HelpView` 是通用文档视图；其它 GUI 需要说明面板时直接复用，不要复制 `QTextBrowser` 逻辑。
- 已知边界：QTextDocument 对 CSS 支持是子集；复杂 HTML / 脚本仍禁用，远程图片不自动加载。
