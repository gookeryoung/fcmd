# 需求卡片：公共分栏手柄

- 编号：0054（卡片存 `docs/requirements/0054-quiet-splitter.md`；与 ADR 同号）
- 来源 / 提出人：用户（会话内提出）
- 日期：2026-11-15
- 关联 ADR：[0054](../adr/0054-quiet-splitter.md)

> 本卡片是**需求原文与约束编号的唯一副本**：ADR 只引用 C1..Cn，不复制原文。

## 需求原句（逐字）

> 分隔符的设计不够美观，需结合llmw项目的处理进行优化，并抽离为公共组件用于其他gui

## 可实施约束（C1..Cn）

| # | 需求句（原文片段） | 可实施约束 | 涉及模块 | 验收方式 |
|---|---|---|---|---|
| C1 | 「分隔符设计不够美观」 | 分栏手柄改为透明底，hover / 拖动时用 `accent_soft` / `accent_soft_strong`，不再画常驻 `$border` 粗线 | `gui/style/qss.py` | QSS 选择器测试；目视 |
| C2 | 「结合llmw项目的处理」 | 复用 llmw `ChatSplitter` 的口径：`handleWidth=4`、透明 handle、hover 强调色 | `gui/widgets/splitter.py` | `tests/gui/test_qt_widgets.py` |
| C3 | 「抽离为公共组件」 | 新增 `QuietSplitter` 并让 `make_splitter()` 默认返回该类型；所有现有 GUI 自动获得统一手柄 | `gui/widgets/splitter.py` | 类型 / 属性断言；llmw 不再自定义 |
| C4 | 「用于其他gui」 | 帮助窗口、llmw 对话 / 工作流、renamer / regexvalidate / cmder 等现有 `make_splitter` 调用无需逐个改样式 | 全部 `make_splitter` 调用点 | 全量测试 + 目视抽查 |
| C5 | 兼容 | 保留 `make_splitter` / `splitter_sizes` / `apply_splitter_sizes` 签名；`ChatSplitter` 改名 `QuietSplitter` 后同步测试 | `tests/apps/llm/test_llmw_qt_smoke.py` | 全量测试 |

## 现状影响面

| # | 疑似受影响能力 | 候选位置 | 备注 |
|---|---|---|---|
| 1 | 分栏创建 | `gui/widgets/splitter.py:18` | 当前 `handleWidth=6` + 全局 `$border` |
| 2 | llmw 对话 / 工作流 | `apps/llm/_llmw_qt_chat.py:361`、`_llmw_qt_flow.py:235` | 自定义 `ChatSplitter` + 4px |
| 3 | 全局 QSS | `gui/style/qss.py:684` | `QSplitter::handle { background: $border; }` |
| 4 | 帮助窗口 | `gui/help.py:205` | 新布局直接受全局手柄影响 |

## 未决问题

- [x] 是否保留 `ChatSplitter` 对象名：不保留，统一为公共 `QuietSplitter`。

## 卡点

- [x] 每条需求句都有 C 编号约束（无遗漏、无「待定」）
- [x] 未决问题全部有结论，需求方确认
