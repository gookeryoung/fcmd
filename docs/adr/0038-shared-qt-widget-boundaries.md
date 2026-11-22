# 0038 Qt 共享控件的抽象边界

- 状态：生效（边界已确定）
- 日期：2026-11-21
- 关联：[0033](0033-qt-gui-migration.md)（GUI 基座）、[0034](0034-qt-full-gui-migration.md)（全量 Qt 迁移）

## 背景

Qt 迁移后，`gui/` 已沉淀 `Card` / `PageScroll` / `FormGrid` / `LogView` / `StatusTag` /
`ProgressRow` / `ChoiceCard` / `ChoiceGroup` / `SelectionRow` / `ItemRow` / `LogDialog` /
`EventBridge` / `ProgressBridge` / `WindowStatusBar` 等共享件。P0–P2 已把 llmw 内部的重复拼装收敛到这些共享件，并推广到
img2ico、pdf 系列、videoconvw、mirrortool 等窗口。

继续推广时，剩下候选都涉及「抽基类」「换可见交互」或「新建通用对话框」。这类改动
不再是单纯搬位置，需要先划清边界，避免以后反复讨论同一问题。

## 决策

| 编号 | 决策 | 落点 |
|---|---|---|
| D1 | 可展开行卡的骨架统一用共享 `ItemRow`（标题 + 副标题 / 标签行 + 右侧数值与动作 + 行内展开区），选中态仍由 `SelectionRow` mixin 提供 | `gui/widgets/rows.py`、llmw 模型行、wslmanw 发行版 / 镜像行 |
| D2 | **不做通用 `TaskDialog`**；进度仍用 `ProgressRow`，日志统一走公共 `LogChannelMixin`（帮助菜单），页面内不再组合 `LogView` 日志区 | llmw 引擎下载、性能测试 |
| D3 | `ChoiceCard` 只用于「卡片式单选」；pdfsplit、mirrortool、checksum 继续用 `QRadioButton` | 上述工具窗口 |
| D4 | `StatusTag` 用于需要语义色的状态块；`objectName("Success"/"Danger")` 属于主题契约，不强行替换 | reshack 等 |
| D5 | 所有窗口的任务日志只经 `LogChannelMixin`：窗口骨架自动把「任务日志…」（`Ctrl+Shift+L`）插到「帮助 → 关于」之前，应用只调 `self.log(...)`；`LogDialog` / 日志缓冲 / 关闭联动都由公共组件维护 | `gui/logdialog.py`、`gui/window.py`、`gui/toolwindow.py`、各 `*w` 窗口 |
| D6 | `ProgressRow` 用于独立进度行；与按钮 / 文字同处一行的进度条保留窗口自己的布局 | ncmconvert、filescan、llmw 下载区 |
| D7 | 图标统一走 `fcmd.gui.icons`（内置 Lucide 线性图标），不引入外部图标包 | 侧边栏、按钮、行卡展开 / 收起 |
| D9 | 行卡选中态走 `RowListWidget.sync_selection()`：只重抛光「旧 / 新」两行，`set_selected` 状态未变时空操作 | 共享 `ItemRow` 的所有列表（wslmanw 发行版 / 镜像、llmw 模型 / 推荐 / 引擎） |
| D9 | 输入法组合期间的占位文字统一由应用级事件过滤器处理（`gui/ime.py` 的 `PlaceholderImeGuard`），不在各输入框重复实现 | `create_application`、llmw 对话输入框等 |
| D10 | 多存储目录设置统一用 `StorageCard`（`StorageRoots` 增删 / 设为主目录 + `DiskGrid` 展示）：组件负责添加 / 移除确认 / 按宿主 ``min_count`` 决定可否清空（llmw 允许清空并回落默认），变更只发 `changed`，应用负责落盘 | `gui/widgets/storage.py`、llmw 设置页存储卡片 |
| D11 | 侧边栏条目列表统一用 `SidebarList`（`SidebarEntry` + 自绘 delegate）：圆角选中底、标题省略、可选副标题 / 右侧摘要 / 分组标题；不再为每个窗口单写 QListWidget QSS | `gui/widgets/sidebar_list.py`、llmw 对话历史 |
| D8 | 底部状态栏统一用 `WindowStatusBar`：单行高度（文字与页内补充都是 `ElidedLabel`，加载条用 `compact`），文字 / 页内补充按分页隔离，任务消息写回「发起任务的那一页」 | `gui/statusbar.py`、`QtMainWindow`、wslmanw |

### D9 为什么占位文字用应用级过滤器

`QPlainTextEdit` 在输入法组合串（preedit）显示时仍会画占位文字：preedit 不在文档里，
Qt 判定「文本为空」就继续画 placeholder，于是中文组合串和占位文字叠成重影
（`QLineEdit` 由 Qt 自己处理，不受影响）。逐个输入框重写 `paintEvent` / 子类化代价高，
而这是「所有输入框都该有」的行为：用一个 `QApplication` 级事件过滤器在组合期间临时
清空 placeholder、组合结束 / 失焦后还原，`create_application` 里装一次即可覆盖全部
窗口与后续新增的输入框。

