# 0042 通用 GUI 组件与主题的整合：窗口外壳 mixin、页面基类与主题覆盖

- 状态：已落地（窗口外壳 mixin、页面基类、主题度量与按窗口覆盖）
- 日期：2026-11-22
- 关联：[0033](0033-qt-gui-migration.md)（Qt 基座）、[0034](0034-qt-full-gui-migration.md)（全量 Qt 迁移）、
  [0038](0038-shared-qt-widget-boundaries.md)（共享控件边界）、[0023](0023-wslman.md)、[0027–0033](0027-llmw-rename.md)（llmw 分页）

## 背景

Qt 迁移后，`fcmd.gui.qt` 已沉淀主题、侧边栏、状态栏、行卡与各类控件；但窗口与页面
这一层仍是「同构代码复制」：

- `QtMainWindow` 与 `QtToolWindow` 各自实现设置读写、几何恢复、主题加载、关闭落盘，
  改一处（例如主题按窗口覆盖）要改两份；
- llmw 的五个页面与 wslmanw 的三个页面都从 `QWidget` 直接派生，每页重复
  「建根布局 + 保存 state + 页面 key 字面量」；页面状态写入走 `state.status(text)`，
  只能落到「当前页」，后台回调在切页后会写到错误的页；
- 主题只有四套整体调色板，圆角 / 控件高度补偿 / 按钮内边距写死在
  `gui/qt/theme.py` 的模块常量里，窗口无法在不复制 QSS 的前提下做局部调整。

需求原句：**「基于 wslmanw、llmw 项目进一步整合已有通用 GUI 组件和主题，使用 mixin、
继承等技术实现复用和高度可配置性」**。

## 决策

| 编号 | 决策 | 被否决的方案（原因） | 落点 |
|---|---|---|---|
| D1 | 窗口外壳拆成四个职责 mixin：设置 / 几何 / 主题 / 关闭，再由 `WindowShellMixin` 组合；两个窗口骨架只继承、不复制 | 继续在两个窗口各写一份（主题覆盖等横切能力每加一项都要改两处）；把外壳做成配置对象 + 一个超大类（职责仍混在一起，测试难按能力隔离） | `gui/qt/shell.py` |
| D2 | 窗口元信息收进 `WindowSpec`（标题 / 配置名 / 导航标题 / 默认与最小尺寸 / 侧边栏状态 / 主题覆盖键 / 设置路径） | 继续用一长串关键字参数（新增一项就要改两个 `__init__` 与所有调用点） | `gui/qt/shell.py`、`window.py`、`toolwindow.py` |
| D3 | 页面拆成两层：`PageBody`（根布局容器）+ `AppPage`（页面 key + state + 状态栏 / 卡片辅助）；llmw 五个页面与 wslmanw 三个页 builder 统一使用 | 让页面继续各自 `QVBoxLayout(self)`（布局与状态通道重复）；把业务逻辑也塞进基类（页面职责不再单一） | `gui/qt/page.py`、`apps/llm/_llmw_qt_*.py`、`apps/wslman/_wslmanw_qt.py` |
| D4 | 页面状态统一走 `AppPage.status()` → `state.page_status(PAGE_KEY, text)`，按页隔离；`statusText` 只保留给「窗口级」提示 | 继续 `state.status(text)`（后台回调落到用户当前所在页，造成串页） | `gui/qt/page.py`、llmw 页面 |
| D5 | 页面登记项收进 `PageSpec`（key / 标题 / 提示 / 图标），并给 `QtMainWindow.add_pages(specs, factories)`；`PageSpec` 保留四元组解包兼容 | 继续在窗口里手写四元组循环（新增页面要同时改元组与 builder 表，key 靠字面量对齐） | `gui/qt/page.py`、`window.py`、两个应用的 `PAGES` |
| D6 | 主题新增 `Metrics`（圆角 / 控件高度补偿 / 主按钮加高 / 按钮与输入框内边距），QSS 模板只从 `theme.metrics` 取值 | 继续写死模块常量（换主题只能换色，改尺寸要改代码）；把度量塞进 `Palette`（调色板与尺寸是两类 token） | `gui/theme.py`、`gui/qt/theme.py` |
| D7 | `ui/theme.json` 支持 `overrides`（全局）与 `apps.<config_name>`（按窗口）覆盖调色板 token / `font_*` / 度量；非法值逐项忽略 | 每个窗口再建一份主题文件（多份配色必然漂移）；只支持整体主题（窗口无法只调强调色或圆角） | `gui/theme.py`、`gui/qt/shell.py` |
| D8 | `save_theme` 只更新 `theme` 字段，保留 `overrides` / `apps`；`apply_theme` 按完整 `Theme` 判等而不是只按 id | 切主题时整体重写文件（用户手写覆盖被冲掉）；只比 id（同 id 不同覆盖时样式不刷新） | `gui/theme.py`、`gui/qt/theme.py` |

