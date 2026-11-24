# 需求卡片：帮助文档渲染层

- 编号：0056（卡片存 `docs/requirements/0056-help-doc-rendering.md`；与 ADR 同号）
- 来源 / 提出人：用户（会话内提出）
- 日期：2026-11-15
- 关联 ADR：[0056](../adr/0056-help-doc-rendering.md)

> 本卡片是**需求原文与约束编号的唯一副本**：ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> 进一步提升文档的渲染能力，包括代码块等，请给出建议放哪

> 可以

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 「提升渲染能力」 | 帮助正文改用 `markdown-it-py` 渲染 HTML，支持表格 / 删除线 / fence，禁用原始 HTML | `gui/docview/render.py` | `tests/gui/docview/test_render.py` |
| C2 | 「包括代码块」 | 代码块按 Obsidian 风格渲染：顶部语言栏 + 复制链接 + 贴底代码区；按语言用 Pygments 高亮，未知语言 / 超长代码块回退转义文本 | `gui/docview/highlight.py`、`render.py`、`view.py` | 高亮 / 复制测试 |
| C3 | 「建议放哪」 | 渲染逻辑从 `gui/help.py` 抽到 `gui/docview/` 包；纯标题提取下沉 `shared/helpdoc.py`；窗口只留编排 | `gui/docview/`、`shared/helpdoc.py`、`gui/help.py` | 目录 / 架构守卫 |
| C4 | 「进一步提升」 | 搜索高亮、标题定位、相对链接 / 图片基准目录、代码复制由 `HelpView` 统一维护；其它 GUI 可复用 | `gui/docview/view.py` | `tests/gui/docview/test_view.py` |

## 现状影响面

| # | 疑似受影响能力 | 候选位置 | 备注 |
|---|---|---|---|
| 1 | Markdown 渲染 | `gui/help.py` | `QTextBrowser.setMarkdown` 无法高亮 / 复制代码块 |
| 2 | 文档 CSS | `gui/help.py` | 应随渲染层迁到 `gui/docview/theme.py` |
| 3 | 标题提取 | `gui/help.py` | 下沉 `shared/helpdoc.py`，CLI / GUI 共用 |
| 4 | 搜索 / 链接 | `gui/help.py` | 抽到 `HelpView` |
| 5 | 依赖 | `pyproject.toml` | `markdown-it-py` / `pygments` 显式声明 |

## 未决问题

- [x] 位置：`gui/docview/` + `shared/helpdoc.py`（用户确认「可以」）。

## 卡点

- [x] 每条需求句都有 C 编号约束（无遗漏、无「待定」）
- [x] 未决问题全部有结论，需求方确认
