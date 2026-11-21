# 0038 Qt 共享控件的抽象边界

- 状态：生效（边界已确定）
- 日期：2026-11-21
- 关联：[0033](0033-qt-gui-migration.md)（GUI 基座）、[0034](0034-qt-full-gui-migration.md)（全量 Qt 迁移）

## 背景

Qt 迁移后，`gui/qt/` 已沉淀 `Card` / `PageScroll` / `FormGrid` / `LogView` / `StatusTag` /
`ProgressRow` / `ChoiceCard` / `ChoiceGroup` / `SelectionRow` / `LogDialog` / `EventBridge` /
`ProgressBridge` 等共享件。P0–P2 已把 llmw 内部的重复拼装收敛到这些共享件，并推广到
img2ico、pdf 系列、videoconvw、mirrortool 等窗口。

继续推广时，剩下候选都涉及「抽基类」「换可见交互」或「新建通用对话框」。这类改动
不再是单纯搬位置，需要先划清边界，避免以后反复讨论同一问题。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | **不做完整 `InfoRowCard` 基类**，行卡继续以 `SelectionRow` mixin 复用选中态 | 三张 llmw 行卡 |
| D2 | **不做通用 `TaskDialog`**；需要进度与日志的窗口各自组合 `ProgressRow` / `LogView` | llmw 引擎下载、性能测试 |
| D3 | `ChoiceCard` 只用于「卡片式单选」；pdfsplit、mirrortool、checksum 继续用 `QRadioButton` | 上述工具窗口 |
| D4 | `StatusTag` 用于需要语义色的状态块；`objectName("Success"/"Danger")` 属于主题契约，不强行替换 | reshack 等 |
| D5 | `LogDialog` 只服务「独立日志窗口」；页面内日志继续用 `LogView` | 各工具窗口 |
| D6 | `ProgressRow` 用于独立进度行；与按钮 / 文字同处一行的进度条保留窗口自己的布局 | ncmconvert、filescan、devman、llmw 下载区 |

### D1 为什么不做 InfoRowCard 基类

三张行卡的规模与结构差异很大：

- `RecommendationRow` 约 58 行：评分徽章 + 名称 / 参数 + 右侧性能摘要；
- `_EngineRow` 约 44 行：单行名称 + 可执行文件 + 状态 / 按钮；
- `_ModelItemRow` 约 253 行：可展开详情、多组状态标签与行内动作。

真正共有的只有「`QFrame` 外壳 + `WA_StyledBackground` + `selected` 属性 + 名称 / 副标题」
约 10 行。若抽完整基类，必须开放徽章、展开区、右侧动作等插槽，基类参数会接近三张卡
的并集，耦合成本高于节省的重复代码。**共性已被 `SelectionRow` 覆盖，不再上提。**

### D2 为什么不做 TaskDialog

现有对话框都是应用专用表单；唯一的「任务型」对话框（引擎下载、性能测试）结构不同，
且已经各自组合共享的 `ProgressRow` 与 `LogView`。不存在第二份相同实现，通用 `TaskDialog`
只能靠大量可选参数适配，收益不足。

### D3 为什么暂不推广 ChoiceCard

`ChoiceCard` 目前只服务 llmw 运行页的「运行方式」选择。pdfsplit、mirrortool、checksum
使用的是原生单选按钮组，改成卡片式选择会改变可见交互与布局，属于产品层面的 UI 变更，
需单独评估，而不是组件收敛。若以后统一改版，再整体迁移。

### D4 / D5 / D6 的边界

- `StatusTag` 与主题的 `Success` / `Danger` 是两套语义：前者是带底色的胶囊块，后者是
  QSS 命名契约。已有窗口按契约上色时保持不动，避免视觉回归。
- `LogDialog` 承载「窗口外回放日志」；页面内嵌日志用 `Card + LogView` 即可，不额外包对话框。
- 进度条若与按钮、计数或说明文字共享同一行，`ProgressRow` 的「文字 + 进度 + 右侧动作」
  三段结构反而不匹配，保留原布局。

## 后果

- `theme.py` 的 objectName / QSS 与相关测试零改动。
- 三张行卡、四个工具窗口、reshack 状态栏的既有行为与视觉不变。
- 新增行卡或 UI 容器时，优先用共享件组合，不新增基类层级。

## 重新评估的触发条件

- 出现第四种行卡，或三张行卡的公共骨架增长到约 30 行以上；
- 两个以上应用需要「窗口外日志回放」；
- 多个应用的产品设计确定改为卡片式单选，届时再整体迁移 `ChoiceCard`。
