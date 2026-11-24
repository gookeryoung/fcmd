# 0054 公共分栏手柄（QuietSplitter）

- 状态：已落地
- 日期：2026-11-15
- 需求卡片：[0054](../requirements/0054-quiet-splitter.md)

## 背景

`make_splitter()` 之前给所有分栏 `handleWidth=6`，并由全局 QSS
`QSplitter::handle { background: $border; }` 画一条常驻粗线；llmw 的对话 /
工作流页则单独用 `ChatSplitter` + `handleWidth=4` + 透明 handle / hover 强调色，
视觉更轻。两套口径并存，帮助窗口等新页面继承的是较重的默认线。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 新增 `QuietSplitter(QSplitter)`：不可折叠、`handleWidth=4`、对象名 `QuietSplitter` | `gui/widgets/splitter.py` |
| D2 | `make_splitter()` 默认返回 `QuietSplitter`；`sizes` / `parent` / 尺寸读写接口保持兼容 | `gui/widgets/splitter.py` |
| D3 | QSS 手柄改透明底，`hover` 用 `$accent_soft`、拖动用 `$accent_soft_strong`；移除常驻 `$border` 粗线与 `ChatSplitter` 专用规则 | `gui/style/qss.py` |
| D4 | llmw 对话 / 工作流删除 `ChatSplitter` 自定义，统一走公共组件；测试改断言 `QuietSplitter` + 4px | `apps/llm/_llmw_qt_chat.py`、`_llmw_qt_flow.py`、`tests/apps/llm/test_llmw_qt_smoke.py` |
| D5 | 全局 `QSplitter::handle` 保留透明 / hover 兜底，`#QuietSplitter` 用同口径显式声明，便于单测与未来覆盖 | `gui/style/qss.py` |

## 口径（C1..Cn → 落点）

| # | 约束 | 落点 |
|---|---|---|
| C1 | 手柄轻量化 | QSS `QSplitter::handle` / `#QuietSplitter::handle` |
| C2 | 复用 llmw 口径 | `QuietSplitter`：4px、透明、hover 强调色 |
| C3 | 抽公共组件 | `make_splitter()` 默认 `QuietSplitter` |
| C4 | 其他 GUI 自动受益 | 所有 `make_splitter()` 调用点 |
| C5 | 兼容 | 函数签名不变；llmw 测试同步 |

## 落地状态

- 已实现 `QuietSplitter` 与默认 `make_splitter()` 切换。
- llmw 对话 / 工作流迁移到公共组件。
- 帮助窗口、renamer / regexvalidate / cmder / paramcad / paramesh / peview / pptmake
  等现有分栏自动使用统一手柄。

## 交接

- 新 GUI 分栏一律用 `make_splitter()`；需要特殊手柄宽度时传 `handle_width=`，不要
  再设 `ChatSplitter` 一类应用专属对象名。
- 若某个页面需要常驻分隔线，优先在分栏两侧卡片自身加边框，不要恢复全局粗线。
- 已知边界：透明 handle 需要 hover 才能看见拖动区域；键盘 / 触摸用户依赖分栏两侧的
  卡片边界，不要再把两侧卡片边框去掉。