### D1 行卡骨架为什么收敛到 ItemRow

单行紧凑行卡（`RecommendationRow`、`_EngineRow`、`_PaintEngineRow`）形态不同：评分徽章 /
名称 + 路径 + 胶囊，各自保留自己的布局，不上提。共骨架的是「可展开行卡」这一类：
`_ModelItemRow`（llmw 模型浏览）、`_DistroItemRow` 与 `_ImageItemRow`（wslmanw）都是
「左侧标题 + 副标题 / 状态胶囊，右侧数值与动作，末尾展开箭头，详情挂在行下方」，
同一段骨架已经被抄了三份（60 行以上）——第三张行卡出现时即触发了本 ADR 原先写下的
重新评估条件。

`ItemRow` 只负责这一段固定骨架与展开 / 收起：

- 内容插槽：`add_title_widget`（标题右侧小标记）、`add_note_widget`（副标题之后、拉伸之前；
  `stretch=1` 时这块小字吃掉剩余宽度，适合「镜像 …」这类可省略的补充说明）、
  `add_tag`（拉伸之后的状态胶囊 / 小字，追加顺序即显示顺序）、`set_metrics`（右侧数值）、
  `add_action`（右侧动作，始终在展开箭头之前）；
- 展开态：`is_expanded` / `set_expanded` 只管详情区显隐与箭头图标，`detail_layout` 由页面
  把复用的详情视图挂进来，「同一时间只展开一行」仍由页面保证；
- 复制全称（可配置）：`ItemRow(copy_text=...)` / `set_copy_text()` 在标题右侧挂一个小号
  复制按钮，点击把完整文本（如 llmw 的 `组织/模型`）写进剪贴板，图标换对勾 1.2 秒后复原；
  不传文本就没有按钮、也不占标题行。按钮是 `QToolButton`，点击不会选中行。
- 对齐：行卡是「标题 + 副标题 / 标签」两行结构，右侧数值 / 动作 / 展开箭头与**第二行
  居中对齐**（底部对齐，中线与标签行重合）；没有第二行时整行垂直居中。空副标题不占位
  （收起而不是留一条空白行），否则单行行卡会多出一条空行、按钮与文字对不齐。
- 命中区：行卡内的文字一律不参与文本选择（`NoTextInteraction`），否则
  `ElidedLabel` / `StatusTag` 会把落在标题 / 胶囊上的点击吃掉，列表选不中这一行；
  需要复制的长文本放进行内详情或 tooltip。
- 外观：只有一份共享契约，不按应用 / 子类分别开对象名——行卡挂动态属性
  `itemRow="true"`（主题里所有行卡规则都用它选），标题统一 `QLabel#ItemRowName`；
  `elide` / `expand_hint` 这类纯展示差异仍由调用方给出。应用要额外样式可再设
  objectName，不会丢掉共享外观。

业务渲染与状态位留在各自行里：模型行的量化精度 / 下载转换按钮，发行版行的启停与
默认胶囊，镜像行的下载 / 安装动作。基类参数只覆盖骨架本身，不是三张行卡的并集。

主题 QSS 把可展开行卡统一成「1px 边框 + 8px 圆角的卡片，hover / 选中加粗边框，选中底
用半透明的 `accent_soft_strong`」：llmw 模型行、wslmanw 发行版行与镜像行都只靠
`QFrame[itemRow="true"]` 这一组选择器取样式，列表自身透明无边框。新增可展开行卡
（任何应用）直接继承 `ItemRow` 就得到同一套外观，不需要改主题、也不需要新增对象名。

行内的**标签与功能键**也走同一份契约，不再各应用各写一套盒模型：

- `ItemRow.add_tag` 给胶囊挂动态属性 `rowChip="true"`，主题用
  `QFrame[itemRow="true"] QLabel[rowChip="true"]` 统一高度（18px）、圆角（9px）、
  内边距与字号；`OwnerTag` / `StatusTag` / `QuantTag` / `DownloadProgress` 只保留
  颜色语义，边框改用同色系浅描边（`*_soft_strong`），避免一排满色粗框抢标题；
- `ItemRow.add_action` 在首个动作前插入 `QFrame#RowActionDivider`（1px、边框同色），
  把「标题 / 副标题 / 胶囊」与「数值摘要 / 动作按钮 / 展开箭头」分成两区；没有动作
  的行不显示分隔线；
- 右侧数值摘要统一 `QLabel#RowMetrics`（小号、加粗、右对齐），与副标题区分；列宽按
  `metrics_samples` 测量一次后固定（镜像行把「仅 wsl --install」也纳入样本），
  同一列表里体积 / 状态文案长短不同也不会让分隔线、动作按钮和标签逐行左右跳动；
  分隔线常态用 `border_strong`，选中行把 `selected="true"` 同步到分隔线并换成
  `accent`，避免深色选中底上「看不见分隔线」；
- 行内动作按钮收紧左右内边距；次要动作（转换 / 量化 / 配置）用 `#Ghost`，主操作
  （下载 / 启动服务 / 安装）保留填充色。选中行卡底色更深，`#Ghost` 的默认边框色
  会与底色融在一起，主题统一把选中行内的 Ghost 边框换成 `accent`（hover 用
  `accent_hover`、禁用用 `border_strong`），各应用不再单独覆盖。

