# 0025 GUI 侧边栏分页导航（`SidebarNav`）

- 状态：已落地（locllmw 等整窗分页；原环境窗口、引擎窗口与转换 / 量化窗口已并入 locllmw，见 0031）
- 日期：2026-11-14
- 关联：`src/fcmd/gui/sidebar.py`、`src/fcmd/gui/nav.py`、`src/fcmd/gui/theme.py`、[ADR 0013](0013-gui-toolkit-feasibility.md)、[ADR 0016](0016-llmdown-model-browser.md)

## 背景

locllmw 原先与其他整窗工具一样，用顶部
`ttk.Notebook` 承载「配置 / 监控 / 日志」这类页面：

1. 标签条与内容抢横向空间：页数到 3–4 个、或标题有中文长词时，标签被截断，
   内容区左侧被挤压。
2. 选中态弱：只在标签底部一条浅色描边，深色主题下不易一眼看出当前页。
3. 每个窗口各自写 `Notebook` + `Frame` + `add/select`，页面 key 用控件对象
   传递（`select(self._roots_page)`），跨页跳转与「切页触发加载」散落在各处。

要求：**以 locllmw 为目标改成侧边栏分页，并把实现收敛成通用 GUI 组件**，
其他同类窗口直接复用，不再各自拼 `Notebook`。

## 决策

### 1. `fcmd.gui.sidebar.SidebarNav` 作为唯一分页容器

- 结构：左侧固定宽度导航栏（可选应用图标 + 标题 + 页按钮），右侧页面区；页面提示只作为按钮 tooltip 与窗口标题栏说明，不在侧边栏底部占一行。
- 折叠：窄栏只显示页名首字与 tooltip，顶部标题区只留应用图标（无图标窗口留空）；
  折叠 / 展开按钮固定在**侧栏最底部**，外观与上方页按钮一致：横向铺满整栏、
  无文字、相同的 36px 高度 / hover 底色，只用 ``chevron-left`` / ``chevron-right``
  图标区分方向。页按钮同样左右不留边距（选中底色与左侧强调条贴到侧栏边缘），
  文字靠 12px 内边距对齐。这样箭头不会和 LOGO 抢顶部位置，60px 窄栏里也能一眼看到展开入口。
  没有传图标的窗口（如 `wslmanw`）同样使用底部按钮。图标来自
  `fcmd.gui.icons`，不再用 `«` / `»` 字形。
- 页面用**字符串 key** 标识：`add(key, label, hint=..., icon=...) -> 页面控件`；
  带图标时展开态「图标 + 文字」、折叠态只显示图标（见 ADR 0038 的 D7），
  `select(target)` 接受 key / 索引 / 页面控件，`step(delta)` 循环切换，
  `keys()` / `selected()` 读取状态。
- 数量徽标 `set_badge(key, count)`：页按钮右端（折叠态右上角）叠加半透明圆形数字，
  `count <= 0` 收起；徽标带 `WA_TransparentForMouseEvents`，点击仍落到按钮上。
  llmw 侧边栏「运行」用它显示运行中模型总数（见 [ADR 0027](0027-llmw-rename.md)）。
- `on_change(key)` 只在**真正切页**时回调；首个页面自动选中但不回调，
  避免构造期回调访问尚未建好的控件（与 `Notebook` 事件异步触发的效果对齐）。
- 快捷键 `enable_shortcuts()`：`Ctrl+Tab` / `Ctrl+Shift+Tab` /
  `Ctrl+PageDown` / `Ctrl+PageUp`。
- tkinter 仍在实例化时延迟导入，保持 CLI 快路径不加载图形库；纯逻辑
  （`resolve_page_key` / `next_page`）拆在模块级，CI 可单测。

### 2. 样式集中在主题层

`fcmd.gui.theme` 新增 5 个样式，全部主题共用：

| 样式 | 用途 |
|---|---|
| `Sidebar.TFrame` | 导航栏底色（`palette.surface`，与内容区 `background` 分层） |
| `SidebarTitle.TLabel` | 导航栏标题 |
| `SidebarHint.TLabel` | 当前页说明（`palette.muted`） |
| `Sidebar.TButton` | 未选中页（muted 前景 + hover/按下态） |
| `SidebarActive.TButton` | 选中页（`surface_active` + 强调色边框） |

`native` 主题的系统按钮不响应背景色，选中页改用强调色**文字**标记，与
`Accent.TButton` 的 native 处理一致。

### 3. 迁移范围

已迁移「整窗主页面」型窗口：locllmw（模型页以浏览列表承载下载 / 转换 / 量化）。
原引擎管理窗口与独立环境窗口已并入 locllmw 设置页的「引擎」「目录」分页，
转换参数已并入 locllmw 设置页，转换 / 量化入口都在模型列表行内，不再单独开窗。跨页跳转改为 `_nav.select("log")` 之类的 key 调用。主容器
`ttk.Frame(self.root)` **不加 padding**，让侧边栏与状态栏贴窗边；内容间距统一
由各页面自己的 `page_padding` 负责，避免外层再叠一层边距。

**不迁移**以下 `Notebook`：它们是同屏子视图而不是顶层页，或受窗口高度约束，
侧边栏会二次挤压内容：

- wslmanw 详情/下载/日志（主网格中部，纵向空间优先）；
- peview、reshack、paramcad 的预览/字段子页；
- dynacalc 的多视图切换。

`filescanw` 原在「不迁移」名单里（同屏多视图），现已按同一 API 迁为
「扫描 / 结果 / 规则 / 设置」侧边栏分页：四页都是顶层视图且结果页需要满高表格，
标签条只会挤压横向空间；表单高的设置页套 `PageScroll`，结果页不套滚动区。

这些窗口需要时可按同一 API 迁移，不存在私有分支。

### 4. 测试口径

- `tests/gui/test_sidebar.py`：key / 索引解析、越界与非法类型、循环切换。
- `tests/gui/test_theme.py`：5 个新样式在四套主题下存在；
  选中页前景与底色对比度、muted 与 surface 对比度均 ≥ 4.5。
- 纯逻辑（key 解析、循环切换）在 :mod:`fcmd.gui.nav`，Qt 控件（`SidebarNav`）标注
  `pragma: no cover`（CI 无显示环境），离屏冒烟覆盖建窗、切页与快捷键绑定。

## 落地状态与交接

- 已完成：组件、主题样式、5 个窗口迁移（llmw / wslmanw / 原引擎与环境窗口合并）、
  filescanw 侧边栏分页迁移（现为四页：扫描 / 结果 / 规则 / 设置）、单测与 ADR 口径。
- 折叠态：应用图标居中显示，展开按钮固定在侧栏底部且与页按钮同款（整行 / 无文字）；
  点击图标或底部箭头都能展开（`test_collapsed_keeps_icon_and_shows_expand_button`）；
  没有图标时底部箭头依然可用（`test_collapsed_without_icon_keeps_toggle_to_expand`）；
  位置与铺满宽度由 `test_toggle_sits_at_sidebar_bottom` 覆盖。
- 视觉回归点：侧边栏默认宽 220px、折叠宽 60px；页按钮字号取 heading、
  高度 36px，保证文字与点击区域足够大。长页名加宽而不是换行。
- 下一步（未做）：若调整 `SidebarNav` 的折叠交互，需同步检查 `wslmanw` 等
  无应用图标窗口的展开入口，并补一次 900×680 最小窗口的截图验收。