### D1 为什么用 mixin 而不是窗口基类

窗口外壳的四个能力彼此独立：测试可以只验证设置合并 / 几何回写 / 主题加载中的一项，
单页窗口与分页窗口也只需要同一套外壳、不同的内容区。拆成 mixin 后：

```python
class QtMainWindow(WindowShellMixin, QMainWindow): ...


class QtToolWindow(WindowShellMixin, QMainWindow): ...
```

`WindowShellMixin._init_window_shell(spec, on_close=..., persist=..., icon=...)` 是唯一入口：
读设置 → 加载主题（可带窗口覆盖）→ 记录关闭回调 → 写标题 / 图标 / 最小尺寸 → 恢复几何。
`closeEvent` / `is_closing` 由 `WindowLifecycleMixin` 提供，MRO 优先于 `QMainWindow`，
窗口子类不再重写关闭流程。新增窗口不需要了解这些细节，也不会漏掉落盘。

### D3 / D4 为什么页面要有基类

页面共同的只有三件事：根布局、页面 key、把提示写到「这一页」的状态栏。`PageBody`
只提供 `body` 布局；`AppPage` 再加 `state` / `PAGE_KEY` / `status()` / `card()`：

```python
class ChatPage(AppPage):
    PAGE_KEY = "chat"

    def __init__(self, state, parent=None):
        super().__init__(state, parent, spacing=0)
```

业务渲染（对话流、模型行、下载按钮）仍留在页面里，基类不做业务判断，也不依赖
`LlmState` 的具体类型（只要求有 `status` / `page_status`），测试桩与其它应用可直接复用。
`header_widgets()` 的默认空实现让窗口不用再 `getattr` 探测。

### D6 / D7 主题如何做到「高度可配置」

`Theme` 现在由四组数据组成：`palette`（语义色）、`fonts`（字号阶梯）、`spacing`
（间距阶梯）、`metrics`（控件度量）。QSS 只从这些数据生成，`gui/qt/theme.py` 的
`_RADIUS` / `CONTROL_HEIGHT_EXTRA` / `_CONTROL_PAD_X_*` 退化为默认值常量。

`${FCMD_HOME:-~/.fcmd}/ui/theme.json` 可写：

```json
{
  "theme": "dark-blue",
  "overrides": { "radius": 10, "font_body": 10 },
  "apps": {
    "llmw": { "accent": "#ff7a00" },
    "wslmanw": { "control_height_extra": 11 }
  }
}
```

- 全局 `overrides` 对所有窗口生效，`apps.<config_name>` 再按窗口叠加（窗口段优先）；
- 键名与 `Palette` 字段 / `font_small|font_body|font_heading|font_title` / `Metrics` 字段同名；
- 值非法（非 `#rrggbb`、字号越界、内边距不是 `em|px`）时逐项忽略，主题仍可用；
- 主题菜单切换只改 `theme` 字段，覆盖段原样保留。

窗口通过 `WindowSpec.theme_app`（默认取 `config_name`）在启动时读取自己的覆盖，
因此 llmw / wslmanw 可以在共享四套主题的前提下各自微调，而不用复制主题或 QSS。

## 后果

- `QtMainWindow` / `QtToolWindow` 各减少约 90 行重复实现，关闭 / 主题 / 几何行为只有一份；
- llmw 五个页面与 wslmanw 三个页 builder 共享页面骨架，页面 key 与状态栏通道不再是
  散落的字符串与 `state.status` 调用；
- 主题从「四套固定配色」升级为「四套基线 + 全局 / 按窗口覆盖」，覆盖不改变主题 id，
  主题菜单勾选与 `ui/theme.json` 互读保持不变；
- 旧 `theme.json`（只有 `{"theme": "light"}`）继续有效；`save_theme` 会保留手写覆盖段；
- 既有控件与 QSS 的对象名契约不变（`ItemRow` / `StatusTag` / `Card` 等），0038 的边界
  继续成立。

## 交接

- 新增窗口：继承 `QtMainWindow`（分页）或 `QtToolWindow`（单页），构造时只传
  `WindowSpec` 所需的参数；不要再复制设置 / 几何 / 主题 / 关闭代码。
- 新增页面：继承 `AppPage`，设 `PAGE_KEY` 后把控件加到 `self.body`；页面状态用
  `self.status(...)`（按页隔离），跨页跳转仍走 state 的 `navigate` 类通道。
- 新增页面登记：在应用模块里定义 `PAGES: tuple[PageSpec, ...]`，窗口用
  `add_pages(PAGES, factories)` 登记；`PageSpec` 仍可四元组解包，旧测试写法不受影响。
- 新增主题可调项：先加到 `Palette` / `FontScale` / `Metrics`，再在
  `gui/qt/theme.py` 的模板里引用；不要在窗口里写死颜色或像素。
- 窗口级主题覆盖的 key 必须等于该窗口的 `config_name`（llmw / wslmanw），
  否则只应用全局 `overrides`。