### D9 行卡选中态只重抛光两行

行卡选中态是动态属性 + QSS（`[selected="true"]`），切换时需要 `unpolish` + `polish`
重解析该控件的样式。原实现每次选中变化都遍历整列行卡逐行 `set_selected`，行数到
几十上百时就是 O(N) 次重抛光（实测 200 行单次切换约 7~11ms，键盘连按明显掉帧）。

现在两处一起改：`SelectionRow.set_selected` 属性没变直接返回（幂等），
`RowListWidget.sync_selection(current)` 只处理上一次的当前行与本次当前行；各窗口的
`currentRowChanged` 回调统一改成调用它，不再自己遍历。行数再多，单次切换的抛光次数
也恒为 0~2 次（每行内部再对子控件重抛光一次；子控件数量固定且很少）。

子控件也可能按祖先的 `[selected="true"]` 匹配（行内 Ghost 按钮的选中态边框）。Qt
只重抛光父控件不会刷新这些后代的 QSS，视觉上就是「选中了但按钮边框还是旧配色」。
`SelectionRow.set_selected` 因此连带 `findChildren(QWidget)` 一起 `unpolish` / `polish`，
保证依赖祖先选中态的 Descendant 样式同步更新。

### D8 为什么统一状态栏

各窗口此前各自往 `QStatusBar` 里塞控件：换行标签（选中项摘要 / 长文案）会把底栏撑成
两三行，页内信息（如选中发行版的摘要）也不会随分页切换消失，任务完成提示还会写到
「用户切过去的那一页」上。`WindowStatusBar` 把这三件事收进一个组件：

- 高度恒为一行：主文字 / 页内补充都用 `ElidedLabel`（垂直 Fixed、超长中间省略 + tooltip），
  加载条用 `LoadingBar(compact=True)`（无上下留白、6px 细进度条）；
- 文字按分页隔离：`set_page_text` / `set_page_detail` + `set_page`，切页恢复该页自己的文案；
- 右侧常驻：`set_note`（更新于 …）+ `show_loading`（不定进度）/ `show_progress`
  （确定进度，如下载，内部按 0..1000 折算）都不挤占左侧文字。

`QtMainWindow` 只做转发（`set_status` / `set_page_status` / `set_status_detail` / `status_text`），
窗口不再直接操作 `statusBar()` 的子控件。

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

### D7 公共图标

- `fcmd.gui.icons` 是唯一图标来源：SVG 以 `currentColor` 渲染，颜色取应用调色板
  正文色，跟随深浅主题；尺寸默认 16px，按屏幕缩放比生成高 DPI 位图。
- 图标名使用 Lucide 语义名（`overview` / `models` / `run` / `chat` / `lab` /
  `settings` / `refresh` / `download` / `trash` / `folder` / `search` / `send` 等），
  新增图标只需往 `ICON_BODIES` 加 24×24 图形片段。
- `SidebarNav.add(..., icon=...)`：展开态图标 + 文字，折叠态只显示图标（不再用首字）；
  `QtMainWindow.add_page(..., icon=...)` 负责透传。
- `widgets.button()` 未显式给 `icon` 时按按钮文案自动推断（`icon_for_text`），
  传图标名可覆盖、传空串可禁用；图标不改变按钮文字，既有文案与快捷键口径不变。
- 图标是按调色板着色的位图，主题切换后必须重绘：统一用
  `icons.set_icon(widget, name, tone=...)` 挂图标（同时记录 `fcmdIconName` /
  `fcmdIconSize` / `fcmdIconTone`），`apply_theme` 会调用 `refresh_icons()`
  按新调色板重绘，避免深色背景上留下深色图标。
- `tone` 必须与文字配色一致：`button()` 自动按变体映射（primary → `on_accent`、
  danger → `danger`、ghost → `muted`，其余 `text`）；侧边栏页图标选中用 `accent`、
  未选中用 `muted`；行卡展开箭头与图标按钮用 `muted`。
- 动态改按钮文案（启动 / 停止服务、下载中…、转换 / 量化中、已启动等）必须用
  `widgets.set_button_text()`：它按新文案重新推断图标并沿用 / 覆盖色调，避免图标
  停留在创建时的旧动作；`InstalledButton` 等绿色变体显式传 `tone="success"`。
- 新增按钮 / 侧边栏项时优先复用上面两条入口，不手工 `setIcon(QIcon(path))`。

## 后果

- `theme.py` 的 objectName / QSS 与相关测试零改动。
- 行卡的既有行为与视觉不变：可展开行卡共享 `ItemRow` 骨架，单行紧凑行卡保持自己的布局。
- 新增可展开行卡时直接继承 `ItemRow`，只写业务渲染与状态位，不再复制骨架。

## 重新评估的触发条件

- 单行紧凑行卡之间也出现三份以上相同骨架；
- 两个以上应用需要「窗口外日志回放」；
- 多个应用的产品设计确定改为卡片式单选，届时再整体迁移 `ChoiceCard`。
